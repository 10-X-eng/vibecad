// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 Joao Matos
// SPDX-FileNotice: Part of the FreeCAD project.

/******************************************************************************
 *                                                                            *
 *   FreeCAD is free software: you can redistribute it and/or modify          *
 *   it under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1            *
 *   of the License, or (at your option) any later version.                   *
 *                                                                            *
 *   FreeCAD is distributed in the hope that it will be useful,               *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty              *
 *   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                  *
 *   See the GNU Lesser General Public License for more details.              *
 *                                                                            *
 *   You should have received a copy of the GNU Lesser General Public         *
 *   License along with FreeCAD. If not, see https://www.gnu.org/licenses     *
 *                                                                            *
 ******************************************************************************/

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <future>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <QAbstractEventDispatcher>
#include <QCoreApplication>
#include <QThread>
#include <QTimer>

#include <boost/scope_exit.hpp>
#include <gtest/gtest.h>

#include "App/Application.h"
#include "App/Document.h"
#include "App/FeatureTest.h"
#include "App/HostRuntime.h"
#include "App/HostWorkflow.h"
#include "Base/Interpreter.h"
#include <src/App/InitApplication.h>

using namespace std::chrono_literals;

namespace
{
App::HostWorkflow<int> orderedWorkerPhases(std::vector<std::thread::id>& threads)
{
    co_yield App::HostWorkflow<int>::Step {App::HostRuntime::Lane::Compute, [&](std::stop_token) {
        threads.push_back(std::this_thread::get_id());
    }};
    try {
        co_yield App::HostWorkflow<int>::Step {App::HostRuntime::Lane::Io, [&](std::stop_token) {
            threads.push_back(std::this_thread::get_id());
            throw std::runtime_error("phase failure");
        }};
    }
    catch (const std::runtime_error&) {
        co_return 7;
    }
    co_return 0;
}

App::HostWorkflow<int> singleComputePhase(std::thread::id& worker, std::thread::id& owner)
{
    co_yield App::HostWorkflow<int>::Step {App::HostRuntime::Lane::Compute, [&](std::stop_token) {
        worker = std::this_thread::get_id();
    }};
    owner = std::this_thread::get_id();
    co_return 11;
}

App::HostWorkflow<int> phaseWithOwnerCleanup(std::promise<std::thread::id>& destroyedOn)
{
    struct OwnerResource
    {
        std::promise<std::thread::id>& destroyedOn;
        ~OwnerResource() { destroyedOn.set_value(std::this_thread::get_id()); }
    } resource {destroyedOn};
    co_yield App::HostWorkflow<int>::Step {
        App::HostRuntime::Lane::Compute, [](std::stop_token) {}
    };
    co_return 1;
}

App::HostWorkflow<int> phaseWithCapturedOwnerResource(std::promise<std::thread::id>& destroyedOn)
{
    struct OwnerResource
    {
        std::promise<std::thread::id>& destroyedOn;
        explicit OwnerResource(std::promise<std::thread::id>& result) : destroyedOn(result) {}
        ~OwnerResource() { destroyedOn.set_value(std::this_thread::get_id()); }
    };
    auto resource = std::make_shared<OwnerResource>(destroyedOn);
    App::HostWorkflow<int>::Step phase {
        App::HostRuntime::Lane::Compute, [resource](std::stop_token) {}
    };
    co_yield std::move(phase);
    co_return 1;
}

class QueuedOwner
{
public:
    QueuedOwner()
    {
        thread = std::thread([this] { run(); });
        started.get_future().wait();
    }

    QueuedOwner(const QueuedOwner&) = delete;
    QueuedOwner& operator=(const QueuedOwner&) = delete;

    ~QueuedOwner()
    {
        {
            std::lock_guard lock(mutex);
            stop = true;
        }
        cv.notify_one();
        thread.join();
    }

    std::thread::id id() const
    {
        return ownerId;
    }

    void dispatch(std::function<void()> work)
    {
        {
            std::lock_guard lock(mutex);
            queue.push_back(std::move(work));
        }
        cv.notify_one();
    }

private:
    void run()
    {
        ownerId = std::this_thread::get_id();
        started.set_value();
        std::unique_lock lock(mutex);
        while (true) {
            cv.wait(lock, [&] { return stop || !queue.empty(); });
            if (queue.empty()) {
                if (stop) {
                    break;
                }
                continue;
            }
            auto work = std::move(queue.front());
            queue.pop_front();
            lock.unlock();
            work();
            lock.lock();
        }
    }

