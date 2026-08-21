# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD behavior contracts for every native Sketch ribbon action.

The old workbench test suite is not the specification here.  These tests
describe the user-facing VibeCAD contract: the ribbon exposes the intended
native command graph, commands are enabled only in a usable state, an
interactive tool can always be stopped without changing the sketch, and task
accept/cancel paths retain exact ownership of their edits.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtGui

from SketcherTests.GuiTestCase import SketcherGuiTestCase


SKETCH_RIBBON_GROUPS = {
    "Finish": (
        "Sketcher_LeaveSketch",
        "Sketcher_CancelSketch",
        "Sketcher_ViewSketch",
        "Sketcher_ViewSection",
    ),
    "Geometry": (
        "Sketcher_CreatePoint",
        "Sketcher_CompLine",
        "Sketcher_CompCreateArc",
        "Sketcher_CompCreateConic",
        "Sketcher_CompCreateRectangles",
        "Sketcher_CompCreateRegularPolygon",
        "Sketcher_CompSlot",
        "Sketcher_CompCreateBSpline",
        "Sketcher_CreateText",
        "Sketcher_ToggleConstruction",
    ),
    "Constraints": (
        "Sketcher_CompDimensionTools",
        "Sketcher_ConstrainCoincidentUnified",
        "Sketcher_CompHorVer",
        "Sketcher_ConstrainParallel",
        "Sketcher_ConstrainPerpendicular",
        "Sketcher_ConstrainTangent",
        "Sketcher_ConstrainEqual",
        "Sketcher_ConstrainSymmetric",
        "Sketcher_ConstrainBlock",
        "Sketcher_ConstrainGroup",
        "Sketcher_CompToggleConstraints",
    ),
    "Modify": (
        "Sketcher_CompCreateFillets",
        "Sketcher_CompCurveEdition",
        "Sketcher_CompExternal",
        "Sketcher_CarbonCopy",
        "Sketcher_Translate",
        "Sketcher_Rotate",
        "Sketcher_Scale",
        "Sketcher_Offset",
        "Sketcher_Symmetry",
        "Sketcher_RemoveAxesAlignment",
    ),
    "B-Spline": (
        "Sketcher_BSplineConvertToNURBS",
        "Sketcher_BSplineIncreaseDegree",
        "Sketcher_BSplineDecreaseDegree",
        "Sketcher_CompModifyKnotMultiplicity",
        "Sketcher_BSplineInsertKnot",
        "Sketcher_JoinCurves",
    ),
    "Visual": (
        "Sketcher_SelectConstraints",
        "Sketcher_SelectElementsAssociatedWithConstraints",
        "Sketcher_ArcOverlay",
        "Sketcher_CompBSplineShowHideGeometryInformation",
        "Sketcher_RestoreInternalAlignmentGeometry",
        "Sketcher_SwitchVirtualSpace",
    ),
}

SKETCH_SETUP_COMMANDS = (
    "Sketcher_NewSketch",
    "Sketcher_EditSketch",
    "Sketcher_MapSketch",
    "Sketcher_ReorientSketch",
    "Sketcher_ValidateSketch",
    "Sketcher_MergeSketches",
    "Sketcher_MirrorSketch",
)

COMPOSITE_ACTIONS = {
    "Sketcher_CompLine": (
        "Sketcher_CreatePolyline",
        "Sketcher_CreateLine",
    ),
    "Sketcher_CompCreateArc": (
        "Sketcher_CreateArc",
        "Sketcher_Create3PointArc",
        "Sketcher_CreateArcOfEllipse",
        "Sketcher_CreateArcOfHyperbola",
        "Sketcher_CreateArcOfParabola",
    ),
    "Sketcher_CompCreateConic": (
        "Sketcher_CreateCircle",
        "Sketcher_Create3PointCircle",
        "Sketcher_CreateEllipseByCenter",
        "Sketcher_CreateEllipseBy3Points",
    ),
    "Sketcher_CompCreateRectangles": (
        "Sketcher_CreateRectangle",
        "Sketcher_CreateRectangle_Center",
        "Sketcher_CreateOblong",
    ),
    "Sketcher_CompCreateRegularPolygon": (
        "Sketcher_CreateTriangle",
        "Sketcher_CreateSquare",
        "Sketcher_CreatePentagon",
        "Sketcher_CreateHexagon",
        "Sketcher_CreateHeptagon",
        "Sketcher_CreateOctagon",
        "Sketcher_CreateRegularPolygon",
    ),
    "Sketcher_CompSlot": (
        "Sketcher_CreateSlot",
        "Sketcher_CreateArcSlot",
    ),
    "Sketcher_CompCreateBSpline": (
        "Sketcher_CreateBSpline",
        "Sketcher_CreatePeriodicBSpline",
        "Sketcher_CreateBSplineByInterpolation",
        "Sketcher_CreatePeriodicBSplineByInterpolation",
    ),
    "Sketcher_CompDimensionTools": (
        "Sketcher_Dimension",
        "Sketcher_ConstrainDistanceX",
        "Sketcher_ConstrainDistanceY",
        "Sketcher_ConstrainDistance",
        "Sketcher_ConstrainRadiam",
        "Sketcher_ConstrainRadius",
        "Sketcher_ConstrainDiameter",
        "Sketcher_ConstrainAngle",
        "Sketcher_ConstrainLock",
    ),
    "Sketcher_CompHorVer": (
        "Sketcher_ConstrainHorVer",
        "Sketcher_ConstrainHorizontal",
        "Sketcher_ConstrainVertical",
    ),
    "Sketcher_CompToggleConstraints": (
        "Sketcher_ToggleDrivingConstraint",
        "Sketcher_ToggleActiveConstraint",
    ),
    "Sketcher_CompCreateFillets": (
        "Sketcher_CreateFillet",
        "Sketcher_CreateChamfer",
    ),
    "Sketcher_CompCurveEdition": (
        "Sketcher_Trimming",
        "Sketcher_Split",
        "Sketcher_Extend",
    ),
    "Sketcher_CompExternal": (
        "Sketcher_Projection",
        "Sketcher_Intersection",
    ),
    "Sketcher_CompModifyKnotMultiplicity": (
        "Sketcher_BSplineIncreaseKnotMultiplicity",
        "Sketcher_BSplineDecreaseKnotMultiplicity",
    ),
    "Sketcher_CompBSplineShowHideGeometryInformation": (
        "Sketcher_BSplineDegree",
        "Sketcher_BSplinePolygon",
        "Sketcher_BSplineComb",
        "Sketcher_BSplineKnotMultiplicity",
        "Sketcher_BSplinePoleWeight",
    ),
}

CUSTOM_COMPOSITE_CHILDREN = frozenset(
    COMPOSITE_ACTIONS["Sketcher_CompModifyKnotMultiplicity"]
    + COMPOSITE_ACTIONS[
        "Sketcher_CompBSplineShowHideGeometryInformation"
    ]
)

