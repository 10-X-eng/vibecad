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

from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets

import UtilsAssembly
import Preferences

translate = App.Qt.translate

__title__ = "Assembly Command Create Assembly"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandCreateAssembly:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Geoassembly",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateAssembly", "New Assembly"),
            "Accel": "A",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateAssembly",
                "Creates an assembly object in the current document, or in the current active assembly (if any). Limit of one root assembly per file.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        document = App.ActiveDocument
        if (
            document is None
            or Gui.Control.activeDialog()
            or document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
        ):
            return False

        if Preferences.preferences().GetBool("EnforceOneAssemblyRule", True):
            activeAssembly = UtilsAssembly.activeAssembly()

            if UtilsAssembly.isThereOneRootAssembly() and not activeAssembly:
                return False

        return True

    def Activated(self):
        if not self.IsActive():
            return

        document = App.ActiveDocument
        activeAssembly = UtilsAssembly.activeAssembly()
        document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        gui_document = Gui.getDocument(document.Name)
        if gui_document is None:
            return
        transaction = gui_document.openCommand("New assembly")
        if transaction == 0:
            return

        try:
            Gui.addModule("UtilsAssembly")
            if activeAssembly:
                commands = (
                    f"document = App.getDocument({str(document.Name)!r})\n"
                    f"activeAssembly = document.getObject("
                    f"{str(activeAssembly.Name)!r})\n"
                    'assembly = activeAssembly.newObject("Assembly::AssemblyObject", "Assembly")\n'
                )
            else:
                commands = (
                    f"document = App.getDocument({str(document.Name)!r})\n"
                    'assembly = document.addObject("Assembly::AssemblyObject", "Assembly")\n'
                )

            commands = commands + 'assembly.Type = "Assembly"\n'
            commands = commands + 'assembly.newObject("Assembly::JointGroup", "Joints")'
            Gui.doCommand(commands)
            created_assembly = Gui.doCommandEval("assembly")
            if (
                not UtilsAssembly._document_is_open(document)
                or str(getattr(document, "Uid", "") or "")
                != document_uid
                or created_assembly is None
                or created_assembly.Document is not document
                or document.getObject(created_assembly.Name)
                is not created_assembly
                or int(created_assembly.ID) <= 0
                or (
                    activeAssembly is not None
                    and (
                        document.getObject(activeAssembly.Name)
                        is not activeAssembly
                        or not UtilsAssembly.isTimelineOperationActive(
                            activeAssembly
                        )
                        or not activeAssembly.hasObject(
                            created_assembly,
                            True,
                        )
                    )
                )
                or document.getBookedTransactionID() != transaction
            ):
                raise RuntimeError(
                    "The new-assembly command lost its exact document state"
                )
            created_name = str(created_assembly.Name)
            created_id = int(created_assembly.ID)
            App.closeActiveTransaction(False, transaction)
        except Exception:
            if document.getBookedTransactionID() == transaction:
                App.closeActiveTransaction(True, transaction)
            raise

        if document.getBookedTransactionID() == transaction:
            App.closeActiveTransaction(True, transaction)
            raise RuntimeError("Could not commit the new assembly")
        committed_assembly = document.getObject(created_name)
        if (
            committed_assembly is not created_assembly
            or int(committed_assembly.ID) != created_id
            or not UtilsAssembly.isTimelineOperationActive(
                committed_assembly
            )
        ):
            raise RuntimeError(
                "The committed assembly changed identity"
            )

        # Assembly edit mode is persistent interaction state. Enter it only
        # after the finite model-creation transaction is durably closed.
        if not activeAssembly:
            Gui.doCommandGui(
                f"Gui.getDocument({str(document.Name)!r}).setEdit("
                f"{created_name!r})"
            )


