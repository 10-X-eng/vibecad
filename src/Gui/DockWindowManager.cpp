/***************************************************************************
 *   Copyright (c) 2007 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <array>
#include <QAction>
#include <QApplication>
#include <QDataStream>
#include <QDockWidget>
#include <QMap>
#include <QMouseEvent>
#include <QPointer>
#include <QSet>
#include <QTimer>

#include <boost/algorithm/string/predicate.hpp>

#include <App/Application.h>
#include <Base/Console.h>
#include <Base/Tools.h>

#include "DockWindowManager.h"
#include "MainWindow.h"
#include "OverlayManager.h"


using namespace Gui;

namespace
{

constexpr int maxMaterializedDockProxies = 64;

struct DuplicateDockStateChoice
{
    QDockWidget* dock {nullptr};
    Qt::DockWidgetArea area {Qt::NoDockWidgetArea};
    QRect geometry;
    QStringList groupNames;
    bool visible {false};
    bool floating {false};
    int score {-1};
    int occurrence {-1};
};

int serializedStringOccurrences(const QByteArray& state, const QString& value)
{
    QByteArray token;
    QDataStream stream(&token, QIODevice::WriteOnly);
    stream.setVersion(QDataStream::Qt_5_0);
    stream << value;
    return static_cast<int>(state.count(token));
}

bool isDockingArea(Qt::DockWidgetArea area)
{
    switch (area) {
        case Qt::LeftDockWidgetArea:
        case Qt::RightDockWidgetArea:
        case Qt::TopDockWidgetArea:
        case Qt::BottomDockWidgetArea:
            return true;
        default:
            return false;
    }
}

}  // namespace

DockWindowItems::DockWindowItems() = default;

DockWindowItems::~DockWindowItems() = default;

void DockWindowItems::addDockWidget(const char* name, Qt::DockWidgetArea pos, DockWindowOptions option)
{
    DockWindowItem item;
    item.name = QString::fromUtf8(name);
    item.pos = pos;
    item.visibility = option.testFlag(DockWindowOption::Visible);
    item.tabbed = option.testFlag(DockWindowOption::HiddenTabbed);
    _items << item;
}

void DockWindowItems::setDockingArea(const char* name, Qt::DockWidgetArea pos)
{
    for (QList<DockWindowItem>::iterator it = _items.begin(); it != _items.end(); ++it) {
        if (it->name == QString::fromUtf8(name)) {
            it->pos = pos;
            break;
        }
    }
}

void DockWindowItems::setVisibility(const char* name, bool v)
{
    for (QList<DockWindowItem>::iterator it = _items.begin(); it != _items.end(); ++it) {
        if (it->name == QString::fromUtf8(name)) {
            it->visibility = v;
            break;
        }
    }
}

void DockWindowItems::setVisibility(bool v)
{
    for (QList<DockWindowItem>::iterator it = _items.begin(); it != _items.end(); ++it) {
        it->visibility = v;
    }
}

const QList<DockWindowItem>& DockWindowItems::dockWidgets() const
{
    return this->_items;
}

// -----------------------------------------------------------

namespace Gui
{

class DockWidgetEventFilter: public QObject
{
public:
    bool eventFilter(QObject* o, QEvent* e)
    {
        if (!o->isWidgetType() || e->type() != QEvent::MouseMove) {
            return false;
        }
        auto widget = qobject_cast<QDockWidget*>(o);
        if (!widget || !widget->isFloating()) {
            if (overridden) {
                overridden = false;
                QApplication::restoreOverrideCursor();
            }
            return false;
        }
        if (static_cast<QMouseEvent*>(e)->buttons() != Qt::NoButton) {
            return false;
        }
        auto pos = QCursor::pos();
        QPoint topLeft = widget->mapToGlobal(QPoint(cursorMargin, cursorMargin));
        int h = widget->frameGeometry().height();
        int w = widget->frameGeometry().width();
        QPoint bottomRight = widget->mapToGlobal(QPoint(w - cursorMargin, h - cursorMargin));
        bool left = QRect(topLeft - QPoint(cursorMargin, cursorMargin), QSize(cursorMargin, h))
                        .contains(pos);
        bool right = QRect(bottomRight.x(), topLeft.y(), cursorMargin, h).contains(pos);
        bool bottom = QRect(topLeft.x() - cursorMargin, bottomRight.y(), w, cursorMargin).contains(pos);
        auto cursor = Qt::ArrowCursor;
        if (left && bottom) {
            cursor = Qt::SizeBDiagCursor;
        }
        else if (right && bottom) {
            cursor = Qt::SizeFDiagCursor;
        }
        else if (bottom) {
            cursor = Qt::SizeVerCursor;
        }
        else if (left || right) {
            cursor = Qt::SizeHorCursor;
        }
        else if (overridden) {
            overridden = false;
            QApplication::restoreOverrideCursor();
            return false;
        }
        if (overridden) {
            QApplication::changeOverrideCursor(cursor);
        }
        else {
            overridden = true;
            QApplication::setOverrideCursor(cursor);
        }
        return false;
    }

    bool overridden = false;
    int cursorMargin = 5;
};

struct DockWindowManagerP
{
    QList<QDockWidget*> _dockedWindows;
    QMap<QString, QPointer<QWidget>> _dockWindows;
    DockWindowItems _dockWindowItems;
    ParameterGrp::handle _hPref;
    fastsignals::advanced_scoped_connection _connParam;
    QTimer _timer;
    DockWidgetEventFilter _dockWidgetEventFilter;
    QPointer<OverlayManager> overlayManager;
};
}  // namespace Gui

DockWindowManager* DockWindowManager::_instance = nullptr;

DockWindowManager* DockWindowManager::instance()
{
    if (!_instance) {
        _instance = new DockWindowManager;
    }
    return _instance;
}

void DockWindowManager::destruct()
{
    delete _instance;
    _instance = nullptr;
}

DockWindowManager::DockWindowManager()
{
    d = new DockWindowManagerP;
    d->_hPref = App::GetApplication().GetUserParameter().GetGroup("BaseApp/MainWindow/DockWindows");

    auto grp = App::GetApplication().GetUserParameter().GetGroup("BaseApp/Preferences/DockWindows");
    if (grp->GetBool("ActivateOverlay", true)) {
        setupOverlayManagement();
    }
}

DockWindowManager::~DockWindowManager()
{
    d->_dockedWindows.clear();
    delete d;
}

bool DockWindowManager::repairDuplicateDockState(QMainWindow* mainWindow, const QByteArray& state)
{
    if (!mainWindow || state.isEmpty()) {
        return false;
    }

    QMap<QString, QDockWidget*> realDocks;
    QSet<QString> ambiguousNames;
    const auto existingDocks = mainWindow->findChildren<QDockWidget*>();
    for (auto* dock : existingDocks) {
        const QString name = dock->objectName();
        if (name.isEmpty()) {
            continue;
        }
        if (realDocks.contains(name)) {
            ambiguousNames.insert(name);
        }
        else {
            realDocks.insert(name, dock);
        }
    }
    for (const auto& name : ambiguousNames) {
        realDocks.remove(name);
    }

    QMap<QString, int> duplicateCounts;
    int proxyCount = 0;
    for (auto it = realDocks.cbegin(); it != realDocks.cend(); ++it) {
        const int occurrences = serializedStringOccurrences(state, it.key());
        if (occurrences > 1) {
            proxyCount += occurrences - 1;
            if (proxyCount > maxMaterializedDockProxies) {
                Base::Console().warning(
                    "Skipped duplicate dock layout repair: state requires more than %d "
                    "temporary docks.\n",
                    maxMaterializedDockProxies
                );
                return false;
            }
            duplicateCounts.insert(it.key(), occurrences);
        }
    }
    if (duplicateCounts.isEmpty()) {
        return false;
    }

    // Materialize every saved occurrence. QMainWindow otherwise binds the real dock to the
    // first matching entry and leaves later entries as placeholders, which can hide the user's
    // actual tab group indefinitely.
    QMap<QString, QList<QDockWidget*>> instances;
    QList<QDockWidget*> proxies;
    for (auto it = duplicateCounts.cbegin(); it != duplicateCounts.cend(); ++it) {
        QList<QDockWidget*> docks {realDocks.value(it.key())};
        for (int index = 1; index < it.value(); ++index) {
            auto* proxy = new QDockWidget(mainWindow);
            proxy->setObjectName(it.key());
            docks.push_back(proxy);
            proxies.push_back(proxy);
        }
        instances.insert(it.key(), docks);
    }

    if (!mainWindow->restoreState(state)) {
        for (auto* proxy : proxies) {
            proxy->setObjectName(QString());
            delete proxy;
        }
        return false;
    }

    QMap<QString, DuplicateDockStateChoice> choices;
    for (auto it = instances.cbegin(); it != instances.cend(); ++it) {
        DuplicateDockStateChoice choice;
        choice.dock = realDocks.value(it.key());
        const auto& docks = it.value();
        for (int index = 0; index < docks.size(); ++index) {
            auto* dock = docks.at(index);
            QSet<QString> groupNames {it.key()};
            const auto tabPeers = mainWindow->tabifiedDockWidgets(dock);
            for (auto* peer : tabPeers) {
                const QString peerName = peer->objectName();
                if (realDocks.contains(peerName)) {
                    groupNames.insert(peerName);
                }
            }

            const int score = groupNames.size() - 1;
            // Prefer the occurrence carrying the richest real tab group. For ungrouped
            // duplicates, retain the first occurrence that Qt previously displayed.
            if (score < choice.score
                || (score == choice.score && (score == 0 || index < choice.occurrence))) {
                continue;
            }

            choice.area = mainWindow->dockWidgetArea(dock);
            choice.geometry = dock->geometry();
            choice.groupNames = groupNames.values();
            choice.groupNames.sort();
            choice.visible = !dock->isHidden();
            choice.floating = dock->isFloating();
            choice.score = score;
            choice.occurrence = index;
        }
        choices.insert(it.key(), choice);
    }

    // Destroy the materialized copies, then consume the temporary placeholders Qt creates
    // during QDockWidget destruction. The final anonymous placeholder is intentionally skipped
    // by QMainWindow::restoreState and disappears on the next serialization.
    QStringList recoveryNames;
    int recoveryIndex = 0;
    for (auto* proxy : proxies) {
        const QString recoveryName
            = QStringLiteral("__FreeCADDockStateRecovery_%1").arg(recoveryIndex++);
        recoveryNames.push_back(recoveryName);
        proxy->setObjectName(recoveryName);
        delete proxy;
    }
    for (const auto& recoveryName : recoveryNames) {
        auto* cleanup = new QDockWidget(mainWindow);
        cleanup->setObjectName(recoveryName);
        mainWindow->addDockWidget(Qt::RightDockWidgetArea, cleanup);
        cleanup->setObjectName(QString());
        delete cleanup;
    }

    QMap<QString, QStringList> tabGroups;
    for (auto it = choices.cbegin(); it != choices.cend(); ++it) {
        const auto& choice = it.value();
        if (!choice.dock) {
            continue;
        }
        if (isDockingArea(choice.area)) {
            mainWindow->addDockWidget(choice.area, choice.dock);
        }
        if (choice.floating) {
            choice.dock->setFloating(true);
            choice.dock->setGeometry(choice.geometry);
        }
        choice.dock->setVisible(choice.visible);

        if (choice.score > 0) {
            tabGroups.insert(choice.groupNames.join(QChar(0x1f)), choice.groupNames);
        }
    }

    for (const auto& groupNames : tabGroups) {
        QList<QDockWidget*> group;
        for (const auto& name : groupNames) {
            if (auto* dock = realDocks.value(name)) {
                group.push_back(dock);
            }
        }
        if (group.size() < 2) {
            continue;
        }
        for (int index = 1; index < group.size(); ++index) {
            mainWindow->tabifyDockWidget(group.front(), group.at(index));
        }
    }

    const QByteArray repairedNames = duplicateCounts.keys().join(QStringLiteral(", ")).toUtf8();
    Base::Console().warning("Repaired duplicate dock layout state for: %s\n", repairedNames.constData());
    return true;
}

bool DockWindowManager::isOverlayActivated() const
{
    return (d->overlayManager != nullptr);
}

void DockWindowManager::setupOverlayManagement()
{
    d->overlayManager = OverlayManager::instance();

    qApp->installEventFilter(&d->_dockWidgetEventFilter);

    d->_dockWidgetEventFilter.cursorMargin = d->_hPref->GetInt("CursorMargin", 5);
    d->_connParam = d->_hPref->Manager()->signalParamChanged.connect(
        [this](ParameterGrp* Param, ParameterGrp::ParamType Type, const char* name, const char*) {
            if (Param == d->_hPref) {
                switch (Type) {
                    case ParameterGrp::ParamType::FCBool:
                        // For batch process UI setting changes, e.g. loading new preferences
                        d->_timer.start(100);
                        break;
                    case ParameterGrp::ParamType::FCInt:
                        if (name && boost::equals(name, "CursorMargin")) {
                            d->_dockWidgetEventFilter.cursorMargin
                                = d->_hPref->GetInt("CursorMargin", 5);
                        }
                        break;
                    default:
                        break;
                }
            }
        },
        fastsignals::advanced_tag()
    );

    d->_timer.setSingleShot(true);

    connect(&d->_timer, &QTimer::timeout, [this]() {
        for (auto w : this->getDockWindows()) {
            if (auto dw = qobject_cast<QDockWidget*>(w)) {
                QSignalBlocker blocker(dw);
                QByteArray dockName = dw->toggleViewAction()->data().toByteArray();
                dw->setVisible(d->_hPref->GetBool(dockName, dw->isVisible()));
            }
        }
    });
}

/**
 * Adds a new dock window to the main window and embeds the given \a widget.
 */
