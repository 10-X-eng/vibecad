/***************************************************************************
 *   Copyright (c) 2014 Luke Parry <l.parry@warwick.ac.uk>                 *
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

# include <QMessageBox>
# include <algorithm>
# include <sstream>


#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawHatch.h>
#include <Mod/TechDraw/App/DrawGeomHatch.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewImage.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "DrawGuiUtil.h"
#include "FrameVisibilityBuilder.h"
#include "HatchBuilder.h"
#include "TaskGeomHatch.h"
#include "TaskHatch.h"
#include "TaskDocumentGuard.h"
#include "ViewProviderGeomHatch.h"
#include "ViewProviderPage.h"
#include "MDIViewPage.h"
#include "CommandHelpers.h"
#include "PreferencesGui.h"


using namespace TechDrawGui;
using namespace TechDraw;
using DU = DrawUtil;

//internal functions
bool _checkSelectionHatch(Gui::Command* cmd);

//===========================================================================
// TechDraw_ToggleFrame
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawToggleFrame)

CmdTechDrawToggleFrame::CmdTechDrawToggleFrame()
  : Command("TechDraw_ToggleFrame")
{
    sAppModule      = "TechDraw";
    sGroup          = QT_TR_NOOP("TechDraw");
    sMenuText       = QT_TR_NOOP("Toggle View Frames");
    sToolTipText    = QT_TR_NOOP("Toggles visibility of view frames and vertices");
    sWhatsThis      = "TechDraw_Toggle";
    sStatusTip      = sToolTipText;
    sPixmap         = "actions/TechDraw_ToggleFrame";
}

// This is a toggle.  Each press flips the fame state.
// Gui::Action *CmdTechDrawToggleFrame::createAction()
// {
//     Gui::Action *action = Gui::Command::createAction();
//     action->setCheckable(true);
//     action->setChecked(false);

//     return action;
// }

void CmdTechDrawToggleFrame::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    if (PreferencesGui::getViewFrameMode() != ViewFrameMode::Manual) {
        return;
    }

    auto mvp = dynamic_cast<MDIViewPage *>(Gui::getMainWindow()->activeWindow());
    if (!mvp) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("No TechDraw Page"),
            QObject::tr("Need a TechDraw Page for this command"));
        return;
    }

    ViewProviderPage* vpp = mvp->getViewProviderPage();
    if (!vpp) {
        return;
    }

    changeDrawingFrameVisibility(vpp, !vpp->getFrameState());

    // Gui::Action *action = this->getAction();
    // if (action) {
    //     action->setChecked(vpp->getFrameState());
    // }
}

bool CmdTechDrawToggleFrame::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView && PreferencesGui::getViewFrameMode() == ViewFrameMode::Manual);
}

//===========================================================================
// TechDraw_ToggleGrid
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawToggleGrid)

CmdTechDrawToggleGrid::CmdTechDrawToggleGrid()
  : Command("TechDraw_ToggleGrid")
{
    sAppModule   = "TechDraw";
    sGroup       = QT_TR_NOOP("TechDraw");
    sMenuText    = QT_TR_NOOP("Toggle Grid");
    sToolTipText = QT_TR_NOOP("Toggles the grid on the active page");
    sWhatsThis   = "TechDraw_ToggleGrid";
    sStatusTip   = sToolTipText;
}

void CmdTechDrawToggleGrid::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto mvp = dynamic_cast<MDIViewPage*>(Gui::getMainWindow()->activeWindow());
    if (!mvp) {
        return;
    }
    ViewProviderPage* vpp = mvp->getViewProviderPage();
    if (!vpp) {
        return;
    }
    const auto current = inspectDrawingGridVisibility(vpp);
    changeDrawingGridVisibility(vpp, !current.visible);
}

bool CmdTechDrawToggleGrid::isActive()
{
    return DrawGuiUtil::needPage(this);
}

//===========================================================================
// TechDraw_Hatch
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawHatch)

CmdTechDrawHatch::CmdTechDrawHatch()
  : Command("TechDraw_Hatch")
{
    sAppModule      = "TechDraw";
    sGroup          = QT_TR_NOOP("TechDraw");
    sMenuText       = QT_TR_NOOP("Image Hatch");
    sToolTipText    = QT_TR_NOOP("Applies a hatch pattern to the selected faces using an image file");
    sWhatsThis      = "TechDraw_Hatch";
    sStatusTip      = sToolTipText;
    sPixmap         = "actions/TechDraw_Hatch";
}

void CmdTechDrawHatch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!_checkSelectionHatch(this)) {
        return;
    }

    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();
    auto partFeat( dynamic_cast<TechDraw::DrawViewPart *>(selection[0].getObject()) );
    if (!partFeat) {
        return;
    }
    const std::vector<std::string> &subNames = selection[0].getSubNames();
    TechDraw::DrawPage* page = partFeat->findParentPage();
    if (!page || page->getDocument() != partFeat->getDocument()) {
        return;
    }
    auto* document = partFeat->getDocument();
    std::vector<int> selFaces;
    for (auto& s: subNames) {
        int f = TechDraw::DrawUtil::getIndexFromName(s);
        selFaces.push_back(f);
    }

    bool removeOld = false;
    std::vector<TechDraw::DrawHatch*> hatchObjs = partFeat->getActiveHatches();
    for (auto& s: subNames) {                             //all the faces selected in DVP
        int face = TechDraw::DrawUtil::getIndexFromName(s);
        if (TechDraw::DrawHatch::faceIsHatched(face, hatchObjs)) {
            QMessageBox::StandardButton rc =
                    QMessageBox::question(Gui::getMainWindow(), QObject::tr("Replace hatch?"),
                            QObject::tr("Some faces in the selection are already hatched. Replace?"));
            if (rc != QMessageBox::StandardButton::Yes) {
                return;
            }

            removeOld = true;
            break;
        }
    }

    // Replacing an existing hatch and creating its replacement are one user
    // operation. Keep one exact transaction open for the task panel so Cancel
    // restores the old hatch and removes any provisional replacement.
    openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create or replace hatch")
    );
    if (removeOld) {
        for (auto* hatch : hatchObjs) {
            if (!hatch || hatch->getDocument() != document) {
                continue;
            }
            std::vector<std::string> hatchSubs =
                hatch->Source.getSubValues();
            for (const auto& hs : hatchSubs) {
                int hatchFace = TechDraw::DrawUtil::getIndexFromName(hs);
                if (auto it = std::ranges::find(selFaces, hatchFace); it != selFaces.end()) {
                    hatch->removeSub(hatchFace);
                }
            }
            if (hatch->empty()) {
                doCommand(
                    Doc,
                    "App.getDocument('%s').removeObject('%s')",
                    document->getName(),
                    hatch->getNameInDocument()
                );
            }
        }
    }

    // dialog to fill in hatch values
    TaskInternal::showDocumentDialog(
        new TaskDlgHatch(partFeat, subNames),
        partFeat->getDocument()
    );

    // Touch the parent feature so the hatching in tree view appears as a child
    partFeat->touch();
    document->recompute();
}

bool CmdTechDrawHatch::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_GeometricHatch
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawGeometricHatch)

CmdTechDrawGeometricHatch::CmdTechDrawGeometricHatch()
  : Command("TechDraw_GeometricHatch")
{
    sAppModule      = "TechDraw";
    sGroup          = QT_TR_NOOP("TechDraw");
    sMenuText       = QT_TR_NOOP("Geometric Hatch");
    sToolTipText    = QT_TR_NOOP("Applies a geometric hatch pattern to the selected faces");
    sWhatsThis      = "TechDraw_GeometricHatch";
    sStatusTip      = sToolTipText;
    sPixmap         = "actions/TechDraw_GeometricHatch";
}

void CmdTechDrawGeometricHatch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!_checkSelectionHatch(this)) {                 //same requirements as hatch - page, DrawViewXXX, face
        return;
    }

    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();
    auto objFeat( dynamic_cast<TechDraw::DrawViewPart *>(selection[0].getObject()) );
    if (!objFeat) {
        return;
    }
    const std::vector<std::string> &subNames = selection[0].getSubNames();
    TechDraw::DrawPage* page = objFeat->findParentPage();
    if (!page || page->getDocument() != objFeat->getDocument()) {
        return;
    }
    auto* document = objFeat->getDocument();

    openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create GeomHatch")
    );
    const auto defaults = drawingHatchDefaults();
    const DrawingGeometricHatchStyle style {
        1.0,
        0.0,
        Base::Vector3d(),
        defaults.geometricLineWidthMm,
        defaults.geometricColor};
    auto* geomhatch = createDrawingGeometricHatch(
        objFeat,
        subNames,
        defaults.geometricPatternFile,
        defaults.geometricPatternName,
        style);
    document->publishProvisionalTimelineOperationBlock(geomhatch, {}, {});
    Gui::ViewProvider* vp =
        Gui::Application::Instance->getDocument(document)
            ->getViewProvider(geomhatch);
    TechDrawGui::ViewProviderGeomHatch* hvp = dynamic_cast<TechDrawGui::ViewProviderGeomHatch*>(vp);
    if (!hvp) {
        throw Base::RuntimeError(
            "The geometric hatch has no compatible view provider"
        );
    }

    // dialog to fill in hatch values
    TaskInternal::showDocumentDialog(
        new TaskDlgGeomHatch(geomhatch, hvp, true),
        geomhatch->getDocument()
    );

    // Touch the parent feature so the hatching in tree view appears as a child
    objFeat->touch();
    document->recompute();
}

bool CmdTechDrawGeometricHatch::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_Image
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawImage)

CmdTechDrawImage::CmdTechDrawImage()
  : Command("TechDraw_Image")
{
    // setting the Gui eye-candy
    sGroup        = QT_TR_NOOP("TechDraw");
    sMenuText     = QT_TR_NOOP("Bitmap Image");
    sToolTipText  = QT_TR_NOOP("Inserts a bitmap from a file into the current page");
    sWhatsThis    = "TechDraw_Image";
    sStatusTip    = QT_TR_NOOP("Insert bitmap from a file into a page");
    sPixmap       = "actions/TechDraw_Image";
}

void CmdTechDrawImage::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    if (!page) {
        return;
    }
    TaskInternal::ObjectIdentity<TechDraw::DrawPage>
        pageIdentity(page);
    App::Document* document = page->getDocument();
    auto* baseView = CommandHelpers::firstViewInSelection(this);
    const bool hadBaseView = baseView != nullptr;
    TaskInternal::ObjectIdentity<TechDraw::DrawView>
        baseViewIdentity(baseView);
    if (baseView
        && (baseView->getDocument() != document
            || baseView->findParentPage() != page)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr(
                "The image owner must belong to the active drawing page."
            )
        );
        return;
    }

    // Reading an image
    const Gui::FileDialog::FilterList filterList {
        {QObject::tr("Image files"), {"*.jpg", "*.jpeg", "*.png", "*.bmp"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    QString fileName = Gui::FileDialog::getOpenFileName(Gui::getMainWindow(),
        QObject::tr("Select an image file"),
        Preferences::defaultSymbolDir(),
        filterList);
    if (fileName.isEmpty()) {
        return;
    }

    page = pageIdentity.resolve();
    baseView = baseViewIdentity.resolve();
    if (!page || page->getDocument() != document
        || (hadBaseView && !baseView)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create image"),
            QObject::tr("The target drawing page or owner was closed.")
        );
        return;
    }

    const std::string filespec = DU::cleanFilespecBackslash(
        Base::Tools::escapeEncodeFilename(fileName.toStdString())
    );
    const std::string pythonFilespec =
        Base::InterpreterSingleton::strToPython(filespec);
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Create Image")
        );
        const std::string featureName =
            document->getUniqueObjectName("Image");
        const std::string documentName =
            Base::InterpreterSingleton::strToPython(
                document->getName()
            );
        const QString imageFactory =
            QStringLiteral(
                "App.getDocument('%1').addObject"
                "('TechDraw::DrawViewImage', '%2')"
            )
                .arg(
                    QString::fromStdString(documentName),
                    QString::fromStdString(featureName)
                );
        auto* image =
            dynamic_cast<TechDraw::DrawViewImage*>(
                Gui::Command::runDocumentObjectCommand(
                    Doc,
                    *document,
                    imageFactory.toUtf8(),
                    TechDraw::DrawViewImage::getClassTypeId()
                )
            );
        if (!image) {
            throw Base::RuntimeError(
                "The drawing image could not be created"
            );
        }
        image->translateLabel(
            "DrawViewImage",
            "Image",
            image->getNameInDocument()
        );
        const std::string imageCommand =
            Gui::Command::getObjectCmd(image);
        const std::string pageCommand =
            Gui::Command::getObjectCmd(page);
        doCommand(
            Doc,
            "%s.ImageFile = '%s'",
            imageCommand.c_str(),
            pythonFilespec.c_str()
        );
        if (baseView) {
            const std::string ownerCommand =
                Gui::Command::getObjectCmd(baseView);
            doCommand(
                Doc,
                "%s.Owner = %s",
                imageCommand.c_str(),
                ownerCommand.c_str()
            );
        }
        doCommand(
            Doc,
            "%s.addView(%s)",
            pageCommand.c_str(),
            imageCommand.c_str()
        );
        image->recomputeFeature();
        if (image->isError()) {
            throw Base::RuntimeError(
                "The drawing image could not be generated"
            );
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Create image"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawImage::isActive()
{
    return DrawGuiUtil::needPage(this);
}

void CreateTechDrawCommandsDecorate()
{
    Gui::CommandManager &rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdTechDrawHatch());
    rcCmdMgr.addCommand(new CmdTechDrawGeometricHatch());
    rcCmdMgr.addCommand(new CmdTechDrawImage());
    rcCmdMgr.addCommand(new CmdTechDrawToggleFrame());
    rcCmdMgr.addCommand(new CmdTechDrawToggleGrid());

//    rcCmdMgr.addCommand(new CmdTechDrawLeaderLine());
//    rcCmdMgr.addCommand(new CmdTechDrawRichTextAnnotation());
}

//===========================================================================
// Selection Validation Helpers
//===========================================================================

bool _checkSelectionHatch(Gui::Command* cmd) {
    if (!cmd || !cmd->getDocument()) {
        return false;
    }
    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    if (selection.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr("Select a face first"));
        return false;
    }

    TechDraw::DrawViewPart * objFeat = dynamic_cast<TechDraw::DrawViewPart *>(selection[0].getObject());
    if(!objFeat || objFeat->getDocument() != cmd->getDocument()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
                             QObject::tr(
                                 "Select a drawing view in the current document"
                             ));
        return false;
    }

    auto* page = objFeat->findParentPage();
    if (!page || page->getDocument() != cmd->getDocument()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
            QObject::tr("The selected view is not on a drawing page"));
        return false;
    }

    const std::vector<std::string> &SubNames = selection[0].getSubNames();
    if (SubNames.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
        QObject::tr("No faces to hatch in this selection"));
        return false;
    }
    const bool allFaces = std::ranges::all_of(
        SubNames,
        [](const std::string& subName) {
            return TechDraw::DrawUtil::getGeomTypeFromName(subName)
                == "Face";
        }
    );
    if (!allFaces) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Incorrect Selection"),
        QObject::tr("No faces to hatch in this selection"));
        return false;
    }

    return true;
}
