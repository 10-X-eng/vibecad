# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

import FreeCAD
import FreeCADGui
import Part
import Path
import Path.Base.Util as PathUtil
import Path.Op.Base as OpBase
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import (
    ExactDocumentObjectIdentity,
    active_jobs,
    can_start_document_command,
    is_timeline_input_usable,
)
from VibeCADNativeTransaction import _OwnedDocumentTransaction

from PySide.QtCore import QT_TRANSLATE_NOOP

__title__ = "CAM Path from Shape with Tool Controller"
__author__ = ""
__inspirer__ = "Russ4262"
__url__ = "https://forum.freecad.org/viewtopic.php?t=93896"
__doc__ = ""


if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


translate = FreeCAD.Qt.translate


def _makeDocumentClone(document, source):
    """Create Draft's linked clone in the captured CAM document."""

    from draftobjects.clone import Clone
    from draftutils import utils as DraftUtils

    if (
        source.isDerivedFrom("Part::Part2DObject")
        and DraftUtils.get_type(source)
        not in {"BezCurve", "BSpline", "Wire"}
    ):
        clone = document.addObject(
            "Part::Part2DObjectPython",
            "Clone2D",
        )
    else:
        clone = document.addObject("Part::FeaturePython", "Clone")
        clone.addExtension("Part::AttachExtensionPython")

    Clone(clone)
    clone.Objects = [source]
    if hasattr(source, "Placement"):
        clone.Placement = source.Placement
    if hasattr(clone, "LongName") and hasattr(source, "LongName"):
        clone.LongName = source.LongName
    if FreeCAD.GuiUp:
        from draftviewproviders.view_clone import ViewProviderClone

        ViewProviderClone(clone.ViewObject)
    return clone


# Add base set of operation properties
def _addBaseProperties(obj):
    obj.addProperty(
        "App::PropertyBool",
        "Active",
        "Path",
        QT_TRANSLATE_NOOP("App::Property", "Make False, to prevent operation from generating code"),
        locked=True,
    )
    obj.addProperty(
        "App::PropertyString",
        "Comment",
        "Path",
        QT_TRANSLATE_NOOP("App::Property", "An optional comment for this operation"),
        locked=True,
    )
    obj.addProperty(
        "App::PropertyString",
        "UserLabel",
        "Path",
        QT_TRANSLATE_NOOP("App::Property", "User assigned label"),
        locked=True,
    )
    obj.addProperty(
        "App::PropertyString",
        "CycleTime",
        "Path",
        QT_TRANSLATE_NOOP("App::Property", "Operations cycle time estimation"),
        locked=True,
    )
    obj.setEditorMode("CycleTime", 1)  # Set property read-only
    obj.Active = True


# Add ToolController properties
def _addToolController(obj):
    obj.addProperty(
        "App::PropertyLink",
        "ToolController",
        "Path",
        QT_TRANSLATE_NOOP(
            "App::Property",
            "The tool controller that will be used to calculate the path",
        ),
    )
    obj.addProperty(
        "App::PropertyDistance",
        "OpToolDiameter",
        "Op Values",
        QT_TRANSLATE_NOOP("App::Property", "Holds the diameter of the tool"),
    )
    obj.setEditorMode("OpToolDiameter", 1)  # Set property read-only
    document = obj.Document
    controllers = [
        controller
        for controller in _getToolControllers(obj)
        if (
            getattr(controller, "Document", None) is document
            and document.getObject(controller.Name) is controller
            and controller.isValid()
            and getattr(controller, "Tool", None) is not None
            and controller.Tool.Document is document
            and document.getObject(controller.Tool.Name) is controller.Tool
            and controller.Tool.isValid()
            and hasattr(controller.Tool, "Diameter")
        )
    ]
    if not controllers:
        raise OpBase.PathNoTCException()
    controller = (
        PathUtils.UserInput.selectedToolController()
        if PathUtils.UserInput
        else None
    )
    if controller not in controllers:
        if len(controllers) == 1:
            controller = controllers[0]
        elif PathUtils.UserInput:
            controller = PathUtils.UserInput.chooseToolController(
                controllers
            )
        else:
            controller = controllers[0]
    if controller is None:
        return False
    obj.ToolController = controller
    obj.OpToolDiameter = obj.ToolController.Tool.Diameter

    obj.FeedRate = obj.ToolController.HorizFeed.Value
    obj.FeedRateVertical = obj.ToolController.VertFeed.Value
    return True


# Get list of tool controllers
def _getToolControllers(obj, proxy=None):
    # Modified getToolControllers() from PathScripts.PathUtils
    # for Path object without Proxy
    job = PathUtils.findParentJob(obj)
    if job:
        return [tc for tc in job.Tools.Group]
    else:
        return []


