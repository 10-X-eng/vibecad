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


#include <FCConfig.h>

#ifdef FC_OS_WIN32
# include <Windows.h>
#elif defined(Q_OS_UNIX)
# include <sys/types.h>
# include <ctime>
# include <unistd.h>
#endif

#include <sstream>
#include <stdexcept>
#include <QAbstractSpinBox>
#include <QByteArray>
#include <QComboBox>
#include <QListView>
#include <QTextStream>
#include <QFileInfo>
#include <QFileOpenEvent>
#include <QSessionManager>
#include <QTimer>
#include <QScopeGuard>
#include <QThread>


#include <QLocalServer>
#include <QLocalSocket>


#include <App/Application.h>
#include <Base/Console.h>
#include <Base/Exception.h>

#include "GuiApplication.h"
#include "Application.h"
#include "MainWindow.h"
#include "SpaceballEvent.h"
#include "FrameBudget.h"


using namespace Gui;

GUIApplication::GUIApplication(int& argc, char** argv)
    : GUIApplicationNativeEventAware(argc, argv)
{
    connect(
        this,
        &GUIApplication::commitDataRequest,
        this,
        &GUIApplication::commitData,
        Qt::DirectConnection
    );
#if QT_VERSION < QT_VERSION_CHECK(6, 0, 0)
    setFallbackSessionManagementEnabled(false);
#endif
    traceClock.start();
    traceEvents = qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE");
}

GUIApplication::~GUIApplication() = default;

QVariantMap GUIApplication::takePerformanceEvents()
{
    if (QThread::currentThread() != thread()) {
        throw std::logic_error("GUI event timings must be drained on the GUI owner");
    }
    QVariantMap result {{"enabled", traceEvents}, {"events", eventTimings},
                        {"clock_ms", traceClock.nsecsElapsed() / 1000000.0},
                        {"dropped", QVariant::fromValue(droppedEventTimings)}};
    eventTimings.clear();
    droppedEventTimings = 0;
    return result;
}

void GUIApplication::recordPerformancePhase(const QString& name, qint64 elapsedNanoseconds)
{
    if (!traceEvents || elapsedNanoseconds < FrameBudget::Milliseconds * 1000000) { return; }
    if (QThread::currentThread() != thread()) {
        throw std::logic_error("GUI phase timings must be recorded on the GUI owner");
    }
    if (eventTimings.size() >= 4096) { ++droppedEventTimings; return; }
    eventTimings.append(QVariantMap {
        {"start_ms", (traceClock.nsecsElapsed() - elapsedNanoseconds) / 1000000.0},
        {"elapsed_ms", elapsedNanoseconds / 1000000.0}, {"phase", name},
        {"event_type", -1}, {"receiver_class", "native_phase"},
        {"receiver_name", name}, {"depth", eventTraceDepth}});
}

