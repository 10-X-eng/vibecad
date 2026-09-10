// SPDX-License-Identifier: LGPL-2.1-or-later

#include <condition_variable>
#include <future>
#include <mutex>
#include <QApplication>
#include <QThread>
#include <QtTest/QtTest>
#include <App/HostRuntime.h>
#include <Gui/FrameBudget.h>

class FrameBudgetTest: public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void shutdownDrainsWorkerCleanupOnOwner()
    {
        App::HostRuntime runtime(1);
        Gui::initializeGuiFrameDispatcher([&] { runtime.shutdown(); });
        bool normalCleanup = false;
        QVERIFY(Gui::dispatchToGuiCleanup([&] { normalCleanup = true; }));
        QTRY_VERIFY(normalCleanup);

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
                });
            });
        ready.get();
        QVERIFY(QMetaObject::invokeMethod(qApp, "aboutToQuit", Qt::DirectConnection));
        worker.get();
        QVERIFY(!ordinaryAccepted);
        QVERIFY(cleanupAccepted);
        QVERIFY(cleanedOnOwner);
        QVERIFY(!runtime.isAccepting());
        QVERIFY(!Gui::dispatchToGuiCleanup([] {}));
    }
};

QTEST_MAIN(FrameBudgetTest)
#include "FrameBudget.moc"
