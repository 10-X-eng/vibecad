// SPDX-License-Identifier: LGPL-2.1-or-later

#include <condition_variable>
#include <atomic>
#include <chrono>
#include <mutex>
#include <set>
#include <sstream>
#include <thread>
#include <vector>

#include <gtest/gtest.h>
#define FC_OS_MACOSX 1
#include "App/ProgramOptionsUtilities.h"

#include <App/HostRuntime.h>
#include <App/private/CpuBudget.h>
#include <Base/CancellationScope.h>
#include <Base/Exception.h>
#include <src/App/InitApplication.h>


using namespace App::Util;

using Spr = std::pair<std::string, std::string>;


class ApplicationTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }
};

TEST_F(ApplicationTest, fCustomSyntaxLookup)
{
    Spr res {customSyntax("-display")};
    Spr exp {"display", "null"};
    EXPECT_EQ(res, exp);
};
TEST_F(ApplicationTest, fCustomSyntaxMac)
{
    Spr res {customSyntax("-psn_stuff")};
    Spr exp {"psn", "stuff"};
    EXPECT_EQ(res, exp);
};
TEST_F(ApplicationTest, fCustomSyntaxWidgetCount)
{
    Spr res {customSyntax("-widgetcount")};
    Spr exp {"widgetcount", ""};
    EXPECT_EQ(res, exp);
}
TEST_F(ApplicationTest, fCustomSyntaxNotFound)
{
    Spr res {customSyntax("-displayx")};
    Spr exp {"", ""};
    EXPECT_EQ(res, exp);
};
TEST_F(ApplicationTest, fCustomSyntaxAmpersand)
{
    Spr res {customSyntax("@freddie")};
    Spr exp {"response-file", "freddie"};
    EXPECT_EQ(res, exp);
};
TEST_F(ApplicationTest, fCustomSyntaxEmptyIn)
{
    Spr res {customSyntax("")};
    Spr exp {"", ""};
    EXPECT_EQ(res, exp);
};

TEST_F(ApplicationTest, CpuAdmissionCancellationDoesNotNeedCapacityToBeReleased)
{
    using namespace std::chrono_literals;
    App::detail::CpuBudget budget(2);
    ASSERT_TRUE(budget.acquire(2, {}));
    std::stop_source cancel;
    std::promise<void> entering;
    auto entered = entering.get_future();
    auto waiting = std::async(std::launch::async, [&] {
        entering.set_value();
        return budget.acquire(2, cancel.get_token());
    });
    entered.get();
    cancel.request_stop();
    const auto cancelledWhileFull = waiting.wait_for(5s);
    // Always release before any failing assertion, so a regression cannot
    // strand the test's joining future. Cancellation must not need this release.
    budget.release(2);
    EXPECT_FALSE(waiting.get());
    EXPECT_EQ(cancelledWhileFull, std::future_status::ready);
    EXPECT_FALSE(budget.acquire(2, cancel.get_token()));
    ASSERT_TRUE(budget.acquire(2, {}));
    budget.release(2);
}

TEST_F(ApplicationTest, AsyncRuntimeCompletesSuccessfulFailedAndAbandonedWork)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(1);
    std::promise<int> success;
    auto succeeded = success.get_future();
    runtime.submitWithCompletion(App::HostRuntime::Lane::Compute,
        [](std::stop_token) { return 42; },
        [&](std::future<int> result) {
            EXPECT_EQ(result.wait_for(0ms), std::future_status::ready);
            success.set_value(result.get());
        });
    EXPECT_EQ(succeeded.get(), 42);

    std::promise<bool> failure;
    auto failed = failure.get_future();
    runtime.submitWithCompletion(App::HostRuntime::Lane::Compute,
        [](std::stop_token) { throw std::runtime_error("prepare failed"); },
        [&](std::future<void> result) {
            try { result.get(); failure.set_value(false); }
            catch (const std::runtime_error& error) {
                failure.set_value(std::string(error.what()) == "prepare failed");
            }
        });
    EXPECT_TRUE(failed.get());

    std::promise<void> entered;
    auto started = entered.get_future();
    std::promise<void> release;
    auto gate = release.get_future().share();
    auto running = runtime.submit([&](std::stop_token) {
        entered.set_value();
        gate.wait();
    });
    started.get();
    std::atomic<int> calls {0};
    std::atomic<bool> executed {false};
    std::promise<bool> abandoned;
    auto cancelled = abandoned.get_future();
    runtime.submitWithCompletion(App::HostRuntime::Lane::Compute,
        [&](std::stop_token) { executed = true; },
        [&](std::future<void> result) {
            ++calls;
            EXPECT_EQ(result.wait_for(0ms), std::future_status::ready);
            // Completion must run outside the queue mutex.
            (void)runtime.queuedTaskCount();
            try { result.get(); abandoned.set_value(false); }
            catch (const std::runtime_error&) { abandoned.set_value(true); }
        });
    auto stopping = std::async(std::launch::async, [&] { runtime.shutdown(); });
    const auto observed = cancelled.wait_for(5s);
    release.set_value();
    running.get();
    stopping.get();
    ASSERT_EQ(observed, std::future_status::ready);
    EXPECT_TRUE(cancelled.get());
    EXPECT_FALSE(executed);
    EXPECT_EQ(calls, 1);
}

