// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <future>
#include <memory>
#include <stop_token>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#include <Base/Interpreter.h>
#include <Base/CancellationScope.h>
#include <FCGlobal.h>

namespace App
{

/**
 * Process-lifetime executor for CPU work that must not run on the GUI thread.
 *
 * Application owns exactly one HostRuntime. Its workers are created eagerly
 * during application startup and remain alive until application shutdown.
 * Submitted functions must accept a stop token and cooperate with shutdown.
 * Submission also captures the caller's CancellationScope tokens. Each job
 * installs its own context, including when a waiting worker helps other jobs;
 * thread-local scope pointers never cross threads.
 */
class AppExport HostRuntime final
{
public:
    enum class Lane
    {
        Compute,
        Io,
        Document
    };

    enum class IsolationStatus
    {
        Completed,
        Cancelled,
        Failed
    };

    struct IsolationConfiguration
    {
        std::string executable;
        std::vector<std::string> arguments;
        std::string workingDirectory;
        std::vector<std::pair<std::string, std::string>> environment;
        std::size_t workerCount {};
    };

    struct IsolationResult
    {
        IsolationStatus status {IsolationStatus::Failed};
        std::string response;
        std::string diagnostic;
        std::string outputTail;
        bool memoryExceeded {false};
        std::size_t observedMemoryBytes {};
    };

    struct IsolationSubmission
    {
        std::uint64_t id {};
        std::future<IsolationResult> completion;
    };

    explicit HostRuntime(
        std::size_t logicalProcessors = 0,
        bool isolationChild = false
    );
    ~HostRuntime();

    HostRuntime(const HostRuntime&) = delete;
    HostRuntime& operator=(const HostRuntime&) = delete;
    HostRuntime(HostRuntime&&) = delete;
    HostRuntime& operator=(HostRuntime&&) = delete;

    template<typename Function>
    [[nodiscard]] auto submit(Function&& function)
        -> std::future<std::invoke_result_t<std::decay_t<Function>&, std::stop_token>>
    {
        return submit(Lane::Compute, std::forward<Function>(function));
    }

    template<typename Function>
    [[nodiscard]] auto submit(Lane lane, Function&& function)
        -> std::future<std::invoke_result_t<std::decay_t<Function>&, std::stop_token>>
    {
        using Callable = std::decay_t<Function>;
        using Result = std::invoke_result_t<Callable&, std::stop_token>;
        static_assert(
            std::is_invocable_v<Callable&, std::stop_token>,
            "HostRuntime work must accept a std::stop_token"
        );

        auto task = std::make_shared<std::packaged_task<Result(std::stop_token)>>(
            [function = std::forward<Function>(function),
             inherited = Base::CancellationScope::capture()](std::stop_token stop) mutable -> Result {
                Base::CancellationScope context(std::move(inherited));
                return std::invoke(function, stop);
            }
        );
        auto result = task->get_future();
        enqueue(lane, Work {[task = std::move(task)](std::stop_token stopToken) {
            (*task)(stopToken);
        }});
        return result;
    }

    /**
     * Notify exactly once for accepted work, including cancellation before it
     * starts. The supplied future is already ready: reading it never waits.
     * Completion runs on the finishing/cancelling lane and must only dispatch
     * owner work, not access GUI state. Admission failure still throws.
     */
    template<typename Function, typename Completion>
    void submitWithCompletion(Lane lane, Function&& function, Completion&& completion)
    {
        using Callable = std::decay_t<Function>;
        using Result = std::invoke_result_t<Callable&, std::stop_token>;
        using Callback = std::decay_t<Completion>;
        struct Pending
        {
            Callable function;
            Callback completion;
            std::promise<Result> promise;
            Base::CancellationScope::Captured inherited;

            Pending(Callable function, Callback completion)
                : function(std::move(function)), completion(std::move(completion)),
                  inherited(Base::CancellationScope::capture()) {}

            void run(std::stop_token stop)
            {
                try {
                    Base::CancellationScope context(std::move(inherited));
                    if constexpr (std::is_void_v<Result>) {
                        std::invoke(function, stop);
                        promise.set_value();
                    }
                    else {
                        promise.set_value(std::invoke(function, stop));
                    }
                }
                catch (...) { promise.set_exception(std::current_exception()); }
                // Completion must be able to schedule cleanup, even when work
                // was cancelled. Nor may a helping worker leak its outer job's
                // context into this unrelated completion.
                Base::CancellationScope context(Base::CancellationScope::Captured {});
                std::invoke(completion, promise.get_future());
            }

            void cancel()
            {
                promise.set_exception(std::make_exception_ptr(
                    std::runtime_error("HostRuntime shut down before this job started")));
                Base::CancellationScope context(Base::CancellationScope::Captured {});
                std::invoke(completion, promise.get_future());
            }
        };
        auto pending = std::make_shared<Pending>(
            std::forward<Function>(function), std::forward<Completion>(completion));
        enqueue(lane, Work {
            [pending](std::stop_token stop) { pending->run(stop); },
            [pending] { pending->cancel(); }
        });
    }

