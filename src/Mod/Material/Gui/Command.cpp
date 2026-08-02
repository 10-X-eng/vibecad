// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2023 David Carter <dcarter@david.carter.ca>             *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#include <QPointer>
#include <memory>
#include <ranges>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>

#include "DlgDisplayPropertiesImp.h"
#include "DlgInspectAppearance.h"
#include "DlgInspectMaterial.h"
#include "DlgMaterialImp.h"
#include "MaterialSave.h"
#include "MaterialsEditor.h"
#include "ModelSelect.h"
#include "SelectionTargetIdentity.h"
#include "TaskMigrateExternal.h"


//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

namespace
{
App::Document* activeMutationDocument()
{
    auto* guiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->activeDocument()
        : nullptr;
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document
        || Gui::Control().activeDialog(document)
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || document->isTransactionLocked()
        || document->transacting()
        || App::GetApplication().getGlobalTransaction()
            != App::NullTransaction) {
        return nullptr;
    }

    const auto selection = Gui::Selection().getCompleteSelection();
    if (selection.empty()
        || std::ranges::any_of(
            selection,
            [document](const auto& selected) {
                if (selected.pDoc != document
                    || selected.pObject == nullptr) {
                    return true;
                }
                const auto target =
                    MatGui::SelectionTargetIdentity::capture(
                        selected.pObject
                    );
                return !target
                    || target->resolveObject() != selected.pObject;
            }
        )) {
        return nullptr;
    }
    return document;
}

bool hasSingleInspectableSelection()
{
    auto* guiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->activeDocument()
        : nullptr;
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document || Gui::Control().activeDialog(document)) {
        return false;
    }

    const auto selection = Gui::Selection().getCompleteSelection();
    if (selection.size() != 1 || selection.front().pDoc != document
        || !selection.front().pObject) {
        return false;
    }
    const auto target = MatGui::SelectionTargetIdentity::capture(
        selection.front().pObject
    );
    return target && target->resolveObject()
        == selection.front().pObject;
}

template<typename Task>
void showExactMaterialTask(App::Document& document)
{
    auto task = std::make_unique<Task>(document);
    auto* taskPointer = task.get();
    Gui::Control().showDialog(taskPointer, &document);
    if (Gui::Control().activeDialog(&document) == taskPointer) {
        task.release();
    }
}
}

//===========================================================================
// Material_Edit
//===========================================================================
DEF_STD_CMD_A(CmdMaterialEdit)

CmdMaterialEdit::CmdMaterialEdit()
    : Command("Material_Edit")
{
    sAppModule = "Material";
    sGroup = QT_TR_NOOP("Material");
    sMenuText = QT_TR_NOOP("Edit");
    sToolTipText = QT_TR_NOOP("Edits material properties");
    sWhatsThis = "Material_Edit";
    sStatusTip = sToolTipText;
    sPixmap = "Material_Edit";
}

void CmdMaterialEdit::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    static QPointer<QDialog> dlg = nullptr;
    if (!dlg) {
        dlg = new MatGui::MaterialsEditor(Gui::getMainWindow());
    }
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    dlg->show();
}

bool CmdMaterialEdit::isActive()
{
    // return (hasActiveDocument() && !Gui::Control().activeDialog());
    return true;
}

//===========================================================================
// Std_SetAppearance
//===========================================================================
DEF_STD_CMD_A(StdCmdSetAppearance)

StdCmdSetAppearance::StdCmdSetAppearance()
    : Command("Std_SetAppearance")
{
    sGroup = "Standard-View";
    sMenuText = QT_TR_NOOP("&Appearance");
    sToolTipText = QT_TR_NOOP("Sets the display properties of the selected object");
    sWhatsThis = "Std_SetAppearance";
    sStatusTip = QT_TR_NOOP("Sets the display properties of the selected object");
    sPixmap = "Std_SetAppearance";
    sAccel = "Ctrl+D";
    eType = Alter3DView;
}

void StdCmdSetAppearance::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = activeMutationDocument();
    if (!document) {
        return;
    }
    try {
        showExactMaterialTask<MatGui::TaskDisplayProperties>(*document);
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not start appearance editor: %s\n",
            error.what()
        );
    }
}

bool StdCmdSetAppearance::isActive()
{
    return activeMutationDocument() != nullptr;
}

