# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached-host coverage for the exact human Sketch Split diagnostic."""

import math
import unittest

import FreeCAD
import Part


App = FreeCAD


class TestSketchSplit(unittest.TestCase):
    def setUp(self):
        self.Doc = FreeCAD.newDocument("TestSketchSplit")

    def _diagnose(self, name, geometry, point, *, construction=False):
        sketch = self.Doc.addObject("Sketcher::SketchObject", name)
        self.assertEqual(sketch.addGeometry(geometry, construction), 0)
        self.Doc.recompute()
        before_type = sketch.Geometry[0].TypeId
        before_count = sketch.GeometryCount

        diagnostic = sketch.diagnoseSplit(0, point)

        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["input_geometry_index"], 0)
        self.assertEqual(diagnostic["reference_point_mm"], [point.x, point.y])
        self.assertEqual(diagnostic["external_geometry_count"], 0)
        self.assertEqual(sketch.GeometryCount, before_count)
        self.assertEqual(sketch.Geometry[0].TypeId, before_type)
        return sketch, diagnostic

    def testDetachedLineDiagnosisCreatesTwoConnectedPieces(self):
        sketch, diagnostic = self._diagnose(
            "LineDiagnosis",
            Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
            App.Vector(8, 0),
            construction=True,
        )

        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(diagnostic["constraint_count"], 1)
        first, second = diagnostic["geometry"]
        self.assertEqual(first.TypeId, "Part::GeomLineSegment")
        self.assertEqual(second.TypeId, "Part::GeomLineSegment")
        self.assertAlmostEqual(first.StartPoint.x, 0.0)
        self.assertAlmostEqual(first.EndPoint.x, 8.0)
        self.assertAlmostEqual(second.StartPoint.x, 8.0)
        self.assertAlmostEqual(second.EndPoint.x, 20.0)
        self.assertAlmostEqual(first.FirstParameter, 0.0)
        self.assertAlmostEqual(first.LastParameter, 8.0)
        self.assertAlmostEqual(second.FirstParameter, 0.0)
        self.assertAlmostEqual(second.LastParameter, 12.0)
        self.assertTrue(diagnostic["geometry_metadata"][0]["Construction"])
        self.assertTrue(diagnostic["geometry_metadata"][1]["Construction"])
        receipt = diagnostic["mutation_receipt"]
        self.assertEqual(receipt["geometry"]["old_to_new"], {})
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["deleted"]],
            [0],
        )
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["created"]],
            [0, 1],
        )
        self.assertEqual(
            [item["index"] for item in receipt["constraints"]["created"]],
            [0],
        )
        self.assertEqual(sketch.ConstraintCount, 0)

    def testDetachedClosedConicDiagnosisCreatesOneOpenArc(self):
        cases = (
            (
                "CircleDiagnosis",
                Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 10),
                App.Vector(10, 0),
                "Part::GeomArcOfCircle",
            ),
            (
                "EllipseDiagnosis",
                Part.Ellipse(App.Vector(30, 0), 8, 3),
                App.Vector(38, 0),
                "Part::GeomArcOfEllipse",
            ),
        )
        for name, geometry, point, expected_type in cases:
            with self.subTest(name=name):
                _sketch, diagnostic = self._diagnose(name, geometry, point)
                self.assertEqual(diagnostic["geometry_count"], 1)
                self.assertEqual(diagnostic["constraint_count"], 0)
                replacement = diagnostic["geometry"][0]
                self.assertEqual(replacement.TypeId, expected_type)
                self.assertGreater(
                    replacement.LastParameter - replacement.FirstParameter,
                    0.0,
                )
                self.assertEqual(
                    [
                        item["index"]
                        for item in diagnostic["mutation_receipt"]["geometry"][
                            "created"
                        ]
                    ],
                    [0],
                )

    def testDetachedOpenCurveDiagnosisCoversEveryHumanCurveKind(self):
        ellipse = Part.Ellipse(App.Vector(30, 0), 8, 3)
        hyperbola = Part.Hyperbola(App.Vector(50, 0), 6, 2)
        parabola = Part.Parabola(
            App.Vector(70, 0),
            App.Vector(67, 0),
            App.Vector(0, 0, 1),
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
        cases = (
            (
                "CircularArcDiagnosis",
                Part.ArcOfCircle(
                    Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 6),
                    -0.8,
                    1.8,
                ),
                App.Vector(6 * math.cos(0.5), 6 * math.sin(0.5)),
                "Part::GeomArcOfCircle",
            ),
            (
                "EllipticalArcDiagnosis",
                Part.ArcOfEllipse(ellipse, -0.5, 2.1),
                ellipse.value(0.8),
                "Part::GeomArcOfEllipse",
            ),
            (
                "HyperbolicArcDiagnosis",
                Part.ArcOfHyperbola(hyperbola, -0.7, 0.8),
                hyperbola.value(0.0),
                "Part::GeomArcOfHyperbola",
            ),
            (
                "ParabolicArcDiagnosis",
                Part.ArcOfParabola(parabola, -4, 5),
                parabola.value(0.0),
                "Part::GeomArcOfParabola",
            ),
            (
                "BSplineDiagnosis",
                spline,
                spline.value(0.5),
                "Part::GeomBSplineCurve",
            ),
        )
        for name, geometry, point, expected_type in cases:
            with self.subTest(name=name):
                _sketch, diagnostic = self._diagnose(name, geometry, point)
                self.assertEqual(diagnostic["geometry_count"], 2)
                self.assertTrue(
                    all(item.TypeId == expected_type for item in diagnostic["geometry"])
                )
                self.assertGreaterEqual(diagnostic["constraint_count"], 1)

    def testDiagnosisRejectsGeometryOutsideTheHumanSelectionGate(self):
        sketch = self.Doc.addObject("Sketcher::SketchObject", "PointDiagnosis")
        sketch.addGeometry(Part.Point(App.Vector(0, 0)), False)
        self.Doc.recompute()

        with self.assertRaisesRegex(ValueError, "human Split target"):
            sketch.diagnoseSplit(0, App.Vector(0, 0))

        self.assertEqual(sketch.GeometryCount, 1)
        self.assertEqual(sketch.ConstraintCount, 0)

    def tearDown(self):
        FreeCAD.closeDocument(self.Doc.Name)
