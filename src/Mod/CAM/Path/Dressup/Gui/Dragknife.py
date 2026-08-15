# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2014 sliptonic <shopinthewoods@gmail.com>               *
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
import Path.Base.Gui.Util as PathGuiUtil
import Path.Base.Util as PathUtil
import PathScripts.PathUtils as PathUtils
import Path.Dressup.Utils as PathDressup
import Path.Dressup.Dragknife as DragknifeCore
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)
from PySide.QtCore import QT_TRANSLATE_NOOP

__doc__ = """Dragknife Dressup object and FreeCAD command"""

if FreeCAD.GuiUp:
    import FreeCADGui


translate = FreeCAD.Qt.translate

# Preserve the long-standing GUI-module names for direct-import callers. The
# generator itself no longer reads or writes process-global position state.
movecommands = ["G1", "G01", "G2", "G02", "G3", "G03"]
rapidcommands = ["G0", "G00"]
arccommands = ["G2", "G3", "G02", "G03"]
currLocation = {}


class ObjectDressup(DragknifeCore.ObjectDressup):
    """GUI-restorable proxy backed by the shared headless generator."""

    pass


class TaskPanel:
    def __init__(self, obj, transaction=None, viewProvider=None):
        self.obj = obj
        if transaction is None:
            transaction = TaskDocumentTransaction(
                obj,
                "Edit Dragknife Dress-up",
            )
        elif transaction.document is not obj.Document:
            raise RuntimeError(
                "The Dragknife task transaction belongs to another document"
            )
        self.transaction = transaction
        self.document = self.transaction.document
        self.viewProvider = viewProvider
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/DragKnifeEdit.ui")
        self.filterAngle = PathGuiUtil.QuantitySpinBox(self.form.filterAngle, obj, "filterAngle")
        self.offsetDistance = PathGuiUtil.QuantitySpinBox(self.form.offsetDistance, obj, "offset")
        self.pivotHeight = PathGuiUtil.QuantitySpinBox(self.form.pivotHeight, obj, "pivotheight")

    def reject(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.transaction.abort()
        self.clearTaskPanel()
        self.transaction.close_dialog()
        self.transaction.recompute_after_close()
        return True

    def accept(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.getFields()
        self.transaction.commit((self.obj,))
        self.clearTaskPanel()
        self.transaction.reset_edit()
        self.transaction.close_dialog()
        self.transaction.recompute_after_close()
        return True

    def clearTaskPanel(self):
        if (
            self.viewProvider is not None
            and self.viewProvider.panel is self
        ):
            self.viewProvider.panel = None

    def closeDeletedDocumentTask(self):
        if self.viewProvider is not None:
            self.viewProvider.panel = None
        self.transaction.close_dialog()

    def getFields(self):
        self.filterAngle.updateProperty()
        self.offsetDistance.updateProperty()
        self.pivotHeight.updateProperty()
        self.updateUI()

        self.obj.Proxy.execute(self.obj)

    def updateUI(self):
        self.filterAngle.updateWidget()
        self.offsetDistance.updateWidget()
        self.pivotHeight.updateWidget()

    def updateModel(self):
        if not self.transaction.is_open():
            return
        self.getFields()
        self.transaction.recompute((self.obj,))

    def setFields(self):
        self.updateUI()

    def open(self):
        pass

    def setupUi(self):
        self.setFields()


class ViewProviderDressup:
    def __init__(self, vobj):
        self.Object = vobj.Object
        self.panel = None

    def attach(self, vobj):
        self.Object = vobj.Object
        self.panel = None
        if self.Object and self.Object.Base:
            for i in self.Object.Base.InList:
                if hasattr(i, "Group"):
                    group = i.Group
                    for g in group:
                        if g.Name == self.Object.Base.Name:
                            group.remove(g)
                    i.Group = group
    def unsetEdit(self, vobj, mode=0):
        if self.panel is not None:
            self.panel.reject()
        return False

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.Object)

    def setEdit(self, vobj, mode=0):
        transaction = TaskDocumentTransaction(
            vobj.Object,
            "Edit Dragknife Dress-up",
        )
        try:
            panel = TaskPanel(
                vobj.Object,
                transaction=transaction,
                viewProvider=self,
            )
            self.panel = panel
            transaction.close_dialog()
            transaction.show_dialog(panel)
            panel.setupUi()
            return True
        except Exception:
            self.panel = None
            transaction.close_dialog()
            if transaction.owns_transaction():
                transaction.abort()
            raise

    def claimChildren(self):
        return [self.Object.Base]

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDelete(self, arg1=None, arg2=None):
        if arg1.Object and arg1.Object.Base:
            gui_document = FreeCADGui.getDocument(
                arg1.Object.Document.Name
            )
            if PathUtil.shouldRestoreTimelineReplacedInput(
                arg1.Object,
                arg1.Object.Base,
            ):
                gui_document.getObject(
                    arg1.Object.Base.Name
                ).Visibility = True
            job = PathUtils.findParentJob(arg1.Object.Base)
            if job:
                job.Proxy.addOperation(arg1.Object.Base, arg1.Object)
            arg1.Object.Base = None
        return True

    def getIcon(self):
        if PathUtil.activeForOp(self.Object):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def _validated_base(base, document):
    if (
        not is_document_object(base, document)
        or not base.isDerivedFrom("Path::Feature")
        or not PathDressup.isOp(base)
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not base.Path.Commands
        or not PathUtil.activeForOp(base)
    ):
        return None

    job = PathUtils.findParentJob(base)
    controller = PathUtil.toolControllerForOp(base)
    if (
        not is_document_object(job, document)
        or getattr(job, "Operations", None) is None
        or base not in tuple(job.Operations.Group or ())
        or not hasattr(getattr(job, "Proxy", None), "addOperation")
        or not is_document_object(controller, document)
    ):
        return None
    return job


def _validate_result(
    document,
    result,
    result_name,
    result_id,
    base,
    base_name,
    base_id,
    job,
    job_name,
    job_id,
    base_was_visible,
):
    replaced_inputs = (
        [base]
        if base_was_visible
        else []
    )
    if (
        document.getObject(result_name) is not result
        or document.getObject(result_id) is not result
        or int(result.ID) != result_id
        or document.getObject(base_name) is not base
        or document.getObject(base_id) is not base
        or int(base.ID) != base_id
        or document.getObject(job_name) is not job
        or document.getObject(job_id) is not job
        or int(job.ID) != job_id
        or result.Document is not document
        or not result.isDerivedFrom("Path::Feature")
        or not isinstance(getattr(result, "Proxy", None), ObjectDressup)
        or not isinstance(
            getattr(result.ViewObject, "Proxy", None),
            ViewProviderDressup,
        )
        or result.Base is not base
        or PathUtils.findParentJob(result) is not job
        or result not in job.Operations.Group
        or PathUtil.timelineParentJob(result) is not job
        or "VibeCADTimelineReplacedInputs" not in result.PropertiesList
        or list(result.VibeCADTimelineReplacedInputs) != replaced_inputs
        or str(result.VibeCADTimelineRole) != "operation"
        or not result.isValid()
        or bool(base.ViewObject.Visibility)
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            result
        )
    ):
        raise RuntimeError(
            "The Drag Knife CAM dress-up was not created as one exact "
            "replacement operation"
        )