QDockWidget* DockWindowManager::addDockWindow(const char* name, QWidget* widget, Qt::DockWidgetArea pos)
{
    if (!widget) {
        return nullptr;
    }
    QDockWidget* dw = qobject_cast<QDockWidget*>(widget->parentWidget());
    if (dw) {
        return dw;
    }

    // creates the dock widget as container to embed this widget
    MainWindow* mw = getMainWindow();
    dw = new QDockWidget(mw);
    // QMainWindow identifies saved dock layout entries by objectName. Set it before
    // asking Qt to restore a dock that was created after MainWindow::restoreState().
    dw->setObjectName(QString::fromUtf8(name));

    if (d->overlayManager) {
        d->overlayManager->setupTitleBar(dw);
    }

    // Note: By default all dock widgets are hidden but the user can show them manually in the view
    // menu. First, hide immediately the dock widget to avoid flickering, after setting up the dock
    // widgets MainWindow::loadLayoutSettings() is called to restore the layout.
    dw->hide();
    connect(dw, &QObject::destroyed, this, &DockWindowManager::onDockWidgetDestroyed);
    connect(widget, &QObject::destroyed, this, &DockWindowManager::onWidgetDestroyed);

    // add the widget to the dock widget
    widget->setParent(dw);
    dw->setWidget(widget);

    // set window title needed for i18n stuff
    dw->setWindowTitle(widget->windowTitle());
    dw->setFeatures(
        QDockWidget::DockWidgetClosable | QDockWidget::DockWidgetMovable
        | QDockWidget::DockWidgetFloatable
    );

    // loadWindowSettings() can restore the main-window state before a workbench creates its
    // docks. Qt retains placeholders for those late-created docks; restoreDockWidget() must be
    // called before addDockWidget() so Qt consumes the saved entry instead of creating another.
    if (!mw->restoreDockWidget(dw)) {
        switch (pos) {
            case Qt::LeftDockWidgetArea:
            case Qt::RightDockWidgetArea:
            case Qt::TopDockWidgetArea:
            case Qt::BottomDockWidgetArea:
                mw->addDockWidget(pos, dw);
            default:
                break;
        }
    }

    d->_dockedWindows.push_back(dw);

    if (d->overlayManager) {
        d->overlayManager->initDockWidget(dw);
    }

    connect(dw->toggleViewAction(), &QAction::triggered, [this, dw]() {
        Base::ConnectionBlocker block(d->_connParam);
        QByteArray dockName = dw->toggleViewAction()->data().toByteArray();
        d->_hPref->SetBool(dockName.constData(), dw->isVisible());
    });

    auto cb = []() {
        getMainWindow()->saveWindowSettings(true);
    };
    connect(dw, &QDockWidget::topLevelChanged, cb);
    connect(dw, &QDockWidget::dockLocationChanged, cb);
    return dw;
}

