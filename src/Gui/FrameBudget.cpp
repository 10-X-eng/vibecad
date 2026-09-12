// SPDX-License-Identifier: LGPL-2.1-or-later

#include "FrameBudget.h"
#include "GuiApplication.h"

#include <deque>
#include <future>
#include <mutex>

#include <QApplication>
#include <QEvent>
#include <QPointer>
#include <QThread>
#include <QTimer>

#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>

namespace Gui
{
namespace
{

class FrameDispatcher final: public QObject
{
public:
    static FrameDispatcher* instance()
    {
        static FrameDispatcher* inst = [] {
            auto* dispatcher = new FrameDispatcher();
            if (qApp && qApp->thread() && QThread::currentThread() != qApp->thread()) {
                dispatcher->moveToThread(qApp->thread());
            }
            QObject::connect(qApp, &QCoreApplication::aboutToQuit, dispatcher,
                             [dispatcher] { dispatcher->stop(); }, Qt::DirectConnection);
            QObject::connect(qApp, &QObject::destroyed, dispatcher,
                             [dispatcher] { dispatcher->stop(); }, Qt::DirectConnection);
            return dispatcher;
        }();
        return inst;
    }

    bool enqueue(std::function<void()> task)
    {
        if (!task || !qApp) {
            return false;
        }

        bool postWake = false;
        {
            std::lock_guard lock(mutex);
            if (stopped) {
                return false;
            }
            queue.push_back(std::move(task));
            if (!wakePending) {
                wakePending = true;
                postWake = true;
            }
        }
        if (postWake) {
            postDrainEvent();
        }
        return true;
    }

    void setWorkerShutdown(std::function<void()> finish)
    {
        if (stopped || finishWorkers || !finish) {
            throw std::logic_error("Invalid GUI worker shutdown registration");
        }
        finishWorkers = std::move(finish);
    }

    bool enqueueCleanup(std::function<void()> task)
    {
        if (!task || !qApp) {
            return false;
        }
        bool postWake = false;
        {
            std::lock_guard lock(mutex);
            if (cleanupStopped) {
                return false;
            }
            cleanupQueue.push_back(std::move(task));
            if (!stopped && !wakePending) {
                wakePending = true;
                postWake = true;
            }
        }
        if (postWake) {
            postDrainEvent();
        }
        return true;
    }

private:
    FrameDispatcher() = default;
    ~FrameDispatcher() override = default;

    void stop()
    {
        // Qt will no longer dispatch ordinary work after aboutToQuit. Release
        // accepted captures on their owner now, including promises whose
        // workers would otherwise wait forever during runtime shutdown.
        // Destructors may submit again: reject before releasing, outside the
        // queue lock, so cancellation cannot deadlock or revive the queue.
        std::deque<std::function<void()>> cancelled;
        {
            std::lock_guard lock(mutex);
            if (stopped) {
                return;
            }
            stopped = true;
            wakePending = false;
            cancelled.swap(queue);
        }
        // Release queued synchronous handoffs before joining their workers.
        cancelled.clear();
        if (auto finish = std::move(finishWorkers)) {
            // A Python-triggered close can hold the GIL while a worker needs
            // it to finish. Restore it before draining owner cleanup below.
            std::unique_ptr<Base::PyGILStateRelease> release;
            if (Py_IsInitialized() && PyGILState_Check()) {
                release = std::make_unique<Base::PyGILStateRelease>();
            }
            finish();
        }
        // Workers are now joined, so no workflow can arrive after this seal.
        // Cleanup may enqueue more cleanup; run it outside the queue mutex.
        for (;;) {
            std::function<void()> task;
            {
                std::lock_guard lock(mutex);
                if (cleanupQueue.empty()) {
                    cleanupStopped = true;
                    break;
                }
                task = std::move(cleanupQueue.front());
                cleanupQueue.pop_front();
            }
            execute(task);
        }
    }

    static QEvent::Type drainEventType()
    {
        static const auto type = static_cast<QEvent::Type>(QEvent::registerEventType());
        return type;
    }

    void postDrainEvent()
    {
        // Adoption is important but never more important than input, paint,
        // timers, or status heartbeats already waiting in the Qt queue.
        QCoreApplication::postEvent(
            this,
            new QEvent(drainEventType()),
            Qt::LowEventPriority
        );
    }

    void requestDrain()
    {
        bool postWake = false;
        {
            std::lock_guard lock(mutex);
            if (!stopped && !wakePending && (!queue.empty() || !cleanupQueue.empty())) {
                wakePending = true;
                postWake = true;
            }
        }
        if (postWake) {
            postDrainEvent();
        }
    }

