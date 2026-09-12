// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <functional>

#include <FCGlobal.h>
#include <QElapsedTimer>

class QObject;

namespace Gui
{

/// Attribute a native owner-thread phase to the existing opt-in GUI trace.
/// Disabled scopes do not start a timer; recording never performs file I/O.
class GuiExport PerformanceScope
{
public:
    explicit PerformanceScope(const char* name);
    ~PerformanceScope();
    PerformanceScope(const PerformanceScope&) = delete;
    PerformanceScope& operator=(const PerformanceScope&) = delete;

private:
    const char* name;
    QElapsedTimer timer;
};

/**
 * A single GUI-event work budget.
 *
 * Native document work may prepare arbitrarily large results, but Qt and Coin
 * state must still be adopted by their owning thread.  All cooperative GUI
 * consumers use this one budget so no subsystem quietly invents a larger
 * synchronous slice.
 */
class FrameBudget
{
public:
    static constexpr qint64 Milliseconds = 8;

    FrameBudget()
    {
        timer.start();
    }

    bool exhausted() const
    {
        return timer.elapsed() >= Milliseconds;
    }

private:
    QElapsedTimer timer;
};

/**
 * Queue one GUI-owned adoption step on the shared frame-budgeted dispatcher.
 *
 * Worker preparation may finish many results at once. Posting each result as
 * an independent Qt event lets those callbacks monopolize the GUI queue. This
 * dispatcher drains ready results for at most one shared frame budget and then
 * yields to input, paint, timers, and status updates before continuing.
 */
// Install the application-lifetime shutdown hook on the Qt owner before any
// document worker can submit GUI notifications.
GuiExport void initializeGuiFrameDispatcher();
// Register owner-side shutdown that joins every worker using the cleanup queue.
// Called once during owner initialization, before starting document workflows.
GuiExport void initializeGuiFrameDispatcher(std::function<void()> finishWorkers);
// Cleanup remains admitted while finishWorkers joins the runtime. It is drained
// on the owner before shutdown returns, then permanently closed.
GuiExport bool dispatchToGuiCleanup(std::function<void()> task);
// Returns false once Qt shutdown begins. Accepted, unexecuted callbacks are
// released on the owner at shutdown; a waiting promise then reports cancellation
// through std::future_error instead of retaining its worker indefinitely.
GuiExport bool dispatchToGuiFrame(std::function<void()> task);
GuiExport bool dispatchToGuiFrame(QObject* context, std::function<void()> task);

/**
 * Queue one GUI-owned step and block its worker caller until adoption finishes.
 *
 * This preserves synchronous document-notification semantics without posting an
 * unbounded burst of normal-priority blocking Qt calls. The shared dispatcher
 * admits the callback within the same elapsed-time frame budget used by every
 * other native adoption path. Exceptions are rethrown on the calling worker.
 */
GuiExport bool dispatchToGuiFrameAndWait(std::function<void()> task);

}  // namespace Gui