/**
 * Returns the widget inside the dock window by name.
 * If it does not exist 0 is returned.
 */
QWidget* DockWindowManager::getDockWindow(const char* name) const
{
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        if ((*it)->objectName() == QString::fromUtf8(name)) {
            return (*it)->widget();
        }
    }

    return nullptr;
}

/**
 * Returns the dock widget by name.
 * If it does not exist 0 is returned.
 */
QDockWidget* DockWindowManager::getDockContainer(const char* name) const
{
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        if ((*it)->objectName() == QLatin1String(name)) {
            return (*it);
        }
    }

    return nullptr;
}

/**
 * Returns a list of all widgets inside the dock windows.
 */
QList<QWidget*> DockWindowManager::getDockWindows() const
{
    QList<QWidget*> docked;
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        docked.push_back((*it)->widget());
    }
    return docked;
}

/**
 * Removes the specified dock window with name \name without deleting it.
 */
QWidget* DockWindowManager::removeDockWindow(const char* name)
{
    QWidget* widget = nullptr;
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        if ((*it)->objectName() == QString::fromUtf8(name)) {
            QDockWidget* dw = *it;
            d->_dockedWindows.erase(it);

            if (d->overlayManager) {
                d->overlayManager->unsetupDockWidget(dw);
            }

            getMainWindow()->removeDockWidget(dw);
            // avoid to destruct the embedded widget
            widget = dw->widget();
            widget->setParent(nullptr);
            dw->setWidget(nullptr);
            disconnect(dw, &QObject::destroyed, this, &DockWindowManager::onDockWidgetDestroyed);
            disconnect(widget, &QObject::destroyed, this, &DockWindowManager::onWidgetDestroyed);
            delete dw;  // destruct the QDockWidget, i.e. the parent of the widget
            break;
        }
    }

    return widget;
}

