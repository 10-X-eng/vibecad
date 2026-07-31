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
#include <array>
#include <ranges>
#include <sstream>


#include <App/Application.h>
#include <App/Document.h>
#include <App/Part.h>
#include <Base/Exception.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/DesignModel.h>
#include <Mod/PartDesign/App/FeaturePrimitive.h>

#include "DlgActiveBody.h"
#include "Utils.h"
#include "WorkflowManager.h"

using namespace std;

DEF_STD_CMD_ACL(CmdPrimitiveDesign)
DEF_STD_CMD_ACL(CmdPrimtiveCompAdditive)

namespace
{
struct PrimitiveDefinition
{
    const char* typeName;
    const char* label;
    const char* icon;
    const char* description;
};

constexpr std::array<PrimitiveDefinition, 9> primitiveDefinitions {{
    {
        "PartDesign::DesignBox",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Box"),
        "PartDesign_AdditiveBox",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric box from its length, width, and height"
        ),
    },
    {
        "PartDesign::DesignCylinder",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Cylinder"),
        "PartDesign_AdditiveCylinder",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric cylinder from its radius, height, and angle"
        ),
    },
    {
        "PartDesign::DesignSphere",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Sphere"),
        "PartDesign_AdditiveSphere",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric sphere from its radius and angular limits"
        ),
    },
    {
        "PartDesign::DesignCone",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Cone"),
        "PartDesign_AdditiveCone",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric cone from two radii, height, and angle"
        ),
    },
    {
        "PartDesign::DesignEllipsoid",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Ellipsoid"),
        "PartDesign_AdditiveEllipsoid",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric ellipsoid from its radii and angular limits"
        ),
    },
    {
        "PartDesign::DesignTorus",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Torus"),
        "PartDesign_AdditiveTorus",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric torus from its radii and angular limits"
        ),
    },
    {
        "PartDesign::DesignPrism",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Prism"),
        "PartDesign_AdditivePrism",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric regular prism"
        ),
    },
    {
        "PartDesign::DesignWedge",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Wedge"),
        "PartDesign_AdditiveWedge",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric wedge"
        ),
    },
    {
        "PartDesign::DesignTube",
        QT_TRANSLATE_NOOP("CmdPrimitiveDesign", "Tube"),
        "Part_Tube_Parametric",
        QT_TRANSLATE_NOOP(
            "CmdPrimitiveDesign",
            "Creates a parametric hollow tube from its outside radius, inside radius, and height"
        ),
    },
}};

const PrimitiveDefinition* primitiveDefinition(const int index)
{
    if (index < 0
        || static_cast<std::size_t>(index)
            >= primitiveDefinitions.size()) {
        return nullptr;
    }
    return &primitiveDefinitions[static_cast<std::size_t>(index)];
}

const char* primitiveIntToName(const int index)
{
    const auto* definition = primitiveDefinition(index);
    if (!definition) {
        return nullptr;
    }
    constexpr std::string_view prefix = "PartDesign::Design";
    return definition->typeName + prefix.size();
}

struct DesignPrimitiveSelection
{
    App::Document* document {};
    App::Part* destinationComponent {};
    std::vector<PartDesign::Body*> bodies;
    bool valid {true};
};

DesignPrimitiveSelection selectedDesignPrimitiveTargets(
    App::Document* activeDocument
)
{
    DesignPrimitiveSelection result;
    result.document = activeDocument;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        if (auto* component = freecad_cast<App::Part*>(object);
            component && component->Type.getStrValue() == "Component") {
            if (result.destinationComponent
                && result.destinationComponent != component) {
                result.valid = false;
            }
            result.destinationComponent = component;
            continue;
        }

        auto* body = freecad_cast<PartDesign::Body*>(object);
        if (!body) {
            body = freecad_cast<PartDesign::Body*>(
                PartGui::findModelingBody(object)
            );
        }
        if (!body) {
            result.valid = false;
            continue;
        }
        if (std::ranges::find(result.bodies, body)
            == result.bodies.end()) {
            result.bodies.push_back(body);
        }
    }

    if (!result.bodies.empty()) {
        result.destinationComponent = nullptr;
    }
    return result;
}

