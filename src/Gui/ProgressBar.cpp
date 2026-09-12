/***************************************************************************
 *   Copyright (c) 2004 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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


#include <algorithm>
#include <atomic>
#include <mutex>

#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QKeyEvent>
#include <QMessageBox>
#include <QMetaObject>
#include <QThread>
#include <QTime>
#include <QTimer>
#include <QWindow>


#include "ProgressBar.h"
#include "FrameBudget.h"
#include "MainWindow.h"
#include "ProgressDialog.h"
#include "WaitCursor.h"



using namespace Gui;


namespace Gui
{
struct SequencerBarPrivate
{
    ProgressBar* bar;
    WaitCursor* waitCursor;
    QElapsedTimer measureTime;
    QElapsedTimer progressTime;
    QElapsedTimer eventServiceTime;
    QString text;
    std::mutex presentationMutex;
    std::atomic_bool guiThread {true};
};

struct ProgressBarPrivate
{
    QTimer* delayShowTimer;
    QTimer* statusTimer;
    int minimumDuration;
    int observeEventFilter;
    bool userEnabled {true};

    bool isModalDialog(QObject* o) const
    {
        QWidget* parent = qobject_cast<QWidget*>(o);
        if (!parent) {
            QWindow* window = qobject_cast<QWindow*>(o);
            if (window) {
                parent = QWidget::find(window->winId());
            }
        }
        while (parent) {
            auto* dlg = qobject_cast<QMessageBox*>(parent);
            if (dlg && dlg->isModal()) {
                return true;
            }
            auto* pd = qobject_cast<QProgressDialog*>(parent);
            if (pd) {
                return true;
            }
            parent = parent->parentWidget();
        }

        return false;
    }
};
}  // namespace Gui

SequencerBar* SequencerBar::_pclSingleton = nullptr;

SequencerBar* SequencerBar::instance()
{
    // not initialized?
    if (!_pclSingleton) {
        _pclSingleton = new SequencerBar();
    }

    return _pclSingleton;
}

SequencerBar::SequencerBar()
{
    d = new SequencerBarPrivate;
    d->bar = nullptr;
    d->waitCursor = nullptr;
    setProgressPulseHandler(&SequencerBar::progressPulse, this);
}

SequencerBar::~SequencerBar()
{
    setProgressPulseHandler(nullptr, nullptr);
    delete d;
}

void SequencerBar::pause()
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    const bool guiThread = d->guiThread.load(std::memory_order_acquire);
    if (thr != currentThread) {
        QMetaObject::invokeMethod(
            d->bar,
            [bar = d->bar, guiThread] { bar->leaveControlEvents(guiThread); },
            Qt::QueuedConnection
        );
        return;
    }
    d->bar->leaveControlEvents(guiThread);
    if (!guiThread) {
        return;
    }

    // allow key handling of dialog and restore cursor
    d->waitCursor->restoreCursor();
    QApplication::setOverrideCursor(Qt::ArrowCursor);
}

void SequencerBar::resume()
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    const bool guiThread = d->guiThread.load(std::memory_order_acquire);
    if (thr != currentThread) {
        QMetaObject::invokeMethod(
            d->bar,
            [bar = d->bar, guiThread] { bar->enterControlEvents(guiThread); },
            Qt::QueuedConnection
        );
        return;
    }
    if (!guiThread) {
        d->bar->enterControlEvents(false);
        return;
    }

    QApplication::restoreOverrideCursor();
    d->waitCursor->setWaitCursor();

    // must be called as last to get control before WaitCursor
    d->bar->enterControlEvents(guiThread);  // grab again
}

void SequencerBar::startStep()
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    d->progressTime.start();
    d->eventServiceTime.start();
    {
        std::lock_guard lock(d->presentationMutex);
        d->measureTime.start();
    }
    if (thr != currentThread) {
        d->guiThread.store(false, std::memory_order_release);
        QMetaObject::invokeMethod(
            d->bar,
            [bar = d->bar, totalSteps = nTotalSteps] {
                bar->setRangeEx(0, static_cast<int>(totalSteps));
                bar->aboutToShow();
                bar->enterControlEvents(false);
            },
            Qt::QueuedConnection
        );
    }
    else {
        d->guiThread.store(true, std::memory_order_release);
        d->bar->setRangeEx(0, (int)nTotalSteps);
        d->waitCursor = new Gui::WaitCursor;
        d->bar->enterControlEvents(true);
        d->bar->aboutToShow();
    }
}

void SequencerBar::stopStep()
{
    QMetaObject::invokeMethod(d->bar, "aboutToHide", Qt::QueuedConnection);
}

void SequencerBar::checkAbort()
{
    if (d->bar->thread() != QThread::currentThread()) {
        return;
    }
    serviceGuiEvents();
    if (!wasCanceled()) {
        return;
    }
    // restore cursor
    pause();
    bool ok = d->bar->canAbort();
    // continue and show up wait cursor if needed
    resume();

    // force to abort the operation
    if (ok) {
        abort();
    }
    else {
        rejectCancel();
    }
}

void SequencerBar::serviceGuiEvents()
{
    if (!d->bar || d->bar->thread() != QThread::currentThread()) {
        return;
    }
    if (!d->eventServiceTime.isValid()) {
        d->eventServiceTime.start();
        return;
    }
    if (d->eventServiceTime.elapsed() < FrameBudget::Milliseconds) {
        return;
    }

    d->eventServiceTime.restart();
    // The owning operation is still exposing a partial document state. Paint,
    // timers, and progress may advance, but no user command may re-enter the
    // document until the operation reaches its stable boundary.
    qApp->processEvents(
        QEventLoop::ExcludeUserInputEvents | QEventLoop::ExcludeSocketNotifiers,
        1
    );
}

void SequencerBar::progressPulse(void* context)
{
    auto* sequencer = static_cast<SequencerBar*>(context);
    sequencer->serviceGuiEvents();
}

void SequencerBar::nextStep(bool canAbort)
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    if (thr != currentThread) {
        if (wasCanceled() && canAbort) {
            abort();
        }
        else {
            setValue((int)nProgress + 1);
        }
    }
    else {
        if (wasCanceled() && canAbort) {
            // restore cursor
            pause();
            bool ok = d->bar->canAbort();
            // continue and show up wait cursor if needed
            resume();

            // force to abort the operation
            if (ok) {
                abort();
            }
            else {
                rejectCancel();
                setValue((int)nProgress + 1);
            }
        }
        else {
            setValue((int)nProgress + 1);
        }
    }
}

void SequencerBar::setProgress(size_t step)
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    if (thr != currentThread) {
        QMetaObject::invokeMethod(d->bar, "show", Qt::QueuedConnection);
    }
    else {
        d->bar->show();
    }

    setValue((int)step);
}

void SequencerBar::setValue(int step)
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    // if number of total steps is unknown then increment only by one
    if (nTotalSteps == 0) {
        int elapsed = d->progressTime.elapsed();
        // allow an update every 100 milliseconds only
        if (elapsed > 100) {
            d->progressTime.restart();
            if (thr != currentThread) {
                QMetaObject::invokeMethod(
                    d->bar,
                    [bar = d->bar] { bar->setValueEx(bar->value() + 1); },
                    Qt::QueuedConnection
                );
            }
            else {
                d->bar->setValueEx(d->bar->value() + 1);
            }
        }
    }
    else {
        int elapsed = d->progressTime.elapsed();
        // allow an update every 100 milliseconds only
        if (elapsed > 100) {
            d->progressTime.restart();
            if (thr != currentThread) {
                QMetaObject::invokeMethod(
                    d->bar,
                    [this, bar = d->bar, step] {
                        bar->setValueEx(step);
                        if (bar->isVisible()) {
                            showRemainingTime();
                        }
                    },
                    Qt::QueuedConnection
                );
            }
            else {
                d->bar->setValueEx(step);
                if (d->bar->isVisible()) {
                    showRemainingTime();
                }
                d->bar->resetObserveEventFilter();
            }
        }
    }
}

void SequencerBar::showRemainingTime()
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread

    qint64 elapsed;
    QString txt;
    {
        std::lock_guard lock(d->presentationMutex);
        elapsed = d->measureTime.elapsed();
        txt = d->text;
    }
    int progress = std::max(0, d->bar->value());
    int totalSteps = d->bar->maximum() - d->bar->minimum();
    const auto duration = [](qint64 seconds) {
        return QStringLiteral("%1:%2:%3")
            .arg(seconds / 3600, 2, 10, QLatin1Char('0'))
            .arg((seconds / 60) % 60, 2, 10, QLatin1Char('0'))
            .arg(seconds % 60, 2, 10, QLatin1Char('0'));
    };
    QString status = txt;
    if (totalSteps > 0) {
        status += QStringLiteral(" — %1 / %2").arg(progress).arg(totalSteps);
    }
    status += QStringLiteral(" — ")
        + Gui::ProgressBar::tr("Elapsed: %1").arg(duration(elapsed / 1000));

    // More than 5 percent complete or more than 5 secs have elapsed.
    if (progress > 0 && totalSteps > progress
        && (progress * 20LL > totalSteps || elapsed > 5000)) {
        qint64 rest = static_cast<qint64>((double)totalSteps / progress * elapsed) - elapsed;

        // more than 1 secs have elapsed and at least 100 ms are remaining
        if (elapsed > 1000 && rest > 100) {
            status += QStringLiteral(" — ")
                + Gui::ProgressBar::tr("Estimated remaining: %1").arg(duration(rest / 1000));
        }
    }
    if (thr != currentThread) {
        QMetaObject::invokeMethod(getMainWindow(), "showMessage", Qt::QueuedConnection,
                                  Q_ARG(QString, status));
    }
    else {
        getMainWindow()->showMessage(status);
    }
}

void SequencerBar::resetData()
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread
    if (thr != currentThread) {
        auto* mainWindow = getMainWindow();
        QMetaObject::invokeMethod(
            d->bar,
            [bar = d->bar, mainWindow] {
                bar->resetEx();
                bar->aboutToHide();
                mainWindow->showMessage(QString());
                mainWindow->setPaneText(1, QString());
                bar->leaveControlEvents(false);
            },
            Qt::QueuedConnection
        );
    }
    else {
        d->bar->resetEx();
        // Note: Under Qt 4.1.4 this forces to run QWindowsStyle::eventFilter() twice
        // handling the same event thus a warning is printed. Possibly, this is a bug
        // in Qt. The message is QEventDispatcherUNIX::unregisterTimer: invalid argument.
        d->bar->aboutToHide();
        delete d->waitCursor;
        d->waitCursor = nullptr;
        d->bar->leaveControlEvents(d->guiThread.load(std::memory_order_acquire));
        getMainWindow()->setPaneText(1, QString());
        getMainWindow()->showMessage(QString());
    }

    SequencerBase::resetData();
}

void SequencerBar::abort()
{
    // resets
    resetData();
    Base::AbortException exc("User aborted");
    throw exc;
}

void SequencerBar::setText(const char* pszTxt)
{
    QThread* currentThread = QThread::currentThread();
    QThread* thr = d->bar->thread();  // this is the main thread

    // print message to the statusbar
    const QString text = pszTxt ? QString::fromUtf8(pszTxt) : QLatin1String("");
    {
        std::lock_guard lock(d->presentationMutex);
        d->text = text;
    }
    if (thr != currentThread) {
        QMetaObject::invokeMethod(
            getMainWindow(),
            "showMessage",
            Qt::/*Blocking*/ QueuedConnection,
            Q_ARG(QString, text)
        );
    }
    else {
        getMainWindow()->showMessage(text);
    }
}

