# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached-host coverage for the exact human Sketch Extend diagnostic."""

import math
import unittest

import FreeCAD
import Part


App = FreeCAD


class TestSketchExtend(unittest.TestCase):
    def setUp(self):
        self.Doc = FreeCAD.newDocument("TestSketchExtend")

    def _line(self, name, *, construction=False):
        sketch = self.Doc.addObject("Sketcher::SketchObject", name)
        self.assertEqual(
            sketch.addGeometry(
                Part.LineSegment(App.Vector(0, 0), App.Vector(10, 0)),
                construction,
            ),
            0,
        )
        self.Doc.recompute()
        return sketch

    def _arc(self, name):
        sketch = self.Doc.addObject("Sketcher::SketchObject", name)
        circle = Part.Circle(App.Vector(0, 0), App.Vector(0, 0, 1), 10)
        self.assertEqual(
            sketch.addGeometry(Part.ArcOfCircle(circle, 0.0, 1.0), False), 0
        )
        self.Doc.recompute()
        return sketch

    def testDetachedLineDiagnosisExtendsAndShortensExactEndpoints(self):
        cases = (
            ("ExtendStart", App.Vector(-5, 4), 1, 5.0, -5.0, 10.0),
            ("ShortenEnd", App.Vector(6, -3), 2, -4.0, 0.0, 6.0),
        )
        for name, point, endpoint, increment, start_x, end_x in cases:
            with self.subTest(name=name):
                sketch = self._line(name, construction=True)
                before_id = sketch.getGeometryId(0)

                diagnostic = sketch.diagnoseExtend(0, point, endpoint)

                self.assertTrue(diagnostic["accepted"])
                self.assertEqual(
                    diagnostic["input_endpoint"],
                    "start" if endpoint == 1 else "end",
                )
                self.assertAlmostEqual(diagnostic["extension_increment"], increment)
                result = diagnostic["geometry"][0]
                self.assertAlmostEqual(result.StartPoint.x, start_x)
                self.assertAlmostEqual(result.StartPoint.y, 0.0)
                self.assertAlmostEqual(result.EndPoint.x, end_x)
                self.assertAlmostEqual(result.EndPoint.y, 0.0)
                self.assertTrue(diagnostic["geometry_metadata"][0]["Construction"])
                self.assertEqual(
                    diagnostic["mutation_receipt"]["geometry"]["old_to_new"],
                    {"0": 0},
                )
                self.assertEqual(
                    diagnostic["mutation_receipt"]["geometry"]["deleted"], []
                )
                self.assertEqual(
                    diagnostic["mutation_receipt"]["geometry"]["created"], []
                )
                self.assertEqual(sketch.getGeometryId(0), before_id)
                self.assertAlmostEqual(sketch.Geometry[0].StartPoint.x, 0.0)
                self.assertAlmostEqual(sketch.Geometry[0].EndPoint.x, 10.0)

    def testDetachedLineDiagnosisRejectsHumanEndpointSwitch(self):
        sketch = self._line("EndpointSwitch")

        with self.assertRaisesRegex(ValueError, "human Extend target"):
            sketch.diagnoseExtend(0, App.Vector(15, 2), 1)

        accepted = sketch.diagnoseExtend(0, App.Vector(15, 2), 2)
        self.assertEqual(accepted["input_endpoint"], "end")
        self.assertAlmostEqual(accepted["extension_increment"], 5.0)
        self.assertAlmostEqual(accepted["geometry"][0].EndPoint.x, 15.0)
        self.assertAlmostEqual(sketch.Geometry[0].EndPoint.x, 10.0)

    def testDetachedCircularArcDiagnosisCoversBothEndpointsAndDirections(self):
        cases = (
            ("ExtendStart", 1, -0.5, 0.5, 1.5),
            ("ShortenStart", 1, 0.25, -0.25, 0.75),
            ("ExtendEnd", 2, 1.5, 0.5, 1.5),
            ("ShortenEnd", 2, 0.75, -0.25, 0.75),
        )
        for name, endpoint, angle, increment, span in cases:
            with self.subTest(name=name):
                sketch = self._arc(name)
                point = App.Vector(10 * math.cos(angle), 10 * math.sin(angle))

                diagnostic = sketch.diagnoseExtend(0, point, endpoint)

                result = diagnostic["geometry"][0]
                self.assertEqual(result.TypeId, "Part::GeomArcOfCircle")
                self.assertAlmostEqual(diagnostic["extension_increment"], increment)
                self.assertAlmostEqual(
                    result.LastParameter - result.FirstParameter, span
                )
                moved = result.StartPoint if endpoint == 1 else result.EndPoint
                self.assertAlmostEqual(moved.x, point.x)
                self.assertAlmostEqual(moved.y, point.y)
                fixed = result.EndPoint if endpoint == 1 else result.StartPoint
                fixed_angle = 1.0 if endpoint == 1 else 0.0
                self.assertAlmostEqual(fixed.x, 10 * math.cos(fixed_angle))
                self.assertAlmostEqual(fixed.y, 10 * math.sin(fixed_angle))
                self.assertAlmostEqual(sketch.Geometry[0].FirstParameter, 0.0)
                self.assertAlmostEqual(sketch.Geometry[0].LastParameter, 1.0)

    def testCircularArcExtendParticipatesInDocumentUndoRedo(self):
        sketch = self._arc("UndoableArc")
        self.Doc.UndoMode = 1
        self.Doc.clearUndos()

        self.Doc.openTransaction("Extend circular arc")
        sketch.extend(0, -0.25, 2)
        self.Doc.recompute()
        self.Doc.commitTransaction()

        self.assertAlmostEqual(sketch.Geometry[0].FirstParameter, 0.0)
        self.assertAlmostEqual(sketch.Geometry[0].LastParameter, 0.75)
        self.assertEqual(self.Doc.UndoCount, 1)

        self.Doc.undo()
        self.assertAlmostEqual(sketch.Geometry[0].FirstParameter, 0.0)
        self.assertAlmostEqual(sketch.Geometry[0].LastParameter, 1.0)

        self.Doc.redo()
        self.assertAlmostEqual(sketch.Geometry[0].FirstParameter, 0.0)
        self.assertAlmostEqual(sketch.Geometry[0].LastParameter, 0.75)

    def testDiagnosisRejectsNoopCenterAndUnsupportedGeometry(self):
        line = self._line("NoopLine")
        arc = self._arc("CenterArc")
        point = self.Doc.addObject("Sketcher::SketchObject", "PointSketch")
        point.addGeometry(Part.Point(App.Vector(3, 4)), False)
        self.Doc.recompute()

        for sketch, target, endpoint in (
            (line, App.Vector(0, 0), 1),
            (arc, App.Vector(0, 0), 1),
            (point, App.Vector(5, 4), 1),
        ):
            with self.subTest(sketch=sketch.Name):
                with self.assertRaisesRegex(ValueError, "human Extend target"):
                    sketch.diagnoseExtend(0, target, endpoint)
        self.assertAlmostEqual(line.Geometry[0].StartPoint.x, 0.0)
        self.assertAlmostEqual(arc.Geometry[0].FirstParameter, 0.0)
        self.assertEqual(point.GeometryCount, 1)

    def tearDown(self):
        FreeCAD.closeDocument(self.Doc.Name)