TEST_F(ApplicationTest, ParallelWorkKeepsItsCapturesAliveDuringShutdown)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(56);
    std::promise<void> entered;
    auto firstWorker = entered.get_future();
    std::promise<void> release;
    auto released = release.get_future().share();
    std::atomic_bool announced {false};
    auto group = std::async(std::launch::async, [&] {
        try {
            runtime.parallelFor(500, [&](std::size_t) {
                if (!announced.exchange(true)) {
                    entered.set_value();
                }
                released.wait();
            });
        }
        catch (const std::exception&) {
            // Shutdown can reject admission or cancel queued work. Either
            // outcome must still join every admitted, running callback.
        }
    });
    firstWorker.wait();
    auto stopping = std::async(std::launch::async, [&] { runtime.shutdown(); });
    const auto stateWhileWorkerHeld = group.wait_for(20ms);
    release.set_value();
    group.get();
    stopping.get();
    EXPECT_EQ(stateWhileWorkerHeld, std::future_status::timeout);
    EXPECT_FALSE(runtime.isAccepting());
}

TEST_F(ApplicationTest, RuntimeSubmissionsInheritOperationCancellation)
{
    App::HostRuntime runtime(1);
    std::stop_source request;
    std::promise<void> release;
    auto gate = release.get_future().share();
    std::future<void> direct;
    std::promise<void> notified;
    auto notification = notified.get_future();
    {
        Base::CancellationScope operation(request.get_token());
        direct = runtime.submit([gate](std::stop_token) {
            gate.wait();
            Base::CancellationScope::check();
        });
        runtime.submitWithCompletion(App::HostRuntime::Lane::Io,
            [gate](std::stop_token) {
                gate.wait();
                Base::CancellationScope::check();
            },
            [&](std::future<void> result) {
                EXPECT_NO_THROW(Base::CancellationScope::check());
                try { result.get(); notified.set_value(); }
                catch (...) { notified.set_exception(std::current_exception()); }
            });
    }
    // The caller's stack scope is already gone. Only owned stop tokens may
    // travel to the workers; neither worker receives the token explicitly.
    request.request_stop();
    release.set_value();
    EXPECT_THROW(direct.get(), Base::AbortException);
    EXPECT_THROW(notification.get(), Base::AbortException);
}

TEST_F(ApplicationTest, HelpingAnotherJobDoesNotLeakTheWaitingJobsCancellation)
{
    App::HostRuntime runtime(1);
    std::stop_source request;
    std::promise<void> entered;
    auto started = entered.get_future();
    std::promise<void> queued;
    auto ready = queued.get_future();
    std::future<bool> unrelated;
    auto parent = runtime.submit([&](std::stop_token) {
        Base::CancellationScope operation(request.get_token());
        request.request_stop();
        entered.set_value();
        ready.wait();
        // With one worker, wait must execute the unrelated queued job here.
        const bool result = runtime.wait(unrelated);
        EXPECT_THROW(Base::CancellationScope::check(), Base::AbortException);
        return result;
    });
    started.get();
    unrelated = runtime.submit([](std::stop_token) {
        Base::CancellationScope::check();
        return true;
    });
    queued.set_value();
    EXPECT_TRUE(parent.get());
}

TEST_F(ApplicationTest, ParallelWorkObservesOperationCancellationBetweenItems)
{
    App::HostRuntime runtime(1);
    std::stop_source request;
    Base::CancellationScope operation(request.get_token());
    unsigned calls = 0;
    EXPECT_THROW(runtime.parallelFor(500, [&](std::size_t) {
        ++calls;
        request.request_stop();
    }), Base::AbortException);
    EXPECT_EQ(calls, 1U);
    EXPECT_TRUE(runtime.isAccepting());
}

