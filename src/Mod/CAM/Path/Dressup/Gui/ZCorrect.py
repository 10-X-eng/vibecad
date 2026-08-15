# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2018 sliptonic <shopinthewoods@gmail.com>
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
import Path
import Path.Base.Util as PathUtil
import PathScripts.PathUtils as PathUtils
from Path.Dressup import ZCorrect as ZCorrectCore
import Path.Dressup.Utils as PathDressup
import Path.Main.Job as PathJob
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    document_is_open,
    is_document_object,
    is_timeline_input_usable,
    open_timeline_mode_zero_editor,
)

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP

"""Z Depth Correction Dressup.  This dressup takes a probe file as input and does bilinear interpolation of the Zdepths to correct for a surface which is not parallel to the milling table/bed.  The probe file should conform to the format specified by the linuxcnc G38 probe logging: 9-number coordinate consisting of XYZABCUVW http://linuxcnc.org/docs/html/gcode/g-code.html#gcode:g38
"""

LOGLEVEL = False

LOG_MODULE = Path.Log.thisModule()

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


translate = FreeCAD.Qt.translate


class ObjectDressup:
    def __init__(self, obj):
        self._ensure_properties(obj)
        obj.Proxy = self
        obj.ArcInterpolate = 0.1
        obj.SegInterpolate = 1.0

    @staticmethod
    def _add_property(obj, type_name, name, group, description=""):
        if name not in tuple(obj.PropertiesList):
            obj.addProperty(type_name, name, group, description)

    def _ensure_properties(self, obj):
        self._add_property(
            obj,
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        self._add_property(
            obj,
            "App::PropertyFile",
            "probefile",
            "ProbeData",
            QT_TRANSLATE_NOOP("App::Property", "The point file from the surface probing."),
        )
        self._add_property(obj, "Part::PropertyPartShape", "interpSurface", "Path")
        self._add_property(
            obj,
            "App::PropertyDistance",
            "ArcInterpolate",
            "Interpolate",
            QT_TRANSLATE_NOOP("App::Property", "Deflection distance for arc interpolation"),
        )
        self._add_property(
            obj,
            "App::PropertyDistance",
            "SegInterpolate",
            "Interpolate",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "break segments into smaller segments of this length.",
            ),
        )
        self._add_property(
            obj,
            "App::PropertyString",
            "ProbeDataSHA256",
            "ProbeData",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Content hash of the human-authorized probe map embedded in this operation.",
            ),
        )
        self._add_property(
            obj,
            "App::PropertyInteger",
            "ProbePointCount",
            "ProbeData",
            QT_TRANSLATE_NOOP("App::Property", "Validated probe-map point count."),
        )
        self._add_property(
            obj,
            "App::PropertyInteger",
            "ProbeGridXCount",
            "ProbeData",
            QT_TRANSLATE_NOOP("App::Property", "Validated probe-map X count."),
        )
        self._add_property(
            obj,
            "App::PropertyInteger",
            "ProbeGridYCount",
            "ProbeData",
            QT_TRANSLATE_NOOP("App::Property", "Validated probe-map Y count."),
        )
        self._add_property(
            obj,
            "App::PropertyStringList",
            "VibeCADExternalInputs",
            "ProbeData",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Names of external files explicitly authorized for this operation.",
            ),
        )
        for property_name in (
            "ProbeDataSHA256",
            "ProbePointCount",
            "ProbeGridXCount",
            "ProbeGridYCount",
            "VibeCADExternalInputs",
        ):
            obj.setEditorMode(property_name, 2)

    def onDocumentRestored(self, obj):
        self._ensure_properties(obj)

    def dumps(self):
        return

    def loads(self, state):
        return

    def onChanged(self, obj, prop):
        if prop == "probefile" and str(getattr(obj, "probefile", "") or ""):
            obj.ProbeDataSHA256 = ""
            obj.ProbePointCount = 0
            obj.ProbeGridXCount = 0
            obj.ProbeGridYCount = 0
            obj.VibeCADExternalInputs = []
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def _getinterpSurface(self, obj):
        if (
            str(getattr(obj, "ProbeDataSHA256", "") or "")
            and not obj.interpSurface.isNull()
        ):
            return True
        filename = str(obj.probefile or "")
        if not filename:
            return False
        try:
            grid = ZCorrectCore.read_probe_file(filename, strict=False)
            obj.interpSurface = ZCorrectCore.build_interpolation_surface(grid)
            obj.ProbePointCount = grid.point_count
            obj.ProbeGridXCount = grid.x_count
            obj.ProbeGridYCount = grid.y_count
            if grid.skipped_line_count:
                Path.Log.warning(
                    translate(
                        "CAM_DressupZCorrect",
                        "Skipped %s malformed probe-data lines in file: %s",
                    )
                    % (grid.skipped_line_count, filename)
                )
            return True
        except (OSError, TypeError, ValueError) as exc:
            import Part

            obj.interpSurface = Part.Shape()
            obj.ProbePointCount = 0
            obj.ProbeGridXCount = 0
            obj.ProbeGridYCount = 0
            Path.Log.warning(
                translate(
                    "CAM_DressupZCorrect",
                    "Could not build a Z Correction surface from %s: %s",
                )
                % (filename, str(exc))
            )
            return False

    def execute(self, obj):
        if not PathUtil.activeForOp(obj):
            obj.Path = Path.Path()
            return
        if not obj.Base or not obj.Base.isDerivedFrom("Path::Feature") or not obj.Base.Path:
            obj.Path = Path.Path()
            return

        path = PathUtils.getPathWithPlacement(obj.Base)
        if not path.Commands:
            obj.Path = Path.Path()
            return

        if not self._getinterpSurface(obj) or obj.interpSurface.isNull():
            obj.Path = path
            return
        try:
            source = ZCorrectCore.freeze_toolpath(
                path,
                maximum_commands=ZCorrectCore.MAX_Z_CORRECT_INPUT_COMMANDS,
            )
            generated = ZCorrectCore.generate_corrected_path(
                source,
                obj.interpSurface,
                ZCorrectCore.ZCorrectionDefinition(
                    arc_maximum_deflection_mm=float(obj.ArcInterpolate.Value),
                    line_maximum_segment_length_mm=float(obj.SegInterpolate.Value),
                ),
            )
            obj.Path = generated.path
        except (TypeError, ValueError) as exc:
            obj.Path = path
            Path.Log.warning(
                translate(
                    "CAM_DressupZCorrect",
                    "Could not apply Z Correction: %s",
                )
                % str(exc)
            )


