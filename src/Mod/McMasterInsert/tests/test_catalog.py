# SPDX-License-Identifier: LGPL-2.1-or-later
"""Catalog filename parsing and imported-component naming."""

from __future__ import annotations

import sys
import tempfile
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

import McMasterCatalogWebKit as webkit_helper


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

    def test_inner_body_has_unique_part_number_label_with_no_description(self):
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
        self.assertEqual(body.Label, "91251A051 Body")
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
        self.assertFalse(str(obj.Label).startswith("Component_"))

    def test_legal_internal_name_is_not_component_prefix(self):
        name = mmc._legal_object_name("Component", "90031A551")
        self.assertEqual(name, "_90031A551")
        self.assertFalse(name.startswith("Component_"))

    def test_origin_objects_are_not_transform_targets(self):
        class Origin:
            TypeId = "App::Origin"

        self.assertTrue(mmc._is_origin_object(Origin()))

    def test_generated_origin_and_timeline_objects_are_internal(self):
        class Internal:
            TypeId = "App::FeaturePython"

            def __init__(self, name):
                self.Name = name

        self.assertTrue(mmc._is_origin_object(Internal("Origin001")))
        self.assertTrue(mmc._is_origin_object(Internal("VibeCADTimeline")))

    def test_timeline_classified_component_remains_a_transform_target(self):
        class Component:
            Name = "Component_95462A029"
            TypeId = "PartDesign::Component"
            VibeCADTimelineRole = "internal"

        component = Component()

        self.assertIs(mmc._transform_target([component]), component)

    def test_component_tree_labels_are_unique_across_bodies_and_geometry(self):
        class Object:
            def __init__(self, name, type_id, group=None):
                self.Name = name
                self.TypeId = type_id
                self.Group = list(group or [])
                self.Label = name
                self.Label2 = ""
                self.Description = ""

        geometry_1 = Object("Feature1", "PartDesign::Feature")
        geometry_2 = Object("Feature2", "PartDesign::Feature")
        body_1 = Object("Body1", "PartDesign::Body", [geometry_1])
        body_2 = Object("Body2", "PartDesign::Body", [geometry_2])
        component = Object(
            "Component", "PartDesign::Component", [body_1, body_2]
        )

        mmc._name_imported_tree(
            component,
            "91251A051",
            Path("91251A051_Black-Oxide Alloy Steel Socket Head Screw.STEP"),
        )

        self.assertEqual(component.Label, "91251A051")
        self.assertEqual(body_1.Label, "91251A051 Body")
        self.assertEqual(body_2.Label, "91251A051 Body 2")
        self.assertEqual(geometry_1.Label, "91251A051 Geometry")
        self.assertEqual(geometry_2.Label, "91251A051 Geometry 2")