bool GUIApplication::notify(QObject* receiver, QEvent* event)
{
    if (!receiver) {
        Base::Console().log(
            "GUIApplication::notify: Unexpected null receiver, event type: %d\n",
            (int)event->type()
        );
        return false;
    }

    const bool tracing = traceEvents && QThread::currentThread() == thread();
    const qint64 started = tracing ? traceClock.nsecsElapsed() : 0;
    // The receiver and event can be destroyed by notify. Capture identity first.
    const QByteArray receiverClass = tracing ? QByteArray(receiver->metaObject()->className()) : QByteArray();
    const QString receiverName = tracing ? receiver->objectName() : QString();
    const int eventType = tracing ? int(event->type()) : 0;
    const int depth = tracing ? ++eventTraceDepth : 0;
    const auto recordTiming = qScopeGuard([&] {
        if (!tracing) { return; }
        --eventTraceDepth;
        const qint64 elapsed = traceClock.nsecsElapsed() - started;
        if (elapsed < FrameBudget::Milliseconds * 1000000) { return; }
        // Bound diagnostic memory if no consumer is attached. This limits
        // retained trace records, never event execution; loss is reported.
        if (eventTimings.size() >= 4096) { ++droppedEventTimings; return; }
        eventTimings.append(QVariantMap {
            {"start_ms", started / 1000000.0}, {"elapsed_ms", elapsed / 1000000.0},
            {"event_type", eventType}, {"receiver_class", QString::fromLatin1(receiverClass)},
            {"receiver_name", receiverName}, {"depth", depth}});
    });

    // https://github.com/FreeCAD/FreeCAD/issues/16905
    std::string exceptionWarning =
#if FC_DEBUG
        "Exceptions must be caught before they go through Qt."
        " Ignoring this will cause crashes on some systems.\n";
#else
        "";
#endif

    try {
        if (event->type() == Spaceball::ButtonEvent::ButtonEventType
            || event->type() == Spaceball::MotionEvent::MotionEventType) {
            return processSpaceballEvent(receiver, event);
        }
        else {
            return QApplication::notify(receiver, event);
        }
    }
    catch (const Base::SystemExitException& e) {
        caughtException.reset(new Base::SystemExitException(e));
        qApp->exit(e.getExitCode());
        return true;
    }
    catch (const Base::Exception& e) {
        Base::Console().error(
            "Unhandled Base::Exception caught in GUIApplication::notify.\n"
            "The error message is: %s\n%s",
            e.what(),
            exceptionWarning
        );
    }
    catch (const std::exception& e) {
        Base::Console().error(
            "Unhandled std::exception caught in GUIApplication::notify.\n"
            "The error message is: %s\n%s",
            e.what(),
            exceptionWarning
        );
    }
    catch (...) {
        Base::Console().error(
            "Unhandled unknown exception caught in GUIApplication::notify.\n%s",
            exceptionWarning
        );
    }

    // Print some more information to the log file (if active) to ease bug fixing
    try {
        std::stringstream dump;
        dump << "The event type " << (int)event->type() << " was sent to "
             << receiver->metaObject()->className() << "\n";
        dump << "Object tree:\n";
        if (receiver->isWidgetType()) {
            QWidget* w = qobject_cast<QWidget*>(receiver);
            while (w) {
                dump << "\t";
                dump << w->metaObject()->className();
                QString name = w->objectName();
                if (!name.isEmpty()) {
                    dump << " (" << (const char*)name.toUtf8() << ")";
                }
                w = w->parentWidget();
                if (w) {
                    dump << " is child of\n";
                }
            }
            std::string str = dump.str();
            Base::Console().log("%s", str.c_str());
        }
    }
    catch (...) {
        Base::Console().log("Invalid recipient and/or event in GUIApplication::notify\n");
    }

    return true;
}

void GUIApplication::commitData(QSessionManager& manager)
{
    if (manager.allowsInteraction()) {
        if (!Gui::getMainWindow()->close()) {
            // cancel the shutdown
            manager.release();
            manager.cancel();
        }
    }
    else {
        // no user interaction allowed, thus close all documents and
        // the main window
        App::GetApplication().closeAllDocuments();
        Gui::getMainWindow()->close();
    }
}

bool GUIApplication::event(QEvent* ev)
{
    if (ev->type() == QEvent::FileOpen) {
        // (macOS workaround when opening FreeCAD by opening a .FCStd file in 1.0)
        // With the current implementation of the splash screen boot procedure, Qt will
        // start an event loop before FreeCAD is fully initialized. This event loop will
        // process the QFileOpenEvent that is sent by macOS before the main window is ready.
        if (!Gui::getMainWindow()->property("eventLoop").toBool()) {
            // If we never reach this point when opening FreeCAD by double clicking an
            // .FCStd file, then the workaround isn't needed anymore and can be removed
            QEvent* eventCopy = new QFileOpenEvent(static_cast<QFileOpenEvent*>(ev)->file());
            QTimer::singleShot(0, [eventCopy, this]() {
                QCoreApplication::postEvent(this, eventCopy);
            });
            return true;
        }

        QString file = static_cast<QFileOpenEvent*>(ev)->file();
        QFileInfo fi(file);
        if (fi.suffix().toLower() == QLatin1String("fcstd")) {
            QByteArray fn = file.toUtf8();
            Application::Instance->openFileFromGui(fn, "FreeCAD");
            return true;
        }
    }

    return GUIApplicationNativeEventAware::event(ev);
}

// ----------------------------------------------------------------------------

class GUISingleApplication::Private
{
public:
    explicit Private(GUISingleApplication* q_ptr)
        : q_ptr(q_ptr)
        , timer(new QTimer(q_ptr))
    {
        timer->setSingleShot(true);
        std::string exeName = App::Application::getExecutableName();
        serverName = QString::fromStdString(exeName);
    }

    ~Private()
    {
        if (server) {
            server->close();
        }
        delete server;
    }

    void setupConnection()
    {
        QLocalSocket socket;
        socket.connectToServer(serverName);
        if (socket.waitForConnected(1000)) {
            this->running = true;
        }
        else {
            startServer();
        }
    }

    void startServer()
    {
        // Start a QLocalServer to listen for connections
        server = new QLocalServer();
        QObject::connect(
            server,
            &QLocalServer::newConnection,
            q_ptr,
            &GUISingleApplication::receiveConnection
        );
        // first attempt
        if (!server->listen(serverName)) {
            if (server->serverError() == QAbstractSocket::AddressInUseError) {
                // second attempt
                server->removeServer(serverName);
                server->listen(serverName);
            }
        }
        if (server->isListening()) {
            Base::Console().log("Local server '%s' started\n", qPrintable(serverName));
        }
        else {
            Base::Console().log("Local server '%s' failed to start\n", qPrintable(serverName));
        }
    }

