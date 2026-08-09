// SPDX-License-Identifier: LGPL-2.1-or-later

#include "SketchEditControl.h"

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/PyObjectBase.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Mod/Sketcher/App/SketchObject.h>

#include "TaskDlgEditSketch.h"
#include "ViewProviderSketch.h"

namespace
{

struct ExactEditState
{
    App::Document* document = nullptr;
    Gui::Document* guiDocument = nullptr;
    SketcherGui::ViewProviderSketch* view = nullptr;
    Sketcher::SketchObject* sketch = nullptr;
    long sketchId = 0;
};

ExactEditState requireExactEditState(
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName
)
{
    auto* application = Gui::Application::Instance;
    auto* document = App::GetApplication().getDocument(documentName.c_str());
    if (!application || !document || document->Uid.getValueStr() != documentUid) {
        throw Base::RuntimeError("The exact Sketch document is no longer open");
    }

    auto* guiDocument = application->getDocument(document);
    if (!guiDocument || application->activeDocument() != guiDocument
        || application->editDocument() != guiDocument) {
        throw Base::RuntimeError(
            "The exact Sketch document is no longer active in edit mode"
        );
    }

    auto* view = dynamic_cast<SketcherGui::ViewProviderSketch*>(
        guiDocument->getInEdit()
    );
    auto* sketch = view ? view->getSketchObject() : nullptr;
    if (!view || !sketch || sketch->getDocument() != document
        || !sketch->getNameInDocument()
        || sketchName != sketch->getNameInDocument()
        || document->getObject(sketchName.c_str()) != sketch) {
        throw Base::RuntimeError(
            "The exact requested Sketch is no longer the active edit target"
        );
    }

    return {document, guiDocument, view, sketch, sketch->getID()};
}

void requireExactFinishedState(
    const ExactEditState& before,
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName
)
{
    auto* application = Gui::Application::Instance;
    auto* document = App::GetApplication().getDocument(documentName.c_str());
    auto* sketch = document
        ? freecad_cast<Sketcher::SketchObject*>(
              document->getObjectByID(before.sketchId)
          )
        : nullptr;
    if (!application || document != before.document
        || document->Uid.getValueStr() != documentUid
        || application->getDocument(document) != before.guiDocument
        || !sketch || sketch != before.sketch
        || !sketch->getNameInDocument()
        || sketchName != sketch->getNameInDocument()
        || document->getObject(sketchName.c_str()) != sketch) {
        throw Base::RuntimeError(
            "The exact Sketch identity changed while leaving edit mode"
        );
    }
    if (Gui::Control().activeDialog(document)
        || before.guiDocument->getInEdit() != nullptr
        || application->editDocument() == before.guiDocument) {
        throw Base::RuntimeError("The exact Sketch edit session did not close");
    }
}

}  // namespace

namespace SketcherGui
{

LeaveSketchResult leaveActiveSketch(
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName,
    bool recordFallbackCommand
)
{
    Gui::requireMainThread("SketcherGui.leaveActiveSketch");
    const auto exact = requireExactEditState(
        documentName,
        documentUid,
        sketchName
    );

    if (exact.view->getSketchMode() != ViewProviderSketch::STATUS_NONE) {
        exact.view->purgeHandler();
    }
    if (exact.guiDocument->getInEdit() != exact.view
        || exact.view->getSketchMode() != ViewProviderSketch::STATUS_NONE) {
        throw Base::RuntimeError(
            "The active Sketch operation could not be stopped before leaving"
        );
    }

    bool acceptedTaskDialog = false;
    if (auto* dialog = Gui::Control().activeDialog(exact.document)) {
        auto* sketchDialog = dynamic_cast<TaskDlgEditSketch*>(dialog);
        if (!sketchDialog || sketchDialog->getSketchView() != exact.view) {
            throw Base::RuntimeError(
                "The active task does not own the exact requested Sketch"
            );
        }
        acceptedTaskDialog = true;
        Gui::Control().accept(exact.document);
    }
    else if (recordFallbackCommand) {
        Gui::Command::doCommand(
            Gui::Command::Gui,
            "Gui.getDocument('%s').resetEdit()",
            documentName.c_str()
        );
        if (App::GetApplication().getDocument(documentName.c_str())
            == exact.document
            && exact.document->Uid.getValueStr() == documentUid) {
            Gui::Command::doCommand(
                Gui::Command::Doc,
                "App.getDocument('%s').recompute()",
                documentName.c_str()
            );
        }
    }
    else {
        exact.guiDocument->resetEdit();
        if (App::GetApplication().getDocument(documentName.c_str())
            == exact.document
            && exact.document->Uid.getValueStr() == documentUid) {
            exact.document->recompute();
        }
    }

    requireExactFinishedState(
        exact,
        documentName,
        documentUid,
        sketchName
    );
    return {
        documentName,
        documentUid,
        sketchName,
        acceptedTaskDialog,
    };
}

bool setActiveSketchSectionView(
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName,
    bool visible
)
{
    Gui::requireMainThread("SketcherGui.setActiveSketchSectionView");
    const auto exact = requireExactEditState(
        documentName,
        documentUid,
        sketchName
    );
    if (exact.view->getSketchMode() != ViewProviderSketch::STATUS_NONE) {
        throw Base::RuntimeError(
            "Finish the active Sketch operation before changing section view"
        );
    }

    Py::Object tempoVis = exact.view->TempoVis.getValue();
    if (tempoVis.isNone()) {
        throw Base::RuntimeError(
            "The exact active Sketch has no visibility controller"
        );
    }
    tempoVis.callMemberFunction(
        "sketchClipPlane",
        Py::TupleN(
            Py::Object(exact.sketch->getPyObject(), true),
            Py::Object(exact.guiDocument->getPyObject(), true),
            Py::Boolean(visible),
            Py::Boolean(exact.view->getViewOrientationFactor() < 0)
        )
    );
    if (exact.view->SectionView.getValue() != visible) {
        throw Base::RuntimeError(
            "The exact active Sketch did not reach the requested section view"
        );
    }
    return exact.view->SectionView.getValue();
}

}  // namespace SketcherGui
