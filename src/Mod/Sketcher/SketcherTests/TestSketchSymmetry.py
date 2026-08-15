# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the exact, pure ribbon Symmetry path."""

import math
import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchSymmetry(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchSymmetry")
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
    def constraintSignature(constraints):
        return tuple(
            (
                constraint.Type,
                constraint.First,
                constraint.FirstPos,
                constraint.Second,
                constraint.SecondPos,
                constraint.Third,
                constraint.ThirdPos,
                round(constraint.Value, 8),
                constraint.Driving,
                constraint.IsActive,
                constraint.InVirtualSpace,
                constraint.Name,
            )
            for constraint in constraints
        )

    @classmethod
    def sketchSignature(cls, sketch):
        return (
            cls.geometrySignature(sketch),
            cls.constraintSignature(sketch.Constraints),
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
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
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

    def testLineAxisDiagnosisIsPureAndCommitMatchesExactly(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 1), App.Vector(5, 3)), True
        )
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseSymmetry([line], -1, 0, 0)
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["input_geometry_indices"], [line])
        self.assertEqual(diagnostic["reference_geometry_index"], -1)
        self.assertEqual(diagnostic["reference_position"], "whole")
        self.assertEqual(diagnostic["source_mode"], "keep")
        self.assertFalse(diagnostic["deleted_originals"])
        self.assertFalse(diagnostic["constrained_symmetry"])
        expected_geometry = self.diagnosticGeometrySignature(diagnostic)
        expected_constraints = self.constraintSignature(diagnostic["constraints"])

        receipt = self.Sketch.symmetryExact([line], -1, 0, 0)
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch), expected_geometry)
        self.assertEqual(
            self.constraintSignature(self.Sketch.Constraints), expected_constraints
        )
        self.assertTrue(self.Sketch.getConstruction(1))
        self.assertAlmostEqual(self.Sketch.Geometry[1].StartPoint.y, -1.0)
        self.assertEqual(len(receipt["geometry"]["created"]), 1)

    def testPointSymmetrySupportsRootAndExactGeometryPoint(self):
        source = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 1), App.Vector(5, 3)), False
        )
        pivot = self.Sketch.addGeometry(Part.Point(App.Vector(10, 10)), False)
        self.Doc.recompute()
        root = self.Sketch.diagnoseSymmetry([source], -1, 1, 0)
        around_point = self.Sketch.diagnoseSymmetry([source], pivot, 1, 0)
        self.assertEqual(root["reference_position"], "start")
        self.assertAlmostEqual(root["geometry"][2].StartPoint.x, -2.0)
        self.assertAlmostEqual(root["geometry"][2].StartPoint.y, -1.0)
        self.assertAlmostEqual(around_point["geometry"][2].StartPoint.x, 18.0)
        self.assertAlmostEqual(around_point["geometry"][2].StartPoint.y, 19.0)
        self.assertEqual(self.Sketch.GeometryCount, 2)

    def testDeleteModeReplacesSourcesAndTheirCopiedConstraints(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 4), App.Vector(7, 4)), False
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseSymmetry([line], -2, 0, 1)
        self.assertTrue(diagnostic["deleted_originals"])
        self.assertEqual(diagnostic["source_mode"], "delete")
        self.assertEqual(diagnostic["geometry_count"], 1)
        self.assertEqual(diagnostic["constraint_count"], 1)
        expected_geometry = self.diagnosticGeometrySignature(diagnostic)
        expected_constraints = self.constraintSignature(diagnostic["constraints"])

        receipt = self.Sketch.symmetryExact([line], -2, 0, 1)
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch), expected_geometry)
        self.assertEqual(
            self.constraintSignature(self.Sketch.Constraints), expected_constraints
        )
        self.assertAlmostEqual(self.Sketch.Geometry[0].StartPoint.x, -2.0)
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)
        self.assertEqual(len(receipt["geometry"]["created"]), 1)

    def testConstrainModeCreatesEditableHumanSymmetryRelations(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 2), App.Vector(6, 4)), False
        )
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseSymmetry([line], -2, 0, 2)
        self.assertTrue(diagnostic["constrained_symmetry"])
        self.assertEqual(diagnostic["source_mode"], "constrain")
        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(
            [constraint.Type for constraint in diagnostic["constraints"]],
            ["Symmetric", "Symmetric"],
        )
        expected_constraints = self.constraintSignature(diagnostic["constraints"])
        self.Sketch.symmetryExact([line], -2, 0, 2)
        self.Doc.recompute()
        self.assertEqual(
            self.constraintSignature(self.Sketch.Constraints), expected_constraints
        )

    def testEveryHumanCurveFamilyCanBeMirrored(self):
        ellipse = Part.Ellipse(App.Vector(30, 0), 8, 3)
        hyperbola = Part.Hyperbola(App.Vector(50, 0), 6, 2)
        parabola = Part.Parabola(
            App.Vector(70, 0), App.Vector(67, 0), App.Vector(0, 0, 1)
        )
        spline = Part.BSplineCurve(
            [App.Vector(90, -5), App.Vector(98, 8), App.Vector(108, -2)],
            [3, 3],
            [0.0, 1.0],
            False,
            2,
            [1.0, 1.8, 1.2],
            False,
        )
        geometries = (
            Part.Circle(App.Vector(10, 2), App.Vector(0, 0, 1), 3),
            Part.ArcOfCircle(
                Part.Circle(App.Vector(20, 0), App.Vector(0, 0, 1), 6),
                -0.8,
                1.8,
            ),
            ellipse,
            Part.ArcOfEllipse(ellipse, -0.5, 2.1),
            Part.ArcOfHyperbola(hyperbola, -0.7, 0.8),
            Part.ArcOfParabola(parabola, -4, 5),
            spline,
            Part.Point(App.Vector(120, 4)),
        )
        indices = [self.Sketch.addGeometry(geometry, False) for geometry in geometries]
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseSymmetry(indices, -1, 0, 0)
        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["geometry_count"], 2 * len(indices))
        self.assertEqual(
            [geometry.TypeId for geometry in diagnostic["geometry"][: len(indices)]],
            [geometry.TypeId for geometry in diagnostic["geometry"][len(indices) :]],
        )
        self.assertEqual(self.Sketch.GeometryCount, len(indices))

    def testExternalGeometryCanBeSourceAndReference(self):
        source = self.Doc.addObject("PartDesign::Feature", "Source")
        source.Shape = Part.makeBox(5, 5, 5)
        self.Sketch.addExternal(source.Name, "Edge1", False, False)
        self.Sketch.addExternal(source.Name, "Edge2", False, False)
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(8, 2), App.Vector(11, 3)), False
        )
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        external_source = self.Sketch.diagnoseSymmetry([-3], -1, 0, 0)
        external_reference = self.Sketch.diagnoseSymmetry([line], -4, 0, 0)
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertEqual(external_source["external_reference_count"], 2)
        self.assertEqual(external_source["geometry_count"], 2)
        self.assertEqual(external_reference["reference_geometry_index"], -4)

    def testInvalidTargetsReferencesAndEnumsArePure(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(1, 1), App.Vector(4, 2)), False
        )
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(5, 5), App.Vector(0, 0, 1), 2), False
        )
        ellipse = self.Sketch.addGeometry(Part.Ellipse(App.Vector(), 4, 2), False)
        self.Sketch.exposeInternalGeometry(ellipse)
        self.Doc.recompute()
        internal = next(
            constraint.First
            for constraint in self.Sketch.Constraints
            if constraint.Type == "InternalAlignment" and constraint.Second == ellipse
        )
        before = self.sketchSignature(self.Sketch)
        invalid = (
            ([line, line], -1, 0, 0),
            ([-1], -2, 0, 0),
            ([line], circle, 0, 0),
            ([line], circle, 1, 0),
            ([line], -2, 1, 0),
            ([line], -2000, 0, 0),
            ([line], -1, 0, 3),
            ([internal], -1, 0, 0),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseSymmetry(*arguments)
                self.assertEqual(self.sketchSignature(self.Sketch), before)
        for arguments in (([line], True, 0, 0), ([line], -1, False, 0)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    self.Sketch.diagnoseSymmetry(*arguments)

    def testArcOrientationIsExactForLineAndPointReferences(self):
        arc = self.Sketch.addGeometry(
            Part.ArcOfCircle(
                Part.Circle(App.Vector(4, 3), App.Vector(0, 0, 1), 2),
                0.2,
                1.4,
            ),
            False,
        )
        self.Doc.recompute()
        line = self.Sketch.diagnoseSymmetry([arc], -1, 0, 0)["geometry"][1]
        point = self.Sketch.diagnoseSymmetry([arc], -1, 1, 0)["geometry"][1]
        self.assertAlmostEqual(line.StartPoint.x, 4 + 2 * math.cos(1.4))
        self.assertAlmostEqual(line.StartPoint.y, -(3 + 2 * math.sin(1.4)))
        self.assertAlmostEqual(point.StartPoint.x, -(4 + 2 * math.cos(0.2)))
        self.assertAlmostEqual(point.StartPoint.y, -(3 + 2 * math.sin(0.2)))

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 2), App.Vector(6, 4)), False
        )
        self.Doc.recompute()
        self.Doc.UndoMode = 1
        before = self.sketchSignature(self.Sketch)
        self.Doc.openTransaction("Symmetry host test")
        self.Sketch.symmetryExact([line], -2, 0, 2)
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
