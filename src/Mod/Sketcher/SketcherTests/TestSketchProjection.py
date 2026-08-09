# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for the pure Sketch external-geometry Projection diagnostic."""

import hashlib
import unittest

import FreeCAD
import Part


App = FreeCAD


class TestSketchProjection(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchProjection")
        self.Source = self.Doc.addObject("Part::Feature", "ProjectionSource")
        self.Source.Shape = Part.makeLine(
            App.Vector(1, 2, 0),
            App.Vector(5, 2, 0),
        )
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")
        self.Doc.recompute()

    @staticmethod
    def geometrySignature(geometry):
        result = [geometry.TypeId]
        for name in (
            "StartPoint",
            "EndPoint",
            "Center",
            "Location",
        ):
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
            False,
        )
        expected = [
            self.geometrySignature(item) for item in diagnostic["external_geometry"]
        ]
        before_source = self.sourceDigest(source)
        before_links = len(self.Sketch.ExternalGeometry)
        before_external = len(self.Sketch.ExternalGeo)

        self.assertEqual(len(self.Sketch.ExternalGeometry), before_links)
        self.assertEqual(len(self.Sketch.ExternalGeo), before_external)
        self.assertEqual(self.sourceDigest(source), before_source)
        self.Sketch.addExternal(source.Name, subelement, defining, False)
        self.Doc.recompute()

        actual = [
            self.geometrySignature(item)
            for item in self.Sketch.ExternalGeo[before_external:]
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(self.sourceDigest(source), before_source)
        return diagnostic

    def testDiagnosisIsPureAndMatchesCommittedProjection(self):
        diagnostic = self.Sketch.diagnoseExternal(
            self.Source.Name,
            "Edge1",
            True,
            False,
        )

        self.assertEqual(len(self.Sketch.ExternalGeometry), 0)
        self.assertEqual(len(self.Sketch.ExternalGeo), 2)
        self.assertEqual(diagnostic["source_object_name"], self.Source.Name)
        self.assertEqual(diagnostic["source_subelement"], "Edge1")
        self.assertTrue(diagnostic["requested_defining"])
        self.assertFalse(diagnostic["requested_intersection"])
        self.assertTrue(diagnostic["added_reference"])
        self.assertEqual(diagnostic["type"], 0)
        self.assertTrue(diagnostic["defining"])
        self.assertEqual(diagnostic["external_geometry_count"], 1)
        expected = diagnostic["external_geometry"][0]
        self.assertEqual(expected.TypeId, "Part::GeomLineSegment")
        self.assertAlmostEqual(expected.StartPoint.x, 1.0)
        self.assertAlmostEqual(expected.StartPoint.y, 2.0)
        self.assertAlmostEqual(expected.EndPoint.x, 5.0)
        self.assertAlmostEqual(expected.EndPoint.y, 2.0)

        self.Sketch.addExternal(self.Source.Name, "Edge1", True, False)
        self.Doc.recompute()

        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        self.assertEqual(len(self.Sketch.ExternalGeo), 3)
        actual = self.Sketch.ExternalGeo[2]
        self.assertEqual(actual.TypeId, expected.TypeId)
        self.assertAlmostEqual(actual.StartPoint.x, expected.StartPoint.x)
        self.assertAlmostEqual(actual.StartPoint.y, expected.StartPoint.y)
        self.assertAlmostEqual(actual.EndPoint.x, expected.EndPoint.x)
        self.assertAlmostEqual(actual.EndPoint.y, expected.EndPoint.y)
        extension = actual.getExtensionOfType("Sketcher::ExternalGeometryExtension")
        self.assertTrue(extension.testFlag("Defining"))

    def testNewSketchExternalTypePaddingHasNoDurableLink(self):
        self.assertEqual(list(self.Sketch.ExternalGeometry), [])
        self.assertEqual(list(self.Sketch.ExternalTypes), [0])
        self.assertEqual(len(self.Sketch.ExternalGeo), 2)

    def testReferenceEdgeDiagnosticHasStrictMetadataAndMatchesCommit(self):
        diagnostic = self.assertDiagnosticMatchesCommitted(
            self.Source,
            "Edge1",
            False,
        )
        self.assertEqual(
            set(diagnostic),
            {
                "source_object_name",
                "source_subelement",
                "requested_defining",
                "requested_intersection",
                "reference",
                "type",
                "reference_index",
                "added_reference",
                "defining",
                "external_geometry_count",
                "external_geometry",
                "external_geometry_metadata",
            },
        )
        self.assertEqual(diagnostic["reference_index"], 0)
        self.assertFalse(diagnostic["defining"])
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

    def testMappedCompoundReferenceKeyMatchesCommittedProjection(self):
        compound = self.Doc.addObject("Part::Feature", "MappedSource")
        compound.Shape = Part.makeCompound(
            [
                Part.makeLine(App.Vector(-2, 4, 0), App.Vector(2, 4, 0)),
                Part.makeLine(App.Vector(-2, 8, 0), App.Vector(2, 8, 0)),
            ]
        )
        self.Doc.recompute()
        diagnostic = self.assertDiagnosticMatchesCommitted(compound, "Edge2", True)
        extension = self.Sketch.ExternalGeo[2].getExtensionOfType(
            "Sketcher::ExternalGeometryExtension"
        )
        self.assertEqual(extension.Ref, diagnostic["reference"])
        self.assertNotEqual(diagnostic["reference"], "MappedSource.Edge2")

    def testVertexAndFaceDiagnosticsMatchCommittedProjection(self):
        vertexDiagnostic = self.assertDiagnosticMatchesCommitted(
            self.Source,
            "Vertex1",
            False,
        )
        self.assertEqual(vertexDiagnostic["external_geometry_count"], 1)
        self.assertEqual(
            vertexDiagnostic["external_geometry"][0].TypeId,
            "Part::GeomPoint",
        )
        vertexExtension = self.Sketch.ExternalGeo[2].getExtensionOfType(
            "Sketcher::ExternalGeometryExtension"
        )
        self.assertEqual(vertexExtension.Ref, vertexDiagnostic["reference"])
        self.assertFalse(vertexExtension.testFlag("Defining"))

        otherSketch = self.Doc.addObject("Sketcher::SketchObject", "FaceSketch")
        faceSource = self.Doc.addObject("Part::Feature", "FaceSource")
        faceSource.Shape = Part.makePlane(4, 3, App.Vector(10, 0, 0))
        self.Doc.recompute()
        self.Sketch = otherSketch
        diagnostic = self.assertDiagnosticMatchesCommitted(
            faceSource,
            "Face1",
            True,
        )
        self.assertGreaterEqual(diagnostic["external_geometry_count"], 1)
        self.assertTrue(all(item["defining"] for item in diagnostic["external_geometry_metadata"]))

    def testDuplicateProjectionAndInvalidStateAreRejectedWithoutMutation(self):
        self.Sketch.addExternal(self.Source.Name, "Edge1", False, False)
        self.Doc.recompute()
        beforeLinks = list(self.Sketch.ExternalGeometry)
        beforeGeometry = [
            self.geometrySignature(item) for item in self.Sketch.ExternalGeo
        ]

        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(self.Source.Name, "Edge1", False, False)
        self.assertEqual(list(self.Sketch.ExternalGeometry), beforeLinks)
        self.assertEqual(
            [self.geometrySignature(item) for item in self.Sketch.ExternalGeo],
            beforeGeometry,
        )

        self.Sketch.ExternalTypes = [99]
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(self.Source.Name, "Edge1", False, True)

    def testIntersectionUpgradePreservesLinkAndRoleAndMatchesCommit(self):
        crossing = self.Doc.addObject("Part::Feature", "CrossingSource")
        crossing.Shape = Part.makeLine(
            App.Vector(1, 2, -2),
            App.Vector(5, 2, 2),
        )
        self.Doc.recompute()
        self.Sketch.addExternal(crossing.Name, "Edge1", False, True)
        self.Doc.recompute()
        self.assertEqual(list(self.Sketch.ExternalTypes), [1])
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        beforeSource = self.sourceDigest(crossing)

        diagnostic = self.Sketch.diagnoseExternal(
            crossing.Name,
            "Edge1",
            False,
            False,
        )
        expected = [
            self.geometrySignature(item) for item in diagnostic["external_geometry"]
        ]
        self.assertFalse(diagnostic["added_reference"])
        self.assertEqual(diagnostic["reference_index"], 0)
        self.assertEqual(diagnostic["type"], 2)
        self.assertFalse(diagnostic["defining"])
        self.assertEqual(list(self.Sketch.ExternalTypes), [1])
        self.assertEqual(self.sourceDigest(crossing), beforeSource)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(crossing.Name, "Edge1", True, False)

        self.Sketch.addExternal(crossing.Name, "Edge1", False, False)
        self.Doc.recompute()
        self.assertEqual(list(self.Sketch.ExternalTypes), [2])
        self.assertEqual(len(self.Sketch.ExternalGeometry), 1)
        self.assertEqual(
            [self.geometrySignature(item) for item in self.Sketch.ExternalGeo[2:]],
            expected,
        )
        self.assertEqual(self.sourceDigest(crossing), beforeSource)
        with self.assertRaises(ValueError):
            self.Sketch.diagnoseExternal(crossing.Name, "Edge1", False, False)

    def testInvalidTargetsAreRejectedWithoutDocumentMutation(self):
        beforeObjects = tuple(self.Doc.Objects)
        beforeLinks = len(self.Sketch.ExternalGeometry)
        beforeExternal = len(self.Sketch.ExternalGeo)
        for objectName, subelement in (
            ("Missing", "Edge1"),
            (self.Source.Name, "Edge99"),
            (self.Sketch.Name, "Edge1"),
        ):
            with self.assertRaises((ValueError, RuntimeError)):
                self.Sketch.diagnoseExternal(
                    objectName,
                    subelement,
                    False,
                    False,
                )
        self.assertEqual(tuple(self.Doc.Objects), beforeObjects)
        self.assertEqual(len(self.Sketch.ExternalGeometry), beforeLinks)
        self.assertEqual(len(self.Sketch.ExternalGeo), beforeExternal)

    def testCommittedProjectionIsOneUndoableAndRedoableDocumentChange(self):
        self.Doc.UndoMode = 1
        self.Doc.openTransaction("Projection host test")
        self.Sketch.addExternal(self.Source.Name, "Edge1", False, False)
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
