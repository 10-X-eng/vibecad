# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for atomic, detached Geometry-to-B-Spline conversion."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchNURBSConversion(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchNURBSConversion")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    def line(self, start=(0, 0, 0), end=(10, 0, 0), construction=False):
        return self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(*start), App.Vector(*end)),
            construction,
        )

    @staticmethod
    def signature(sketch):
        geometry = tuple(
            (
                item.TypeId,
                str(facade.Tag),
                int(facade.Id),
                bool(facade.Construction),
            )
            for item, facade in zip(
                sketch.Geometry, sketch.GeometryFacadeList, strict=True
            )
        )
        constraints = tuple(
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
        )
        return (
            geometry,
            constraints,
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

    def test_internal_conversion_diagnosis_is_pure_and_commit_matches(self):
        line = self.line(construction=True)
        constraint = self.Sketch.addConstraint(
            Sketcher.Constraint("Distance", line, 10.0)
        )
        self.Sketch.renameConstraint(constraint, "RemovedLength")
        self.Sketch.setExpression(f"Constraints[{constraint}]", "10 mm")
        self.Doc.recompute()
        before = self.signature(self.Sketch)

        diagnostic = self.Sketch.diagnoseConvertToNURBS([line])
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["input_geometry_indices"], [line])
        self.assertEqual(diagnostic["converted_geometry_indices"], [line])
        self.assertEqual(diagnostic["exposed_internal_geometry_count"], 4)
        self.assertEqual(diagnostic["geometry_count"], 5)
        self.assertEqual(diagnostic["constraint_count"], 6)
        self.assertEqual(diagnostic["geometry"][line].TypeId, "Part::GeomBSplineCurve")
        self.assertEqual(
            [item.Type for item in diagnostic["constraints"]],
            [
                "InternalAlignment",
                "Weight",
                "InternalAlignment",
                "Equal",
                "InternalAlignment",
                "InternalAlignment",
            ],
        )
        self.assertEqual(self.signature(self.Sketch), before)

        receipt = self.Sketch.convertToNURBSExact([line])
        self.assertEqual(self.Sketch.Geometry[line].TypeId, "Part::GeomBSplineCurve")
        self.assertEqual(
            (self.Sketch.GeometryCount, self.Sketch.ConstraintCount), (5, 6)
        )
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), ())
        expected = diagnostic["mutation_receipt"]
        for collection in ("geometry", "constraints"):
            for field in ("deleted", "created"):
                self.assertEqual(
                    self.receipt_indices(receipt, collection, field),
                    self.receipt_indices(expected, collection, field),
                )

    def test_mixed_external_and_internal_selection_keeps_human_order(self):
        internal = self.line((0, 0, 0), (8, 0, 0))
        source = self.Doc.addObject("Part::Feature", "Source")
        source.Shape = Part.makeLine(App.Vector(0, 5, 0), App.Vector(8, 5, 0))
        self.Doc.recompute()
        self.assertIsNone(self.Sketch.addExternal(source.Name, "Edge1"))
        self.Doc.recompute()
        before = self.signature(self.Sketch)

        diagnostic = self.Sketch.diagnoseConvertToNURBS([-3, internal])
        self.assertEqual(diagnostic["converted_geometry_indices"], [1, internal])
        self.assertEqual(diagnostic["exposed_internal_geometry_count"], 4)
        self.assertEqual(diagnostic["geometry_count"], 6)
        self.assertEqual(diagnostic["external_geometry_count"], 1)
        self.assertEqual(self.signature(self.Sketch), before)

        self.Sketch.convertToNURBSExact([-3, internal])
        self.assertEqual(
            [self.Sketch.Geometry[index].TypeId for index in (internal, 1)],
            ["Part::GeomBSplineCurve", "Part::GeomBSplineCurve"],
        )
        self.assertEqual(self.Sketch.GeometryCount, 6)
        self.assertEqual(len(self.Sketch.ExternalGeo), 3)

    def test_noncoincident_constraints_are_removed_but_endpoint_coincident_survives(
        self,
    ):
        first = self.line((0, 0, 0), (8, 0, 0))
        second = self.line((8, 0, 0), (12, 3, 0))
        coincident = self.Sketch.addConstraint(
            Sketcher.Constraint("Coincident", first, 2, second, 1)
        )
        coincident_tag = str(self.Sketch.Constraints[coincident].Tag)
        distance = self.Sketch.addConstraint(
            Sketcher.Constraint("Distance", first, 8.0)
        )
        self.Sketch.setExpression(f"Constraints[{distance}]", "8 mm")
        self.Doc.recompute()

        diagnostic = self.Sketch.diagnoseConvertToNURBS([first])
        self.assertEqual(diagnostic["constraints"][0].Type, "Coincident")
        self.assertEqual(str(diagnostic["constraints"][0].Tag), coincident_tag)
        self.Sketch.convertToNURBSExact([first])
        self.assertEqual(self.Sketch.Constraints[0].Type, "Coincident")
        self.assertEqual(str(self.Sketch.Constraints[0].Tag), coincident_tag)
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), ())

    def test_circle_center_coincident_is_removed(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 5), False
        )
        point = self.Sketch.addGeometry(Part.Point(App.Vector(0, 0)), False)
        self.Sketch.addConstraint(
            Sketcher.Constraint("Coincident", circle, 3, point, 1)
        )
        self.Doc.recompute()

        diagnostic = self.Sketch.diagnoseConvertToNURBS([circle])
        self.assertNotIn(
            "Coincident", [item.Type for item in diagnostic["constraints"]]
        )
        self.Sketch.convertToNURBSExact([circle])
        self.assertEqual(self.Sketch.Geometry[circle].TypeId, "Part::GeomBSplineCurve")
        self.assertNotIn("Coincident", [item.Type for item in self.Sketch.Constraints])

    def test_invalid_mixed_selection_is_rejected_before_any_mutation(self):
        line = self.line()
        point = self.Sketch.addGeometry(Part.Point(App.Vector(2, 3)), False)
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        for targets in (
            [],
            [line, line],
            [line, point],
            [-1],
            [-2],
            [-3],
            [999],
        ):
            with self.subTest(targets=targets):
                with self.assertRaises((TypeError, ValueError)):
                    self.Sketch.diagnoseConvertToNURBS(targets)
                with self.assertRaises((TypeError, ValueError)):
                    self.Sketch.convertToNURBSExact(targets)
                self.assertEqual(self.signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseConvertToNURBS([True])

    def test_existing_single_geometry_api_remains_available(self):
        line = self.line()
        self.assertIsNone(self.Sketch.convertToNURBS(line))
        self.assertEqual(self.Sketch.Geometry[line].TypeId, "Part::GeomBSplineCurve")
        self.assertEqual(
            (self.Sketch.GeometryCount, self.Sketch.ConstraintCount), (1, 0)
        )

    def test_exact_commit_is_one_undoable_and_redoable_change(self):
        line = self.line()
        self.Doc.recompute()
        before = self.signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Geometry to B-Spline")
        self.Sketch.convertToNURBSExact([line])
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)
        self.Doc.undo()
        self.assertEqual(self.signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.signature(self.Sketch), after)
