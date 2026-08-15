/***************************************************************************
 *   Copyright (c) 2021 edi                                                *
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
#include <exception>
#include <utility>


#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Base/Type.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/ViewProvider.h>
#include <Mod/Part/App/Geometry2d.h>
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawProjGroup.h>
#include <Mod/TechDraw/App/DrawProjGroupItem.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/DrawViewSection.h>
#include <Mod/TechDraw/App/Preferences.h>
#include <Mod/TechDraw/App/LineFormat.h>
#include <Mod/TechDraw/App/LineGenerator.h>
#include <Mod/TechDraw/App/LineGroup.h>

#include "BalloonBuilder.h"
#include "CircleCenterLineBuilder.h"
#include "CosmeticCurveBuilder.h"
#include "CosmeticLineBuilder.h"
#include "CosmeticVertexBuilder.h"
#include "DrawGuiUtil.h"
#include "LineAttributeBuilder.h"
#include "LineLengthBuilder.h"
#include "TaskCosmeticCircle.h"
#include "TaskDocumentGuard.h"
#include "TaskSelectLineAttributes.h"
#include "ThreadRepresentationBuilder.h"
#include "ViewLockBuilder.h"


using namespace TechDrawGui;
using namespace TechDraw;
using DU = DrawUtil;


namespace TechDrawGui
{
//TechDraw::LineFormat activeAttributes; // container holding global line attributes

//internal helper functions
TechDraw::LineFormat& _getActiveLineAttributes();
Base::Vector3d _circleCenter(Base::Vector3d p1, Base::Vector3d p2, Base::Vector3d p3);
bool _createThreadCircle(
    const std::string& name,
    TechDraw::DrawViewPart* objFeat,
    DrawingThreadRepresentationKind kind
);
void _setLineAttributes(TechDraw::CosmeticEdge* cosEdge);
void _setLineAttributes(TechDraw::CenterLine* cosEdge);
void _setLineAttributes(TechDraw::CosmeticEdge* cosEdge, int style, float weight, Base::Color color);
void _setLineAttributes(TechDraw::CenterLine* cosEdge, int style, float weight, Base::Color color);
double _getAngle(Base::Vector3d center, Base::Vector3d point);
std::vector<Base::Vector3d> _getVertexPoints(const std::vector<std::string>& SubNames,
                                             TechDraw::DrawViewPart* objFeat);
bool _checkSel(Gui::Command* cmd, std::vector<Gui::SelectionObject>& selection,
               TechDraw::DrawViewPart*& objFeat, const std::string& message);

template<typename ObjectType>
ObjectType* _resolveSelectedObject(
    const Gui::SelectionObject& selected)
{
    const auto* selectedObject = selected.getObject();
    App::Document* document = selectedObject
        ? selectedObject->getDocument()
        : nullptr;
    auto* liveObject = document && selectedObject
        ? document->getObjectByID(selectedObject->getID())
        : nullptr;
    return liveObject && liveObject == selectedObject
            && document->containsObject(liveObject)
        ? dynamic_cast<ObjectType*>(liveObject)
        : nullptr;
}

template<typename Operation>
bool _runExactExtensionCommand(
    Gui::Command* cmd,
    const std::string& transactionName,
    const QString& title,
    Operation&& operation)
{
    try {
        App::Document* document = cmd ? cmd->getDocument() : nullptr;
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            transactionName
        );
        if (!std::forward<Operation>(operation)()) {
            return false;
        }
        TaskInternal::updateExactDocument(document);
        transaction.commit();
        return true;
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            title,
            QString::fromUtf8(error.what())
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            title,
            QString::fromUtf8(error.what())
        );
    }
    return false;
}

//===========================================================================
// TechDraw_ExtensionHoleCircle
//===========================================================================

void execHoleCircle(Gui::Command* cmd)
{
    //create centerlines of a hole/bolt circle
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw hole circle"))) {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    std::vector<std::string> circleNames;
    for (const std::string& Name : SubNames) {
        int GeoId = TechDraw::DrawUtil::getIndexFromName(Name);
        std::string GeoType = TechDraw::DrawUtil::getGeomTypeFromName(Name);
        TechDraw::BaseGeomPtr geom = objFeat->getGeomByIndex(GeoId);
        if (GeoType == "Edge" && geom) {
            if (geom->getGeomType() == GeomType::CIRCLE || geom->getGeomType() == GeomType::ARCOFCIRCLE) {
                circleNames.push_back(Name);
            } else {
                QMessageBox::warning(Gui::getMainWindow(), QObject::tr("TechDraw hole circle"),
                                     QObject::tr("Can not make hole circle for %1")
                                         .arg(QString::fromStdString(GeometryUtils::getGeomTypeName(geom->getGeomType()))));

            }
        }
    }
    if (circleNames.size() <= 2) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("TechDraw hole circle"),
                             QObject::tr("Fewer than three circles selected"));
        return;
    }
    const double scale = objFeat->getScale();
    if (std::abs(scale) <= Base::Vector3d::epsilon()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Bolt Circle Centerlines"),
            QObject::tr("The drawing view has an invalid scale")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Bolt circle centerlines"),
        QObject::tr("Bolt Circle Centerlines"),
        [&]() {
            createDrawingBoltCircleCenterLines(objFeat, circleNames);
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}
}// namespace TechDrawGui

DEF_STD_CMD_A(CmdTechDrawExtensionHoleCircle)

CmdTechDrawExtensionHoleCircle::CmdTechDrawExtensionHoleCircle()
    : Command("TechDraw_ExtensionHoleCircle")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Bolt Circle Centerlines");
    sToolTipText = QT_TR_NOOP("Adds centerlines to a circular pattern of three or more selected circles");
    sWhatsThis = "TechDraw_ExtensionHoleCircle";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionHoleCircle";
}

void CmdTechDrawExtensionHoleCircle::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execHoleCircle(this);
    //Base::Console().message("HoleCircle started\n");
}

bool CmdTechDrawExtensionHoleCircle::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionCircleCenterLines
//===========================================================================

void execCircleCenterLines(Gui::Command* cmd)
{
    // create circle centerlines
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw circle centerlines"))) {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    std::vector<std::string> circleNames;
    for (const std::string& Name : SubNames) {
        int GeoId = TechDraw::DrawUtil::getIndexFromName(Name);
        TechDraw::BaseGeomPtr geom = objFeat->getGeomByIndex(GeoId);
        std::string GeoType = TechDraw::DrawUtil::getGeomTypeFromName(Name);
        if (GeoType == "Edge" && geom) {
            if (geom->getGeomType() == GeomType::CIRCLE || geom->getGeomType() == GeomType::ARCOFCIRCLE) {
                circleNames.push_back(Name);
            } else {
                QMessageBox::warning(Gui::getMainWindow(), QObject::tr("TechDraw circle centerlines"),
                                     QObject::tr("Can not make centerlines for %1")
                                        .arg(QString::fromStdString(GeometryUtils::getGeomTypeName(geom->getGeomType()))));
            }
        }
    }
    if (circleNames.empty()) {
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Circle Centerlines"),
        QObject::tr("Circle Centerlines"),
        [&]() {
            return !createDrawingCircleCenterLines(
                        objFeat,
                        circleNames)
                        .empty();
        }
    );
    if (created) {
        Gui::Selection().clearCompleteSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionCircleCenterLines)

CmdTechDrawExtensionCircleCenterLines::CmdTechDrawExtensionCircleCenterLines()
    : Command("TechDraw_ExtensionCircleCenterLines")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Circle Centerlines");
    sToolTipText = QT_TR_NOOP("Adds centerlines to the selected circles and arcs");
    sWhatsThis = "TechDraw_ExtensionCircleCenterLines";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionCircleCenterLines";
}

void CmdTechDrawExtensionCircleCenterLines::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execCircleCenterLines(this);
}

bool CmdTechDrawExtensionCircleCenterLines::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionCircleCenterLinesGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawExtensionCircleCenterLinesGroup)

CmdTechDrawExtensionCircleCenterLinesGroup::CmdTechDrawExtensionCircleCenterLinesGroup()
    : Command("TechDraw_ExtensionCircleCenterLinesGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Circle Centerlines");
    sToolTipText = QT_TR_NOOP("Adds centerlines to selected circles and arcs");
    sWhatsThis = "TechDraw_ExtensionCircleCenterLinesGroup";
    sStatusTip = sMenuText;
}

void CmdTechDrawExtensionCircleCenterLinesGroup::activated(int iMsg)
{
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again."));
        return;
    }

    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0://circle centerlines
            execCircleCenterLines(this);
            break;
        case 1://bolt circle centerlines
            execHoleCircle(this);
            break;
        default:
            Base::Console().message("CMD::CVGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawExtensionCircleCenterLinesGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionCircleCenterLines"));
    p1->setObjectName(QStringLiteral("TechDraw_ExtensionCircleCenterLines"));
    p1->setWhatsThis(QStringLiteral("TechDraw_ExtensionCircleCenterLines"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionHoleCircle"));
    p2->setObjectName(QStringLiteral("TechDraw_ExtensionHoleCircle"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ExtensionHoleCircle"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawExtensionCircleCenterLinesGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> action = pcAction->actions();

    QAction* arc1 = action[0];
    arc1->setText(
        QApplication::translate("CmdTechDrawExtensionCircleCenterLines", "Circle Centerlines"));
    arc1->setToolTip(QApplication::translate("CmdTechDrawExtensionCircleCenterLines",
                                             "Adds centerlines to selected circles and arcs:"));
    arc1->setStatusTip(arc1->text());
    QAction* arc2 = action[1];
    arc2->setText(
        QApplication::translate("CmdTechDrawExtensionHoleCircle", "Bolt Circle Centerlines"));
    arc2->setToolTip(QApplication::translate("CmdTechDrawExtensionHoleCircle",
                                             "Adds centerlines to a circular pattern of selected circles"));
    arc2->setStatusTip(arc2->text());
}

bool CmdTechDrawExtensionCircleCenterLinesGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionThreadHoleSide
//===========================================================================

void execThreadHoleSide(Gui::Command* cmd)
{
    // add cosmetic thread to side view of hole
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Thread Hole Side"))) {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.size() < 2) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Thread Hole Side View"),
            QObject::tr("Select two straight, parallel edges")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic Thread Hole Side"),
        QObject::tr("Cosmetic Thread Hole Side View"),
        [&]() {
            createDrawingThreadSide(
                objFeat,
                DrawingThreadRepresentationKind::HoleSide,
                {SubNames.at(0), SubNames.at(1)});
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionThreadHoleSide)

CmdTechDrawExtensionThreadHoleSide::CmdTechDrawExtensionThreadHoleSide()
    : Command("TechDraw_ExtensionThreadHoleSide")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Thread Hole Side View");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic thread to the side view of a hole or circle");
    sWhatsThis = "TechDraw_ExtensionThreadHoleSide";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionThreadHoleSide";
}

void CmdTechDrawExtensionThreadHoleSide::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execThreadHoleSide(this);
}

bool CmdTechDrawExtensionThreadHoleSide::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionThreadBoltSide
//===========================================================================

void execThreadBoltSide(Gui::Command* cmd)
{
    // add cosmetic thread to side view of bolt
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Thread Bolt Side")))  {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.size() < 2) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Thread Bolt Side View"),
            QObject::tr("Select two straight, parallel edges")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic Thread Bolt Side"),
        QObject::tr("Cosmetic Thread Bolt Side View"),
        [&]() {
            createDrawingThreadSide(
                objFeat,
                DrawingThreadRepresentationKind::BoltSide,
                {SubNames.at(0), SubNames.at(1)});
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionThreadBoltSide)

CmdTechDrawExtensionThreadBoltSide::CmdTechDrawExtensionThreadBoltSide()
    : Command("TechDraw_ExtensionThreadBoltSide")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Thread Bolt Side View");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic thread to the side view of a "
            "bolt/screw/rod between two selected parallel lines");
    sWhatsThis = "TechDraw_ExtensionThreadBoltSide";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionThreadBoltSide";
}

void CmdTechDrawExtensionThreadBoltSide::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execThreadBoltSide(this);
}

bool CmdTechDrawExtensionThreadBoltSide::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionThreadHoleBottom
//===========================================================================

void execThreadHoleBottom(Gui::Command* cmd)
{
    // add cosmetic thread to bottom view of hole
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Thread Hole Bottom"))) {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Thread Hole Bottom View"),
            QObject::tr("Select at least one circular edge")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic thread hole bottom"),
        QObject::tr("Cosmetic Thread Hole Bottom View"),
        [&]() {
            bool madeAny = false;
            for (const std::string& Name : SubNames) {
                madeAny =
                    _createThreadCircle(
                        Name,
                        objFeat,
                        DrawingThreadRepresentationKind::HoleBottom)
                    || madeAny;
            }
            if (madeAny) {
                objFeat->refreshCEGeoms();
                objFeat->requestPaint();
            }
            return madeAny;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionThreadHoleBottom)

CmdTechDrawExtensionThreadHoleBottom::CmdTechDrawExtensionThreadHoleBottom()
    : Command("TechDraw_ExtensionThreadHoleBottom")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Thread Hole Bottom View");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic thread to the top or bottom view of selected holes or circles");
    sWhatsThis = "TechDraw_ExtensionThreadHoleBottom";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionThreadHoleBottom";
}

void CmdTechDrawExtensionThreadHoleBottom::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execThreadHoleBottom(this);
}

bool CmdTechDrawExtensionThreadHoleBottom::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionThreadBoltBottom
//===========================================================================

void execThreadBoltBottom(Gui::Command* cmd)
{
    // add cosmetic thread to bottom view of bolt
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Thread Bolt Bottom")))  {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Thread Bolt Bottom View"),
            QObject::tr("Select at least one circular edge")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic Thread Bolt Bottom"),
        QObject::tr("Cosmetic Thread Bolt Bottom View"),
        [&]() {
            bool madeAny = false;
            for (const std::string& Name : SubNames) {
                madeAny =
                    _createThreadCircle(
                        Name,
                        objFeat,
                        DrawingThreadRepresentationKind::BoltBottom)
                    || madeAny;
            }
            if (madeAny) {
                objFeat->refreshCEGeoms();
                objFeat->requestPaint();
            }
            return madeAny;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionThreadBoltBottom)

CmdTechDrawExtensionThreadBoltBottom::CmdTechDrawExtensionThreadBoltBottom()
    : Command("TechDraw_ExtensionThreadBoltBottom")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Thread Bolt Bottom View");
    sToolTipText =
        QT_TR_NOOP("Adds a cosmetic thread to the top or bottom view of the selected bolts/screws/rods");
    sWhatsThis = "TechDraw_ExtensionThreadBoltBottom";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionThreadBoltBottom";
}

void CmdTechDrawExtensionThreadBoltBottom::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execThreadBoltBottom(this);
}

bool CmdTechDrawExtensionThreadBoltBottom::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionThreadsGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawExtensionThreadsGroup)

CmdTechDrawExtensionThreadsGroup::CmdTechDrawExtensionThreadsGroup()
    : Command("TechDraw_ExtensionThreadsGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Thread Hole Side View");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic thread to the side view of a selected hole between two selected parallel lines");
    sWhatsThis = "TechDraw_ExtensionThreadsGroup";
    sStatusTip = sMenuText;
}

void CmdTechDrawExtensionThreadsGroup::activated(int iMsg)
{
    //    Base::Console().message("CMD::TechDrawExtensionThreadsGroup - activated(%d)\n", iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again."));
        return;
    }

    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0://thread hole side view
            execThreadHoleSide(this);
            break;
        case 1://thread hole bottom view
            execThreadHoleBottom(this);
            break;
        case 2://thread bolt side view
            execThreadBoltSide(this);
            break;
        case 3://thread bolt bottom view
            execThreadBoltBottom(this);
            break;
        default:
            Base::Console().message("CMD::CVGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawExtensionThreadsGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionThreadHoleSide"));
    p1->setObjectName(QStringLiteral("TechDraw_ExtensionThreadHoleSide"));
    p1->setWhatsThis(QStringLiteral("TechDraw_ExtensionThreadHoleSide"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionThreadHoleBottom"));
    p2->setObjectName(QStringLiteral("TechDraw_ExtensionThreadHoleBottom"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ExtensionThreadHoleBottom"));
    QAction* p3 = pcAction->addAction(QString());
    p3->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionThreadBoltSide"));
    p3->setObjectName(QStringLiteral("TechDraw_ExtensionThreadBoltSide"));
    p3->setWhatsThis(QStringLiteral("TechDraw_ExtensionThreadBoltSide"));
    QAction* p4 = pcAction->addAction(QString());
    p4->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionThreadBoltBottom"));
    p4->setObjectName(QStringLiteral("TechDraw_ExtensionThreadBoltBottom"));
    p4->setWhatsThis(QStringLiteral("TechDraw_ExtensionThreadBoltBottom"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawExtensionThreadsGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction)  {
        return;
    }
    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> action = pcAction->actions();

    QAction* arc1 = action[0];
    arc1->setText(QApplication::translate("CmdTechDrawExtensionThreadHoleSide",
                                          "Cosmetic Thread Hole Side View"));
    arc1->setToolTip(QApplication::translate("CmdTechDrawExtensionThreadHoleSide",
                                             "Adds a cosmetic thread to the side view of a "
                                             "selected hole between two selected parallel lines"));
    arc1->setStatusTip(arc1->text());
    QAction* arc2 = action[1];
    arc2->setText(QApplication::translate("CmdTechDrawExtensionThreadHoleBottom",
                                          "Cosmetic Thread Hole Bottom View"));
    arc2->setToolTip(
        QApplication::translate("CmdTechDrawExtensionThreadHoleBottom",
                                "Adds a cosmetic thread to the top or bottom view of holes or circles"));
    arc2->setStatusTip(arc2->text());
    QAction* arc3 = action[2];
    arc3->setText(QApplication::translate("CmdTechDrawExtensionThreadBoltSide",
                                          "Cosmetic Thread Bolt Side View"));
    arc3->setToolTip(
        QApplication::translate("CmdTechDrawExtensionThreadBoltSide",
                                "Adds a cosmetic thread to the side view of a bolt/screw/rod "
                                "between two selected parallel lines"));
    arc3->setStatusTip(arc3->text());
    QAction* arc4 = action[3];
    arc4->setText(QApplication::translate("CmdTechDrawExtensionThreadBoltBottom",
                                          "Cosmetic Thread Bolt Bottom View"));
    arc4->setToolTip(QApplication::translate(
        "CmdTechDrawExtensionThreadBoltBottom",
        "Adds a cosmetic thread to the top or bottom view of the selected bolts/screws/rods"));
    arc4->setStatusTip(arc4->text());
}

bool CmdTechDrawExtensionThreadsGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionSelectLineAttributes
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionSelectLineAttributes)

CmdTechDrawExtensionSelectLineAttributes::CmdTechDrawExtensionSelectLineAttributes()
    : Command("TechDraw_ExtensionSelectLineAttributes")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Select Line Attributes, Cascade Spacing and Delta Distance");
    sToolTipText = QT_TR_NOOP(
        "Configures the default attributes for cosmetic lines and centerlines, including cascade spacing and delta distance");
    sWhatsThis = "TechDraw_ExtensionSelectLineAttributes";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionSelectLineAttributes";
}

void CmdTechDrawExtensionSelectLineAttributes::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    TaskInternal::showDocumentDialog(
        new TaskDlgSelectLineAttributes(),
        getDocument()
    );
}

bool CmdTechDrawExtensionSelectLineAttributes::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionChangeLineAttributes
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionChangeLineAttributes)

CmdTechDrawExtensionChangeLineAttributes::CmdTechDrawExtensionChangeLineAttributes()
    : Command("TechDraw_ExtensionChangeLineAttributes")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Change Line Attributes");
    sToolTipText = QT_TR_NOOP("Changes the selected cosmetic lines and centerlines to the specified attributes");
    sWhatsThis = "TechDraw_ExtensionChangeLineAttributes";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionChangeLineAttributes";
}

void CmdTechDrawExtensionChangeLineAttributes::activated(int iMsg)
{
    // change attributes (type, width, color) of a cosmetic or centerline
    Q_UNUSED(iMsg);
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(this, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw change line attributes"))) {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    if (subNames.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Change Line Attributes"),
            QObject::tr("Select one or more cosmetic lines or centerlines")
        );
        return;
    }
    const bool changed = _runExactExtensionCommand(
        this,
        QT_TRANSLATE_NOOP("Command", "Change line attributes"),
        QObject::tr("Change Line Attributes"),
        [&]() {
            const auto targets = drawingLineTargetsFromSelection(
                objFeat,
                subNames
            );
            if (targets.empty()) {
                return false;
            }
            changeDrawingLineAttributes(
                objFeat,
                targets,
                _getActiveLineAttributes()
            );
            return true;
        }
    );
    if (changed) {
        getSelection().clearSelection();
    }
}

bool CmdTechDrawExtensionChangeLineAttributes::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionVertexAtIntersection
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionVertexAtIntersection)

CmdTechDrawExtensionVertexAtIntersection::CmdTechDrawExtensionVertexAtIntersection()
    : Command("TechDraw_ExtensionVertexAtIntersection")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Intersection Vertices");
    sToolTipText =
        QT_TR_NOOP("Adds cosmetic vertices at the intersections of selected edges");
    sWhatsThis = "TechDraw_ExtensionVertexAtIntersection";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionVertexAtIntersection";
}

void CmdTechDrawExtensionVertexAtIntersection::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    //Base::Console().message("VertexAtIntersection started\n");
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(this, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw cosmetic intersection vertices")))  {
        return;
    }
    const std::vector<std::string> SubNames = selection[0].getSubNames();
    if (SubNames.size() != 2
        || TechDraw::DrawUtil::getGeomTypeFromName(SubNames[0])
            != "Edge"
        || TechDraw::DrawUtil::getGeomTypeFromName(SubNames[1])
            != "Edge") {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Intersection Vertices"),
            QObject::tr("Select exactly two edges")
        );
        return;
    }
    const auto geom1 = objFeat->getGeomByIndex(
        TechDraw::DrawUtil::getIndexFromName(SubNames[0])
    );
    const auto geom2 = objFeat->getGeomByIndex(
        TechDraw::DrawUtil::getIndexFromName(SubNames[1])
    );
    if (!geom1 || !geom2) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Intersection Vertices"),
            QObject::tr("The selected edge geometry is unavailable")
        );
        return;
    }
    try {
        (void) validateDrawingVertexIntersections(objFeat, SubNames);
    }
    catch (const Base::Exception&) {
        QMessageBox::information(
            Gui::getMainWindow(),
            QObject::tr("Cosmetic Intersection Vertices"),
            QObject::tr("The selected edges do not intersect")
        );
        return;
    }
    const bool created = _runExactExtensionCommand(
        this,
        QT_TRANSLATE_NOOP("Command", "Cosmetic intersection vertices"),
        QObject::tr("Cosmetic Intersection Vertices"),
        [&]() {
            createDrawingVertexIntersections(objFeat, SubNames);
            return true;
        }
    );
    if (created) {
        getSelection().clearSelection();
    }
}

bool CmdTechDrawExtensionVertexAtIntersection::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_CosmeticCircle
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawCosmeticCircle)

CmdTechDrawCosmeticCircle::CmdTechDrawCosmeticCircle()
  : Command("TechDraw_CosmeticCircle")
{
    sAppModule      = "TechDraw";
    sGroup          = QT_TR_NOOP("TechDraw");
    sMenuText       = QT_TR_NOOP("Cosmetic 1 Point Circle");
    sToolTipText    = QT_TR_NOOP("Adds a cosmetic circle based on a selected centerpoint");
    sWhatsThis      = "TechDraw_CosmeticCircle";
    sStatusTip      = sToolTipText;
    sPixmap         = "actions/TechDraw_CosmeticCircle";
}



bool CmdTechDrawCosmeticCircle::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

void execCosmeticCircleCenter(Gui::Command* cmd)
{
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(cmd);
    if (!page) {
        return;
    }

    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    TechDraw::DrawViewPart* baseFeat = nullptr;
    std::vector<std::string> subNames2D;
    std::vector< std::pair<Part::Feature*, std::string> > objs3D;
    if (selection.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong Selection"),
                             QObject::tr("Selection is empty."));
        return;
    }

    for (auto& so: selection) {
        if (so.getObject()->isDerivedFrom<TechDraw::DrawViewPart>()) {
            baseFeat = static_cast<TechDraw::DrawViewPart*> (so.getObject());
            subNames2D = so.getSubNames();
        } else if (so.getObject()->isDerivedFrom<Part::Feature>()) {
            std::vector<std::string> subNames3D = so.getSubNames();
            for (auto& sub3D: subNames3D) {
                std::pair<Part::Feature*, std::string> temp;
                temp.first = static_cast<Part::Feature*>(so.getObject());
                temp.second = sub3D;
                objs3D.push_back(temp);
            }
        } else {
            //garbage
        }
    }

    if (!baseFeat) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong Selection"),
                             QObject::tr("You must select a base View for the circle."));
        return;
    }
    if (baseFeat->getDocument() != page->getDocument()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong Selection"),
            QObject::tr(
                "The cosmetic circle view must belong to the selected page."
            )
        );
        return;
    }

    std::vector<std::string> edgeNames;
    std::vector<std::string> vertexNames;
    for (auto& s: subNames2D) {
        std::string geomType = DrawUtil::getGeomTypeFromName(s);
        if (geomType == "Vertex") {
            vertexNames.push_back(s);
        } else if (geomType == "Edge") {
            edgeNames.push_back(s);
        }
    }

    //check if editing existing edge
    if (!edgeNames.empty() && (edgeNames.size() == 1)) {
        TechDraw::CosmeticEdge* ce = baseFeat->getCosmeticEdgeBySelection(edgeNames.front());
        if (!ce
            || !(ce->m_geometry->getGeomType() == GeomType::CIRCLE
                || ce->m_geometry->getGeomType() == GeomType::ARCOFCIRCLE)) {
            QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong Selection"),
                             QObject::tr("Selection is not a Cosmetic Circle or a Cosmetic Arc of Circle."));
            return;
        }

        TaskInternal::showDocumentDialog(
            new TaskDlgCosmeticCircle(baseFeat, edgeNames.front()),
            baseFeat->getDocument()
        );
        return;
    }

    std::vector<Base::Vector3d> points;
    std::vector<bool> is3d;
    //get the 2D points
    if (!vertexNames.empty()) {
        for (auto& v2d: vertexNames) {
            int idx = DrawUtil::getIndexFromName(v2d);
            TechDraw::VertexPtr v = baseFeat->getProjVertexByIndex(idx);
            if (v) {
                points.push_back(v->point());
                is3d.push_back(false);
            }
        }
    }
    //get the 3D points
    if (!objs3D.empty()) {
        for (auto& o3D: objs3D) {
            int idx = DrawUtil::getIndexFromName(o3D.second);
            Part::TopoShape s = o3D.first->Shape.getShape();
            TopoDS_Vertex v = TopoDS::Vertex(s.getSubShape(TopAbs_VERTEX, idx));
            Base::Vector3d p = DrawUtil::vertex2Vector(v);
            points.push_back(p);
            is3d.push_back(true);
        }
    }

    if (points.empty()) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong Selection"),
                             QObject::tr("Select a center for the circle."));
        return;
    }

    bool centerIs3d = false;
    if (!is3d.empty()) {
        centerIs3d = is3d.front();
    }

    TaskInternal::showDocumentDialog(
        new TaskDlgCosmeticCircle(baseFeat, points, centerIs3d),
        baseFeat->getDocument()
    );
}
void CmdTechDrawCosmeticCircle::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    Gui::TaskView::TaskDialog *dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task In Progress"),
            QObject::tr("Close active task dialog and try again."));
        return;
    }
    execCosmeticCircleCenter(this);

    updateActive();
    Gui::Selection().clearSelection();
}

//===========================================================================
// TechDraw_ExtensionDrawCosmArc
//===========================================================================

//! adds an anti-clockwise arc based on a center point, a radius/start angle point and an end angle
//! point.  Selection order is significant - center, start end.
void execDrawCosmArc(Gui::Command* cmd)
{
    //draw a cosmetic arc of circle
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw cosmetic arc")))  {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic arc"),
        QObject::tr("Cosmetic Arc"),
        [&]() {
            createDrawingCosmeticCurve(
                objFeat,
                DrawingCosmeticCurveKind::CenterStartEndArc,
                subNames);
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionDrawCosmArc)

CmdTechDrawExtensionDrawCosmArc::CmdTechDrawExtensionDrawCosmArc()
    : Command("TechDraw_ExtensionDrawCosmArc")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Arc");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic counter clockwise arc based on three vertices, "
            "where the first selection is the center point and the second is the radius and start point");
    sWhatsThis = "TechDraw_ExtensionDrawCosmArc";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionDrawCosmArc";
}

void CmdTechDrawExtensionDrawCosmArc::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    //Base::Console().message("Cosmetic Arc started\n");
    execDrawCosmArc(this);
}

bool CmdTechDrawExtensionDrawCosmArc::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionDrawCosmCircle
//===========================================================================

void execDrawCosmCircle(Gui::Command* cmd)
{
    //draw a cosmetic circle
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw cosmetic circle"))) {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic Circle"),
        QObject::tr("Cosmetic 2 Point Circle"),
        [&]() {
            createDrawingCosmeticCurve(
                objFeat,
                DrawingCosmeticCurveKind::TwoPointCircle,
                subNames);
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionDrawCosmCircle)

CmdTechDrawExtensionDrawCosmCircle::CmdTechDrawExtensionDrawCosmCircle()
    : Command("TechDraw_ExtensionDrawCosmCircle")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic 2 Point Circle");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic circle based on two selected vertices, where the first is the center point and the second is the radius");
    sWhatsThis = "TechDraw_ExtensionDrawCosmCircle";
    sStatusTip = sToolTipText;
    sPixmap = "TechDraw_ExtensionDrawCosmCircle";
}

void CmdTechDrawExtensionDrawCosmCircle::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    //Base::Console().message("Cosmetic Circle started\n");
    execDrawCosmCircle(this);
}

bool CmdTechDrawExtensionDrawCosmCircle::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionDrawCosmCircle3Points
//===========================================================================

void execDrawCosmCircle3Points(Gui::Command* cmd)
{
    //draw a cosmetic circle through 3 points
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Cosmetic Circle 3 Points")))  {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Cosmetic Circle 3 Points"),
        QObject::tr("Cosmetic 3 Point Circle"),
        [&]() {
            createDrawingCosmeticCurve(
                objFeat,
                DrawingCosmeticCurveKind::ThreePointCircle,
                subNames);
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionDrawCosmCircle3Points)

CmdTechDrawExtensionDrawCosmCircle3Points::CmdTechDrawExtensionDrawCosmCircle3Points()
    : Command("TechDraw_ExtensionDrawCosmCircle3Points")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic 3 Point Circle");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic circle that passes through 3 selected perimeter points");
    sWhatsThis = "TechDraw_ExtensionDrawCosmCircle3Points";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionDrawCosmCircle3Points";
}

void CmdTechDrawExtensionDrawCosmCircle3Points::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    //Base::Console().message("Cosmetic Circle 3 Points started\n");
    execDrawCosmCircle3Points(this);
}

bool CmdTechDrawExtensionDrawCosmCircle3Points::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionDrawCirclesGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawExtensionDrawCirclesGroup)

CmdTechDrawExtensionDrawCirclesGroup::CmdTechDrawExtensionDrawCirclesGroup()
    : Command("TechDraw_ExtensionDrawCirclesGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic 1 Point Circle");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic circle based on two vertices, where the first selection is the centerpoint and the second is the radius");
    sWhatsThis = "TechDraw_ExtensionDrawCirclesGroup";
    sStatusTip = sMenuText;
}

void CmdTechDrawExtensionDrawCirclesGroup::activated(int iMsg)
{
    //    Base::Console().message("CMD::ExtensionDrawCirclesGroup - activated(%d)\n", iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again."));
        return;
    }

    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0: // 1 Point Circle
            execCosmeticCircleCenter(this);
            break;
        case 1: // 2 Point Circle
            execDrawCosmCircle(this);
            break;
        case 2: // 3 Point Circle
            execDrawCosmCircle3Points(this);
            break;
        case 3: // Cosmetic Arc
            execDrawCosmArc(this);
            break;
        default:
            Base::Console().message("CMD::CVGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawExtensionDrawCirclesGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("actions/TechDraw_CosmeticCircle"));
    p1->setObjectName(QStringLiteral("TechDraw_CosmeticCircle"));
    p1->setWhatsThis(QStringLiteral("TechDraw_CosmeticCircle"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionDrawCosmCircle"));
    p2->setObjectName(QStringLiteral("TechDraw_ExtensionDrawCosmCircle"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ExtensionDrawCosmCircle"));
    QAction* p3 = pcAction->addAction(QString());
    p3->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionDrawCosmCircle3Points"));
    p3->setObjectName(QStringLiteral("TechDraw_ExtensionDrawCosmCircle3Points"));
    p3->setWhatsThis(QStringLiteral("TechDraw_ExtensionDrawCosmCircle3Points"));
    QAction* p4 = pcAction->addAction(QString());
    p4->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionDrawCosmArc"));
    p4->setObjectName(QStringLiteral("TechDraw_ExtensionDrawCosmArc"));
    p4->setWhatsThis(QStringLiteral("TechDraw_ExtensionDrawCosmArc"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawExtensionDrawCirclesGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> action = pcAction->actions();

    QAction* p1 = action[0];
    p1->setText(QApplication::translate("CmdTechDrawCosmeticCircle",
                                        "Cosmetic 1 Point Circle"));
    p1->setToolTip(QApplication::translate("CmdTechDrawCosmeticCircle",
                                           "Adds a cosmetic circle based on a selected centerpoint"));
    p1->setStatusTip(p1->text());

    QAction* p2 = action[1];
    p2->setText(QApplication::translate("CmdTechDrawExtensionDrawCosmCircle",
                                        "Cosmetic 2 Point Circle"));
    p2->setToolTip(QApplication::translate("CmdTechDrawExtensionDrawCosmCircle",
                                           "Adds a cosmetic circle based on two vertices, where "
                                           "the first selection is the centerpoint and the second is the radius"));
    p2->setStatusTip(p2->text());

    QAction* p3 = action[2];
    p3->setText(QApplication::translate("CmdTechDrawExtensionDrawCosmCircle3Points",
                                        "Cosmetic 3 Point Circle"));
    p3->setToolTip(QApplication::translate("CmdTechDrawExtensionDrawCosmCircle3Points",
                                           "Adds a cosmetic circle that passes through 3 selected perimeter points"));
    p3->setStatusTip(p3->text());

    QAction* p4 = action[3];
    p4->setText(QApplication::translate("CmdTechDrawExtensionDrawCosmArc", "Cosmetic Arc"));
    p4->setToolTip(QApplication::translate("CmdTechDrawExtensionDrawCosmArc",
                                           "Adds a cosmetic counter clockwise arc based on three vertices, "
                                           "where the first selection is the center point and the second is the radius and start point."));
    p4->setStatusTip(p4->text());
}

bool CmdTechDrawExtensionDrawCirclesGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionLineParallel
//===========================================================================

void execLineParallelPerpendicular(Gui::Command* cmd, bool isParallel)
{
    // create a line parallel or perpendicular to another line
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Cosmetic Line Parallel/Perpendicular"))) {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    const bool created = _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP(
            "Command",
            "Cosmetic Line Parallel/Perpendicular"
        ),
        isParallel
            ? QObject::tr("Cosmetic Parallel Line")
            : QObject::tr("Cosmetic Perpendicular Line"),
        [&]() {
            createDrawingCosmeticLine(
                objFeat,
                isParallel
                    ? DrawingCosmeticLineConstruction::Parallel
                    : DrawingCosmeticLineConstruction::Perpendicular,
                subNames);
            return true;
        }
    );
    if (created) {
        cmd->getSelection().clearSelection();
    }
}

DEF_STD_CMD_A(CmdTechDrawExtensionLineParallel)

CmdTechDrawExtensionLineParallel::CmdTechDrawExtensionLineParallel()
    : Command("TechDraw_ExtensionLineParallel")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Parallel Line");
    sToolTipText =
        QT_TR_NOOP(
            "Adds a cosmetic line parallel to the selected straight edge through the selected vertex"
        );
    sWhatsThis = "TechDraw_ExtensionLineParallel";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionLineParallel";
}

void CmdTechDrawExtensionLineParallel::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execLineParallelPerpendicular(this, true);
}

bool CmdTechDrawExtensionLineParallel::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionLinePerpendicular
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionLinePerpendicular)

CmdTechDrawExtensionLinePerpendicular::CmdTechDrawExtensionLinePerpendicular()
    : Command("TechDraw_ExtensionLinePerpendicular")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Perpendicular Line");
    sToolTipText =
        QT_TR_NOOP("Adds a cosmetic line perpendicular to the selected line through the selected vertex");
    sWhatsThis = "TechDraw_ExtensionLinePerpendicular";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionLinePerpendicular";
}

void CmdTechDrawExtensionLinePerpendicular::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execLineParallelPerpendicular(this, false);
}

bool CmdTechDrawExtensionLinePerpendicular::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionLinePPGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawExtensionLinePPGroup)

CmdTechDrawExtensionLinePPGroup::CmdTechDrawExtensionLinePPGroup()
    : Command("TechDraw_ExtensionLinePPGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Cosmetic Parallel Line");
    sToolTipText = QT_TR_NOOP("Adds a cosmetic line parallel to the selected line through the selected vertex");
    sWhatsThis = "TechDraw_ExtensionLinePPGroup";
    sStatusTip = sMenuText;
}

void CmdTechDrawExtensionLinePPGroup::activated(int iMsg)
{
    //    Base::Console().message("CMD::ExtensionLinePPGroup - activated(%d)\n", iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again."));
        return;
    }

    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0://create parallel line
            execLineParallelPerpendicular(this, true);
            break;
        case 1://create perpendicular line
            execLineParallelPerpendicular(this, false);
            break;
        default:
            Base::Console().message("CMD::CVGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawExtensionLinePPGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionLineParallel"));
    p1->setObjectName(QStringLiteral("TechDraw_ExtensionLineParallel"));
    p1->setWhatsThis(QStringLiteral("TechDraw_ExtensionLineParallel"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionLinePerpendicular"));
    p2->setObjectName(QStringLiteral("TechDraw_ExtensionLinePerpendicular"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ExtensionLinePerpendicular"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawExtensionLinePPGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> action = pcAction->actions();

    QAction* arc1 = action[0];
    arc1->setText(
        QApplication::translate("CmdTechDrawExtensionLineParallel", "Cosmetic Parallel Line"));
    arc1->setToolTip(
        QApplication::translate("CmdTechDrawExtensionLineParallel",
                                "Adds a cosmetic line parallel to the selected line through the selected vertex"));
    arc1->setStatusTip(arc1->text());
    QAction* arc2 = action[1];
    arc2->setText(QApplication::translate("CmdTechDrawExtensionLinePerpendicular",
                                          "Cosmetic Perpendicular Line"));
    arc2->setToolTip(QApplication::translate(
        "CmdTechDrawExtensionLinePerpendicular",
        "Adds a cosmetic line perpendicular to the selected line through the selected vertex"));
    arc2->setStatusTip(arc2->text());
}

bool CmdTechDrawExtensionLinePPGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionLockUnlockView
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionLockUnlockView)

CmdTechDrawExtensionLockUnlockView::CmdTechDrawExtensionLockUnlockView()
    : Command("TechDraw_ExtensionLockUnlockView")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Toggle View Lock");
    sToolTipText = QT_TR_NOOP("Locks or unlocks the position of the selected views");
    sWhatsThis = "TechDraw_ExtensionLockUnlockView";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionLockUnlockView";
}

void CmdTechDrawExtensionLockUnlockView::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();
    TechDraw::DrawPage* page = DrawGuiUtil::findPage(this);
    App::Document* document = getDocument();
    if (selection.empty() || !page || page->getDocument() != document) {
        return;
    }

    std::vector<TechDraw::DrawViewPart*> views;
    views.reserve(selection.size());
    for (const auto& selected : selection) {
        auto* view =
            _resolveSelectedObject<
                TechDraw::DrawViewPart
            >(selected);
        if (!view
            || view->getDocument() != document
            || view->findParentPage() != page) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Toggle View Lock"),
                QObject::tr(
                    "Select only drawing views on the active page"
                )
            );
            return;
        }
        views.push_back(view);
    }
    _runExactExtensionCommand(
        this,
        QT_TRANSLATE_NOOP("Command", "Lock/Unlock View"),
        QObject::tr("Toggle View Lock"),
        [&]() {
            std::vector<DrawingViewLockRequest> requests;
            requests.reserve(views.size());
            for (auto* view : views) {
                requests.push_back(
                    {view, !view->LockPosition.getValue()}
                );
            }
            changeDrawingViewLocks(page, requests);
            return true;
        }
    );
}

bool CmdTechDrawExtensionLockUnlockView::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionExtendLine
//===========================================================================

void execExtendShortenLine(Gui::Command* cmd, bool extend)
{
    // extend or shorten a cosmetic line or a centerline
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(cmd, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Extend/Shorten Line"))) {
        return;
    }
    const std::vector<std::string> subNames = selection[0].getSubNames();
    if (subNames.size() != 1
        || DrawUtil::getGeomTypeFromName(subNames.front()) != "Edge") {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Extend/Shorten Line"),
            QObject::tr("Select exactly one cosmetic line or centerline")
        );
        return;
    }
    const auto targets = drawingLineTargetsFromSelection(objFeat, subNames);
    if (targets.size() != 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Extend/Shorten Line"),
            QObject::tr("Select exactly one straight cosmetic line or centerline")
        );
        return;
    }
    const double stretch = activeDimAttributes.getLineStretch();
    _runExactExtensionCommand(
        cmd,
        QT_TRANSLATE_NOOP("Command", "Extend/shorten line"),
        extend
            ? QObject::tr("Extend Line")
            : QObject::tr("Shorten Line"),
        [&]() {
            changeDrawingLineLength(
                objFeat,
                targets.front(),
                extend
                    ? DrawingLineLengthOperation::Extend
                    : DrawingLineLengthOperation::Shorten,
                stretch
            );
            return true;
        }
    );
}

DEF_STD_CMD_A(CmdTechDrawExtensionExtendLine)

CmdTechDrawExtensionExtendLine::CmdTechDrawExtensionExtendLine()
    : Command("TechDraw_ExtensionExtendLine")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Extend Line");
    sToolTipText = QT_TR_NOOP("Extends a selected cosmetic line or centerline at both ends by the specified delta distance");
    sWhatsThis = "TechDraw_ExtensionExtendLine";
    sStatusTip = sToolTipText;
    sPixmap = "TechDraw_ExtensionExtendLine";
}

void CmdTechDrawExtensionExtendLine::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execExtendShortenLine(this, true);
    ///Base::Console().message("ExtendLine started\n");
}

bool CmdTechDrawExtensionExtendLine::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionShortenLine
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionShortenLine)

CmdTechDrawExtensionShortenLine::CmdTechDrawExtensionShortenLine()
    : Command("TechDraw_ExtensionShortenLine")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Shorten Line");
    sToolTipText = QT_TR_NOOP("Shortens a selected cosmetic line or centerline at both ends by the specified delta distance");
    sWhatsThis = "TechDraw_ExtensionShortenLine";
    sStatusTip = sMenuText;
    sPixmap = "TechDraw_ExtensionShortenLine";
}

void CmdTechDrawExtensionShortenLine::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    execExtendShortenLine(this, false);
    ///Base::Console().message("ShortenLine started\n");
}

bool CmdTechDrawExtensionShortenLine::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionExtendShortenLineGroup
//===========================================================================

DEF_STD_CMD_ACL(CmdTechDrawExtendShortenLineGroup)

CmdTechDrawExtendShortenLineGroup::CmdTechDrawExtendShortenLineGroup()
    : Command("TechDraw_ExtensionExtendShortenLineGroup")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Extend Line");
    sToolTipText = QT_TR_NOOP("Extends a selected cosmetic line or centerline at both ends by the specified delta distance");
    sWhatsThis = "TechDraw_ExtensionExtendShortenLineGroup";
    sStatusTip = sMenuText;
}

void CmdTechDrawExtendShortenLineGroup::activated(int iMsg)
{
    // Base::Console().message("CMD::ExtendShortenLineGroup - activated(%d)\n", iMsg);
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Task in progress"),
                             QObject::tr("Close active task dialog and try again."));
        return;
    }

    auto pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    switch (iMsg) {
        case 0://extend a line
            execExtendShortenLine(this, true);
            break;
        case 1://shorten line
            execExtendShortenLine(this, false);
            break;
        default:
            Base::Console().message("CMD::CVGrp - invalid iMsg: %d\n", iMsg);
    };
}

Gui::Action* CmdTechDrawExtendShortenLineGroup::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionExtendLine"));
    p1->setObjectName(QStringLiteral("TechDraw_ExtensionExtendLine"));
    p1->setWhatsThis(QStringLiteral("TechDraw_ExtensionExtendLine"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("TechDraw_ExtensionShortenLine"));
    p2->setObjectName(QStringLiteral("TechDraw_ExtensionShortenLine"));
    p2->setWhatsThis(QStringLiteral("TechDraw_ExtensionShortenLine"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdTechDrawExtendShortenLineGroup::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> action = pcAction->actions();

    QAction* arc1 = action[0];
    arc1->setText(QApplication::translate("CmdTechDrawExtensionExtendLine", "Extend Line"));
    arc1->setToolTip(QApplication::translate(
        "CmdTechDrawExtensionExtendLine", "Extends a selected cosmetic line or centerline at both ends by the specified delta distance"));
    arc1->setStatusTip(arc1->text());
    QAction* arc2 = action[1];
    arc2->setText(QApplication::translate("CmdTechDrawExtensionShortenLine", "Shorten Line"));
    arc2->setToolTip(QApplication::translate(
        "CmdTechDrawExtensionShortenLine", "Shortens a selected cosmetic line or centerline at both ends by the specified delta distance"));
    arc2->setStatusTip(arc2->text());
}

bool CmdTechDrawExtendShortenLineGroup::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this, true);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionAreaAnnotation
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionAreaAnnotation)

CmdTechDrawExtensionAreaAnnotation::CmdTechDrawExtensionAreaAnnotation()
    : Command("TechDraw_ExtensionAreaAnnotation")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Area Annotation");
    sToolTipText = QT_TR_NOOP("Calculates the area of multiple selected faces");
    sWhatsThis = "TechDraw_ExtensionAreaAnnotation";
    sStatusTip = sToolTipText;
    sPixmap = "TechDraw_ExtensionAreaAnnotation";
}

void CmdTechDrawExtensionAreaAnnotation::activated(int iMsg)
// calculate the area of selected faces, create output in a balloon
{
    Q_UNUSED(iMsg);
    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart* objFeat{nullptr};
    if (!_checkSel(this, selection, objFeat, QT_TRANSLATE_NOOP("Command","TechDraw Calculate Selected Area")))  {
        return;
    }

    // we must have at least 1 face in the selection
    const std::vector<std::string> subNamesAll = selection[0].getSubNames();
    std::vector<std::string> subNames;
    for (auto& name : subNamesAll) {
        std::string geomType = DrawUtil::getGeomTypeFromName(name);
        if (geomType == "Face") {
            subNames.push_back(name);
        }
    }

    if (subNames.empty()) {
        QMessageBox::warning(Gui::getMainWindow(),
                             QObject::tr("Incorrect Selection"),
                             QObject::tr("No faces in selection"));
        return;
    }

    App::Document* document = objFeat->getDocument();
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Calculate Face Area")
        );
        createProjectedMeasurementAnnotationFeature(
            objFeat,
            MeasurementAnnotationKind::Area,
            subNames,
            std::nullopt);
        objFeat->touch(true);
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Area Annotation"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawExtensionAreaAnnotation::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// TechDraw_ExtensionArcLengthAnnotation
//===========================================================================

DEF_STD_CMD_A(CmdTechDrawExtensionArcLengthAnnotation)

CmdTechDrawExtensionArcLengthAnnotation::CmdTechDrawExtensionArcLengthAnnotation()
    : Command("TechDraw_ExtensionArcLengthAnnotation")
{
    sAppModule = "TechDraw";
    sGroup = QT_TR_NOOP("TechDraw");
    sMenuText = QT_TR_NOOP("Arc Length Annotation");
    sToolTipText = QT_TR_NOOP("Inserts an annotation with the calculated arc length of the selected edges");
    sWhatsThis = "TechDraw_ExtensionArcLengthAnnotation";
    sStatusTip = sToolTipText;
    sPixmap = "TechDraw_ExtensionArcLengthAnnotation";
}

void CmdTechDrawExtensionArcLengthAnnotation::activated(int iMsg)
// Calculate the arc length of selected edge and create a balloon holding the datum
{
    Q_UNUSED(iMsg);

    std::vector<Gui::SelectionObject> selection;
    TechDraw::DrawViewPart *objFeat{nullptr};
    if (!_checkSel(this, selection, objFeat, QT_TRANSLATE_NOOP("Command", "TechDraw Calculate Selected Arc Length"))) {
        return;
    }

    // Collect all edges in the selection
    std::vector<std::string> subNames;
    for (auto &name : selection[0].getSubNames()) {
        if (DrawUtil::getGeomTypeFromName(name) == "Edge") {
            subNames.push_back(name);
        }
    }

    if (subNames.empty()) {
        QMessageBox::warning(Gui::getMainWindow(),
                             QObject::tr("Incorrect Selection"),
                             QObject::tr("No edges in selection"));
        return;
    }

    App::Document* document = objFeat->getDocument();
    try {
        TaskInternal::OwnedDocumentTransaction transaction(
            document,
            QT_TRANSLATE_NOOP("Command", "Calculate Edge Length")
        );
        createProjectedMeasurementAnnotationFeature(
            objFeat,
            MeasurementAnnotationKind::ArcLength,
            subNames,
            std::nullopt);
        objFeat->touch(true);
        TaskInternal::updateExactDocument(document);
        transaction.commit();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Arc Length Annotation"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdTechDrawExtensionArcLengthAnnotation::isActive()
{
    bool havePage = DrawGuiUtil::needPage(this);
    bool haveView = DrawGuiUtil::needView(this);
    return (havePage && haveView);
}

//===========================================================================
// internal helper routines
//===========================================================================
namespace TechDrawGui
{

LineFormat& _getActiveLineAttributes()
{
    return LineFormat::getCurrentLineFormat();
}

bool _checkSel(Gui::Command* cmd, std::vector<Gui::SelectionObject>& selection,
               TechDraw::DrawViewPart*& objFeat, const std::string& message)
{
    // check selection of getSelectionEx() and selection[0].getObject()
    App::Document* document = cmd ? cmd->getDocument() : nullptr;
    TechDraw::DrawPage* page = cmd
        ? DrawGuiUtil::findPage(cmd)
        : nullptr;
    if (!document || !page || page->getDocument() != document) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QString::fromUtf8(message.c_str()),
            QObject::tr("No active drawing page")
        );
        return false;
    }
    selection = cmd->getSelection().getSelectionEx();
    if (selection.empty()) {
        // message is translated in caller
        QMessageBox::warning(Gui::getMainWindow(), QString::fromUtf8(message.c_str()),
                             QObject::tr("Selection is empty"));
        return false;
    }
    if (selection.size() != 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QString::fromUtf8(message.c_str()),
            QObject::tr("Select exactly one drawing view")
        );
        return false;
    }

    objFeat = dynamic_cast<TechDraw::DrawViewPart*>(selection[0].getObject());
    if (!objFeat
        || objFeat->getDocument() != document
        || objFeat->findParentPage() != page) {
        QMessageBox::warning(Gui::getMainWindow(), QString::fromUtf8(message.c_str()),
                             QObject::tr("Select a view on the active drawing page"));
        return false;
    }

    return true;
}

//! return the vertices in the selection as [Base::Vector3d]
std::vector<Base::Vector3d> _getVertexPoints(const std::vector<std::string>& SubNames,
                                             TechDraw::DrawViewPart* objFeat)
{
    std::vector<Base::Vector3d> vertexPoints;
    for (const std::string& Name : SubNames) {
        std::string GeoType = TechDraw::DrawUtil::getGeomTypeFromName(Name);
        if (GeoType == "Vertex") {
            int GeoId = TechDraw::DrawUtil::getIndexFromName(Name);
            TechDraw::VertexPtr vert = objFeat->getProjVertexByIndex(GeoId);
            if (vert) {
                vertexPoints.push_back(vert->point());
            }
        }
    }
    return vertexPoints;
}

//! get angle between x-axis and the vector from center to point.
//! result is [0, 360]
double _getAngle(Base::Vector3d center, Base::Vector3d point)
{
    constexpr double DegreesHalfCircle{180.0};
    Base::Vector3d vecCP = point - center;
    double angle = DU::angleWithX(vecCP) * DegreesHalfCircle / std::numbers::pi;
    return angle;
}

Base::Vector3d _circleCenter(Base::Vector3d p1, Base::Vector3d p2, Base::Vector3d p3)
{
    Base::Vector2d v1(p1.x, p1.y);
    Base::Vector2d v2(p2.x, p2.y);
    Base::Vector2d v3(p3.x, p3.y);
    Base::Vector2d center = Part::Geom2dCircle::getCircleCenter(v1, v2, v3);
    return Base::Vector3d(center.x, center.y, 0.0);
}

bool _createThreadCircle(
    const std::string& name,
    TechDraw::DrawViewPart* objFeat,
    DrawingThreadRepresentationKind kind
)
{
    // create the 3/4 arc symbolizing a thread from top seen
    if (!objFeat) {
        return false;
    }
    int GeoId = TechDraw::DrawUtil::getIndexFromName(name);
    TechDraw::BaseGeomPtr geom = objFeat->getGeomByIndex(GeoId);
    std::string GeoType =
        TechDraw::DrawUtil::getGeomTypeFromName(name);

    if (GeoType == "Edge"
        && geom
        && geom->getGeomType() == GeomType::CIRCLE) {
        const auto created = createDrawingThreadBottom(
            objFeat,
            kind,
            {name});
        if (created.size() != 1) {
            throw Base::RuntimeError(
                "The cosmetic thread circle could not be created");
        }
        return true;
    }
    else {
        const QString geometryName = geom
            ? QString::fromStdString(
                  GeometryUtils::getGeomTypeName(
                      geom->getGeomType()
                  )
              )
            : QObject::tr("invalid geometry");
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("TechDraw create thread circle"),
                             QObject::tr("Can not make thread circle for %1")
                                 .arg(geometryName));
    }
    return false;
}

void _setLineAttributes(TechDraw::CosmeticEdge* cosEdge)
{
    // set line attributes of a cosmetic edge
    if (!cosEdge) {
        throw Base::RuntimeError(
            "The cosmetic edge could not be created"
        );
    }
    cosEdge->m_format.setStyle(_getActiveLineAttributes().getStyle());
    cosEdge->m_format.setWidth(_getActiveLineAttributes().getWidth());
    cosEdge->m_format.setColor(_getActiveLineAttributes().getColor());
    cosEdge->m_format.setVisible(_getActiveLineAttributes().getVisible());
    cosEdge->m_format.setLineNumber(_getActiveLineAttributes().getLineNumber());
}

void _setLineAttributes(TechDraw::CenterLine* cosEdge)
{
    // set line attributes of a cosmetic edge
    if (!cosEdge) {
        throw Base::RuntimeError(
            "The centerline could not be created"
        );
    }
    cosEdge->m_format.setStyle(_getActiveLineAttributes().getStyle());
    cosEdge->m_format.setWidth(_getActiveLineAttributes().getWidth());
    cosEdge->m_format.setColor(_getActiveLineAttributes().getColor());
    cosEdge->m_format.setVisible(_getActiveLineAttributes().getVisible());
    cosEdge->m_format.setLineNumber(_getActiveLineAttributes().getLineNumber());
}

void _setLineAttributes(TechDraw::CosmeticEdge* cosEdge, int style, float weight, Base::Color color)
{
    // set line attributes of a cosmetic edge
    if (!cosEdge) {
        throw Base::RuntimeError(
            "The cosmetic edge could not be created"
        );
    }
    cosEdge->m_format.setStyle(style);
    cosEdge->m_format.setWidth(weight);
    cosEdge->m_format.setColor(color);
    cosEdge->m_format.setVisible(_getActiveLineAttributes().getVisible());
    cosEdge->m_format.setLineNumber(style);
}

void _setLineAttributes(TechDraw::CenterLine* cosEdge, int style, float weight, Base::Color color)
{
    // set line attributes of a centerline
    if (!cosEdge) {
        throw Base::RuntimeError(
            "The centerline could not be created"
        );
    }
    cosEdge->m_format.setStyle(style);
    cosEdge->m_format.setWidth(weight);
    cosEdge->m_format.setColor(color);
    cosEdge->m_format.setVisible(_getActiveLineAttributes().getVisible());
    cosEdge->m_format.setLineNumber(style);}
}// namespace TechDrawGui

//------------------------------------------------------------------------------
void CreateTechDrawCommandsExtensions()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdTechDrawExtensionSelectLineAttributes());
    rcCmdMgr.addCommand(new CmdTechDrawExtendShortenLineGroup());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionExtendLine());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionShortenLine());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionLockUnlockView());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionChangeLineAttributes());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionCircleCenterLinesGroup());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionCircleCenterLines());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionHoleCircle());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionVertexAtIntersection());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionDrawCirclesGroup());
    rcCmdMgr.addCommand(new CmdTechDrawCosmeticCircle());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionDrawCosmCircle());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionDrawCosmArc());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionDrawCosmCircle3Points());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionLinePPGroup());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionLineParallel());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionLinePerpendicular());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionThreadsGroup());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionThreadHoleSide());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionThreadBoltSide());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionThreadHoleBottom());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionThreadBoltBottom());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionAreaAnnotation());
    rcCmdMgr.addCommand(new CmdTechDrawExtensionArcLengthAnnotation());
}