class TaskPanel:
    def __init__(self, obj, transaction=None, viewProvider=None):
        self.obj = obj
        if transaction is None:
            transaction = TaskDocumentTransaction(
                obj,
                "Edit Z Correction Dress-up",
            )
        elif transaction.document is not obj.Document:
            raise RuntimeError(
                "The Z Correction task transaction belongs to another document"
            )
        self.transaction = transaction
        self.document = self.transaction.document
        self.viewProvider = viewProvider
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/ZCorrectEdit.ui")
        self.interpshape = self.document.addObject(
            "Part::Feature",
            "InterpolationSurface",
        )
        self.interpshape.Shape = obj.interpSurface
        self.interpshape.ViewObject.Transparency = 60
        self.interpshape.ViewObject.ShapeColor = (1.00000, 1.00000, 0.01961)
        self.interpshape.ViewObject.Selectable = False
        stock = PathUtils.findParentJob(obj).Stock
        self.interpshape.Placement.Base.z = stock.Shape.BoundBox.ZMax

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
        if self.document.getObject(self.interpshape.Name) is self.interpshape:
            self.document.removeObject(self.interpshape.Name)
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
        self.interpshape = None
        self.transaction.close_dialog()

    def getFields(self):
        self.obj.Proxy.execute(self.obj)

    def updateUI(self):
        if Path.Log.getLevel(LOG_MODULE) == Path.Log.Level.DEBUG:
            for obj in self.document.Objects:
                if obj.Name.startswith("Shape"):
                    self.document.removeObject(obj.Name)
            print("object name %s" % self.obj.Name)
            if hasattr(self.obj.Proxy, "shapes"):
                Path.Log.info("showing shapes attribute")
                for shapes in self.obj.Proxy.shapes.itervalues():
                    for shape in shapes:
                        debug_shape = self.document.addObject(
                            "Part::Feature",
                            "Shape",
                        )
                        debug_shape.Shape = shape
            else:
                Path.Log.info("no shapes attribute found")

    def updateModel(self):
        if not self.transaction.is_open():
            return
        self.getFields()
        self.updateUI()
        self.transaction.recompute((self.obj,))

    def setFields(self):
        self.form.ProbePointFileName.setText(self.obj.probefile)
        self.updateUI()

    def open(self):
        pass

    def setupUi(self):
        self.setFields()
        # now that the form is filled, setup the signal handlers
        self.form.ProbePointFileName.editingFinished.connect(self.updateModel)
        self.form.SetProbePointFileName.clicked.connect(self.SetProbePointFileName)

    def SetProbePointFileName(self):
        filename = QtGui.QFileDialog.getOpenFileName(
            self.form,
            translate("CAM_Probe", "Select Probe Point File"),
            None,
            translate("CAM_Probe", "All Files (*.*)"),
        )
        if filename and filename[0]:
            self.obj.probefile = str(filename[0])
            self.setFields()


