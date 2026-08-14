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

import FreeCAD
import FreeCADGui
import Path.Base.Util as PathUtil
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
from Path.Dressup import Mirror as MirrorCore
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


class ObjectDressup(MirrorCore.ObjectDressup):
    """GUI-restorable proxy backed by the shared headless generator."""

    pass


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
    has_active_source_path = (
        PathUtil.activeForOp(base)
        and getattr(base, "Path", None)
        and bool(base.Path.Commands)
    )
    if (
        result is None
        or result.Document is not document
        or result.TypeId != "Path::FeaturePython"
        or document.getObject(result_name) is not result
        or document.getObject(result_id) is not result
        or str(result.Name) != result_name
        or int(result.ID) != result_id
        or not isinstance(getattr(result, "Proxy", None), ObjectDressup)
        or not isinstance(
            getattr(result.ViewObject, "Proxy", None),
            ViewProviderDressup,
        )
        or result.Base is not base
        or base.Document is not document
        or document.getObject(base_name) is not base
        or document.getObject(base_id) is not base
        or str(base.Name) != base_name
        or int(base.ID) != base_id
        or job.Document is not document
        or document.getObject(job_name) is not job
        or document.getObject(job_id) is not job
        or str(job.Name) != job_name
        or int(job.ID) != job_id
        or not result.isValid()
        or PathUtils.findParentJob(result) is not job
        or PathUtil.timelineParentJob(result) is not job
        or result not in tuple(job.Operations.Group or ())
        or base in tuple(job.Operations.Group or ())
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            result
        )
        or tuple(result.VibeCADTimelineReplacedInputs)
        != ((base,) if base_was_visible else ())
        or bool(base.ViewObject.Visibility)
        or not bool(result.ViewObject.Visibility)
        or (
            has_active_source_path
            and (
                not getattr(result, "Path", None)
                or not result.Path.Commands
            )
        )
    ):
        raise RuntimeError("The mirrored CAM toolpath was not created correctly")


def createDressupFeature(document, name="MirrorDressup"):
    """Create and initialize one exact mirror dress-up feature."""
    if document is None:
        raise RuntimeError("A document is required for a mirror dress-up")
    result = document.addObject(
        "Path::FeaturePython",
        name,
    )
    ObjectDressup(result)
    return result


def CreateInTransaction(base, name="MirrorDressup", hide_base=True):
    """Create one Mirror replacement inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot be mirror-dressed")
    base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
    result = createDressupFeature(document, name)
    result.Base = base
    job.Proxy.addOperation(result, base, removeBefore=True)
    result.ViewObject.Proxy = ViewProviderDressup(result.ViewObject)
    PathUtil.markTimelineReplacedInputs(
        result,
        [base] if base_was_visible else [],
    )
    if hide_base:
        base.ViewObject.Visibility = False
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

        base_name = str(op.Name)
        base_id = int(op.ID)
        job_name = str(job.Name)
        job_id = int(job.ID)
        base_was_visible = bool(op.ViewObject.Visibility)

        transaction = _OwnedDocumentTransaction(
            document,
            "Create CAM mirror dress-up",
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.Mirror")
            FreeCADGui.doCommand(
                f"document = FreeCAD.getDocument({document.Name!r})"
            )
            FreeCADGui.doCommand(f"baseOp = document.getObject({op.Name!r})")
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.Mirror.CreateInTransaction(baseOp)",
                "Path::FeaturePython",
            )
            result_name = str(result.Name)
            result_id = int(result.ID)

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
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupMirror", CommandPathDressup())

FreeCAD.Console.PrintLog("Loading PathDressup... done\n")
