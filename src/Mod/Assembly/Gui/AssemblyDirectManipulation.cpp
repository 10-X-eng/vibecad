// SPDX-License-Identifier: LGPL-2.1-or-later

#include "AssemblyDirectManipulation.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>

#include <QApplication>
#include <QEvent>
#include <QGuiApplication>
#include <QKeyEvent>
#include <QMouseEvent>

#include <Inventor/SbVec2s.h>

#include <App/Document.h>
#include <App/DocumentObject.h>

#include <Gui/Application.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Quarter/devices/InputDevice.h>
#include <Gui/Selection/Selection.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/WorkbenchManager.h>

#include <Mod/Assembly/App/AssemblyObject.h>
#include <Mod/Assembly/App/AssemblyUtils.h>

#include "ViewProviderAssembly.h"

namespace AssemblyGui
{

namespace
{

constexpr auto controllerObjectName = "AssemblyDirectManipulationController";

bool containsAssemblyMember(Assembly::AssemblyObject* assembly, App::DocumentObject* object)
{
    return assembly && object && assembly != object && assembly->hasObject(object, true);
}

SbVec2s eventPosition(const Gui::View3DInventorViewer* viewer, const QMouseEvent* event)
{
    return SIM::Coin3D::Quarter::InputDevice::toDevicePixelPosition(
        event->position(),
        SbVec2s(viewer->width(), viewer->height()),
        viewer->devicePixelRatio()
    );
}

}  // namespace

void AssemblyDirectManipulation::install()
{
    auto* mainWindow = Gui::getMainWindow();
    if (!mainWindow || mainWindow->findChild<QObject*>(controllerObjectName)) {
        return;
    }
    auto* controller = new AssemblyDirectManipulation(mainWindow);
    controller->setObjectName(controllerObjectName);
}

AssemblyDirectManipulation::AssemblyDirectManipulation(QObject* parent)
    : QObject(parent)
{
    auto* application = Gui::Application::Instance;
    activateWorkbenchConnection = application->signalActivateWorkbench.connect([this](const char*) {
        finishCandidate(false);
        refreshViewer();
    });
    activateViewConnection = application->signalActivateView.connect([this](const Gui::MDIView* view) {
        auto* view3d = dynamic_cast<const Gui::View3DInventor*>(view);
        setViewer(supportsActiveWorkbench() && view3d ? view3d->getViewer() : nullptr);
    });
    closeViewConnection = application->signalCloseView.connect([this](const Gui::MDIView* view) {
        auto* view3d = dynamic_cast<const Gui::View3DInventor*>(view);
        if (view3d && view3d->getViewer() == viewer) {
            setViewer(nullptr);
        }
    });
    enterEditConnection = application->signalInEdit.connect(
        [this](const Gui::ViewProviderDocumentObject&) { finishCandidate(false); }
    );
    connect(Gui::getMainWindow(), &Gui::MainWindow::mainWindowClosed, this, [this]() {
        setViewer(nullptr);
    });
    connect(qApp, &QGuiApplication::applicationStateChanged, this, [this](Qt::ApplicationState state) {
        if (state != Qt::ApplicationActive) {
            finishCandidate(false);
        }
    });
    refreshViewer();
}

AssemblyDirectManipulation::~AssemblyDirectManipulation()
{
    setViewer(nullptr);
}

bool AssemblyDirectManipulation::supportsActiveWorkbench() const
{
    const std::string workbench = Gui::WorkbenchManager::instance()->activeName();
    return workbench == "AssemblyWorkbench" || workbench == "PartDesignWorkbench";
}

void AssemblyDirectManipulation::refreshViewer()
{
    if (!supportsActiveWorkbench()) {
        setViewer(nullptr);
        return;
    }
    auto* view = dynamic_cast<Gui::View3DInventor*>(Gui::getMainWindow()->activeWindow());
    setViewer(view ? view->getViewer() : nullptr);
}

void AssemblyDirectManipulation::setViewer(Gui::View3DInventorViewer* nextViewer)
{
    if (viewer == nextViewer) {
        return;
    }
    const bool wasMoving = finishCandidate(false);
    if (viewer) {
        if (wasMoving) {
            viewer->setSelectionEnabled(true);
        }
        viewer->removeEventFilter(this);
        viewer->viewport()->removeEventFilter(this);
    }
    viewer = nextViewer;
    if (viewer) {
        viewer->installEventFilter(this);
        viewer->viewport()->installEventFilter(this);
    }
}

ViewProviderAssembly* AssemblyDirectManipulation::resolveAssemblyAtPreselection() const
{
    if (!viewer || viewer->isEditingViewProvider() || !Gui::Selection().hasPreselection()) {
        return nullptr;
    }
    auto* guiDocument = viewer->getDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto& preselection = Gui::Selection().getPreselection();
    auto* selectedRoot = preselection.Object.getObject();
    if (!document || !selectedRoot || selectedRoot->getDocument() != document
        || Gui::Control().activeDialog(document)) {
        return nullptr;
    }

    Assembly::AssemblyObject* selectedAssembly = nullptr;
    if (selectedRoot->is<Assembly::AssemblyObject>()) {
        selectedAssembly = static_cast<Assembly::AssemblyObject*>(selectedRoot);
    }
    else {
        for (auto* assembly : document->getObjectsOfType<Assembly::AssemblyObject>()) {
            if (!Assembly::isTimelineOperationActive(assembly)
                || !containsAssemblyMember(assembly, selectedRoot)) {
                continue;
            }
            if (!selectedAssembly || containsAssemblyMember(selectedAssembly, assembly)) {
                selectedAssembly = assembly;
            }
            else if (!containsAssemblyMember(assembly, selectedAssembly)) {
                return nullptr;
            }
        }
    }
    if (!selectedAssembly || !Assembly::isTimelineOperationActive(selectedAssembly)) {
        return nullptr;
    }
    return freecad_cast<ViewProviderAssembly*>(guiDocument->getViewProvider(selectedAssembly));
}

ViewProviderAssembly* AssemblyDirectManipulation::resolveCandidate() const
{
    auto* guiDocument = viewer ? viewer->getDocument() : nullptr;
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document || document->Uid.getValueStr() != documentUid || assemblyId < 0) {
        return nullptr;
    }
    auto* assembly = freecad_cast<Assembly::AssemblyObject*>(document->getObjectByID(assemblyId));
    if (!assembly) {
        return nullptr;
    }
    return freecad_cast<ViewProviderAssembly*>(guiDocument->getViewProvider(assembly));
}