/**
 * Method provided for convenience. Does basically the same as the method above unless that
 * it accepts a pointer.
 */
void DockWindowManager::removeDockWindow(QWidget* widget)
{
    if (!widget) {
        return;
    }
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        if ((*it)->widget() == widget) {
            QDockWidget* dw = *it;
            d->_dockedWindows.erase(it);
            if (d->overlayManager) {
                d->overlayManager->unsetupDockWidget(dw);
            }
            getMainWindow()->removeDockWidget(dw);
            // avoid to destruct the embedded widget
            widget->setParent(nullptr);
            dw->setWidget(nullptr);
            disconnect(dw, &QObject::destroyed, this, &DockWindowManager::onDockWidgetDestroyed);
            disconnect(widget, &QObject::destroyed, this, &DockWindowManager::onWidgetDestroyed);
            delete dw;  // destruct the QDockWidget, i.e. the parent of the widget
            break;
        }
    }
}

/**
 * If the corresponding dock widget isn't visible then activate it.
 */
void DockWindowManager::activate(QWidget* widget)
{
    QDockWidget* dw = nullptr;
    QWidget* par = widget->parentWidget();
    while (par) {
        dw = qobject_cast<QDockWidget*>(par);
        if (dw) {
            break;
        }
        par = par->parentWidget();
    }

    if (!dw) {
        return;
    }

    if (!dw->toggleViewAction()->isChecked()) {
        dw->toggleViewAction()->activate(QAction::Trigger);
    }

    dw->raise();
}

