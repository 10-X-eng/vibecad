# SPDX-License-Identifier: LGPL-2.1-or-later
"""Catalog filename parsing and imported-component naming."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import McMasterInsert as mmc
except ImportError as exc:
    if exc.name != "FreeCAD":
        raise
    with mock.patch.dict(sys.modules, {"FreeCAD": ModuleType("FreeCAD")}):
        import McMasterInsert as mmc


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

    def test_catalog_description_strips_cad_variant_prefix(self):
        path = Path(
            "91290A115_NO THREADS_Black-Oxide Alloy Steel Socket Head Screw.STEP"
        )
        self.assertEqual(
            mmc.catalog_description("91290A115", path),
            "Black-Oxide Alloy Steel Socket Head Screw",
        )

    def test_inner_body_is_part_number_with_no_description(self):
        class Dummy:
            pass

        body = Dummy()
        body.Label2 = "should be cleared"
        body.Description = "should be cleared"
        mmc._stamp_body(
            body,
            "91251A051",
            Path("91251A051_Black-Oxide Alloy Steel Socket Head Screw.STEP"),
        )
        self.assertEqual(body.Label, "91251A051")
        self.assertEqual(body.Label2, "")
        self.assertEqual(body.Description, "")

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


class TestCatalogLaunch(unittest.TestCase):
    def test_without_webkit_opens_catalog_in_system_browser(self):
        with (
            mock.patch.object(mmc, "_webkit_dylib", return_value=None),
            mock.patch.object(mmc, "show_catalog_window") as embedded,
            mock.patch.object(mmc, "open_external_catalog", return_value=True) as external,
        ):
            self.assertEqual(mmc.open_catalog(), "external")

        embedded.assert_not_called()
        external.assert_called_once_with()

    def test_available_webkit_keeps_embedded_catalog(self):
        with (
            mock.patch.object(mmc, "_webkit_dylib", return_value=object()),
            mock.patch.object(mmc, "show_catalog_window", return_value=True) as embedded,
            mock.patch.object(mmc, "open_external_catalog") as external,
        ):
            self.assertEqual(mmc.open_catalog(), "embedded")

        embedded.assert_called_once_with()
        external.assert_not_called()

    def test_broken_webkit_falls_back_to_system_browser(self):
        with (
            mock.patch.object(mmc, "_webkit_dylib", side_effect=OSError("bad library")),
            mock.patch.object(mmc, "show_catalog_window") as embedded,
            mock.patch.object(mmc, "open_external_catalog", return_value=True) as external,
        ):
            self.assertEqual(mmc.open_catalog(), "external")

        embedded.assert_not_called()
        external.assert_called_once_with()

    def test_external_catalog_opens_live_mcmaster_url(self):
        with mock.patch.object(mmc, "_open_system_url", return_value=True) as open_url:
            self.assertTrue(mmc.open_external_catalog())

        open_url.assert_called_once_with(mmc.CATALOG_URL)


if __name__ == "__main__":
    unittest.main()
