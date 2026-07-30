// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2015 Stefan Tröger (stefantroeger@gmx.net)              *
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
#include <QByteArray>
#include <QMessageBox>
#include <sstream>


#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/MainWindow.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeaturePrimitive.h>

#include "DlgActiveBody.h"
#include "Utils.h"
#include "WorkflowManager.h"

using namespace std;

DEF_STD_CMD_ACL(CmdPrimtiveCompAdditive)

static const char* primitiveIntToName(int id)
{
    switch (id) {
        case 0:
            return "Box";
        case 1:
            return "Cylinder";
        case 2:
            return "Sphere";
        case 3:
            return "Cone";
        case 4:
            return "Ellipsoid";
        case 5:
            return "Torus";
        case 6:
            return "Prism";
        case 7:
            return "Wedge";
        default:
            return nullptr;
    };
}

static bool hasUsableSolidTip(PartDesign::Body* body)
{
    auto* tip = body ? freecad_cast<Part::Feature*>(body->Tip.getValue()) : nullptr;
    if (!tip || !tip->isValid()) {
        return false;
    }
    const auto& shape = tip->Shape.getShape();
    return !shape.isNull() && shape.countSubShapes(TopAbs_SOLID) > 0;
}

struct ExactBodyIdentity
{
    App::Document* document {};
    std::string documentName;
    std::string documentUid;
    long objectId {-1};
    std::string objectName;
};

static ExactBodyIdentity exactBodyIdentity(const PartDesign::Body* body)
{
    return body && body->isAttachedToDocument()
        ? ExactBodyIdentity {
              .document = body->getDocument(),
              .documentName = body->getDocument()->getName(),
              .documentUid = body->getDocument()->Uid.getValueStr(),
              .objectId = body->getID(),
              .objectName = body->getNameInDocument(),
          }
        : ExactBodyIdentity {};
}

static PartDesign::Body* resolveExactBody(const ExactBodyIdentity& identity) noexcept
{
    if (!identity.document || identity.documentName.empty() || identity.documentUid.empty()
        || identity.objectId <= 0 || identity.objectName.empty()) {
        return nullptr;
    }
    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(identity.documentName.c_str());
    }
    catch (...) {
    }
    auto* body = document == identity.document
            && document->Uid.getValueStr() == identity.documentUid
        ? freecad_cast<PartDesign::Body*>(document->getObjectByID(identity.objectId))
        : nullptr;
    return body && body->getNameInDocument()
            && identity.objectName == body->getNameInDocument()
        ? body
        : nullptr;
}

static PartDesign::FeaturePrimitive* resolveExactPrimitive(
    const ExactBodyIdentity& bodyIdentity,
    const long objectId,
    const std::string& objectName
) noexcept
{
    auto* body = resolveExactBody(bodyIdentity);
    if (!body || objectId <= 0 || objectName.empty()) {
        return nullptr;
    }
    auto* primitive = freecad_cast<PartDesign::FeaturePrimitive*>(
        body->getDocument()->getObjectByID(objectId)
    );
    return primitive && primitive->getNameInDocument()
            && objectName == primitive->getNameInDocument()
        ? primitive
        : nullptr;
}

static PartDesign::FeaturePrimitive* createPrimitiveExact(
    PartDesign::Body* body,
    const std::string& typeName,
    const std::string& featureName,
    const char* factoryMethod,
    const bool documentFactory
)
{
    if (!body || !body->isAttachedToDocument() || typeName.empty() || featureName.empty()
        || !factoryMethod) {
        throw Base::ValueError("Creating a Part Design primitive requires one exact Body and type");
    }
    auto* document = body->getDocument();
    std::ostringstream expression;
    if (documentFactory) {
        expression << "App.getDocument('" << document->getName() << "').";
    }
    else {
        expression << Gui::Command::getObjectCmd(body) << '.';
    }
    expression << factoryMethod << "('" << typeName << "','" << featureName << "')";
    auto* result = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        QByteArray(expression.str().c_str()),
        PartDesign::FeaturePrimitive::getClassTypeId()
    );
    auto* primitive = freecad_cast<PartDesign::FeaturePrimitive*>(result);
    if (!primitive) {
        throw Base::RuntimeError("The Part Design primitive factory returned an incompatible object");
    }
    return primitive;
}

