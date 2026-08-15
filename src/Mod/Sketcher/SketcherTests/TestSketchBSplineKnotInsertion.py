# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, detached B-spline knot insertion."""

import math
import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchBSplineKnotInsertion(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchBSplineKnotInsertion")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    def cubic_bspline(self, construction=False):
        curve = Part.BSplineCurve()
        curve.interpolate(
            [
                App.Vector(0, 0),
                App.Vector(2, 3),
                App.Vector(5, -2),
                App.Vector(8, 2),
                App.Vector(11, 0),
            ]
        )
        return self.Sketch.addGeometry(curve, construction)

    @staticmethod
    def insertion_parameter(curve):
        knots = tuple(curve.getKnots())
        return (knots[1] + knots[2]) * 0.5

    @staticmethod
    def samples(curve):
        first = float(curve.FirstParameter)
        last = float(curve.LastParameter)
        return tuple(
            tuple(curve.value(first + (last - first) * offset / 64.0))
            for offset in range(65)
        )

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

    def assert_complete_helpers(self, root):
        curve = self.Sketch.Geometry[root]
        alignments = [
            item
            for item in self.Sketch.Constraints
            if item.Type == "InternalAlignment" and item.Second == root
        ]
        self.assertEqual(len(alignments), curve.NbPoles + curve.NbKnots)
        for internal_type, count in (
            ("BSplineControlPoint", curve.NbPoles),
            ("BSplineKnotPoint", curve.NbKnots),
        ):
            self.assertEqual(
                sorted(
                    item.InternalAlignmentIndex
                    for item in alignments
                    if self.Sketch.GeometryFacadeList[item.First].InternalType
                    == internal_type
                ),
                list(range(count)),
            )

    def test_new_knot_diagnosis_is_pure_and_commit_preserves_shape_and_root(self):
        root = self.cubic_bspline(construction=True)
        parameter = self.insertion_parameter(self.Sketch.Geometry[root])
        before = self.signature(self.Sketch)
        before_samples = self.samples(self.Sketch.Geometry[root])
        before_knots = tuple(self.Sketch.Geometry[root].getKnots())
        root_tag = str(self.Sketch.GeometryFacadeList[root].Tag)

        diagnostic = self.Sketch.diagnoseInsertBSplineKnot(root, parameter)
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["geometry_index"], root)
        self.assertAlmostEqual(diagnostic["requested_parameter"], parameter)
        self.assertAlmostEqual(diagnostic["knot_parameter"], parameter)
        self.assertEqual(diagnostic["old_multiplicity"], 0)
        self.assertEqual(diagnostic["new_multiplicity"], 1)
        self.assertEqual(self.signature(self.Sketch), before)

        self.Sketch.insertBSplineKnotExact(root, parameter)
        changed = self.Sketch.Geometry[root]
        self.assertEqual(changed.NbKnots, len(before_knots) + 1)
        self.assertIn(parameter, tuple(changed.getKnots()))
        self.assertLess(
            max(
                math.dist(left, right)
                for left, right in zip(
                    before_samples, self.samples(changed), strict=True
                )
            ),
            1.0e-3,
        )
        self.assertEqual(str(self.Sketch.GeometryFacadeList[root].Tag), root_tag)
        self.assertTrue(self.Sketch.getConstruction(root))
        self.assert_complete_helpers(root)

    def test_existing_knot_increases_multiplicity_by_one(self):
        root = self.cubic_bspline()
        curve = self.Sketch.Geometry[root]
        parameter = tuple(curve.getKnots())[1]
        multiplicity = tuple(curve.getMultiplicities())[1]
        diagnostic = self.Sketch.diagnoseInsertBSplineKnot(root, parameter)
        self.assertEqual(diagnostic["old_multiplicity"], multiplicity)
        self.assertEqual(diagnostic["new_multiplicity"], multiplicity + 1)
        self.Sketch.insertBSplineKnotExact(root, parameter)
        self.assertEqual(
            tuple(self.Sketch.Geometry[root].getMultiplicities())[1], multiplicity + 1
        )

    def test_existing_helpers_and_unrelated_expression_are_preserved(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 8), App.Vector(11, 8)), False
        )
        length = self.Sketch.addConstraint(Sketcher.Constraint("Distance", line, 11.0))
        self.Sketch.renameConstraint(length, "ReferenceLength")
        self.Sketch.setExpression(f"Constraints[{length}]", "11 mm")
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        constraint_tag = str(self.Sketch.Constraints[length].Tag)
        parameter = self.insertion_parameter(self.Sketch.Geometry[root])

        self.Sketch.insertBSplineKnotExact(root, parameter)
        self.assertEqual(str(self.Sketch.Constraints[length].Tag), constraint_tag)
        self.assertEqual(self.Sketch.Constraints[length].Name, "ReferenceLength")
        self.assertEqual(
            tuple(self.Sketch.ExpressionEngine),
            ((".Constraints.ReferenceLength", "11 mm"),),
        )
        self.assert_complete_helpers(root)

    def test_invalid_targets_are_rejected_without_mutation(self):
        root = self.cubic_bspline()
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 5), App.Vector(11, 5)), False
        )
        curve = self.Sketch.Geometry[root]
        before = self.signature(self.Sketch)
        for geometry, parameter in (
            (line, self.insertion_parameter(curve)),
            (-1, self.insertion_parameter(curve)),
            (999, self.insertion_parameter(curve)),
            (root, float("nan")),
            (root, float("inf")),
            (root, curve.FirstParameter - 1.0),
            (root, curve.LastParameter + 1.0),
            (root, curve.FirstParameter),
        ):
            with self.subTest(geometry=geometry, parameter=parameter):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseInsertBSplineKnot(geometry, parameter)
                with self.assertRaises(ValueError):
                    self.Sketch.insertBSplineKnotExact(geometry, parameter)
                self.assertEqual(self.signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseInsertBSplineKnot(True, 0.5)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseInsertBSplineKnot(root, True)

    def test_diagnosis_rejects_duplicate_helper_alignment(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        alignment = next(
            item
            for item in self.Sketch.Constraints
            if item.Type == "InternalAlignment" and item.Second == root
        )
        internal_type = self.Sketch.GeometryFacadeList[alignment.First].InternalType
        self.Sketch.addConstraint(
            Sketcher.Constraint(
                f"InternalAlignment:{internal_type}",
                alignment.First,
                alignment.FirstPos,
                alignment.Second,
                alignment.InternalAlignmentIndex,
            )
        )
        before = self.signature(self.Sketch)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseInsertBSplineKnot(
                root, self.insertion_parameter(self.Sketch.Geometry[root])
            )
        self.assertEqual(self.signature(self.Sketch), before)

    def test_existing_insert_api_remains_available(self):
        root = self.cubic_bspline()
        parameter = self.insertion_parameter(self.Sketch.Geometry[root])
        before = self.Sketch.Geometry[root].NbKnots
        self.Sketch.insertBSplineKnot(root, parameter, 1)
        self.assertEqual(self.Sketch.Geometry[root].NbKnots, before + 1)

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        parameter = self.insertion_parameter(self.Sketch.Geometry[root])
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Insert Knot")
        self.Sketch.insertBSplineKnotExact(root, parameter)
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
