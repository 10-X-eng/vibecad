# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for atomic, detached B-spline degree elevation."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchBSplineDegree(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchBSplineDegree")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    def line_bspline(self, y=0.0, construction=False):
        index = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, y), App.Vector(10, y)),
            construction,
        )
        self.Sketch.convertToNURBS(index)
        return index

    def interpolated_bspline(self, y=0.0, construction=False):
        curve = Part.BSplineCurve()
        curve.interpolate(
            [
                App.Vector(0, y),
                App.Vector(3, y + 2),
                App.Vector(6, y - 1),
                App.Vector(10, y + 1),
            ]
        )
        return self.Sketch.addGeometry(curve, construction)

    @staticmethod
    def curve_samples(curve):
        first = float(curve.FirstParameter)
        last = float(curve.LastParameter)
        return tuple(
            tuple(curve.value(first + (last - first) * fraction))
            for fraction in (0.0, 0.125, 0.25, 0.5, 0.75, 0.875, 1.0)
        )

    @staticmethod
    def close_samples(test, actual, expected, tolerance=1.0e-8):
        test.assertEqual(len(actual), len(expected))
        for point, reference in zip(actual, expected, strict=True):
            test.assertAlmostEqual(point[0], reference[0], delta=tolerance)
            test.assertAlmostEqual(point[1], reference[1], delta=tolerance)
            test.assertAlmostEqual(point[2], reference[2], delta=tolerance)

    @staticmethod
    def signature(sketch):
        return (
            tuple(
                (
                    item.TypeId,
                    str(facade.Tag),
                    int(facade.Id),
                    bool(facade.Construction),
                    getattr(item, "Degree", None),
                    getattr(item, "NbPoles", None),
                    getattr(item, "NbKnots", None),
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
                    str(item.Tag),
                    item.Name,
                )
                for item in sketch.Constraints
            ),
            tuple(sketch.ExpressionEngine),
            tuple(sketch.ExternalGeometry),
            tuple(sketch.ExternalTypes),
            tuple(item.TypeId for item in sketch.ExternalGeo),
            sketch.GeometryCount,
            sketch.ConstraintCount,
        )

    @staticmethod
    def receipt_indices(receipt, collection, field):
        return [item["index"] for item in receipt[collection][field]]

    def test_unexposed_degree_increase_diagnosis_is_pure_and_commit_matches(self):
        spline = self.line_bspline(construction=True)
        before = self.signature(self.Sketch)
        samples = self.curve_samples(self.Sketch.Geometry[spline])

        diagnostic = self.Sketch.diagnoseIncreaseBSplineDegree([spline])
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["input_geometry_indices"], [spline])
        self.assertEqual(diagnostic["old_degrees"], [1])
        self.assertEqual(diagnostic["new_degrees"], [2])
        self.assertEqual(diagnostic["exposed_internal_geometry_count"], 5)
        self.assertEqual(diagnostic["geometry_count"], 6)
        self.assertEqual(diagnostic["constraint_count"], 8)
        elevated = diagnostic["geometry"][spline]
        self.assertEqual((elevated.Degree, elevated.NbPoles, elevated.NbKnots), (2, 3, 2))
        self.close_samples(self, self.curve_samples(elevated), samples)
        self.assertEqual(self.signature(self.Sketch), before)

        receipt = self.Sketch.increaseBSplineDegreeExact([spline])
        elevated = self.Sketch.Geometry[spline]
        self.assertEqual((elevated.Degree, elevated.NbPoles, elevated.NbKnots), (2, 3, 2))
        self.close_samples(self, self.curve_samples(elevated), samples)
        expected = diagnostic["mutation_receipt"]
        for collection in ("geometry", "constraints"):
            for field in ("deleted", "created"):
                self.assertEqual(
                    self.receipt_indices(receipt, collection, field),
                    self.receipt_indices(expected, collection, field),
                )

    def test_existing_helpers_constraints_and_expression_are_preserved(self):
        spline = self.line_bspline()
        self.assertEqual(
            self.Sketch.exposeInternalGeometry(spline)["created_count"], 4
        )
        weight = next(
            index
            for index, constraint in enumerate(self.Sketch.Constraints)
            if constraint.Type == "Weight"
        )
        self.Sketch.renameConstraint(weight, "FirstWeight")
        self.Sketch.setExpression(f"Constraints[{weight}]", "1")
        self.Doc.recompute()
        root_tag = str(self.Sketch.GeometryFacadeList[spline].Tag)
        helper_tags = tuple(
            str(item.Tag) for item in self.Sketch.GeometryFacadeList[1:]
        )
        constraint_tags = tuple(str(item.Tag) for item in self.Sketch.Constraints)

        diagnostic = self.Sketch.diagnoseIncreaseBSplineDegree([spline])
        self.assertEqual(diagnostic["exposed_internal_geometry_count"], 1)
        self.assertEqual((diagnostic["geometry_count"], diagnostic["constraint_count"]), (6, 8))
        expected_expression = ((".Constraints.FirstWeight", "1"),)
        self.assertEqual(self.signature(self.Sketch)[2], expected_expression)

        self.Sketch.increaseBSplineDegreeExact([spline])
        self.assertEqual(str(self.Sketch.GeometryFacadeList[spline].Tag), root_tag)
        self.assertEqual(
            tuple(str(item.Tag) for item in self.Sketch.GeometryFacadeList[1:5]),
            helper_tags,
        )
        self.assertEqual(
            tuple(str(item.Tag) for item in self.Sketch.Constraints[:6]),
            constraint_tags,
        )
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), expected_expression)

    def test_multiple_splines_preserve_order_and_metadata(self):
        cubic = self.interpolated_bspline(construction=True)
        line = self.line_bspline(y=8.0)
        old_degrees = [self.Sketch.Geometry[index].Degree for index in (line, cubic)]
        samples = {
            index: self.curve_samples(self.Sketch.Geometry[index])
            for index in (line, cubic)
        }

        diagnostic = self.Sketch.diagnoseIncreaseBSplineDegree([line, cubic])
        self.assertEqual(diagnostic["input_geometry_indices"], [line, cubic])
        self.assertEqual(diagnostic["old_degrees"], old_degrees)
        self.assertEqual(
            diagnostic["new_degrees"], [degree + 1 for degree in old_degrees]
        )
        self.Sketch.increaseBSplineDegreeExact([line, cubic])
        for index, old_degree in zip((line, cubic), old_degrees, strict=True):
            self.assertEqual(self.Sketch.Geometry[index].Degree, old_degree + 1)
            self.close_samples(
                self, self.curve_samples(self.Sketch.Geometry[index]), samples[index]
            )
        self.assertFalse(self.Sketch.getConstruction(line))
        self.assertTrue(self.Sketch.getConstruction(cubic))

    def test_invalid_or_mixed_targets_are_rejected_before_mutation(self):
        spline = self.line_bspline()
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 4), App.Vector(10, 4)), False
        )
        source = self.Doc.addObject("Part::Feature", "Source")
        source.Shape = Part.makeLine(App.Vector(0, 8), App.Vector(10, 8))
        self.Doc.recompute()
        self.Sketch.addExternal(source.Name, "Edge1")
        before = self.signature(self.Sketch)
        for targets in (
            [],
            [spline, spline],
            [spline, line],
            [line],
            [-1],
            [-2],
            [-3],
            [999],
        ):
            with self.subTest(targets=targets):
                with self.assertRaises((TypeError, ValueError)):
                    self.Sketch.diagnoseIncreaseBSplineDegree(targets)
                with self.assertRaises((TypeError, ValueError)):
                    self.Sketch.increaseBSplineDegreeExact(targets)
                self.assertEqual(self.signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseIncreaseBSplineDegree([True])

    def test_maximum_degree_is_rejected_without_mutation(self):
        spline = self.line_bspline()
        self.Sketch.increaseBSplineDegree(spline, 24)
        self.assertEqual(self.Sketch.Geometry[spline].Degree, 25)
        before = self.signature(self.Sketch)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseIncreaseBSplineDegree([spline])
        with self.assertRaises(ValueError):
            self.Sketch.increaseBSplineDegreeExact([spline])
        self.assertEqual(self.signature(self.Sketch), before)

    def test_existing_single_geometry_api_remains_available(self):
        spline = self.line_bspline()
        self.assertIsNone(self.Sketch.increaseBSplineDegree(spline, 2))
        self.assertEqual(self.Sketch.Geometry[spline].Degree, 3)
        self.assertEqual((self.Sketch.GeometryCount, self.Sketch.ConstraintCount), (1, 0))

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        spline = self.line_bspline()
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Increase B-Spline Degree")
        self.Sketch.increaseBSplineDegreeExact([spline])
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