CmdPrimtiveCompAdditive::CmdPrimtiveCompAdditive()
    : Command("PartDesign_CompPrimitiveAdditive")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Primitive — Add Material");
    sToolTipText = QT_TR_NOOP("Adds a parametric primitive to the active Body");
    sWhatsThis = "PartDesign_CompPrimitiveAdditive";
    sStatusTip = sToolTipText;
    eType = ForEdit;
}

void CmdPrimtiveCompAdditive::activated(int iMsg)
{
    App::Document* doc = getDocument();
    const char* primitiveName = primitiveIntToName(iMsg);
    if (!doc || !primitiveName) {
        return;
    }

    // We need either an active Body, or for there to be no Body objects
    // (in which case, just make one) to make a new additive shape.

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(/* messageIfNot = */ false);

    auto shouldMakeBody(false);
    if (!pcActiveBody) {
        if (doc->getObjectsOfType(PartDesign::Body::getClassTypeId()).empty()) {
            shouldMakeBody = true;
        }
        else {
            PartDesignGui::DlgActiveBody dia(Gui::getMainWindow(), doc);
            if (dia.exec() == QDialog::DialogCode::Accepted) {
                pcActiveBody = dia.getActiveBody();
            }
            if (!pcActiveBody) {
                return;
            }
        }
    }

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    if (pcAction && iMsg >= 0 && iMsg < pcAction->actions().size()) {
        pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    }

    std::string shapeType(primitiveName);

    openCommand("Make additive " + shapeType);
    if (shouldMakeBody) {
        pcActiveBody = PartDesignGui::makeBody(doc);
    }

    if (!pcActiveBody) {
        abortCommand();
        return;
    }

    const ExactBodyIdentity bodyIdentity = exactBodyIdentity(pcActiveBody);
    auto FeatName(getUniqueObjectName(shapeType.c_str(), pcActiveBody));
    auto* prm = createPrimitiveExact(
        pcActiveBody,
        "PartDesign::Additive" + shapeType,
        FeatName,
        "addObject",
        true
    );
    const long primitiveId = prm->getID();
    const std::string exactPrimitiveName = prm->getNameInDocument();
    pcActiveBody = resolveExactBody(bodyIdentity);
    prm = resolveExactPrimitive(
        bodyIdentity,
        primitiveId,
        exactPrimitiveName
    );
    if (!pcActiveBody || !prm) {
        abortCommand();
        return;
    }
    auto* primitiveBody = PartDesign::Body::findBodyOf(prm);
    if (primitiveBody && primitiveBody != pcActiveBody) {
        abortCommand();
        return;
    }
    FCMD_OBJ_CMD(pcActiveBody, "addObject(" << getObjectCmd(prm) << ")");
    pcActiveBody = resolveExactBody(bodyIdentity);
    prm = resolveExactPrimitive(
        bodyIdentity,
        primitiveId,
        exactPrimitiveName
    );
    if (!pcActiveBody || !prm
        || PartDesign::Body::findBodyOf(prm) != pcActiveBody) {
        abortCommand();
        return;
    }
    Gui::Command::updateActive();

    auto base = prm->BaseFeature.getValue();
    FCMD_OBJ_HIDE(base);

    if (!base) {
        base = pcActiveBody;
    }
    copyVisual(prm, "ShapeAppearance", base);
    copyVisual(prm, "LineColor", base);
    copyVisual(prm, "PointColor", base);
    copyVisual(prm, "Transparency", base);
    copyVisual(prm, "DisplayMode", base);

    if (!PartDesignGui::setEdit(prm, pcActiveBody)) {
        abortCommand();
    }
}

