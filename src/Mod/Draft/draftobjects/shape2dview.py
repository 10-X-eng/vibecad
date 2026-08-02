# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2009, 2010 Yorik van Havre <yorik@uncreated.net>        *
# *   Copyright (c) 2009, 2010 Ken Cline <cline@frii.com>                   *
# *   Copyright (c) 2020 FreeCAD Developers                                 *
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
"""Provides the object code for the Shape2dView object."""

## @package shape2dview
# \ingroup draftobjects
# \brief Provides the object code for the Shape2dView object.

## \addtogroup draftobjects
# @{
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import DraftVecUtils
from draftgeoutils import wires as geo_wires
from draftobjects.base import DraftObject
from draftutils import groups
from draftutils import gui_utils
from draftutils import utils
from draftutils.translate import translate


class Shape2DView(DraftObject):
    """The Shape2DView object"""

    def __init__(self, obj):

        self.setProperties(obj)
        super().__init__(obj, "Shape2DView")

    def onDocumentRestored(self, obj):
        self.setProperties(obj)
        super().onDocumentRestored(obj)
        gui_utils.restore_view_object(
            obj, vp_module="view_base", vp_class="ViewProviderDraftAlt", format=False
        )

    def setProperties(self, obj):

        pl = obj.PropertiesList

        if not "Base" in pl:
            _tip = QT_TRANSLATE_NOOP("App::Property", "The base object this 2D view must represent")
            obj.addProperty("App::PropertyLink", "Base", "Draft", _tip, locked=True)
        if not "Projection" in pl:
            _tip = QT_TRANSLATE_NOOP("App::Property", "The projection vector of this object")
            obj.addProperty("App::PropertyVector", "Projection", "Draft", _tip, locked=True)
            obj.Projection = App.Vector(0, 0, 1)
        if not "ProjectionMode" in pl:
            _tip = QT_TRANSLATE_NOOP("App::Property", "The way the viewed object must be projected")
            obj.addProperty(
                "App::PropertyEnumeration", "ProjectionMode", "Draft", _tip, locked=True
            )
            obj.ProjectionMode = [
                "Solid",
                "Individual Faces",
                "Cutlines",
                "Cutfaces",
                "Solid faces",
            ]
        if not "FaceNumbers" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property", "The indices of the faces to be projected in Individual Faces mode"
            )
            obj.addProperty("App::PropertyIntegerList", "FaceNumbers", "Draft", _tip, locked=True)
        if not "HiddenLines" in pl:
            _tip = QT_TRANSLATE_NOOP("App::Property", "Show hidden lines")
            obj.addProperty("App::PropertyBool", "HiddenLines", "Draft", _tip, locked=True)
            obj.HiddenLines = False
        if not "Tessellation" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property", "Tessellate Ellipses and B-splines into line segments"
            )
            obj.addProperty("App::PropertyBool", "Tessellation", "Draft", _tip, locked=True)
            obj.Tessellation = False
        if not "InPlace" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "For Cutlines and Cutfaces modes, this leaves the faces at the cut location",
            )
            obj.addProperty("App::PropertyBool", "InPlace", "Draft", _tip, locked=True)
            obj.InPlace = True
        if not "SegmentLength" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "Length of line segments if tessellating Ellipses or B-splines into line segments",
            )
            obj.addProperty("App::PropertyFloat", "SegmentLength", "Draft", _tip, locked=True)
            obj.SegmentLength = 0.05
        if not "VisibleOnly" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property", "If this is True, this object will include only visible objects"
            )
            obj.addProperty("App::PropertyBool", "VisibleOnly", "Draft", _tip, locked=True)
            obj.VisibleOnly = False
        if not "ExclusionPoints" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "A list of exclusion points. Any edge touching any of those points will not be drawn.",
            )
            obj.addProperty(
                "App::PropertyVectorList", "ExclusionPoints", "Draft", _tip, locked=True
            )
        if not "ExclusionNames" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "A list of exclusion object names. Any object viewed that matches a name from the list will not be drawn.",
            )
            obj.addProperty("App::PropertyStringList", "ExclusionNames", "Draft", _tip, locked=True)
        if not "OnlySolids" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "If this is True, only solid geometry is handled. This overrides the base object's Only Solids property",
            )
            obj.addProperty("App::PropertyBool", "OnlySolids", "Draft", _tip, locked=True)
        if not "Clip" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property",
                "If this is True, the contents are clipped to the borders of the section plane, if applicable. This overrides the base object's Clip property",
            )
            obj.addProperty("App::PropertyBool", "Clip", "Draft", _tip, locked=True)
        if not "AutoUpdate" in pl:
            _tip = QT_TRANSLATE_NOOP(
                "App::Property", "This object will be recomputed only if this is True."
            )
            obj.addProperty("App::PropertyBool", "AutoUpdate", "Draft", _tip, locked=True)
            obj.AutoUpdate = True

    def getProjected(self, obj, shape, direction):
        "returns projected edges from a shape and a direction"
        import Part
        import TechDraw

        edges = []
        _groups = TechDraw.projectEx(shape, direction)
        for g in _groups[0:5]:
            if not g.isNull():
                edges.append(g)
        if getattr(obj, "HiddenLines", False):
            for g in _groups[5:]:
                if not g.isNull():
                    edges.append(g)
        edges = self.cleanExcluded(obj, edges)
        if getattr(obj, "Tessellation", False):
            return geo_wires.cleanProjection(
                Part.makeCompound(edges), obj.Tessellation, obj.SegmentLength
            )
        else:
            return Part.makeCompound(edges)

    def cleanExcluded(self, obj, shapes):
        """removes any edge touching exclusion points"""
        import Part

        MAXDIST = 0.0001
        if (not hasattr(obj, "ExclusionPoints")) or (not obj.ExclusionPoints):
            return shapes
        # verts = [Part.Vertex(obj.Placement.multVec(p)) for p in obj.ExclusionPoints]
        verts = [Part.Vertex(p) for p in obj.ExclusionPoints]
        nedges = []
        for s in shapes:
            for e in s.Edges:
                for v in verts:
                    try:
                        d = e.distToShape(v)
                        if d and (d[0] <= MAXDIST):
                            break
                    except RuntimeError:
                        print(
                            "FIXME: shape2dview: distance unavailable for edge", e, "in", obj.Label
                        )
                else:
                    nedges.append(e)
        return nedges

    def excludeNames(self, obj, objs):
        if hasattr(obj, "ExclusionNames"):
            objs = [o for o in objs if not (o.Name in obj.ExclusionNames)]
            return objs

    def _get_shapes(self, shape, onlysolids=False):
        if onlysolids:
            return shape.Solids
        if shape.isNull():
            return []
        if shape.ShapeType == "Compound":
            return shape.SubShapes
        return [shape.copy()]

    def execute(self, obj):
        if self.props_changed_placement_only(obj) or not getattr(obj, "AutoUpdate", True):
            obj.positionBySupport()
            self.props_changed_clear()
            return

        import Part

        pl = obj.Placement
        if obj.Base:
            if obj.Base.isDerivedFrom("App::DocumentObjectGroup"):
                shapes = []
                objs = self.excludeNames(obj, groups.get_group_contents(obj.Base))
                for o in objs:
                    if hasattr(o, "Shape"):
                        shapes.extend(self._get_shapes(o.Shape))
                if shapes:
                    import Part

                    comp = Part.makeCompound(shapes)
                    obj.Shape = self.getProjected(obj, comp, obj.Projection)

            elif hasattr(obj.Base, "Shape"):
                if not DraftVecUtils.isNull(obj.Projection):
                    if obj.ProjectionMode == "Solid":
                        obj.Shape = self.getProjected(obj, obj.Base.Shape, obj.Projection)
                    elif obj.ProjectionMode == "Individual Faces":
                        import Part

                        if obj.FaceNumbers:
                            faces = []
                            for i in obj.FaceNumbers:
                                if len(obj.Base.Shape.Faces) > i:
                                    faces.append(obj.Base.Shape.Faces[i])
                            views = []
                            for f in faces:
                                views.append(self.getProjected(obj, f, obj.Projection))
                            if views:
                                obj.Shape = Part.makeCompound(views)
                    else:
                        App.Console.PrintWarning(obj.ProjectionMode + " mode not implemented\n")

        obj.Placement = pl
        obj.positionBySupport()
        self.props_changed_clear()

    def onChanged(self, obj, prop):
        self.props_changed_store(prop)


# Alias for compatibility with v0.18 and earlier
_Shape2DView = Shape2DView

## @}
