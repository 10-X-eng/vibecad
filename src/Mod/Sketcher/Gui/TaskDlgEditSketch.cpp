// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2009 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <exception>
#include <QMessageBox>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Mod/Sketcher/App/SketchObject.h>

#include "TaskDlgEditSketch.h"
#include "ViewProviderSketch.h"


using namespace SketcherGui;

namespace sp = std::placeholders;

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgEditSketch::TaskDlgEditSketch(ViewProviderSketch* sketchView)
    : TaskDialog()
    , sketchView(sketchView)
    , exactDocument(
          sketchView ? sketchView->getObject()->getDocument() : nullptr
      )
    , exactDocumentName(
          exactDocument ? exactDocument->getName() : std::string()
      )
    , exactDocumentUid(
          exactDocument
              ? exactDocument->Uid.getValueStr()
              : std::string()
      )
    , exactSketchId(
          sketchView ? sketchView->getObject()->getID() : 0
      )
    , exactSketchName(
          sketchView && sketchView->getObject()->getNameInDocument()
              ? sketchView->getObject()->getNameInDocument()
              : std::string()
      )
{
    assert(sketchView);
    roleOnEscape = QDialogButtonBox::ButtonRole::AcceptRole;

    ToolSettings = new TaskSketcherTool(sketchView);
    Constraints = new TaskSketcherConstraints(sketchView);
    Elements = new TaskSketcherElements(sketchView);
    Messages = new TaskSketcherMessages(sketchView);
    SolverAdvanced = new TaskSketcherSolverAdvanced(sketchView);

    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Sketcher"
    );
    setEscapeButtonEnabled(hGrp->GetBool("LeaveSketchWithEscape", true));
    setAutoCloseOnResetEdit(true);

    Content.push_back(Messages);
    Content.push_back(ToolSettings);

    if (hGrp->GetBool("ShowSolverAdvancedWidget", false)) {
        Content.push_back(SolverAdvanced);
    }

    Content.push_back(Constraints);
    Content.push_back(Elements);

    if (!hGrp->GetBool("ExpandedMessagesWidget", true)) {
        Messages->hideGroupBox();
    }
    if (!hGrp->GetBool("ExpandedSolverAdvancedWidget", false)) {
        SolverAdvanced->hideGroupBox();
    }
    if (!hGrp->GetBool("ExpandedConstraintsWidget", true)) {
        Constraints->hideGroupBox();
    }
    if (!hGrp->GetBool("ExpandedElementsWidget", true)) {
        Elements->hideGroupBox();
    }

    connectionToolSettings = sketchView->registerToolChanged(
        std::bind(&SketcherGui::TaskDlgEditSketch::slotToolChanged, this, sp::_1)
    );

    ToolSettings->setHidden(true);

    associateToObject3dView(sketchView->getObject());
}

TaskDlgEditSketch::~TaskDlgEditSketch()
{
    // to make sure to delete the advanced solver panel
    // it must be part to the 'Content' array
    if (const auto it = std::ranges::find(Content, SolverAdvanced); it == Content.end()) {
        Content.push_back(SolverAdvanced);
    }

    connectionToolSettings.disconnect();
}

void TaskDlgEditSketch::slotToolChanged(const std::string& toolname)
{
    auto* exactView = resolveExactSketchView();
    if (!exactView) {
        return;
    }
    bool widgetvisible = false;

    if (toolname != "DSH_None") {
        widgetvisible = exactView->toolManager.isWidgetVisible();

        ToolSettings->toolChanged(toolname);
    }

    ToolSettings->setHidden(!widgetvisible);
}

//==== calls from the TaskView ===============================================================


void TaskDlgEditSketch::open()
{}

void TaskDlgEditSketch::closed()
{}

void TaskDlgEditSketch::clicked(int)
{}

Gui::Document* TaskDlgEditSketch::resolveExactGuiDocument() const
{
    if (!exactDocument || exactDocumentName.empty()
        || exactDocumentUid.empty()) {
        return nullptr;
    }
    auto* document =
        App::GetApplication().getDocument(exactDocumentName.c_str());
    if (document != exactDocument
        || document->Uid.getValueStr() != exactDocumentUid) {
        return nullptr;
    }
    return Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
}

