# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, detached B-spline knot multiplicity increase."""

import unittest
import math

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchBSplineKnotMultiplicityIncrease(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchBSplineKnotMultiplicityIncrease")
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
        self.assertGreater(self.Sketch.Geometry[index].NbKnots, 2)
        return index

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

    def test_unexposed_diagnosis_is_pure_and_commit_matches(self):
        root = self.cubic_bspline(construction=True)
        before = self.signature(self.Sketch)
        original_samples = self.samples(self.Sketch.Geometry[root])
        multiplicities = tuple(self.Sketch.Geometry[root].getMultiplicities())
        root_tag = str(self.Sketch.GeometryFacadeList[root].Tag)

        diagnostic = self.Sketch.diagnoseIncreaseBSplineKnotMultiplicity(root, 1)
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["geometry_index"], root)
        self.assertEqual(diagnostic["knot_index"], 1)
        self.assertEqual(diagnostic["degree"], 3)
        self.assertEqual(diagnostic["old_multiplicity"], multiplicities[1])
        self.assertEqual(diagnostic["new_multiplicity"], multiplicities[1] + 1)
        self.assertEqual(diagnostic["retained_internal_geometry_count"], 0)
        self.assertEqual(diagnostic["deleted_internal_geometry_count"], 0)
        self.assertGreater(diagnostic["exposed_internal_geometry_count"], 0)
        self.assertEqual(self.signature(self.Sketch), before)

        receipt = self.Sketch.increaseBSplineKnotMultiplicityExact(root, 1)
        changed = self.Sketch.Geometry[root]
        self.assertEqual(changed.getMultiplicities()[1], multiplicities[1] + 1)
        self.assertLess(
            max(
                math.dist(before_point, after_point)
                for before_point, after_point in zip(
                    original_samples, self.samples(changed), strict=True
                )
            ),
            1.0e-3,
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

    def test_existing_helpers_are_reconciled_without_replacing_root(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        root_tag = str(self.Sketch.GeometryFacadeList[root].Tag)
        before_count = self.Sketch.GeometryCount

        diagnostic = self.Sketch.diagnoseIncreaseBSplineKnotMultiplicity(root, 1)
        self.assertEqual(
            diagnostic["retained_internal_geometry_count"]
            + diagnostic["deleted_internal_geometry_count"],
            before_count - 1,
        )
        self.Sketch.increaseBSplineKnotMultiplicityExact(root, 1)
        self.assertEqual(str(self.Sketch.GeometryFacadeList[root].Tag), root_tag)
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

        self.Sketch.increaseBSplineKnotMultiplicityExact(root, 1)
        self.assertEqual(str(self.Sketch.Constraints[length].Tag), constraint_tag)
        self.assertEqual(self.Sketch.Constraints[length].Name, "ReferenceLength")
        self.assertEqual(
            tuple(self.Sketch.ExpressionEngine),
            ((".Constraints.ReferenceLength", "11 mm"),),
        )

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
            (root, 0),
        ):
            with self.subTest(geometry=geometry, knot=knot):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseIncreaseBSplineKnotMultiplicity(geometry, knot)
                with self.assertRaises(ValueError):
                    self.Sketch.increaseBSplineKnotMultiplicityExact(geometry, knot)
                self.assertEqual(self.signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseIncreaseBSplineKnotMultiplicity(True, 1)

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
            self.Sketch.diagnoseIncreaseBSplineKnotMultiplicity(root, 1)
        self.assertEqual(self.signature(self.Sketch), before)

    def test_existing_occ_indexed_api_remains_available(self):
        root = self.cubic_bspline()
        before = tuple(self.Sketch.Geometry[root].getMultiplicities())
        self.Sketch.modifyBSplineKnotMultiplicity(root, 2, 1)
        self.assertEqual(
            self.Sketch.Geometry[root].getMultiplicities()[1], before[1] + 1
        )

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        root = self.cubic_bspline()
        self.Sketch.exposeInternalGeometry(root)
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Increase Knot Multiplicity")
        self.Sketch.increaseBSplineKnotMultiplicityExact(root, 1)
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