bool SequencerBar::isBlocking() const
{
    return d->guiThread.load(std::memory_order_acquire);
}

QProgressBar* SequencerBar::getProgressBar(QWidget* parent)
{
    if (!d->bar) {
        d->bar = new ProgressBar(this, parent);
    }
    return d->bar;
}

// -------------------------------------------------------

/* TRANSLATOR Gui::ProgressBar */

ProgressBar::ProgressBar(SequencerBar* s, QWidget* parent)
    : QProgressBar(parent)
    , sequencer(s)
{
#ifdef QT_WINEXTRAS_LIB
    m_taskbarButton = nullptr;
    m_taskbarButton = nullptr;
#endif
    d = new Gui::ProgressBarPrivate;
    d->minimumDuration = 2000;  // 2 seconds
    d->delayShowTimer = new QTimer(this);
    d->delayShowTimer->setSingleShot(true);
    connect(d->delayShowTimer, &QTimer::timeout, this, &ProgressBar::delayedShow);
    d->statusTimer = new QTimer(this);
    d->statusTimer->setInterval(1000);
    connect(d->statusTimer, &QTimer::timeout, this, [this] {
        if (sequencer->isRunning()) {
            sequencer->showRemainingTime();
        }
    });
    d->observeEventFilter = 0;
    // Visibility is owned by MainWindow's status-bar registry, which sets
    // userEnabled from the persisted value after registration.

    setFixedWidth(120);

    // write percentage to the center
    setAlignment(Qt::AlignHCenter);
    //: A context menu action used to show or hide the progress indicator in the status bar
    setWindowTitle(tr("Progress Indicator"));
    hide();
}