    std::mutex mutex;
    std::condition_variable cv;
    std::deque<std::function<void()>> queue;
    std::thread thread;
    std::thread::id ownerId;
    std::promise<void> started;
    bool stop {false};
};

std::thread::id queuedOwnerId;
std::mutex queuedOwnerMutex;
std::deque<std::function<void()>> queuedOwnerWork;

bool queuedOwnerIsMainThread()
{
    return std::this_thread::get_id() == queuedOwnerId;
}

void queuedOwnerInvoke(std::function<void()>&& work, bool blocking)
{
    if (blocking || std::this_thread::get_id() == queuedOwnerId) {
        work();
        return;
    }
    std::lock_guard lock(queuedOwnerMutex);
    queuedOwnerWork.push_back(std::move(work));
}

void queuedOwnerInvokeNextFrame(std::function<void()>&& work, bool blocking)
{
    if (blocking) {
        work();
        return;
    }
    std::lock_guard lock(queuedOwnerMutex);
    queuedOwnerWork.push_back(std::move(work));
}

std::deque<std::function<void()>> takeQueuedOwnerWork()
{
    std::lock_guard lock(queuedOwnerMutex);
    std::deque<std::function<void()>> pending;
    pending.swap(queuedOwnerWork);
    return pending;
}
}

TEST(MainThreadCleanupTest, MissingHookNeverRunsCleanupInline)
{
    ASSERT_FALSE(App::MainThreadSignalConfig::hasCleanupHook());
    bool invoked = false;
    EXPECT_THROW(App::MainThreadSignalConfig::invokeCleanup([&] { invoked = true; }),
                 std::runtime_error);
    EXPECT_FALSE(invoked);
}

TEST(MainThreadCleanupTest, CleanupHasAnIndependentOwnerHook)
{
    queuedOwnerId = std::this_thread::get_id();
    takeQueuedOwnerWork();
    App::MainThreadSignalConfig::setCleanupHook([](std::function<void()>&& cleanup) {
        std::lock_guard lock(queuedOwnerMutex);
        queuedOwnerWork.push_back(std::move(cleanup));
    });
    BOOST_SCOPE_EXIT_ALL(&) {
        App::MainThreadSignalConfig::setCleanupHook(nullptr);
        takeQueuedOwnerWork();
    };
    ASSERT_TRUE(App::MainThreadSignalConfig::hasCleanupHook());
    std::thread::id cleanedOn;
    std::thread worker([&] {
        App::MainThreadSignalConfig::invokeCleanup([&] {
            cleanedOn = std::this_thread::get_id();
        });
    });
    worker.join();
    EXPECT_EQ(cleanedOn, std::thread::id {});
    auto cleanup = takeQueuedOwnerWork();
    ASSERT_EQ(cleanup.size(), 1);
    cleanup.front()();
    EXPECT_EQ(cleanedOn, queuedOwnerId);
}

TEST(HostWorkflowTest, WorkerErrorsReturnToTheSuspendedPhase)
{
    App::HostRuntime runtime(4);
    std::vector<std::thread::id> threads;
    auto workflow = orderedWorkerPhases(threads);
    while (workflow.advance()) {
        auto step = workflow.step();
        auto completion = runtime.submit(step.lane, std::move(step.work));
        try {
            completion.get();
        }
        catch (...) {
            workflow.failStep(std::current_exception());
        }
    }
    EXPECT_EQ(workflow.takeResult(), 7);
    ASSERT_EQ(threads.size(), 2);
    for (const auto thread : threads) {
        EXPECT_NE(thread, std::this_thread::get_id());
    }

    // The existing synchronous API must run the same phase/exception logic,
    // not a second implementation of document restoration.
    threads.clear();
    EXPECT_EQ(orderedWorkerPhases(threads).runSynchronously(), 7);
    ASSERT_EQ(threads.size(), 2);
    EXPECT_EQ(threads.front(), std::this_thread::get_id());
}

