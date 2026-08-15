# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, pure Sketch Carbon Copy diagnosis and execution."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchCarbonCopy(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchCarbonCopy")
        self.Source = self.Doc.addObject("Sketcher::SketchObject", "Source")
        self.Target = self.Doc.addObject("Sketcher::SketchObject", "Target")

    @staticmethod
    def geometrySignature(sketch):
        values = []
        for index, geometry in enumerate(sketch.Geometry):
            item = [geometry.TypeId, sketch.getConstruction(index)]
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
                point = getattr(geometry, name, None)
                if point is not None:
                    item.append(
                        (
                            name,
                            round(point.x, 9),
                            round(point.y, 9),
                            round(point.z, 9),
                        )
                    )
            values.append(tuple(item))
        return tuple(values)

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
            )
            for constraint in sketch.Constraints
        )

    @staticmethod
    def externalSignature(sketch):
        values = []
        for obj, rawNames in sketch.ExternalGeometry:
            names = (rawNames,) if isinstance(rawNames, str) else tuple(rawNames)
            values.extend((obj.Name, name) for name in names)
        return tuple(values)

    @classmethod
    def sketchSignature(cls, sketch):
        return (
            cls.geometrySignature(sketch),
            cls.constraintSignature(sketch),
            cls.externalSignature(sketch),
            tuple(sketch.ExternalTypes),
            tuple(sketch.ExpressionEngine),
            sketch.GeometryCount,
            sketch.ConstraintCount,
        )

    @staticmethod
    def diagnosticGeometrySignature(diagnostic):
        values = []
        for geometry, metadata in zip(
            diagnostic["geometry"],
            diagnostic["geometry_metadata"],
            strict=True,
        ):
            item = [geometry.TypeId, metadata["Construction"]]
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
                point = getattr(geometry, name, None)
                if point is not None:
                    item.append(
                        (
                            name,
                            round(point.x, 9),
                            round(point.y, 9),
                            round(point.z, 9),
                        )
                    )
            values.append(tuple(item))
        return tuple(values)

    @staticmethod
    def createdIndices(receipt, collection):
        return tuple(
            sorted(item["index"] for item in receipt[collection]["created"])
        )

    def addSourceLineAndDistance(self):
        geometry = self.Source.addGeometry(
            Part.LineSegment(App.Vector(1, 2, 0), App.Vector(11, 2, 0)),
            False,
        )
        constraint = self.Source.addConstraint(
            Sketcher.Constraint("Distance", geometry, 10.0)
        )
        return geometry, constraint

    def assertDiagnosticMatchesCommit(
        self,
        *,
        construction,
        allowOtherBody=False,
        allowUnaligned=False,
    ):
        sourceBefore = self.sketchSignature(self.Source)
        targetBefore = self.sketchSignature(self.Target)
        diagnostic = self.Target.diagnoseCarbonCopy(
            self.Source.Name,
            construction,
            allowOtherBody,
            allowUnaligned,
        )
        self.assertEqual(self.sketchSignature(self.Source), sourceBefore)
        self.assertEqual(self.sketchSignature(self.Target), targetBefore)
        expectedGeometry = self.diagnosticGeometrySignature(diagnostic)
        expectedConstraints = tuple(
            (value.Type, value.First, value.Second, value.Third)
            for value in diagnostic["constraints"]
        )

        receipt = self.Target.carbonCopyExact(
            self.Source.Name,
            construction,
            allowOtherBody,
            allowUnaligned,
        )
        self.Doc.recompute()

        self.assertEqual(self.sketchSignature(self.Source), sourceBefore)
        self.assertEqual(self.geometrySignature(self.Target), expectedGeometry)
        self.assertEqual(
            tuple(
                (value.Type, value.First, value.Second, value.Third)
                for value in self.Target.Constraints
            ),
            expectedConstraints,
        )
        self.assertEqual(
            self.createdIndices(receipt, "geometry"),
            tuple(range(len(targetBefore[0]), self.Target.GeometryCount)),
        )
        self.assertEqual(
            self.createdIndices(receipt, "constraints"),
            tuple(range(len(targetBefore[1]), self.Target.ConstraintCount)),
        )
        return diagnostic, receipt

    def testDiagnosisIsPureAndMatchesConstructionCommitAndExpressions(self):
        self.Target.addGeometry(
            Part.LineSegment(App.Vector(-2, -2, 0), App.Vector(0, -2, 0)),
            False,
        )
        self.Target.addConstraint(Sketcher.Constraint("Horizontal", 0))
        self.addSourceLineAndDistance()

        diagnostic, _receipt = self.assertDiagnosticMatchesCommit(construction=True)

        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["source_object_name"], self.Source.Name)
        self.assertTrue(diagnostic["requested_construction"])
        self.assertFalse(diagnostic["requested_allow_other_body"])
        self.assertFalse(diagnostic["requested_allow_unaligned"])
        self.assertEqual(diagnostic["copied_geometry_count"], 1)
        self.assertEqual(diagnostic["copied_constraint_count"], 1)
        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(diagnostic["constraint_count"], 2)
        self.assertTrue(diagnostic["geometry_metadata"][1]["Construction"])
        self.assertEqual(
            diagnostic["expressions"],
            [
                {
                    "constraint_index": 1,
                    "path": "Constraints[1]",
                    "expression": "Source.Constraints[0]",
                }
            ],
        )
        self.assertEqual(
            tuple(self.Target.ExpressionEngine),
            (("Constraints[1]", "Source.Constraints[0]"),),
        )

    def testRegularModePreservesSourceConstructionState(self):
        first = self.Source.addGeometry(
            Part.LineSegment(App.Vector(0, 0, 0), App.Vector(5, 0, 0)),
            False,
        )
        second = self.Source.addGeometry(
            Part.LineSegment(App.Vector(0, 1, 0), App.Vector(5, 1, 0)),
            True,
        )
        self.assertEqual((first, second), (0, 1))

        diagnostic, _receipt = self.assertDiagnosticMatchesCommit(construction=False)

        self.assertEqual(
            [item["Construction"] for item in diagnostic["geometry_metadata"]],
            [False, True],
        )
        self.assertEqual(
            [self.Target.getConstruction(index) for index in range(2)],
            [False, True],
        )

    def testExternalLinksAndGeometryArePureAndMatchCommit(self):
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.addSourceLineAndDistance()
        self.Source.addExternal(box.Name, "Edge1", False, False)
        self.Doc.recompute()

        diagnostic, _receipt = self.assertDiagnosticMatchesCommit(construction=True)

        self.assertEqual(diagnostic["copied_external_reference_count"], 1)
        self.assertEqual(diagnostic["external_reference_count"], 1)
        self.assertEqual(
            diagnostic["external_references"],
            [{"object_name": box.Name, "subelement": "Edge1", "type": 0}],
        )
        self.assertEqual(
            len(diagnostic["external_geometry"]),
            diagnostic["external_geometry_count"],
        )
        self.assertEqual(self.externalSignature(self.Target), ((box.Name, "Edge1"),))

    def testExplicitUnalignedPermissionAndFlipCorrections(self):
        self.addSourceLineAndDistance()
        self.Source.Placement = App.Placement(
            App.Vector(),
            App.Rotation(App.Vector(0, 1, 0), 180),
        )
        self.Doc.recompute()

        diagnostic = self.Target.diagnoseCarbonCopy(
            self.Source.Name,
            True,
            False,
            False,
        )
        self.assertTrue(diagnostic["x_inverted"])
        self.assertFalse(diagnostic["y_inverted"])
        self.assertAlmostEqual(diagnostic["geometry"][0].StartPoint.x, -1.0)
        self.assertAlmostEqual(diagnostic["geometry"][0].EndPoint.x, -11.0)

        self.Source.Placement = App.Placement(
            App.Vector(),
            App.Rotation(App.Vector(0, 0, 1), 45),
        )
        self.Doc.recompute()
        with self.assertRaises(ValueError):
            self.Target.diagnoseCarbonCopy(
                self.Source.Name,
                True,
                True,
                False,
            )
        unaligned = self.Target.diagnoseCarbonCopy(
            self.Source.Name,
            True,
            True,
            True,
        )
        self.assertFalse(unaligned["x_inverted"])
        self.assertFalse(unaligned["y_inverted"])
        self.assertTrue(unaligned["requested_allow_other_body"])
        self.assertTrue(unaligned["requested_allow_unaligned"])

    def testExplicitCrossBodyPermission(self):
        self.Doc.removeObject(self.Source.Name)
        self.Doc.removeObject(self.Target.Name)
        firstBody = self.Doc.addObject("PartDesign::Body", "FirstBody")
        secondBody = self.Doc.addObject("PartDesign::Body", "SecondBody")
        self.Source = self.Doc.addObject("Sketcher::SketchObject", "Source")
        self.Target = self.Doc.addObject("Sketcher::SketchObject", "Target")
        firstBody.addObject(self.Source)
        secondBody.addObject(self.Target)
        self.addSourceLineAndDistance()
        self.Doc.recompute()

        with self.assertRaises(ValueError):
            self.Target.diagnoseCarbonCopy(
                self.Source.Name,
                True,
                False,
                False,
            )
        diagnostic, _receipt = self.assertDiagnosticMatchesCommit(
            construction=True,
            allowOtherBody=True,
            allowUnaligned=False,
        )
        self.assertTrue(diagnostic["requested_allow_other_body"])

    def testInvalidDuplicateAndCircularSourcesDoNotMutate(self):
        self.addSourceLineAndDistance()
        box = self.Doc.addObject("PartDesign::Feature", "Box")
        box.Shape = Part.makeBox(5, 5, 5)
        self.Source.addExternal(box.Name, "Edge1", False, False)
        self.Target.addExternal(box.Name, "Edge1", False, False)
        self.Doc.recompute()
        sourceBefore = self.sketchSignature(self.Source)
        targetBefore = self.sketchSignature(self.Target)

        with self.assertRaises(ValueError):
            self.Target.diagnoseCarbonCopy("Missing", True, False, False)
        with self.assertRaises(ValueError):
            self.Target.diagnoseCarbonCopy(
                self.Target.Name,
                True,
                False,
                False,
            )
        with self.assertRaises(ValueError):
            self.Target.diagnoseCarbonCopy(
                self.Source.Name,
                True,
                False,
                False,
            )
        self.assertEqual(self.sketchSignature(self.Source), sourceBefore)
        self.assertEqual(self.sketchSignature(self.Target), targetBefore)

    def testCommitIsOneUndoableAndRedoableDocumentChange(self):
        self.addSourceLineAndDistance()
        self.Doc.recompute()
        self.Doc.UndoMode = 1
        self.Doc.openTransaction("Carbon Copy host test")
        self.Target.carbonCopyExact(self.Source.Name, True, False, False)
        self.Doc.recompute()
        self.Doc.commitTransaction()
        committed = self.sketchSignature(self.Target)
        self.assertEqual((self.Target.GeometryCount, self.Target.ConstraintCount), (1, 1))

        self.Doc.undo()
        self.assertEqual((self.Target.GeometryCount, self.Target.ConstraintCount), (0, 0))
        self.Doc.redo()
        self.assertEqual(self.sketchSignature(self.Target), committed)

    def tearDown(self):
        App.closeDocument(self.Doc.Name)
