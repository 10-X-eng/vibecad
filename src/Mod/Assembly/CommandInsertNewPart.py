# SPDX-License-Identifier: LGPL-2.1-or-later
# /**************************************************************************
#                                                                           *
#    Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
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

import re
import os
import FreeCAD as App

from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets
    from PySide.QtGui import QIcon
    from PySide.QtCore import QTimer

import UtilsAssembly
import Preferences
import JointObject

translate = App.Qt.translate

__title__ = "Assembly Command Insert New Part"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandInsertNewPart:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Geofeaturegroup",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_InsertNewPart", "New Part"),
            "Accel": "P",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_InsertNewPart",
                "Insert a new part into the active assembly. The new part's origin can be positioned in the assembly.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return UtilsAssembly.isAssemblyCommandActive()

    def Activated(self):
        if not self.IsActive():
            return
        # Check if document is saved before proceeding
        doc = App.ActiveDocument
        assembly = UtilsAssembly.activeAssembly()
        if doc is None or assembly is None or assembly.Document is not doc:
            return
        if not doc.FileName:
            msgBox = QtWidgets.QMessageBox()
            msgBox.setIcon(QtWidgets.QMessageBox.Warning)
            msgBox.setText(
                translate(
                    "Assembly",
                    "The assembly document must be saved before inserting a new part.",
                )
            )
            msgBox.setWindowTitle(translate("Assembly", "Save Document"))
            saveButton = msgBox.addButton(
                translate("Assembly", "Save"), QtWidgets.QMessageBox.AcceptRole
            )
            msgBox.addButton(QtWidgets.QMessageBox.Cancel)
            msgBox.exec_()
            if msgBox.clickedButton() == saveButton:
                if not Gui.getDocument(doc).saveAs():
                    return
            else:
                return

        panel = TaskAssemblyNewPart(
            document_name=doc.Name,
            assembly_name=assembly.Name,
        )
        dialog = Gui.Control.showDialog(panel, panel.gui_doc)
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(doc.Name)