TEST(HostWorkflowTest, RunAsyncResumesOnTheOwnerAndRunsWorkOffIt)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(2);
    QueuedOwner owner;
    std::thread::id worker;
    std::thread::id resumed;
    std::promise<std::thread::id> finishedOn;
    auto future = singleComputePhase(worker, resumed).runAsync(
        runtime,
        [&](std::function<void()> resume) { owner.dispatch(std::move(resume)); },
        [&] { finishedOn.set_value(std::this_thread::get_id()); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(future.get(), 11);
    EXPECT_EQ(resumed, owner.id());
    EXPECT_EQ(finishedOn.get_future().get(), owner.id());
    EXPECT_NE(worker, std::thread::id {});
    EXPECT_NE(worker, owner.id());
    EXPECT_NE(worker, std::this_thread::get_id());
}

TEST(HostWorkflowTest, RunAsyncWorkerErrorsReturnToTheOwner)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(2);
    QueuedOwner owner;
    std::vector<std::thread::id> threads;
    std::promise<std::thread::id> finishedOn;
    auto future = orderedWorkerPhases(threads).runAsync(
        runtime,
        [&](std::function<void()> resume) { owner.dispatch(std::move(resume)); },
        [&] { finishedOn.set_value(std::this_thread::get_id()); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(future.get(), 7);
    ASSERT_EQ(threads.size(), 2);
    for (const auto thread : threads) {
        EXPECT_NE(thread, owner.id());
        EXPECT_NE(thread, std::this_thread::get_id());
    }
    EXPECT_EQ(finishedOn.get_future().get(), owner.id());
}

TEST(HostWorkflowTest, RunAsyncOwnerDispatchFailureCompletesTheFuture)
{
    using namespace std::chrono_literals;
    App::HostRuntime runtime(1);
    QueuedOwner owner;
    std::thread::id worker;
    std::thread::id resumed;
    std::atomic<int> dispatches {0};
    std::promise<std::thread::id> finishedOn;
    std::atomic<int> finishedCalls {0};
    auto future = singleComputePhase(worker, resumed).runAsyncWithCleanup(
        runtime,
        [&](std::function<void()> resume) {
            if (dispatches.fetch_add(1) >= 1) {
                throw std::runtime_error("owner dispatch failed");
            }
            owner.dispatch(std::move(resume));
        },
        [&](std::function<void()> cleanup) { owner.dispatch(std::move(cleanup)); },
        [&] { ++finishedCalls; finishedOn.set_value(std::this_thread::get_id()); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready)
        << "Owner dispatch failure must complete the workflow future; HostRuntime "
           "swallows the completion exception and the coordinator currently hangs";
    EXPECT_THROW(future.get(), std::runtime_error);
    EXPECT_EQ(resumed, std::thread::id {});
    EXPECT_NE(worker, std::thread::id {});
    EXPECT_EQ(finishedOn.get_future().get(), owner.id());
    EXPECT_EQ(finishedCalls.load(), 1);
}

TEST(HostWorkflowTest, DispatchFailureDestroysSuspendedFrameOnOwner)
{
    App::HostRuntime runtime(1);
    QueuedOwner owner;
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::atomic<int> dispatches {0};
    auto future = phaseWithOwnerCleanup(destroyedOn).runAsyncWithCleanup(
        runtime,
        [&](std::function<void()> resume) {
            if (dispatches.fetch_add(1) != 0) {
                throw std::runtime_error("owner dispatch failed");
            }
            owner.dispatch(std::move(resume));
        },
        [&](std::function<void()> cleanup) { owner.dispatch(std::move(cleanup)); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_THROW(future.get(), std::runtime_error);
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id())
        << "Suspended document resources must be released by their owner";
}

TEST(HostWorkflowTest, OwnerSubmissionFailureUsesCleanupQueue)
{
    App::HostRuntime runtime(1);
    runtime.shutdown();
    QueuedOwner owner;
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::atomic<int> dispatches {0};
    std::atomic<int> cleanupDispatches {0};
    auto future = phaseWithOwnerCleanup(destroyedOn).runAsyncWithCleanup(
        runtime,
        [&](std::function<void()> resume) {
            if (dispatches.fetch_add(1) != 0) {
                throw std::runtime_error("normal queue stopped");
            }
            owner.dispatch(std::move(resume));
        },
        [&](std::function<void()> cleanup) {
            ++cleanupDispatches;
            owner.dispatch(std::move(cleanup));
        }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_THROW(future.get(), std::runtime_error);
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id());
    EXPECT_EQ(cleanupDispatches.load(), 1)
        << "Owner-side cancellation must also defer cleanup until after worker joining";
}

TEST(HostWorkflowTest, RejectedDispatchUsesIndependentOwnerCleanup)
{
    App::HostRuntime runtime(1);
    QueuedOwner owner;
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::promise<std::thread::id> finishedOn;
    std::atomic<int> dispatches {0};
    std::atomic<int> cleanupDispatches {0};
    auto future = phaseWithOwnerCleanup(destroyedOn).runAsyncWithCleanup(
        runtime,
        [&](std::function<void()> resume) {
            if (dispatches.fetch_add(1) != 0) {
                throw std::runtime_error("normal queue stopped");
            }
            owner.dispatch(std::move(resume));
        },
        [&](std::function<void()> cleanup) {
            ++cleanupDispatches;
            owner.dispatch(std::move(cleanup));
        },
        [&] { finishedOn.set_value(std::this_thread::get_id()); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_THROW(future.get(), std::runtime_error);
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id());
    EXPECT_EQ(finishedOn.get_future().get(), owner.id());
    EXPECT_EQ(dispatches.load(), 2);
    EXPECT_EQ(cleanupDispatches.load(), 1);
}

TEST(HostWorkflowTest, DroppedOwnerDispatchStillCleansUpOnOwner)
{
    App::HostRuntime runtime(1);
    QueuedOwner owner;
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::promise<void> discarded;
    auto discardedOnOwner = discarded.get_future();
    std::atomic<int> dispatches {0};
    std::atomic<int> finishedCalls {0};
    auto future = phaseWithOwnerCleanup(destroyedOn).runAsync(
        runtime,
        [&](std::function<void()> resume) {
            if (dispatches.fetch_add(1) == 0) {
                owner.dispatch(std::move(resume));
                return;
            }
            // Model a GUI queue accepting a completion and then discarding it
            // on shutdown while the submitting worker still owns its driver.
            owner.dispatch([resume = std::move(resume), &discarded]() mutable {
                resume = {};
                discarded.set_value();
            });
            discardedOnOwner.wait();
        },
        [&] { ++finishedCalls; }
    );
    ASSERT_EQ(discardedOnOwner.wait_for(2s), std::future_status::ready);
    runtime.shutdown();
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_ANY_THROW(future.get());
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id());
    EXPECT_EQ(finishedCalls.load(), 1);
}

TEST(HostWorkflowTest, WorkerDoesNotRetainPhaseCapturesAfterOwnerCompletion)
{
    App::HostRuntime runtime(1);
    QueuedOwner owner;
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::promise<void> finished;
    auto finishedFuture = finished.get_future().share();
    std::atomic<int> dispatches {0};
    auto future = phaseWithCapturedOwnerResource(destroyedOn).runAsync(
        runtime,
        [&](std::function<void()> resume) {
            const bool completion = dispatches.fetch_add(1) != 0;
            owner.dispatch(std::move(resume));
            if (completion) {
                // Hold the worker completion on this side of dispatch until
                // the owner has destroyed its frame and notified the caller.
                EXPECT_EQ(finishedFuture.wait_for(2s), std::future_status::ready);
            }
        },
        [&] { finished.set_value(); }
    );
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(future.get(), 1);
    runtime.shutdown();
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id());
}

TEST(HostWorkflowTest, QueuedPhaseCancellationReleasesCapturesOnOwner)
{
    QueuedOwner owner;
    App::HostRuntime runtime(1);
    std::promise<void> blockerStarted;
    auto started = blockerStarted.get_future();
    auto blocker = runtime.submit(App::HostRuntime::Lane::Compute, [&](std::stop_token stop) {
        std::promise<void> released;
        auto release = released.get_future();
        std::stop_callback cancelled(stop, [&] { released.set_value(); });
        blockerStarted.set_value();
        release.get();
    });
    ASSERT_EQ(started.wait_for(2s), std::future_status::ready);
    std::promise<std::thread::id> destroyedOn;
    auto destroyed = destroyedOn.get_future();
    std::promise<void> phaseQueued;
    auto queued = phaseQueued.get_future();
    std::promise<void> finished;
    auto finish = finished.get_future().share();
    std::atomic<int> dispatches {0};
    auto future = phaseWithCapturedOwnerResource(destroyedOn).runAsync(
        runtime,
        [&](std::function<void()> resume) {
            const bool initial = dispatches.fetch_add(1) == 0;
            owner.dispatch([&, initial, resume = std::move(resume)] {
                resume();
                if (initial) { phaseQueued.set_value(); }
            });
            if (!initial) {
                EXPECT_EQ(finish.wait_for(2s), std::future_status::ready);
            }
        },
        [&] { finished.set_value(); }
    );
    ASSERT_EQ(queued.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(runtime.queuedTaskCount(App::HostRuntime::Lane::Compute), 1);
    runtime.shutdown();
    blocker.get();
    ASSERT_EQ(future.wait_for(2s), std::future_status::ready);
    EXPECT_THROW(future.get(), std::runtime_error);
    ASSERT_EQ(destroyed.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(destroyed.get(), owner.id());
}

TEST(HostWorkflowTest, InitialDispatchFailurePropagatesWithoutInlineCompletion)
{
    App::HostRuntime runtime(1);
    std::thread::id worker, resumed;
    bool finished = false;
    EXPECT_THROW(
        singleComputePhase(worker, resumed).runAsync(
            runtime,
            [](std::function<void()>) { throw std::runtime_error("initial dispatch failed"); },
            [&] { finished = true; }
        ),
        std::runtime_error
    );
    EXPECT_FALSE(finished)
        << "The caller has not received or stored the workflow future yet";
    EXPECT_EQ(worker, std::thread::id {});
    EXPECT_EQ(resumed, std::thread::id {});
}

class AsyncRecomputeTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("async_recompute");
        _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
    }

    void TearDown() override
    {
        if (!_docName.empty() && App::GetApplication().getDocument(_docName.c_str())) {
            App::GetApplication().closeDocument(_docName.c_str());
        }
    }

    std::string _docName;
    App::Document* _doc {};
};

TEST_F(AsyncRecomputeTest, CloseDocumentCancelsWithoutBlockingCaller)
{
    auto* object = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "BlockingFeature")
    );
    auto* queuedObject = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "QueuedFeature")
    );
    ASSERT_NE(object, nullptr);
    ASSERT_NE(queuedObject, nullptr);

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::FeatureTestAsyncBlocker::releaseBlocker();
    };

    object->touch();
    queuedObject->touch();

    App::GetApplication().queueRecomputeRequest(App::RecomputeRequest::fromDocument(*_doc));

    ASSERT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2, 2s));

    const auto closeStarted = std::chrono::steady_clock::now();
    EXPECT_FALSE(App::GetApplication().closeDocument(_docName.c_str()));
    EXPECT_LT(std::chrono::steady_clock::now() - closeStarted, 50ms);

    App::FeatureTestAsyncBlocker::releaseBlocker();
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (App::GetApplication().hasPendingRecomputeRequest(_docName)
           && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }
    ASSERT_FALSE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    EXPECT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2, 0ms));
    EXPECT_TRUE(App::GetApplication().closeDocument(_docName.c_str()));

    _doc = nullptr;
}

