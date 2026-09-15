# SPDX-License-Identifier: LGPL-2.1-or-later
"""A reference face must develop stock at its known material thickness."""

import json
import os
from pathlib import Path
import unittest

import Part

import SheetMetalEditable as Editable
import SheetMetalSourceFeatures as Sources
from SheetMetalEditGeometry import SheetGeometry
from SheetMetalNewUnfolder import BendAllowanceCalculator
from SMTests import testSheetSourceFeatures


class TestSheetReferenceFaces(unittest.TestCase):
    def setUp(self):
        self.case = testSheetSourceFeatures.TestSheetSourceFeatures()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.source = self.case.source()
        self.case.helper.recompute()
        self.shape = self.source.Shape
        self.calculator = BendAllowanceCalculator.from_single_value(.42, "ansi")
        self.skin = max((index for index, face in enumerate(self.shape.Faces)
                         if isinstance(face.Surface, Part.Plane)),
                        key=lambda index: self.shape.Faces[index].Area)

    def test_end_face_cannot_be_prepared_as_stock_with_the_requested_gauge(self):
        # This is the exact Face1 chosen in the failed ordinary-prompt run.
        old = SheetGeometry.prepare(self.shape, 0, self.calculator)
        Path(os.environ["VIBECAD_TEST_OUTPUT"], "end-face-evidence.json").write_text(json.dumps({
            "source_thickness": float(self.source.thickness),
            "inferred_thickness": old.mapping.thickness,
            "normal": list(old.normal),
            "flat_bounds": [old.flat.BoundBox.XLength, old.flat.BoundBox.YLength, old.flat.BoundBox.ZLength],
        }, indent=2))
        with self.assertRaisesRegex(ValueError, "thickness|skin"):
            SheetGeometry.prepare(self.shape, 0, self.calculator, expected_thickness=1.6)

    def test_stock_skin_develops_at_the_nominal_gauge(self):
        result = SheetGeometry.prepare(self.shape, self.skin, self.calculator, expected_thickness=1.6)
        self.assertAlmostEqual(result.mapping.thickness, 1.6)
        depths = [vertex.Point.dot(result.normal) for vertex in result.flat.Vertexes]
        self.assertAlmostEqual(max(depths)-min(depths), 1.6)
        self.assertEqual(len(result.flat.Solids), 1)
        self.assertTrue(result.flat.isValid())

    def test_wrong_face_is_rejected_before_creating_a_tree_or_history_entry(self):
        import SheetMetalOperations as Operations

        objects, undo = tuple(self.case.doc.Objects), self.case.doc.UndoCount
        revision = Operations.capture_source_revision(self.source)
        with self.assertRaisesRegex(ValueError, "thickness|skin"):
            Operations.start_creation(self.source, "Face1", expected_revision=revision)
        self.assertEqual(tuple(self.case.doc.Objects), objects)
        self.assertEqual(self.case.doc.UndoCount, undo)

    def test_document_never_publishes_wrong_gauge_as_prepared_and_can_be_repaired(self):
        sheet = Editable.create_sheet(self.source, "Face1")
        self.case.helper.recompute()
        with self.assertRaises(RuntimeError):
            Editable.get_prepared(sheet)
        self.assertTrue({"Invalid", "Error"}.intersection(sheet.State), sheet.State)
        sheet.SourceFace = (self.source, [f"Face{self.skin+1}"])
        self.case.helper.recompute()
        result = Editable.get_prepared(sheet)
        self.assertAlmostEqual(result.mapping.thickness, 1.6)

    def test_worker_metadata_distinguishes_skin_candidates_from_thickness_walls(self):
        summary = Sources.get_prepared_source(self.source)
        self.assertAlmostEqual(summary["thickness_mm"], 1.6)
        faces = summary["reference_faces"]
        self.assertNotIn("Face1", [face["name"] for face in faces])
        self.assertIn(f"Face{self.skin+1}", [face["name"] for face in faces])
        self.assertGreaterEqual(summary["reference_face_count"], len(faces))
        for face in faces:
            geometry = SheetGeometry.prepare(self.shape, int(face["name"][4:])-1,
                                             self.calculator, expected_thickness=1.6)
            self.assertAlmostEqual(geometry.mapping.thickness, 1.6)
