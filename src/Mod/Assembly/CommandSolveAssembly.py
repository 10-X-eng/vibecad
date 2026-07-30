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

import os
import FreeCAD as App

from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets

import UtilsAssembly
import Assembly_rc
from VibeCADNativeTransaction import _OwnedDocumentTransaction

__title__ = "Assembly Command to Solve Assembly"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandSolveAssembly:
    def __init__(self):
        pass

    def GetResources(self):

        return {
            "Pixmap": "Assembly_SolveAssembly",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_SolveAssembly", "Solve Assembly"),
            "Accel": "Z",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_SolveAssembly",
                "Solves the currently active assembly.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return UtilsAssembly.isAssemblyCommandActive()

    def Activated(self):
        if not self.IsActive():
            return

        assembly = UtilsAssembly.activeAssembly()
        if not assembly:
            return

        document = assembly.Document
        document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        assembly_identity = (
            str(assembly.Name),
            int(assembly.ID),
            assembly,
        )
        transaction = _OwnedDocumentTransaction(
            document,
            "Solve assembly",
        )
        try:
            if (
                not UtilsAssembly._document_is_open(document)
                or str(getattr(document, "Uid", "") or "")
                != document_uid
                or document.getObject(assembly_identity[0])
                is not assembly_identity[2]
                or int(assembly_identity[2].ID)
                != assembly_identity[1]
                or not UtilsAssembly.isTimelineOperationActive(
                    assembly
                )
            ):
                raise RuntimeError(
                    "The exact active assembly changed before solving"
                )
            solver_code = int(assembly.solve(False))
            document.recompute()
            if (
                document.getObject(assembly_identity[0])
                is not assembly_identity[2]
                or int(assembly_identity[2].ID)
                != assembly_identity[1]
                or not UtilsAssembly.isTimelineOperationActive(
                    assembly
                )
            ):
                raise RuntimeError(
                    "The assembly changed identity while solving"
                )
            if solver_code != 0 or not assembly.isValid():
                diagnostics = assembly.getSolverDiagnostics()
                message = str(
                    diagnostics.get("solver_message")
                    or f"native solver returned {solver_code}"
                )
                raise RuntimeError(f"Assembly solve failed: {message}")
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if App.GuiUp:
    Gui.addCommand("Assembly_SolveAssembly", CommandSolveAssembly())
