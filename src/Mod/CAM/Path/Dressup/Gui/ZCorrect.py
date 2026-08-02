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
import Path.Dressup.Utils as PathDressup
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP

import os

# lazily loaded modules
from lazy_loader.lazy_loader import LazyLoader

Part = LazyLoader("Part", globals(), "Part")

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
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        obj.addProperty(
            "App::PropertyFile",
            "probefile",
            "ProbeData",
            QT_TRANSLATE_NOOP("App::Property", "The point file from the surface probing."),
        )
        obj.addProperty("Part::PropertyPartShape", "interpSurface", "Path")
        obj.addProperty(
            "App::PropertyDistance",
            "ArcInterpolate",
            "Interpolate",
            QT_TRANSLATE_NOOP("App::Property", "Deflection distance for arc interpolation"),
        )
        obj.addProperty(
            "App::PropertyDistance",
            "SegInterpolate",
            "Interpolate",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "break segments into smaller segments of this length.",
            ),
        )
        obj.Proxy = self
        obj.ArcInterpolate = 0.1
        obj.SegInterpolate = 1.0

    def dumps(self):
        return

    def loads(self, state):
        return

    def onChanged(self, obj, prop):
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def _bilinearInterpolate(self, surface, x, y):
        p1 = FreeCAD.Vector(x, y, 100.0)
        p2 = FreeCAD.Vector(x, y, -100.0)

        vertical_line = Part.Line(p1, p2)
        points, _ = vertical_line.intersectCS(surface)
        return points[0].Z

    def _getinterpSurface(self, obj):
        filename = obj.probefile
        if not filename:
            return

        if not os.path.isfile(filename):
            Path.Log.warning(
                translate("CAM_DressupZCorrect", "Probe file not found: %s") % filename
            )
            return

        with open(filename, "r") as file:
            lines = file.readlines()

        pointlist = []
        skipped = []
        for i, line in enumerate(lines):
            w = line.replace(",", ".").split()
            if len(w) < 3:
                skipped.append(i + 1)
                continue
            try:
                xval = round(float(w[0]), 2)
                yval = round(float(w[1]), 2)
                zval = round(float(w[2]), 2)
            except ValueError:
                skipped.append(i + 1)
                continue
            pointlist.append((xval, yval, zval))

        if skipped:
            Path.Log.warning(
                translate("CAM_DressupZCorrect", "Skipped non-data lines in file: %s (lines %s)")
                % (filename, ", ".join(str(n) for n in skipped))
            )

        if len(pointlist) < 3:
            obj.interpSurface = Part.Shape()
            Path.Log.warning(
                translate("CAM_DressupZCorrect", "Not enough points (%s) got from file: %s")
                % (len(pointlist), filename)
            )
            return

        cols = list(zip(*pointlist))
        yindex = list(sorted(set(cols[1])))

        Path.Log.debug(pointlist)
        Path.Log.debug("cols: {}".format(cols))
        Path.Log.debug("yindex: {}".format(yindex))

        array = []
        for y in yindex:
            points = sorted([p for p in pointlist if p[1] == y])
            array.append([FreeCAD.Vector(p[0], p[1], p[2]) for p in points])

        intSurf = Part.BSplineSurface()
        try:
            intSurf.interpolate(array)
            obj.interpSurface = intSurf.toShape()
        except Exception:
            obj.interpSurface = Part.Shape()
            Path.Log.warning(
                translate("CAM_DressupZCorrect", "Failed to create surface from probe data: %s")
                % filename
            )

        return

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

        self._getinterpSurface(obj)
        if obj.interpSurface.isNull():
            # returns base path if no valid probe data
            obj.Path = path
            return

        face = obj.interpSurface.toNurbs().Faces[0]
        surface = face.Surface
        bb = face.BoundBox
        bb.ZMax = 0
        bb.ZMin = 0

        newcommandlist = []
        currLocation = {"X": 0, "Y": 0, "Z": 0, "F": 0}
        for cmd in path.Commands:
            Path.Log.debug(cmd)
            Path.Log.debug("     curLoc:{}".format(currLocation))
            newparams = dict(cmd.Parameters)
            zval = newparams.get("Z", currLocation["Z"])
            if cmd.Name not in Path.Geom.CmdMoveMill:
                # non mill command
                newcommandlist.append(cmd)
                currLocation.update(cmd.Parameters)
            else:
                curVec = FreeCAD.Vector(currLocation["X"], currLocation["Y"], currLocation["Z"])
                edge = Path.Geom.edgeForCmd(cmd, curVec)
                if edge is None:
                    continue
                if cmd.Name in Path.Geom.CmdMoveArc:
                    pointlist = edge.discretize(Deflection=obj.ArcInterpolate.Value)
                else:
                    disc_number = int(edge.Length / obj.SegInterpolate.Value)
                    if disc_number > 1:
                        pointlist = edge.discretize(Number=disc_number)
                    else:
                        pointlist = [v.Point for v in edge.Vertexes]

                for point in pointlist:
                    if not bb.isInside(FreeCAD.Vector(point.x, point.y, 0)):
                        obj.Path = path
                        pointStr = f"({round(point.x, 3)}, {round(point.y, 3)})"
                        bbMin = f"XMin={round(bb.XMin, 3)}, YMin={round(bb.YMin, 3)}"
                        bbMax = f"XMax={round(bb.XMax, 3)}, YMax={round(bb.YMax, 3)}"
                        Path.Log.warning(
                            translate(
                                "CAM_DressupZCorrect",
                                "Path point %s is outside of the probe area %s, %s",
                            )
                            % (pointStr, bbMin, bbMax)
                        )
                        return

                    offset = self._bilinearInterpolate(surface, point.x, point.y)
                    commandparams = {"X": point.x, "Y": point.y, "Z": point.z + offset}
                    if "F" in newparams.keys():
                        commandparams["F"] = newparams["F"]
                    newcommand = Path.Command("G1", commandparams)
                    newcommandlist.append(newcommand)
                    currLocation.update(newcommand.Parameters)
                    currLocation["Z"] = zval

        obj.Path = Path.Path(newcommandlist)


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

    def onDelete(self, arg1=None, arg2=None):
        """this makes sure that the base operation is added back to the project and visible"""
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
        job.Proxy.addOperation(arg1.Object.Base)
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
            "The Z Correction CAM dress-up was not created as one exact "
            "replacement operation"
        )


def createDressupFeature(document):
    """Create and initialize one exact Z Correction dress-up feature."""
    if document is None:
        raise RuntimeError(
            "A document is required for a Z Correction dress-up"
        )
    result = document.addObject(
        "Path::FeaturePython",
        "ZCorrectDressup",
    )
    ObjectDressup(result)
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
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.ZCorrect.createDressupFeature(document)",
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
            FreeCADGui.doCommand(
                "job = PathScripts.PathUtils.findParentJob(base)"
            )
            FreeCADGui.doCommand(f"{result_expression}.Base = base")
            FreeCADGui.doCommand(
                f"job.Proxy.addOperation({result_expression}, base)"
            )
            FreeCADGui.doCommand(
                "Path.Dressup.Gui.ZCorrect.ViewProviderDressup("
                f"{result_expression}.ViewObject)"
            )
            FreeCADGui.doCommand(
                "Path.Base.Util.markTimelineReplacedInputs("
                f"{result_expression}, "
                "[base] if _cam_base_was_visible else [])"
            )
            FreeCADGui.doCommand(
                "Gui.getDocument(document.Name).getObject("
                "base.Name).Visibility = False"
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