class ViewProviderDressup:
    def __init__(self, vobj):
        self.panel = None
        self.attach(vobj)
        vobj.Proxy = self

    def attach(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        self._job_name = ""
        try:
            job = PathUtils.findParentJob(self.obj)
            if job is not None and job.Document is self.obj.Document:
                self._job_name = str(job.Name)
        except (ReferenceError, RuntimeError):
            pass

    def claimChildren(self):
        return [self.obj.Base]

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        if mode == 1:
            FreeCADGui.runCommand("Std_TransformManip")
        elif mode == 0:
            transaction = TaskDocumentTransaction(
                vobj.Object,
                "Edit Z Correction Dress-up",
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
            except Exception:
                self.panel = None
                transaction.close_dialog()
                if transaction.owns_transaction():
                    transaction.abort()
                raise
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode == 0 and self.panel is not None:
            self.panel.reject()
        return False

    def dumps(self):
        return None

    def loads(self, state):
        return None

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
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not tuple(base.Path.Commands or ())
        or not is_timeline_input_usable(base, document)
    ):
        return None

    job = PathUtils.findParentJob(base)
    if (
        not is_document_object(job, document)
        or not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
        or getattr(job, "Operations", None) is None
        or base not in tuple(job.Operations.Group or ())
        or not hasattr(getattr(job, "Proxy", None), "addOperation")
        or not is_timeline_input_usable(job, document)
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
        or base in job.Operations.Group
        or PathUtil.timelineParentJob(result) is not job
        or "VibeCADTimelineReplacedInputs" not in result.PropertiesList
        or list(result.VibeCADTimelineReplacedInputs) != replaced_inputs
        or str(result.VibeCADTimelineRole) != "operation"
        or not result.isValid()
        or bool(base.ViewObject.Visibility)
        or not bool(result.ViewObject.Visibility)
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            result
        )
    ):
        raise RuntimeError(
            "The Z Correction CAM dress-up was not created as one exact "
            "replacement operation"
        )


def createDressupFeature(document, name="ZCorrectDressup"):
    """Create and initialize one exact Z Correction dress-up feature."""
    if document is None:
        raise RuntimeError(
            "A document is required for a Z Correction dress-up"
        )
    result = document.addObject(
        "Path::FeaturePython",
        name,
    )
    ObjectDressup(result)
    return result


def CreateInTransaction(base, name="ZCorrectDressup", hide_base=True):
    """Create one Z Correction replacement inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot receive Z Correction")
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
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupZCorrect", "Z Depth Correction"),
            "Accel": "",
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupZCorrect", "Corrects Z depth using a probe map"
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
            "Create Z Correction Dress-up",
            document,
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.ZCorrect")
            FreeCADGui.addModule("Path.Base.Util")
            FreeCADGui.addModule("PathScripts.PathUtils")
            FreeCADGui.doCommand(
                "document = FreeCAD.getDocument(%r)"
                % document.Name
            )
            FreeCADGui.doCommand(
                "base = document.getObject(%r)" % base_name
            )
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.ZCorrect.CreateInTransaction(base)",
                "Path::FeaturePython",
            )
            result_name = str(result.Name)
            result_id = int(result.ID)
            result_expression = "document.getObject(%r)" % result_name
            FreeCADGui.doCommand(
                "job = PathScripts.PathUtils.findParentJob(base)"
            )
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
    FreeCADGui.addCommand("CAM_DressupZCorrect", CommandPathDressup())

FreeCAD.Console.PrintLog("Loading PathDressup… done\n")