Gui::Action* CmdPrimtiveCompAdditive::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveBox"));
    p1->setObjectName(QStringLiteral("PartDesign_AdditiveBox"));
    p1->setWhatsThis(QStringLiteral("PartDesign_AdditiveBox"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveCylinder"));
    p2->setObjectName(QStringLiteral("PartDesign_AdditiveCylinder"));
    p2->setWhatsThis(QStringLiteral("PartDesign_AdditiveCylinder"));
    QAction* p3 = pcAction->addAction(QString());
    p3->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveSphere"));
    p3->setObjectName(QStringLiteral("PartDesign_AdditiveSphere"));
    p3->setWhatsThis(QStringLiteral("PartDesign_AdditiveSphere"));
    QAction* p4 = pcAction->addAction(QString());
    p4->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveCone"));
    p4->setObjectName(QStringLiteral("PartDesign_AdditiveCone"));
    p4->setWhatsThis(QStringLiteral("PartDesign_AdditiveCone"));
    QAction* p5 = pcAction->addAction(QString());
    p5->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveEllipsoid"));
    p5->setObjectName(QStringLiteral("PartDesign_AdditiveEllipsoid"));
    p5->setWhatsThis(QStringLiteral("PartDesign_AdditiveEllipsoid"));
    QAction* p6 = pcAction->addAction(QString());
    p6->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveTorus"));
    p6->setObjectName(QStringLiteral("PartDesign_AdditiveTorus"));
    p6->setWhatsThis(QStringLiteral("PartDesign_AdditiveTorus"));
    QAction* p7 = pcAction->addAction(QString());
    p7->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditivePrism"));
    p7->setObjectName(QStringLiteral("PartDesign_AdditivePrism"));
    p7->setWhatsThis(QStringLiteral("PartDesign_AdditivePrism"));
    QAction* p8 = pcAction->addAction(QString());
    p8->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_AdditiveWedge"));
    p8->setObjectName(QStringLiteral("PartDesign_AdditiveWedge"));
    p8->setWhatsThis(QStringLiteral("PartDesign_AdditiveWedge"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdPrimtiveCompAdditive::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    QAction* arc1 = a[0];
    arc1->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Box — Add Material"));
    arc1->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveAdditive",
            "Creates an additive box by its width, height, and length"
        )
    );
    arc1->setStatusTip(arc1->toolTip());
    QAction* arc2 = a[1];
    arc2->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Cylinder — Add Material"));
    arc2->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveAdditive",
            "Creates an additive cylinder by its radius, height, and angle"
        )
    );
    arc2->setStatusTip(arc2->toolTip());
    QAction* arc3 = a[2];
    arc3->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Sphere — Add Material"));
    arc3->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveAdditive",
            "Creates an additive sphere by its radius and various angles"
        )
    );
    arc3->setStatusTip(arc3->toolTip());
    QAction* arc4 = a[3];
    arc4->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Cone — Add Material"));
    arc4->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveAdditive", "Creates an additive cone")
    );
    arc4->setStatusTip(arc4->toolTip());
    QAction* arc5 = a[4];
    arc5->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Ellipsoid — Add Material"));
    arc5->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveAdditive", "Creates an additive ellipsoid")
    );
    arc5->setStatusTip(arc5->toolTip());
    QAction* arc6 = a[5];
    arc6->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Torus — Add Material"));
    arc6->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveAdditive", "Creates an additive torus")
    );
    arc6->setStatusTip(arc6->toolTip());
    QAction* arc7 = a[6];
    arc7->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Prism — Add Material"));
    arc7->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveAdditive", "Creates an additive prism")
    );
    arc7->setStatusTip(arc7->toolTip());
    QAction* arc8 = a[7];
    arc8->setText(QApplication::translate("CmdPrimtiveCompAdditive", "Wedge — Add Material"));
    arc8->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveAdditive", "Creates an additive wedge")
    );
    arc8->setStatusTip(arc8->toolTip());
}