TEST_F(AsyncRecomputeTest, SynchronousGuiCallerKeepsEventsAndMutationLeaseLive)
{
    int argc = 1;
    char name[] = "recompute-gui-owner";
    char* argv[] = {name, nullptr};
    std::unique_ptr<QCoreApplication> application;
    if (!QCoreApplication::instance()) {
        application = std::make_unique<QCoreApplication>(argc, argv);
    }
    ASSERT_FALSE(App::MainThreadSignalConfig::hasHooks());
    // App-only fixture: no GUI observers. The real packaged GUI gate exercises
    // queued property notifications; this test isolates owner event liveness.
    App::MainThreadSignalConfig::setHooks(
        [] { return QThread::currentThread() == QCoreApplication::instance()->thread(); },
        [](std::function<void()>&& work, bool) { work(); });
    BOOST_SCOPE_EXIT_ALL(&) { App::MainThreadSignalConfig::setHooks(nullptr, nullptr); };

    auto* object = _doc->addObject("App::FeatureTestAsyncBlocker", "OwnerWaitFeature");
    ASSERT_NE(object, nullptr);
    App::FeatureTestAsyncBlocker::resetBlocker();
    object->touch();
    bool timerRan = false;
    bool protectedDocument = false;
    QObject timerOwner;
    QTimer::singleShot(0, &timerOwner, [&] {
        timerRan = true;
        protectedDocument = _doc->isCooperativeMutationActive();
        App::FeatureTestAsyncBlocker::releaseBlocker();
    });
    // Release the old blocked implementation so red is a failed assertion,
    // not an indefinitely hung test process. This is a test watchdog only.
    std::jthread watchdog([] {
        App::FeatureTestAsyncBlocker::waitUntilStarted(1, 2s);
        std::this_thread::sleep_for(100ms);
        App::FeatureTestAsyncBlocker::releaseBlocker();
    });
    // A completed worker must interrupt the current dispatcher wait itself,
    // not rely on subsequent mouse input or a timer to let recompute return.
    bool neededWakeup = false;
    QTimer completionWatchdog;
    completionWatchdog.setSingleShot(true);
    QObject::connect(&completionWatchdog, &QTimer::timeout, [&] {
        neededWakeup = true;
        QAbstractEventDispatcher::instance()->interrupt();
    });
    completionWatchdog.start(2000);
    EXPECT_GE(_doc->recompute(), 1);
    EXPECT_FALSE(neededWakeup);
    EXPECT_TRUE(timerRan);
    EXPECT_TRUE(protectedDocument);
    EXPECT_FALSE(_doc->isCooperativeMutationActive());
}

