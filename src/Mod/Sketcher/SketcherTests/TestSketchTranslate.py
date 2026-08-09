# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the exact, pure ribbon Translate / rectangular-array path."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchTranslate(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchTranslate")
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

    def addExpressedLine(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(4, 1, 0)), False
        )
        constraint = self.Sketch.addConstraint(
            Sketcher.Constraint("DistanceX", line, 1, line, 2, 4.0)
        )
        self.Sketch.setExpression(f"Constraints[{constraint}]", "4 mm")
        self.Doc.recompute()
        return line

    def testMoveDiagnosisIsPureAndMatchesCommitWithExpression(self):
        line = self.addExpressedLine()
        before = self.sketchSignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseTranslate(
            [line], App.Vector(5, 2, 0), 0, App.Vector(), 1, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertTrue(diagnostic["accepted"])
        self.assertTrue(diagnostic["deleted_originals"])
        self.assertEqual(diagnostic["input_geometry_indices"], [line])
        self.assertEqual(diagnostic["first_vector_mm"], {"x": 5.0, "y": 2.0})
        self.assertEqual(diagnostic["geometry_count"], 1)
        self.assertEqual(diagnostic["constraint_count"], 1)
        self.assertEqual(
            diagnostic["expressions"],
            [{"constraint_index": 0, "path": "Constraints[0]", "expression": "4 mm"}],
        )

        expected_geometry = self.diagnosticGeometrySignature(diagnostic)
        expected_constraints = tuple(
            (item.Type, item.First, item.Second, item.Third)
            for item in diagnostic["constraints"]
        )
        receipt = self.Sketch.translateExact(
            [line], App.Vector(5, 2, 0), 0, App.Vector(), 1, False
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
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), (("Constraints[0]", "4 mm"),))
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)
        self.assertEqual(len(receipt["geometry"]["created"]), 1)
        self.assertEqual(len(receipt["constraints"]["deleted"]), 1)
        self.assertEqual(len(receipt["constraints"]["created"]), 1)

    def testLinearAndArbitrarySecondVectorArray(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(2, 0, 0)), False
        )
        diagnostic = self.Sketch.diagnoseTranslate(
            [line], App.Vector(10, 0, 0), 2, App.Vector(3, 7, 0), 2, False
        )
        self.assertEqual(diagnostic["geometry_count"], 6)
        self.assertFalse(diagnostic["deleted_originals"])
        self.Sketch.translateExact(
            [line], App.Vector(10, 0, 0), 2, App.Vector(3, 7, 0), 2, False
        )
        self.Doc.recompute()
        self.assertEqual(
            [(item.StartPoint.x, item.StartPoint.y) for item in self.Sketch.Geometry],
            [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (3.0, 7.0), (13.0, 7.0), (23.0, 7.0)],
        )

    def testEqualizeDimensionalConstraintsUsesOriginalGeometry(self):
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(), App.Vector(0, 0, 1), 3.0), False
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Radius", circle, 3.0))
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseTranslate(
            [circle], App.Vector(8, 0, 0), 2, App.Vector(0, 6, 0), 2, True
        )
        self.assertEqual(diagnostic["geometry_count"], 6)
        self.assertEqual(diagnostic["constraint_count"], 6)
        self.assertEqual(
            [item.Type for item in diagnostic["constraints"]],
            ["Radius", "Equal", "Equal", "Equal", "Equal", "Equal"],
        )
        self.Sketch.translateExact(
            [circle], App.Vector(8, 0, 0), 2, App.Vector(0, 6, 0), 2, True
        )
        self.Doc.recompute()
        self.assertEqual([item.Type for item in self.Sketch.Constraints][1:], ["Equal"] * 5)
        self.assertTrue(all(abs(item.Radius - 3.0) < 1e-9 for item in self.Sketch.Geometry))

    def testConstructionAndSupportedCurvesPreserveTheirGeometryState(self):
        point = self.Sketch.addGeometry(Part.Point(App.Vector(1, 2, 0)), True)
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(4, 5, 0), App.Vector(0, 0, 1), 2.0), False
        )
        diagnostic = self.Sketch.diagnoseTranslate(
            [point, circle], App.Vector(-3, 4, 0), 1, App.Vector(), 1, False
        )
        self.assertEqual(
            [item["Construction"] for item in diagnostic["geometry_metadata"]],
            [True, False, True, False],
        )
        self.Sketch.translateExact(
            [point, circle], App.Vector(-3, 4, 0), 1, App.Vector(), 1, False
        )
        self.assertEqual(
            [self.Sketch.getConstruction(index) for index in range(4)],
            [True, False, True, False],
        )

    def testExternalGeometryCopyAndMoveMatchDiagnosticState(self):
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.Sketch.addExternal(box.Name, "Edge1", False, False)
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        copy_diagnostic = self.Sketch.diagnoseTranslate(
            [-3], App.Vector(3, 0, 0), 1, App.Vector(), 1, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertEqual(copy_diagnostic["external_reference_count"], 1)
        self.assertEqual(copy_diagnostic["geometry_count"], 1)
        self.Sketch.translateExact(
            [-3], App.Vector(3, 0, 0), 1, App.Vector(), 1, False
        )
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 1)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)

        move_diagnostic = self.Sketch.diagnoseTranslate(
            [-3], App.Vector(0, 4, 0), 0, App.Vector(), 1, False
        )
        self.assertEqual(move_diagnostic["external_reference_count"], 0)
        self.assertEqual(move_diagnostic["external_geometry_count"], 0)
        self.Sketch.translateExact(
            [-3], App.Vector(0, 4, 0), 0, App.Vector(), 1, False
        )
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 2)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 0)

    def testInvalidInputsAndIncompleteInternalGeometryArePure(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(), App.Vector(4, 0, 0)), False
        )
        ellipse = self.Sketch.addGeometry(
            Part.Ellipse(App.Vector(), 4.0, 2.0), False
        )
        self.Sketch.exposeInternalGeometry(ellipse)
        self.Doc.recompute()
        internal = next(
            constraint.First
            for constraint in self.Sketch.Constraints
            if constraint.Type == "InternalAlignment" and constraint.Second == ellipse
        )
        before = self.sketchSignature(self.Sketch)
        invalid = (
            ([line, line], App.Vector(1, 0, 0), 1, App.Vector(), 1, False),
            ([line], App.Vector(), 1, App.Vector(), 1, False),
            ([line], App.Vector(1, 0, 0), 1, App.Vector(), 2, False),
            ([-1], App.Vector(1, 0, 0), 1, App.Vector(), 1, False),
            ([internal], App.Vector(1, 0, 0), 1, App.Vector(), 1, False),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseTranslate(*arguments)
                self.assertEqual(self.sketchSignature(self.Sketch), before)

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        line = self.addExpressedLine()
        self.Doc.UndoMode = 1
        before = self.sketchSignature(self.Sketch)
        self.Doc.openTransaction("Translate host test")
        self.Sketch.translateExact(
            [line], App.Vector(6, 3, 0), 0, App.Vector(), 1, False
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
