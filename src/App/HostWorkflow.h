// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <coroutine>
#include <exception>
#include <optional>
#include <stdexcept>
#include <thread>
#include <utility>

#include "HostRuntime.h"
#include <Base/CancellationScope.h>

namespace App
{

struct HostWorkflowStep
{
    HostRuntime::Lane lane;
    std::function<void(std::stop_token)> work;
};

/** An owner-driven sequence of worker phases.
 *
 * The coordinator resumes on its owner only after the previous phase finishes.
 * A phase exception is rethrown at its yield, preserving the document routine's
 * existing recovery and cleanup scopes. The frame owns all local state across
 * suspension; worker steps must finish before the coordinator is destroyed.
 * Scheduling belongs to HostRuntime's driver, not to individual workbenches.
 */
template<typename Result>
class HostWorkflow
{
public:
    using Step = HostWorkflowStep;

    struct promise_type
    {
        Step step;
        std::optional<Result> result;
        std::exception_ptr failure;
        std::exception_ptr stepFailure;

        HostWorkflow get_return_object()
        {
            return HostWorkflow(std::coroutine_handle<promise_type>::from_promise(*this));
        }
        std::suspend_always initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void return_value(Result value) { result.emplace(std::move(value)); }
        void unhandled_exception() noexcept { failure = std::current_exception(); }

        auto yield_value(Step next)
        {
            step = std::move(next);
            struct Suspension: std::suspend_always
            {
                promise_type& promise;
                void await_resume()
                {
                    if (auto error = std::exchange(promise.stepFailure, {})) {
                        std::rethrow_exception(error);
                    }
                }
            };
            return Suspension {{}, *this};
        }
    };

    HostWorkflow(HostWorkflow&& other) noexcept
        : frame(std::exchange(other.frame, {}))
    {}
    HostWorkflow(const HostWorkflow&) = delete;
    HostWorkflow& operator=(const HostWorkflow&) = delete;
    ~HostWorkflow() { if (frame) { frame.destroy(); } }

    bool advance()
    {
        if (!frame.done()) {
            frame.resume();
        }
        return !frame.done();
    }
    const Step& step() const { return frame.promise().step; }
    void failStep(std::exception_ptr error) { frame.promise().stepFailure = std::move(error); }
    Result takeResult()
    {
        auto& promise = frame.promise();
        if (promise.failure) {
            std::rethrow_exception(promise.failure);
        }
        if (!frame.done() || !promise.result) {
            throw std::logic_error("Workflow result requested before completion");
        }
        return std::move(*promise.result);
    }

    // Explicit adapter for existing synchronous public APIs. An asynchronous
    // driver must submit each step to HostRuntime; it never calls this adapter.
    Result runSynchronously()
    {
        while (advance()) {
            try {
                step().work(std::stop_token {});
            }
            catch (...) {
                failStep(std::current_exception());
            }
        }
        return takeResult();
    }

    /** Start on the coordinator owner and return without waiting for a worker.
     * dispatchOwner must enqueue onto that same owner, which must remain alive
     * until the returned future is ready. Both coroutine resumption and frame
     * destruction stay there. Worker failures re-enter the suspended phase;
     * submission failure is never replaced by caller-thread execution.
     * finished runs once on the owner after the frame is destroyed and the
     * future is ready (success or failure); it must not throw. Dispatch must
     * enqueue, never invoke inline, including the initial coordinator step.
     * After initial admission, dispatchOwner must remain available until the
     * workflow finishes. Use runAsyncWithCleanup when that queue can stop first.
     * Initial dispatch failure propagates to the caller without invoking
     * finished. For a later dispatch failure the future receives that error;
     * the coordinator is not resumed on the failing thread.
     */
    std::future<Result> runAsync(
        HostRuntime& runtime,
        std::function<void(std::function<void()>)> dispatchOwner,
        std::function<void()> finished = {},
        std::stop_token cancellation = {}) &&
    {
        return std::move(*this).runAsyncImpl(runtime, std::move(dispatchOwner), {},
                                             std::move(finished), cancellation);
    }