TEST_F(AsyncRecomputeTest, SynchronousGuiCallerRecomputesWorkTouchedDuringItsActiveUpdate)
{
    int argc = 1;
    char name[] = "recompute-gui-follow-up";
    char* argv[] = {name, nullptr};
    std::unique_ptr<QCoreApplication> application;
    if (!QCoreApplication::instance()) {
        application = std::make_unique<QCoreApplication>(argc, argv);
    }
    ASSERT_FALSE(App::MainThreadSignalConfig::hasHooks());
    App::MainThreadSignalConfig::setHooks(
        [] { return QThread::currentThread() == QCoreApplication::instance()->thread(); },
        [](std::function<void()>&& work, bool) { work(); }
    );
    BOOST_SCOPE_EXIT_ALL(&) { App::MainThreadSignalConfig::setHooks(nullptr, nullptr); };

    auto* blocker = _doc->addObject("App::FeatureTestAsyncBlocker", "FollowUpBlocker");
    auto* followUp = _doc->addObject("App::FeatureTest", "FollowUpFeature");
    ASSERT_NE(blocker, nullptr);
    ASSERT_NE(followUp, nullptr);
    followUp->purgeTouched();
    ASSERT_FALSE(followUp->isTouched());

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&) { App::FeatureTestAsyncBlocker::releaseBlocker(); };
    blocker->touch();
    bool nestedRequestRan = false;
    QObject timerOwner;
    QTimer::singleShot(0, &timerOwner, [&] {
        ASSERT_TRUE(_doc->isCooperativeMutationActive());
        followUp->touch();
        nestedRequestRan = true;
        EXPECT_EQ(_doc->recompute(), 0);
        App::FeatureTestAsyncBlocker::releaseBlocker();
    });

    EXPECT_GE(_doc->recompute(), 2);
    EXPECT_TRUE(nestedRequestRan);
    EXPECT_FALSE(followUp->isTouched());
    EXPECT_FALSE(_doc->isCooperativeMutationActive());
}