class TestCatalogImport(unittest.TestCase):
    def test_promoted_solid_never_uses_its_parent_body_label(self):
        class Object:
            def __init__(self, name, type_id):
                self.Name = name
                self.TypeId = type_id
                self.Group = []
                self.Label = name
                self._parent = None

            def getParentGeoFeatureGroup(self):
                return self._parent

            def addObject(self, child):
                child._parent = self
                self.Group.append(child)

        class Document:
            def __init__(self):
                self.created = []

            def addObject(self, type_id, name):
                obj = Object(name, type_id)
                self.created.append(obj)
                return obj

            def removeObject(self, name):
                self.created = [obj for obj in self.created if obj.Name != name]

        imported = Object("ImportedSolid", "Part::Feature")
        stamped = []
        named = []

        def stamp(obj, _part_number, _source_path, role="Body"):
            stamped.append((obj, role))

        def add_named(doc, type_id, visible_name, fallback_prefix):
            named.append((type_id, visible_name))
            return doc.addObject(type_id, fallback_prefix)

        with (
            mock.patch.object(mmc, "_stamp_body", side_effect=stamp),
            mock.patch.object(mmc, "_add_named_object", side_effect=add_named),
        ):
            mmc._promote_to_component(
                Document(),
                [imported],
                "95462A029",
                Path("95462A029_Zinc-Plated Medium-Strength Steel Hex Nut.STEP"),
            )

        imported_roles = [role for obj, role in stamped if obj is imported]
        self.assertEqual(imported_roles, ["Geometry"])
        self.assertEqual(
            named,
            [
                ("PartDesign::Component", "95462A029"),
                ("PartDesign::Body", "McMaster Import Body"),
            ],
        )

    def test_import_owns_transaction_through_component_promotion(self):
        events = []

        class Imported:
            Name = "ImportedSolid"
            TypeId = "Part::Feature"

        class Component:
            Name = "McMasterComponent"

        class Document:
            Name = "TestDocument"
            HasPendingTransaction = False

            def __init__(self):
                self.Objects = []

            def openTransaction(self, label):
                events.append(("open", label))
                self.HasPendingTransaction = True

            def commitTransaction(self):
                events.append(("commit", None))
                self.HasPendingTransaction = False

            def abortTransaction(self):
                events.append(("abort", None))
                self.HasPendingTransaction = False

            def recompute(self):
                events.append(("recompute", None))

        document = Document()
        imported = Imported()
        component = Component()
        import_gui = ModuleType("ImportGui")

        def insert(**_kwargs):
            events.append(("insert", document.HasPendingTransaction))
            document.Objects.append(imported)

        def promote(doc, created, _part_number, _path):
            events.append(("promote", doc.HasPendingTransaction))
            self.assertEqual(created, [imported])
            return component

        import_gui.insert = insert
        with (
            mock.patch.object(mmc.App, "ActiveDocument", document, create=True),
            mock.patch.dict(sys.modules, {"ImportGui": import_gui}),
            mock.patch.object(mmc, "_promote_to_component", side_effect=promote),
            mock.patch.object(mmc, "_name_imported_tree"),
        ):
            self.assertEqual(
                mmc.import_cad(Path("95462A029.STEP"), "95462A029"),
                ["McMasterComponent"],
            )

        self.assertEqual(events[0], ("open", "Insert McMaster-Carr Component"))
        self.assertIn(("insert", True), events)
        self.assertIn(("promote", True), events)
        self.assertEqual(events[-1], ("commit", None))
        self.assertNotIn(("abort", None), events)