/**
 * Sets the window title for the dockable windows.
 */
void DockWindowManager::retranslate()
{
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        QString title = (*it)->windowTitle();
        if (title.isEmpty()) {
            (*it)->setWindowTitle(QDockWidget::tr((*it)->objectName().toUtf8()));
        }
        else {
            (*it)->setWindowTitle(title);
        }
    }
}

/**
 * Appends a new \a widget with \a name to the list of available dock widgets. The caller must make
 * sure that the name is unique. If a widget with this name is already registered nothing is done
 * but false is returned, otherwise it is appended and true is returned.
 *
 * As default the following widgets are already registered:
 * \li Std_TreeView
 * \li Std_PropertyView
 * \li Std_ReportView
 * \li Std_ToolBox
 * \li Std_ComboView
 * \li Std_SelectionView
 *
 * To avoid name clashes the caller should use names of the form \a module_widgettype, i. e. if a
 * analyse dialog for the mesh module is added the name must then be Mesh_AnalyzeDialog.
 *
 * To make use of dock windows when a workbench gets loaded the method setupDockWindows() must
 * reimplemented in a subclass of Gui::Workbench.
 */
bool DockWindowManager::registerDockWindow(const char* name, QWidget* widget)
{
    QMap<QString, QPointer<QWidget>>::Iterator it = d->_dockWindows.find(QString::fromUtf8(name));
    if (it != d->_dockWindows.end() || !widget) {
        return false;
    }
    d->_dockWindows[QString::fromUtf8(name)] = widget;
    widget->hide();  // hide the widget if not used
    return true;
}

