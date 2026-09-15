# SPDX-License-Identifier: LGPL-2.1-or-later
"""Upstream source builders validate on native workers and retain editable inputs."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import FreeCAD as App
import Part

from SMTests import testEditableSheet


class TestSheetSourceFeatures(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("NativeSheetSources")
        self.helper = testEditableSheet.TestEditableSheet()
        self.helper.doc = self.doc
        self.addCleanup(self.helper.close_document)

    def source(self, kind="L-Shape"):
        import SheetMetalSourceFeatures as Sources
        obj = self.doc.addObject("Part::FeaturePython", "BaseShape")
        Sources.BaseShape(obj)
        obj.shapeType = kind
        obj.thickness, obj.radius = 1.6, 2
        obj.width, obj.length, obj.height, obj.flangeWidth = 50, 70, 25, 8
        obj.originLoc, obj.fillGaps = "0,0", True
        return obj

    def test_all_upstream_base_shapes_have_worker_validated_geometry(self):
        import SheetMetalSourceFeatures as Sources
        from SheetMetalBaseShapeCmd import SMBaseShape, base_shape_types
        original, threads = Sources._validate_shape, []
        def validate(shape):
            threads.append(threading.get_ident())
            return original(shape)
        with patch.object(Sources, "_validate_shape", side_effect=validate):
            for kind in base_shape_types:
                with self.subTest(kind=kind):
                    obj = self.source(kind)
                    with self.assertRaises(RuntimeError):
                        Sources.get_prepared_source(obj)
                    self.helper.recompute()
                    result = Sources.get_prepared_source(obj)
                    self.assertIsInstance(obj.Proxy, SMBaseShape)
                    self.assertEqual(result["solid_count"], 1)
                    self.assertGreater(result["volume_mm3"], 0)
                    self.assertEqual(result["object_name"], obj.Name)
                    self.assertEqual(result["document_uid"], self.doc.Uid)
                    self.assertTrue(all(value > 0 for value in result["size_mm"]))
                    self.assertIsNone(obj.Proxy.__getstate__())
        self.assertEqual(len(threads), len(base_shape_types))
        self.assertTrue(all(value != threading.get_ident() for value in threads))

    def test_open_and_closed_sketch_sources_keep_the_exact_editable_sketch(self):
        import SheetMetalSourceFeatures as Sources
        from SheetMetalBaseCmd import SMBaseBend
        for closed in (False, True):
            with self.subTest(closed=closed):
                sketch = self.doc.addObject("Sketcher::SketchObject", "SourceSketch")
                points = [App.Vector(), App.Vector(40, 0), App.Vector(40, 30)]
                if closed:
                    points.extend((App.Vector(0, 30), App.Vector()))
                for start, end in zip(points, points[1:]):
                    sketch.addGeometry(Part.LineSegment(start, end), False)
                self.helper.recompute()
                obj = self.doc.addObject("Part::FeaturePython", "BaseBend")
                Sources.BaseBend(obj, sketch)
                obj.Thickness, obj.Radius, obj.Length = 1.6, 2, 25
                obj.BendSide, obj.MidPlane, obj.Reverse = "Inside", False, False
                self.helper.recompute()
                self.assertIsInstance(obj.Proxy, SMBaseBend)
                self.assertIs(obj.BendSketch, sketch)
                self.assertEqual(Sources.get_prepared_source(obj)["solid_count"], 1)

    def test_creation_evidence_names_planar_reference_faces_without_gui_geometry_work(self):
        import SheetMetalSourceFeatures as Sources
        obj = self.source()
        self.helper.recompute()
        with patch.object(Sources, "_validate_shape", side_effect=AssertionError("GUI geometry inspection")):
            result = Sources.get_prepared_source(obj)
        faces = result["planar_faces"]
        self.assertGreater(len(faces), 0)
        self.assertLessEqual(len(faces), 32)
        self.assertGreaterEqual(result["planar_face_count"], len(faces))
        areas = []
        for item in faces:
            face = obj.Shape.getElement(item["name"])
            self.assertIsInstance(face.Surface, Part.Plane)
            self.assertAlmostEqual(item["area_mm2"], face.Area)
            areas.append(item["area_mm2"])
        self.assertEqual(areas, sorted(areas, reverse=True))
        faces[0]["name"] = "Face99999"
        self.assertNotEqual(Sources.get_prepared_source(obj)["planar_faces"][0]["name"], "Face99999")

    def test_solid_conversion_keeps_the_exact_selected_faces(self):
        import SheetMetalSourceFeatures as Sources
        from SheetMetalFromSolid import SMFromSolid
        source = self.doc.addObject("Part::Feature", "Solid")
        source.Shape = Part.makeBox(50, 40, 30)
        self.helper.recompute()
        removed = next(f"Face{i+1}" for i, face in enumerate(source.Shape.Faces)
                       if face.normalAt(0, 0).z > .9)
        obj = self.doc.addObject("Part::FeaturePython", "SolidToSheet")
        Sources.FromSolid(obj, source, [removed])
        obj.Thickness, obj.Radius, obj.Invert = 1.6, 2, False
        self.helper.recompute()
        self.assertIsInstance(obj.Proxy, SMFromSolid)
        self.assertEqual(obj.baseObject, (source, [removed]))
        self.assertEqual(Sources.get_prepared_source(obj)["solid_count"], 1)

        # The upstream builder returns early for a missing input. An old Shape
        # must not become a fresh success after that no-op.
        obj.baseObject = None
        self.helper.recompute()
        with self.assertRaises(RuntimeError):
            Sources.get_prepared_source(obj)

    def test_failed_rebuild_never_reuses_the_previous_validity_proof(self):
        import SheetMetalSourceFeatures as Sources
        obj = self.source()
        self.helper.recompute()
        Sources.get_prepared_source(obj)
        obj.height = 28
        with patch.object(Sources, "_validate_shape", side_effect=RuntimeError("invalid result")):
            self.helper.recompute()
        with self.assertRaises(RuntimeError):
            Sources.get_prepared_source(obj)
        obj.touch()
        self.helper.recompute()
        self.assertEqual(Sources.get_prepared_source(obj)["solid_count"], 1)

    def test_restore_keeps_parameters_and_revalidates_without_serializing_cached_geometry(self):
        import SheetMetalSourceFeatures as Sources
        obj = self.source()
        self.helper.recompute()
        volume = Sources.get_prepared_source(obj)["volume_mm3"]
        with tempfile.TemporaryDirectory() as directory:
            name = obj.Name
            filename = str(Path(directory)/"native-source.FCStd")
            self.doc.saveAs(filename)
            self.helper.settle()
            App.closeDocument(self.doc.Name)
            self.doc = self.helper.doc = App.openDocument(filename)
            self.helper.settle()
            obj = self.doc.getObject(name)
            self.assertEqual(float(obj.height), 25)
            self.assertIsNone(obj.Proxy.__getstate__())
            obj.touch()
            self.helper.recompute()
            self.assertAlmostEqual(Sources.get_prepared_source(obj)["volume_mm3"], volume)
