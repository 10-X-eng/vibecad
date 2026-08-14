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


#include <algorithm>
#include <vector>

#include <QString>


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

namespace
{

std::string boundedTreeText(const std::string& value, int maximumCharacters = 240)
{
    QString text = QString::fromUtf8(value.c_str()).simplified();
    if (text.size() > maximumCharacters) {
        text = text.left(maximumCharacters - 1) + QString::fromUtf8("…");
    }
    return text.toUtf8().toStdString();
}

}  // namespace

QIcon ViewProviderBom::getIcon() const
{
    return Gui::BitmapFactory().pixmap("Assembly_BillOfMaterials.svg");
}

std::vector<Gui::TreeViewDetail> ViewProviderBom::getTreeViewDetails() const
{
    constexpr std::size_t maxPublishedRows = 256;
    constexpr std::size_t maxScannedRows = 4096;
    constexpr std::size_t maxColumns = 32;

    const auto* bom = dynamic_cast<const Assembly::BomObject*>(getObject());
    if (!bom) {
        return {};
    }
    const auto& allColumns = bom->columnsNames.getValues();
    const std::size_t columnCount = std::min(allColumns.size(), maxColumns);
    if (columnCount == 0) {
        return {{"empty", "No BOM columns", {},
                 "This bill of materials has no configured columns.",
                 "Assembly_BillOfMaterials"}};
    }

    const int nameColumn = bom->getColumnIndex("Name");
    const int quantityColumn = bom->getColumnIndex("Quantity");
    const int indexColumn = bom->getColumnIndex("Index");
    std::vector<Gui::TreeViewDetail> details;
    details.reserve(maxPublishedRows + 1);
    std::size_t rowCount = 0;
    bool scanLimitReached = false;

    // Spreadsheet addresses are zero-based here: row 0 contains headings and
    // native Assembly BOM data is contiguous from row 1.
    for (std::size_t row = 1; row < maxScannedRows + 1; ++row) {
        std::vector<std::string> values;
        values.reserve(columnCount);
        bool anyValue = false;
        for (std::size_t column = 0; column < columnCount; ++column) {
            auto value = boundedTreeText(bom->getText(row, column));
            anyValue = anyValue || !value.empty();
            values.push_back(std::move(value));
        }
        if (!anyValue) {
            break;
        }
        ++rowCount;
        if (rowCount > maxPublishedRows) {
            if (row == maxScannedRows) {
                scanLimitReached = true;
            }
            continue;
        }

        const auto valueAt = [&](int zeroBasedColumn) -> std::string {
            if (zeroBasedColumn < 0
                || static_cast<std::size_t>(zeroBasedColumn) >= values.size()) {
                return {};
            }
            return values[static_cast<std::size_t>(zeroBasedColumn)];
        };
        std::string name = valueAt(nameColumn);
        if (name.empty()) {
            for (const auto& value : values) {
                if (!value.empty()) {
                    name = value;
                    break;
                }
            }
        }
        if (name.empty()) {
            name = "BOM row " + std::to_string(rowCount);
        }
        const std::string index = valueAt(indexColumn);
        const std::string quantity = valueAt(quantityColumn);
        std::string label;
        if (!index.empty()) {
            label = index + ". ";
        }
        label += name;
        if (!quantity.empty()) {
            label += "  ×" + quantity;
        }

        std::string toolTip;
        for (std::size_t column = 0; column < values.size(); ++column) {
            if (values[column].empty()) {
                continue;
            }
            if (!toolTip.empty()) {
                toolTip += '\n';
            }
            toolTip += boundedTreeText(allColumns[column], 80)
                + ": " + values[column];
        }
        details.push_back({
            "row:" + std::to_string(row + 1),
            std::move(label),
            quantity.empty() ? std::string() : "Quantity " + quantity,
            std::move(toolTip),
            "Assembly_BillOfMaterials",
        });
    }

    if (rowCount == 0) {
        details.push_back({"empty", "No BOM rows", {},
                           "This bill of materials contains no part rows.",
                           "Assembly_BillOfMaterials"});
    }
    else if (rowCount > maxPublishedRows) {
        const std::size_t omitted = rowCount - maxPublishedRows;
        details.push_back({
            "truncated",
            QString::fromUtf8("… %1%2 additional BOM rows not shown")
                .arg(static_cast<qulonglong>(omitted))
                .arg(scanLimitReached ? "+" : "")
                .toUtf8()
                .toStdString(),
            {},
            "Open the BOM to inspect the complete table.",
            "Assembly_BillOfMaterials",
        });
    }
    return details;
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