    bool event(QEvent* event) override
    {
        if (event->type() == drainEventType()) {
            {
                std::lock_guard lock(mutex);
                wakePending = false;
                if (stopped) {
                    return true;
                }
            }
            drain();
            return true;
        }
        return QObject::event(event);
    }

    void drain()
    {
        FrameBudget budget;
        do {
            std::function<void()> task;
            {
                std::lock_guard lock(mutex);
                if (queue.empty() && cleanupQueue.empty()) {
                    return;
                }
                auto& ready = cleanupQueue.empty() ? queue : cleanupQueue;
                task = std::move(ready.front());
                ready.pop_front();
            }

            execute(task);
        } while (!budget.exhausted());

        // Posting another event while Qt is draining posted events can let the
        // continuation run immediately in the same event-loop pass. A zero
        // timer is an event-loop yield, not a time delay: it gives input,
        // paint, status, and other due timers one dispatch opportunity before
        // the next bounded adoption frame is posted.
        QTimer::singleShot(0, this, [this] { requestDrain(); });
    }

    static void execute(const std::function<void()>& task)
    {
        try {
            task();
        }
        catch (const Base::Exception& exception) {
            exception.reportException();
        }
        catch (const std::exception& exception) {
            Base::Console().error(
                "GUI frame adoption failed: %s\n",
                exception.what()
            );
        }
        catch (...) {
            Base::Console().error("GUI frame adoption failed with an unknown exception\n");
        }
    }

    std::mutex mutex;
    std::deque<std::function<void()>> queue;
    std::deque<std::function<void()>> cleanupQueue;
    std::function<void()> finishWorkers;
    bool cleanupStopped {false};
    // A posted wake event, not an active drain. Clearing this before executing
    // a task lets a worker post a nested owner handoff when that task runs an
    // event-driven wait for the worker completion.
    bool wakePending {false};
    bool stopped {false};
};

}  // namespace

PerformanceScope::PerformanceScope(const char* name) : name(name)
{
    static const bool enabled = qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE");
    if (enabled && qApp && QThread::currentThread() == qApp->thread()) {
        timer.start();
    }
}

PerformanceScope::~PerformanceScope()
{
    if (!timer.isValid() || timer.elapsed() < FrameBudget::Milliseconds || !qApp) {
        return;
    }
    // Native Qt fixtures may use plain QApplication rather than GUIApplication.
    if (auto* application = qobject_cast<GUIApplication*>(qApp)) {
        application->recordPerformancePhase(QString::fromLatin1(name), timer.nsecsElapsed());
    }
}

void initializeGuiFrameDispatcher()
{
    if (!qApp || QThread::currentThread() != qApp->thread()) {
        throw std::logic_error("GUI frame dispatcher initialization requires the Qt owner");
    }
    FrameDispatcher::instance();
}

void initializeGuiFrameDispatcher(std::function<void()> finishWorkers)
{
    initializeGuiFrameDispatcher();
    FrameDispatcher::instance()->setWorkerShutdown(std::move(finishWorkers));
}

bool dispatchToGuiCleanup(std::function<void()> task)
{
    if (!task || !qApp) {
        return false;
    }
    return FrameDispatcher::instance()->enqueueCleanup(std::move(task));
}

bool dispatchToGuiFrame(std::function<void()> task)
{
    // Do not construct a QObject after QApplication has already disappeared.
    if (!task || !qApp || QCoreApplication::closingDown()) {
        return false;
    }
    return FrameDispatcher::instance()->enqueue(std::move(task));
}

bool dispatchToGuiFrame(QObject* context, std::function<void()> task)
{
    if (!context || !task) {
        return false;
    }
    QPointer<QObject> lifetime(context);
    return dispatchToGuiFrame(
        [lifetime, task = std::move(task)]() mutable {
            if (lifetime) {
                task();
            }
        }
    );
}

bool dispatchToGuiFrameAndWait(std::function<void()> task)
{
    if (!task || !qApp) {
        return false;
    }
    if (QThread::currentThread() == qApp->thread()) {
        task();
        return true;
    }

    auto completion = std::make_shared<std::promise<void>>();
    auto finished = completion->get_future();
    const bool accepted = dispatchToGuiFrame(
        [task = std::move(task), completion = std::move(completion)]() mutable {
            try {
                task();
                completion->set_value();
            }
            catch (...) {
                completion->set_exception(std::current_exception());
            }
        }
    );
    if (!accepted) {
        return false;
    }

    // Python-backed document callers can own the GIL. Their GUI adoption may
    // itself call Python, so release it for the wait, never on GUI execution.
    std::unique_ptr<Base::PyGILStateRelease> release;
    if (Py_IsInitialized() && PyGILState_Check()) {
        release = std::make_unique<Base::PyGILStateRelease>();
    }
    finished.get();
    return true;
}

}  // namespace Gui