class ActivateAssemblyTaskPanel:
    """A basic TaskPanel to select an assembly to activate."""

    def __init__(self, assemblies):
        self.assemblies = assemblies
        self.document = assemblies[0].Document if assemblies else None
        if self.document is None or any(
            assembly.Document is not self.document
            for assembly in assemblies
        ):
            raise RuntimeError(
                "Activate Assembly requires assemblies from one document"
            )
        self.gui_document = Gui.getDocument(self.document.Name)
        if self.gui_document is None:
            raise RuntimeError("Activate Assembly has no GUI document")
        self.document_uid = str(
            getattr(self.document, "Uid", "") or ""
        )
        self.assembly_identities = tuple(
            (str(assembly.Name), int(assembly.ID))
            for assembly in assemblies
        )
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("Assembly_ActivateAssembly", "Activate Assembly"))

        layout = QtWidgets.QVBoxLayout(self.form)
        label = QtWidgets.QLabel(
            translate("Assembly_ActivateAssembly", "Select an assembly to activate:")
        )
        self.combo = QtWidgets.QComboBox()

        for asm in self.assemblies:
            # Store the user-friendly Label for display, and the internal Name for activation
            self.combo.addItem(asm.Label, asm.Name)

        layout.addWidget(label)
        layout.addWidget(self.combo)

    def accept(self):
        """Called when the user clicks OK."""
        selected_index = self.combo.currentIndex()
        selected_identity = (
            self.assembly_identities[selected_index]
            if 0 <= selected_index < len(self.assembly_identities)
            else None
        )
        selected = (
            self.document.getObject(selected_identity[0])
            if (
                selected_identity is not None
                and UtilsAssembly._document_is_open(self.document)
                and str(
                    getattr(self.document, "Uid", "") or ""
                )
                == self.document_uid
            )
            else None
        )
        if (
            selected is not None
            and int(selected.ID) == selected_identity[1]
            and selected.isDerivedFrom("Assembly::AssemblyObject")
            and UtilsAssembly.isTimelineOperationActive(selected)
        ):
            Gui.doCommand(
                f"Gui.getDocument({str(self.document.Name)!r}).setEdit("
                f"{str(selected.Name)!r})"
            )
        return True

    def reject(self):
        """Called when the user clicks Cancel or closes the panel."""
        return True


class CommandActivateAssembly:
    def __init__(self):
        self.task_panel = None

    def GetResources(self):
        return {
            "Pixmap": "Assembly_ActivateAssembly",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_ActivateAssembly", "Activate Assembly"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_ActivateAssembly", "Sets an assembly as the active one for editing."
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        document = App.ActiveDocument
        if (
            Gui.Control.activeDialog()
            or document is None
            or document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
        ):
            return False

        # Command is only active if no assembly is currently active
        if UtilsAssembly.activeAssembly() is not None:
            return False

        # And if there is at least one assembly in the document to activate
        for obj in document.Objects:
            if (
                obj.isDerivedFrom("Assembly::AssemblyObject")
                and UtilsAssembly.isTimelineOperationActive(obj)
            ):
                return True

        return False

    def Activated(self):
        if not self.IsActive():
            return

        doc = App.ActiveDocument
        assemblies = [
            obj
            for obj in doc.Objects
            if (
                obj.isDerivedFrom("Assembly::AssemblyObject")
                and UtilsAssembly.isTimelineOperationActive(obj)
            )
        ]

        if len(assemblies) == 1:
            # If there's only one, activate it directly without showing a dialog
            Gui.doCommand(
                f"Gui.getDocument({str(doc.Name)!r}).setEdit("
                f"{str(assemblies[0].Name)!r})"
            )
        elif len(assemblies) > 1:
            # If there are multiple, show a task panel to let the user choose
            self.task_panel = ActivateAssemblyTaskPanel(assemblies)
            dialog = Gui.Control.showDialog(
                self.task_panel,
                self.task_panel.gui_document,
            )
            if dialog is not None:
                dialog.setAutoCloseOnDeletedDocument(True)
                dialog.setDocumentName(doc.Name)


if App.GuiUp:
    Gui.addCommand("Assembly_CreateAssembly", CommandCreateAssembly())
    Gui.addCommand("Assembly_ActivateAssembly", CommandActivateAssembly())
