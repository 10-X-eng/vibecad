# SPDX-License-Identifier: LGPL-2.1-or-later
"""Catalog filename parsing and imported-component naming."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import McMasterInsert as mmc
except ImportError as exc:  # pragma: no cover - needs FreeCAD on PYTHONPATH
    mmc = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(mmc is None, f"FreeCAD not importable: {_IMPORT_ERROR}")
class TestCatalogNames(unittest.TestCase):
    def test_part_number_from_mcmaster_step_filename(self):
        self.assertEqual(
            mmc.part_number_from_filename(
                "91251A051_Black-Oxide Alloy Steel Socket Head Screw.STEP"
            ),
            "91251A051",
        )

    def test_catalog_description_strips_part_number(self):
        path = Path("91251A051_Black-Oxide Alloy Steel Socket Head Screw.STEP")
        self.assertEqual(
            mmc.catalog_description("91251A051", path),
            "Black-Oxide Alloy Steel Socket Head Screw",
        )

    def test_catalog_description_keeps_no_threads_variant(self):
        path = Path(
            "91290A115_NO THREADS_Black-Oxide Alloy Steel Socket Head Screw.STEP"
        )
        self.assertEqual(
            mmc.catalog_description("91290A115", path),
            "NO THREADS Black-Oxide Alloy Steel Socket Head Screw",
        )

    def test_component_label_is_part_number_not_mmc_prefix(self):
        class Dummy:
            pass

        obj = Dummy()
        mmc._stamp_metadata(
            obj,
            "91251A051",
            Path("91251A051_Black-Oxide Alloy Steel Socket Head Screw.STEP"),
        )
        self.assertEqual(obj.Label, "91251A051")
        self.assertFalse(str(obj.Label).startswith("MMC-"))
        self.assertEqual(
            obj.Description,
            "Black-Oxide Alloy Steel Socket Head Screw",
        )
        self.assertEqual(obj.Label2, obj.Description)

    def test_origin_objects_are_not_transform_targets(self):
        class Origin:
            TypeId = "App::Origin"

        self.assertTrue(mmc._is_origin_object(Origin()))


if __name__ == "__main__":
    unittest.main()
