# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2015 Yorik van Havre <yorik@uncreated.net>              *
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

import FreeCAD
import FreeCADGui
import Path.Main.Job as PathJob
import Path.Op.Custom as PathCustom
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import (
    ExactDocumentObjectIdentity,
    can_start_document_command,
    is_timeline_input_usable,
)
from Path.Base.Util import activeForOp
from Path.Base.Util import coolantModeForOp
from Path.Base.Util import toolControllerForOp
import Path.Dressup.Utils as PathDressup
from VibeCADNativeTransaction import _OwnedDocumentTransaction
from PySide.QtCore import QT_TRANSLATE_NOOP

__doc__ = """CAM SimpleCopy command"""

translate = FreeCAD.Qt.translate


class ViewProvider:

    def __init__(self, vobj):
        self.attach(vobj)
        vobj.Proxy = self

    def attach(self, vobj):
        self.vobj = vobj
        self.obj = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, vobj, prop):
        return

    def getIcon(self):
        if activeForOp(self.obj):
            return ":/icons/CAM_SimpleCopy.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def Create(name="Simple Copy", bases=None, parentJob=None, gcode=None):
    """Create one flattened, source-preserving CAM toolpath copy.

    Transaction and History publication remain caller-owned. Supplying
    ``gcode`` lets a guarded caller publish the exact stream frozen during
    preflight; human callers omit it and use the live placed base paths.
    """

    label = str(name or "").strip()
    operations = tuple(bases or ())
    document = getattr(parentJob, "Document", None)
    if not label:
        raise ValueError("A CAM Simple Copy label must not be empty")
    if document is None or not operations:
        raise ValueError("A CAM Simple Copy requires one Job and at least one source")
    if any(getattr(operation, "Document", None) is not document for operation in operations):
        raise ValueError("Every CAM Simple Copy source must belong to the Job document")
    if not isinstance(getattr(parentJob, "Proxy", None), PathJob.ObjectJob):
        raise ValueError("A CAM Simple Copy requires a native CAM Job")

    controller = toolControllerForOp(operations[0])
    coolant = coolantModeForOp(operations[0])
    if controller is None or any(
        toolControllerForOp(operation) is not controller
        or coolantModeForOp(operation) != coolant
        for operation in operations
    ):
        raise ValueError("CAM Simple Copy sources must share one controller and coolant mode")
    lines = (
        tuple(str(value) for value in gcode)
        if gcode is not None
        else tuple(
            command.toGCode()
            for operation in operations
            for command in PathUtils.getPathWithPlacement(operation).Commands
        )
    )
    if not lines:
        raise ValueError("CAM Simple Copy sources contain no toolpath commands")

    internal_name = (
        f"{operations[0].Name}_SimpleCopy"
        if len(operations) == 1
        else "SimpleCopy"
    )
    result = PathCustom.CreateEmbeddedPath(
        internal_name,
        obj=document.addObject("Path::FeaturePython", internal_name),
        parentJob=parentJob,
    )
    result.Label = label
    result.ToolController = controller
    result.CoolantMode = coolant
    if FreeCAD.GuiUp and getattr(result, "ViewObject", None) is not None:
        result.ViewObject.Proxy = ViewProvider(result.ViewObject)
    result.Gcode = list(lines)
    return result


def _selected_copy_operations():
    document = FreeCAD.ActiveDocument
    selection = FreeCADGui.Selection.getSelection()
    if document is None or not selection:
        return None

    if any(
        getattr(operation, "Document", None) is not document
        or not is_timeline_input_usable(operation, document)
        or not PathDressup.isOp(operation)
        or not operation.isValid()
        or not activeForOp(operation)
        or not getattr(operation, "Path", None)
        or not operation.Path.Commands
        for operation in selection
    ):
        return None

    coolant = coolantModeForOp(selection[0])
    controller = toolControllerForOp(selection[0])
    parent_job = None
    if (
        controller is None
        or controller.Document is not document
        or not is_timeline_input_usable(controller, document)
    ):
        return None

    for operation in selection:
        if (
            toolControllerForOp(operation) is not controller
            or coolantModeForOp(operation) != coolant
        ):
            return None

        job = PathUtils.findParentJob(operation)
        if (
            job is None
            or job.Document is not document
            or not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
            or getattr(job, "Operations", None) is None
            or not is_timeline_input_usable(job, document)
        ):
            return None
        if parent_job is None:
            parent_job = job
        elif job is not parent_job:
            return None

    return tuple(selection), parent_job, controller, coolant


