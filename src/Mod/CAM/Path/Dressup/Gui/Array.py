# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Dressup.Array as DressupArray
import Path.Dressup.Utils as PathDressup
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import can_start_document_command
from VibeCADNativeTransaction import _OwnedDocumentTransaction

from PySide.QtCore import QT_TRANSLATE_NOOP

translate = FreeCAD.Qt.translate


class DressupArrayViewProvider(object):
    def __init__(self, vobj):
        self.attach(vobj)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def attach(self, vobj):
        self.vobj = vobj
        self.obj = vobj.Object
        self.panel = None

    def claimChildren(self):
        return [self.obj.Base]

    def onDelete(self, vobj, args=None):
        if vobj.Object and vobj.Object.Proxy:
            vobj.Object.Proxy.onDelete(vobj.Object, args)
        return True

    def setEdit(self, vobj, mode=0):
        return True

    def unsetEdit(self, vobj, mode=0):
        pass

    def setupTaskPanel(self, panel):
        pass

    def clearTaskPanel(self):
        pass

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
    if (
        result is None
        or result.Document is not document
        or not result.isDerivedFrom("Path::Feature")
        or not isinstance(
            getattr(result, "Proxy", None),
            DressupArray.DressupArray,
        )
        or result.Base is not base
        or not result.isValid()
        or PathUtils.findParentJob(result) is not job
        or result not in job.Operations.Group
    ):
        raise RuntimeError("The CAM array dress-up was not created correctly")


class CommandPathDressupArray:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupArray", "Array"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupArray",
                "Creates an array from a selected toolpath",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        document = FreeCAD.ActiveDocument
        return _validated_base(PathDressup.selection(), document) is not None

    def Activated(self):
        if not self.IsActive():
            return

        op = PathDressup.selection(verbose=True)
        if not op:
            return

        FreeCADGui.addModule("Path.Dressup.Gui.Array")
        FreeCADGui.doCommand(
            "Path.Dressup.Gui.Array.Create("
            "App.getDocument(%r).getObject(%r))"
            % (op.Document.Name, op.Name)
        )


def Create(base, name="DressupPathArray"):
    document = FreeCAD.ActiveDocument
    if document is None or not can_start_document_command(document):
        raise RuntimeError("A CAM array dress-up requires an idle active document")

    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot be arrayed")

    transaction = _OwnedDocumentTransaction(
        document,
        "Create CAM array dress-up",
    )
    try:
        base_was_visible = bool(
            base.ViewObject
            and base.ViewObject.Visibility
        )
        obj = DressupArray.Create(base, name)
        if obj is None:
            raise RuntimeError("Could not create the CAM array dress-up")
        obj.ViewObject.Proxy = DressupArrayViewProvider(obj.ViewObject)
        PathUtil.markTimelineReplacedInputs(
            obj,
            [base] if base_was_visible else [],
        )
        obj.Base.ViewObject.Visibility = False
        document.recompute()
        _validate_result(document, obj, base, job)
    except Exception:
        transaction.abort()
        raise
    transaction.commit()
    return obj


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupArray", CommandPathDressupArray())
