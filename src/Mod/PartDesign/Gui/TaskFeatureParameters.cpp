// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (C) 2015 Alexander Golubev (Fat-Zer) <fatzer2@gmail.com>    *
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

#include <QApplication>
#include <QMessageBox>

#include <unordered_map>

#include <App/Application.h>
#include <App/DocumentObserver.h>
#include <Gui/Application.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/InputHint.h>
#include <Gui/Inventor/Draggers/Gizmo.h>
#include <Gui/MainWindow.h>
#include <Gui/BitmapFactory.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/Feature.h>

#include "ui_TaskPreviewParameters.h"

#include "TaskDesignOperation.h"
#include "TaskFeatureParameters.h"
#include "TaskDialogState.h"
#include "TaskSketchBasedParameters.h"

using namespace PartDesignGui;
using namespace Gui;

namespace
{
std::unordered_map<
    const TaskDlgFeatureParameters*,
    TaskInternal::VisibilitySnapshot>
    taskVisibility;
}

/*********************************************************************
 *                      Task Feature Parameters                      *
 *********************************************************************/

TaskPreviewParameters::TaskPreviewParameters(ViewProvider* vp, QWidget* parent)
    : TaskBox(BitmapFactory().pixmap("tree-pre-sel"), tr("Preview"), true, parent)
    , vp(vp)
    , ui(std::make_unique<Ui_TaskPreviewParameters>())
{
    vp->showPreviousFeature(!hGrp->GetBool("ShowFinal", false));
    vp->showPreview(hGrp->GetBool("ShowTransparentPreview", true));

    auto* proxy = new QWidget(this);
    ui->setupUi(proxy);

    ui->showFinalCheckBox->setChecked(vp->isVisible());
    ui->showTransparentPreviewCheckBox->setChecked(vp->isPreviewEnabled());

#if QT_VERSION >= QT_VERSION_CHECK(6, 7, 0)
    connect(
        ui->showTransparentPreviewCheckBox,
        &QCheckBox::checkStateChanged,
        this,
        &TaskPreviewParameters::onShowPreviewChanged
    );
    connect(
        ui->showFinalCheckBox,
        &QCheckBox::checkStateChanged,
        this,
        &TaskPreviewParameters::onShowFinalChanged
    );
#else
    connect(
        ui->showTransparentPreviewCheckBox,
        &QCheckBox::stateChanged,
        this,
        &TaskPreviewParameters::onShowPreviewChanged
    );
    connect(
        ui->showFinalCheckBox,
        &QCheckBox::stateChanged,
        this,
        &TaskPreviewParameters::onShowFinalChanged
    );
#endif

    groupLayout()->addWidget(proxy);
}

TaskPreviewParameters::~TaskPreviewParameters() = default;

void TaskPreviewParameters::onShowFinalChanged(bool show)
{
    vp->showPreviousFeature(!show);
}

void TaskPreviewParameters::onShowPreviewChanged(bool show)
{
    vp->showPreview(show);
}

TaskFeatureParameters::TaskFeatureParameters(
    PartDesignGui::ViewProvider* vp,
    QWidget* parent,
    const std::string& pixmapname,
    const QString& parname
)
    : TaskBox(Gui::BitmapFactory().pixmap(pixmapname.c_str()), parname, true, parent)
    , vp(vp)
    , blockUpdate(false)
{
    Gui::Document* doc = vp->getDocument();
    this->attachDocument(doc);
}

TaskFeatureParameters::~TaskFeatureParameters()
{
    hideDraggerHints();
}

void TaskFeatureParameters::showDraggerHints()
{
    if (!Gui::GizmoContainer::isEnabled() || !Gui::GizmoContainer::isCoarseSnapEnabled()) {
        return;
    }

    const Gui::InputHint::UserInput key = Gui::GizmoContainer::getFineSnapKey();
    const bool coarseByDefault = Gui::GizmoContainer::isCoarseByDefault();

    QString message;
    if (coarseByDefault) {
        message = tr("%1 fine dragging");
    }
    else {
        message = tr("%1 coarse dragging");
    }

    Gui::getMainWindow()->showHints({{
        .message = message,
        .sequences = {{key}},
    }});
}