    /** Use an independent owner queue for failure cleanup.
     * dispatchCleanup must accept and execute cleanup on the same owner even
     * after normal dispatch stops. It must not throw or discard accepted work.
     * Its lifetime must cover worker completion and cleanup during shutdown.
     * Normal work continues to use dispatchOwner and its existing frame budget.
     */
    std::future<Result> runAsyncWithCleanup(
        HostRuntime& runtime,
        std::function<void(std::function<void()>)> dispatchOwner,
        std::function<void(std::function<void()>)> dispatchCleanup,
        std::function<void()> finished = {},
        std::stop_token cancellation = {}) &&
    {
        if (!dispatchCleanup) {
            throw std::invalid_argument("Workflow cleanup dispatcher is required");
        }
        return std::move(*this).runAsyncImpl(runtime, std::move(dispatchOwner),
                                             std::move(dispatchCleanup),
                                             std::move(finished), cancellation);
    }

private:
    std::future<Result> runAsyncImpl(
        HostRuntime& runtime,
        std::function<void(std::function<void()>)> dispatchOwner,
        std::function<void(std::function<void()>)> dispatchCleanup,
        std::function<void()> finished,
        std::stop_token cancellation) &&
    {
        struct Driver: std::enable_shared_from_this<Driver>
        {
            std::optional<HostWorkflow> workflow;
            HostRuntime& runtime;
            std::function<void(std::function<void()>)> dispatchOwner;
            std::function<void(std::function<void()>)> dispatchCleanup;
            std::promise<Result> completion;
            std::function<void()> finished;
            std::stop_token cancellation;
            std::thread::id ownerThread;

            Driver(HostWorkflow&& workflow, HostRuntime& runtime,
                   std::function<void(std::function<void()>)> dispatchOwner,
                   std::function<void(std::function<void()>)> dispatchCleanup,
                   std::function<void()> finished,
                   std::stop_token cancellation)
                : workflow(std::move(workflow)), runtime(runtime),
                  dispatchOwner(std::move(dispatchOwner)),
                  dispatchCleanup(std::move(dispatchCleanup)), finished(std::move(finished)),
                  cancellation(std::move(cancellation))
            {}

            void completeFromDispatchFailure(std::exception_ptr error)
            {
                // HostRuntime swallows completion exceptions to keep the lane
                // alive. Complete this future here so callers cannot hang, and
                // do not resume the coordinator on the failing thread.
                try {
                    workflow.reset();
                    completion.set_exception(std::move(error));
                }
                catch (...) {
                }
                if (auto notify = std::move(finished)) {
                    notify();
                }
            }

            void dispatchFailure(std::exception_ptr error)
            {
                if (dispatchCleanup) {
                    // Owner-side queued cancellation can occur inside worker
                    // joining, with the GIL released. Cleanup must wait too.
                    auto self = this->shared_from_this();
                    dispatchCleanup([self, error] { self->completeFromDispatchFailure(error); });
                    return;
                }
                completeFromDispatchFailure(std::move(error));
            }

            std::function<void()> continuation(std::exception_ptr failure)
            {
                struct Delivery
                {
                    std::shared_ptr<Driver> driver;
                    std::exception_ptr failure;
                    bool invoked {false};

                    Delivery(std::shared_ptr<Driver> driver, std::exception_ptr failure)
                        : driver(std::move(driver)), failure(std::move(failure))
                    {}

                    ~Delivery()
                    {
                        // An accepted callback can be discarded by its owner
                        // during shutdown while the worker still holds Driver.
                        // Release the suspended frame here, before that worker
                        // drops the last reference. Rejection on the submitting
                        // thread is handled separately by the dispatch catch.
                        if (!invoked && std::this_thread::get_id() == driver->ownerThread) {
                            try {
                                driver->completeFromDispatchFailure(std::make_exception_ptr(
                                    std::runtime_error("Owner discarded workflow completion")));
                            }
                            catch (...) {
                                // A completion notification must not throw from
                                // destruction of a cancelled queue entry.
                            }
                        }
                    }
                };
                return [delivery = std::make_shared<Delivery>(
                            this->shared_from_this(), std::move(failure))] {
                    delivery->invoked = true;
                    delivery->driver->resume(delivery->failure);
                };
            }

            void resume(std::exception_ptr failure = {})
            {
                ownerThread = std::this_thread::get_id();
                workflow->failStep(std::move(failure));
                if (!workflow->advance()) {
                    try {
                        auto result = workflow->takeResult();
                        workflow.reset();
                        completion.set_value(std::move(result));
                    }
                    catch (...) {
                        workflow.reset();
                        completion.set_exception(std::current_exception());
                    }
                    // Release owner-affine callback captures here. The worker
                    // submitting this dispatch may still hold the driver.
                    if (auto notify = std::move(finished)) {
                        notify();
                    }
                    return;
                }

                auto self = this->shared_from_this();
                try {
                    const auto& next = workflow->step();
                    // The suspended frame owns the callable until completion
                    // returns to the owner. A worker-side copy could outlive
                    // owner cleanup and release its captures on the worker.
                    runtime.submitWithCompletion(next.lane,
                        [self, work = &next.work](std::stop_token stop) {
                            Base::CancellationScope shutdown(stop);
                            Base::CancellationScope operation(self->cancellation);
                            Base::CancellationScope::check();
                            (*work)(stop);
                        },
                        [self](std::future<void> result) {
                            std::exception_ptr failure;
                            try { result.get(); }
                            catch (...) { failure = std::current_exception(); }
                            try {
                                self->dispatchOwner(self->continuation(failure));
                            }
                            catch (...) {
                                self->dispatchFailure(std::current_exception());
                            }
                        });
                }
                catch (...) {
                    // Let the coroutine run its own cleanup/recovery at the
                    // failed yield, on a later owner dispatch (no recursion).
                    auto failure = std::current_exception();
                    try {
                        dispatchOwner([self, failure] { self->resume(failure); });
                    }
                    catch (...) {
                        dispatchFailure(std::current_exception());
                    }
                }
            }
        };
        auto driver = std::make_shared<Driver>(std::move(*this), runtime,
                                              std::move(dispatchOwner), std::move(dispatchCleanup),
                                              std::move(finished), std::move(cancellation));
        auto result = driver->completion.get_future();
        // Preserve initial admission failure as an exception to the caller.
        // Its finished callback may read the returned future, which cannot be
        // stored until this function returns. Never notify it inline here.
        driver->dispatchOwner([driver] { driver->resume(); });
        return result;
    }

private:
    explicit HostWorkflow(std::coroutine_handle<promise_type> frame) : frame(frame) {}
    std::coroutine_handle<promise_type> frame;
};

}  // namespace App
