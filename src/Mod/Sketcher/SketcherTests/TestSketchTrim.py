# SPDX-License-Identifier: LGPL-2.1-or-later

"""Detached-host coverage for the exact human Sketch Trim diagnostic."""

import unittest

import FreeCAD
import Part


App = FreeCAD


class TestSketchTrim(unittest.TestCase):
    def setUp(self):
        self.Doc = FreeCAD.newDocument("TestSketchTrim")

    def _sketch(self, name, cutter_x_values=()):
        sketch = self.Doc.addObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.LineSegment(App.Vector(0, 0), App.Vector(20, 0)),
            False,
        )
        for x_value in cutter_x_values:
            sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(x_value, -5),
                    App.Vector(x_value, 5),
                ),
                False,
            )
        self.Doc.recompute()
        return sketch

    def _assert_live_unchanged(self, sketch, expected_geometry_count):
        self.assertEqual(sketch.GeometryCount, expected_geometry_count)
        self.assertEqual(sketch.ConstraintCount, 0)
        self.assertAlmostEqual(sketch.Geometry[0].StartPoint.x, 0.0)
        self.assertAlmostEqual(sketch.Geometry[0].StartPoint.y, 0.0)
        self.assertAlmostEqual(sketch.Geometry[0].EndPoint.x, 20.0)
        self.assertAlmostEqual(sketch.Geometry[0].EndPoint.y, 0.0)

    def testDetachedDeleteDiagnosis(self):
        sketch = self._sketch("DeleteDiagnosis")

        diagnostic = sketch.diagnoseTrim(0, App.Vector(10, 0))

        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["input_geometry_index"], 0)
        self.assertEqual(diagnostic["reference_point_mm"], [10.0, 0.0])
        self.assertEqual(diagnostic["external_geometry_count"], 0)
        self.assertEqual(diagnostic["geometry_count"], 0)
        self.assertEqual(diagnostic["constraint_count"], 0)
        receipt = diagnostic["mutation_receipt"]
        self.assertEqual(receipt["geometry"]["old_to_new"], {})
        self.assertEqual(len(receipt["geometry"]["deleted"]), 1)
        self.assertEqual(receipt["geometry"]["deleted"][0]["index"], 0)
        self.assertEqual(receipt["geometry"]["created"], [])
        self._assert_live_unchanged(sketch, 1)

    def testDetachedShortenDiagnosis(self):
        sketch = self._sketch("ShortenDiagnosis", (5.0,))

        diagnostic = sketch.diagnoseTrim(0, App.Vector(10, 0))

        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["geometry_count"], 2)
        self.assertEqual(diagnostic["constraint_count"], 1)
        self.assertAlmostEqual(diagnostic["geometry"][0].StartPoint.x, 0.0)
        self.assertAlmostEqual(diagnostic["geometry"][0].EndPoint.x, 5.0)
        receipt = diagnostic["mutation_receipt"]
        self.assertEqual(receipt["geometry"]["old_to_new"], {"1": 1})
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["deleted"]],
            [0],
        )
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["created"]],
            [0],
        )
        self.assertNotEqual(
            receipt["geometry"]["deleted"][0]["tag"],
            receipt["geometry"]["created"][0]["tag"],
        )
        self._assert_live_unchanged(sketch, 2)

    def testDetachedSplitDiagnosis(self):
        sketch = self._sketch("SplitDiagnosis", (5.0, 15.0))

        diagnostic = sketch.diagnoseTrim(0, App.Vector(10, 0))

        self.assertTrue(diagnostic["accepted"])
        self.assertEqual(diagnostic["geometry_count"], 4)
        self.assertEqual(diagnostic["constraint_count"], 2)
        self.assertAlmostEqual(diagnostic["geometry"][0].StartPoint.x, 0.0)
        self.assertAlmostEqual(diagnostic["geometry"][0].EndPoint.x, 5.0)
        self.assertAlmostEqual(diagnostic["geometry"][3].StartPoint.x, 15.0)
        self.assertAlmostEqual(diagnostic["geometry"][3].EndPoint.x, 20.0)
        self.assertAlmostEqual(diagnostic["geometry"][0].FirstParameter, 0.0)
        self.assertAlmostEqual(diagnostic["geometry"][0].LastParameter, 5.0)
        self.assertAlmostEqual(diagnostic["geometry"][3].FirstParameter, 0.0)
        self.assertAlmostEqual(diagnostic["geometry"][3].LastParameter, 5.0)
        receipt = diagnostic["mutation_receipt"]
        self.assertEqual(receipt["geometry"]["old_to_new"], {"1": 1, "2": 2})
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["deleted"]],
            [0],
        )
        self.assertEqual(
            [item["index"] for item in receipt["geometry"]["created"]],
            [0, 3],
        )
        self.assertEqual(receipt["constraints"]["old_to_new"], {})
        self.assertEqual(
            [item["index"] for item in receipt["constraints"]["created"]],
            [0, 1],
        )
        self._assert_live_unchanged(sketch, 3)

    def tearDown(self):
        FreeCAD.closeDocument(self.Doc.Name)