bool ProgressBar::isUserEnabled() const
{
    return d->userEnabled;
}

void ProgressBar::setUserEnabled(bool enabled)
{
    if (d->userEnabled == enabled) {
        return;
    }
    d->userEnabled = enabled;
    if (!enabled) {
        QProgressBar::setVisible(false);
    }
    else if (sequencer->isRunning() && !sequencer->wasCanceled()) {
        QProgressBar::setVisible(true);
    }
}

void ProgressBar::setVisible(bool visible)
{
    // Block every show() the sequencer fires when the user has disabled the bar.
    if (visible && !d->userEnabled) {
        return;
    }
    QProgressBar::setVisible(visible);
}

ProgressBar::~ProgressBar()
{
    disconnect(d->delayShowTimer, &QTimer::timeout, this, &ProgressBar::delayedShow);
    delete d->delayShowTimer;
    delete d;
}

int ProgressBar::minimumDuration() const
{
    return d->minimumDuration;
}

void ProgressBar::resetEx()
{
    QProgressBar::reset();
#ifdef QT_WINEXTRAS_LIB
    setupTaskBarProgress();
    m_taskbarProgress->reset();
#endif
}

void ProgressBar::setRangeEx(int minimum, int maximum)
{
    QProgressBar::setRange(minimum, maximum);
#ifdef QT_WINEXTRAS_LIB
    setupTaskBarProgress();
    m_taskbarProgress->setRange(minimum, maximum);
#endif
}

