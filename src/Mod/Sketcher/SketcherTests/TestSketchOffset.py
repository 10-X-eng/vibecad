# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the exact, pure ribbon Offset path."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchOffset(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchOffset")
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    @staticmethod
    def geometrySignature(sketch):
        result = []
        for index, geometry in enumerate(sketch.Geometry):
            points = []
            for name in ("StartPoint", "EndPoint", "Center"):
                point = getattr(geometry, name, None)
                if point is not None:
                    points.append(
                        (name, round(point.x, 8), round(point.y, 8), round(point.z, 8))
                    )
            result.append(
                (
                    geometry.TypeId,
                    sketch.getConstruction(index),
                    tuple(points),
                    round(float(getattr(geometry, "Radius", 0.0)), 8),
                )
            )
        return tuple(result)

    @staticmethod
    def constraintsSignature(constraints):
        return tuple(
            (
                constraint.Type,
                constraint.First,
                constraint.FirstPos,
                constraint.Second,
                constraint.SecondPos,
                round(constraint.Value, 8),
                constraint.Driving,
                constraint.IsActive,
                constraint.InVirtualSpace,
                round(constraint.LabelDistance, 8),
                round(constraint.LabelPosition, 8),
                constraint.Name,
            )
            for constraint in constraints
        )

    @classmethod
    def constraintSignature(cls, sketch):
        return cls.constraintsSignature(sketch.Constraints)

    @classmethod
    def sketchSignature(cls, sketch):
        return (
            cls.geometrySignature(sketch),
            cls.constraintSignature(sketch),
            tuple(sketch.ExpressionEngine),
            tuple(sketch.ExternalGeometry),
            tuple(sketch.ExternalTypes),
            sketch.GeometryCount,
            sketch.ConstraintCount,
        )

    @staticmethod
    def diagnosticGeometrySignature(diagnostic):
        result = []
        for geometry, metadata in zip(
            diagnostic["geometry"], diagnostic["geometry_metadata"], strict=True
        ):
            points = []
            for name in ("StartPoint", "EndPoint", "Center"):
                point = getattr(geometry, name, None)
                if point is not None:
                    points.append(
                        (name, round(point.x, 8), round(point.y, 8), round(point.z, 8))
                    )
            result.append(
                (
                    geometry.TypeId,
                    metadata["Construction"],
                    tuple(points),
                    round(float(getattr(geometry, "Radius", 0.0)), 8),
                )
            )
        return tuple(result)

    def addSquare(self, size=10.0):
        points = (
            App.Vector(0, 0),
            App.Vector(size, 0),
            App.Vector(size, size),
            App.Vector(0, size),
        )
        return [
            self.Sketch.addGeometry(
                Part.LineSegment(points[index], points[(index + 1) % 4]), False
            )
            for index in range(4)
        ]

    def testSingleLineDiagnosisIsPureAndCommitMatchesExactly(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)), False
        )
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseOffset([line], 2.0, 0, 0)
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["input_geometry_indices"], [line])
        self.assertEqual(diagnostic["offset_length_mm"], 2.0)
        self.assertEqual(diagnostic["join_type"], "arc")
        self.assertEqual(diagnostic["source_mode"], "keep")
        self.assertFalse(diagnostic["deleted_originals"])
        self.assertFalse(diagnostic["constrained_offset"])
        self.assertGreater(diagnostic["geometry_count"], 1)
        expected = self.diagnosticGeometrySignature(diagnostic)

        receipt = self.Sketch.offsetExact([line], 2.0, 0, 0)
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch), expected)
        expected_receipt = diagnostic["mutation_receipt"]
        for collection in ("geometry", "constraints"):
            self.assertEqual(
                receipt[collection]["old_to_new"],
                expected_receipt[collection]["old_to_new"],
            )
            for state in ("created", "deleted"):
                self.assertEqual(
                    [item["index"] for item in receipt[collection][state]],
                    [item["index"] for item in expected_receipt[collection][state]],
                )

    def testSignedCircleOffsetProducesOuterAndInnerRadii(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(3, 4), App.Vector(0, 0, 1), 10.0), False
        )
        self.Doc.recompute()
        outer = self.Sketch.diagnoseOffset([circle], 2.0, 0, 0)
        inner = self.Sketch.diagnoseOffset([circle], -2.0, 0, 0)
        self.assertEqual(outer["geometry_count"], 2)
        self.assertEqual(inner["geometry_count"], 2)
        self.assertAlmostEqual(outer["geometry"][1].Radius, 12.0)
        self.assertAlmostEqual(inner["geometry"][1].Radius, 8.0)
        self.assertEqual(self.Sketch.GeometryCount, 1)

    def testArcAndIntersectionJoinsUseTheHumanModes(self):
        square = self.addSquare()
        self.Doc.recompute()
        arc = self.Sketch.diagnoseOffset(square, 2.0, 0, 0)
        intersection = self.Sketch.diagnoseOffset(square, 2.0, 2, 0)
        self.assertEqual(arc["join_type"], "arc")
        self.assertEqual(intersection["join_type"], "intersection")
        self.assertGreater(arc["geometry_count"], intersection["geometry_count"])
        self.assertEqual(intersection["geometry_count"], 8)
        self.assertEqual(self.Sketch.GeometryCount, 4)

    def testDeleteModeRemovesSourcesAndTheirConstraints(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(), App.Vector(0, 0, 1), 5.0), False
        )
        radius = self.Sketch.addConstraint(Sketcher.Constraint("Radius", circle, 5.0))
        self.Sketch.setExpression(f"Constraints[{radius}]", "5 mm")
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseOffset([circle], 1.0, 0, 1)
        self.assertTrue(diagnostic["deleted_originals"])
        self.assertEqual(diagnostic["source_mode"], "delete")
        self.assertEqual(diagnostic["geometry_count"], 1)
        self.assertEqual(diagnostic["constraint_count"], 0)
        self.assertEqual(diagnostic["expressions"], [])
        receipt = self.Sketch.offsetExact([circle], 1.0, 0, 1)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 1)
        self.assertAlmostEqual(self.Sketch.Geometry[0].Radius, 6.0)
        self.assertEqual(self.Sketch.ConstraintCount, 0)
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), ())
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)

    def testConstrainModeBuildsEditableCircleOffset(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(2, 3), App.Vector(0, 0, 1), 5.0), False
        )
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseOffset([circle], 1.5, 0, 2)
        self.assertTrue(diagnostic["constrained_offset"])
        self.assertEqual(diagnostic["source_mode"], "constrain")
        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(
            [constraint.Type for constraint in diagnostic["constraints"]],
            ["Coincident", "Distance"],
        )
        self.assertAlmostEqual(diagnostic["constraints"][1].Value, 1.5)
        expected_constraints = self.constraintsSignature(diagnostic["constraints"])
        self.Sketch.offsetExact([circle], 1.5, 0, 2)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 2)
        self.assertEqual(self.Sketch.ConstraintCount, 2)
        self.assertEqual(self.constraintSignature(self.Sketch), expected_constraints)

    def testConstrainModeBuildsOneSharedDistanceForPolygon(self):
        square = self.addSquare()
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseOffset(square, 2.0, 2, 2)
        construction = [
            metadata["Construction"] for metadata in diagnostic["geometry_metadata"]
        ]
        self.assertTrue(diagnostic["accepted"])
        self.assertTrue(diagnostic["constrained_offset"])
        self.assertGreater(sum(construction), 1)
        dimensional = [
            constraint
            for constraint in diagnostic["constraints"]
            if constraint.Type == "Distance"
        ]
        self.assertEqual(len(dimensional), 1)
        self.assertAlmostEqual(dimensional[0].Value, 2.0)

    def testConstrainDiagnosisMatchesAfterExistingConstraints(self):
        square = self.addSquare()
        for index, geometry in enumerate(square):
            self.Sketch.addConstraint(
                Sketcher.Constraint(
                    "Coincident", geometry, 2, square[(index + 1) % 4], 1
                )
            )
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(50, 5), App.Vector(0, 0, 1), 5.0), False
        )
        self.Doc.recompute()

        diagnostic = self.Sketch.diagnoseOffset([circle], 1.5, 0, 2)
        expected_constraints = self.constraintsSignature(diagnostic["constraints"])
        self.Sketch.offsetExact([circle], 1.5, 0, 2)
        self.Doc.recompute()

        self.assertEqual(self.constraintSignature(self.Sketch), expected_constraints)

    def testExternalGeometryCanBeKeptOrDeleted(self):
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.Sketch.addExternal(box.Name, "Edge9", False, False)
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        kept = self.Sketch.diagnoseOffset([-3], 1.0, 0, 0)
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertEqual(kept["external_reference_count"], 1)
        self.assertGreater(kept["geometry_count"], 0)
        deleted = self.Sketch.diagnoseOffset([-3], 1.0, 0, 1)
        self.assertEqual(deleted["external_reference_count"], 0)
        self.assertEqual(deleted["external_geometry_count"], 0)

    def testInvalidTargetsModesAndDistancesArePure(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(), App.Vector(4, 0)), False
        )
        point = self.Sketch.addGeometry(Part.Point(App.Vector(1, 1)), False)
        ellipse = self.Sketch.addGeometry(Part.Ellipse(App.Vector(), 4.0, 2.0), False)
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        invalid = (
            ([line, line], 1.0, 0, 0),
            ([line], 0.0, 0, 0),
            ([line], float("inf"), 0, 0),
            ([line], 1.0, 1, 0),
            ([line], 1.0, 0, 3),
            ([point], 1.0, 0, 0),
            ([ellipse], 1.0, 0, 0),
            ([-1], 1.0, 0, 0),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseOffset(*arguments)
                self.assertEqual(self.sketchSignature(self.Sketch), before)
        for arguments in (([line], 1.0, True, 0), ([line], 1.0, 0, False)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    self.Sketch.diagnoseOffset(*arguments)

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        square = self.addSquare()
        self.Doc.recompute()
        self.Doc.UndoMode = 1
        before = self.sketchSignature(self.Sketch)
        self.Doc.openTransaction("Offset host test")
        self.Sketch.offsetExact(square, 2.0, 2, 0)
        self.Doc.recompute()
        self.Doc.commitTransaction()
        committed = self.sketchSignature(self.Sketch)
        self.assertNotEqual(committed, before)
        self.Doc.undo()
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.sketchSignature(self.Sketch), committed)

    def tearDown(self):
        App.closeDocument(self.Doc.Name)
