# SPDX-License-Identifier: LGPL-2.1-or-later
"""Artifact collection must not change the geometry used by live-test oracles."""

from pathlib import Path
import tempfile
import unittest

import Part

from SMTests import live_sheet_prompt


class TestLiveSheetArtifacts(unittest.TestCase):
    def test_step_artifact_preserves_the_original_brep(self):
        shape = Part.makeBox(70, 50, 1.6).cut(Part.makeCylinder(3, 1.6))
        self.assertTrue(shape.isValid())
        before = shape.exportBrepToString()
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "sheet.step"
            live_sheet_prompt.export_step_artifact(shape, filename)
            self.assertEqual(shape.exportBrepToString(), before)
            exported = Part.Shape()
            exported.read(str(filename))
            self.assertTrue(exported.isValid())
            self.assertEqual(len(exported.Solids), 1)
            self.assertAlmostEqual(exported.Volume, shape.Volume, places=5)