    GUISingleApplication* q_ptr;
    QTimer* timer;
    QLocalServer* server {nullptr};
    QString serverName;
    QList<QString> messages;
    bool running {false};
};

GUISingleApplication::GUISingleApplication(int& argc, char** argv)
    : GUIApplication(argc, argv)
    , d_ptr(new Private(this))
{
    d_ptr->setupConnection();
    connect(d_ptr->timer, &QTimer::timeout, this, &GUISingleApplication::processMessages);
}

GUISingleApplication::~GUISingleApplication() = default;

bool GUISingleApplication::isRunning() const
{
    return d_ptr->running;
}

bool GUISingleApplication::sendMessage(const QString& message, int timeout)
{
    QLocalSocket socket;
    bool connected = false;
    for (int i = 0; i < 2; i++) {
        socket.connectToServer(d_ptr->serverName);
        connected = socket.waitForConnected(timeout / 2);
        if (connected || i > 0) {
            break;
        }
        int ms = 250;
#if defined(Q_OS_WIN)
        Sleep(DWORD(ms));
#else
        usleep(ms * 1000);
#endif
    }
    if (!connected) {
        return false;
    }

    QTextStream ts(&socket);
#if QT_VERSION <= QT_VERSION_CHECK(6, 0, 0)
    ts.setCodec("UTF-8");
#else
    ts.setEncoding(QStringConverter::Utf8);
#endif
#if QT_VERSION <= QT_VERSION_CHECK(5, 15, 0)
    ts << message << endl;
#else
    ts << message << Qt::endl;
#endif

    return socket.waitForBytesWritten(timeout);
}

void GUISingleApplication::readFromSocket()
{
    auto socket = qobject_cast<QLocalSocket*>(sender());
    if (socket) {
        QTextStream in(socket);
#if QT_VERSION < QT_VERSION_CHECK(6, 0, 0)
        in.setCodec("UTF-8");
#else
        in.setEncoding(QStringConverter::Utf8);
#endif
        while (socket->canReadLine()) {
            d_ptr->timer->stop();
            QString message = in.readLine();
            Base::Console().log("Received message: %s\n", message.toStdString());
            d_ptr->messages.push_back(message);
            d_ptr->timer->start(1000);
        }
    }
}

void GUISingleApplication::receiveConnection()
{
    QLocalSocket* socket = d_ptr->server->nextPendingConnection();
    if (!socket) {
        return;
    }

    connect(socket, &QLocalSocket::disconnected, socket, &QLocalSocket::deleteLater);
    connect(socket, &QLocalSocket::readyRead, this, &GUISingleApplication::readFromSocket);
}

void GUISingleApplication::processMessages()
{
    QList<QString> msg = d_ptr->messages;
    d_ptr->messages.clear();
    Q_EMIT messageReceived(msg);
}

// ----------------------------------------------------------------------------

WheelEventFilter::WheelEventFilter(QObject* parent)
    : QObject(parent)
{}

bool WheelEventFilter::eventFilter(QObject* obj, QEvent* ev)
{
    if (qobject_cast<QComboBox*>(obj) && ev->type() == QEvent::Wheel) {
        return true;
    }
    auto sb = qobject_cast<QAbstractSpinBox*>(obj);
    if (sb) {
        if (ev->type() == QEvent::Show) {
            sb->setFocusPolicy(Qt::StrongFocus);
        }
        else if (ev->type() == QEvent::Wheel) {
            return !sb->hasFocus();
        }
    }
    return false;
}

// ----------------------------------------------------------------------------

ListViewStyleRelayoutFilter::ListViewStyleRelayoutFilter(QObject* parent)
    : QObject(parent)
{}

bool ListViewStyleRelayoutFilter::eventFilter(QObject* obj, QEvent* ev)
{
    // Qt caches QListView row positions before the application stylesheet is
    // fully polished, so rows overlap when the stylesheet enlarges items via
    // min-height/padding. Queue a one-shot relayout on first show to rebuild
    // the row cache with correct stylesheet metrics.
    if (ev->type() == QEvent::Show) {
        auto view = qobject_cast<QListView*>(obj);
        if (view && !view->property("fc_styleRelayoutDone").toBool()) {
            view->setProperty("fc_styleRelayoutDone", true);
            QTimer::singleShot(0, view, [view]() {
                view->doItemsLayout();
            });
        }
    }
    return false;
}

#include "moc_GuiApplication.cpp"