TEST_F(AsyncRecomputeTest, RequestedCloseRunsOnceAfterPresentationReleases)
{
    queuedOwnerId = std::this_thread::get_id();
    takeQueuedOwnerWork();
    App::MainThreadSignalConfig::setHooks(&queuedOwnerIsMainThread, &queuedOwnerInvokeNextFrame);
    BOOST_SCOPE_EXIT_ALL(&) {
        App::MainThreadSignalConfig::setHooks(nullptr, nullptr);
        takeQueuedOwnerWork();
    };

    const std::string documentName = _doc->getName();
    _doc->beginCooperativeMutation();
    _doc->beginPresentationUpdate();
    _doc->beginVisualUpdate();
    EXPECT_TRUE(App::GetApplication().requestCloseDocument(documentName.c_str()));
    EXPECT_TRUE(App::GetApplication().requestCloseDocument(documentName.c_str()));
    EXPECT_NE(App::GetApplication().getDocument(documentName.c_str()), nullptr);

    _doc->endPresentationUpdate();
    _doc->endVisualUpdate();
    _doc->endCooperativeMutation();
    EXPECT_NE(App::GetApplication().getDocument(documentName.c_str()), nullptr);
    auto queued = takeQueuedOwnerWork();
    ASSERT_EQ(queued.size(), 1);
    queued.front()();
    EXPECT_EQ(App::GetApplication().getDocument(documentName.c_str()), nullptr);
    _doc = nullptr;
}

TEST_F(AsyncRecomputeTest, VisualUpdateRetainsLifetimeWithoutBlockingRecompute)
{
    std::vector<bool> lifetimeTransitions;
    std::vector<bool> mutationTransitions;
    auto lifetimeConnection = _doc->signalPresentationUpdateChanged.connect(
        [&lifetimeTransitions](const App::Document&, bool active) {
            lifetimeTransitions.push_back(active);
        }
    );
    auto mutationConnection = _doc->signalMutationBlockingPresentationUpdateChanged.connect(
        [&mutationTransitions](const App::Document&, bool active) {
            mutationTransitions.push_back(active);
        }
    );
    auto* object = dynamic_cast<App::FeatureTest*>(
        _doc->addObject("App::FeatureTest", "VisualUpdateFeature")
    );
    ASSERT_NE(object, nullptr);
    object->touch();
    _doc->beginVisualUpdate();

    EXPECT_TRUE(_doc->isPresentationUpdateActive());
    EXPECT_FALSE(_doc->isMutationBlockingPresentationUpdateActive());
    EXPECT_EQ(lifetimeTransitions, std::vector<bool>({true}));
    EXPECT_TRUE(mutationTransitions.empty());
    EXPECT_GE(_doc->recompute(), 1);
    EXPECT_FALSE(object->isTouched());
    EXPECT_TRUE(_doc->isPresentationUpdateActive());
    EXPECT_EQ(mutationTransitions, std::vector<bool>({true, false}));

    _doc->beginPresentationUpdate();
    EXPECT_TRUE(_doc->isMutationBlockingPresentationUpdateActive());
    EXPECT_EQ(lifetimeTransitions, std::vector<bool>({true}));
    EXPECT_EQ(mutationTransitions, std::vector<bool>({true, false, true}));
    _doc->endPresentationUpdate();
    EXPECT_FALSE(_doc->isMutationBlockingPresentationUpdateActive());
    EXPECT_TRUE(_doc->isPresentationUpdateActive());
    EXPECT_EQ(lifetimeTransitions, std::vector<bool>({true}));
    EXPECT_EQ(mutationTransitions, std::vector<bool>({true, false, true, false}));

    _doc->endVisualUpdate();
    EXPECT_FALSE(_doc->isPresentationUpdateActive());
    EXPECT_EQ(lifetimeTransitions, std::vector<bool>({true, false}));
}

TEST_F(AsyncRecomputeTest, GuiRecomputeDuringPublicationRunsAtNextStableBoundary)
{
    int argc = 1;
    char name[] = "recompute-gui-publication-follow-up";
    char* argv[] = {name, nullptr};
    std::unique_ptr<QCoreApplication> application;
    if (!QCoreApplication::instance()) {
        application = std::make_unique<QCoreApplication>(argc, argv);
    }

    queuedOwnerId = std::this_thread::get_id();
    takeQueuedOwnerWork();
    App::MainThreadSignalConfig::setHooks(&queuedOwnerIsMainThread, &queuedOwnerInvokeNextFrame);
    BOOST_SCOPE_EXIT_ALL(&) {
        App::MainThreadSignalConfig::setHooks(nullptr, nullptr);
        takeQueuedOwnerWork();
    };

    auto* object = dynamic_cast<App::FeatureTest*>(
        _doc->addObject("App::FeatureTest", "DeferredGuiRecomputeFeature")
    );
    ASSERT_NE(object, nullptr);
    object->touch();
    _doc->beginPresentationUpdate();

    EXPECT_EQ(_doc->recompute(), 0);
    EXPECT_TRUE(object->isTouched());
    EXPECT_TRUE(takeQueuedOwnerWork().empty());

    _doc->endPresentationUpdate();
    auto queued = takeQueuedOwnerWork();
    ASSERT_EQ(queued.size(), 1);
    queued.front()();

    EXPECT_FALSE(object->isTouched());
    EXPECT_FALSE(_doc->isCooperativeMutationActive());
    EXPECT_FALSE(_doc->isMutationBlockingPresentationUpdateActive());
}