def createDressupFeature(document, name="DragknifeDressup"):
    """Create and initialize one exact Drag Knife dress-up feature."""
    if document is None:
        raise RuntimeError("A document is required for a Drag Knife dress-up")
    result = document.addObject(
        "Path::FeaturePython",
        name,
    )
    ObjectDressup(result)
    return result


def CreateInTransaction(base, name="DragknifeDressup", hide_base=True):
    """Create one Drag Knife replacement inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    if document is None:
        raise RuntimeError("A Drag Knife dress-up requires one live base operation")
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot be drag-knife dressed")
    base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
    result = createDressupFeature(document, name)
    result.Base = base
    job.Proxy.addOperation(result, base)
    if FreeCAD.GuiUp:
        result.ViewObject.Proxy = ViewProviderDressup(result.ViewObject)
        PathUtil.markTimelineReplacedInputs(
            result,
            [base] if base_was_visible else [],
        )
        if hide_base:
            base.ViewObject.Visibility = False
    return result


class CommandDressupDragknife:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupDragKnife", "Drag Knife"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupDragKnife",
                "Modifies a toolpath to add dragknife corner actions",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        document = FreeCAD.ActiveDocument
        return _validated_base(PathDressup.selection(), document) is not None

    def Activated(self):
        document = FreeCAD.ActiveDocument
        if document is None or not can_start_document_command(document):
            return

        op = PathDressup.selection(verbose=True)
        if not op:
            return
        job = _validated_base(op, document)
        if job is None:
            return

        base_name = str(op.Name)
        base_id = int(op.ID)
        job_name = str(job.Name)
        job_id = int(job.ID)
        base_was_visible = bool(op.ViewObject.Visibility)
        launch = begin_task_launch(
            "Create Drag Knife Dress-up",
            document,
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.Dragknife")
            FreeCADGui.addModule("Path.Base.Util")
            FreeCADGui.addModule("PathScripts.PathUtils")
            FreeCADGui.doCommand(
                "document = FreeCAD.getDocument(%r)"
                % document.Name
            )
            FreeCADGui.doCommand(
                "base = document.getObject(%r)" % base_name
            )
            FreeCADGui.doCommand(
                "_cam_base_was_visible = bool(base.ViewObject.Visibility)"
            )
            FreeCADGui.doCommand("job = PathScripts.PathUtils.findParentJob(base)")
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.Dragknife.CreateInTransaction(base)",
                "Path::FeaturePython",
            )
            result_name = str(result.Name)
            result_id = int(result.ID)
            result_expression = "document.getObject(%r)" % result_name
            FreeCADGui.doCommand(f"{result_expression}.filterAngle = 20")
            FreeCADGui.doCommand(f"{result_expression}.offset = 2")
            FreeCADGui.doCommand(f"{result_expression}.pivotheight = 4")
            FreeCADGui.doCommand(
                f"{result_expression}.ViewObject.Document.setEdit("
                f"{result_expression}.ViewObject, 0)"
            )

            document.recompute()
            _validate_result(
                document,
                result,
                result_name,
                result_id,
                op,
                base_name,
                base_id,
                job,
                job_name,
                job_id,
                base_was_visible,
            )
            launch.require_claimed()
        except Exception:
            launch.abort()
            raise

if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupDragKnife", CommandDressupDragknife())

FreeCAD.Console.PrintLog("Loading CAM_DressupDragKnife… done\n")