TEST_F(ApplicationTest, ParallelWorkCannotReportSuccessForAnInterruptedChunk)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(1);
    std::vector<std::size_t> values(500);
    runtime.parallelFor(values.size(), [&](std::size_t index) { values[index] = index + 1; });
    for (std::size_t index = 0; index < values.size(); ++index) {
        ASSERT_EQ(values[index], index + 1);
    }
    std::promise<void> release;
    auto gate = release.get_future().share();
    std::promise<void> observing;
    auto observerReady = observing.get_future();
    std::promise<void> stopped;
    auto stopSeen = stopped.get_future();
    auto observer = runtime.submit(App::HostRuntime::Lane::Io, [&](std::stop_token stop) {
        std::stop_callback notice(stop, [&] { stopped.set_value(); });
        observing.set_value();
        gate.wait();
    });
    observerReady.get();

    std::promise<void> entered;
    auto firstItem = entered.get_future();
    std::atomic_size_t calls {0};
    auto group = std::async(std::launch::async, [&] {
        runtime.parallelFor(500, [&](std::size_t) {
            if (calls.fetch_add(1) == 0) {
                entered.set_value();
                gate.wait();
            }
        });
    });
    firstItem.get();
    // All compute work is admitted and running, so this exercises a stopped
    // chunk, not the already-covered rejection/abandoned-future cases.
    auto stopping = std::async(std::launch::async, [&] { runtime.shutdown(); });
    const auto notified = stopSeen.wait_for(5s);
    release.set_value();
    EXPECT_THROW(group.get(), std::runtime_error);
    observer.get();
    stopping.get();
    ASSERT_EQ(notified, std::future_status::ready);
    EXPECT_EQ(calls.load(), std::size_t {1});
}

TEST_F(ApplicationTest, HostRuntimeWorkersAreEagerAndPersistent)
{
    auto& runtime = App::GetApplication().hostRuntime();
    const auto expectedWorkers = App::HostRuntime::workerBudget(
        std::thread::hardware_concurrency()
    );
    const auto expectedIoWorkers = App::HostRuntime::ioWorkerBudget(
        std::thread::hardware_concurrency()
    );
    const auto expectedDocumentWorkers = App::HostRuntime::documentWorkerBudget(
        std::thread::hardware_concurrency()
    );
    const auto expectedTotalWorkers =
        expectedWorkers + expectedIoWorkers + expectedDocumentWorkers;

    ASSERT_EQ(runtime.workerCount(), expectedWorkers);
    ASSERT_EQ(runtime.workerCount(App::HostRuntime::Lane::Io), expectedIoWorkers);
    ASSERT_EQ(
        runtime.workerCount(App::HostRuntime::Lane::Document),
        expectedDocumentWorkers
    );
    ASSERT_EQ(runtime.readyWorkerCount(), expectedWorkers);
    ASSERT_EQ(runtime.readyWorkerCount(App::HostRuntime::Lane::Io), expectedIoWorkers);
    ASSERT_EQ(
        runtime.readyWorkerCount(App::HostRuntime::Lane::Document),
        expectedDocumentWorkers
    );
    ASSERT_TRUE(runtime.isAccepting());

    std::mutex mutex;
    std::condition_variable allEntered;
    std::condition_variable releaseWorkers;
    std::condition_variable allCompleted;
    std::set<std::thread::id> workerThreads;
    std::size_t entered = 0;
    std::size_t completed = 0;
    bool release = false;
    std::vector<std::future<void>> work;
    work.reserve(expectedTotalWorkers);

    const auto submitBlockingTask = [&](App::HostRuntime::Lane lane) {
        work.emplace_back(runtime.submit(lane, [&](std::stop_token stopToken) {
            std::unique_lock lock(mutex);
            workerThreads.insert(std::this_thread::get_id());
            ++entered;
            allEntered.notify_one();
            releaseWorkers.wait(lock, [&] { return release || stopToken.stop_requested(); });
            ++completed;
            allCompleted.notify_one();
        }));
    };
    for (std::size_t index = 0; index < expectedWorkers; ++index) {
        submitBlockingTask(App::HostRuntime::Lane::Compute);
    }
    for (std::size_t index = 0; index < expectedIoWorkers; ++index) {
        submitBlockingTask(App::HostRuntime::Lane::Io);
    }
    for (std::size_t index = 0; index < expectedDocumentWorkers; ++index) {
        submitBlockingTask(App::HostRuntime::Lane::Document);
    }

    {
        std::unique_lock lock(mutex);
        allEntered.wait(lock, [&] { return entered == expectedTotalWorkers; });
        EXPECT_EQ(workerThreads.size(), expectedTotalWorkers);
        release = true;
    }
    releaseWorkers.notify_all();

    {
        std::unique_lock lock(mutex);
        allCompleted.wait(lock, [&] { return completed == expectedTotalWorkers; });
    }
    for (auto& result : work) {
        EXPECT_NO_THROW(result.get());
    }

    EXPECT_EQ(runtime.readyWorkerCount(), expectedWorkers);
    EXPECT_EQ(runtime.readyWorkerCount(App::HostRuntime::Lane::Io), expectedIoWorkers);
    EXPECT_EQ(
        runtime.readyWorkerCount(App::HostRuntime::Lane::Document),
        expectedDocumentWorkers
    );
}

