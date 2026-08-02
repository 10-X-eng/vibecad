# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   (c) 2009 Yorik van Havre <yorik@uncreated.net>                        *
# *   (c) 2010 Ken Cline <cline@frii.com>                                   *
# *   (c) 2020 Eliud Cabrera Castillo <e.cabrera-castillo@tum.de>           *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************
"""Provides GUI tools to create straight Line and Wire objects.

The Line class is used by other Gui Commands that behave in a similar way
like Wire, BSpline, and BezCurve.
"""

## @package gui_lines
# \ingroup draftguitools
# \brief Provides GUI tools to create straight Line and Wire objects.

## \addtogroup draftguitools
# @{
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui
import DraftVecUtils
from draftgeoutils import geometry as geo_geometry
from draftguitools import gui_base_original
from draftguitools import gui_tool_utils
from draftguitools import gui_trackers as trackers
from draftutils import params
from draftutils import utils
from draftutils import todo
from draftutils.messages import _err, _toolmsg, _wrn
from draftutils.transaction import document_is_available_for_mutation
from draftutils.transaction import object_is_usable_at_current_position
from draftutils.translate import translate


class Line(gui_base_original.Creator):
    """Gui command for the Line tool."""

    def __init__(self, mode="line"):
        super().__init__()
        self.mode = mode

    def GetResources(self):
        """Set icon, menu and tooltip."""

        return {
            "Pixmap": "Draft_Line",
            "Accel": "L,I",
            "MenuText": QT_TRANSLATE_NOOP("Draft_Line", "Line"),
            "ToolTip": QT_TRANSLATE_NOOP("Draft_Line", "Creates a 2-point line"),
        }

    def Activated(
        self, name=QT_TRANSLATE_NOOP("draft", "Line"), icon="Draft_Line", task_title=None
    ):
        """Execute when the command is called."""
        super().Activated(name)
        if task_title is None:
            title = translate("draft", name)
        else:
            title = task_title
        if self.mode == "wire":
            self.ui.wireUi(title=title, icon=icon)
        elif self.mode == "leader":
            self.ui.wireUi(title=title, icon=icon)
            self.ui.closeButton.hide()
            self.ui.makeFace.hide()
        else:
            self.ui.lineUi(title=title, icon=icon)

        # Interactive geometry is a scene-only preview. Creating a document
        # object here would make Cancel publish and then delete a fake History
        # operation before the user's Line/Wire is accepted.
        self.preview_shape = None
        self.preview_tracker = None

        self.call = self.view.addEventCallback("SoEvent", self.action)
        _toolmsg(translate("draft", "Pick first point"))

    def action(self, arg):
        """Handle the 3D scene events.

        This is installed as an EventCallback in the Inventor view.

        Parameters
        ----------
        arg: dict
            Dictionary with strings that indicates the type of event received
            from the 3D view.
        """
        if arg["Type"] == "SoKeyboardEvent":
            if arg["Key"] == "ESCAPE":
                self.finish()
            return
        if arg["Type"] == "SoLocation2Event":
            self.point, ctrlPoint, info = gui_tool_utils.getPoint(self, arg)
            gui_tool_utils.redraw3DView()
            return
        if arg["Type"] != "SoMouseButtonEvent":
            return
        if arg["State"] == "UP":
            return
        if arg["State"] == "DOWN" and arg["Button"] == "BUTTON1":
            if arg["Position"] == self.pos:
                self.finish(cont=None)
                return
            if (not self.node) and (not self.support):
                gui_tool_utils.getSupport(arg)
                self.point, ctrlPoint, info = gui_tool_utils.getPoint(self, arg)
            if self.point:
                self.ui.redraw()
                if not self._append_point(self.point):
                    return
                self.pos = arg["Position"]
                self.drawUpdate(self.point)
                if self.mode == "line" and len(self.node) == 2:
                    self.finish(cont=None, closed=False)
                if len(self.node) > 2:
                    # The wire is closed
                    if (self.point - self.node[0]).Length < utils.tolerance():
                        self.undolast()
                        if len(self.node) > 2:
                            self.finish(cont=None, closed=True)
                        else:
                            self.finish(cont=None, closed=False)

    def finish(self, cont=False, closed=False):
        """Terminate the operation and close the polyline if asked.

        Parameters
        ----------
        cont: bool or None, optional
            Restart (continue) the command if `True`, or if `None` and
            `ui.continueMode` is `True`.
        closed: bool, optional
            Close the line if `True`.
        """
        self.end_callbacks(self.call)
        self.removeTemporaryObject()

        if len(self.node) > 1:
            Gui.addModule("Draft")
            # The command to run is built as a series of text strings
            # to be committed through the `draftutils.todo.ToDo` class.
            if len(self.node) == 2 and params.get_param("UsePartPrimitives"):
                # Insert a Part::Primitive object
                p1 = self.node[0]
                p2 = self.node[-1]

                _cmd = "FreeCAD.ActiveDocument."
                _cmd += 'addObject("Part::Line", "Line")'
                _cmd_list = [
                    "line = " + _cmd,
                    "line.X1 = " + str(p1.x),
                    "line.Y1 = " + str(p1.y),
                    "line.Z1 = " + str(p1.z),
                    "line.X2 = " + str(p2.x),
                    "line.Y2 = " + str(p2.y),
                    "line.Z2 = " + str(p2.z),
                    "Draft.autogroup(line)",
                    "Draft.select(line)",
                    "FreeCAD.ActiveDocument.recompute()",
                ]
                self.commit(
                    translate("draft", "Create Line"),
                    _cmd_list,
                    inputs=(),
                )
            else:
                # Insert a Draft line
                rot, sup, pts, fil = self.getStrings()

                _base = DraftVecUtils.toString(self.node[0])
                _cmd = "Draft.make_wire"
                _cmd += "("
                _cmd += "points, "
                _cmd += "placement=pl, "
                _cmd += "closed=" + str(closed) + ", "
                _cmd += "face=" + fil + ", "
                _cmd += "support=" + sup
                _cmd += ")"
                _cmd_list = [
                    "pl = FreeCAD.Placement()",
                    "pl.Rotation.Q = " + rot,
                    "pl.Base = " + _base,
                    "points = " + pts,
                    "line = " + _cmd,
                    "Draft.autogroup(line)",
                    "FreeCAD.ActiveDocument.recompute()",
                ]
                self.commit(
                    translate("draft", "Create Wire"),
                    _cmd_list,
                    inputs=self.getSupportInputs(),
                )
        super().finish()
        if cont or (cont is None and self.ui and self.ui.continueMode):
            self.Activated()

    def removeTemporaryObject(self):
        """Remove the scene-only preview without mutating the document."""

        tracker = getattr(self, "preview_tracker", None)
        if tracker is not None:
            tracker.finalize()
        self.preview_tracker = None
        self.preview_shape = None

    def _set_preview_shape(self, shape):
        """Display one immutable shape without creating a History object."""

        tracker = getattr(self, "preview_tracker", None)
        if tracker is not None:
            tracker.finalize()
        self.preview_tracker = None
        self.preview_shape = shape
        if shape is not None and not shape.isNull():
            self.preview_tracker = trackers.ghostTracker(shape)
            self.preview_tracker.on()

    def undolast(self):
        """Undoes last line segment."""
        import Part

        if len(self.node) > 1:
            self.node.pop()
            if len(self.node) > 1:
                self._set_preview_shape(Part.makePolygon(self.node))
            else:
                self._set_preview_shape(None)
            # DNC: report on removal
            # _toolmsg(translate("draft", "Removing last point"))
            _toolmsg(translate("draft", "Pick next point"))
            self.update_hints()

    def _append_point(self, point):
        """Append a point unless it would create a zero-length segment."""
        if self.node and DraftVecUtils.equals(self.node[-1], point):
            _wrn(translate("draft", "Point identical to previous point"))
            return False

        self.node.append(point)
        return True

    def drawUpdate(self, point):
        """Draws new line segment."""
        import Part

        if self.planetrack and self.node:
            self.planetrack.set(self.node[-1])
        if len(self.node) == 1:
            _toolmsg(translate("draft", "Pick next point"))
        else:
            self._set_preview_shape(Part.makePolygon(self.node))
            _toolmsg(translate("draft", "Pick next point"))
        self.update_hints()

    def wipe(self):
        """Remove all previous segments and starts from last point."""
        if len(self.node) > 1:
            self._set_preview_shape(None)
            self.node = [self.node[-1]]
            self._reset_curve_preview()
            if self.planetrack:
                self.planetrack.set(self.node[0])
            _toolmsg(translate("draft", "Pick next point"))
            self.update_hints()

    def _reset_curve_preview(self):
        """Reset an optional curve tracker after the point list is wiped."""

    def orientWP(self):
        """Orient the working plane."""
        if len(self.node) > 1 and self.preview_shape is not None:
            n = geo_geometry.get_normal(self.preview_shape)
            if not n:
                n = self.wp.axis
            p = self.node[-1]
            v = self.node[-1].sub(self.node[-2])
            self.wp.align_to_point_and_axis(p, n, upvec=v, _hist_add=False)
            if self.planetrack:
                self.planetrack.set(self.node[-1])

    def numericInput(self, numx, numy, numz):
        """Validate the entry fields in the user interface.

        This function is called by the toolbar or taskpanel interface
        when valid x, y, and z have been entered in the input fields.
        """
        self.point = App.Vector(numx, numy, numz)
        if not self._append_point(self.point):
            self.ui.setNextFocus()
            return
        self.drawUpdate(self.point)
        if self.mode == "line" and len(self.node) == 2:
            self.finish(cont=None, closed=False)
        self.ui.setNextFocus()

    def get_hints(self):
        if len(self.node) == 0:
            hints = [
                Gui.InputHint(translate("draft", "%1 pick first point"), Gui.UserInput.MouseLeft)
            ]
        elif self.mode == "line":
            hints = [
                Gui.InputHint(translate("draft", "%1 pick second point"), Gui.UserInput.MouseLeft)
            ]
        elif len(self.node) > 2:
            hints = [
                Gui.InputHint(
                    translate("draft", "%1 pick next point, snap to first point to close"),
                    Gui.UserInput.MouseLeft,
                )
            ]
        else:
            hints = [
                Gui.InputHint(translate("draft", "%1 pick next point"), Gui.UserInput.MouseLeft)
            ]
        return (
            hints
            + gui_tool_utils._get_hint_xyz_constrain()
            + gui_tool_utils._get_hint_mod_constrain()
            + gui_tool_utils._get_hint_mod_snap()
        )


