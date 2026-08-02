// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2002 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Part.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MDIView.h>
#include <Mod/Part/App/FeaturePartBox.h>
#include <Mod/Part/App/PrimitiveFeature.h>


//===========================================================================
// Utils
//===========================================================================
namespace
{
void autoGroupObject(App::DocumentObject* object)
{
    auto* activeView = Gui::Application::Instance->activeView();
    auto* activePart = activeView ? activeView->getActiveObject<App::Part*>("part") : nullptr;
    if (object && activePart && activePart->getDocument() == object->getDocument()) {
        Gui::Command::doCommand(
            Gui::Command::Doc,
            "%s.addObject(%s)",
            Gui::Command::getObjectCmd(activePart).c_str(),
            Gui::Command::getObjectCmd(object).c_str()
        );
    }
    else {
        Gui::Command::doCommand(Gui::Command::Doc, "# Object created at document root.");
    }
}

template<typename Feature>
Feature* createParametricPrimitive(
    App::Document& document,
    const char* typeName,
    const char* objectName,
    const QString& label
)
{
    const QString factory = QStringLiteral("App.getDocument('%1').addObject('%2','%3')")
                                .arg(
                                    QString::fromLatin1(document.getName()),
                                    QString::fromLatin1(typeName),
                                    QString::fromLatin1(objectName)
                                );
    auto* result = dynamic_cast<Feature*>(Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        document,
        factory.toUtf8(),
        Feature::getClassTypeId()
    ));
    if (!result) {
        throw Base::RuntimeError("Parametric primitive returned an incompatible result");
    }

    const auto escapedLabel = Base::InterpreterSingleton::strToPython(label.toUtf8().constData());
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.Label = \"%s\"",
        Gui::Command::getObjectCmd(result).c_str(),
        escapedLabel.c_str()
    );
    autoGroupObject(result);
    return result;
}
}  // namespace

//===========================================================================
// Part_Cylinder
//===========================================================================
DEF_STD_CMD_A(CmdPartCylinder)

CmdPartCylinder::CmdPartCylinder()
    : Command("Part_Cylinder")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Cylinder");
    sToolTipText = QT_TR_NOOP("Creates a solid cylinder");
    sWhatsThis = "Part_Cylinder";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Cylinder_Parametric";
}

void CmdPartCylinder::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const QString label = qApp->translate("CmdPartCylinder", "Cylinder");
    openCommand(label.toUtf8().constData());
    try {
        createParametricPrimitive<Part::Cylinder>(*document, "Part::Cylinder", "Cylinder", label);
        commitCommand();
    }
    catch (Base::Exception& error) {
        abortCommand();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand();
        throw;
    }
    updateDocument(document);
    runCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
}

bool CmdPartCylinder::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}

//===========================================================================
// Part_Box
//===========================================================================
DEF_STD_CMD_A(CmdPartBox)

CmdPartBox::CmdPartBox()
    : Command("Part_Box")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Cube");
    sToolTipText = QT_TR_NOOP("Creates a solid cube");
    sWhatsThis = "Part_Box";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Box_Parametric";
}

void CmdPartBox::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const QString label = qApp->translate("CmdPartBox", "Cube");
    openCommand(label.toUtf8().constData());
    try {
        createParametricPrimitive<Part::Box>(*document, "Part::Box", "Box", label);
        commitCommand();
    }
    catch (Base::Exception& error) {
        abortCommand();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand();
        throw;
    }
    updateDocument(document);
    runCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
}

bool CmdPartBox::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}

//===========================================================================
// Part_Sphere
//===========================================================================
DEF_STD_CMD_A(CmdPartSphere)

CmdPartSphere::CmdPartSphere()
    : Command("Part_Sphere")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Sphere");
    sToolTipText = QT_TR_NOOP("Creates a solid sphere");
    sWhatsThis = "Part_Sphere";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Sphere_Parametric";
}

void CmdPartSphere::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const QString label = qApp->translate("CmdPartSphere", "Sphere");
    openCommand(label.toUtf8().constData());
    try {
        createParametricPrimitive<Part::Sphere>(*document, "Part::Sphere", "Sphere", label);
        commitCommand();
    }
    catch (Base::Exception& error) {
        abortCommand();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand();
        throw;
    }
    updateDocument(document);
    runCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
}

bool CmdPartSphere::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}

//===========================================================================
// Part_Cone
//===========================================================================
DEF_STD_CMD_A(CmdPartCone)

CmdPartCone::CmdPartCone()
    : Command("Part_Cone")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Cone");
    sToolTipText = QT_TR_NOOP("Creates a solid cone");
    sWhatsThis = "Part_Cone";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Cone_Parametric";
}

void CmdPartCone::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const QString label = qApp->translate("CmdPartCone", "Cone");
    openCommand(label.toUtf8().constData());
    try {
        createParametricPrimitive<Part::Cone>(*document, "Part::Cone", "Cone", label);
        commitCommand();
    }
    catch (Base::Exception& error) {
        abortCommand();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand();
        throw;
    }
    updateDocument(document);
    runCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
}

bool CmdPartCone::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}

//===========================================================================
// Part_Torus
//===========================================================================
DEF_STD_CMD_A(CmdPartTorus)

CmdPartTorus::CmdPartTorus()
    : Command("Part_Torus")
{
    sAppModule = "Part";
    sGroup = QT_TR_NOOP("Part");
    sMenuText = QT_TR_NOOP("Torus");
    sToolTipText = QT_TR_NOOP("Creates a solid torus");
    sWhatsThis = "Part_Torus";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Torus_Parametric";
}

void CmdPartTorus::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const QString label = qApp->translate("CmdPartTorus", "Torus");
    openCommand(label.toUtf8().constData());
    try {
        createParametricPrimitive<Part::Torus>(*document, "Part::Torus", "Torus", label);
        commitCommand();
    }
    catch (Base::Exception& error) {
        abortCommand();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand();
        throw;
    }
    updateDocument(document);
    runCommand(Gui, "Gui.SendMsgToActiveView(\"ViewFit\")");
}

bool CmdPartTorus::isActive()
{
    if (getActiveGuiDocument()) {
        return true;
    }
    else {
        return false;
    }
}


//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

void CreateParamPartCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdPartCylinder());
    rcCmdMgr.addCommand(new CmdPartBox());
    rcCmdMgr.addCommand(new CmdPartSphere());
    rcCmdMgr.addCommand(new CmdPartCone());
    rcCmdMgr.addCommand(new CmdPartTorus());
}