//===========================================================================
// Std_SetMaterial
//===========================================================================
DEF_STD_CMD_A(StdCmdSetMaterial)

StdCmdSetMaterial::StdCmdSetMaterial()
    : Command("Std_SetMaterial")
{
    sGroup = "Standard-View";
    sMenuText = QT_TR_NOOP("&Material");
    sToolTipText = QT_TR_NOOP("Sets the material of the selected object");
    sWhatsThis = "Std_SetMaterial";
    sStatusTip = QT_TR_NOOP("Sets the material of the selected object");
    sPixmap = "Material_Edit";
    // sAccel        = "Ctrl+D";
    // eType = Alter3DView;
}

void StdCmdSetMaterial::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = activeMutationDocument();
    if (!document) {
        return;
    }
    try {
        showExactMaterialTask<MatGui::TaskMaterial>(*document);
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not start material editor: %s\n",
            error.what()
        );
    }
}

bool StdCmdSetMaterial::isActive()
{
    return activeMutationDocument() != nullptr;
}

//===========================================================================
// Materials_InspectAppearance
//===========================================================================
DEF_STD_CMD_A(CmdInspectAppearance)

CmdInspectAppearance::CmdInspectAppearance()
    : Command("Materials_InspectAppearance")
{
    sGroup = "Standard-View";
    sMenuText = QT_TR_NOOP("Inspect Appearance");
    sToolTipText = QT_TR_NOOP("Inspects the appearance properties of the selected object");
    sWhatsThis = "Materials_InspectAppearance";
    sStatusTip = QT_TR_NOOP("Inspect the appearance properties of the selected object");
    sPixmap = "preview-rendered";
}

void CmdInspectAppearance::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!hasSingleInspectableSelection()) {
        return;
    }
    Gui::Control().showDialog(new MatGui::TaskInspectAppearance());
}

bool CmdInspectAppearance::isActive()
{
    return hasSingleInspectableSelection();
}

//===========================================================================
// Materials_InspectMaterial
//===========================================================================
DEF_STD_CMD_A(CmdInspectMaterial)

CmdInspectMaterial::CmdInspectMaterial()
    : Command("Materials_InspectMaterial")
{
    sGroup = "Standard-View";
    sMenuText = QT_TR_NOOP("Inspect Material");
    sToolTipText = QT_TR_NOOP("Inspects the material properties of the selected object");
    sWhatsThis = "Materials_InspectMaterial";
    sStatusTip = QT_TR_NOOP("Inspect the material properties of the selected object");
    sPixmap = "Material_Edit";
}

void CmdInspectMaterial::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!hasSingleInspectableSelection()) {
        return;
    }
    Gui::Control().showDialog(new MatGui::TaskInspectMaterial());
}

bool CmdInspectMaterial::isActive()
{
    return hasSingleInspectableSelection();
}

//===========================================================================
// Materials_MigrateToDatabase
//===========================================================================

#if defined(BUILD_MATERIAL_EXTERNAL)
DEF_STD_CMD_A(CmdMigrateToExternal)

CmdMigrateToExternal::CmdMigrateToExternal()
    : Command("Materials_MigrateToExternal")
{
    sGroup = "Standard-View";
    sMenuText = QT_TR_NOOP("Migrate");
    sToolTipText = QT_TR_NOOP("Migrates the materials to the external materials manager");
    sWhatsThis = "Materials_MigrateToDatabase";
    sStatusTip = QT_TR_NOOP("Migrate existing materials to the external materials manager");
    // sPixmap = "Materials_Edit";
}

void CmdMigrateToExternal::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    MatGui::TaskMigrateExternal* dlg = new MatGui::TaskMigrateExternal();
    Gui::Control().showDialog(dlg);
}

bool CmdMigrateToExternal::isActive()
{
    return true;
}
#endif

//---------------------------------------------------------------

void CreateMaterialCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdMaterialEdit());
    rcCmdMgr.addCommand(new StdCmdSetAppearance());
    rcCmdMgr.addCommand(new StdCmdSetMaterial());
    rcCmdMgr.addCommand(new CmdInspectAppearance());
    rcCmdMgr.addCommand(new CmdInspectMaterial());
#if defined(BUILD_MATERIAL_EXTERNAL)
    rcCmdMgr.addCommand(new CmdMigrateToExternal());
#endif
}
