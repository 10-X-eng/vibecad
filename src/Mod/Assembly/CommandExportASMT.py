# SPDX-License-Identifier: LGPL-2.1-or-later
# /**************************************************************************
#                                                                           *
#    Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
#                                                                           *
#    This file is part of FreeCAD.                                          *
#                                                                           *
#    FreeCAD is free software: you can redistribute it and/or modify it     *
#    under the terms of the GNU Lesser General Public License as            *
#    published by the Free Software Foundation, either version 2.1 of the   *
#    License, or (at your option) any later version.                        *
#                                                                           *
#    FreeCAD is distributed in the hope that it will be useful, but         *
#    WITHOUT ANY WARRANTY; without even the implied warranty of             *
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
#    Lesser General Public License for more details.                        *
#                                                                           *
#    You should have received a copy of the GNU Lesser General Public       *
#    License along with FreeCAD. If not, see                                *
#    <https://www.gnu.org/licenses/>.                                       *
#                                                                           *
# **************************************************************************/

import FreeCAD as App
import UtilsAssembly

from PySide.QtCore import QT_TRANSLATE_NOOP
from PySide.QtWidgets import QFileDialog

if App.GuiUp:
    import FreeCADGui as Gui


__title__ = "Assembly Command Create Assembly"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandExportASMT:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_ExportASMT",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_ExportASMT", "Export ASMT File"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_ExportASMT",
                "Export currently active assembly as a ASMT file.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return UtilsAssembly.isAssemblyCommandActive()

    def Activated(self):
        if not self.IsActive():
            return
        document = App.ActiveDocument
        if not document:
            return

        assembly = UtilsAssembly.activeAssembly()
        if not assembly:
            return
        document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        assembly_identity = (
            str(assembly.Name),
            int(assembly.ID),
            assembly,
        )

        # Prompt the user for a file location and name
        defaultFileName = document.Name + ".asmt"
        filePath, _ = QFileDialog.getSaveFileName(
            None,
            "Save ASMT File",
            defaultFileName,
            "ASMT Files (*.asmt);;All Files (*)",
        )

        if (
            filePath
            and UtilsAssembly._document_is_open(document)
            and str(getattr(document, "Uid", "") or "")
            == document_uid
            and document.getObject(assembly_identity[0])
            is assembly_identity[2]
            and int(assembly_identity[2].ID)
            == assembly_identity[1]
            and UtilsAssembly.isTimelineOperationActive(assembly)
        ):
            Gui.doCommand(
                f"document = App.getDocument({str(document.Name)!r})\n"
                f"assembly = document.getObject({str(assembly.Name)!r})\n"
                f"assembly.exportAsASMT({str(filePath)!r})"
            )


if App.GuiUp:
    Gui.addCommand("Assembly_ExportASMT", CommandExportASMT())
