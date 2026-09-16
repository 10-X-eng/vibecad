# SPDX-License-Identifier: LGPL-2.1-or-later
"""Unfold operations must retain their document when the user changes tabs."""

import unittest
import time
import threading
import SheetMetalTools
from PySide import QtCore
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD
import Part
import SheetMetalKfactor
import SheetMetalNewUnfolder
import SheetMetalUnfolder


class TestUnfoldDocument(unittest.TestCase):
    def setUp(self):
        self.owner = FreeCAD.newDocument("UnfoldOwnerTest")
        self.other = FreeCAD.newDocument("UnfoldOtherTest")
        self.edge = Part.makeLine(FreeCAD.Vector(), FreeCAD.Vector(10, 0, 0))

    def tearDown(self):
        for doc in (self.other, self.owner):
            deadline = time.monotonic() + 5
            while (
                hasattr(doc, "isClosable")
                and not doc.isClosable()
                and time.monotonic() < deadline
            ):
                QtCore.QCoreApplication.processEvents()
                time.sleep(0.001)
            FreeCAD.closeDocument(doc.Name)

    def test_material_lookup_owner_and_legacy_default(self):
        for doc, factor in ((self.owner, 0.42), (self.other, 0.11)):
            sheet = doc.addObject("Spreadsheet::Sheet", "material_test")
            sheet.set("A1", "Radius / Thickness")
            sheet.set("B1", "K-factor (ANSI)")
            sheet.set("A2", "1")
            sheet.set("B2", str(factor))
            sheet.recompute()
        lookup = SheetMetalKfactor.KFactorLookupTable(
            "material_test", document=self.owner
        )
        self.assertEqual(lookup.k_factor_lookup[1], 0.42)
        self.assertEqual(
            SheetMetalKfactor.KFactorLookupTable("material_test").k_factor_lookup[1],
            0.11,
        )
        self.assertIs(FreeCAD.ActiveDocument, self.other)

    def _check_sketch(self, make):
        unrelated = self.other.addObject("Sketcher::SketchObject", "UnfoldSketch")
        unrelated.addGeometry(
            Part.LineSegment(FreeCAD.Vector(), FreeCAD.Vector(999, 0, 0)), False
        )
        before = str(unrelated.Geometry)
        sketch = make([self.edge], "UnfoldSketch", document=self.owner)
        self.assertIs(sketch.Document, self.owner)
        self.assertEqual(sketch.GeometryCount, 1)
        again = make(
            [self.edge], "UnfoldSketch", existing=[sketch.Name], document=self.owner
        )
        self.assertEqual(again.Name, sketch.Name)
        self.assertEqual(again.GeometryCount, 1)
        self.assertEqual(str(unrelated.Geometry), before)
        default = make([self.edge], "DefaultSketch")
        self.assertIs(default.Document, self.other)
        self.assertIs(FreeCAD.ActiveDocument, self.other)

    def test_v2_sketch_ownership_and_existing_caller_default(self):
        def make(edges, name, existing=None, **kwargs):
            return SheetMetalNewUnfolder.SketchExtraction.edges_to_sketch_object(
                edges, name, existing, **kwargs
            )

        self._check_sketch(make)

    def test_v1_sketch_ownership_and_existing_caller_default(self):
        def make(edges, name, existing=None, **kwargs):
            return SheetMetalUnfolder.generateSketch(
                edges, name, "#000000", existing, **kwargs
            )

        self._check_sketch(make)

    def test_v1_fallback_preserves_unrelated_active_objects(self):
        sentinel = self.owner.addObject("Part::Feature", "KeepMe")
        other_sentinel = self.other.addObject("Part::Feature", "KeepMe")
        with patch.object(
            SheetMetalUnfolder.Draft,
            "makeSketch",
            side_effect=RuntimeError("force fallback"),
        ):
            sketch = SheetMetalUnfolder.generateSketch(
                [self.edge], "UnfoldSketch", "#000000", document=self.owner
            )
        self.assertIs(sketch.Document, self.owner)
        self.assertEqual(sketch.GeometryCount, 1)
        self.assertIs(self.owner.getObject("KeepMe"), sentinel)
        self.assertIs(self.other.getObject("KeepMe"), other_sentinel)
        self.assertIs(FreeCAD.ActiveDocument, self.other)

    def test_v1_sew_recovery_stays_in_solid_document(self):
        solid = self.owner.addObject("Part::Feature", "SourceSolid")
        solid.Shape = Part.makeBox(10, 10, 1)
        other_before = tuple(obj.Name for obj in self.other.Objects)
        failed_tree = SimpleNamespace(error_code=1, failed_face_idx=0)
        with patch.object(SheetMetalUnfolder, "SheetTree", return_value=failed_tree):
            result = SheetMetalUnfolder.getUnfold({1: 0.42}, solid, "Face1", "ansi")
        self.assertEqual(result[4], 1)
        self.assertIsNotNone(self.owner.getObject(result[6]))
        self.assertEqual(tuple(obj.Name for obj in self.other.Objects), other_before)
        self.assertIs(FreeCAD.ActiveDocument, self.other)

    def test_background_presentation_runs_on_gui_and_discards_deleted_target(self):
        calls = []
        main_thread = threading.get_ident()
        sketch = self.owner.addObject("Sketcher::SketchObject", "StyledSketch")

        def update(view):
            calls.append(threading.get_ident())
            view.DrawStyle = "Dashdot"

        worker = threading.Thread(
            target=lambda: SheetMetalTools.smUpdateViewObject(sketch, update)
        )
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive(), "Presentation must not wait for the GUI")
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            QtCore.QCoreApplication.processEvents()
            time.sleep(0.001)
        self.assertEqual(calls, [main_thread])
        self.assertEqual(sketch.ViewObject.DrawStyle, "Dashdot")

        calls.clear()
        worker = threading.Thread(
            target=lambda: SheetMetalTools.smUpdateViewObject(sketch, update)
        )
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.owner.removeObject(sketch.Name)
        for _ in range(10):
            QtCore.QCoreApplication.processEvents()
        self.assertEqual(calls, [])

    def test_v1_fallback_resets_partial_draft_placement(self):
        def partial_failure(*args, **kwargs):
            kwargs["addTo"].Placement = FreeCAD.Placement(
                FreeCAD.Vector(50, 0, 0), FreeCAD.Rotation()
            )
            raise RuntimeError("Draft failed after changing the placement")

        with patch.object(
            SheetMetalUnfolder.Draft, "makeSketch", side_effect=partial_failure
        ):
            sketch = SheetMetalUnfolder.generateSketch(
                [self.edge], "UnfoldSketch", "#000000", document=self.owner
            )
        self.assertEqual(sketch.Placement.Base, FreeCAD.Vector())
