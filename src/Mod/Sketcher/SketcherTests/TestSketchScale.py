# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the exact, pure ribbon Scale path."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchScale(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchScale")
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
            radii = tuple(
                (name, round(float(getattr(geometry, name)), 9))
                for name in ("Radius", "MajorRadius", "MinorRadius", "Focal")
                if hasattr(geometry, name)
            )
            result.append(
                (
                    geometry.TypeId,
                    sketch.getConstruction(index),
                    tuple(points),
                    radii,
                )
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
                round(constraint.Value, 9),
                round(constraint.LabelDistance, 9),
                round(constraint.LabelPosition, 9),
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
            radii = tuple(
                (name, round(float(getattr(geometry, name)), 9))
                for name in ("Radius", "MajorRadius", "MinorRadius", "Focal")
                if hasattr(geometry, name)
            )
            result.append(
                (geometry.TypeId, metadata["Construction"], tuple(points), radii)
            )
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

    def testReplacementDiagnosisIsPureAndMatchesHumanConstraintSemantics(self):
        circle = self.addExpressedCircle()
        facade_id = self.Sketch.getGeometryId(circle)
        before = self.sketchSignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseScale(
            [circle], App.Vector(1, 1, 0), 2.0, False, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertTrue(diagnostic["accepted"])
        self.assertTrue(diagnostic["deleted_originals"])
        self.assertFalse(diagnostic["keep_originals"])
        self.assertEqual(diagnostic["input_geometry_indices"], [circle])
        self.assertEqual(diagnostic["center_mm"], {"x": 1.0, "y": 1.0})
        self.assertEqual(diagnostic["scale_factor"], 2.0)
        self.assertEqual(diagnostic["geometry_count"], 1)
        self.assertEqual(diagnostic["constraint_count"], 1)
        self.assertEqual(diagnostic["expressions"], [])
        self.assertAlmostEqual(diagnostic["constraints"][0].Value, 6.0)

        expected_geometry = self.diagnosticGeometrySignature(diagnostic)
        receipt = self.Sketch.scaleExact(
            [circle], App.Vector(1, 1, 0), 2.0, False, False
        )
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch), expected_geometry)
        self.assertEqual(self.Sketch.getGeometryId(0), facade_id)
        self.assertAlmostEqual(self.Sketch.Constraints[0].Value, 6.0)
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), ())
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)
        self.assertEqual(len(receipt["geometry"]["created"]), 1)
        self.assertEqual(len(receipt["constraints"]["deleted"]), 1)
        self.assertEqual(len(receipt["constraints"]["created"]), 1)

    def testKeepOriginalsPreservesSourceAndCreatesScaledConstructionCopy(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 3), App.Vector(5, 3)), True
        )
        distance = self.Sketch.addConstraint(Sketcher.Constraint("Distance", line, 3.0))
        self.Sketch.setExpression(f"Constraints[{distance}]", "3 mm")
        self.Doc.recompute()
        before_geometry = self.geometrySignature(self.Sketch)
        diagnostic = self.Sketch.diagnoseScale(
            [line], App.Vector(1, 1), 0.5, True, False
        )
        self.assertFalse(diagnostic["deleted_originals"])
        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(diagnostic["constraint_count"], 2)
        self.assertEqual(diagnostic["expressions"], [])
        self.Sketch.scaleExact([line], App.Vector(1, 1), 0.5, True, False)
        self.Doc.recompute()
        self.assertEqual(self.geometrySignature(self.Sketch)[:1], before_geometry)
        self.assertTrue(self.Sketch.getConstruction(1))
        self.assertAlmostEqual(self.Sketch.Constraints[0].Value, 3.0)
        self.assertAlmostEqual(self.Sketch.Constraints[1].Value, 1.5)
        self.assertEqual(
            tuple(self.Sketch.ExpressionEngine), (("Constraints[0]", "3 mm"),)
        )

    def testDimensionalAndOrientationConstraintsFollowRibbonScaling(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(1, 2), App.Vector(5, 2)), False
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        distance = self.Sketch.addConstraint(
            Sketcher.Constraint("DistanceX", line, 1, line, 2, 4.0)
        )
        self.Sketch.setDatum(distance, App.Units.Quantity("4 mm"))
        self.Sketch.setLabelDistance(distance, 8.0)
        self.Sketch.setLabelPosition(distance, 6.0)
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseScale(
            [line], App.Vector(), 1.5, True, False
        )
        copied = diagnostic["constraints"][2:]
        self.assertEqual([item.Type for item in copied], ["Horizontal", "DistanceX"])
        self.assertAlmostEqual(copied[1].Value, 6.0)
        self.assertAlmostEqual(copied[1].LabelDistance, 12.0)
        self.assertAlmostEqual(copied[1].LabelPosition, 9.0)

    def testSupportedCurveFamiliesUseTheSameUniformScale(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(2, 1), App.Vector(4, 1)), False
        )
        circle = self.Sketch.addGeometry(
            Part.Circle(App.Vector(3, 2), App.Vector(0, 0, 1), 2.0), False
        )
        ellipse = self.Sketch.addGeometry(
            Part.Ellipse(App.Vector(4, 3), 4.0, 2.0), False
        )
        point = self.Sketch.addGeometry(Part.Point(App.Vector(5, 4)), False)
        self.Doc.recompute()
        self.Sketch.scaleExact(
            [line, circle, ellipse, point], App.Vector(1, 1), 0.5, False, False
        )
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 4)
        self.assertAlmostEqual(self.Sketch.Geometry[0].StartPoint.x, 1.5)
        self.assertAlmostEqual(self.Sketch.Geometry[1].Radius, 1.0)
        self.assertAlmostEqual(self.Sketch.Geometry[1].Center.x, 2.0)
        self.assertAlmostEqual(self.Sketch.Geometry[2].MajorRadius, 2.0)
        self.assertAlmostEqual(self.Sketch.Geometry[2].MinorRadius, 1.0)
        self.assertAlmostEqual(self.Sketch.Geometry[3].X, 3.0)
        self.assertAlmostEqual(self.Sketch.Geometry[3].Y, 2.5)

    def testExternalGeometryCopyAndReplacementMatchDiagnosticState(self):
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.Sketch.addExternal(box.Name, "Edge1", False, False)
        self.Doc.recompute()
        before = self.sketchSignature(self.Sketch)
        copy_diagnostic = self.Sketch.diagnoseScale(
            [-3], App.Vector(), 2.0, True, False
        )
        self.assertEqual(self.sketchSignature(self.Sketch), before)
        self.assertEqual(copy_diagnostic["external_reference_count"], 1)
        self.assertEqual(copy_diagnostic["geometry_count"], 1)
        self.Sketch.scaleExact([-3], App.Vector(), 2.0, True, False)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 1)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)

        replace_diagnostic = self.Sketch.diagnoseScale(
            [-3], App.Vector(), 0.5, False, False
        )
        self.assertEqual(replace_diagnostic["external_reference_count"], 0)
        self.assertEqual(replace_diagnostic["external_geometry_count"], 0)
        self.Sketch.scaleExact([-3], App.Vector(), 0.5, False, False)
        self.Doc.recompute()
        self.assertEqual(self.Sketch.GeometryCount, 2)
        self.assertEqual(len(self.Sketch.ExternalGeometry), 0)

    def testWholeSketchOriginModeIsExplicitAndGuarded(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(), App.Vector(4, 0)), False
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Coincident", line, 1, -1, 1))
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        self.Doc.recompute()
        diagnostic = self.Sketch.diagnoseScale(
            [line], App.Vector(), 2.0, False, True
        )
        self.assertTrue(diagnostic["allow_origin_constraints"])
        self.assertEqual(
            [item.Type for item in diagnostic["constraints"]],
            ["Coincident", "Horizontal"],
        )
        for arguments in (
            ([line], App.Vector(1, 0), 2.0, False, True),
            ([line], App.Vector(), 2.0, True, True),
        ):
            with self.assertRaises(ValueError):
                self.Sketch.diagnoseScale(*arguments)

    def testInvalidInputsAndIncompleteInternalGeometryArePure(self):
        line = self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(), App.Vector(4, 0)), False
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
            ([line, line], App.Vector(), 2.0, False, False),
            ([line], App.Vector(), 0.0, False, False),
            ([line], App.Vector(), float("inf"), False, False),
            ([line], App.Vector(0, 0, 1), 2.0, False, False),
            ([-1], App.Vector(), 2.0, False, False),
            ([internal], App.Vector(), 2.0, False, False),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.Sketch.diagnoseScale(*arguments)
                self.assertEqual(self.sketchSignature(self.Sketch), before)

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        circle = self.addExpressedCircle()
        self.Doc.UndoMode = 1
        before = self.sketchSignature(self.Sketch)
        self.Doc.openTransaction("Scale host test")
        self.Sketch.scaleExact(
            [circle], App.Vector(1, 1, 0), 2.0, False, False
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
