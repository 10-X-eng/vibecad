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

#include <chrono>
#include <future>
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