TEST_F(AsyncRecomputeTest, WorkerSafetyIsCheckedFromRequest)
{
    auto* safeObject = dynamic_cast<App::FeatureTest*>(
        _doc->addObject("App::FeatureTest", "SafeFeature")
    );
    auto* unsafeObject = dynamic_cast<App::FeatureTestAttribute*>(
        _doc->addObject("App::FeatureTestAttribute", "UnsafeFeature")
    );

    ASSERT_NE(safeObject, nullptr);
    ASSERT_NE(unsafeObject, nullptr);

    EXPECT_TRUE(
        App::GetApplication().canRecomputeRequestOnWorker(
            App::RecomputeRequest::fromDocumentObject(*safeObject)
        )
    );
    EXPECT_TRUE(
        App::GetApplication().canRecomputeRequestOnWorker(
            App::RecomputeRequest::fromDocumentObject(*unsafeObject)
        )
    );
    EXPECT_TRUE(
        App::GetApplication().canRecomputeRequestOnWorker(App::RecomputeRequest::fromDocument(*_doc))
    );

    safeObject->Source1.setValue(unsafeObject);
    EXPECT_TRUE(
        App::GetApplication().canRecomputeRequestOnWorker(
            App::RecomputeRequest::fromDocumentObject(*safeObject, false)
        )
    );
    EXPECT_TRUE(
        App::GetApplication().canRecomputeRequestOnWorker(
            App::RecomputeRequest::fromDocumentObject(*safeObject, true)
        )
    );
}

TEST_F(AsyncRecomputeTest, MixedPythonAndNativeBatchUsesBackgroundRuntime)
{
    auto* safeObject = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "SafeFeature")
    );
    auto* unsafeObject = dynamic_cast<App::FeatureTestAttribute*>(
        _doc->addObject("App::FeatureTestAttribute", "UnsafeFeature")
    );
    ASSERT_NE(safeObject, nullptr);
    ASSERT_NE(unsafeObject, nullptr);

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::FeatureTestAsyncBlocker::releaseBlocker();
    };

    safeObject->touch();
    unsafeObject->touch();
    std::vector<App::RecomputeRequest> requests;
    requests.push_back(App::RecomputeRequest::fromDocumentObject(*safeObject));
    requests.push_back(App::RecomputeRequest::fromDocumentObject(*unsafeObject));

    EXPECT_TRUE(App::GetApplication().tryQueueRecomputeRequests(std::move(requests)));
    EXPECT_TRUE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    EXPECT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2s));
    App::FeatureTestAsyncBlocker::releaseBlocker();
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (App::GetApplication().hasPendingRecomputeRequest(_docName)
           && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }
    EXPECT_FALSE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    EXPECT_FALSE(App::FeatureTestAsyncBlocker::waitUntilStarted(2, 50ms));

    // A public recompute invoked from Python enters App while holding the
    // interpreter lock. Waiting for worker-side Python must release that lock.
    Base::Interpreter().runString(
        "import types; App.ActiveDocument.UnsafeFeature.Object = "
        "types.SimpleNamespace(Name='worker-safe'); App.ActiveDocument.recompute()"
    );
    EXPECT_FALSE(unsafeObject->isTouched());
    EXPECT_FALSE(unsafeObject->isError());
}

TEST_F(AsyncRecomputeTest, IndependentDocumentsRunConcurrently)
{
    const std::string secondName
        = App::GetApplication().getUniqueDocumentName("async_recompute_parallel");
    App::Document* second = App::GetApplication().newDocument(secondName.c_str(), "testUser");
    ASSERT_NE(second, nullptr);

    auto* firstObject = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "FirstBlockingFeature")
    );
    auto* secondObject = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        second->addObject("App::FeatureTestAsyncBlocker", "SecondBlockingFeature")
    );
    ASSERT_NE(firstObject, nullptr);
    ASSERT_NE(secondObject, nullptr);

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::FeatureTestAsyncBlocker::releaseBlocker();
        if (App::GetApplication().getDocument(secondName.c_str())) {
            App::GetApplication().closeDocument(secondName.c_str());
        }
    };

    firstObject->touch();
    secondObject->touch();
    App::GetApplication().queueRecomputeRequest(
        App::RecomputeRequest::fromDocumentObject(*firstObject)
    );
    App::GetApplication().queueRecomputeRequest(
        App::RecomputeRequest::fromDocumentObject(*secondObject)
    );

    EXPECT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2, 2s));
    App::FeatureTestAsyncBlocker::releaseBlocker();

    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while ((App::GetApplication().hasPendingRecomputeRequest(_docName)
            || App::GetApplication().hasPendingRecomputeRequest(secondName))
           && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }
    ASSERT_FALSE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    ASSERT_FALSE(App::GetApplication().hasPendingRecomputeRequest(secondName));
    EXPECT_TRUE(App::GetApplication().closeDocument(secondName.c_str()));
}

