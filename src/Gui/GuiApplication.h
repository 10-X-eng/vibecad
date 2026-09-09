/***************************************************************************
 *   Copyright (c) 2015 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#pragma once

#include "GuiApplicationNativeEventAware.h"
#include <Base/Interpreter.h>  // For Base::SystemExitException
#include <QList>
#include <QElapsedTimer>
#include <QVariantMap>
#include <memory>

class QSessionManager;

namespace Gui
{
/** Override QCoreApplication::notify() to fetch exceptions in Qt widgets
 * properly that are not handled in the event handler or slot.
 */
class GUIApplication: public GUIApplicationNativeEventAware
{
    Q_OBJECT

public:
    explicit GUIApplication(int& argc, char** argv);
    ~GUIApplication() override;

    /**
     * Make forwarding events exception-safe and get more detailed information
     * where an unhandled exception comes from.
     */
    bool notify(QObject* receiver, QEvent* event) override;

    /// Pointer to exceptions caught in Qt event handler
    std::shared_ptr<Base::SystemExitException> caughtException;

public Q_SLOTS:
    void commitData(QSessionManager& manager);
    /// Drain opt-in event timings on the GUI owner; performs no filesystem I/O.
    QVariantMap takePerformanceEvents();
    void recordPerformancePhase(const QString& name, qint64 elapsedNanoseconds);

protected:
    bool event(QEvent* event) override;

private:
    bool traceEvents {false};
    QElapsedTimer traceClock;
    QVariantList eventTimings;
    quint64 droppedEventTimings {0};
    int eventTraceDepth {0};
};

class GUISingleApplication: public GUIApplication
{
    Q_OBJECT

public:
    explicit GUISingleApplication(int& argc, char** argv);
    ~GUISingleApplication() override;

    bool isRunning() const;
    bool sendMessage(const QString& message, int timeout = 5000);

private Q_SLOTS:
    void receiveConnection();
    void processMessages();
    void readFromSocket();

Q_SIGNALS:
    void messageReceived(const QList<QString>&);

private:
    class Private;
    QScopedPointer<Private> d_ptr;
};

class WheelEventFilter: public QObject
{
    Q_OBJECT

public:
    explicit WheelEventFilter(QObject* parent);
    bool eventFilter(QObject* obj, QEvent* ev) override;
};

/** Works around a Qt layout-caching bug where QListView caches item row
 * positions before the application stylesheet is fully polished, causing
 * rows to overlap when the stylesheet enlarges items (min-height/padding).
 * On the first Show event of any QListView-derived widget a single
 * doItemsLayout() is queued so the row cache is rebuilt with the correct
 * stylesheet metrics. Other widget types are not affected.
 */
class ListViewStyleRelayoutFilter: public QObject
{
    Q_OBJECT

public:
    explicit ListViewStyleRelayoutFilter(QObject* parent);
    bool eventFilter(QObject* obj, QEvent* ev) override;
};

}  // namespace Gui