    /**
     * Wait for runtime work without starving nested compute submissions.
     *
     * A compute task may itself fan work out onto the compute lane. While it
     * waits, its physical worker executes pending compute work so a saturated
     * pool cannot deadlock on its own child tasks.
     */
    template<typename Result>
    Result wait(std::future<Result>& future)
    {
        using namespace std::chrono_literals;
        // Python entry points commonly invoke synchronous public APIs while
        // holding the interpreter lock. Release it for the duration of the
        // wait so Python-backed work in the shared runtime can acquire it.
        std::unique_ptr<Base::PyGILStateRelease> release;
        if (Py_IsInitialized() && PyGILState_Check()) {
            release = std::make_unique<Base::PyGILStateRelease>();
        }
        while (future.wait_for(0ms) != std::future_status::ready) {
            if (!tryRunOnePendingComputeTask()) {
                future.wait_for(1ms);
            }
        }
        release.reset();
        return future.get();
    }

    /** Stop admission, cancel queued work, request cancellation, and join. */
    void shutdown();

    [[nodiscard]] bool isAccepting() const;
    [[nodiscard]] std::size_t logicalProcessorCount() const;
    [[nodiscard]] std::size_t workerCount() const;
    [[nodiscard]] std::size_t workerCount(Lane lane) const;
    [[nodiscard]] std::size_t readyWorkerCount() const;
    [[nodiscard]] std::size_t readyWorkerCount(Lane lane) const;
    [[nodiscard]] std::size_t queuedTaskCount() const;
    [[nodiscard]] std::size_t queuedTaskCount(Lane lane) const;
    [[nodiscard]] std::size_t activeTaskCount() const;
    [[nodiscard]] std::size_t activeTaskCount(Lane lane) const;

    /**
     * Execute independent indexed work on the application-owned compute lane.
     *
     * The call joins the submitted group before returning and propagates the
     * first worker exception after every member has stopped using the supplied
     * callable. A zero concurrency uses the runtime's current compute width.
     * Shutdown is observed between items and incomplete work throws; a normal
     * return means every indexed invocation completed, not merely that the
     * worker futures became ready.
     */
    void parallelFor(
        std::size_t count,
        const std::function<void(std::size_t)>& function,
        std::size_t concurrency = 0
    );

    /**
     * Start the process-lifetime isolation workers.
     *
     * Configuration is single-assignment. Repeating the identical request is
     * harmless; attempting to replace a live pool is an explicit error.
     */
    void startIsolationWorkers(IsolationConfiguration configuration);
    [[nodiscard]] IsolationSubmission submitIsolation(
        std::string request,
        std::size_t memoryLimitBytes = 0,
        std::size_t cpuSlots = 1
    );
    [[nodiscard]] bool cancelIsolation(std::uint64_t id);
    [[nodiscard]] std::size_t isolationWorkerCount() const;
    [[nodiscard]] std::size_t readyIsolationWorkerCount() const;
    [[nodiscard]] std::size_t queuedIsolationTaskCount() const;
    [[nodiscard]] std::size_t activeIsolationTaskCount() const;

    [[nodiscard]] static std::size_t workerBudget(std::size_t logicalProcessors);
    [[nodiscard]] static std::size_t ioWorkerBudget(std::size_t logicalProcessors);
    [[nodiscard]] static std::size_t documentWorkerBudget(std::size_t logicalProcessors);
    [[nodiscard]] static std::size_t isolationWorkerBudget(std::size_t logicalProcessors);

private:
    struct Work
    {
        std::function<void(std::stop_token)> execute;
        std::function<void()> cancelled {};
        explicit operator bool() const { return bool(execute); }
        void operator()(std::stop_token stop) const noexcept;
        void abandon() const noexcept;
    };

    void enqueue(Lane lane, Work work);
    [[nodiscard]] bool tryRunOnePendingComputeTask();

    class Private;
    std::unique_ptr<Private> d;
};

}  // namespace App