QWidget* DockWindowManager::unregisterDockWindow(const char* name)
{
    QWidget* widget = nullptr;
    QMap<QString, QPointer<QWidget>>::Iterator it = d->_dockWindows.find(QString::fromUtf8(name));
    if (it != d->_dockWindows.end()) {
        widget = d->_dockWindows.take(QString::fromUtf8(name));
    }

    return widget;
}

QWidget* DockWindowManager::findRegisteredDockWindow(const char* name)
{
    QMap<QString, QPointer<QWidget>>::Iterator it = d->_dockWindows.find(QString::fromUtf8(name));
    if (it != d->_dockWindows.end()) {
        return it.value();
    }
    return nullptr;
}

/** Sets up the dock windows of the activated workbench. */
void DockWindowManager::setup(DockWindowItems* items)
{
    // save state of current dock windows
    saveState();
    d->_dockWindowItems = *items;

    QList<QDockWidget*> docked = d->_dockedWindows;
    const QList<DockWindowItem>& dws = items->dockWidgets();
    for (const auto& it : dws) {
        QDockWidget* dw = findDockWidget(docked, it.name);
        QByteArray dockName = it.name.toLatin1();
        bool visible = d->_hPref->GetBool(dockName.constData(), it.visibility);

        if (!dw) {
            QMap<QString, QPointer<QWidget>>::Iterator jt = d->_dockWindows.find(it.name);
            if (jt != d->_dockWindows.end()) {
                dw = addDockWindow(dockName.constData(), jt.value(), it.pos);
                jt.value()->show();
                dw->toggleViewAction()->setData(it.name);
                dw->setVisible(visible);
            }
        }
        else {
            dw->setVisible(visible);
            dw->toggleViewAction()->setVisible(true);
            int index = docked.indexOf(dw);
            docked.removeAt(index);
        }

        if (d->overlayManager && dw && visible && !dw->titleBarWidget()) {
            d->overlayManager->setupDockWidget(dw);
        }
    }

    tabifyDockWidgets(items);
}