def _validate_copy_result(
    document,
    result,
    job,
    controller,
    coolant,
):
    if (
        result is None
        or result.Document is not document
        or not result.isDerivedFrom("Path::Feature")
        or not result.isValid()
        or PathUtils.findParentJob(result) is not job
        or result not in job.Operations.Group
        or toolControllerForOp(result) is not controller
        or coolantModeForOp(result) != coolant
        or not getattr(result, "Path", None)
        or not result.Path.Commands
    ):
        raise RuntimeError("The CAM toolpath copy was not created correctly")


class CommandPathSimpleCopy:
    def GetResources(self):
        return {
            "Pixmap": "CAM_SimpleCopy",
            "MenuText": QT_TRANSLATE_NOOP("CAM_SimpleCopy", "Simple Copy"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_SimpleCopy",
                "Creates a non-parametric copy of another toolpath\n"
                "Several operations can be used with identical tool controller and coolant mode",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        return _selected_copy_operations() is not None

    def Activated(self):
        document = FreeCAD.ActiveDocument
        if document is None or not can_start_document_command(document):
            return

        selected = _selected_copy_operations()
        if selected is None:
            return
        selection, job, controller, coolant = selected
        source_identities = [
            ExactDocumentObjectIdentity(operation, document)
            for operation in selection
        ]
        job_identity = ExactDocumentObjectIdentity(job, document)
        controller_identity = ExactDocumentObjectIdentity(
            controller,
            document,
        )

        transaction = _OwnedDocumentTransaction(document, "Create CAM toolpath copy")
        try:
            selection = tuple(
                identity.resolve(require_timeline=True)
                for identity in source_identities
            )
            job = job_identity.resolve(require_timeline=True)
            controller = controller_identity.resolve(
                require_timeline=True,
            )
            FreeCADGui.doCommand(
                "document = App.getDocument(%r)" % document.Name
            )
            selectionString = "[%s]" % ",".join(
                [
                    "document.getObject(%r)" % operation.Name
                    for operation in selection
                ]
            )
            FreeCADGui.doCommand("selection = %s" % selectionString)
            FreeCADGui.doCommand(
                "name = selection[0].Name+'_SimpleCopy' if len(selection) == 1 else 'SimpleCopy'"
            )
            FreeCADGui.addModule("PathScripts.PathUtils")
            FreeCADGui.doCommand("job = PathScripts.PathUtils.findParentJob(selection[0])")
            FreeCADGui.addModule("Path.Op.Gui.SimpleCopy")
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Op.Gui.SimpleCopy.Create(name, selection, job)",
                "Path::FeaturePython",
            )
            result_name = result.Name
            result_id = int(result.ID)

            selection = tuple(
                identity.resolve(require_timeline=True)
                for identity in source_identities
            )
            job = job_identity.resolve(require_timeline=True)
            controller = controller_identity.resolve(
                require_timeline=True,
            )
            document.recompute()
            resolved = document.getObject(result_name)
            if (
                resolved is not result
                or int(resolved.ID) != result_id
                or document.getObject(result_id) is not result
                or not document
                .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                    result
                )
            ):
                raise RuntimeError(
                    "The CAM toolpath copy command did not return its exact output"
                )
            _validate_copy_result(
                document,
                result,
                job,
                controller,
                coolant,
            )
            document.publishProvisionalTimelineOperationBlock(
                result,
                [],
            )
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_SimpleCopy", CommandPathSimpleCopy())