INTERACTIVE_CHILDREN = tuple(
    dict.fromkeys(
        (
            "Sketcher_CreatePoint",
            "Sketcher_CreatePolyline",
            "Sketcher_CreateLine",
            "Sketcher_CreateArc",
            "Sketcher_Create3PointArc",
            "Sketcher_CreateArcOfEllipse",
            "Sketcher_CreateArcOfHyperbola",
            "Sketcher_CreateArcOfParabola",
            "Sketcher_CreateCircle",
            "Sketcher_Create3PointCircle",
            "Sketcher_CreateEllipseByCenter",
            "Sketcher_CreateEllipseBy3Points",
            "Sketcher_CreateRectangle",
            "Sketcher_CreateRectangle_Center",
            "Sketcher_CreateOblong",
            "Sketcher_CreateTriangle",
            "Sketcher_CreateSquare",
            "Sketcher_CreatePentagon",
            "Sketcher_CreateHexagon",
            "Sketcher_CreateHeptagon",
            "Sketcher_CreateOctagon",
            "Sketcher_CreateSlot",
            "Sketcher_CreateArcSlot",
            "Sketcher_CreateBSpline",
            "Sketcher_CreatePeriodicBSpline",
            "Sketcher_CreateBSplineByInterpolation",
            "Sketcher_CreatePeriodicBSplineByInterpolation",
            "Sketcher_CreateText",
            "Sketcher_Dimension",
            "Sketcher_ConstrainDistanceX",
            "Sketcher_ConstrainDistanceY",
            "Sketcher_ConstrainDistance",
            "Sketcher_ConstrainRadiam",
            "Sketcher_ConstrainRadius",
            "Sketcher_ConstrainDiameter",
            "Sketcher_ConstrainAngle",
            "Sketcher_ConstrainLock",
            "Sketcher_ConstrainCoincidentUnified",
            "Sketcher_ConstrainHorVer",
            "Sketcher_ConstrainHorizontal",
            "Sketcher_ConstrainVertical",
            "Sketcher_ConstrainParallel",
            "Sketcher_ConstrainPerpendicular",
            "Sketcher_ConstrainTangent",
            "Sketcher_ConstrainEqual",
            "Sketcher_ConstrainSymmetric",
            "Sketcher_ConstrainBlock",
            "Sketcher_CreateFillet",
            "Sketcher_CreateChamfer",
            "Sketcher_Trimming",
            "Sketcher_Split",
            "Sketcher_Extend",
            "Sketcher_Projection",
            "Sketcher_Intersection",
            "Sketcher_CarbonCopy",
        )
    )
)

SKETCH_STANDALONE_OPERATION_COMMANDS = {
    "Sketcher_NewSketch",
}

SKETCH_SOURCE_PRESERVING_OPERATION_COMMANDS = {
    "Sketcher_MergeSketches",
    "Sketcher_MirrorSketch",
}

SKETCH_TASK_VIEW_OR_SELECTION_COMMANDS = set(
    SKETCH_RIBBON_GROUPS["Finish"]
    + SKETCH_RIBBON_GROUPS["Visual"]
    + COMPOSITE_ACTIONS[
        "Sketcher_CompBSplineShowHideGeometryInformation"
    ]
)

SKETCH_IN_PLACE_COMMANDS = (
    set(SKETCH_SETUP_COMMANDS)
    - SKETCH_STANDALONE_OPERATION_COMMANDS
    - SKETCH_SOURCE_PRESERVING_OPERATION_COMMANDS
) | set(
    SKETCH_RIBBON_GROUPS["Geometry"]
    + SKETCH_RIBBON_GROUPS["Constraints"]
    + SKETCH_RIBBON_GROUPS["Modify"]
    + SKETCH_RIBBON_GROUPS["B-Spline"]
) | (
    {
        child
        for children in COMPOSITE_ACTIONS.values()
        for child in children
    }
    - SKETCH_TASK_VIEW_OR_SELECTION_COMMANDS
)


def _command_ids(menu):
    return tuple(
        str(action.property("VibeCADCommandId"))
        for action in menu.actions()
        if not action.isSeparator()
    )