PartDesign::FeaturePrimitive* createDesignPrimitiveExact(
    App::Document& document,
    const PrimitiveDefinition& definition,
    const std::string& objectName
)
{
    const Base::Type expectedType =
        Base::Type::fromName(definition.typeName);
    if (expectedType.isBad()) {
        throw Base::TypeError(
            "The requested Design primitive type is not registered"
        );
    }

    std::ostringstream expression;
    expression << "App.getDocument('" << document.getName()
               << "').addObject('" << definition.typeName << "','"
               << objectName << "')";
    auto* object = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        document,
        QByteArray(expression.str().c_str()),
        expectedType
    );
    auto* primitive =
        freecad_cast<PartDesign::FeaturePrimitive*>(object);
    if (!primitive
        || !dynamic_cast<PartDesign::DesignOperationProperties*>(
            primitive
        )) {
        throw Base::TypeError(
            "The Design primitive factory returned an incompatible object"
        );
    }
    return primitive;
}

}  // namespace

CmdPrimitiveDesign::CmdPrimitiveDesign()
    : Command("PartDesign_DesignPrimitive")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Primitive");
    sToolTipText = QT_TR_NOOP(
        "Creates a parametric primitive as a new Body or applies it to selected Bodies"
    );
    sWhatsThis = "PartDesign_DesignPrimitive";
    sStatusTip = sToolTipText;
    eType = ForEdit;
}

void CmdPrimitiveDesign::activated(const int iMsg)
{
    const auto* definition = primitiveDefinition(iMsg);
    auto* document = getDocument();
    if (!definition || !document
        || Gui::Control().activeDialog(document)) {
        return;
    }

    const auto selected =
        selectedDesignPrimitiveTargets(document);
    if (!selected.valid || selected.document != document) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Select Bodies or one Component"),
            QObject::tr(
                "Select zero or more Bodies to create one explicit result "
                "per Body, or select one Component as the destination for a "
                "new Body."
            )
        );
        return;
    }

    Gui::ActionGroup* action =
        qobject_cast<Gui::ActionGroup*>(_pcAction);
    if (action && iMsg >= 0
        && iMsg < action->actions().size()) {
        action->setIcon(action->actions().at(iMsg)->icon());
    }

    const std::string primitiveName =
        primitiveIntToName(iMsg);
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Create Primitive")
    );
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        auto* primitive = createDesignPrimitiveExact(
            *document,
            *definition,
            document->getUniqueObjectName(
                primitiveName.c_str()
            )
        );
        primitive->Label.setValue(primitiveName);
        if (selected.bodies.empty()) {
            PartDesign::DesignModel::setOperationTargets(
                *primitive,
                "New Body",
                {},
                selected.destinationComponent
            );
        }
        else {
            PartDesign::DesignModel::setOperationTargets(
                *primitive,
                "Join",
                selected.bodies
            );
        }

        primitive->recomputeFeature();
        primitive->recomputePreview();
        doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            document->getName(),
            primitive->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(document)) {
            throw Base::RuntimeError(
                "The Design primitive task panel did not open"
            );
        }
        Gui::Selection().clearSelection(document->getName());
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

Gui::Action* CmdPrimitiveDesign::createAction()
{
    auto* action =
        new Gui::ActionGroup(this, Gui::getMainWindow());
    action->setDropDownMenu(true);
    applyCommandData(this->className(), action);
    for (const auto& definition : primitiveDefinitions) {
        auto* item = action->addAction(QString());
        item->setIcon(
            Gui::BitmapFactory().iconFromTheme(definition.icon)
        );
        item->setObjectName(
            QString::fromLatin1(definition.typeName)
        );
        item->setWhatsThis(item->objectName());
    }

    _pcAction = action;
    languageChange();
    action->setIcon(action->actions().front()->icon());
    action->setProperty("defaultAction", QVariant(0));
    return action;
}

void CmdPrimitiveDesign::languageChange()
{
    Command::languageChange();
    auto* action =
        qobject_cast<Gui::ActionGroup*>(_pcAction);
    if (!action) {
        return;
    }

    const auto items = action->actions();
    for (std::size_t index = 0;
         index < primitiveDefinitions.size()
         && index < static_cast<std::size_t>(items.size());
         ++index) {
        const auto& definition = primitiveDefinitions[index];
        auto* item = items.at(static_cast<qsizetype>(index));
        item->setText(
            QApplication::translate(
                "CmdPrimitiveDesign",
                definition.label
            )
        );
        item->setToolTip(
            QApplication::translate(
                "CmdPrimitiveDesign",
                definition.description
            )
        );
        item->setStatusTip(item->toolTip());
    }
}

bool CmdPrimitiveDesign::isActive()
{
    return PartDesignGui::canStartModelingCommand();
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

    rcCmdMgr.addCommand(new CmdPrimitiveDesign);
    rcCmdMgr.addCommand(new CmdPrimtiveCompAdditive);
    rcCmdMgr.addCommand(new CmdPrimtiveCompSubtractive);
}