void ProgressBar::setValueEx(int value)
{
    QProgressBar::setValue(value);
#ifdef QT_WINEXTRAS_LIB
    setupTaskBarProgress();
    m_taskbarProgress->setValue(value);
#endif
}

void ProgressBar::setMinimumDuration(int ms)
{
    if (value() == 0) {
        d->delayShowTimer->stop();
        d->delayShowTimer->start(ms);
    }

    d->minimumDuration = ms;
}

void ProgressBar::aboutToShow()
{
    d->statusTimer->start();
    // delay showing the bar
    d->delayShowTimer->start(d->minimumDuration);
#ifdef QT_WINEXTRAS_LIB
    setupTaskBarProgress();
    m_taskbarProgress->show();
#endif
}

void ProgressBar::delayedShow()
{
    if (!isVisible() && !sequencer->wasCanceled() && sequencer->isRunning()) {
        show();
    }
}

void ProgressBar::aboutToHide()
{
    d->statusTimer->stop();
    hide();
#ifdef QT_WINEXTRAS_LIB
    setupTaskBarProgress();
    m_taskbarProgress->hide();
#endif
}

bool ProgressBar::canAbort() const
{
    auto ret = QMessageBox::question(
        getMainWindow(),
        tr("Aborting"),
        tr("Abort the operation?"),
        QMessageBox::Yes | QMessageBox::No,
        QMessageBox::No
    );

    return (ret == QMessageBox::Yes) ? true : false;
}

