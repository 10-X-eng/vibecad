# SPDX-License-Identifier: MIT
"""Smoke tests for SendCutSendPresets (no FreeCAD GUI required).

Run from the Mod directory:
  python -m unittest tests.test_smoke -v

AI/TDD note (vibecad CONTRIBUTING): these unit tests cover pure data/helpers.
GUI Apply/Unfold flows are listed in SMOKE.md (FreeCADCmd + manual GUI).
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_freecad_stub():
    """bend_common imports FreeCAD; stub it for headless unittest."""
    if "FreeCAD" in sys.modules:
        return
    fc = types.ModuleType("FreeCAD")
    fc.Units = types.SimpleNamespace(Quantity=lambda s: s)
    fc.getUserAppDataDir = lambda: str(ROOT)
    sys.modules["FreeCAD"] = fc


class TestBendTable(unittest.TestCase):
    def setUp(self):
        path = ROOT / "data" / "sendcutsend_bends.json"
        with path.open(encoding="utf-8") as fh:
            self.data = json.load(fh)

    def test_json_has_materials(self):
        mats = self.data.get("materials")
        self.assertIsInstance(mats, list)
        self.assertGreaterEqual(len(mats), 5)

    def test_5052_063_row(self):
        alum = next(m for m in self.data["materials"] if m["name"] == "5052 Aluminum")
        row = next(t for t in alum["thicknesses"] if abs(float(t["t"]) - 0.063) < 1e-9)
        self.assertIn("k", row)
        self.assertIn("r", row)
        self.assertIn("min_flange", row)
        self.assertGreater(float(row["k"]), 0.0)
        self.assertGreater(float(row["r"]), 0.0)

    def test_rows_have_required_keys(self):
        required = {"t", "k", "r", "bd", "relief", "die", "min_flange", "min_corner_relief"}
        for mat in self.data["materials"]:
            for row in mat.get("thicknesses", []):
                missing = required - set(row)
                self.assertFalse(missing, msg="%s missing %s" % (mat["name"], missing))


class TestNamingHelpers(unittest.TestCase):
    def setUp(self):
        _install_freecad_stub()
        sys.path.insert(0, str(ROOT))
        import bend_common  # noqa: WPS433

        self.bc = bend_common

    def test_material_short_name_known(self):
        self.assertEqual(self.bc.material_short_name("5052 Aluminum"), "5052")
        self.assertEqual(self.bc.material_short_name("304 Stainless Steel"), "304SS")

    def test_material_short_name_custom(self):
        self.assertEqual(self.bc.material_short_name("Adamantine"), "Adamantine"[:12])

    def test_thickness_thou(self):
        self.assertEqual(self.bc.thickness_thou(0.063), "063")
        self.assertEqual(self.bc.thickness_thou(0.250), "250")


class TestMinFlangeRule(unittest.TestCase):
    """Mirror of bend_actions check: warn only when length is strictly below min."""

    @staticmethod
    def should_warn(length_in, min_flange):
        if min_flange is None or float(min_flange) <= 0:
            return False
        return float(length_in) + 1e-6 < float(min_flange)

    def test_equal_is_ok(self):
        self.assertFalse(self.should_warn(1.0, 1.0))

    def test_below_warns(self):
        self.assertTrue(self.should_warn(0.999, 1.0))

    def test_blank_min_skips(self):
        self.assertFalse(self.should_warn(0.5, 0))
        self.assertFalse(self.should_warn(0.5, None))


if __name__ == "__main__":
    unittest.main()
