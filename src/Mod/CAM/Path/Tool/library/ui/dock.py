# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2019 sliptonic <shopinthewoods@gmail.com>               *
# *                 2020 Schildkroet                                        *
# *                 2025 Samuel Abels <knipknap@gmail.com>                  *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""ToolBit Library Dock Widget."""

import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Tool.Gui.Controller as PathToolControllerGui
import PathScripts.PathUtilsGui as PathUtilsGui
from Path.CommandBoundary import active_jobs, is_job
from VibeCADNativeTransaction import _OwnedDocumentTransaction
from PySide import QtGui, QtCore, QtWidgets
from functools import partial
from typing import List, Tuple
from ...camassets import cam_assets, ensure_assets_initialized
from ...toolbit import ToolBit
from .editor import LibraryEditor
from .browser import LibraryBrowserWithCombo

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


translate = FreeCAD.Qt.translate


class ToolBitLibraryDock(object):
    """Controller for displaying a library and creating ToolControllers"""

    def __init__(self, defaultJob=None, autoClose=False):
        ensure_assets_initialized(cam_assets)
        # Create the main form widget directly
        self.defaultJob = defaultJob
        self.autoClose = autoClose
        self.form = QtWidgets.QDialog()
        self.form.setObjectName("ToolSelector")
        self.form.setWindowTitle(translate("CAM_ToolBit", "Toolbit Selector"))
        self.form.setMinimumSize(600, 400)
        self.form.resize(800, 600)
        self.form.adjustSize()
        self.form_layout = QtGui.QVBoxLayout(self.form)
        self.form_layout.setContentsMargins(4, 4, 4, 4)
        self.form_layout.setSpacing(4)

        # Create the browser widget
        self.browser_widget = LibraryBrowserWithCombo(asset_manager=cam_assets)

        self._setup_ui()

    def _setup_ui(self):
        """Setup the form and load the tooltable data"""
        Path.Log.track()

        # Create a main widget and layout for the dock
        main_widget = QtGui.QWidget()
        main_layout = QtGui.QVBoxLayout(main_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        main_layout.addWidget(self.browser_widget)

        # Create buttons
        self.libraryEditorOpenButton = QtGui.QPushButton(
            translate("CAM_ToolBit", "Open Library Editor")
        )
        self.addToolControllerButton = QtGui.QPushButton(translate("CAM_ToolBit", "Add to Job"))
        self.closeButton = QtGui.QPushButton(translate("CAM_ToolBit", "Close"))

        button_width = 120
        self.libraryEditorOpenButton.setMinimumWidth(button_width)
        self.addToolControllerButton.setMinimumWidth(button_width)
        self.closeButton.setMinimumWidth(button_width)

        # Add buttons to a horizontal layout, right-align Close
        button_layout = QtGui.QHBoxLayout()
        button_layout.addWidget(self.libraryEditorOpenButton)
        button_layout.addWidget(self.addToolControllerButton)
        button_layout.addStretch(1)
        button_layout.addWidget(self.closeButton)

        # Add the button layout to the main layout
        main_layout.addLayout(button_layout)

        # Set the main widget as the dock's widget
        self.form.layout().addWidget(main_widget)

        # Connect signals from the browser widget and buttons
        self.browser_widget.toolSelected.connect(lambda x: self._update_state())
        self.browser_widget.itemDoubleClicked.connect(self._on_doubleclick)
        self.libraryEditorOpenButton.clicked.connect(self._open_editor)
        self.addToolControllerButton.clicked.connect(self._add_tool_controller_to_doc)
        self.closeButton.clicked.connect(self.form.reject)

        # Update the initial state of the UI
        self._update_state()

    def _count_jobs(self):
        return bool(active_jobs())

    def _update_state(self):
        """Enable button to add tool controller when a tool is selected"""
        selected = bool(self.browser_widget.get_selected_bit_uris())
        has_job = selected and self._count_jobs() > 0
        self.addToolControllerButton.setEnabled(selected and has_job)

    def _on_doubleclick(self, toolbit: ToolBit):
        """Opens the ToolBitEditor for the selected toolbit."""
        self._add_tool_controller_to_doc()

    def _open_editor(self):
        library = LibraryEditor(parent=FreeCADGui.getMainWindow())
        library.open()
        # After editing, we might need to refresh the libraries in the browser widget
        # Assuming _populate_libraries is the correct method to call
        self.browser_widget.refresh()

    def _add_tool_to_doc(
        self,
        document=None,
        job=None,
        created=None,
    ) -> List[Tuple[int, ToolBit]]:
        """
        Get the selected toolbit assets from the browser widget.
        """
        Path.Log.track()
        document = document or FreeCAD.ActiveDocument
        if document is None:
            return []

        tools = []
        selected_assets = self.browser_widget.get_selected_bits()
        allocated_numbers = (
            {
                int(controller.ToolNumber)
                for controller in job.Tools.Group
            }
            if job is not None
            else set()
        )
        next_number = int(job.Proxy.nextToolNumber()) if job is not None else None

        for selected_asset in selected_assets:
            # AssetManager caches library assets.  Never attach that cached
            # instance to a document: an aborted insertion would leave the
            # library entry pointing at a deleted DocumentObject and make the
            # next Add attempt unusable.  Each inserted tool is an independent
            # document instance of the selected asset definition.
            toolbit = ToolBit.from_dict(selected_asset.to_dict())
            # Need to get the tool number for this toolbit from the currently
            # selected library in the browser widget.
            toolNr = self.browser_widget.get_tool_no_from_current_library(
                selected_asset
            )
            if toolNr is not None:
                toolNr = int(toolNr)
            if job is not None and (
                toolNr is None or toolNr in allocated_numbers
            ):
                while next_number in allocated_numbers:
                    next_number += 1
                toolNr = next_number
                next_number += 1
            if toolNr is None:
                raise RuntimeError(
                    "The selected toolbit has no tool number and no Job "
                    "was supplied to allocate one"
                )
            allocated_numbers.add(toolNr)

            toolbit.attach_to_doc(
                document,
                timeline_owner=job,
            )
            if getattr(toolbit.obj, "Document", None) is not document:
                raise RuntimeError(
                    f"Toolbit {toolbit.label} was not added to the intended document"
                )
            tools.append((toolNr, toolbit))
            if created is not None:
                created.append(
                    (
                        str(toolbit.obj.Name),
                        int(toolbit.obj.ID),
                        toolbit,
                    )
                )

        return tools

    @staticmethod
    def _validate_tool_controller_addition(document, job, additions):
        """Validate the complete selected-tool gesture before it is committed."""

        if not is_job(job, document):
            raise RuntimeError("The selected CAM Job is no longer available")
        if FreeCAD.ActiveDocument is not document:
            raise RuntimeError("The active document changed while adding toolbits")

        document.recompute()
        controllers = tuple(job.Tools.Group)
        controller_numbers = [int(controller.ToolNumber) for controller in controllers]
        if len(controller_numbers) != len(set(controller_numbers)):
            raise RuntimeError("CAM Job tool numbers must be unique")
        for toolbit, controller in additions:
            tool = toolbit.obj
            if (
                getattr(tool, "Document", None) is not document
                or document.getObject(tool.Name) != tool
                or not tool.isValid()
            ):
                raise RuntimeError(
                    f"Toolbit {toolbit.label} is not a valid object in the intended document"
                )
            if (
                getattr(controller, "Document", None) is not document
                or document.getObject(controller.Name) != controller
                or not controller.isValid()
                or controller.Tool != tool
                or controller not in controllers
            ):
                raise RuntimeError(
                    f"Tool controller for {toolbit.label} was not added completely"
                )

    def _add_tool_controller_to_doc(self):
        """
        if no jobs, don't do anything, otherwise all TCs for all
        selected toolbit assets
        """
        Path.Log.track()
        document = FreeCAD.ActiveDocument
        if document is None:
            return

        jobs = [
            job
            for job in PathUtilsGui.PathUtils.GetJobs()
            if is_job(job, document)
        ]
        if len(jobs) == 0:
            QtGui.QMessageBox.information(
                self.form,
                translate("CAM_ToolBit", "No Job Found"),
                translate("CAM_ToolBit", "Create a Job first."),
            )
            return
        elif self.defaultJob or len(jobs) == 1:
            job = self.defaultJob or jobs[0]
        else:
            userinput = PathUtilsGui.PathUtilsUserInput()
            job = userinput.chooseJob(jobs)
            self.defaultJob = job

        if job is None:  # user may have canceled
            return
        if not is_job(job, document):
            raise RuntimeError(
                "Toolbits can only be added to a real Job in the active document"
            )
        selected_uris = self.browser_widget.get_selected_bit_uris()
        if not selected_uris:
            return

        # The standalone library owns one exact transaction per Add or
        # double-click gesture.  The embedded picker used by an operation task
        # already runs inside that task's exact transaction, so its additions
        # remain part of the operation's all-or-nothing edit.
        transaction = None
        existing_transaction = int(document.getBookedTransactionID())
        if existing_transaction == 0 and not document.HasPendingTransaction:
            transaction = _OwnedDocumentTransaction(
                document,
                "Add CAM toolbits",
            )
        elif not (
            self.autoClose
            and self.defaultJob is job
            and FreeCADGui.Control.activeDialog()
        ):
            return

        reconciliation = None
        created_toolbits = []
        created_controllers = []
        try:
            reconciliation = PathUtil.stageTimelineResourceGraphExtension(
                job
            )
            selected_tools = self._add_tool_to_doc(
                document,
                job,
                created_toolbits,
            )
            if len(selected_tools) != len(selected_uris):
                raise RuntimeError("Not every selected toolbit could be loaded")
            additions = []
            for toolNr, toolbit in selected_tools:
                if FreeCAD.ActiveDocument is not document:
                    raise RuntimeError(
                        "The active document changed while adding toolbits"
                    )
                controller = PathToolControllerGui.Create(
                    f"TC: {toolbit.label}",
                    toolbit.obj,
                    toolNr,
                    document=document,
                    timelineOwner=job,
                )
                if getattr(controller, "Document", None) is not document:
                    raise RuntimeError(
                        f"Tool controller for {toolbit.label} was created "
                        "in the wrong document"
                    )
                job.Proxy.addToolController(controller)
                created_controllers.append(
                    (str(controller.Name), int(controller.ID))
                )
                additions.append((toolbit, controller))

            if len(additions) != len(selected_tools):
                raise RuntimeError("Not every selected toolbit was added")
            self._validate_tool_controller_addition(
                document,
                job,
                additions,
            )
            PathUtil.finalizeTimelineResourceGraphExtension(
                job,
                reconciliation,
                [
                    resource
                    for _toolbit, controller in additions
                    for resource in PathUtil.toolControllerResourceGraph(
                        controller
                    )
                ],
            )
            if transaction is not None:
                transaction.commit()
        except Exception:
            if transaction is not None:
                transaction.abort()
            elif reconciliation is not None:
                for name, object_id in reversed(created_controllers):
                    controller = document.getObject(name)
                    if (
                        controller is not None
                        and int(controller.ID) == object_id
                    ):
                        document.removeObject(name)
                for name, object_id, toolbit in reversed(
                    created_toolbits
                ):
                    tool = document.getObject(name)
                    if (
                        tool is not None
                        and int(tool.ID) == object_id
                    ):
                        proxy = getattr(tool, "Proxy", None)
                        on_delete = getattr(proxy, "onDelete", None)
                        if callable(on_delete):
                            on_delete(tool)
                        elif document.getObject(name) is tool:
                            document.removeObject(name)
                PathUtil.cancelTimelineResourceGraphExtension(
                    job,
                    reconciliation,
                )
                document.recompute()
            raise

        if self.autoClose:
            self.form.accept()

    def open(self, path=None):
        """load library stored in path and bring up ui"""
        self.form.exec_()