void AssemblyDirectManipulation::beginCandidate(const SbVec2s& position)
{
    finishCandidate(false);
    auto* assemblyView = resolveAssemblyAtPreselection();
    auto* assembly = assemblyView ? assemblyView->getObject<Assembly::AssemblyObject>() : nullptr;
    if (!assembly || !assemblyView->prepareDirectManipulation()) {
        return;
    }
    documentUid = assembly->getDocument()->Uid.getValueStr();
    assemblyId = assembly->getID();
    pressPosition = position;
    leftButtonDown = true;
}

bool AssemblyDirectManipulation::moveCandidate(const SbVec2s& position)
{
    if (!leftButtonDown || assemblyId < 0) {
        return false;
    }
    auto* assemblyView = resolveCandidate();
    if (!assemblyView) {
        clearCandidate();
        return false;
    }
    if (!moving) {
        const int dx = std::abs(int(position[0]) - int(pressPosition[0]));
        const int dy = std::abs(int(position[1]) - int(pressPosition[1]));
        const int threshold = std::lround(
            QApplication::startDragDistance() * viewer->devicePixelRatio()
        );
        if (std::max(dx, dy) < threshold) {
            return false;
        }
        moving = assemblyView->beginDirectManipulation(pressPosition, viewer);
        if (!moving) {
            clearCandidate();
            return false;
        }
    }
    return assemblyView->updateDirectManipulation(position, viewer);
}

bool AssemblyDirectManipulation::finishCandidate(bool commit)
{
    const bool wasMoving = moving;
    if (auto* assemblyView = resolveCandidate()) {
        assemblyView->finishDirectManipulation(commit && moving);
    }
    clearCandidate();
    if (commit && wasMoving && Gui::Application::Instance) {
        Gui::Application::Instance->updateActions();
    }
    return wasMoving;
}

void AssemblyDirectManipulation::clearCandidate()
{
    documentUid.clear();
    assemblyId = -1;
    leftButtonDown = false;
    moving = false;
}

bool AssemblyDirectManipulation::eventFilter(QObject* watched, QEvent* event)
{
    if (!viewer || !event || (watched != viewer && watched != viewer->viewport())) {
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::KeyPress) {
        const auto* keyEvent = static_cast<const QKeyEvent*>(event);
        if (keyEvent->key() == Qt::Key_Escape && finishCandidate(false)) {
            return true;
        }
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::MouseButtonPress) {
        const auto* mouseEvent = static_cast<const QMouseEvent*>(event);
        if (watched == viewer->viewport() && mouseEvent->button() == Qt::LeftButton
            && mouseEvent->modifiers() == Qt::NoModifier) {
            beginCandidate(eventPosition(viewer, mouseEvent));
        }
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::MouseMove) {
        const auto* mouseEvent = static_cast<const QMouseEvent*>(event);
        if (watched == viewer->viewport() && mouseEvent->buttons().testFlag(Qt::LeftButton)
            && moveCandidate(eventPosition(viewer, mouseEvent))) {
            return true;
        }
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::MouseButtonRelease) {
        const auto* mouseEvent = static_cast<const QMouseEvent*>(event);
        if (watched == viewer->viewport() && mouseEvent->button() == Qt::LeftButton) {
            finishCandidate(true);
        }
        return QObject::eventFilter(watched, event);
    }

    if (event->type() == QEvent::WindowDeactivate) {
        finishCandidate(false);
    }
    return QObject::eventFilter(watched, event);
}

}  // namespace AssemblyGui