TEST_F(AsyncRecomputeTest, IndependentObjectsInOneDocumentRunConcurrently)
{
    constexpr std::size_t objectCount = 50;
    const auto parallelCount = std::min(
        objectCount,
        App::GetApplication().hostRuntime().workerCount(App::HostRuntime::Lane::Compute)
    );
    if (parallelCount < 2) {
        GTEST_SKIP() << "At least two compute workers are required to prove parallel execution";
    }

    std::vector<App::FeatureTestAsyncBlocker*> objects;
    objects.reserve(objectCount);
    for (std::size_t index = 0; index < objectCount; ++index) {
        const auto name = "IndependentFeature" + std::to_string(index);
        auto* object = dynamic_cast<App::FeatureTestAsyncBlocker*>(
            _doc->addObject("App::FeatureTestAsyncBlocker", name.c_str())
        );
        ASSERT_NE(object, nullptr);
        objects.push_back(object);
    }

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::FeatureTestAsyncBlocker::releaseBlocker();
    };

    for (auto* object : objects) {
        object->touch();
    }
    App::GetApplication().queueRecomputeRequest(App::RecomputeRequest::fromDocument(*_doc));

    const bool saturatedComputeLane = App::FeatureTestAsyncBlocker::waitUntilStarted(
        parallelCount,
        2s
    );
    App::FeatureTestAsyncBlocker::releaseBlocker();

    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (App::GetApplication().hasPendingRecomputeRequest(_docName)
           && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }

    EXPECT_TRUE(saturatedComputeLane);
    EXPECT_FALSE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    EXPECT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(objectCount, 0ms));
    for (const auto* object : objects) {
        EXPECT_FALSE(object->isTouched());
        EXPECT_FALSE(object->isError());
        EXPECT_EQ(object->ExecutionCount.getValue(), 1);
    }
}

TEST_F(AsyncRecomputeTest, PendingStateCoversQueuedAndInFlightWork)
{
    auto* object = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "BlockingFeature")
    );
    ASSERT_NE(object, nullptr);

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&)
    {
        App::FeatureTestAsyncBlocker::releaseBlocker();
    };

    object->touch();
    ASSERT_TRUE(App::GetApplication().tryQueueRecomputeRequest(
        App::RecomputeRequest::fromDocumentObject(*object)
    ));
    EXPECT_TRUE(App::GetApplication().hasPendingRecomputeRequest(_docName));
    ASSERT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2s));
    EXPECT_TRUE(App::GetApplication().hasPendingRecomputeRequest(_docName));

    App::FeatureTestAsyncBlocker::releaseBlocker();
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (
        App::GetApplication().hasPendingRecomputeRequest(_docName)
        && std::chrono::steady_clock::now() < deadline
    ) {
        std::this_thread::sleep_for(10ms);
    }
    EXPECT_FALSE(App::GetApplication().hasPendingRecomputeRequest(_docName));
}

TEST_F(AsyncRecomputeTest, FinishedSignalDoesNotAdoptAReplacementDocument)
{
    using namespace std::chrono_literals;
    auto& application = App::GetApplication();
    queuedOwnerId = std::this_thread::get_id();
    takeQueuedOwnerWork();
    App::MainThreadSignalConfig::setHooks(&queuedOwnerIsMainThread, &queuedOwnerInvoke);
    BOOST_SCOPE_EXIT_ALL(&) {
        App::MainThreadSignalConfig::setHooks(nullptr, nullptr);
        takeQueuedOwnerWork();
    };

    auto* object = dynamic_cast<App::FeatureTestAsyncBlocker*>(
        _doc->addObject("App::FeatureTestAsyncBlocker", "BlockingFeature")
    );
    ASSERT_NE(object, nullptr);
    const std::string originalUid = _doc->Uid.getValueStr();

    std::atomic<int> finishedAfterReplacement {0};
    fastsignals::scoped_connection finished =
        application.signalRecomputeRequestFinished.connect([&](const std::string& name) {
            if (name != _docName) {
                return;
            }
            auto* current = application.getDocument(name.c_str());
            if (current && current->Uid.getValueStr() != originalUid) {
                ++finishedAfterReplacement;
            }
        });

    App::FeatureTestAsyncBlocker::resetBlocker();
    BOOST_SCOPE_EXIT_ALL(&) {
        App::FeatureTestAsyncBlocker::releaseBlocker();
    };

    object->touch();
    application.queueRecomputeRequest(App::RecomputeRequest::fromDocumentObject(*object));
    ASSERT_TRUE(App::FeatureTestAsyncBlocker::waitUntilStarted(2s));
    App::FeatureTestAsyncBlocker::releaseBlocker();

    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while ((application.hasPendingRecomputeRequest(_docName)
            || _doc->isCooperativeMutationActive()
            || _doc->isPresentationUpdateActive())
           && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(10ms);
    }
    ASSERT_FALSE(application.hasPendingRecomputeRequest(_docName));
    ASSERT_FALSE(_doc->isCooperativeMutationActive());
    ASSERT_TRUE(application.closeDocument(_docName.c_str()));
    _doc = nullptr;
    auto* replacement = application.newDocument(_docName.c_str(), "testUser");
    ASSERT_NE(replacement, nullptr);
    EXPECT_NE(replacement->Uid.getValueStr(), originalUid);

    for (auto& work : takeQueuedOwnerWork()) {
        work();
    }

    EXPECT_EQ(finishedAfterReplacement.load(), 0)
        << "A queued recompute-finished notification must not adopt a replacement "
           "document that reused the cancelled document's name";
}
