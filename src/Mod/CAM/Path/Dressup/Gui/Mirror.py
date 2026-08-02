# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

################################################################################
#                                                                              #
#   FreeCAD is free software: you can redistribute it and/or modify            #
#   it under the terms of the GNU Lesser General Public License as             #
#   published by the Free Software Foundation, either version 2.1              #
#   of the License, or (at your option) any later version.                     #
#                                                                              #
#   FreeCAD is distributed in the hope that it will be useful,                 #
#   but WITHOUT ANY WARRANTY; without even the implied warranty                #
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                    #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with FreeCAD. If not, see https://www.gnu.org/licenses       #
#                                                                              #
################################################################################

import Constants
import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
import Path.Dressup.Utils as PathDressup
from Path.CommandBoundary import (
    can_start_document_command,
    document_is_open,
    is_document_object,
)
from VibeCADNativeTransaction import _OwnedDocumentTransaction

from PySide.QtCore import QT_TRANSLATE_NOOP

__doc__ = """Mirror Dressup object. This dressup create mirrored path."""


translate = FreeCAD.Qt.translate


class ObjectDressup:
    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base path for mirroring"),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "MirrorAxis",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The mirroring axis"),
        )
        obj.addProperty(
            "App::PropertyVectorDistance",
            "Offset",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Offset for the mirroring axis "),
        )
        obj.addProperty(
            "App::PropertyBool",
            "CenterModel",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Mirroring at the center of base model"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "KeepBasePath",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Add path from base operation"),
        )
        obj.addProperty(
            "App::PropertyLinkSubGlobal",
            "Reference",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Define the reference edge or plane for mirroring"),
        )
        obj.MirrorAxis = ("X", "Y", "XY", "Reference", "None")
        obj.Proxy = self
        self.setEditorModes(obj)

    def dumps(self):
        return

    def loads(self, state):
        return

    def onDocumentRestored(self, obj):
        self.setEditorModes(obj)

    def onChanged(self, obj, prop):
        if prop == "MirrorAxis":
            self.setEditorModes(obj)
        elif prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def setEditorModes(self, obj):
        centerModelMode = 2 if obj.MirrorAxis in ("None", "Reference") else 0
        keepBasePathMode = 2 if obj.MirrorAxis == "None" else 0
        referenceMode = 0 if obj.MirrorAxis == "Reference" else 2

        obj.setEditorMode("CenterModel", centerModelMode)
        obj.setEditorMode("KeepBasePath", keepBasePathMode)
        obj.setEditorMode("Reference", referenceMode)

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = Path.Path()
            return
        if not obj.Base:
            obj.Path = Path.Path()
            Path.Log.warning(translate("MirrorDressup", "No base operation"))
            return

        if not obj.Base.isDerivedFrom("Path::Feature"):
            obj.Path = Path.Path()
            Path.Log.warning(
                translate("MirrorDressup", "Base object '%s' is not derived from Path::Feature")
                % obj.Base.Label
            )
            return

        if not obj.Base.Path.Commands:
            obj.Path = Path.Path()
            Path.Log.warning(
                translate("MirrorDressup", "Base operation '%s' with empty path") % obj.Base.Label
            )
            return

        if obj.MirrorAxis == "None":
            obj.Path = obj.Base.Path
            return

        offsetX = obj.Offset.x
        offsetY = obj.Offset.y
        offsetZ = obj.Offset.z

        if obj.MirrorAxis == "Reference":
            if not obj.Reference or not obj.Reference[1]:
                obj.Path = obj.Base.Path
                return
            model, subName = obj.Reference
            sub = model.Shape.getElement(subName[0])
            bb = sub.BoundBox
            if Path.Geom.isRoughly(bb.XLength, 0):
                mirrorAxis = "Y"
                offsetX += 2 * bb.XMin
            elif Path.Geom.isRoughly(bb.YLength, 0):
                mirrorAxis = "X"
                offsetY += 2 * bb.YMin
        else:
            mirrorAxis = obj.MirrorAxis

        # Calculate offset for center of model
        if obj.CenterModel and obj.MirrorAxis != "Reference":
            # if possible get model from base operation
            baseOp = PathDressup.baseOp(obj)
            if (
                hasattr(baseOp, "Base")
                and isinstance(baseOp.Base, (list, tuple))
                and baseOp.Base
                and isinstance(baseOp.Base[0], (list, tuple))
                and baseOp.Base[0]
                and baseOp.Base[0][0].isDerivedFrom("Part::Feature")
            ):
                model = baseOp.Base[0][0]
            else:
                # otherwise get first model from Model group of the Job
                job = PathUtils.findParentJob(obj)
                model = job.Model.Group[0]

            offsetX += model.Shape.BoundBox.XMax + model.Placement.Base.x
            offsetY += model.Shape.BoundBox.YMax + model.Placement.Base.y

        commands = PathUtils.getPathWithPlacement(obj.Base).Commands
        for cmd in commands:
            if cmd.Name not in Constants.GCODE_MOVE_ALL:
                # command without move, change nothing
                continue
            else:
                if cmd.x is not None:
                    # process X move
                    if mirrorAxis in ("Y", "XY"):
                        cmd.x = -cmd.x
                    cmd.x += offsetX

                if cmd.y is not None:
                    # process Y move
                    if mirrorAxis in ("X", "XY"):
                        cmd.y = -cmd.y
                    cmd.y += offsetY

                if cmd.z is not None:
                    # process Z move
                    cmd.z += offsetZ

                if cmd.i is not None and mirrorAxis in ("Y", "XY"):
                    # process I (X offset) from Arc move
                    cmd.i = -cmd.i

                if cmd.j is not None and mirrorAxis in ("X", "XY"):
                    # process J (Y offset) from Arc move
                    cmd.j = -cmd.j

                if mirrorAxis != "XY" and cmd.Name in Constants.GCODE_MOVE_ARC:
                    # change direction of Arc move
                    if cmd.Name in Constants.GCODE_MOVE_CCW:
                        cmd.Name = "G2"
                    else:
                        cmd.Name = "G3"

        if obj.KeepBasePath:
            obj.Path = PathUtils.getPathWithPlacement(obj.Base)
            obj.Path.addCommands(commands)
        else:
            obj.Path = Path.Path(commands)


