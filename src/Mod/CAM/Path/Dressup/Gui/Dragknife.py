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
import Path
import Path.Base.Gui.Util as PathGuiUtil
import Path.Base.Util as PathUtil
from PySide import QtCore
import math
import PathScripts.PathUtils as PathUtils
import Path.Dressup.Utils as PathDressup
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)
from PySide.QtCore import QT_TRANSLATE_NOOP

# lazily loaded modules
from lazy_loader.lazy_loader import LazyLoader

D = LazyLoader("DraftVecUtils", globals(), "DraftVecUtils")

__doc__ = """Dragknife Dressup object and FreeCAD command"""

if FreeCAD.GuiUp:
    import FreeCADGui


translate = FreeCAD.Qt.translate


movecommands = ["G1", "G01", "G2", "G02", "G3", "G03"]
rapidcommands = ["G0", "G00"]
arccommands = ["G2", "G3", "G02", "G03"]

currLocation = {}


class ObjectDressup:
    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        obj.addProperty(
            "App::PropertyAngle",
            "filterAngle",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Angles less than filter angle will not receive corner actions",
            ),
        )
        obj.addProperty(
            "App::PropertyFloat",
            "offset",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Distance the point trails behind the spindle"),
        )
        obj.addProperty(
            "App::PropertyFloat",
            "pivotheight",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Height to raise during corner action"),
        )

        obj.Proxy = self

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, obj, prop):
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def shortcut(self, queue):
        """Determines whether its shorter to twist CW or CCW to align with
        the next move"""
        # get the vector of the last move

        if queue[1].Name in arccommands:
            arcLoc = FreeCAD.Vector(
                queue[2].x + queue[1].I, queue[2].y + queue[1].J, currLocation["Z"]
            )
            radvector = arcLoc.sub(
                queue[1].Placement.Base
            )  # .sub(arcLoc)  # vector of chord from center to point
            # vector of line perp to chord.
            v1 = radvector.cross(FreeCAD.Vector(0, 0, 1))
        else:
            v1 = queue[1].Placement.Base.sub(queue[2].Placement.Base)

        # get the vector of the current move
        if queue[0].Name in arccommands:
            arcLoc = FreeCAD.Vector(
                (queue[1].x + queue[0].I), (queue[1].y + queue[0].J), currLocation["Z"]
            )
            radvector = queue[1].Placement.Base.sub(arcLoc)  # calculate arcangle
            v2 = radvector.cross(FreeCAD.Vector(0, 0, 1))
        else:
            v2 = queue[0].Placement.Base.sub(queue[1].Placement.Base)

        if (v2.x * v1.y) - (v2.y * v1.x) >= 0:
            return "CW"
        else:
            return "CCW"

    def segmentAngleXY(self, prevCommand, currCommand, endpos=False, currentZ=0):
        """returns in the starting angle in radians for a Path command.
        requires the previous command in order to calculate arcs correctly
        if endpos = True, return the angle at the end of the segment."""

        if currCommand.Name in arccommands:
            arcLoc = FreeCAD.Vector(
                (prevCommand.x + currCommand.I),
                (prevCommand.y + currCommand.J),
                currentZ,
            )
            if endpos is True:
                radvector = arcLoc.sub(
                    currCommand.Placement.Base
                )  # Calculate vector at start of arc
            else:
                radvector = arcLoc.sub(prevCommand.Placement.Base)  # Calculate vector at end of arc

            v1 = radvector.cross(FreeCAD.Vector(0, 0, 1))
            if currCommand.Name in ["G2", "G02"]:
                v1 = D.rotate2D(v1, math.radians(180))
        else:
            v1 = currCommand.Placement.Base.sub(
                prevCommand.Placement.Base
            )  # Straight segments are easy

        myAngle = D.angle(v1, FreeCAD.Base.Vector(1, 0, 0), FreeCAD.Base.Vector(0, 0, -1))
        return myAngle

    def getIncidentAngle(self, queue):
        # '''returns in the incident angle in degrees between the current and previous moves'''

        angleatend = float(math.degrees(self.segmentAngleXY(queue[2], queue[1], True)))
        angleatstart = float(math.degrees(self.segmentAngleXY(queue[1], queue[0])))

        incident_angle = (angleatstart - angleatend + 360) % 360

        # The incident can never be greater than 180 degrees.  If it is
        # then we need to measure the other way around the circle.
        if incident_angle > 180:
            incident_angle = 360 - incident_angle

        return incident_angle

    def arcExtension(self, obj, queue):
        """returns gcode for arc extension"""
        global currLocation
        results = []

        offset = obj.offset
        # Find the center of the old arc
        C = FreeCAD.Base.Vector(queue[2].x + queue[1].I, queue[2].y + queue[1].J, currLocation["Z"])

        # Find radius of old arc
        R = math.hypot(queue[1].I, queue[1].J)

        # Find angle subtended by the extension arc
        theta = math.atan2(queue[1].y - C.y, queue[1].x - C.x)
        if queue[1].Name in ["G2", "G02"]:
            theta = theta - offset / R
        else:
            theta = theta + offset / R

        # XY coordinates of new arc endpoint.
        Bx = C.x + R * math.cos(theta)
        By = C.y + R * math.sin(theta)

        # endpoint = FreeCAD.Base.Vector(Bx, By, currLocation["Z"])
        startpoint = queue[1].Placement.Base
        offsetvector = C.sub(startpoint)

        I = offsetvector.x
        J = offsetvector.y

        extend = Path.Command(queue[1].Name, {"I": I, "J": J, "X": Bx, "Y": By})
        results.append(extend)
        currLocation.update(extend.Parameters)

        replace = None
        return (results, replace)

    def arcTwist(self, obj, queue, lastXY, twistCW=False):
        """returns gcode to do an arc move toward an arc to perform
        a corner action twist. Includes lifting and plungeing the knife"""

        global currLocation
        pivotheight = obj.pivotheight
        offset = obj.offset
        results = []

        # set the correct twist command
        if twistCW is False:
            arcdir = "G3"
        else:
            arcdir = "G2"

        # move to the pivot height
        zdepth = currLocation["Z"]
        retract = Path.Command("G0", {"Z": pivotheight})
        results.append(retract)
        currLocation.update(retract.Parameters)

        # get the center of the destination arc
        arccenter = FreeCAD.Base.Vector(
            queue[1].x + queue[0].I, queue[1].y + queue[0].J, currLocation["Z"]
        )

        # The center of the twist arc is the old line end point.
        C = queue[1].Placement.Base

        # Find radius of old arc
        R = math.hypot(queue[0].I, queue[0].J)

        # find angle of original center to startpoint
        v1 = queue[1].Placement.Base.sub(arccenter)
        segAngle = D.angle(v1, FreeCAD.Base.Vector(1, 0, 0), FreeCAD.Base.Vector(0, 0, -1))

        # Find angle subtended by the offset
        theta = offset / R

        # add or subtract theta depending on direction
        if queue[1].Name in ["G2", "G02"]:
            newangle = segAngle + theta
        else:
            newangle = segAngle - theta

        # calculate endpoints
        Bx = arccenter.x + R * math.cos(newangle)
        By = arccenter.y + R * math.sin(newangle)
        endpointvector = FreeCAD.Base.Vector(Bx, By, currLocation["Z"])

        # calculate IJ offsets of twist arc from current position.
        offsetvector = C.sub(lastXY)

        # add G2/G3 move
        arcmove = Path.Command(
            arcdir,
            {
                "X": endpointvector.x,
                "Y": endpointvector.y,
                "I": offsetvector.x,
                "J": offsetvector.y,
            },
        )
        results.append(arcmove)
        currLocation.update(arcmove.Parameters)

        # plunge back to depth
        plunge = Path.Command("G1", {"Z": zdepth})
        results.append(plunge)
        currLocation.update(plunge.Parameters)

        # The old arc move won't work so calculate a replacement command
        offsetv = arccenter.sub(endpointvector)

        replace = Path.Command(
            queue[0].Name,
            {"X": queue[0].X, "Y": queue[0].Y, "I": offsetv.x, "J": offsetv.y},
        )
        return (results, replace)

    def lineExtension(self, obj, queue):
        """returns gcode for line extension"""
        global currLocation

        offset = float(obj.offset)
        results = []

        v1 = queue[1].Placement.Base.sub(queue[2].Placement.Base)

        # extend the current segment to comp for offset
        segAngle = D.angle(v1, FreeCAD.Base.Vector(1, 0, 0), FreeCAD.Base.Vector(0, 0, -1))
        xoffset = math.cos(segAngle) * offset
        yoffset = math.sin(segAngle) * offset

        newX = currLocation["X"] + xoffset
        newY = currLocation["Y"] + yoffset

        extendcommand = Path.Command("G1", {"X": newX, "Y": newY})
        results.append(extendcommand)

        currLocation.update(extendcommand.Parameters)

        replace = None
        return (results, replace)

    def lineTwist(self, obj, queue, lastXY, twistCW=False):
        """returns gcode to do an arc move toward a line to perform
        a corner action twist. Includes lifting and plungeing the knife"""
        global currLocation
        pivotheight = obj.pivotheight
        offset = obj.offset

        results = []

        # set the correct twist command
        if twistCW is False:
            arcdir = "G3"
        else:
            arcdir = "G2"

        # move to pivot height
        zdepth = currLocation["Z"]
        retract = Path.Command("G0", {"Z": pivotheight})
        results.append(retract)
        currLocation.update(retract.Parameters)

        C = queue[1].Placement.Base

        # get the vectors between endpoints to calculate twist
        v2 = queue[0].Placement.Base.sub(queue[1].Placement.Base)

        # calc arc endpoints to twist to
        segAngle = D.angle(v2, FreeCAD.Base.Vector(1, 0, 0), FreeCAD.Base.Vector(0, 0, -1))
        xoffset = math.cos(segAngle) * offset
        yoffset = math.sin(segAngle) * offset
        newX = queue[1].x + xoffset
        newY = queue[1].y + yoffset

        offsetvector = C.sub(lastXY)
        I = offsetvector.x
        J = offsetvector.y

        # add the arc move
        arcmove = Path.Command(arcdir, {"X": newX, "Y": newY, "I": I, "J": J})  # add G2/G3 move
        results.append(arcmove)

        currLocation.update(arcmove.Parameters)

        # plunge back to depth
        plunge = Path.Command("G1", {"Z": zdepth})
        results.append(plunge)
        currLocation.update(plunge.Parameters)

        replace = None
        return (results, replace)

    def execute(self, obj):
        newpath = []
        global currLocation

        if not PathUtil.activeForOp(obj):
            obj.Path = Path.Path()
            return

        if not obj.Base:
            obj.Path = Path.Path()
            return

        if not obj.Base.isDerivedFrom("Path::Feature"):
            obj.Path = Path.Path()
            return

        if not obj.Base.Path.Commands:
            obj.Path = Path.Path()
            return

        if obj.Base.Path.Commands:
            firstmove = Path.Command("G0", {"X": 0, "Y": 0, "Z": 0})
            currLocation.update(firstmove.Parameters)

            queue = []

            for curCommand in PathUtils.getPathWithPlacement(obj.Base).Commands:
                replace = None
                # don't worry about non-move commands, just add to output
                if curCommand.Name not in movecommands + rapidcommands:
                    newpath.append(curCommand)
                    continue

                if curCommand.x is None:
                    curCommand.x = currLocation["X"]
                if curCommand.y is None:
                    curCommand.y = currLocation["Y"]
                if curCommand.z is None:
                    curCommand.z = currLocation["Z"]

                # rapid retract triggers exit move, else just add to output
                if curCommand.Name in rapidcommands:
                    if (curCommand.z > obj.pivotheight) and (len(queue) == 3):
                        # Process the exit move
                        tempqueue = queue
                        tempqueue.insert(0, curCommand)

                        if queue[1].Name in ["G01", "G1"]:
                            temp = self.lineExtension(obj, tempqueue)
                            newpath.extend(temp[0])
                            lastxy = temp[0][-1].Placement.Base
                        elif queue[1].Name in arccommands:
                            temp = self.arcExtension(obj, tempqueue)
                            newpath.extend(temp[0])
                            lastxy = temp[0][-1].Placement.Base

                    newpath.append(curCommand)
                    currLocation.update(curCommand.Parameters)
                    queue = []
                    continue

                # keep a queue of feed moves and check for needed corners
                if curCommand.Name in movecommands:
                    changedXYFlag = False
                    if queue:
                        if (curCommand.x != queue[0].x) or (curCommand.y != queue[0].y):
                            queue.insert(0, curCommand)
                            if len(queue) > 3:
                                queue.pop()
                            changedXYFlag = True
                    else:
                        queue = [curCommand]

                    # vertical feeding to depth
                    if curCommand.z != currLocation["Z"]:
                        newpath.append(curCommand)
                        currLocation.update(curCommand.Parameters)
                        continue

                    # Corner possibly needed
                    if changedXYFlag and (len(queue) == 3):

                        # check if the inciden angle incident exceeds the filter
                        incident_angle = self.getIncidentAngle(queue)

                        if abs(incident_angle) >= obj.filterAngle:
                            if self.shortcut(queue) == "CW":
                                # if incident_angle >= 0:
                                twistCW = True
                            else:
                                twistCW = False
                            #
                            #  DO THE EXTENSION
                            #
                            if queue[1].Name in ["G01", "G1"]:
                                temp = self.lineExtension(obj, queue)
                                newpath.extend(temp[0])
                                replace = temp[1]
                                lastxy = temp[0][-1].Placement.Base
                            elif queue[1].Name in arccommands:
                                temp = self.arcExtension(obj, queue)
                                newpath.extend(temp[0])
                                replace = temp[1]
                                lastxy = temp[0][-1].Placement.Base
                            else:
                                FreeCAD.Console.PrintWarning("I don't know what's up")
                            #
                            #  DO THE TWIST
                            #
                            if queue[0].Name in ["G01", "G1"]:
                                temp = self.lineTwist(obj, queue, lastxy, twistCW)
                                replace = temp[1]
                                newpath.extend(temp[0])
                            elif queue[0].Name in arccommands:
                                temp = self.arcTwist(obj, queue, lastxy, twistCW)
                                replace = temp[1]
                                newpath.extend(temp[0])
                            else:
                                FreeCAD.Console.PrintWarning("I don't know what's up")
                    if replace is None:
                        newpath.append(curCommand)
                    else:
                        newpath.append(replace)
                    currLocation.update(curCommand.Parameters)
                    continue

            commands = newpath
            path = Path.Path(commands)
            obj.Path = path


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
                    print(i.Group)
            # FreeCADGui.ActiveDocument.getObject(obj.Base.Name).Visibility = False

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
            "The Drag Knife CAM dress-up was not created as one exact "
            "replacement operation"
        )


def createDressupFeature(document):
    """Create and initialize one exact Drag Knife dress-up feature."""
    if document is None:
        raise RuntimeError("A document is required for a Drag Knife dress-up")
    result = document.addObject(
        "Path::FeaturePython",
        "DragknifeDressup",
    )
    ObjectDressup(result)
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
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.Dragknife.createDressupFeature(document)",
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
                f"{result_expression}.ViewObject.Proxy = "
                "Path.Dressup.Gui.Dragknife.ViewProviderDressup("
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