class TestVibeCADSketchRibbonTools(SketcherGuiTestCase):
    def setUp(self):
        super().setUp()
        Gui.activateWorkbench("SketcherWorkbench")
        self.doc = self.new_document("VibeCADSketchRibbonTools")
        self.doc.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.sketch = self.doc.addObject("Sketcher::SketchObject", "ContractSketch")
        self.sketch.addGeometry(
            [
                Part.LineSegment(App.Vector(-18, -8, 0), App.Vector(-6, -3, 0)),
                Part.LineSegment(App.Vector(-6, -3, 0), App.Vector(2, 6, 0)),
                Part.LineSegment(App.Vector(2, 6, 0), App.Vector(12, 6, 0)),
                Part.Circle(App.Vector(16, 0, 0), App.Vector(0, 0, 1), 4),
            ],
            False,
        )
        self.length_constraint = self.sketch.addConstraint(
            Sketcher.Constraint("Distance", 2, 10.0)
        )
        self.doc.recompute()
        Gui.activeDocument().activeView().viewTop()
        Gui.activeDocument().activeView().fitAll()
        self.flush_gui(80)

    def tearDown(self):
        document = getattr(self, "doc", None)
        document_name = self._document_name(document)
        if document_name in App.listDocuments():
            App.setActiveDocument(document_name)
            gui_document = Gui.getDocument(document_name)
            if gui_document.getInEdit() is not None:
                Gui.runCommand("Sketcher_StopOperation", 0)
                self.flush_gui()
        super().tearDown()

    def _enter_edit(self):
        Gui.Selection.clearSelection()
        self.assertTrue(Gui.activeDocument().setEdit(self.sketch.Name))
        self.flush_gui(120)
        self.assertIsNotNone(Gui.activeDocument().getInEdit())
        self.assertTrue(Gui.Control.activeDialog())

    def _stop_operation(self):
        Gui.runCommand("Sketcher_StopOperation", 0)
        self.flush_gui(40)

    def _sketch_state(self):
        return (
            self.sketch.GeometryCount,
            self.sketch.ConstraintCount,
            tuple(self.sketch.getConstruction(index)
                  for index in range(self.sketch.GeometryCount)),
            self.doc.HasPendingTransaction,
            self.doc.UndoCount,
        )

    def _ribbon_group(self, name):
        return Gui.getMainWindow().findChild(
            QtGui.QFrame,
            "VibeCADRibbonGroup_" + name.replace("-", "_"),
        )

    def _sketch_sidebar_list(self, object_name):
        widget = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            object_name,
        )
        self.assertIsNotNone(widget, object_name)
        self.assertTrue(
            self.wait_until(lambda: widget.count() > 0),
            object_name,
        )
        return widget

    def _visual_layer_id(self, sketch, geometry_index):
        facade = sketch.GeometryFacadeList[geometry_index]
        extension_type = (
            "SketcherGui::ViewProviderSketchGeometryExtension"
        )
        if not facade.hasExtensionOfType(extension_type):
            return 0
        return facade.getExtensionOfType(extension_type).VisualLayerId

    def _click_sketch_point(self, point):
        view = Gui.activeDocument().activeView()
        viewport = self.active_viewport(view)
        screen = view.getPointOnScreen(App.Vector(point[0], point[1], 0))
        position = self.viewport_to_qpoint(view, viewport, screen)
        self.assertTrue(
            viewport.rect().contains(position),
            (point, position, viewport.rect()),
        )
        self.move(viewport, position)
        self.click(viewport, position)

    def _click_sketch_subelement(self, point, subelement):
        view = Gui.activeDocument().activeView()
        viewport = self.active_viewport(view)
        screen = view.getPointOnScreen(App.Vector(point[0], point[1], 0))
        center = self.viewport_to_qpoint(view, viewport, screen)
        offsets = (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6)
        observed = set()
        for dy in offsets:
            for dx in offsets:
                position = center + QtCore.QPoint(dx, dy)
                if not viewport.rect().contains(position):
                    continue
                self.move(viewport, position)
                preselection = Gui.Selection.getPreselection()
                observed.add(
                    (
                        preselection.ObjectName,
                        tuple(preselection.SubElementNames),
                    )
                )
                if (
                    preselection.ObjectName == self.sketch.Name
                    and subelement in preselection.SubElementNames
                ):
                    self.click(viewport, position)
                    return
        self.fail(
            f"Could not preselect {subelement} near {point}; observed {observed}"
        )

    def _right_click_sketch_point(self, point):
        view = Gui.activeDocument().activeView()
        viewport = self.active_viewport(view)
        screen = view.getPointOnScreen(App.Vector(point[0], point[1], 0))
        position = self.viewport_to_qpoint(view, viewport, screen)
        self.move(viewport, position)
        self.right_click(viewport, position)

    def _bspline_index(self):
        matches = [
            index
            for index, geometry in enumerate(self.sketch.Geometry)
            if geometry.TypeId == "Part::GeomBSplineCurve"
        ]
        self.assertEqual(
            1,
            len(matches),
            [
                (
                    index,
                    self.sketch.GeometryFacadeList[index].InternalType,
                    self.sketch.GeometryFacadeList[index].Construction,
                )
                for index in matches
            ],
        )
        return matches[0]

    def _select_internal_bspline_knot(self, spline_index):
        spline = self.sketch.Geometry[spline_index]
        endpoints = (spline.StartPoint, spline.EndPoint)
        candidates = []
        for constraint in self.sketch.Constraints:
            if (
                constraint.Type != "InternalAlignment"
                or constraint.Second != spline_index
                or constraint.First < 0
            ):
                continue
            geometry = self.sketch.Geometry[constraint.First]
            if geometry.TypeId != "Part::GeomPoint":
                continue
            point = App.Vector(geometry.X, geometry.Y, geometry.Z)
            if all((point - endpoint).Length > 1e-6 for endpoint in endpoints):
                candidates.append(point)

        self.assertTrue(candidates, "The B-spline exposes no selectable internal knot")
        point = candidates[0]
        Gui.Selection.clearSelection()
        self._click_sketch_point((point.x, point.y))
        self.flush_gui(80)
        selection = Gui.Selection.getSelectionEx()
        self.assertEqual(1, len(selection))
        self.assertIs(selection[0].Object, self.sketch)
        self.assertEqual(1, len(selection[0].SubElementNames))

    def _bspline_knot_diagnostics(self, spline_index):
        selection = [
            (
                selected.ObjectName,
                tuple(selected.SubElementNames),
            )
            for selected in Gui.Selection.getSelectionEx()
        ]
        alignments = [
            (
                constraint.First,
                constraint.Second,
                self.sketch.GeometryFacadeList[
                    constraint.First
                ].InternalType,
            )
            for constraint in self.sketch.Constraints
            if (
                constraint.Type == "InternalAlignment"
                and constraint.Second == spline_index
            )
        ]
        return {
            "selection": selection,
            "internal_alignments": alignments,
        }

    def _respond_to_modal(self, accept):
        attempts = [0]

        def respond():
            attempts[0] += 1
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                if attempts[0] < 200:
                    QtCore.QTimer.singleShot(5, respond)
                return
            if accept:
                modal.accept()
            else:
                modal.reject()

        QtCore.QTimer.singleShot(0, respond)

    def _timeline(self):
        return next(
            (
                obj
                for obj in self.doc.Objects
                if obj.TypeId == "App::DocumentTimeline"
            ),
            None,
        )

    def _timeline_button(self, object_name):
        button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            object_name,
        )
        self.assertIsNotNone(button, object_name)
        return button

    def _timeline_object_names(self):
        self.flush_gui()
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)
        return tuple(
            timeline.item(row).data(QtCore.Qt.UserRole)
            for row in range(timeline.count())
            if timeline.item(row).data(QtCore.Qt.UserRole)
        )

    def test_complete_ribbon_and_composite_action_graph_is_registered(self):
        commands = set(Gui.listCommands())
        surfaced = set(SKETCH_SETUP_COMMANDS)
        for group_commands in SKETCH_RIBBON_GROUPS.values():
            surfaced.update(group_commands)
        for children in COMPOSITE_ACTIONS.values():
            surfaced.update(children)
        self.assertEqual(set(), surfaced - commands)

        for command_name in sorted(surfaced):
            command = Gui.Command.get(command_name)
            self.assertIsNotNone(command, command_name)
            actions = command.getAction()
            if not actions:
                self.assertIn(
                    command_name,
                    CUSTOM_COMPOSITE_CHILDREN,
                    f"{command_name} has no native action",
                )
                continue
            self.assertTrue(actions, command_name)
            self.assertFalse(
                actions[0].icon().isNull(),
                f"{command_name} has no native icon",
            )
            self.assertFalse(
                actions[0].icon().pixmap(24, 24).isNull(),
                f"{command_name} icon does not render",
            )

        for composite, expected_children in COMPOSITE_ACTIONS.items():
            actions = [
                action
                for action in Gui.Command.get(composite).getAction()
                if not action.isSeparator()
            ]
            self.assertEqual(
                expected_children,
                tuple(action.objectName() for action in actions),
                composite,
            )
            for action in actions:
                self.assertEqual(
                    composite,
                    str(action.property("FreeCADCommandGroupParentId")),
                    action.objectName(),
                )
                self.assertFalse(
                    bool(action.property("FreeCADCommandGroupSynthetic")),
                    action.objectName(),
                )
                self.assertFalse(action.icon().isNull(), action.objectName())

    def test_every_sketch_command_has_one_explicit_history_contract(self):
        surfaced = set(SKETCH_SETUP_COMMANDS)
        for group_commands in SKETCH_RIBBON_GROUPS.values():
            surfaced.update(group_commands)
        for children in COMPOSITE_ACTIONS.values():
            surfaced.update(children)

        contracts = (
            SKETCH_STANDALONE_OPERATION_COMMANDS,
            SKETCH_SOURCE_PRESERVING_OPERATION_COMMANDS,
            SKETCH_IN_PLACE_COMMANDS,
            SKETCH_TASK_VIEW_OR_SELECTION_COMMANDS,
        )
        self.assertEqual(set().union(*contracts), surfaced)
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

    def test_ribbon_page_contains_exact_setup_and_edit_surfaces(self):
        setup_group = self._ribbon_group("Sketch")
        self.assertIsNotNone(setup_group)
        setup_menu = setup_group.findChild(
            QtGui.QToolButton,
            "VibeCADRibbonGroupMenu",
        ).menu()
        self.assertEqual(SKETCH_SETUP_COMMANDS, _command_ids(setup_menu))

        self._enter_edit()
        for title, expected in SKETCH_RIBBON_GROUPS.items():
            group = self._ribbon_group(title)
            self.assertIsNotNone(group, title)
            menu = group.findChild(
                QtGui.QToolButton,
                "VibeCADRibbonGroupMenu",
            ).menu()
            self.assertEqual(expected, _command_ids(menu), title)

    def test_setup_commands_are_locked_while_editing(self):
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        self.assertTrue(Gui.isCommandActive("Sketcher_NewSketch"))
        self.assertTrue(Gui.isCommandActive("Sketcher_EditSketch"))
        self.assertTrue(Gui.isCommandActive("Sketcher_ReorientSketch"))
        self.assertTrue(Gui.isCommandActive("Sketcher_ValidateSketch"))
        self.assertTrue(Gui.isCommandActive("Sketcher_MirrorSketch"))

        self._enter_edit()
        for command_name in SKETCH_SETUP_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                f"{command_name} can start inside an existing native task",
            )

    def test_setup_commands_reject_foreign_and_future_selections(self):
        other = self.new_document("VibeCADSketchForeignSelection")
        try:
            foreign = other.addObject(
                "Sketcher::SketchObject",
                "ForeignSketch",
            )
            foreign.addGeometry(
                Part.LineSegment(
                    App.Vector(0, 0, 0),
                    App.Vector(4, 2, 0),
                ),
                False,
            )
            other.recompute()

            App.setActiveDocument(self.doc.Name)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(foreign)
            self.flush_gui(80)
            self.assertEqual([], Gui.Selection.getSelectionEx())
            self.assertTrue(Gui.isCommandActive("Sketcher_NewSketch"))
            for command_name in (
                command
                for command in SKETCH_SETUP_COMMANDS
                if command != "Sketcher_NewSketch"
            ):
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    f"{command_name} used an inactive document's selection",
                )
        finally:
            App.setActiveDocument(self.doc.Name)
            Gui.Selection.clearSelection()
            if other.Name in App.listDocuments():
                self.close_gui_document(other)
            self.flush_gui()

        mixed_second = self.doc.addObject(
            "Sketcher::SketchObject",
            "MixedSelectionSketch",
        )
        mixed_second.addGeometry(
            Part.LineSegment(
                App.Vector(18, -8, 0),
                App.Vector(24, -3, 0),
            ),
            False,
        )
        mixed_result = self.doc.addObject(
            "Part::Feature",
            "MixedSelectionResult",
        )
        mixed_result.Shape = Part.makeBox(2, 2, 2)
        self.doc.recompute()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(mixed_second)
        Gui.Selection.addSelection(mixed_result)
        self.flush_gui(80)
        for command_name in (
            "Sketcher_EditSketch",
            "Sketcher_ReorientSketch",
            "Sketcher_ValidateSketch",
            "Sketcher_MergeSketches",
            "Sketcher_MirrorSketch",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                f"{command_name} ignored a non-Sketch selection",
            )
        Gui.Selection.clearSelection()

        future = self.doc.addObject(
            "Sketcher::SketchObject",
            "FutureSketch",
        )
        future.addGeometry(
            Part.LineSegment(
                App.Vector(22, 3, 0),
                App.Vector(28, 8, 0),
            ),
            False,
        )
        self.doc.recompute()
        controller = self._timeline()
        future_index = list(controller.Operations).index(future)
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self.flush_gui(80)
        while int(controller.Position) > future_index:
            self._timeline_button(
                "VibeCADFeatureTimelinePrevious"
            ).click()
            self.flush_gui(40)
        self.assertEqual(future_index, int(controller.Position))

        Gui.Selection.addSelection(future)
        self.flush_gui(80)
        for command_name in SKETCH_SETUP_COMMANDS:
            self.assertFalse(
                Gui.isCommandActive(command_name),
                f"{command_name} accepted a future History object",
            )

        Gui.Selection.clearSelection()
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self.flush_gui(80)

    def test_setup_commands_do_not_join_a_caller_owned_transaction(self):
        second = self.doc.addObject(
            "Sketcher::SketchObject",
            "ForeignTransactionSource",
        )
        second.addGeometry(
            Part.LineSegment(
                App.Vector(30, 0, 0),
                App.Vector(34, 4, 0),
            ),
            False,
        )
        self.doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()
        before = tuple(self.doc.Objects)
        self.doc.openTransaction(
            "Caller-owned Sketch transaction"
        )
        transaction_id = self.doc.getBookedTransactionID()
        self.assertIsNotNone(transaction_id)
        try:
            self.assertFalse(
                Gui.isCommandActive("Sketcher_MergeSketches")
            )
            Gui.runCommand("Sketcher_MergeSketches", 0)
            self.flush_gui(80)
            self.assertEqual(
                transaction_id,
                self.doc.getBookedTransactionID(),
            )
            self.assertEqual(before, tuple(self.doc.Objects))
        finally:
            self.doc.abortTransaction()
            self.flush_gui()

    def test_every_interactive_child_stops_without_mutating_the_sketch(self):
        self._enter_edit()
        for command_name in INTERACTIVE_CHILDREN:
            with self.subTest(command=command_name):
                self._stop_operation()
                Gui.Selection.clearSelection()
                self.flush_gui()
                before = self._sketch_state()
                self.assertTrue(Gui.isCommandActive(command_name), command_name)
                Gui.runCommand(command_name, 0)
                self.flush_gui(30)
                self.assertIsNotNone(
                    Gui.activeDocument().getInEdit(),
                    f"{command_name} exited sketch edit mode",
                )
                self._stop_operation()
                self.assertEqual(
                    before,
                    self._sketch_state(),
                    f"Stopping {command_name} left geometry, constraints, or a transaction",
                )

    def test_stopping_a_partial_bspline_rolls_back_its_provisional_point(self):
        self._enter_edit()
        before = self._sketch_state()

        Gui.runCommand("Sketcher_CreateBSpline", 0)
        self.flush_gui()
        self._click_sketch_point((-28, 18))
        self.assertGreater(
            self.sketch.GeometryCount,
            before[0],
            "The B-spline tool did not create its provisional control point",
        )

        self._stop_operation()
        self.assertEqual(
            before,
            self._sketch_state(),
            "Stopping a partial B-spline retained provisional geometry or an undo entry",
        )

    def test_selection_only_commands_are_not_offered_for_wrong_input(self):
        foreign = self.doc.addObject("Part::Feature", "ForeignResult")
        foreign.Shape = Part.makeBox(2, 2, 2)
        other_sketch = self.doc.addObject(
            "Sketcher::SketchObject",
            "NonEditingSketch",
        )
        other_sketch.addGeometry(
            Part.LineSegment(
                App.Vector(24, -10, 0),
                App.Vector(30, -4, 0),
            ),
            False,
        )
        self.doc.recompute()
        self._enter_edit()

        Gui.Selection.clearSelection()
        self.flush_gui()
        for command_name in (
            "Sketcher_ConstrainGroup",
            "Sketcher_CompToggleConstraints",
            "Sketcher_ToggleDrivingConstraint",
            "Sketcher_ToggleActiveConstraint",
            "Sketcher_Translate",
            "Sketcher_Rotate",
            "Sketcher_Scale",
            "Sketcher_Offset",
            "Sketcher_Symmetry",
            "Sketcher_RemoveAxesAlignment",
            "Sketcher_BSplineConvertToNURBS",
            "Sketcher_RestoreInternalAlignmentGeometry",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        Gui.Selection.addSelection(self.sketch, "Vertex1")
        self.flush_gui()
        for command_name in (
            "Sketcher_Offset",
            "Sketcher_RemoveAxesAlignment",
            "Sketcher_BSplineConvertToNURBS",
            "Sketcher_RestoreInternalAlignmentGeometry",
            "Sketcher_ToggleConstruction",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                f"{command_name} accepts a line endpoint it cannot process",
            )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge4")
        self.flush_gui()
        self.assertTrue(Gui.isCommandActive("Sketcher_Offset"))
        self.assertTrue(Gui.isCommandActive("Sketcher_RemoveAxesAlignment"))
        self.assertTrue(Gui.isCommandActive("Sketcher_BSplineConvertToNURBS"))
        self.assertTrue(Gui.isCommandActive("Sketcher_ToggleConstruction"))
        self.assertFalse(
            Gui.isCommandActive("Sketcher_RestoreInternalAlignmentGeometry")
        )

        Gui.Selection.addSelection(self.sketch, "Edge1")
        Gui.Selection.addSelection(foreign, "Edge1")
        self.flush_gui()
        self.assertFalse(Gui.isCommandActive("Sketcher_ConstrainGroup"))
        self.assertFalse(Gui.isCommandActive("Sketcher_CompToggleConstraints"))
        self.assertFalse(Gui.isCommandActive("Sketcher_ToggleConstruction"))
        self.assertFalse(Gui.isCommandActive("Sketcher_SwitchVirtualSpace"))

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(other_sketch, "Edge1")
        self.flush_gui()
        for command_name in (
            "Sketcher_ConstrainGroup",
            "Sketcher_CompToggleConstraints",
            "Sketcher_Offset",
            "Sketcher_BSplineConvertToNURBS",
            "Sketcher_ToggleConstruction",
            "Sketcher_SwitchVirtualSpace",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                f"{command_name} accepted a non-editing sketch",
            )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge1")
        Gui.Selection.addSelection(self.sketch, "Edge2")
        self.flush_gui()
        self.assertTrue(Gui.isCommandActive("Sketcher_ConstrainGroup"))

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.sketch,
            f"Constraint{self.length_constraint + 1}",
        )
        self.flush_gui()
        self.assertTrue(Gui.isCommandActive("Sketcher_CompToggleConstraints"))
        self.assertTrue(Gui.isCommandActive("Sketcher_ToggleDrivingConstraint"))
        self.assertTrue(Gui.isCommandActive("Sketcher_ToggleActiveConstraint"))

    def test_sidebar_mutations_stay_with_their_editing_sketch_when_another_document_is_active(self):
        self._enter_edit()
        elements = self._sketch_sidebar_list("listWidgetElements")
        constraints = self._sketch_sidebar_list("listWidgetConstraints")
        target_edge = "Edge4"
        target_item_index = 3

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, target_edge)
        self.flush_gui(80)
        self.assertTrue(elements.item(target_item_index).isSelected())

        other = self.new_document("VibeCADSketchSidebarForeign")
        try:
            other.UndoMode = True
            foreign_sketch = other.addObject(
                "Sketcher::SketchObject",
                "ForeignSketch",
            )
            foreign_sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(0, 0, 0),
                    App.Vector(8, 0, 0),
                ),
                False,
            )
            foreign_sketch.addConstraint(
                Sketcher.Constraint("Distance", 0, 8.0)
            )
            other.recompute()
            App.setActiveDocument(other.Name)
            self.assertEqual(other.Name, App.ActiveDocument.Name)

            Gui.Selection.addSelection(foreign_sketch, "Edge1")
            self.flush_gui(40)
            original_foreign_state = (
                foreign_sketch.GeometryCount,
                foreign_sketch.ConstraintCount,
            )

            Gui.Selection.removeSelection(self.sketch, target_edge)
            Gui.Selection.addSelection(self.sketch, target_edge)
            self.flush_gui(40)
            self.assertTrue(elements.item(target_item_index).isSelected())

            layer_index = elements.model().index(1, 0)
            App.setActiveDocument(other.Name)
            self.assertTrue(
                QtCore.QMetaObject.invokeMethod(
                    elements,
                    "onIndexChecked",
                    QtCore.Qt.DirectConnection,
                    QtCore.Q_ARG(QtCore.QModelIndex, layer_index),
                    QtCore.Q_ARG(
                        "Qt::CheckState",
                        QtCore.Qt.Unchecked,
                    ),
                )
            )
            self.flush_gui(80)
            self.assertEqual(
                2,
                self._visual_layer_id(self.sketch, 1),
            )
            self.assertEqual(
                0,
                self._visual_layer_id(foreign_sketch, 0),
            )
            self.assertTrue(Gui.Selection.isSelected(foreign_sketch, "Edge1"))
            self.assertEqual(other.Name, App.ActiveDocument.Name)

            App.setActiveDocument(other.Name)
            self.assertTrue(
                QtCore.QMetaObject.invokeMethod(
                    elements,
                    "onIndexChecked",
                    QtCore.Qt.DirectConnection,
                    QtCore.Q_ARG(QtCore.QModelIndex, layer_index),
                    QtCore.Q_ARG(
                        "Qt::CheckState",
                        QtCore.Qt.Checked,
                    ),
                )
            )
            self.flush_gui(80)
            self.assertEqual(
                0,
                self._visual_layer_id(self.sketch, 1),
            )
            self.assertEqual(
                0,
                self._visual_layer_id(foreign_sketch, 0),
            )
            self.assertTrue(Gui.Selection.isSelected(foreign_sketch, "Edge1"))
            self.assertEqual(other.Name, App.ActiveDocument.Name)

            Gui.Selection.removeSelection(self.sketch, target_edge)
            Gui.Selection.addSelection(self.sketch, target_edge)
            self.flush_gui(40)
            self.assertTrue(elements.item(target_item_index).isSelected())
            App.setActiveDocument(other.Name)
            self.assertTrue(
                QtCore.QMetaObject.invokeMethod(
                    elements,
                    "deleteSelectedItems",
                    QtCore.Qt.DirectConnection,
                )
            )
            self.flush_gui(80)
            self.assertEqual(3, self.sketch.GeometryCount)
            self.assertEqual(
                original_foreign_state,
                (
                    foreign_sketch.GeometryCount,
                    foreign_sketch.ConstraintCount,
                ),
            )
            self.assertTrue(
                Gui.Selection.isSelected(foreign_sketch, "Edge1"),
                "Deleting from the editing sketch cleared the active foreign document",
            )
            self.assertEqual(other.Name, App.ActiveDocument.Name)

            constraint_name = f"Constraint{self.length_constraint + 1}"
            Gui.Selection.addSelection(self.sketch, constraint_name)
            self.flush_gui(80)
            self.assertTrue(constraints.selectedItems())
            App.setActiveDocument(other.Name)
            self.assertEqual(other.Name, App.ActiveDocument.Name)
            self.assertTrue(
                QtCore.QMetaObject.invokeMethod(
                    constraints,
                    "deleteSelectedItems",
                    QtCore.Qt.DirectConnection,
                )
            )
            self.flush_gui(80)
            self.assertEqual(0, self.sketch.ConstraintCount)
            self.assertEqual(
                original_foreign_state,
                (
                    foreign_sketch.GeometryCount,
                    foreign_sketch.ConstraintCount,
                ),
            )
            self.assertTrue(Gui.Selection.isSelected(foreign_sketch, "Edge1"))
            self.assertEqual(other.Name, App.ActiveDocument.Name)

            App.setActiveDocument(self.doc.Name)
            Gui.runCommand("Sketcher_CancelSketch", 0)
            self.flush_gui(100)
            self.assertEqual(4, self.sketch.GeometryCount)
            self.assertEqual(1, self.sketch.ConstraintCount)
            self.assertEqual(0, self._visual_layer_id(self.sketch, 1))
            self.assertFalse(self.doc.HasPendingTransaction)
        finally:
            App.setActiveDocument(self.doc.Name)
            if other.Name in App.listDocuments():
                self.close_gui_document(other)
            self.flush_gui()

    def test_representative_geometry_child_from_each_creation_composite(self):
        self._enter_edit()
        cases = (
            ("Sketcher_CreateLine", ((-28, -18), (-20, -13)), 1),
            ("Sketcher_Create3PointArc", ((-12, -18), (-2, -18), (-7, -12)), 1),
            ("Sketcher_CreateCircle", ((6, -16), (10, -16)), 1),
            ("Sketcher_CreateRectangle", ((19, -18), (28, -10)), 4),
            ("Sketcher_CreateTriangle", ((-24, 13), (-18, 18)), 3),
            ("Sketcher_CreateSlot", ((30, 10), (40, 10), (40, 14)), 4),
        )
        for command_name, points, minimum_added in cases:
            with self.subTest(command=command_name):
                before = self.sketch.GeometryCount
                Gui.runCommand(command_name, 0)
                self.flush_gui()
                for point in points:
                    self._click_sketch_point(point)
                self._stop_operation()
                self.assertGreaterEqual(
                    self.sketch.GeometryCount - before,
                    minimum_added,
                    command_name,
                )

        before = self.sketch.GeometryCount
        Gui.runCommand("Sketcher_CreateBSpline", 0)
        self.flush_gui()
        spline_points = ((10, 14), (16, 20), (23, 14))
        for point in spline_points:
            self._click_sketch_point(point)
        self._right_click_sketch_point(spline_points[-1])
        self._stop_operation()
        self.assertGreater(self.sketch.GeometryCount, before)
        self.assertTrue(
            any(
                geometry.TypeId == "Part::GeomBSplineCurve"
                for geometry in self.sketch.Geometry
            ),
            "The B-spline composite did not create a native B-spline",
        )

    def test_regular_polygon_dialog_cancel_is_an_exact_no_op(self):
        self._enter_edit()
        before = self._sketch_state()
        self._respond_to_modal(False)
        Gui.runCommand("Sketcher_CreateRegularPolygon", 0)
        self.flush_gui()
        self.assertEqual(before, self._sketch_state())
        self.assertIsNotNone(Gui.activeDocument().getInEdit())

    def test_accepted_geometry_edit_updates_existing_sketch_in_one_undo(self):
        operations_before = tuple(self._timeline().Operations)
        geometry_before = self.sketch.GeometryCount
        undo_before = self.doc.UndoCount
        self._enter_edit()

        Gui.runCommand("Sketcher_CreateLine", 0)
        self.flush_gui()
        self._click_sketch_point((-32, -24))
        self._click_sketch_point((-24, -20))
        self._stop_operation()
        Gui.runCommand("Sketcher_LeaveSketch", 0)
        self.flush_gui(80)

        self.assertEqual(geometry_before + 1, self.sketch.GeometryCount)
        self.assertEqual(operations_before, tuple(self._timeline().Operations))
        self.assertEqual(undo_before + 1, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)
        self.doc.undo()
        self.flush_gui()
        self.assertEqual(geometry_before, self.sketch.GeometryCount)
        self.assertEqual(operations_before, tuple(self._timeline().Operations))

    def test_horizontal_constraint_composite_child_creates_real_constraint(self):
        self._enter_edit()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge1")
        self.flush_gui()
        before = self.sketch.ConstraintCount
        Gui.runCommand("Sketcher_ConstrainHorizontal", 0)
        self.flush_gui()

        self.assertEqual(before + 1, self.sketch.ConstraintCount)
        self.assertEqual(
            "Horizontal",
            self.sketch.Constraints[-1].Type,
        )

    def _move_sketch_point(self, point):
        view = Gui.activeDocument().activeView()
        viewport = self.active_viewport(view)
        screen = view.getPointOnScreen(App.Vector(point[0], point[1], 0))
        position = self.viewport_to_qpoint(view, viewport, screen)
        self.assertTrue(
            viewport.rect().contains(position),
            (point, position, viewport.rect()),
        )
        self.move(viewport, position)

    def test_dimension_tool_does_not_insert_a_second_automatic_dimension(self):
        self._enter_edit()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge1")
        self.flush_gui()
        before = self.sketch.ConstraintCount
        dimensional_before = [
            constraint.Type
            for constraint in self.sketch.Constraints
            if constraint.Type in ("Distance", "DistanceX", "DistanceY")
        ]

        Gui.runCommand("Sketcher_Dimension", 0)
        self.flush_gui()

        start = self.sketch.getPoint(0, 1)
        end = self.sketch.getPoint(0, 2)
        self._move_sketch_point(
            (
                (start.x + end.x) / 2.0,
                max(start.y, end.y) + 12.0,
            )
        )
        self.flush_gui(80)

        dimensional = [
            constraint
            for constraint in self.sketch.Constraints
            if constraint.Type in ("Distance", "DistanceX", "DistanceY")
        ]
        self.assertEqual(
            len(dimensional_before) + 1,
            len(dimensional),
            [
                (constraint.Type, constraint.First, constraint.Second)
                for constraint in dimensional
            ],
        )
        self.assertEqual(before + 1, self.sketch.ConstraintCount)
        self._stop_operation()

    def test_dimension_composite_child_creates_a_native_radius_constraint(self):
        self._enter_edit()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge4")
        self.flush_gui()
        before = self.sketch.ConstraintCount

        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_ConstrainRadius", 0)
        self.flush_gui(80)

        self.assertEqual(before + 1, self.sketch.ConstraintCount)
        self.assertEqual("Radius", self.sketch.Constraints[-1].Type)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_group_constraint_accepts_two_distinct_native_geometries(self):
        self._enter_edit()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, "Edge1")
        Gui.Selection.addSelection(self.sketch, "Edge2")
        self.flush_gui()
        before = self.sketch.ConstraintCount

        Gui.runCommand("Sketcher_ConstrainGroup", 0)
        self.flush_gui()

        self.assertEqual(before + 1, self.sketch.ConstraintCount)
        self.assertEqual("Group", self.sketch.Constraints[-1].Type)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_toggle_constraint_composite_changes_only_selected_constraint(self):
        self._enter_edit()
        constraint_name = f"Constraint{self.length_constraint + 1}"
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch, constraint_name)
        self.flush_gui()
        self.assertTrue(self.sketch.getDriving(self.length_constraint))

        Gui.runCommand("Sketcher_ToggleDrivingConstraint", 0)
        self.flush_gui()
        self.assertFalse(self.sketch.getDriving(self.length_constraint))
        self.assertEqual(1, self.sketch.ConstraintCount)

        Gui.Selection.addSelection(self.sketch, constraint_name)
        self.flush_gui()
        Gui.runCommand("Sketcher_ToggleDrivingConstraint", 0)
        self.flush_gui()
        self.assertTrue(self.sketch.getDriving(self.length_constraint))

    def test_bspline_visual_composite_child_roundtrips_its_real_preference(self):
        self._enter_edit()
        parameters = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/Sketcher/General"
        )
        key = "BSplineControlPolygonVisible"
        original = parameters.GetBool(key, True)

        Gui.runCommand("Sketcher_BSplinePolygon", 0)
        self.flush_gui()
        self.assertEqual(not original, parameters.GetBool(key, True))

        Gui.runCommand("Sketcher_BSplinePolygon", 0)
        self.flush_gui()
        self.assertEqual(original, parameters.GetBool(key, True))

    def test_fillet_composite_child_creates_a_real_native_chamfer(self):
        self.sketch.addConstraint(
            Sketcher.Constraint("Coincident", 0, 2, 1, 1)
        )
        self.doc.recompute()
        self._enter_edit()
        before = self.sketch.GeometryCount
        before_lines = sum(
            geometry.TypeId == "Part::GeomLineSegment"
            for geometry in self.sketch.Geometry
        )

        Gui.runCommand("Sketcher_CreateChamfer", 0)
        self.flush_gui()
        self._click_sketch_point((-6, -3))
        self._stop_operation()

        self.assertGreater(self.sketch.GeometryCount, before)
        self.assertGreater(
            sum(
                geometry.TypeId == "Part::GeomLineSegment"
                for geometry in self.sketch.Geometry
            ),
            before_lines,
        )

    def test_curve_edit_composite_child_splits_a_real_native_edge(self):
        line_index = self.sketch.addGeometry(
            Part.LineSegment(
                App.Vector(-28, 11, 0),
                App.Vector(-20, 11, 0),
            ),
            False,
        )
        self.doc.recompute()
        self._enter_edit()
        Gui.activeDocument().activeView().fitAll()
        self.flush_gui(100)
        before = self.sketch.GeometryCount

        Gui.runCommand("Sketcher_Split", 0)
        self.flush_gui()
        self._click_sketch_subelement(
            (-24, 11),
            f"Edge{line_index + 1}",
        )
        self._stop_operation()

        self.assertEqual(before + 1, self.sketch.GeometryCount)
        self.assertEqual(
            "Part::GeomLineSegment",
            self.sketch.Geometry[line_index].TypeId,
        )
        self.assertEqual(
            "Part::GeomLineSegment",
            self.sketch.Geometry[-1].TypeId,
        )

    def test_external_composite_child_projects_a_real_linked_edge(self):
        reference = self.doc.addObject("Part::Feature", "ExternalReference")
        reference.Shape = Part.makeLine(
            App.Vector(-28, 16, 0),
            App.Vector(-20, 16, 0),
        )
        self.doc.recompute()
        self._enter_edit()
        before = len(self.sketch.ExternalGeometry)

        Gui.runCommand("Sketcher_Projection", 0)
        self.flush_gui()
        Gui.Selection.addSelection(reference, "Edge1")
        self.flush_gui(100)
        self._stop_operation()

        self.assertEqual(before + 1, len(self.sketch.ExternalGeometry))
        linked_object, linked_subelements = self.sketch.ExternalGeometry[-1]
        self.assertIs(linked_object, reference)
        self.assertEqual(("Edge1",), linked_subelements)

    def test_knot_multiplicity_composite_roundtrips_a_real_internal_knot(self):
        self._enter_edit()
        Gui.runCommand("Sketcher_CreateBSpline", 0)
        self.flush_gui()
        spline_points = (
            (-27, 18),
            (-20, 23),
            (-13, 17),
            (-6, 23),
            (1, 18),
        )
        for point in spline_points:
            self._click_sketch_point(point)
        self._right_click_sketch_point(spline_points[-1])
        self._stop_operation()

        spline_index = self._bspline_index()
        self.sketch.exposeInternalGeometry(spline_index)
        self.doc.recompute()
        Gui.activeDocument().activeView().fitAll()
        self.flush_gui(100)
        before = sum(self.sketch.Geometry[spline_index].getMultiplicities())

        self._select_internal_bspline_knot(spline_index)
        self.assertTrue(
            Gui.isCommandActive("Sketcher_BSplineIncreaseKnotMultiplicity")
        )
        Gui.runCommand("Sketcher_BSplineIncreaseKnotMultiplicity", 0)
        self.flush_gui(100)

        spline_index = self._bspline_index()
        increased = sum(
            self.sketch.Geometry[spline_index].getMultiplicities()
        )
        self.assertEqual(before + 1, increased)

        self._select_internal_bspline_knot(spline_index)
        self.assertTrue(
            Gui.isCommandActive("Sketcher_BSplineDecreaseKnotMultiplicity"),
            self._bspline_knot_diagnostics(spline_index),
        )
        Gui.runCommand("Sketcher_BSplineDecreaseKnotMultiplicity", 0)
        self.flush_gui(100)

        spline_index = self._bspline_index()
        self.assertEqual(
            before,
            sum(self.sketch.Geometry[spline_index].getMultiplicities()),
        )

    def test_new_sketch_task_cancel_removes_the_provisional_sketch(self):
        Gui.Selection.clearSelection()
        original_objects = tuple(self.doc.Objects)
        original_undo_count = self.doc.UndoCount
        original_operations = tuple(self._timeline().Operations)
        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_NewSketch", 0)
        self.flush_gui(80)

        created = [obj for obj in self.doc.Objects if obj not in original_objects]
        self.assertEqual(1, len(created))
        self.assertTrue(created[0].isDerivedFrom("Sketcher::SketchObject"))
        self.assertIsNotNone(Gui.activeDocument().getInEdit())

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self.flush_gui(80)
        self.assertEqual(original_objects, tuple(self.doc.Objects))
        self.assertEqual(original_undo_count, self.doc.UndoCount)
        self.assertEqual(original_operations, tuple(self._timeline().Operations))
        self.assertFalse(self.doc.HasPendingTransaction)

        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_NewSketch", 0)
        self.flush_gui(80)
        accepted = [obj for obj in self.doc.Objects if obj not in original_objects]
        self.assertEqual(1, len(accepted))
        accepted[0].addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(5, 2, 0)),
            False,
        )
        Gui.runCommand("Sketcher_LeaveSketch", 0)
        self.flush_gui(80)
        self.assertIn(accepted[0], self.doc.Objects)
        self.assertEqual(1, accepted[0].GeometryCount)
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertEqual(
            original_operations + (accepted[0],),
            tuple(self._timeline().Operations),
        )
        self.assertEqual(original_undo_count + 1, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_new_sketch_cancel_restores_undo_mode_zero_without_an_undo_entry(self):
        Gui.Selection.clearSelection()
        self.doc.UndoMode = False
        original_objects = tuple(self.doc.Objects)

        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_NewSketch", 0)
        self.flush_gui(80)
        self.assertIsNotNone(Gui.activeDocument().getInEdit())

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self.flush_gui(80)
        self.assertEqual(original_objects, tuple(self.doc.Objects))
        self.assertEqual(0, self.doc.UndoMode)
        self.assertEqual(0, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_edit_sketch_cancel_restores_geometry_with_undo_mode_zero(self):
        self.doc.UndoMode = False
        original_geometry = self.sketch.GeometryCount
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()

        Gui.runCommand("Sketcher_EditSketch", 0)
        self.flush_gui(80)
        self.assertIsNotNone(Gui.activeDocument().getInEdit())
        self.sketch.addGeometry(
            Part.LineSegment(App.Vector(30, 1, 0), App.Vector(35, 4, 0)),
            False,
        )
        self.assertEqual(original_geometry + 1, self.sketch.GeometryCount)

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self.flush_gui(80)
        self.assertEqual(original_geometry, self.sketch.GeometryCount)
        self.assertEqual(0, self.doc.UndoMode)
        self.assertEqual(0, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_validate_sketch_task_cancel_is_an_exact_no_op(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        before = self._sketch_state()

        Gui.runCommand("Sketcher_ValidateSketch", 0)
        self.flush_gui(80)
        self.assertTrue(Gui.Control.activeDialog())
        Gui.Control.activeTaskDialog().reject()
        self.flush_gui(80)

        self.assertEqual(before, self._sketch_state())
        self.assertFalse(Gui.Control.activeDialog())

    def test_merge_sketches_creates_one_editable_native_sketch(self):
        second = self.doc.addObject("Sketcher::SketchObject", "SecondSketch")
        second.addGeometry(
            Part.LineSegment(App.Vector(30, 0, 0), App.Vector(35, 5, 0)),
            False,
        )
        self.doc.recompute()
        originals = tuple(self.doc.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()

        Gui.runCommand("Sketcher_MergeSketches", 0)
        self.flush_gui(80)

        created = [obj for obj in self.doc.Objects if obj not in originals]
        self.assertEqual(1, len(created))
        merged = created[0]
        self.assertTrue(merged.isDerivedFrom("Sketcher::SketchObject"))
        self.assertEqual(
            self.sketch.GeometryCount + second.GeometryCount,
            merged.GeometryCount,
        )
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_merge_sketches_uses_its_exact_creation_return_with_a_distractor(self):
        source_placement = App.Placement(
            App.Vector(7, 11, 0),
            App.Rotation(App.Vector(0, 0, 1), 17),
        )
        self.sketch.Placement = source_placement
        second = self.doc.addObject(
            "Sketcher::SketchObject",
            "DistractorMergeSource",
        )
        second.addGeometry(
            Part.LineSegment(App.Vector(30, 0, 0), App.Vector(35, 5, 0)),
            False,
        )
        self.doc.recompute()
        originals = tuple(self.doc.Objects)
        document = self.doc

        class SameTransactionSketchDistractor:
            def __init__(self):
                self.injected = False
                self.merge_result = None
                self.distractor = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj in originals
                    or obj.TypeId != "Sketcher::SketchObject"
                ):
                    return
                self.injected = True
                self.merge_result = obj
                self.distractor = document.addObject(
                    "Sketcher::SketchObject",
                    "SameTransactionSketchDistractor",
                )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()
        observer = SameTransactionSketchDistractor()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("Sketcher_MergeSketches", 0)
            self.flush_gui(80)
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.merge_result)
        self.assertIsNotNone(observer.distractor)
        self.assertEqual(
            self.sketch.GeometryCount + second.GeometryCount,
            observer.merge_result.GeometryCount,
        )
        self.assertLess(
            observer.merge_result.Placement.Base.distanceToPoint(
                source_placement.Base
            ),
            1e-9,
        )
        self.assertGreater(
            observer.distractor.Placement.Base.distanceToPoint(
                source_placement.Base
            ),
            1.0,
        )
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_merge_sketches_aborts_if_a_source_is_replaced_during_creation(self):
        second = self.doc.addObject(
            "Sketcher::SketchObject",
            "ReplaceMergeSource",
        )
        second.addGeometry(
            Part.LineSegment(
                App.Vector(30, 0, 0),
                App.Vector(35, 5, 0),
            ),
            False,
        )
        self.doc.recompute()
        originals = tuple(self.doc.Objects)
        operations_before = tuple(self._timeline().Operations)
        undo_before = self.doc.UndoCount
        document = self.doc
        source_name = second.Name

        class ReplaceMergeSourceObserver:
            def __init__(self):
                self.injected = False

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj in originals
                    or obj.TypeId != "Sketcher::SketchObject"
                ):
                    return
                self.injected = True
                document.removeObject(source_name)
                replacement = document.addObject(
                    "Sketcher::SketchObject",
                    source_name,
                )
                replacement.addGeometry(
                    Part.LineSegment(
                        App.Vector(80, 0, 0),
                        App.Vector(90, 0, 0),
                    ),
                    False,
                )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()
        observer = ReplaceMergeSourceObserver()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("Sketcher_MergeSketches", 0)
            self.flush_gui(100)
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertEqual(originals, tuple(self.doc.Objects))
        self.assertEqual(
            operations_before,
            tuple(self._timeline().Operations),
        )
        self.assertEqual(undo_before, self.doc.UndoCount)
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertIs(self.doc.getObject(source_name), second)

    def test_merge_and_mirror_outputs_are_intentionally_independent_copies(self):
        second = self.doc.addObject(
            "Sketcher::SketchObject",
            "IndependentCopySource",
        )
        second.addGeometry(
            Part.LineSegment(
                App.Vector(30, 0, 0),
                App.Vector(35, 5, 0),
            ),
            False,
        )
        self.doc.recompute()

        merge_originals = tuple(self.doc.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()
        Gui.runCommand("Sketcher_MergeSketches", 0)
        self.flush_gui(80)
        merged = next(
            obj
            for obj in self.doc.Objects
            if obj not in merge_originals
        )
        merged_count = merged.GeometryCount

        mirror_originals = tuple(self.doc.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_MirrorSketch", 0)
        self.flush_gui(80)
        mirrored = next(
            obj
            for obj in self.doc.Objects
            if obj not in mirror_originals
        )
        mirrored_count = mirrored.GeometryCount

        self.sketch.addGeometry(
            Part.LineSegment(
                App.Vector(-30, 20, 0),
                App.Vector(-22, 24, 0),
            ),
            False,
        )
        second.addGeometry(
            Part.LineSegment(
                App.Vector(40, 0, 0),
                App.Vector(44, 4, 0),
            ),
            False,
        )
        self.doc.recompute()

        self.assertEqual(merged_count, merged.GeometryCount)
        self.assertEqual(mirrored_count, mirrored.GeometryCount)
        self.assertNotIn("SourceSketches", merged.PropertiesList)
        self.assertNotIn("SourceSketches", mirrored.PropertiesList)

    def test_mirror_sketch_rebases_only_real_geometry_references(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        originals = tuple(self.doc.Objects)
        self._respond_to_modal(True)

        Gui.runCommand("Sketcher_MirrorSketch", 0)
        self.flush_gui(80)

        created = [obj for obj in self.doc.Objects if obj not in originals]
        self.assertEqual(1, len(created))
        mirrored = created[0]
        self.assertTrue(mirrored.isDerivedFrom("Sketcher::SketchObject"))
        self.assertEqual(self.sketch.GeometryCount, mirrored.GeometryCount)
        undefined_geometry = Sketcher.Constraint().First
        for constraint in mirrored.Constraints:
            for geometry_id in (
                constraint.First,
                constraint.Second,
                constraint.Third,
            ):
                self.assertTrue(
                    geometry_id == undefined_geometry
                    or geometry_id in (-1, -2)
                    or 0 <= geometry_id < mirrored.GeometryCount,
                    (constraint.Type, geometry_id, mirrored.GeometryCount),
                )
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_multi_mirror_is_one_timeline_operation_and_survives_reopen(self):
        second = self.doc.addObject("Sketcher::SketchObject", "SecondMirrorSource")
        second.addGeometry(
            Part.LineSegment(App.Vector(30, 0, 0), App.Vector(35, 5, 0)),
            False,
        )
        self.doc.recompute()
        originals = tuple(self.doc.Objects)
        undo_before = self.doc.UndoCount
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        Gui.Selection.addSelection(second)
        self.flush_gui()
        self._respond_to_modal(True)

        Gui.runCommand("Sketcher_MirrorSketch", 0)
        self.flush_gui(80)

        created = [obj for obj in self.doc.Objects if obj not in originals]
        self.assertEqual(2, len(created))
        resource, operation = created
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(resource.VibeCADTimelineRole, "resource")
        self.assertIs(resource.VibeCADTimelineOwner, operation)
        self.assertEqual(
            resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        controller = self._timeline()
        self.assertIn(operation, controller.Operations)
        self.assertIn(resource, controller.Operations)
        visible_names = self._timeline_object_names()
        self.assertEqual(visible_names.count(operation.Name), 1)
        self.assertNotIn(resource.Name, visible_names)
        self.assertEqual(self.doc.UndoCount, undo_before + 1)
        self.assertFalse(self.doc.HasPendingTransaction)

        operation_name = operation.Name
        resource_name = resource.Name
        source_name = self.sketch.Name
        second_name = second.Name
        self.doc.undo()
        self.flush_gui()
        self.assertIsNone(self.doc.getObject(operation_name))
        self.assertIsNone(self.doc.getObject(resource_name))
        self.doc.redo()
        self.flush_gui()
        operation = self.doc.getObject(operation_name)
        resource = self.doc.getObject(resource_name)
        self.assertIs(resource.VibeCADTimelineOwner, operation)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self.flush_gui()
        self.assertFalse(operation.Visibility)
        self.assertFalse(resource.Visibility)
        self.assertTrue(self.doc.getObject(source_name).Visibility)
        self.assertTrue(self.doc.getObject(second_name).Visibility)
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self.flush_gui()
        self.assertTrue(operation.Visibility)
        self.assertTrue(resource.Visibility)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "multi-mirror.FCStd"
            self.doc.saveAs(str(path))
            closing_document = self.doc
            self.doc = None
            self.close_gui_document(closing_document)
            self.doc = App.openDocument(str(path))
            App.setActiveDocument(self.doc.Name)
            self.flush_gui(80)

            reopened_operation = self.doc.getObject(operation_name)
            reopened_resource = self.doc.getObject(resource_name)
            self.assertEqual(
                reopened_operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                reopened_resource.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                reopened_resource.VibeCADTimelineOwner,
                reopened_operation,
            )
            self.assertIn(reopened_operation, self._timeline().Operations)
            self.assertIn(reopened_resource, self._timeline().Operations)
            visible_names = self._timeline_object_names()
            self.assertEqual(
                visible_names.count(reopened_operation.Name),
                1,
            )
            self.assertNotIn(reopened_resource.Name, visible_names)

    def test_reorient_rejects_a_same_name_replacement_from_its_modal_dialog(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        original_name = self.sketch.Name
        replacement_holder = []
        attempts = [0]

        def replace_and_accept():
            attempts[0] += 1
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                if attempts[0] < 200:
                    QtCore.QTimer.singleShot(5, replace_and_accept)
                return
            self.doc.removeObject(original_name)
            replacement = self.doc.addObject(
                "Sketcher::SketchObject",
                original_name,
            )
            replacement.Placement = App.Placement(
                App.Vector(90, 80, 70),
                App.Rotation(App.Vector(0, 0, 1), 13),
            )
            replacement_holder.append(replacement)
            modal.accept()

        QtCore.QTimer.singleShot(0, replace_and_accept)
        Gui.runCommand("Sketcher_ReorientSketch", 0)
        self.flush_gui(100)

        self.assertEqual(1, len(replacement_holder))
        replacement = replacement_holder[0]
        self.assertIs(self.doc.getObject(original_name), replacement)
        self.assertTrue(
            replacement.Placement.isSame(
                App.Placement(
                    App.Vector(90, 80, 70),
                    App.Rotation(App.Vector(0, 0, 1), 13),
                ),
                1e-12,
            )
        )
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_mirror_rejects_a_same_name_replacement_from_its_modal_dialog(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        original_name = self.sketch.Name
        original_object_names = {
            obj.Name
            for obj in self.doc.Objects
            if obj is not self.sketch
        }
        replacement_holder = []
        attempts = [0]

        def replace_and_accept():
            attempts[0] += 1
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                if attempts[0] < 200:
                    QtCore.QTimer.singleShot(5, replace_and_accept)
                return
            self.doc.removeObject(original_name)
            replacement = self.doc.addObject(
                "Sketcher::SketchObject",
                original_name,
            )
            replacement.addGeometry(
                Part.LineSegment(
                    App.Vector(70, 0, 0),
                    App.Vector(80, 0, 0),
                ),
                False,
            )
            replacement_holder.append(replacement)
            modal.accept()

        QtCore.QTimer.singleShot(0, replace_and_accept)
        Gui.runCommand("Sketcher_MirrorSketch", 0)
        self.flush_gui(100)

        self.assertEqual(1, len(replacement_holder))
        replacement = replacement_holder[0]
        self.assertIs(self.doc.getObject(original_name), replacement)
        self.assertEqual(1, replacement.GeometryCount)
        self.assertFalse(
            any(
                obj.Name not in original_object_names | {original_name}
                and obj.TypeId == "Sketcher::SketchObject"
                for obj in self.doc.Objects
            )
        )
        self.assertFalse(self.doc.HasPendingTransaction)

    def test_reorient_task_cancel_restores_exact_sketch_placement(self):
        self.sketch.Placement = App.Placement(
            App.Vector(7, -4, 3),
            App.Rotation(App.Vector(1, 1, 0), 27),
        )
        self.doc.recompute()
        original = App.Placement(self.sketch.Placement)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.sketch)
        self.flush_gui()
        self._respond_to_modal(True)
        Gui.runCommand("Sketcher_ReorientSketch", 0)
        self.flush_gui(80)
        self.assertIsNotNone(Gui.activeDocument().getInEdit())

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self.flush_gui(80)
        self.assertTrue(self.sketch.Placement.isSame(original, 1e-12))
        self.assertFalse(self.doc.HasPendingTransaction)


if __name__ == "__main__":
    unittest.main()
