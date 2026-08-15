# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, detached B-spline knot multiplicity decrease."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchBSplineKnotMultiplicityDecrease(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchBSplineKnotMultiplicityDecrease")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    def cubic_bspline(self, y=0.0, construction=False):
        curve = Part.BSplineCurve()
        curve.interpolate(
            [
                App.Vector(0, y),
                App.Vector(2, y + 3),
                App.Vector(5, y - 2),
                App.Vector(8, y + 2),
                App.Vector(11, y),
            ]
        )
        index = self.Sketch.addGeometry(curve, construction)
        self.assertEqual(self.Sketch.Geometry[index].Degree, 3)
        self.assertGreater(self.Sketch.Geometry[index].NbKnots, 3)
        return index

    @staticmethod
    def maximum_shape_deviation(first_curve, second_curve):
        def directed(source, target_shape):
            first = float(source.FirstParameter)
            last = float(source.LastParameter)
            return max(
                Part.Vertex(source.value(first + (last - first) * fraction / 64.0))
                .distToShape(target_shape)[0]
                for fraction in range(65)
            )

        return max(
            directed(first_curve, second_curve.toShape()),
            directed(second_curve, first_curve.toShape()),
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

    @staticmethod
    def receipt_indices(receipt, collection, field):
        return [item["index"] for item in receipt[collection][field]]

    def assert_complete_helpers(self, root):
        curve = self.Sketch.Geometry[root]
        alignments = [
            item
            for item in self.Sketch.Constraints
            if item.Type == "InternalAlignment" and item.Second == root
        ]
        self.assertEqual(len(alignments), curve.NbPoles + curve.NbKnots)
        self.assertEqual(
            sorted(
                item.InternalAlignmentIndex
                for item in alignments
                if self.Sketch.GeometryFacadeList[item.First].InternalType
                == "BSplineControlPoint"
            ),
            list(range(curve.NbPoles)),
        )
        self.assertEqual(
            sorted(
                item.InternalAlignmentIndex
                for item in alignments
                if self.Sketch.GeometryFacadeList[item.First].InternalType
                == "BSplineKnotPoint"
            ),
            list(range(curve.NbKnots)),
        )

    def test_unexposed_diagnosis_is_pure_and_commit_removes_single_knot(self):
        root = self.cubic_bspline(construction=True)
        knot = 2
        before = self.signature(self.Sketch)
        original = self.Sketch.Geometry[root].copy()
        old_knots = tuple(original.getKnots())
        old_poles = original.NbPoles
        root_tag = str(self.Sketch.GeometryFacadeList[root].Tag)

        diagnostic = self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(root, knot)
        changed = diagnostic["geometry"][root]
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["geometry_index"], root)
        self.assertEqual(diagnostic["knot_index"], knot)
        self.assertEqual(diagnostic["knot_parameter"], old_knots[knot])
        self.assertEqual(diagnostic["old_multiplicity"], 1)
        self.assertEqual(diagnostic["new_multiplicity"], 0)
        self.assertEqual(changed.NbPoles, old_poles - 1)
        self.assertEqual(
            tuple(changed.getKnots()), old_knots[:knot] + old_knots[knot + 1 :]
        )
        expected_deviation = self.maximum_shape_deviation(original, changed)
        self.assertGreater(expected_deviation, 0.0)
        self.assertEqual(self.signature(self.Sketch), before)

        receipt = self.Sketch.decreaseBSplineKnotMultiplicityExact(root, knot)
        changed = self.Sketch.Geometry[root]
        self.assertAlmostEqual(
            self.maximum_shape_deviation(original, changed),
            expected_deviation,
            delta=1.0e-9,
        )
        self.assertEqual(str(self.Sketch.GeometryFacadeList[root].Tag), root_tag)
        self.assertTrue(self.Sketch.getConstruction(root))
        self.assert_complete_helpers(root)
        expected = diagnostic["mutation_receipt"]
        for collection in ("geometry", "constraints"):
            for field in ("deleted", "created"):
                self.assertEqual(
                    self.receipt_indices(receipt, collection, field),
                    self.receipt_indices(expected, collection, field),
                )

    def test_existing_higher_multiplicity_and_helpers_are_reconciled(self):
        root = self.cubic_bspline()
        knot = 2
        self.Sketch.modifyBSplineKnotMultiplicity(root, knot + 1, 1)
        self.Sketch.exposeInternalGeometry(root)
        before_knots = tuple(self.Sketch.Geometry[root].getKnots())
        before_count = self.Sketch.GeometryCount
        root_tag = str(self.Sketch.GeometryFacadeList[root].Tag)

        diagnostic = self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(root, knot)
        self.assertEqual(
            (diagnostic["old_multiplicity"], diagnostic["new_multiplicity"]),
            (2, 1),
        )
        self.assertEqual(tuple(diagnostic["geometry"][root].getKnots()), before_knots)
        self.assertEqual(
            diagnostic["retained_internal_geometry_count"]
            + diagnostic["deleted_internal_geometry_count"],
            before_count - 1,
        )
        self.Sketch.decreaseBSplineKnotMultiplicityExact(root, knot)
        self.assertEqual(str(self.Sketch.GeometryFacadeList[root].Tag), root_tag)
        self.assertEqual(tuple(self.Sketch.Geometry[root].getKnots()), before_knots)
        self.assertEqual(self.Sketch.Geometry[root].getMultiplicities()[knot], 1)
        self.assert_complete_helpers(root)

    def test_unrelated_constraint_identity_and_expression_are_preserved(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 8), App.Vector(11, 8)), False
        )
        length = self.Sketch.addConstraint(Sketcher.Constraint("Distance", line, 11.0))
        self.Sketch.renameConstraint(length, "ReferenceLength")
        self.Sketch.setExpression(f"Constraints[{length}]", "11 mm")
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        constraint_tag = str(self.Sketch.Constraints[length].Tag)

        self.Sketch.decreaseBSplineKnotMultiplicityExact(root, 2)
        self.assertEqual(str(self.Sketch.Constraints[length].Tag), constraint_tag)
        self.assertEqual(self.Sketch.Constraints[length].Name, "ReferenceLength")
        self.assertEqual(
            tuple(self.Sketch.ExpressionEngine),
            ((".Constraints.ReferenceLength", "11 mm"),),
        )

    def test_kernel_rejected_endpoint_is_refused_without_mutation(self):
        root = self.cubic_bspline()
        before = self.signature(self.Sketch)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(root, 0)
        with self.assertRaises(ValueError):
            self.Sketch.decreaseBSplineKnotMultiplicityExact(root, 0)
        self.assertEqual(self.signature(self.Sketch), before)

    def test_invalid_targets_are_rejected_without_mutation(self):
        root = self.cubic_bspline()
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 5), App.Vector(11, 5)), False
        )
        before = self.signature(self.Sketch)
        for geometry, knot in (
            (line, 0),
            (-1, 0),
            (999, 0),
            (root, -1),
            (root, 999),
        ):
            with self.subTest(geometry=geometry, knot=knot):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(
                        geometry, knot
                    )
                with self.assertRaises(ValueError):
                    self.Sketch.decreaseBSplineKnotMultiplicityExact(geometry, knot)
                self.assertEqual(self.signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(True, 1)

    def test_diagnosis_rejects_malformed_duplicate_helper_alignment(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        alignment = next(
            item
            for item in self.Sketch.Constraints
            if item.Type == "InternalAlignment" and item.Second == root
        )
        internal_type = self.Sketch.GeometryFacadeList[alignment.First].InternalType
        duplicate = Sketcher.Constraint(
            f"InternalAlignment:{internal_type}",
            alignment.First,
            alignment.FirstPos,
            alignment.Second,
            alignment.InternalAlignmentIndex,
        )
        self.Sketch.addConstraint(duplicate)
        before = self.signature(self.Sketch)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseDecreaseBSplineKnotMultiplicity(root, 2)
        self.assertEqual(self.signature(self.Sketch), before)

    def test_existing_occ_indexed_api_remains_available(self):
        root = self.cubic_bspline()
        knot = 2
        parameter = self.Sketch.Geometry[root].getKnots()[knot]
        self.Sketch.modifyBSplineKnotMultiplicity(root, knot + 1, -1)
        self.assertNotIn(parameter, tuple(self.Sketch.Geometry[root].getKnots()))

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Decrease Knot Multiplicity")
        self.Sketch.decreaseBSplineKnotMultiplicityExact(root, 2)
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
