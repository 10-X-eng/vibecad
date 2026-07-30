// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
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
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>

#include <Base/Interpreter.h>

#include <Mod/Assembly/App/AssemblyObject.h>
#include <Mod/Assembly/App/AssemblyUtils.h>
#include <Mod/Assembly/App/BomObject.h>

#include "ViewProviderBom.h"

using namespace AssemblyGui;

PROPERTY_SOURCE(AssemblyGui::ViewProviderBom, SpreadsheetGui::ViewProviderSheet)

ViewProviderBom::ViewProviderBom()
{}

ViewProviderBom::~ViewProviderBom() = default;

QIcon ViewProviderBom::getIcon() const
{
    return Gui::BitmapFactory().pixmap("Assembly_BillOfMaterials.svg");
}

bool ViewProviderBom::doubleClicked()
{
    auto* bom = dynamic_cast<Assembly::BomObject*>(getObject());
    if (!bom || !Assembly::isTimelineOperationActive(bom)) {
        return false;
    }
    if (auto* assembly = bom->getAssembly();
        assembly
        && !Assembly::isTimelineOperationActive(assembly)) {
        return false;
    }

    std::string obj_name = getObject()->getNameInDocument();
    std::string doc_name = getObject()->getDocument()->getName();

    std::string pythonCommand = "import CommandCreateBom\n"
                                "obj = App.getDocument('"
        + doc_name + "').getObject('" + obj_name
        + "')\n"
          "panel = CommandCreateBom.TaskAssemblyCreateBom("
          "obj, existing_transaction_id="
          "obj.Document.getBookedTransactionID())\n"
          "dialog = Gui.Control.showDialog(panel, panel.gui_doc)\n"
          "if dialog is not None:\n"
          "    dialog.setAutoCloseOnDeletedDocument(True)\n"
          "    dialog.setDocumentName(obj.Document.Name)";

    Gui::Command::runCommand(Gui::Command::App, pythonCommand.c_str());

    return true;
}