bool CmdPrimtiveCompAdditive::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

DEF_STD_CMD_ACL(CmdPrimtiveCompSubtractive)

CmdPrimtiveCompSubtractive::CmdPrimtiveCompSubtractive()
    : Command("PartDesign_CompPrimitiveSubtractive")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Primitive — Remove Material");
    sToolTipText = QT_TR_NOOP("Removes a parametric primitive from the active Body");
    sWhatsThis = "PartDesign_CompPrimitiveSubtractive";
    sStatusTip = sToolTipText;
    eType = ForEdit;
}

void CmdPrimtiveCompSubtractive::activated(int iMsg)
{
    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);
    const char* shapeType = primitiveIntToName(iMsg);

    if (!pcActiveBody || !shapeType) {
        return;
    }

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    if (pcAction && iMsg >= 0 && iMsg < pcAction->actions().size()) {
        pcAction->setIcon(pcAction->actions().at(iMsg)->icon());
    }

    // check if we already have a feature as subtractive ones work only if we have
    // something to subtract from.
    App::DocumentObject* prevSolid = pcActiveBody->Tip.getValue();
    if (!hasUsableSolidTip(pcActiveBody)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No solid body result"),
            QObject::tr(
                "A subtractive primitive requires the active Body to end in a valid solid result."
            )
        );
        return;
    }

    auto FeatName(getUniqueObjectName(shapeType, pcActiveBody));

    openCommand(std::string("Make subtractive ") + shapeType);
    const ExactBodyIdentity bodyIdentity = exactBodyIdentity(pcActiveBody);
    auto* Feat = createPrimitiveExact(
        pcActiveBody,
        std::string("PartDesign::Subtractive") + shapeType,
        FeatName,
        "newObject",
        false
    );
    const long primitiveId = Feat->getID();
    const std::string exactPrimitiveName = Feat->getNameInDocument();
    pcActiveBody = resolveExactBody(bodyIdentity);
    Feat = resolveExactPrimitive(
        bodyIdentity,
        primitiveId,
        exactPrimitiveName
    );
    if (!pcActiveBody || !Feat
        || PartDesign::Body::findBodyOf(Feat) != pcActiveBody) {
        abortCommand();
        return;
    }
    Gui::Command::updateActive();

    copyVisual(Feat, "ShapeAppearance", prevSolid);
    copyVisual(Feat, "LineColor", prevSolid);
    copyVisual(Feat, "PointColor", prevSolid);
    copyVisual(Feat, "Transparency", prevSolid);
    copyVisual(Feat, "DisplayMode", prevSolid);

    if (isActiveObjectValid()) {
        // TODO  (2015-08-05, Fat-Zer)
        FCMD_OBJ_HIDE(prevSolid);
    }

    if (!PartDesignGui::setEdit(Feat, pcActiveBody)) {
        abortCommand();
    }
}

