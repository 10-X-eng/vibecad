// SPDX-License-Identifier: LGPL-2.1-or-later

#include <Python.h>
#include <condition_variable>
#include <future>
#include <mutex>
#include <QApplication>
#include <QThread>
#include <QtTest/QtTest>
#include <App/HostRuntime.h>
#include <App/HostWorkflow.h>
#include <Gui/FrameBudget.h>

namespace
{
App::HostWorkflow<int> cancellableWorkflow(std::promise<void>& started,
                                         std::thread::id& destroyedOn)
{
    struct OwnerResource
    {
        std::thread::id& destroyedOn;
        ~OwnerResource() { destroyedOn = std::this_thread::get_id(); }
    } resource {destroyedOn};
    co_yield App::HostWorkflowStep {App::HostRuntime::Lane::Io, [&](std::stop_token stop) {
        std::mutex mutex;
        std::unique_lock lock(mutex);
        std::condition_variable_any wake;
        started.set_value();
        wake.wait(lock, stop, [] { return false; });
    }};
    co_return 1;
}
}

class FrameBudgetTest: public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void nestedOwnerHandoffRunsWhileAFrameTaskWaitsForItsWorker()
    {
        Gui::initializeGuiFrameDispatcher();
        App::HostRuntime runtime(1);
        std::future<bool> worker;
        std::atomic<bool> outerFinished {false};
        std::atomic<bool> ownerHandoffRan {false};
        bool workerFinishedBeforeOuterReturned = false;

        QVERIFY(Gui::dispatchToGuiFrame([&] {
            worker = runtime.submit([&](std::stop_token) {
                return Gui::dispatchToGuiFrameAndWait([&] {
                    ownerHandoffRan.store(true, std::memory_order_release);
                });
            });

            QElapsedTimer elapsed;
            elapsed.start();
            while (worker.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready
                   && elapsed.elapsed() < 1000) {
                QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
            }
            workerFinishedBeforeOuterReturned =
                worker.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready;
            outerFinished.store(true, std::memory_order_release);
        }));

        QTRY_VERIFY_WITH_TIMEOUT(outerFinished.load(std::memory_order_acquire), 2000);
        QTRY_VERIFY_WITH_TIMEOUT(
            worker.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready,
            2000
        );
        QVERIFY(worker.get());
        QVERIFY(ownerHandoffRan.load(std::memory_order_acquire));
        QVERIFY2(
            workerFinishedBeforeOuterReturned,
            "The active frame drain suppressed the wake needed by its own worker"
        );
    }

    void shutdownDrainsWorkerCleanupOnOwner()
    {
        Py_Initialize();
        QVERIFY(PyGILState_Check());
        bool joinedWithoutGil = false;
        bool cleanupHasGil = false;
        App::HostRuntime runtime(1);
        Gui::initializeGuiFrameDispatcher([&] {
            joinedWithoutGil = !PyGILState_Check();
            runtime.shutdown();
        });
        bool normalCleanup = false;
        QVERIFY(Gui::dispatchToGuiCleanup([&] { normalCleanup = true; }));
        QTRY_VERIFY(normalCleanup);

        std::promise<void> workflowStarted;
        auto workflowReady = workflowStarted.get_future();
        std::thread::id frameDestroyedOn, finishedOn;
        auto workflow = cancellableWorkflow(workflowStarted, frameDestroyedOn).runAsyncWithCleanup(
            runtime,
            [](std::function<void()> resume) {
                if (!Gui::dispatchToGuiFrame(std::move(resume))) {
                    throw std::runtime_error("normal queue stopped");
                }
            },
            [](std::function<void()> cleanup) {
                if (!Gui::dispatchToGuiCleanup(std::move(cleanup))) {
                    throw std::runtime_error("cleanup queue stopped too early");
                }
            },
            [&] { finishedOn = std::this_thread::get_id(); });
        QTRY_VERIFY(workflowReady.wait_for(std::chrono::milliseconds(0))
                    == std::future_status::ready);

        bool cleanedOnOwner = false;
        bool cleanupAccepted = false;
        bool ordinaryAccepted = true;
        std::promise<void> started;
        auto ready = started.get_future();
        auto worker = runtime.submit(App::HostRuntime::Lane::Compute,
            [&](std::stop_token stop) {
                std::mutex mutex;
                std::unique_lock lock(mutex);
                std::condition_variable_any wake;
                started.set_value();
                wake.wait(lock, stop, [] { return false; });
                ordinaryAccepted = Gui::dispatchToGuiFrame([] {});
                cleanupAccepted = Gui::dispatchToGuiCleanup([&] {
                    cleanedOnOwner = QThread::currentThread() == qApp->thread();
                    cleanupHasGil = PyGILState_Check();
                });
            });
        ready.get();
        QVERIFY(QMetaObject::invokeMethod(qApp, "aboutToQuit", Qt::DirectConnection));
        worker.get();
        QVERIFY(workflow.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready);
        QVERIFY_EXCEPTION_THROWN(workflow.get(), std::runtime_error);
        QCOMPARE(frameDestroyedOn, std::this_thread::get_id());
        QCOMPARE(finishedOn, std::this_thread::get_id());
        QVERIFY(!ordinaryAccepted);
        QVERIFY(cleanupAccepted);
        QVERIFY(cleanedOnOwner);
        QVERIFY(joinedWithoutGil);
        QVERIFY(cleanupHasGil);
        QVERIFY(PyGILState_Check());
        QVERIFY(!runtime.isAccepting());
        QVERIFY(!Gui::dispatchToGuiCleanup([] {}));
    }
};

QTEST_MAIN(FrameBudgetTest)
#include "FrameBudget.moc"
