# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, detached Sketch Join Curves."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchJoin(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchJoin")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    @staticmethod
    def signature(sketch):
        return (
            tuple(
                (
                    item.TypeId,
                    str(facade.Tag),
                    int(facade.Id),
                    bool(facade.Construction),
                    facade.InternalType,
                    tuple(getattr(item, "getKnots", lambda: ())()),
                    tuple(getattr(item, "getMultiplicities", lambda: ())()),
                )
                for item, facade in zip(
                    sketch.Geometry, sketch.GeometryFacadeList, strict=True
                )
            ),
            tuple(
                (
                    item.Type,
                    item.First,
                    item.FirstPos,
                    item.Second,
                    item.SecondPos,
                    item.InternalAlignmentIndex,
                    str(item.Tag),
                    item.Name,
                )
                for item in sketch.Constraints
            ),
            tuple(sketch.ExpressionEngine),
            sketch.GeometryCount,
            sketch.ConstraintCount,
        )

    def line_pair(self, *, construction=False, tangent=False):
        first = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)), construction
        )
        second = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(10, 0), App.Vector(20, 0 if tangent else 5)),
            construction,
        )
        constraint = "Tangent" if tangent else "Coincident"
        self.Sketch.addConstraint(Sketcher.Constraint(constraint, first, 2, second, 1))
        self.Doc.recompute()
        return first, second

    @staticmethod
    def endpoint(curve, first):
        parameter = curve.FirstParameter if first else curve.LastParameter
        return tuple(curve.value(parameter))

    def assert_complete_helpers(self, root):
        curve = self.Sketch.Geometry[root]
        alignments = [
            item
            for item in self.Sketch.Constraints
            if item.Type == "InternalAlignment" and item.Second == root
        ]
        self.assertEqual(len(alignments), curve.NbPoles + curve.NbKnots)

    def test_detached_diagnosis_is_pure_and_exact_commit_preserves_unrelated_state(
        self,
    ):
        first, second = self.line_pair()
        unrelated = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 10), App.Vector(12, 10)), False
        )
        distance = self.Sketch.addConstraint(
            Sketcher.Constraint("Distance", unrelated, 12.0)
        )
        self.Sketch.renameConstraint(distance, "PreservedLength")
        self.Sketch.setExpression(f"Constraints[{distance}]", "12 mm")
        self.Doc.recompute()
        unrelated_tag = str(self.Sketch.GeometryFacadeList[unrelated].Tag)
        constraint_tag = str(self.Sketch.Constraints[distance].Tag)
        before_expressions = tuple(self.Sketch.ExpressionEngine)
        before = self.signature(self.Sketch)
        undo_before = self.Doc.UndoCount

        diagnostic = self.Sketch.diagnoseJoinCurves(first, 2, second, 1)
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["continuity"], 0)
        self.assertEqual(diagnostic["first_geometry_index"], first)
        self.assertEqual(diagnostic["second_geometry_index"], second)
        self.assertEqual(self.signature(self.Sketch), before)
        self.assertEqual(self.Doc.UndoCount, undo_before)

        receipt = self.Sketch.joinCurvesExact(first, 2, second, 1)
        self.assertEqual(self.Sketch.Geometry[0].TypeId, "Part::GeomBSplineCurve")
        self.assertEqual(self.endpoint(self.Sketch.Geometry[0], True), (0.0, 0.0, 0.0))
        self.assertEqual(
            self.endpoint(self.Sketch.Geometry[0], False), (20.0, 5.0, 0.0)
        )
        self.assert_complete_helpers(0)
        unrelated_after = next(
            index
            for index, facade in enumerate(self.Sketch.GeometryFacadeList)
            if str(facade.Tag) == unrelated_tag
        )
        distance_after = next(
            index
            for index, item in enumerate(self.Sketch.Constraints)
            if str(item.Tag) == constraint_tag
        )
        self.assertEqual(self.Sketch.Constraints[distance_after].First, unrelated_after)
        self.assertEqual(
            self.Sketch.Constraints[distance_after].Name, "PreservedLength"
        )
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), before_expressions)
        self.assertEqual(
            len(receipt["geometry"]["created"]),
            self.Sketch.GeometryCount - 1,
        )

    def test_tangent_constraint_selects_c1_join(self):
        first, second = self.line_pair(tangent=True)
        diagnostic = self.Sketch.diagnoseJoinCurves(first, 2, second, 1)
        self.assertEqual(diagnostic["continuity"], 1)
        self.Sketch.joinCurvesExact(first, 2, second, 1)
        self.assertEqual(self.endpoint(self.Sketch.Geometry[0], True), (0.0, 0.0, 0.0))
        self.assertEqual(
            self.endpoint(self.Sketch.Geometry[0], False), (20.0, 0.0, 0.0)
        )

    def test_start_end_selection_reverses_both_inputs(self):
        first = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)), False
        )
        second = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(-10, 5), App.Vector(0, 0)), False
        )
        self.Sketch.addConstraint(
            Sketcher.Constraint("Coincident", first, 1, second, 2)
        )
        self.Doc.recompute()
        self.Sketch.joinCurvesExact(first, 1, second, 2)
        curve = self.Sketch.Geometry[0]
        self.assertEqual(self.endpoint(curve, True), (10.0, 0.0, 0.0))
        self.assertEqual(self.endpoint(curve, False), (-10.0, 5.0, 0.0))

    def test_join_accepts_two_splines_with_existing_exposed_helpers(self):
        first_curve = Part.BSplineCurve()
        first_curve.interpolate(
            [App.Vector(0, 0), App.Vector(4, 2), App.Vector(10, 0)]
        )
        second_curve = Part.BSplineCurve()
        second_curve.interpolate(
            [App.Vector(10, 0), App.Vector(15, -2), App.Vector(20, 3)]
        )
        first = self.Sketch.addGeometry(first_curve, False)
        second = self.Sketch.addGeometry(second_curve, False)
        self.Sketch.addConstraint(
            Sketcher.Constraint("Coincident", first, 2, second, 1)
        )
        self.Sketch.exposeInternalGeometry(first)
        self.Sketch.exposeInternalGeometry(second)
        self.Doc.recompute()
        before_count = self.Sketch.GeometryCount

        diagnostic = self.Sketch.diagnoseJoinCurves(first, 2, second, 1)
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertLess(diagnostic["geometry_count"], before_count - 2)
        self.Sketch.joinCurvesExact(first, 2, second, 1)
        self.assert_complete_helpers(0)
        self.assertEqual(self.endpoint(self.Sketch.Geometry[0], True), (0.0, 0.0, 0.0))
        self.assertEqual(
            self.endpoint(self.Sketch.Geometry[0], False), (20.0, 3.0, 0.0)
        )

    def test_invalid_targets_are_rejected_without_mutation(self):
        first, second = self.line_pair()
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(40, 0), App.Vector(0, 0, 1), 5), False
        )
        point = self.Sketch.addGeometry(Part.Point(App.Vector(50, 0)), False)
        construction = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(20, 5), App.Vector(30, 5)), True
        )
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        for arguments in (
            (first, 0, second, 1),
            (first, 2, second, 3),
            (first, 2, first, 1),
            (first, 2, 99, 1),
            (first, 2, circle, 1),
            (first, 2, point, 1),
            (first, 2, construction, 1),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.Sketch.diagnoseJoinCurves(*arguments)
                self.assertEqual(self.signature(self.Sketch), before)
        for arguments in (
            (True, 2, second, 1),
            (first, True, second, 1),
            (first, 2, False, 1),
            (first, 2, second, False),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    self.Sketch.diagnoseJoinCurves(*arguments)
                self.assertEqual(self.signature(self.Sketch), before)

    def test_legacy_join_api_remains_available(self):
        first, second = self.line_pair()
        self.assertIsNone(self.Sketch.join(first, 2, second, 1, 0))
        self.assertEqual(self.Sketch.Geometry[0].TypeId, "Part::GeomBSplineCurve")

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        first, second = self.line_pair(construction=True)
        self.Doc.clearUndos()
        before = self.signature(self.Sketch)
        self.Doc.openTransaction("Join Sketch Curves")
        self.Sketch.joinCurvesExact(first, 2, second, 1)
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertTrue(self.Sketch.GeometryFacadeList[0].Construction)
        self.assertEqual(self.Doc.UndoNames[0], "Join Sketch Curves")
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