class ViewProviderDressup:
    def __init__(self, vobj):
        self.attach(vobj)
        vobj.Proxy = self

    def attach(self, vobj):
        self.vobj = vobj
        self.obj = vobj.Object
        self._job_name = ""
        try:
            job = PathUtils.findParentJob(self.obj)
            if job is not None and job.Document is self.obj.Document:
                self._job_name = str(job.Name)
        except (ReferenceError, RuntimeError):
            pass

    def dumps(self):
        return

    def loads(self, state):
        return

    def onChanged(self, vobj, prop):
        return

    def claimChildren(self):
        if hasattr(self.obj.Base, "InList"):
            for i in self.obj.Base.InList:
                if hasattr(i, "Group"):
                    group = i.Group
                    for g in group:
                        if g.Name == self.obj.Base.Name:
                            group.remove(g)
                    i.Group = group
        return [self.obj.Base]

    def _restore_base_before_delete(self, vobj):
        try:
            dressup = vobj.Object if vobj is not None else None
            document = getattr(dressup, "Document", None)
            base = getattr(dressup, "Base", None)
        except (ReferenceError, RuntimeError):
            return
        try:
            callback_matches_provider = (
                document_is_open(document)
                and dressup is self.obj
                and getattr(dressup, "Document", None) is document
            )
        except (NameError, ReferenceError, RuntimeError):
            callback_matches_provider = False
        if not callback_matches_provider or not is_document_object(
            base,
            document,
        ):
            return

        try:
            job = (
                document.getObject(self._job_name)
                if self._job_name
                else None
            )
        except (NameError, ReferenceError, RuntimeError):
            job = None
        if (
            job is not None
            and is_document_object(job, document)
            and isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
            and is_document_object(
                getattr(job, "Operations", None),
                document,
            )
        ):
            before = (
                dressup
                if dressup in job.Operations.Group
                else None
            )
            job.Proxy.addOperation(base, before)

        # Deletion can be triggered while another document tab is active.
        # Restore the source through its own ViewProvider, never ActiveDocument.
        try:
            if PathUtil.shouldRestoreTimelineReplacedInput(
                dressup,
                base,
            ):
                base.ViewObject.Visibility = True
        except (ReferenceError, RuntimeError):
            pass
        try:
            dressup.Base = None
        except (ReferenceError, RuntimeError):
            pass

    def beforeDelete(self, vobj):
        """Restore the source for every model-deletion path."""

        self._restore_base_before_delete(vobj)

    def onDelete(self, arg1=None, arg2=None):
        """Restore the source before an interactive GUI deletion."""

        self._restore_base_before_delete(arg1)
        return True

    def setEdit(self, vobj, mode=0):
        if mode == 1:
            FreeCADGui.runCommand("Std_TransformManip")
        return True

    def getIcon(self):
        if PathUtil.activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def _validated_base(base, document):
    if (
        base is None
        or getattr(base, "Document", None) is not document
        or not base.isDerivedFrom("Path::Feature")
        or not PathDressup.isOp(base)
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not base.Path.Commands
    ):
        return None

    job = PathUtils.findParentJob(base)
    if (
        job is None
        or job.Document is not document
        or not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
        or getattr(job, "Operations", None) is None
    ):
        return None
    return job