void TaskFeatureParameters::hideDraggerHints()
{
    Gui::getMainWindow()->hideHints();
}

void TaskFeatureParameters::slotDeletedObject(const Gui::ViewProviderDocumentObject& Obj)
{
    if (this->vp == &Obj) {
        this->vp = nullptr;
    }
}

void TaskFeatureParameters::onUpdateView(bool on)
{
    blockUpdate = !on;
    recomputeFeature();
}

void TaskFeatureParameters::recomputeFeature()
{
    if (!blockUpdate) {
        auto* feature = getObject<PartDesign::Feature>();
        assert(feature);

        feature->recomputeFeature();
        feature->recomputePreview();
    }
}

/*********************************************************************
 *                            Task Dialog                            *
 *********************************************************************/
TaskDlgFeatureParameters::TaskDlgFeatureParameters(PartDesignGui::ViewProvider* vp)
    : preview(nullptr)
    , vp(vp)
    , designTargets(nullptr)
{
    assert(vp);
    // Gui::Document::resetEdit() is a commit boundary, while cancelEdit() is
    // the explicit rollback boundary.  Keep this dialog alive until the edit
    // transaction has produced that exact outcome so its command checkpoint
    // cannot be destroyed early and reinterpret resetEdit() as Cancel.
    setAutoCloseOnResetEdit(true);
    // The dialog and its parameter widgets are tied to this feature's
    // ViewProvider.  Remove the dialog while TaskView still knows which
    // document owned it; keeping it alive after that document is deleted
    // would leave the task holding an invalid ViewProvider.
    setAutoCloseOnDeletedDocument(true);
    taskVisibility.insert_or_assign(
        this,
        TaskInternal::VisibilitySnapshot(
            vp && vp->getObject()
                ? vp->getObject()->getDocument()
                : nullptr
        )
    );
    preview = new TaskPreviewParameters(vp);
    if (dynamic_cast<PartDesign::DesignOperationProperties*>(
            vp->getObject()
        )) {
        designTargets = new TaskDesignOperationTargets(vp->getObject());
        Content.push_back(designTargets);
    }
}

TaskDlgFeatureParameters::~TaskDlgFeatureParameters()
{
    taskVisibility.erase(this);
}

