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

        bool schedule = false;
        {
            std::lock_guard lock(mutex);
            if (stopped) {
                return false;
            }
            queue.push_back(std::move(task));
            if (!scheduled) {
                scheduled = true;
                schedule = true;
            }
        }
        if (!schedule) {
            return true;
        }
        scheduleDrain();
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
            stopped = true;
            scheduled = false;
            cancelled.swap(queue);
        }
    }

    static QEvent::Type drainEventType()
    {
        static const auto type = static_cast<QEvent::Type>(QEvent::registerEventType());
        return type;
    }

    void scheduleDrain()
    {
        std::lock_guard lock(mutex);
        if (stopped) {
            return;
        }
        // Adoption is important but never more important than input, paint,
        // timers, or status heartbeats already waiting in the Qt queue.
        QCoreApplication::postEvent(
            this,
            new QEvent(drainEventType()),
            Qt::LowEventPriority
        );
    }

    bool event(QEvent* event) override
    {
        if (event->type() == drainEventType()) {
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
                if (queue.empty()) {
                    scheduled = false;
                    return;
                }
                task = std::move(queue.front());
                queue.pop_front();
            }

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
        } while (!budget.exhausted());

        // Posting another event while Qt is draining posted events can let the
        // continuation run immediately in the same event-loop pass. A zero
        // timer is an event-loop yield, not a time delay: it gives input,
        // paint, status, and other due timers one dispatch opportunity before
        // the next bounded adoption frame is posted.
        QTimer::singleShot(0, this, [this] { scheduleDrain(); });
    }

    std::mutex mutex;
    std::deque<std::function<void()>> queue;
    bool scheduled {false};
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
