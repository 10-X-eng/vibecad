# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the exact, pure ribbon Rotate / polar-transform path."""

import math
import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchRotate(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchRotate")
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    @staticmethod
    def geometrySignature(sketch):
        result = []
        for index, geometry in enumerate(sketch.Geometry):
            points = []
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
                point = getattr(geometry, name, None)
                if point is not None:
                    points.append(
                        (name, round(point.x, 9), round(point.y, 9), round(point.z, 9))
                    )
            result.append(
                (geometry.TypeId, sketch.getConstruction(index), tuple(points))
            )
        return tuple(result)

    @staticmethod
    def constraintSignature(sketch):
        return tuple(
            (
                constraint.Type,
                constraint.First,
                constraint.FirstPos,
                constraint.Second,
                constraint.SecondPos,
                constraint.Third,
                constraint.ThirdPos,
                constraint.Driving,
                constraint.IsActive,
                constraint.InVirtualSpace,
            )
            for constraint in sketch.Constraints
        )

    @staticmethod
    def externalSignature(sketch):
        values = []
        for obj, raw_names in sketch.ExternalGeometry:
            names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names)
            values.extend((obj.Name, name) for name in names)
        return tuple(values), tuple(sketch.ExternalTypes)

    @classmethod
    def sketchSignature(cls, sketch):
        return (
            cls.geometrySignature(sketch),
            cls.constraintSignature(sketch),
            cls.externalSignature(sketch),
            tuple(sketch.ExpressionEngine),
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
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
                point = getattr(geometry, name, None)
                if point is not None:
                    points.append(
                        (name, round(point.x, 9), round(point.y, 9), round(point.z, 9))
                    )
            result.append((geometry.TypeId, metadata["Construction"], tuple(points)))
        return tuple(result)

    def addExpressedCircle(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(4, 1, 0), App.Vector(0, 0, 1), 3.0), False
        )
        constraint = self.Sketch.addConstraint(
            Sketcher.Constraint("Radius", circle, 3.0)
        )
        self.Sketch.setExpression(f"Constraints[{constraint}]", "3 mm")
        self.Doc.recompute()
        return circle

    def testMoveDiagnosisIsPureAndMatchesCommitWithExpression(self):
        circle = self.addExpressedCircle()
        before = self.sketchSignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseRotate(
            [circle], App.Vector(1, 1, 0), math.pi / 2.0, 0, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertTrue(diagnostic["accepted"])
        self.assertTrue(diagnostic["deleted_originals"])
        self.assertEqual(diagnostic["input_geometry_indices"], [circle])
        self.assertEqual(diagnostic["center_mm"], {"x": 1.0, "y": 1.0})
        self.assertAlmostEqual(diagnostic["total_angle_radians"], math.pi / 2.0)
        self.assertEqual(diagnostic["geometry_count"], 1)
        self.assertEqual(diagnostic["constraint_count"], 1)
        self.assertEqual(
            diagnostic["expressions"],
            [{"constraint_index": 0, "path": "Constraints[0]", "expression": "3 mm"}],
        )

        expected_geometry = self.diagnosticGeometrySignature(diagnostic)
        expected_constraints = tuple(
            (item.Type, item.First, item.Second, item.Third)
            for item in diagnostic["constraints"]
        )
        receipt = self.Sketch.rotateExact(
            [circle], App.Vector(1, 1, 0), math.pi / 2.0, 0, False
        )
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch), expected_geometry)
        self.assertEqual(
            tuple(
                (item.Type, item.First, item.Second, item.Third)
                for item in self.Sketch.Constraints
            ),
            expected_constraints,
        )
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), (("Constraints[0]", "3 mm"),))
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)
        self.assertEqual(len(receipt["geometry"]["created"]), 1)
        self.assertEqual(len(receipt["constraints"]["deleted"]), 1)
        self.assertEqual(len(receipt["constraints"]["created"]), 1)

    def testCopiesAreDistributedAcrossTheTotalAngle(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 0, 0), App.Vector(4, 0, 0)), False
        )
        diagnostic = self.Sketch.diagnoseRotate(
            [line], App.Vector(), math.pi / 2.0, 3, False
        )
        self.assertEqual(diagnostic["geometry_count"], 4)
        self.assertFalse(diagnostic["deleted_originals"])
        self.Sketch.rotateExact([line], App.Vector(), math.pi / 2.0, 3, False)
        self.Doc.recompute()
        expected = (
            (2.0, 0.0),
            (math.sqrt(3.0), 1.0),
            (1.0, math.sqrt(3.0)),
            (0.0, 2.0),
        )
        for geometry, point in zip(self.Sketch.Geometry, expected, strict=True):
            self.assertAlmostEqual(geometry.StartPoint.x, point[0], places=9)
            self.assertAlmostEqual(geometry.StartPoint.y, point[1], places=9)

    def testEqualizeDimensionalConstraintsUsesOriginalGeometry(self):
        circle = self.addExpressedCircle()
        diagnostic = self.Sketch.diagnoseRotate(
            [circle], App.Vector(), math.pi, 3, True
        )
        self.assertEqual(diagnostic["geometry_count"], 4)
        self.assertEqual(diagnostic["constraint_count"], 4)
        self.assertEqual(
            [item.Type for item in diagnostic["constraints"]],
            ["Radius", "Equal", "Equal", "Equal"],
        )
        self.assertEqual(diagnostic["expressions"], [])
        self.Sketch.rotateExact([circle], App.Vector(), math.pi, 3, True)
        self.Doc.recompute()
        self.assertEqual([item.Type for item in self.Sketch.Constraints][1:], ["Equal"] * 3)
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), (("Constraints[0]", "3 mm"),))

    def testAxisDependentConstraintsAreNotCopiedByRotation(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(1, 0, 0), App.Vector(5, 0, 0)), False
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        self.Sketch.addConstraint(
            Sketcher.Constraint("DistanceX", line, 1, line, 2, 4.0)
        )
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseRotate(
            [line], App.Vector(), math.pi / 2.0, 1, False
        )
        self.assertEqual([item.Type for item in diagnostic["constraints"]], ["Horizontal", "DistanceX"])
        self.assertEqual(diagnostic["geometry_count"], 2)

    def testExternalGeometryCopyAndMoveMatchDiagnosticState(self):
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.Sketch.addExternal(box.Name, "Edge1", False, False)
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        copy_diagnostic = self.Sketch.diagnoseRotate(
            [-3], App.Vector(), math.pi / 4.0, 1, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertEqual(copy_diagnostic["external_reference_count"], 1)
        self.assertEqual(copy_diagnostic["geometry_count"], 1)
        self.Sketch.rotateExact([-3], App.Vector(), math.pi / 4.0, 1, False)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 1)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)

        move_diagnostic = self.Sketch.diagnoseRotate(
            [-3], App.Vector(), math.pi / 3.0, 0, False
        )
        self.assertEqual(move_diagnostic["external_reference_count"], 0)
        self.assertEqual(move_diagnostic["external_geometry_count"], 0)
        self.Sketch.rotateExact([-3], App.Vector(), math.pi / 3.0, 0, False)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 2)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 0)

    def testInvalidInputsAndIncompleteInternalGeometryArePure(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(), App.Vector(4, 0, 0)), False
        )
        ellipse = self.Sketch.addGeometry(Part.Ellipse(App.Vector(), 4.0, 2.0), False)
        self.Sketch.exposeInternalGeometry(ellipse)
        self.Doc.recompute()
        internal = next(
            constraint.First
            for constraint in self.Sketch.Constraints
            if constraint.Type == "InternalAlignment" and constraint.Second == ellipse
        )
        before = self.sketchSignature(self.Sketch)
        invalid = (
            ([line, line], App.Vector(), 1.0, 1, False),
            ([line], App.Vector(), 0.0, 1, False),
            ([line], App.Vector(0, 0, 1), 1.0, 1, False),
            ([line], App.Vector(), float("inf"), 1, False),
            ([-1], App.Vector(), 1.0, 1, False),
            ([internal], App.Vector(), 1.0, 1, False),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseRotate(*arguments)
                self.assertEqual(self.sketchSignature(self.Sketch), before)

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        circle = self.addExpressedCircle()
        self.Doc.UndoMode = 1
        before = self.sketchSignature(self.Sketch)
        self.Doc.openTransaction("Rotate host test")
        self.Sketch.rotateExact(
            [circle], App.Vector(1, 1, 0), math.pi / 2.0, 0, False
        )
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