void DockWindowManager::tabifyDockWidgets(DockWindowItems* items)
{
    // Tabify dock widgets only once to avoid to override the current layout
    // in case it was modified by the user. The user shouldn't be forced to
    // restore a possibly changed layout after switching to another workbench.
    static bool tabify = false;
    if (tabify) {
        return;
    }

    std::array<QList<QDockWidget*>, 4> areas;
    const QList<DockWindowItem>& dws = items->dockWidgets();
    QList<QDockWidget*> docked = d->_dockedWindows;
    for (const auto& it : dws) {
        QDockWidget* dw = findDockWidget(docked, it.name);
        if (it.tabbed && dw) {
            Qt::DockWidgetArea pos = getMainWindow()->dockWidgetArea(dw);
            switch (pos) {
                case Qt::LeftDockWidgetArea:
                    areas[0] << dw;
                    break;
                case Qt::RightDockWidgetArea:
                    areas[1] << dw;
                    break;
                case Qt::TopDockWidgetArea:
                    areas[2] << dw;
                    break;
                case Qt::BottomDockWidgetArea:
                    areas[3] << dw;
                    break;
                default:
                    break;
            }
        }
    }

    // tabify dock widgets for which "tabbed" is true and which have the same position
    for (auto& area : areas) {
        for (auto it : area) {
            if (it != area.front()) {
                getMainWindow()->tabifyDockWidget(area.front(), it);
                tabify = true;
            }
        }

        // activate the first of the tabbed dock widgets
        if (area.size() > 1) {
            area.front()->raise();
        }
    }
}

void DockWindowManager::saveState()
{
    const QList<DockWindowItem>& dockItems = d->_dockWindowItems.dockWidgets();
    for (QList<DockWindowItem>::ConstIterator it = dockItems.begin(); it != dockItems.end(); ++it) {
        QDockWidget* dw = findDockWidget(d->_dockedWindows, it->name);
        if (dw) {
            QByteArray dockName = dw->toggleViewAction()->data().toByteArray();
            d->_hPref->SetBool(dockName.constData(), dw->isVisible());
        }
    }
}

void DockWindowManager::loadState()
{
    ParameterGrp::handle hPref = App::GetApplication()
                                     .GetUserParameter()
                                     .GetGroup("BaseApp")
                                     ->GetGroup("MainWindow")
                                     ->GetGroup("DockWindows");
    const QList<DockWindowItem>& dockItems = d->_dockWindowItems.dockWidgets();
    for (QList<DockWindowItem>::ConstIterator it = dockItems.begin(); it != dockItems.end(); ++it) {
        QDockWidget* dw = findDockWidget(d->_dockedWindows, it->name);
        if (dw) {
            QByteArray dockName = it->name.toUtf8();
            bool visible = hPref->GetBool(dockName.constData(), it->visibility);
            dw->setVisible(visible);
        }
    }
}

QDockWidget* DockWindowManager::findDockWidget(const QList<QDockWidget*>& dw, const QString& name) const
{
    for (QList<QDockWidget*>::ConstIterator it = dw.begin(); it != dw.end(); ++it) {
        if ((*it)->toggleViewAction()->data().toString() == name) {
            return *it;
        }
    }

    return nullptr;
}

void DockWindowManager::onDockWidgetDestroyed(QObject* dw)
{
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        if (*it == dw) {
            d->_dockedWindows.erase(it);
            break;
        }
    }
}

void DockWindowManager::onWidgetDestroyed(QObject* widget)
{
    for (QList<QDockWidget*>::Iterator it = d->_dockedWindows.begin(); it != d->_dockedWindows.end();
         ++it) {
        // make sure that the dock widget is not about to being deleted
        if ((*it)->metaObject() != &QDockWidget::staticMetaObject) {
            disconnect(*it, &QObject::destroyed, this, &DockWindowManager::onDockWidgetDestroyed);
            d->_dockedWindows.erase(it);
            break;
        }

        if ((*it)->widget() == widget) {
            // Delete the widget if not used anymore
            QDockWidget* dw = *it;
            dw->deleteLater();
            break;
        }
    }
}

#include "moc_DockWindowManager.cpp"
