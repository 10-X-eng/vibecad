# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host-python tests for printables reverse IR planning. No mesh.to_shape."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestPrintablesReconstructIR(unittest.TestCase):
    def _load(self):
        from VibeCADNativeReconstructParametricRuntime import printables_ir_plan
        from VibeCADNativeMeshReconstructParametricSchema import (
            MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
        )

        return printables_ir_plan, MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME

    def test_capability_name_is_not_to_shape(self):
        _plan, name = self._load()
        self.assertEqual(name, "mesh.reconstruct_parametric")
        self.assertNotEqual(name, "mesh.to_shape")

    def test_plan_reads_schema_version_1_extrude_and_holes(self):
        printables_ir_plan, _name = self._load()
        ir = {
            "schema_version": 1,
            "units": "mm",
            "body": "bracket",
            "class": "parametric",
            "expected_shells": 1,
            "forbidden": {"triangle_wrapped_step": True},
            "sketches": [
                {
                    "id": "s1",
                    "origin_mm": [0.0, 0.0, 0.0],
                    "normal": [0.0, 0.0, 1.0],
                    "x_axis": [1.0, 0.0, 0.0],
                    "y_axis": [0.0, 1.0, 0.0],
                    "profiles": [
                        {
                            "id": "outer",
                            "role": "outer",
                            "entities": [
                                {"type": "line", "a_mm": [0.0, 0.0], "b_mm": [20.0, 0.0]},
                                {"type": "line", "a_mm": [20.0, 0.0], "b_mm": [20.0, 20.0]},
                                {"type": "line", "a_mm": [20.0, 20.0], "b_mm": [0.0, 20.0]},
                                {"type": "line", "a_mm": [0.0, 20.0], "b_mm": [0.0, 0.0]},
                            ],
                        }
                    ],
                }
            ],
            "features": [
                {
                    "id": "f1",
                    "type": "extrude",
                    "op": "add",
                    "sketch": "s1",
                    "depth_mm": 12.0,
                    "direction": [0.0, 0.0, 1.0],
                },
                {
                    "id": "f2",
                    "type": "hole",
                    "diameter_mm": 4.2,
                    "uv_mm": [5.0, 5.0],
                },
            ],
        }
        plan = printables_ir_plan(ir)
        self.assertEqual(plan["class"], "parametric")
        self.assertEqual(plan["extrude"]["sketch"], "s1")
        self.assertEqual(len(plan["holes"]), 1)
        self.assertEqual(plan["holes"][0]["diameter_mm"], 4.2)

    def test_rejects_missing_schema(self):
        printables_ir_plan, _name = self._load()
        with self.assertRaises(Exception):
            printables_ir_plan({"units": "mm", "class": "parametric"})


if __name__ == "__main__":
    unittest.main()