ViewProviderSketch* TaskDlgEditSketch::resolveExactSketchView() const
{
    auto* guiDocument = resolveExactGuiDocument();
    auto* document =
        guiDocument ? guiDocument->getDocument() : nullptr;
    auto* object =
        document && exactSketchId > 0
        ? document->getObjectByID(exactSketchId)
        : nullptr;
    if (!object || !object->getNameInDocument()
        || exactSketchName != object->getNameInDocument()
        || document->getObject(exactSketchName.c_str()) != object) {
        return nullptr;
    }
    auto* view = dynamic_cast<ViewProviderSketch*>(
        Gui::Application::Instance->getViewProvider(object)
    );
    return view == sketchView ? view : nullptr;
}

bool TaskDlgEditSketch::reject()
{
    auto* guiDocument = resolveExactGuiDocument();
    auto* view = resolveExactSketchView();
    if (!guiDocument || !view
        || guiDocument->getInEdit() != view) {
        return false;
    }

    App::Document* documentAddress = exactDocument;
    const std::string documentName = exactDocumentName;
    const std::string documentUid = exactDocumentUid;
    const long sketchId = exactSketchId;
    const std::string sketchName = exactSketchName;
    view->editingCancelled = true;
    guiDocument->cancelEdit();

    // Cancel can abort the transaction that created this sketch, so resolve
    // the exact original identity instead of accepting a same-name
    // replacement after rollback.
    auto* restoredDocument =
        App::GetApplication().getDocument(documentName.c_str());
    if (restoredDocument == documentAddress
        && restoredDocument->Uid.getValueStr() == documentUid) {
        auto* sketch = restoredDocument->getObjectByID(sketchId);
        if (sketch && sketch->getNameInDocument()
            && sketchName == sketch->getNameInDocument()
            && restoredDocument->getObject(sketchName.c_str())
                == sketch) {
            if (auto* restored =
                    dynamic_cast<ViewProviderSketch*>(
                        Gui::Application::Instance->getViewProvider(
                            sketch
                        )
                    )) {
                restored->editingCancelled = false;
            }
        }
    }

    return true;
}

bool TaskDlgEditSketch::accept()
{
    auto* guiDocument = resolveExactGuiDocument();
    auto* view = resolveExactSketchView();
    if (!guiDocument || !view
        || guiDocument->getInEdit() != view) {
        return false;
    }

    auto* sketch = view->getSketchObject();
    auto* appDocument =
        sketch ? sketch->getDocument() : nullptr;
    if (sketch && appDocument
        && sketch->isDesignScopeDefinition()) {
        try {
            sketch->finalizeDesignDefinition();
        }
        catch (const Base::Exception& error) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                tr("Cannot save Sketch"),
                QCoreApplication::translate(
                    "Exception",
                    error.what()
                )
            );
            return false;
        }
        catch (const std::exception& error) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                tr("Cannot save Sketch"),
                QString::fromUtf8(error.what())
            );
            return false;
        }
        catch (...) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                tr("Cannot save Sketch"),
                tr(
                    "An unexpected error prevented the Sketch from being "
                    "saved to global History."
                )
            );
            return false;
        }
    }

    // resetEdit() deletes this task. Keep only value copies and verify the
    // exact document before issuing each command.
    App::Document* documentAddress = exactDocument;
    const std::string document = exactDocumentName;
    const std::string documentUid = exactDocumentUid;
    Gui::Command::doCommand(
        Gui::Command::Gui,
        "Gui.getDocument('%s').resetEdit()",
        document.c_str()
    );
    auto* recomputeDocument =
        App::GetApplication().getDocument(document.c_str());
    if (recomputeDocument == documentAddress
        && recomputeDocument->Uid.getValueStr() == documentUid) {
        Gui::Command::doCommand(
            Gui::Command::Doc,
            "App.getDocument('%s').recompute()",
            document.c_str()
        );
    }

    return true;
}

void TaskDlgEditSketch::saveDialogState() const
{
    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Sketcher"
    );
    hGrp->SetBool("ExpandedMessagesWidget", Messages->isGroupVisible());
    hGrp->SetBool("ExpandedSolverAdvancedWidget", SolverAdvanced->isGroupVisible());
    hGrp->SetBool("ExpandedConstraintsWidget", Constraints->isGroupVisible());
    hGrp->SetBool("ExpandedElementsWidget", Elements->isGroupVisible());
}

QDialogButtonBox::StandardButtons TaskDlgEditSketch::getStandardButtons() const
{
    return QDialogButtonBox::Ok | QDialogButtonBox::Cancel;
}

void TaskDlgEditSketch::autoClosedOnResetEdit()
{
    saveDialogState();
}

void TaskDlgEditSketch::autoClosedOnClosedView()
{
    // Make sure the edit mode is exited when the view is closed.
    reject();
}

#include "moc_TaskDlgEditSketch.cpp"