# Set safety height parameters for Path operation
def _setSafetyZ(obj):
    job = PathUtils.findParentJob(obj)
    if job:
        safetyZ = job.Stock.Shape.BoundBox.ZMax + 10
        obj.RetractThreshold = safetyZ
        obj.Retraction = safetyZ
        obj.ResumeHeight = safetyZ


# Geometry for selected shapes
class ObjectPartShape:
    def __init__(self, obj, base):
        # Path.Log.info("ObjectPartShape.__init__()")
        self.obj = obj
        obj.addProperty(
            "App::PropertyLinkSubListGlobal",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base geometry for this operation"),
        )
        obj.Base = base

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

    def onDelete(self, obj, args):
        return True

    def onDocumentRestored(self, obj):
        self.obj = obj

    def onChanged(self, obj, prop):
        """onChanged(obj, prop) ... method called when objECT is changed,
        with source propERTY of the change."""
        if "Restore" in obj.State:
            pass

    def execute(self, obj):
        edges = []
        if obj.Base:
            base, subNames = obj.Base[0]
            edges = [
                base.Shape.getElement(sub).copy() for sub in subNames if sub.startswith("Edge")
            ]

        if edges:
            obj.Shape = Part.Wire(Part.__sortEdges__(edges))
        else:
            obj.Shape = Part.Shape()


class CommandPathShapeTC:
    def GetResources(self):
        return {
            "Pixmap": "CAM_ShapeTC",
            "MenuText": QT_TRANSLATE_NOOP("CAM_PathShapeTC", "Path From Shape TC"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_PathShapeTC",
                "Creates a path from the selected shapes with the tool controller",
            ),
        }

    def IsActive(self):
        if (
            not can_start_document_command()
            or not active_jobs(require_tool=True)
        ):
            return False
        selection = FreeCADGui.Selection.getSelectionEx()
        if len(selection) != 1:
            return False
        base = selection[0].Object
        document = FreeCAD.ActiveDocument
        if (
            base is None
            or base.Document is not document
            or document.getObject(base.Name) is not base
            or not base.isValid()
            or not is_timeline_input_usable(base, document)
            or not hasattr(base, "Shape")
            or base.Shape.isNull()
        ):
            return False

        selected_edges = [
            name
            for name in selection[0].SubElementNames
            if name.startswith("Edge")
        ]
        return bool(
            selected_edges
            or base.Shape.ShapeType in {"Wire", "Edge"}
        )

    def Activated(self):
        if not self.IsActive():
            return

        doc = FreeCAD.ActiveDocument
        selection = FreeCADGui.Selection.getSelectionEx()
        base = selection[0].Object
        subEdges = [
            name
            for name in selection[0].SubElementNames
            if name.startswith("Edge")
        ]
        jobs = active_jobs(require_tool=True)
        job = PathUtils.UserInput.chooseJob(jobs)
        if (
            job is None
            or job.Document is not doc
            or FreeCAD.ActiveDocument is not doc
        ):
            return
        base_identity = ExactDocumentObjectIdentity(base, doc)
        job_identity = ExactDocumentObjectIdentity(job, doc)

        transaction = _OwnedDocumentTransaction(
            doc,
            "Create path from shape",
        )
        try:
            base = base_identity.resolve(require_timeline=True)
            job = job_identity.resolve(require_timeline=True)
            if subEdges:
                shapeObj = doc.addObject("Part::FeaturePython", "PartShape")
                shapeObj.ViewObject.Proxy = 0
                shapeObj.Visibility = False
                shapeObj.Proxy = ObjectPartShape(shapeObj, [(base, subEdges)])
            else:
                shapeObj = _makeDocumentClone(doc, base)
            if (
                shapeObj is None
                or shapeObj.Document is not doc
                or doc.getObject(shapeObj.Name) is not shapeObj
            ):
                raise RuntimeError(
                    "The selected shape could not be copied for CAM"
                )
            shapeObj.ViewObject.Visibility = False

            pathObj = doc.addObject(
                "Path::FeatureShape",
                "PathShape",
            )
            pathObj.Sources = [shapeObj]
            PathUtil.markTimelineOperation(pathObj)

            job.Proxy.addOperation(pathObj)
            _addBaseProperties(pathObj)
            if not _addToolController(pathObj):
                transaction.abort()
                return None
            _setSafetyZ(pathObj)
            doc.recompute()
            base = base_identity.resolve(require_timeline=True)
            job = job_identity.resolve(require_timeline=True)
            if (
                not pathObj.isValid()
                or list(pathObj.Sources) != [shapeObj]
                or pathObj not in job.Operations.Group
            ):
                raise RuntimeError(
                    "The path-from-shape operation is invalid"
                )
            doc.publishProvisionalTimelineOperationBlock(
                pathObj,
                [shapeObj],
            )
        except Exception:
            transaction.abort()
            raise
        transaction.commit()
        return pathObj


if FreeCAD.GuiUp:
    # Register the FreeCAD command
    FreeCADGui.addCommand("CAM_PathShapeTC", CommandPathShapeTC())