bool TaskDlgFeatureParameters::accept()
{
    TaskInternal::AcceptedMacro acceptedMacro;
    App::DocumentObject* feature = getObject();
    if (!feature || !feature->getDocument()) {
        acceptedMacro.discard();
        return false;
    }
    bool isUpdateBlocked = false;
    try {
        // Iterate over parameter dialogs and apply all parameters from them
        for (QWidget* wgt : Content) {
            TaskFeatureParameters* param = qobject_cast<TaskFeatureParameters*>(wgt);
            if (!param) {
                continue;
            }

            param->saveHistory();
            param->apply();
            isUpdateBlocked |= param->isUpdateBlocked();
        }
        // Make sure the feature is what we are expecting
        // Should be fine but you never know...
        if (!feature->isDerivedFrom<PartDesign::Feature>()) {
            throw Base::TypeError("Bad object processed in the feature dialog.");
        }

        if (designTargets) {
            // The Design service first validates this controller alone,
            // atomically reconciles its complete Body-state graph, and only
            // then recomputes downstream consumers. Recomputing the whole
            // document before reconciliation would execute deliberately
            // retired output slots and show false errors while a target set
            // is being edited.
            designTargets->finalize();
        }
        else {
            if (isUpdateBlocked) {
                Gui::cmdAppDocument(feature, "recompute()");
            }
            else {
                // object was already computed, nothing more to do with it...
                Gui::cmdAppDocument(feature, "purgeTouched()");

                if (!feature->isValid()) {
                    throw Base::RuntimeError(getObject()->getStatusString());
                }

                // ...but touch parents to signal the change...
                for (auto obj : feature->getInList()) {
                    obj->touch();
                }
                // ...and recompute them
                Gui::cmdAppDocument(feature->getDocument(), "recompute()");
            }

            if (!feature->isValid()) {
                throw Base::RuntimeError(getObject()->getStatusString());
            }

            App::DocumentObject* previous =
                static_cast<PartDesign::Feature*>(feature)->getBaseObject(
                    /* silent = */ true
                );
            Gui::cmdAppObjectHide(previous);
        }

        if (!feature->isValid()) {
            throw Base::RuntimeError(getObject()->getStatusString());
        }
        finalizeAcceptedFeature(feature);

        // detach the task panel from the selection to avoid to invoke
        // eventually onAddSelection when the selection changes
        std::vector<QWidget*> subwidgets = getDialogContent();
        for (auto it : subwidgets) {
            TaskSketchBasedParameters* param = qobject_cast<TaskSketchBasedParameters*>(it);
            if (param) {
                param->detachSelection();
            }
        }

        Gui::cmdGuiDocument(feature, "resetEdit()");
    }
    catch (const Base::Exception& e) {
        acceptedMacro.discard();
        QString errorText = QString::fromUtf8(e.what());
        auto* currentObject = getObject();
        QString statusText = currentObject
            ? QString::fromUtf8(currentObject->getStatusString())
            : QString();

        // generic, fallback error message
        if (errorText == QStringLiteral("Error") || errorText.isEmpty()) {
            if (!statusText.isEmpty() && statusText != QStringLiteral("Error")) {
                errorText = statusText;
            }
            else {
                errorText = tr(
                    "The feature could not be created with the given parameters.\n"
                    "The geometry may be invalid or the parameters may be incompatible.\n"
                    "Adjust the parameters and try again."
                );
            }
        }
        Base::Console().error("%s\n", errorText.toUtf8().constData());
        return false;
    }
    acceptedMacro.publish();
    return true;
}

bool TaskDlgFeatureParameters::reject()
{
    auto* feature = getObject<PartDesign::Feature>();
    if (!feature || !feature->getDocument()) {
        return true;
    }
    App::Document* document = feature->getDocument();
    const std::string documentName = document->getName();
    const std::string featureName =
        feature->getNameInDocument() ? feature->getNameInDocument() : "";
    const auto visibilityState = taskVisibility.find(this);
    const bool hasVisibilityState =
        visibilityState != taskVisibility.end();
    const TaskInternal::VisibilitySnapshot initialVisibility =
        hasVisibilityState
        ? visibilityState->second
        : TaskInternal::VisibilitySnapshot();

    // detach the task panel from the selection to avoid to invoke
    // eventually onAddSelection when the selection changes
    std::vector<QWidget*> subwidgets = getDialogContent();
    for (auto it : subwidgets) {
        TaskSketchBasedParameters* param = qobject_cast<TaskSketchBasedParameters*>(it);
        if (param) {
            param->detachSelection();
        }
    }

    // Tear down the edit view while its ViewProvider and feature are still
    // alive. Gui::Document::cancelEdit() calls finishEditing(), clears every
    // GUI edit pointer, and only then aborts the owning transaction. Aborting
    // first leaves the task, tree, and edit machinery holding deleted objects.
    auto* guiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    TaskInternal::cancelOwnedEdit(guiDocument);

    App::Document* restoredDocument = nullptr;
    try {
        restoredDocument =
            App::GetApplication().getDocument(documentName.c_str());
    }
    catch (...) {
    }
    if (!restoredDocument) {
        return true;
    }

    const bool featureSurvived =
        !featureName.empty()
        && restoredDocument->getObject(featureName.c_str());
    if (featureSurvived) {
        // Tree edits retain the feature, so transaction rollback cannot undo
        // every temporary ViewProvider change made by the task panel.
        if (hasVisibilityState) {
            initialVisibility.restore(restoredDocument);
        }
    }

    restoredDocument->recompute();

    return true;
}

#include "moc_TaskFeatureParameters.cpp"
