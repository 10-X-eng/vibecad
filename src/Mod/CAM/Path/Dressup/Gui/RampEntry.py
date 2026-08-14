# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI restoration and human command boundary for CAM Ramp Entry."""

from __future__ import annotations

import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
from Path.CommandBoundary import (
    can_start_document_command,
    document_is_open,
    is_document_object,
)
from Path.Dressup import RampEntry as RampEntryCore
import Path.Dressup.Utils as PathDressup
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
from PySide.QtCore import QT_TRANSLATE_NOOP
from VibeCADNativeTransaction import _OwnedDocumentTransaction


class AnnotatedGCode(RampEntryCore.AnnotatedGCode):
    """Compatibility alias for direct human-side algorithm imports."""


class ObjectDressup(RampEntryCore.ObjectDressup):
    """GUI-restorable proxy backed by the shared headless generator."""


class ViewProviderDressup:
    def __init__(self, view_object):
        self.attach(view_object)
        view_object.Proxy = self

    def attach(self, view_object):
        self.obj = view_object.Object
        self._job_name = ""
        try:
            job = PathUtils.findParentJob(self.obj)
            if job is not None and job.Document is self.obj.Document:
                self._job_name = str(job.Name)
        except (ReferenceError, RuntimeError):
            pass

    def claimChildren(self):
        return [self.obj.Base]

    def _restore_base_before_delete(self, view_object):
        try:
            dressup = view_object.Object if view_object is not None else None
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
        if not callback_matches_provider or not is_document_object(base, document):
            return
        try:
            job = document.getObject(self._job_name) if self._job_name else None
        except (NameError, ReferenceError, RuntimeError):
            job = None
        if (
            job is not None
            and is_document_object(job, document)
            and isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
            and is_document_object(getattr(job, "Operations", None), document)
        ):
            before = dressup if dressup in job.Operations.Group else None
            job.Proxy.addOperation(base, before)
        try:
            if PathUtil.shouldRestoreTimelineReplacedInput(dressup, base):
                base.ViewObject.Visibility = True
        except (ReferenceError, RuntimeError):
            pass
        try:
            dressup.Base = None
        except (ReferenceError, RuntimeError):
            pass

    def beforeDelete(self, view_object):
        self._restore_base_before_delete(view_object)

    def onDelete(self, view_object=None, _arguments=None):
        self._restore_base_before_delete(view_object)
        return True

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def getIcon(self):
        return (
            ":/icons/CAM_Dressup.svg"
            if PathUtil.activeForOp(self.obj)
            else ":/icons/CAM_OpActive.svg"
        )


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
        or base not in tuple(job.Operations.Group or ())
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
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(result)
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
        raise RuntimeError(
            "The Ramp Entry CAM dress-up was not created as one exact replacement"
        )


def createDressupFeature(document, name="RampEntryDressup"):
    """Create and initialize one exact Ramp Entry feature."""

    if document is None:
        raise RuntimeError("A document is required for a Ramp Entry dress-up")
    result = document.addObject("Path::FeaturePython", name)
    ObjectDressup(result)
    return result


def CreateInTransaction(base, name="RampEntryDressup", hide_base=True):
    """Create one Ramp Entry replacement inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot receive Ramp Entry")
    base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
    result = createDressupFeature(document, name)
    result.Base = base
    result.Proxy.setup(result)
    job.Proxy.addOperation(result, base, removeBefore=True)
    result.ViewObject.Proxy = ViewProviderDressup(result.ViewObject)
    PathUtil.markTimelineReplacedInputs(
        result,
        [base] if base_was_visible else [],
    )
    if hide_base:
        base.ViewObject.Visibility = False
    return result


class CommandPathDressupRampEntry:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupRampEntry", "Ramp Entry"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupRampEntry",
                "Creates a ramp entry dress-up object from a selected toolpath",
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
        base = PathDressup.selection(verbose=True)
        if base is None:
            return
        job = _validated_base(base, document)
        if job is None:
            return
        base_name = str(base.Name)
        base_id = int(base.ID)
        job_name = str(job.Name)
        job_id = int(job.ID)
        base_was_visible = bool(base.ViewObject.Visibility)
        transaction = _OwnedDocumentTransaction(
            document,
            "Create CAM Ramp Entry dress-up",
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.RampEntry")
            FreeCADGui.doCommand(
                f"document = FreeCAD.getDocument({document.Name!r})"
            )
            FreeCADGui.doCommand(f"base = document.getObject({base_name!r})")
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.RampEntry.CreateInTransaction(base)",
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
            ):
                raise RuntimeError(
                    "The Ramp Entry command did not return its exact output"
                )
            _validate_result(
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
            )
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CAM_DressupRampEntry", CommandPathDressupRampEntry())

Path.Log.notice("Loading CAM_DressupRampEntry… done\n")
