// SPDX - License - Identifier: LGPL - 2.1 - or -later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2025 Pierre-Louis Boyer                                  *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/

#include <vector>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Gui/Application.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Tree.h>

#include <Mod/Assembly/App/AssemblyLink.h>
#include <Mod/Assembly/App/AssemblyObject.h>
#include <Mod/Assembly/App/AssemblyUtils.h>

#include "Commands.h"
#include "ViewProviderAssembly.h"


using namespace Assembly;
using namespace AssemblyGui;

// Helper function to get the active AssemblyObject in edit mode
static AssemblyObject* getActiveAssembly()
{
    Gui::Document* doc = Gui::Application::Instance->activeDocument();
    if (!doc) {
        return nullptr;
    }

    auto* vp = doc->getInEdit();
    if (auto* assemblyVP = freecad_cast<ViewProviderAssembly*>(vp)) {
        auto* assembly = assemblyVP->getObject<AssemblyObject>();
        return Assembly::isTimelineOperationActive(assembly)
            ? assembly
            : nullptr;
    }

    return nullptr;
}

void selectObjects(const std::vector<App::DocumentObject*>& objectsToSelect)
{
    if (objectsToSelect.empty()) {
        return;
    }

    auto* document = objectsToSelect.front()
        ? objectsToSelect.front()->getDocument()
        : nullptr;
    if (!document) {
        return;
    }
    Gui::Selection().clearSelection(document->getName());
    for (App::DocumentObject* obj : objectsToSelect) {
        if (obj && obj->getDocument() == document
            && Assembly::isTimelineOperationActive(obj)) {
            Gui::Selection().addSelection(
                document->getName(),
                obj->getNameInDocument()
            );
        }
    }
}

bool selectionContainsAssemblyComponent(
    AssemblyObject* assembly
)
{
    if (!Assembly::isTimelineOperationActive(assembly)) {
        return false;
    }
    auto* document = assembly->getDocument();
    for (auto& selection : Gui::Selection().getSelectionEx(
             "",
             App::DocumentObject::getClassTypeId(),
             Gui::ResolveMode::NoResolve
         )) {
        auto* root = selection.getObject();
        if (!root || root->getDocument() != document
            || !Assembly::isTimelineOperationActive(root)) {
            continue;
        }
        auto subs = selection.getSubNames();
        if (subs.empty()) {
            subs.emplace_back();
        }
        for (const auto& sub : subs) {
            auto* component =
                getMovingPartFromSel(assembly, root, sub);
            if (Assembly::isTimelineOperationActive(component)) {
                return true;
            }
        }
    }
    return false;
}

void selectObjectsByName(AssemblyObject* assembly, const std::vector<std::string>& names)
{
    if (!assembly || names.empty()) {
        return;
    }

    std::vector<App::DocumentObject*> objectsToSelect;
    App::Document* doc = assembly->getDocument();

    for (const auto& name : names) {
        if (auto* obj = doc->getObject(name.c_str())) {
            if (assembly->hasObject(obj, true)
                && Assembly::isTimelineOperationActive(obj)) {
                objectsToSelect.push_back(obj);
            }
        }
    }

    selectObjects(objectsToSelect);
}

// ================================================================================
// Go to Linked Assembly
// ================================================================================

DEF_STD_CMD_A(CmdAssemblyLinkSelectLinked)

CmdAssemblyLinkSelectLinked::CmdAssemblyLinkSelectLinked()
    : Command("Assembly_LinkSelectLinked")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Go to Linked Assembly");
    sToolTipText = QT_TR_NOOP("Selects the linked assembly and switches to its original document");
    sWhatsThis = "Assembly_LinkSelectLinked";
    sStatusTip = sToolTipText;
    eType = AlterSelection;
    sPixmap = "LinkSelect";
    sAccel = "S, G";
}

