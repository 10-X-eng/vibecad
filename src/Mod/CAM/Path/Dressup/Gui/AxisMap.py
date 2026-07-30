# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2018 sliptonic <shopinthewoods@gmail.com>               *
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
import Path
import math
import Path.Base.Gui.Util as PathGuiUtil
import Path.Base.Util as PathUtil
import PathScripts.PathUtils as PathUtils
import Path.Dressup.Utils as PathDressup
import Path.Post.Utils as PostUtils
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)
from PySide.QtCore import QT_TRANSLATE_NOOP

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


if FreeCAD.GuiUp:
    import FreeCADGui

__doc__ = """Axis remapping Dressup object and FreeCAD command.
This dressup remaps one axis of motion to another.
For example, you can re-map the Y axis to A to control a 4th axis rotary."""


translate = FreeCAD.Qt.translate


class ObjectDressup:
    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base path to modify"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "AxisMap",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The input mapping axis"),
        )
        obj.addProperty(
            "App::PropertyDistance",
            "Radius",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The radius of the wrapped axis"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "Reverse",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Reverse rotary axis direction"),
        )
        obj.AxisMap = ("X->A", "Y->A", "X->B", "Y->B", "X->C", "Y->C")
        obj.AxisMap = "Y->A"
        obj.Radius = 45
        obj.Proxy = self

    def dumps(self):
        return

    def loads(self, state):
        return

    def onChanged(self, obj, prop):
        if "Restore" not in obj.State and prop == "Radius":
            job = PathUtils.findParentJob(obj)
            if job:
                job.Proxy.setCenterOfRotation(self.center(obj))

        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def onDocumentRestored(self, obj):
        if not hasattr(obj, "Reverse"):
            obj.addProperty(
                "App::PropertyBool",
                "Reverse",
                "Path",
                QT_TRANSLATE_NOOP("App::Property", "Reverse rotary axis direction"),
            )

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = Path.Path()
            return

        inAxis = obj.AxisMap[0]
        outAxis = obj.AxisMap[3]

        if (
            not obj.Base
            or not obj.Base.isDerivedFrom("Path::Feature")
            or not obj.Base.Path
            or not obj.Base.Path.Commands
        ):
            obj.Path = Path.Path()
            return

        job = PathUtils.findParentJob(obj)
        deflection = job.GeometryTolerance.Value
        path = PathUtils.getPathWithPlacement(obj.Base)
        path = PostUtils.splitArcs(path, deflection=deflection)

        newcommandlist = []
        lastPar = {"X": 0, "Y": 0, "Z": 0, "F": 0}

        for cmd in path.Commands:
            newparams = dict(cmd.Parameters)
            remapvar = newparams.pop(inAxis, None)
            if remapvar is not None:
                if obj.Reverse:
                    remapvar = -remapvar
                newparams[outAxis] = math.degrees(remapvar / obj.Radius.Value)
                locdiff = dict(set(newparams.items()) - set(lastPar.items()))
                if len(locdiff) == 1 and outAxis in locdiff:
                    # calculate rotational feed rate
                    feed = cmd.Parameters["F"] if "F" in cmd.Parameters else lastPar["F"]
                    newparams.update({"F": math.degrees(feed / obj.Radius.Value)})
                newcommand = Path.Command(cmd.Name, newparams)
                newcommandlist.append(newcommand)
                lastPar.update(newparams)
            else:
                newcommandlist.append(cmd)
                lastPar.update(cmd.Parameters)

        path = Path.Path(newcommandlist)
        path.Center = self.center(obj)
        obj.Path = path

    def center(self, obj):
        return FreeCAD.Vector(0, 0, 0 - obj.Radius.Value)


class TaskPanel:
    def __init__(self, obj, transaction=None, viewProvider=None):
        self.obj = obj
        if transaction is None:
            transaction = TaskDocumentTransaction(
                obj,
                "Edit AxisMap Dress-up",
            )
        elif transaction.document is not obj.Document:
            raise RuntimeError(
                "The Axis Map task transaction belongs to another document"
            )
        self.transaction = transaction
        self.document = self.transaction.document
        self.viewProvider = viewProvider
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/AxisMapEdit.ui")
        self.radius = PathGuiUtil.QuantitySpinBox(self.form.radius, obj, "Radius")
        self.reverse = PathGuiUtil.BooleanComboBox(self.form.reverse, obj, "Reverse")

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
        self.radius.updateProperty()
        self.reverse.updateProperty()
        self.obj.AxisMap = self.form.axisMapInput.currentText()
        self.obj.Proxy.execute(self.obj)

    def updateUI(self):
        self.radius.updateWidget()
        self.reverse.updateWidget()
        self.form.axisMapInput.setCurrentText(self.obj.AxisMap)
        self.updateModel()

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
        self.form.radius.valueChanged.connect(self.updateModel)
        self.form.reverse.currentIndexChanged.connect(self.updateModel)
        self.form.axisMapInput.currentIndexChanged.connect(self.updateModel)