def _validate_result(document, result, base, job):
    has_active_source_path = (
        PathUtil.activeForOp(base)
        and getattr(base, "Path", None)
        and bool(base.Path.Commands)
    )
    if (
        result is None
        or result.Document is not document
        or not result.isDerivedFrom("Path::Feature")
        or not isinstance(getattr(result, "Proxy", None), ObjectDressup)
        or result.Base is not base
        or not result.isValid()
        or PathUtils.findParentJob(result) is not job
        or result not in job.Operations.Group
        or (
            has_active_source_path
            and (
                not getattr(result, "Path", None)
                or not result.Path.Commands
            )
        )
    ):
        raise RuntimeError("The mirrored CAM toolpath was not created correctly")


def createDressupFeature(document):
    """Create and initialize one exact mirror dress-up feature."""
    if document is None:
        raise RuntimeError("A document is required for a mirror dress-up")
    result = document.addObject(
        "Path::FeaturePython",
        "MirrorDressup",
    )
    ObjectDressup(result)
    return result


class CommandPathDressup:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupMirror", "Mirror"),
            "Accel": "",
            "ToolTip": QT_TRANSLATE_NOOP("CAM_DressupMirror", "Creates mirror of a selected path"),
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

        transaction = _OwnedDocumentTransaction(
            document,
            "Create CAM mirror dress-up",
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.Mirror")
            FreeCADGui.addModule("Path.Base.Util")
            FreeCADGui.addModule("PathScripts.PathUtils")
            FreeCADGui.doCommand(
                f"document = FreeCAD.getDocument({document.Name!r})"
            )
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.Mirror.createDressupFeature(document)",
                "Path::FeaturePython",
            )
            result_name = result.Name
            result_id = int(result.ID)
            result_expression = "document.getObject(%r)" % result_name
            FreeCADGui.doCommand(
                f"baseOp = document.getObject({op.Name!r})"
            )
            FreeCADGui.doCommand(
                "_cam_base_was_visible = bool(baseOp.ViewObject.Visibility)"
            )
            FreeCADGui.doCommand("job = PathScripts.PathUtils.findParentJob(baseOp)")
            FreeCADGui.doCommand(f"{result_expression}.Base = baseOp")
            FreeCADGui.doCommand(
                f"job.Proxy.addOperation({result_expression}, baseOp)"
            )
            FreeCADGui.doCommand(
                "Path.Dressup.Gui.Mirror.ViewProviderDressup("
                f"{result_expression}.ViewObject)"
            )
            FreeCADGui.doCommand(
                "Path.Base.Util.markTimelineReplacedInputs("
                f"{result_expression}, "
                "[baseOp] if _cam_base_was_visible else [])"
            )
            FreeCADGui.doCommand("baseOp.Visibility = False")

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
                    "The mirror dress-up command did not return its exact output"
                )
            _validate_result(document, result, op, job)
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupMirror", CommandPathDressup())

FreeCAD.Console.PrintLog("Loading PathDressup... done\n")
