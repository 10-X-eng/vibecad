# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for pure Sketch external-geometry Intersection diagnosis."""

import hashlib
import unittest

import FreeCAD
import Part


App = FreeCAD


class TestSketchIntersection(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchIntersection")
        self.Source = self.Doc.addObject("Part::Feature", "IntersectionSource")
        self.Source.Shape = Part.makeLine(
            App.Vector(1, 2, -2),
            App.Vector(5, 2, 2),
        )
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")
        self.Doc.recompute()

    @staticmethod
    def geometrySignature(geometry):
        result = [geometry.TypeId]
        for name in ("StartPoint", "EndPoint", "Center", "Location"):
            point = getattr(geometry, name, None)
            if point is not None:
                result.append(
                    (name, round(point.x, 9), round(point.y, 9), round(point.z, 9))
                )
        for name in ("Radius", "MajorRadius", "MinorRadius"):
            if hasattr(geometry, name):
                result.append((name, round(float(getattr(geometry, name)), 9)))
        return tuple(result)

    @staticmethod
    def sourceDigest(source):
        return hashlib.sha256(source.Shape.exportBrepToString().encode()).hexdigest()

    def assertDiagnosticMatchesCommitted(self, source, subelement, defining):
        diagnostic = self.Sketch.diagnoseExternal(
            source.Name,
            subelement,
            defining,
            True,
        )
        expected = [
            self.geometrySignature(item) for item in diagnostic["external_geometry"]
        ]
        beforeSource = self.sourceDigest(source)
        beforeLinks = len(self.Sketch.ExternalGeometry)
        beforeExternal = len(self.Sketch.ExternalGeo)

        self.assertEqual(len(self.Sketch.ExternalGeometry), beforeLinks)
        self.assertEqual(len(self.Sketch.ExternalGeo), beforeExternal)
        self.assertEqual(self.sourceDigest(source), beforeSource)
        self.Sketch.addExternal(source.Name, subelement, defining, True)
        self.Doc.recompute()

        self.assertEqual(
            [self.geometrySignature(item) for item in self.Sketch.ExternalGeo[2:]],
            expected,
        )
        self.assertEqual(self.sourceDigest(source), beforeSource)
        return diagnostic

    def testEdgeDiagnosisIsPureAndMatchesCommittedIntersection(self):
        diagnostic = self.assertDiagnosticMatchesCommitted(
            self.Source,
            "Edge1",
            True,
        )
        self.assertEqual(diagnostic["source_object_name"], self.Source.Name)
        self.assertEqual(diagnostic["source_subelement"], "Edge1")
        self.assertTrue(diagnostic["requested_defining"])
        self.assertTrue(diagnostic["requested_intersection"])
        self.assertTrue(diagnostic["added_reference"])
        self.assertEqual(diagnostic["type"], 1)
        self.assertEqual(diagnostic["reference_index"], 0)
        self.assertTrue(diagnostic["defining"])
        self.assertEqual(diagnostic["external_geometry_count"], 1)
        point = diagnostic["external_geometry"][0]
        self.assertEqual(point.TypeId, "Part::GeomPoint")
        self.assertAlmostEqual(point.X, 3.0)
        self.assertAlmostEqual(point.Y, 2.0)
        self.assertEqual(list(self.Sketch.ExternalTypes), [1])
        extension = self.Sketch.ExternalGeo[2].getExtensionOfType(
            "Sketcher::ExternalGeometryExtension"
        )
        self.assertEqual(extension.Ref, diagnostic["reference"])
        self.assertTrue(extension.testFlag("Defining"))

    def testReferenceFaceDiagnosisHasStrictMetadataAndMatchesCommit(self):
        source = self.Doc.addObject("Part::Feature", "FaceSource")
        source.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(-5, -5, -5),
        )
        self.Doc.recompute()
        diagnostic = self.assertDiagnosticMatchesCommitted(source, "Face1", False)
        self.assertEqual(diagnostic["external_geometry_count"], 1)
        self.assertEqual(
            diagnostic["external_geometry_metadata"],
            [
                {
                    "reference": diagnostic["reference"],
                    "defining": False,
                    "frozen": False,
                    "detached": False,
                    "missing": False,
                    "synchronized": False,
                }
            ],
        )
        self.assertEqual(
            diagnostic["external_geometry"][0].TypeId,
            "Part::GeomLineSegment",
        )

    def testProjectionUpgradePreservesLinkAndRoleAndMatchesCommit(self):
        self.Sketch.addExternal(self.Source.Name, "Edge1", False, False)
        self.Doc.recompute()
        self.assertEqual(list(self.Sketch.ExternalTypes), [0])
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        beforeSource = self.sourceDigest(self.Source)

        diagnostic = self.Sketch.diagnoseExternal(
            self.Source.Name,
            "Edge1",
            False,
            True,
        )
        expected = [
            self.geometrySignature(item) for item in diagnostic["external_geometry"]
        ]
        self.assertFalse(diagnostic["added_reference"])
        self.assertEqual(diagnostic["reference_index"], 0)
        self.assertEqual(diagnostic["type"], 2)
        self.assertFalse(diagnostic["defining"])
        self.assertEqual(list(self.Sketch.ExternalTypes), [0])
        self.assertEqual(self.sourceDigest(self.Source), beforeSource)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(
                self.Source.Name,
                "Edge1",
                True,
                True,
            )

        self.Sketch.addExternal(self.Source.Name, "Edge1", False, True)
        self.Doc.recompute()
        self.assertEqual(list(self.Sketch.ExternalTypes), [2])
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        self.assertEqual(
            [self.geometrySignature(item) for item in self.Sketch.ExternalGeo[2:]],
            expected,
        )
        self.assertEqual(self.sourceDigest(self.Source), beforeSource)

    def testDuplicateAndNonIntersectingSourcesAreRejectedWithoutMutation(self):
        self.Sketch.addExternal(self.Source.Name, "Edge1", False, True)
        self.Doc.recompute()
        beforeLinks = list(self.Sketch.ExternalGeometry)
        beforeGeometry = [
            self.geometrySignature(item) for item in self.Sketch.ExternalGeo
        ]
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(
                self.Source.Name,
                "Edge1",
                False,
                True,
            )

        remote = self.Doc.addObject("Part::Feature", "RemoteSource")
        remote.Shape = Part.makeLine(
            App.Vector(0, 20, 5),
            App.Vector(10, 20, 5),
        )
        self.Doc.recompute()
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(remote.Name, "Edge1", False, True)
        self.assertEqual(list(self.Sketch.ExternalGeometry), beforeLinks)
        self.assertEqual(
            [self.geometrySignature(item) for item in self.Sketch.ExternalGeo],
            beforeGeometry,
        )

    def testCommittedIntersectionIsOneUndoableAndRedoableDocumentChange(self):
        self.Doc.UndoMode = 1
        self.Doc.openTransaction("Intersection host test")
        self.Sketch.addExternal(self.Source.Name, "Edge1", False, True)
        self.Doc.recompute()
        self.Doc.commitTransaction()
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        self.assertEqual(len(self.Sketch.ExternalGeo), 3)

        self.Doc.undo()
        self.assertEqual(len(self.Sketch.ExternalGeometry), 0)
        self.assertEqual(len(self.Sketch.ExternalGeo), 2)
        self.Doc.redo()
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        self.assertEqual(len(self.Sketch.ExternalGeo), 3)

    def tearDown(self):
        App.closeDocument(self.Doc.Name)