class ViewProviderDressup:
    def __init__(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        vobj.Proxy = self

    def attach(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        if self.obj and self.obj.Base:
            for i in self.obj.Base.InList:
                if hasattr(i, "Group"):
                    group = i.Group
                    for g in group:
                        if g.Name == self.obj.Base.Name:
                            group.remove(g)
                    i.Group = group
        return

    def unsetEdit(self, vobj, mode=0):
        if self.panel is not None:
            self.panel.reject()
        return False

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        transaction = TaskDocumentTransaction(
            vobj.Object,
            "Edit AxisMap Dress-up",
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
        return [self.obj.Base]

    def dumps(self):
        return

    def loads(self, state):
        return

    def onDelete(self, arg1=None, arg2=None):
        """this makes sure that the base operation is added back to the project and visible"""
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
            job = PathUtils.findParentJob(arg1.Object)
            if job:
                job.Proxy.addOperation(arg1.Object.Base, arg1.Object)
            arg1.Object.Base = None
        return True

    def getIcon(self):
        if PathUtil.activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def _validated_base(base, document):
    if (
        not is_document_object(base, document)
        or not base.isDerivedFrom("Path::Feature")
        or not PathDressup.isOp(base)
    ):
        return None

    job = PathUtils.findParentJob(base)
    if (
        not is_document_object(job, document)
        or getattr(job, "Operations", None) is None
        or not hasattr(getattr(job, "Proxy", None), "addOperation")
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
            "The Axis Map CAM dress-up was not created as one exact "
            "replacement operation"
        )


def createDressupFeature(document):
    """Create and initialize one exact Axis Map dress-up feature."""
    if document is None:
        raise RuntimeError("A document is required for an Axis Map dress-up")
    result = document.addObject(
        "Path::FeaturePython",
        "AxisMapDressup",
    )
    ObjectDressup(result)
    return result


class CommandPathDressup:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupAxisMap", "Axis Map"),
            "Accel": "",
            "ToolTip": QT_TRANSLATE_NOOP("CAM_DressupAxisMap", "Remaps one axis to another"),
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
        launch = begin_task_launch("Create Axis Map Dress-up", document)
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.AxisMap")
            FreeCADGui.addModule("Path.Base.Util")
            FreeCADGui.addModule("PathScripts.PathUtils")
            FreeCADGui.doCommand(
                "document = FreeCAD.getDocument(%r)"
                % document.Name
            )
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.AxisMap.createDressupFeature(document)",
                "Path::FeaturePython",
            )
            result_name = str(result.Name)
            result_id = int(result.ID)
            result_expression = "document.getObject(%r)" % result_name
            FreeCADGui.doCommand(
                "base = document.getObject(%r)" % base_name
            )
            FreeCADGui.doCommand(
                "_cam_base_was_visible = bool(base.ViewObject.Visibility)"
            )
            FreeCADGui.doCommand("job = PathScripts.PathUtils.findParentJob(base)")
            FreeCADGui.doCommand(f"{result_expression}.Base = base")
            FreeCADGui.doCommand(
                f"job.Proxy.addOperation({result_expression}, base)"
            )
            FreeCADGui.doCommand(
                "Path.Dressup.Gui.AxisMap.ViewProviderDressup("
                f"{result_expression}.ViewObject)"
            )
            FreeCADGui.doCommand(
                "Path.Base.Util.markTimelineReplacedInputs("
                f"{result_expression}, "
                "[base] if _cam_base_was_visible else [])"
            )
            FreeCADGui.doCommand("base.Visibility = False")
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
    FreeCADGui.addCommand("CAM_DressupAxisMap", CommandPathDressup())

FreeCAD.Console.PrintLog("Loading PathDressup… done\n")