class TaskAssemblyNewPart(JointObject.TaskAssemblyCreateJoint):
    def __init__(self, document_name=None, assembly_name=None):
        super().__init__(
            0,
            None,
            True,
            provisional_timeline_internal=True,
            document_name=document_name,
            container_name=assembly_name,
        )

        # Retrieve the existing layout of `self.form`
        mainLayout = self.form.layout()

        # Add a name input
        nameLayout = QtWidgets.QHBoxLayout()
        nameLabel = QtWidgets.QLabel(translate("Assembly", "Part name"))
        self.nameEdit = QtWidgets.QLineEdit()
        nameLayout.addWidget(nameLabel)
        nameLayout.addWidget(self.nameEdit)
        mainLayout.addLayout(nameLayout)
        self.nameEdit.setText(translate("Assembly", "Part"))

        # Add a checkbox
        self.createInNewFileCheck = QtWidgets.QCheckBox(
            translate("Assembly", "Create part in new file")
        )
        mainLayout.addWidget(self.createInNewFileCheck)
        self.createInNewFileCheck.setChecked(
            Preferences.preferences().GetBool("PartInNewFile", True)
        )

        # Wrap the joint creation UI in a groupbox
        jointGroupBox = QtWidgets.QGroupBox(translate("Assembly", "Joint new part origin"))
        jointLayout = QtWidgets.QVBoxLayout(jointGroupBox)
        jointLayout.addWidget(self.jForm)
        jointLayout.setContentsMargins(0, 0, 0, 0)
        jointLayout.setSpacing(0)
        mainLayout.addWidget(jointGroupBox)

        self.link = self.assembly.newObject("App::Link", "Link")
        self.link_identity = (
            str(self.link.Name),
            int(self.link.ID),
            self.link,
        )
        self.part_created = False
        self.created_part = None
        self.created_body = None
        self.created_document = None
        # add the link as the first object of the joint
        Gui.Selection.addSelection(
            self.assembly.Document.Name, self.assembly.Name, self.link.Name + "."
        )

    def createPart(self):
        if self.created_part is None:
            partName = self.nameEdit.text()
            newFile = self.createInNewFileCheck.isChecked()

            doc = self.assembly.Document
            if newFile:
                doc = App.newDocument(partName)

            part, body = UtilsAssembly.createPart(partName, doc)

            App.setActiveDocument(self.assembly.Document.Name)

            # Then we need to link the part.
            if newFile and not self._saveCreatedExternalDocument(doc):
                return False

            self.created_part = part
            self.created_body = body
            self.created_document = doc
            self.created_document_uid = str(
                getattr(doc, "Uid", "") or ""
            )
            self.created_part_identity = (
                str(part.Name),
                int(part.ID),
                part,
            )
            self.created_body_identity = (
                str(body.Name),
                int(body.ID),
                body,
            )
        else:
            part = self.created_part
            body = self.created_body
            doc = self.created_document
            if (
                not UtilsAssembly._document_is_open(doc)
                or part.Document is not doc
                or body.Document is not doc
            ):
                raise RuntimeError(
                    "The new assembly part is no longer available"
                )

        self.link.LinkedObject = part
        self.link.touch()

        self.link.Label = part.Label

        # Leave the new component ready for modeling in the document that
        # actually owns it.  An external component body is not a valid active
        # Body for the assembly document's 3D view.
        self.expandLinkManually(self.link)
        self._activateCreatedBody(body)
        self.assembly.Document.recompute()
        return True

    @staticmethod
    def _saveCreatedExternalDocument(document, save_callback=None):
        """Save one newly-created external component or discard it cleanly."""

        gui_document = Gui.getDocument(document.Name)
        if gui_document is None:
            raise RuntimeError("The new part has no GUI document")
        if save_callback is None:
            save_callback = gui_document.saveAs

        try:
            saved = bool(save_callback())
        except Exception:
            TaskAssemblyNewPart._discardUnsavedCreatedDocument(document)
            raise

        if saved:
            return True

        TaskAssemblyNewPart._discardUnsavedCreatedDocument(document)
        return False

    @staticmethod
    def _discardUnsavedCreatedDocument(document):
        """Close only the exact unsaved document created by this task."""

        if document is None or str(document.FileName or ""):
            return
        try:
            live_document = App.getDocument(document.Name)
        except (NameError, RuntimeError):
            return
        if live_document is document:
            App.closeDocument(document.Name)

    @staticmethod
    def _activateCreatedBody(body):
        document = body.Document
        gui_document = Gui.getDocument(document.Name)
        if gui_document is None:
            raise RuntimeError("The new part has no GUI document")
        view = gui_document.activeView()
        if view is None:
            raise RuntimeError("The new part has no active 3D view")
        view.setActiveObject("pdbody", body)

    def expandLinkManually(self, link):
        # Should not be necessary
        # This is a workaround of https://github.com/FreeCAD/FreeCAD/issues/17904
        mw = Gui.getMainWindow()
        trees = mw.findChildren(QtGui.QTreeWidget)

        Gui.Selection.addSelection(link)
        for tree in trees:
            for item in tree.selectedItems():
                tree.expandItem(item)

    def accept(self):
        if not self.transaction.owns_current():
            App.Console.PrintError(
                "Could not finalize the new assembly part: "
                "the task no longer owns its exact document transaction\n"
            )
            return False
        try:
            if not self.part_created:
                if not self.createPart():
                    return False
                self.part_created = True

            completed_joint = len(self.refs) == 2
            if len(self.refs) != 2:
                # If the joint is incomplete, keep the new part but discard the
                # provisional joint.
                self.joint.Document.removeObject(self.joint.Name)
            else:
                if not self._referencesRemainUsable():
                    raise RuntimeError(
                        "The new part joint no longer references two active "
                        "components in this exact assembly"
                    )
                JointObject.solveIfAllowed(self.assembly)
                self.joint.Visibility = False

            if (
                not UtilsAssembly._document_is_open(self.doc)
                or str(getattr(self.doc, "Uid", "") or "")
                != self.document_uid
                or self.doc.getObject(self.assembly.Name)
                is not self.assembly
                or not UtilsAssembly.isTimelineOperationActive(
                    self.assembly
                )
                or self.doc.getObject(self.link_identity[0])
                is not self.link_identity[2]
                or int(self.link_identity[2].ID)
                != self.link_identity[1]
                or not UtilsAssembly._document_is_open(
                    self.created_part.Document
                )
                or str(
                    getattr(
                        self.created_part.Document,
                        "Uid",
                        "",
                    )
                    or ""
                )
                != self.created_document_uid
                or self.created_part.Document.getObject(
                    self.created_part_identity[0]
                )
                is not self.created_part_identity[2]
                or int(self.created_part_identity[2].ID)
                != self.created_part_identity[1]
                or not UtilsAssembly.isTimelineOperationActive(
                    self.created_part
                )
                or self.created_body.Document
                is not self.created_part.Document
                or self.created_part.Document.getObject(
                    self.created_body_identity[0]
                )
                is not self.created_body_identity[2]
                or int(self.created_body_identity[2].ID)
                != self.created_body_identity[1]
                or not UtilsAssembly.isTimelineOperationActive(
                    self.created_body
                )
                or not UtilsAssembly.isTimelineOperationActive(
                    self.link
                )
            ):
                raise RuntimeError(
                    "The new Assembly part changed identity before accept"
                )

            if self.created_part.Document is self.doc:
                # The same-document Part definition is the durable object the
                # user edits and reuses. Its initial empty Body and assembly
                # occurrence are the implementation graph of that one New
                # Part operation.
                UtilsAssembly.finalizeNewPartTimeline(
                    self.created_part,
                    self.created_body,
                    self.link,
                )
            else:
                # The definition belongs to its newly saved external
                # document. In the assembly document, the occurrence itself
                # is the public insertion operation.
                UtilsAssembly.finalizeInsertedComponentTimeline(
                    self.link,
                )

            if completed_joint:
                # Promote the exact task-preview identity only after its Part
                # or occurrence is a complete earlier history block.  The
                # role change re-enrolls it at the end of this transaction,
                # where its connector dependencies are valid.
                UtilsAssembly.markTimelineOperationEditor(
                    self.joint,
                    "Assembly_EditHistoryOperation",
                )
                self.doc.finalizeProvisionalTimelineOperationBlock(
                    self.joint,
                    [self.joint],
                )

                # Record only the accepted object and its final metadata.  A
                # replay must never persist the task-preview classification.
                commands = UtilsAssembly.generatePropertySettings(self.joint)
                Gui.doCommand(commands)
        except Exception as error:
            App.Console.PrintError(
                "Could not finalize the new assembly part: "
                f"{error}\n"
            )
            return False

        self.deactivate()
        return True

    def deactivate(self):
        Preferences.preferences().SetBool("PartInNewFile", self.createInNewFileCheck.isChecked())
        super().deactivate()


if App.GuiUp:
    Gui.addCommand("Assembly_InsertNewPart", CommandInsertNewPart())