void CmdAssemblyLinkSelectLinked::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    std::vector<Gui::SelectionObject> selection = Gui::Selection().getSelectionEx();
    if (selection.size() != 1) {
        return;
    }

    auto* asmLink = dynamic_cast<Assembly::AssemblyLink*>(selection[0].getObject());

    if (!asmLink || !Assembly::isTimelineOperationActive(asmLink)) {
        return;
    }

    // Get the linked object (usually an AssemblyObject in another doc)
    App::DocumentObject* linkedObj = asmLink->getLinkedAssembly();
    if (!linkedObj || !Assembly::isTimelineOperationActive(linkedObj)) {
        return;
    }

    auto* sourceDocument = asmLink->getDocument();
    auto* linkedDocument = linkedObj->getDocument();
    Gui::Selection().clearSelection(sourceDocument->getName());
    if (linkedDocument != sourceDocument) {
        Gui::Selection().clearSelection(linkedDocument->getName());
    }
    Gui::Selection().addSelection(linkedObj->getDocument()->getName(), linkedObj->getNameInDocument());

    // Switch view/tab
    Gui::Document* guiDoc = Gui::Application::Instance->getDocument(linkedObj->getDocument());
    if (guiDoc) {
        // Try to activate the view containing the object
        Gui::ViewProvider* vp = guiDoc->getViewProvider(linkedObj);
        if (auto vpDoc = dynamic_cast<Gui::ViewProviderDocumentObject*>(vp)) {
            guiDoc->setActiveView(vpDoc);
        }
    }
}

bool CmdAssemblyLinkSelectLinked::isActive()
{
    std::vector<Gui::SelectionObject> selection = Gui::Selection().getSelectionEx();
    if (selection.size() != 1) {
        return false;
    }
    auto* link = freecad_cast<AssemblyLink*>(
        selection[0].getObject()
    );
    return Assembly::isTimelineOperationActive(link)
        && Assembly::isTimelineOperationActive(
            link->getLinkedAssembly()
        );
}


// ================================================================================
// Select Conflicting Constraints
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectConflictingConstraints)

CmdAssemblySelectConflictingConstraints::CmdAssemblySelectConflictingConstraints()
    : Command("Assembly_SelectConflictingConstraints")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Conflicting Constraints");
    sToolTipText = QT_TR_NOOP("Selects conflicting joints in the active assembly");
    sWhatsThis = "Assembly_SelectConflictingConstraints";
    sStatusTip = sToolTipText;
    sPixmap = "Assembly_SelectConflictingConstraints";
    eType = ForEdit;
}

void CmdAssemblySelectConflictingConstraints::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    selectObjectsByName(assembly, assembly->getLastConflicting());
}

bool CmdAssemblySelectConflictingConstraints::isActive()
{
    return getActiveAssembly() != nullptr;
}

// ================================================================================
// Select Redundant Constraints
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectRedundantConstraints)

CmdAssemblySelectRedundantConstraints::CmdAssemblySelectRedundantConstraints()
    : Command("Assembly_SelectRedundantConstraints")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Redundant Constraints");
    sToolTipText = QT_TR_NOOP("Selects redundant joints in the active assembly");
    sWhatsThis = "Assembly_SelectRedundantConstraints";
    sStatusTip = sToolTipText;
    sPixmap = "Assembly_SelectRedundantConstraints";
    eType = ForEdit;
}

void CmdAssemblySelectRedundantConstraints::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    selectObjectsByName(assembly, assembly->getLastRedundant());
}

bool CmdAssemblySelectRedundantConstraints::isActive()
{
    return getActiveAssembly() != nullptr;
}

// ================================================================================
// Select Partially Redundant Constraints
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectPartiallyRedundantConstraints)

CmdAssemblySelectPartiallyRedundantConstraints::CmdAssemblySelectPartiallyRedundantConstraints()
    : Command("Assembly_SelectPartiallyRedundantConstraints")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Partially Redundant Constraints");
    sToolTipText = QT_TR_NOOP("Selects partially redundant joints in the active assembly");
    sWhatsThis = "Assembly_SelectPartiallyRedundantConstraints";
    sStatusTip = sToolTipText;
    sPixmap = "Assembly_SelectPartiallyRedundantConstraints";
    eType = ForEdit;
}

void CmdAssemblySelectPartiallyRedundantConstraints::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    selectObjectsByName(assembly, assembly->getLastPartiallyRedundant());
}

bool CmdAssemblySelectPartiallyRedundantConstraints::isActive()
{
    return getActiveAssembly() != nullptr;
}

// ================================================================================
// Select Malformed Constraints
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectMalformedConstraints)

CmdAssemblySelectMalformedConstraints::CmdAssemblySelectMalformedConstraints()
    : Command("Assembly_SelectMalformedConstraints")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Malformed Constraints");
    sToolTipText = QT_TR_NOOP("Selects malformed joints in the active assembly");
    sWhatsThis = "Assembly_SelectMalformedConstraints";
    sStatusTip = sToolTipText;
    sPixmap = "Assembly_SelectMalformedConstraints";
    eType = ForEdit;
}