void ProgressBar::showEvent(QShowEvent* e)
{
    QProgressBar::showEvent(e);
    d->delayShowTimer->stop();
}

void ProgressBar::hideEvent(QHideEvent* e)
{
    QProgressBar::hideEvent(e);
    d->delayShowTimer->stop();
}

void ProgressBar::resetObserveEventFilter()
{
    d->observeEventFilter = 0;
}

void ProgressBar::enterControlEvents(bool grab)
{
    qApp->installEventFilter(this);

    // Make sure that we get the key events, otherwise the Inventor viewer usurps the key events
    // This also disables accelerators.
#if defined(Q_OS_LINUX)
    Q_UNUSED(grab)
#else
    if (grab) {
        grabKeyboard();
    }
#endif
}

void ProgressBar::leaveControlEvents(bool release)
{
    qApp->removeEventFilter(this);

#if defined(Q_OS_LINUX)
    Q_UNUSED(release)
#else
    // release the keyboard again
    if (release) {
        releaseKeyboard();
    }
#endif
}

#ifdef QT_WINEXTRAS_LIB
void ProgressBar::setupTaskBarProgress()
{
    if (!m_taskbarButton || !m_taskbarProgress) {
        m_taskbarButton = new QWinTaskbarButton(this);
        m_taskbarButton->setWindow(MainWindow::getInstance()->windowHandle());
        // m_myButton->setOverlayIcon(QIcon(""));

        m_taskbarProgress = m_taskbarButton->progress();
    }
}
#endif

bool ProgressBar::eventFilter(QObject* o, QEvent* e)
{
    if (sequencer->isRunning() && e) {
        if (!sequencer->isBlocking()) {
            if (e->type() == QEvent::KeyPress) {
                auto* keyEvent = static_cast<QKeyEvent*>(e);
                if (keyEvent->key() == Qt::Key_Escape) {
                    sequencer->tryToCancel();
                    return true;
                }
            }
            return QProgressBar::eventFilter(o, e);
        }

        QThread* currentThread = QThread::currentThread();
        QThread* thr = this->thread();  // this is the main thread
        if (thr != currentThread) {
            if (e->type() == QEvent::KeyPress) {
                auto ke = static_cast<QKeyEvent*>(e);
                if (ke->key() == Qt::Key_Escape) {
                    // cancel the operation
                    sequencer->tryToCancel();
                    return true;
                }
            }
            return QProgressBar::eventFilter(o, e);
        }

        // main thread
        switch (e->type()) {
            // check for ESC
            case QEvent::KeyPress: {
                auto ke = static_cast<QKeyEvent*>(e);
                if (ke->key() == Qt::Key_Escape) {
                    // eventFilter() was called from the application 50 times without performing a
                    // new step (app could hang)
                    if (d->observeEventFilter > 50) {
                        // tries to unlock the application if it hangs (probably due to incorrect
                        // usage of Base::Sequencer)
                        if (ke->modifiers() & (Qt::ControlModifier | Qt::AltModifier)) {
                            sequencer->resetData();
                            return true;
                        }
                    }

                    // cancel the operation
                    sequencer->tryToCancel();
                }

                return true;
            } break;

            // ignore all these events
            case QEvent::KeyRelease:
            case QEvent::Enter:
            case QEvent::Leave:
            case QEvent::MouseButtonDblClick:
            case QEvent::MouseButtonRelease:
            case QEvent::MouseMove:
            case QEvent::NativeGesture:
            case QEvent::ContextMenu: {
                if (!d->isModalDialog(o)) {
                    return true;
                }
            } break;

            // special case if the main window's close button was pressed
            case QEvent::Close: {
                // avoid to exit while app is working
                // note: all other widget types are allowed to be closed anyway
                if (o == getMainWindow()) {
                    e->ignore();
                    return true;
                }
            } break;

            // do a system beep and ignore the event
            case QEvent::MouseButtonPress: {
                if (!d->isModalDialog(o)) {
                    QApplication::beep();
                    return true;
                }
            } break;

            default: {
            } break;
        }

        d->observeEventFilter++;
    }

    return QProgressBar::eventFilter(o, e);
}


#include "moc_ProgressBar.cpp"