Gui::Action* CmdPrimtiveCompSubtractive::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* p1 = pcAction->addAction(QString());
    p1->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveBox"));
    p1->setObjectName(QStringLiteral("PartDesign_SubtractiveBox"));
    p1->setWhatsThis(QStringLiteral("PartDesign_SubtractiveBox"));
    QAction* p2 = pcAction->addAction(QString());
    p2->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveCylinder"));
    p2->setObjectName(QStringLiteral("PartDesign_SubtractiveCylinder"));
    p2->setWhatsThis(QStringLiteral("PartDesign_SubtractiveCylinder"));
    QAction* p3 = pcAction->addAction(QString());
    p3->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveSphere"));
    p3->setObjectName(QStringLiteral("PartDesign_SubtractiveSphere"));
    p3->setWhatsThis(QStringLiteral("PartDesign_SubtractiveSphere"));
    QAction* p4 = pcAction->addAction(QString());
    p4->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveCone"));
    p4->setObjectName(QStringLiteral("PartDesign_SubtractiveCone"));
    p4->setWhatsThis(QStringLiteral("PartDesign_SubtractiveCone"));
    QAction* p5 = pcAction->addAction(QString());
    p5->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveEllipsoid"));
    p5->setObjectName(QStringLiteral("PartDesign_SubtractiveEllipsoid"));
    p5->setWhatsThis(QStringLiteral("PartDesign_SubtractiveEllipsoid"));
    QAction* p6 = pcAction->addAction(QString());
    p6->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveTorus"));
    p6->setObjectName(QStringLiteral("PartDesign_SubtractiveTorus"));
    p6->setWhatsThis(QStringLiteral("PartDesign_SubtractiveTorus"));
    QAction* p7 = pcAction->addAction(QString());
    p7->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractivePrism"));
    p7->setObjectName(QStringLiteral("PartDesign_SubtractivePrism"));
    p7->setWhatsThis(QStringLiteral("PartDesign_SubtractivePrism"));
    QAction* p8 = pcAction->addAction(QString());
    p8->setIcon(Gui::BitmapFactory().iconFromTheme("PartDesign_SubtractiveWedge"));
    p8->setObjectName(QStringLiteral("PartDesign_SubtractiveWedge"));
    p8->setWhatsThis(QStringLiteral("PartDesign_SubtractiveWedge"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(p1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdPrimtiveCompSubtractive::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    QAction* arc1 = a[0];
    arc1->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Box — Remove Material"));
    arc1->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveSubtractive",
            "Creates a subtractive box by its width, height and length"
        )
    );
    arc1->setStatusTip(arc1->toolTip());
    QAction* arc2 = a[1];
    arc2->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Cylinder — Remove Material"));
    arc2->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveSubtractive",
            "Creates a subtractive cylinder by its radius, height and angle"
        )
    );
    arc2->setStatusTip(arc2->toolTip());
    QAction* arc3 = a[2];
    arc3->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Sphere — Remove Material"));
    arc3->setToolTip(
        QApplication::translate(
            "PartDesign_CompPrimitiveSubtractive",
            "Creates a subtractive sphere by its radius and various angles"
        )
    );
    arc3->setStatusTip(arc3->toolTip());
    QAction* arc4 = a[3];
    arc4->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Cone — Remove Material"));
    arc4->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveSubtractive", "Creates a subtractive cone")
    );
    arc4->setStatusTip(arc4->toolTip());
    QAction* arc5 = a[4];
    arc5->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Ellipsoid — Remove Material"));
    arc5->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveSubtractive", "Creates a subtractive ellipsoid")
    );
    arc5->setStatusTip(arc5->toolTip());
    QAction* arc6 = a[5];
    arc6->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Torus — Remove Material"));
    arc6->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveSubtractive", "Creates a subtractive torus")
    );
    arc6->setStatusTip(arc6->toolTip());
    QAction* arc7 = a[6];
    arc7->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Prism — Remove Material"));
    arc7->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveSubtractive", "Creates a subtractive prism")
    );
    arc7->setStatusTip(arc7->toolTip());
    QAction* arc8 = a[7];
    arc8->setText(QApplication::translate("CmdPrimtiveCompSubtractive", "Wedge — Remove Material"));
    arc8->setToolTip(
        QApplication::translate("PartDesign_CompPrimitiveSubtractive", "Creates a subtractive wedge")
    );
    arc8->setStatusTip(arc8->toolTip());
}

bool CmdPrimtiveCompSubtractive::isActive()
{
    if (!PartDesignGui::canStartModelingCommand()) {
        return false;
    }
    return hasUsableSolidTip(PartDesignGui::getBody(false));
}

//===========================================================================
// Initialization
//===========================================================================

void CreatePartDesignPrimitiveCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdPrimtiveCompAdditive);
    rcCmdMgr.addCommand(new CmdPrimtiveCompSubtractive);
}