class TestCatalogLaunch(unittest.TestCase):
    def test_linux_helper_persists_login_cookies_in_its_profile(self):
        class CookieManager:
            def __init__(self):
                self.storage = None

            def set_persistent_storage(self, path, storage):
                self.storage = (path, storage)

        cookies = CookieManager()

        class DataManager:
            def get_cookie_manager(self):
                return cookies

        data_manager = DataManager()
        context = object()
        webkit = mock.Mock()
        webkit.WebsiteDataManager.return_value = data_manager
        webkit.CookiePersistentStorage.SQLITE = "sqlite"
        webkit.WebContext.new_with_website_data_manager.return_value = context

        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            result = webkit_helper.create_web_context(webkit, profile)

            self.assertIs(result, context)
            self.assertEqual(
                cookies.storage,
                (str(profile / "cookies.sqlite"), "sqlite"),
            )

    def test_linux_helper_waits_for_all_downloads_before_closing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_staging = root / "first.step.download"
            first_target = root / "first.step"
            second_staging = root / "second.step.download"
            second_target = root / "second.step"
            first_staging.write_bytes(b"first")
            second_staging.write_bytes(b"second")
            first = object()
            second = object()

            window = webkit_helper.CatalogWindow.__new__(
                webkit_helper.CatalogWindow
            )
            window.downloads = {
                id(first): (first_staging, first_target),
                id(second): (second_staging, second_target),
            }
            window.GLib = mock.Mock()
            window._set_status = mock.Mock()
            window._quit = mock.Mock(return_value=False)

            window._download_finished(second)
            window.GLib.timeout_add.assert_not_called()
            self.assertTrue(second_target.is_file())

            window._download_finished(first)
            window.GLib.timeout_add.assert_called_once_with(500, window._quit)
            self.assertTrue(first_target.is_file())

    def test_linux_helper_publishes_a_complete_step_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "part.step.download"
            target = root / "part.step"
            staging.write_bytes(
                b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n"
                b"END-ISO-10303-21;\n"
            )

            download = mock.Mock()

            window = webkit_helper.CatalogWindow.__new__(
                webkit_helper.CatalogWindow
            )
            window.downloads = {id(download): (staging, target)}
            window.GLib = mock.Mock()
            window._set_status = mock.Mock()
            window._quit = mock.Mock(return_value=False)

            window._download_received(download, 5)

            self.assertTrue(target.is_file())
            self.assertFalse(staging.exists())
            window.GLib.timeout_add.assert_called_once_with(500, window._quit)

    def test_linux_helper_does_not_publish_an_incomplete_step_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "part.step.download"
            target = root / "part.step"
            staging.write_bytes(b"ISO-10303-21;\nHEADER;\n")
            download = mock.Mock()

            window = webkit_helper.CatalogWindow.__new__(
                webkit_helper.CatalogWindow
            )
            window.downloads = {id(download): (staging, target)}
            window.GLib = mock.Mock()
            window._set_status = mock.Mock()
            window._quit = mock.Mock(return_value=False)

            window._download_received(download, 5)

            self.assertFalse(target.exists())
            self.assertTrue(staging.is_file())
            window.GLib.timeout_add.assert_not_called()

    def test_linux_helper_only_classifies_real_cad_filenames(self):
        self.assertFalse(webkit_helper.is_cad_filename(""))
        self.assertFalse(webkit_helper.is_cad_filename("/"))
        self.assertFalse(webkit_helper.is_cad_filename("https://www.mcmaster.com/"))
        self.assertTrue(webkit_helper.is_cad_filename("91251A051.STEP"))

    def test_download_session_ignores_existing_files_and_stops_after_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "session"
            downloads = root / "Downloads"
            inbox.mkdir()
            downloads.mkdir()
            existing = downloads / "91251A051_existing.STEP"
            existing.write_bytes(b"existing")

            session = mmc.CatalogDownloadSession(
                inbox,
                downloads,
                document_name="TestDocument",
            )
            session.set_mode("external")
            downloaded = downloads / "95462A029_new.STEP"
            downloaded.write_bytes(b"complete")

            self.assertEqual(session.ready_paths(), [])
            self.assertEqual(session.ready_paths(), [downloaded])
            session.stop()
            self.assertEqual(session.ready_paths(), [])

    def test_embedded_session_only_observes_its_private_inbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "session"
            downloads = root / "Downloads"
            inbox.mkdir()
            downloads.mkdir()
            session = mmc.CatalogDownloadSession(
                inbox,
                downloads,
                document_name="TestDocument",
            )
            session.set_mode("embedded")
            unrelated = downloads / "95462A029_unrelated.STEP"
            unrelated.write_bytes(b"other browser")
            exact = inbox / "download.step"
            exact.write_bytes(b"embedded browser")

            self.assertEqual(session.ready_paths(), [])
            self.assertEqual(session.ready_paths(), [exact])

    def test_embedded_session_does_not_expire_while_catalog_is_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inbox = root / "session"
            downloads = root / "Downloads"
            inbox.mkdir()
            downloads.mkdir()
            session = mmc.CatalogDownloadSession(
                inbox,
                downloads,
                lifetime_seconds=0,
            )
            session.set_mode("embedded")
            exact = inbox / "download.step"
            exact.write_bytes(b"embedded browser")

            self.assertEqual(session.ready_paths(), [])
            self.assertEqual(session.ready_paths(), [exact])

    def test_embedded_catalog_imports_every_completed_download(self):
        paths = [Path("first.step"), Path("second.step")]

        self.assertEqual(mmc.catalog_paths_to_import(paths, "embedded"), paths)

    def test_webview2_profile_is_persistent_under_vibecad_user_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(mmc.os.environ, {"LOCALAPPDATA": temp_dir}):
                root = mmc.webview2_profile_root()

            self.assertEqual(root, Path(temp_dir) / "VibeCAD" / "McMasterBrowser")
            self.assertTrue(root.is_dir())

    def test_webview2_is_the_preferred_catalog_backend(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=True
            ) as webview2,
            mock.patch.object(mmc, "_webkit_dylib") as webkit,
            mock.patch.object(mmc, "show_catalog_window") as native_catalog,
            mock.patch.object(mmc, "open_external_catalog") as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "embedded")

        webview2.assert_called_once_with(inbox)
        webkit.assert_not_called()
        native_catalog.assert_not_called()
        external.assert_not_called()

    def test_without_webkit_opens_catalog_in_system_browser(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=False
            ),
            mock.patch.object(
                mmc, "show_linux_catalog_window", return_value=False
            ),
            mock.patch.object(mmc, "_webkit_dylib", return_value=None),
            mock.patch.object(mmc, "show_catalog_window") as embedded,
            mock.patch.object(mmc, "open_external_catalog", return_value=True) as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "external")

        embedded.assert_not_called()
        external.assert_called_once_with()

    def test_linux_helper_is_preferred_before_system_browser(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=False
            ),
            mock.patch.object(
                mmc, "show_linux_catalog_window", return_value=True
            ) as linux,
            mock.patch.object(mmc, "_webkit_dylib") as webkit,
            mock.patch.object(mmc, "open_external_catalog") as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "embedded")

        linux.assert_called_once_with(inbox)
        webkit.assert_not_called()
        external.assert_not_called()

    def test_without_embedded_backend_opens_system_browser(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=False
            ),
            mock.patch.object(
                mmc, "show_linux_catalog_window", return_value=False
            ),
            mock.patch.object(mmc, "_webkit_dylib", return_value=None),
            mock.patch.object(mmc, "show_catalog_window") as embedded,
            mock.patch.object(mmc, "open_external_catalog", return_value=True) as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "external")

        embedded.assert_not_called()
        external.assert_called_once_with()

    def test_available_webkit_keeps_embedded_catalog(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=False
            ),
            mock.patch.object(
                mmc, "show_linux_catalog_window", return_value=False
            ),
            mock.patch.object(mmc, "_webkit_dylib", return_value=object()),
            mock.patch.object(mmc, "show_catalog_window", return_value=True) as embedded,
            mock.patch.object(mmc, "open_external_catalog") as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "embedded")

        embedded.assert_called_once_with(inbox)
        external.assert_not_called()

    def test_broken_webkit_falls_back_to_system_browser(self):
        inbox = Path("/tmp/mcmaster-session")
        with (
            mock.patch.object(
                mmc, "show_webview2_catalog_window", return_value=False
            ),
            mock.patch.object(
                mmc, "show_linux_catalog_window", return_value=False
            ),
            mock.patch.object(mmc, "_webkit_dylib", side_effect=OSError("bad library")),
            mock.patch.object(mmc, "show_catalog_window") as embedded,
            mock.patch.object(mmc, "open_external_catalog", return_value=True) as external,
        ):
            self.assertEqual(mmc.open_catalog(inbox), "external")

        embedded.assert_not_called()
        external.assert_called_once_with()

    def test_external_catalog_opens_live_mcmaster_url(self):
        with mock.patch.object(mmc, "_open_system_url", return_value=True) as open_url:
            self.assertTrue(mmc.open_external_catalog())

        open_url.assert_called_once_with(mmc.CATALOG_URL)

    def test_windows_build_defines_and_installs_webview2_helper(self):
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        source = ROOT / "McMasterCatalogWebView2.cpp"
        bundle = (
            ROOT.parents[2] / "package/rattler-build/windows/create_bundle.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("if(WIN32 AND BUILD_GUI)", cmake)
        self.assertIn("McMasterCatalogWebView2", cmake)
        self.assertIn("WebView2LoaderStatic.lib", cmake)
        self.assertIn("INSTALL(TARGETS McMasterCatalogWebView2", cmake)
        self.assertIn('McMasterCatalogWebView2.exe" --smoke-test', bundle)
        webview_source = source.read_text(encoding="utf-8")
        self.assertIn('L".download"', webview_source)
        self.assertIn("std::filesystem::rename", webview_source)
        self.assertTrue(source.is_file())

    def test_linux_build_packages_webkit_helper(self):
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        source = ROOT / "McMasterCatalogWebKit.py"
        bundle = (
            ROOT.parents[2] / "package/rattler-build/linux/create_bundle.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("McMasterCatalogWebKit.py", cmake)
        self.assertIn("McMasterCatalogWebKit.py", bundle)
        self.assertIn("-m py_compile", bundle)
        self.assertTrue(source.is_file())


if __name__ == "__main__":
    unittest.main()