TEST_F(ApplicationTest, HostRuntimeIsolationWorkersAreConcurrentPersistentAndRecoverable)
{
    using namespace std::chrono_literals;
    auto& runtime = App::GetApplication().hostRuntime();
    App::HostRuntime::IsolationConfiguration configuration;
    configuration.executable = HOST_RUNTIME_ISOLATION_HELPER;
    configuration.workerCount = 3;
    runtime.startIsolationWorkers(std::move(configuration));

    const auto readyDeadline = std::chrono::steady_clock::now() + 10s;
    while (runtime.readyIsolationWorkerCount() != 3
           && std::chrono::steady_clock::now() < readyDeadline) {
        std::this_thread::sleep_for(10ms);
    }
    ASSERT_EQ(runtime.isolationWorkerCount(), 3);
    ASSERT_EQ(runtime.readyIsolationWorkerCount(), 3);

    std::vector<App::HostRuntime::IsolationSubmission> firstWave;
    for (int index = 0; index < 3; ++index) {
        firstWave.push_back(runtime.submitIsolation("sleep:150"));
    }
    std::set<std::string> initialProcesses;
    for (auto& submission : firstWave) {
        const auto result = submission.completion.get();
        ASSERT_EQ(result.status, App::HostRuntime::IsolationStatus::Completed)
            << result.diagnostic;
        initialProcesses.insert(result.response.substr(0, result.response.find(':')));
    }
    EXPECT_EQ(initialProcesses.size(), 3);

    auto reused = runtime.submitIsolation("reused");
    const auto reusedResult = reused.completion.get();
    ASSERT_EQ(reusedResult.status, App::HostRuntime::IsolationStatus::Completed)
        << reusedResult.diagnostic;
    EXPECT_TRUE(initialProcesses.contains(
        reusedResult.response.substr(0, reusedResult.response.find(':'))
    ));

    auto cancelled = runtime.submitIsolation("sleep:5000");
    const auto activeDeadline = std::chrono::steady_clock::now() + 10s;
    while (runtime.activeIsolationTaskCount() != 1
           && std::chrono::steady_clock::now() < activeDeadline) {
        std::this_thread::sleep_for(10ms);
    }
    ASSERT_EQ(runtime.activeIsolationTaskCount(), 1);
    ASSERT_TRUE(runtime.cancelIsolation(cancelled.id));
    EXPECT_EQ(
        cancelled.completion.get().status,
        App::HostRuntime::IsolationStatus::Cancelled
    );

    auto afterCancellation = runtime.submitIsolation("after-cancellation");
    const auto recovered = afterCancellation.completion.get();
    ASSERT_EQ(recovered.status, App::HostRuntime::IsolationStatus::Completed)
        << recovered.diagnostic;
    EXPECT_NE(recovered.response.find(":after-cancellation"), std::string::npos);

    auto memoryBound = runtime.submitIsolation("sleep:5000", 1);
    const auto memoryResult = memoryBound.completion.get();
    EXPECT_EQ(memoryResult.status, App::HostRuntime::IsolationStatus::Failed);
    EXPECT_TRUE(memoryResult.memoryExceeded);
    EXPECT_GT(memoryResult.observedMemoryBytes, 1);

    auto afterMemoryLimit = runtime.submitIsolation("after-memory-limit");
    const auto memoryRecovered = afterMemoryLimit.completion.get();
    ASSERT_EQ(memoryRecovered.status, App::HostRuntime::IsolationStatus::Completed)
        << memoryRecovered.diagnostic;
    EXPECT_NE(memoryRecovered.response.find(":after-memory-limit"), std::string::npos);
}