Gui.addCommand("Draft_Line", Line())


class Wire(Line):
    """Gui command for the Wire or Polyline tool.

    It inherits the `Line` class, and calls essentially the same code,
    only this time the `mode` is set to `"wire"`,
    so we are allowed to place more than two points.
    """

    def __init__(self):
        super().__init__(mode="wire")

    def GetResources(self):
        """Set icon, menu and tooltip."""

        return {
            "Pixmap": "Draft_Wire",
            "Accel": "P, L",
            "MenuText": QT_TRANSLATE_NOOP("Draft_Wire", "Polyline"),
            "ToolTip": QT_TRANSLATE_NOOP("Draft_Wire", "Creates a polyline"),
        }

    def Activated(self):
        """Execute when the command is called."""
        import Part

        document = App.ActiveDocument
        if not document_is_available_for_mutation(document):
            return
        selection = tuple(Gui.Selection.getSelection())
        if selection and not all(
            object_is_usable_at_current_position(obj, document)
            for obj in selection
        ):
            _err(
                translate(
                    "draft",
                    "Polyline cannot use a selection from another document "
                    "or outside the current History position",
                )
            )
            return

        # If there is a selection, and this selection contains various
        # two-point lines, their shapes are extracted, and we attempt
        # to join them into a single Wire (polyline),
        # then the old lines are removed.
        if len(selection) > 1:
            edges = []
            for o in selection:
                if utils.get_type(o) != "Wire":
                    edges = []
                    break
                edges.extend(o.Shape.Edges)
            if edges:
                try:
                    w = Part.Wire(Part.__sortEdges__(edges))
                except Exception:
                    _err(translate("draft", "Unable to create a wire " "from the selected objects"))
                else:
                    # Points of the new fused Wire in string form
                    # 'FreeCAD.Vector(x,y,z), FreeCAD.Vector(x1,y1,z1), ...'
                    pts = ", ".join([str(v.Point) for v in w.Vertexes])
                    pts = pts.replace("Vector ", "FreeCAD.Vector")

                    Gui.addModule("Draft")
                    Gui.addModule("draftutils.timeline")
                    # The command to run is built as a series of text strings
                    # to be committed through the `draftutils.todo.ToDo` class
                    _cmd = "wire = Draft.make_wire("
                    _cmd += "[" + pts + "], closed=" + str(w.isClosed())
                    _cmd += ")"
                    selected_objects = ", ".join(
                        "FreeCAD.ActiveDocument." + obj.Name
                        for obj in selection
                    )
                    _cmd_list = [
                        "_vibecad_inputs = draftutils.timeline.visible_inputs(["
                        + selected_objects
                        + "])",
                        _cmd,
                    ]
                    _cmd_list.append("Draft.autogroup(wire)")
                    _cmd_list.append(
                        "draftutils.timeline.accept_outputs([wire], _vibecad_inputs)"
                    )
                    _cmd_list.append("FreeCAD.ActiveDocument.recompute()")

                    _op_name = translate("draft", "Convert to Wire")
                    todo.ToDo.delayCommit(
                        [
                            (
                                _op_name,
                                _cmd_list,
                                selection,
                            )
                        ],
                        document,
                    )
                    return

        # If there was no selection or the selection was just one object
        # then we proceed with the normal line creation functions,
        # only this time we will be able to input more than two points
        super().Activated(
            name="Polyline", icon="Draft_Wire", task_title=translate("draft", "Polyline")
        )


Gui.addCommand("Draft_Wire", Wire())

## @}