void CmdAssemblySelectMalformedConstraints::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    selectObjectsByName(assembly, assembly->getLastMalformed());
}

bool CmdAssemblySelectMalformedConstraints::isActive()
{
    return getActiveAssembly() != nullptr;
}


// ================================================================================
// Select Components with Degrees of Freedom
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectComponentsWithDoFs)

CmdAssemblySelectComponentsWithDoFs::CmdAssemblySelectComponentsWithDoFs()
    : Command("Assembly_SelectComponentsWithDoFs")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Components With DoFs");
    sToolTipText = QT_TR_NOOP("Selects unconstrained components in the active assembly");
    sWhatsThis = "Assembly_SelectComponentsWithDoFs";
    sStatusTip = sToolTipText;
    eType = ForEdit;
}

void CmdAssemblySelectComponentsWithDoFs::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    std::vector<App::DocumentObject*> objectsToSelect;
    std::vector<App::DocumentObject*> allParts = getAssemblyComponents(assembly);

    // Iterate through all collected parts and check their connectivity
    for (App::DocumentObject* part : allParts) {
        if (!assembly->isPartConnected(part)) {
            objectsToSelect.push_back(part);
        }
    }

    selectObjects(objectsToSelect);
}

bool CmdAssemblySelectComponentsWithDoFs::isActive()
{
    return getActiveAssembly() != nullptr;
}

// ================================================================================
// Select Joints of Component
// ================================================================================

DEF_STD_CMD_A(CmdAssemblySelectJointsOfComponent)

CmdAssemblySelectJointsOfComponent::CmdAssemblySelectJointsOfComponent()
    : Command("Assembly_SelectJointsOfComponent")
{
    sGroup = QT_TR_NOOP("Assembly");
    sMenuText = QT_TR_NOOP("Select Component Joints");
    sToolTipText = QT_TR_NOOP("Selects all joints referencing the selected component");
    sWhatsThis = "Assembly_SelectJointsOfComponent";
    sStatusTip = sToolTipText;
    sPixmap = "Assembly_SelectJointsOfComponent";
    eType = ForEdit;
}

void CmdAssemblySelectJointsOfComponent::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    AssemblyObject* assembly = getActiveAssembly();
    if (!assembly) {
        return;
    }

    auto selection = Gui::Selection().getSelectionEx(
        "",
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    if (selection.empty()) {
        return;
    }

    std::set<App::DocumentObject*> components;

    for (auto& sel : selection) {
        auto* root = sel.getObject();
        if (!root
            || root->getDocument() != assembly->getDocument()
            || !Assembly::isTimelineOperationActive(root)) {
            continue;
        }
        auto subs = sel.getSubNames();
        if (subs.empty()) {
            subs.emplace_back();
        }
        for (const auto& sub : subs) {
            if (App::DocumentObject* comp =
                    getMovingPartFromSel(assembly, root, sub);
                Assembly::isTimelineOperationActive(comp)) {
                components.insert(comp);
            }
        }
    }

    if (components.empty()) {
        return;
    }

    std::vector<App::DocumentObject*> jointsToSelect;
    for (auto* comp : components) {
        std::vector<App::DocumentObject*> partJoints = assembly->getJointsOfPart(comp);
        jointsToSelect.insert(jointsToSelect.end(), partJoints.begin(), partJoints.end());
    }

    selectObjects(jointsToSelect);
}

bool CmdAssemblySelectJointsOfComponent::isActive()
{
    return selectionContainsAssemblyComponent(
        getActiveAssembly()
    );
}


// ================================================================================
// Command Creation
// ================================================================================

void AssemblyGui::CreateAssemblyCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdAssemblyLinkSelectLinked());
    rcCmdMgr.addCommand(new CmdAssemblySelectConflictingConstraints());
    rcCmdMgr.addCommand(new CmdAssemblySelectRedundantConstraints());
    rcCmdMgr.addCommand(new CmdAssemblySelectPartiallyRedundantConstraints());
    rcCmdMgr.addCommand(new CmdAssemblySelectMalformedConstraints());
    rcCmdMgr.addCommand(new CmdAssemblySelectComponentsWithDoFs());
    rcCmdMgr.addCommand(new CmdAssemblySelectJointsOfComponent());
}
