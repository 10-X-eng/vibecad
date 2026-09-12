# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD's macOS bundles must not claim upstream FreeCAD's identity.

macOS keys Gatekeeper approvals, TCC privacy grants, LaunchServices document
bindings and QuickLook plug-in registration on bundle identifiers.  Shipping
upstream FreeCAD's identifiers makes a VibeCAD install collide with an
already-installed, notarized FreeCAD.app instead of coexisting with it.
"""

from __future__ import annotations

import plistlib
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
QUICKLOOK_ROOT = REPO_ROOT / "src" / "MacAppBundle" / "QuickLook"
MAIN_WINDOW = REPO_ROOT / "src" / "Gui" / "MainWindow.cpp"
BUNDLE_TEMPLATE = (
    REPO_ROOT / "package" / "rattler-build" / "osx" / "Info.plist.template"
)
QUICKLOOK_GENERATOR = (
    QUICKLOOK_ROOT
    / "legacy"
    / "QuicklookFCStd.qlgenerator"
    / "Contents"
    / "Info.plist"
)
QUICKLOOK_CMAKE_FILES = (
    QUICKLOOK_ROOT / "CMakeLists.txt",
    QUICKLOOK_ROOT / "modern" / "CMakeLists.txt",
)

FREECAD_APP_IDENTIFIER = "org.freecad.FreeCAD"
FREECAD_QUICKLOOK_IDENTIFIER = "org.freecad.qlgenerator.QuicklookFCStd"
FREECAD_DOCUMENT_UTI = "org.freecad.fcstd"


def _load(path: Path) -> dict:
    with path.open("rb") as stream:
        return plistlib.load(stream)


class TestMacOSBundleIdentity(unittest.TestCase):
    def test_application_bundle_identifier_is_vibecad_owned(self) -> None:
        identifier = _load(BUNDLE_TEMPLATE)["CFBundleIdentifier"]
        self.assertNotEqual(
            identifier,
            FREECAD_APP_IDENTIFIER,
            "VibeCAD.app must not reuse upstream FreeCAD's bundle identifier; "
            "macOS would treat the two apps as one for Gatekeeper, TCC and "
            "LaunchServices.",
        )
        self.assertNotIn(
            "freecad",
            identifier.casefold(),
            f"VibeCAD.app must own its bundle identifier, got {identifier!r}.",
        )
        self.assertIn("vibecad", identifier.casefold())

    def test_quicklook_generator_identifier_is_vibecad_owned(self) -> None:
        identifier = _load(QUICKLOOK_GENERATOR)["CFBundleIdentifier"]
        self.assertNotEqual(
            identifier,
            FREECAD_QUICKLOOK_IDENTIFIER,
            "The bundled QuickLook generator must not reuse upstream FreeCAD's "
            "plug-in identifier; pluginkit registers one of the two only.",
        )
        self.assertIn("vibecad", identifier.casefold())

    def test_quicklook_extension_ids_match_the_application_identifier(self) -> None:
        # pluginkit registers the QuickLook app extensions under an identifier
        # derived from the host application. MainWindow.cpp queries the same
        # identifier, so a mismatch silently disables QuickLook registration.
        expected = _load(BUNDLE_TEMPLATE)["CFBundleIdentifier"]
        for cmake_file in QUICKLOOK_CMAKE_FILES:
            with self.subTest(cmake_file=cmake_file.name):
                match = re.search(
                    r'set\(FREECAD_BUNDLE_ID\s+"([^"]+)"\)',
                    cmake_file.read_text(encoding="utf-8"),
                )
                self.assertIsNotNone(match, f"{cmake_file} defines no bundle id.")
                self.assertEqual(match.group(1), expected)

        main_window = MAIN_WINDOW.read_text(encoding="utf-8")
        for role in ("thumbnail", "preview"):
            with self.subTest(role=role):
                self.assertIn(f'"{expected}.quicklook.{role}"', main_window)

    def test_freecad_document_type_is_imported_not_exported(self) -> None:
        bundle = _load(BUNDLE_TEMPLATE)
        exported = {
            declaration.get("UTTypeIdentifier")
            for declaration in bundle.get("UTExportedTypeDeclarations", ())
        }
        self.assertNotIn(
            FREECAD_DOCUMENT_UTI,
            exported,
            "VibeCAD does not own org.freecad.fcstd, so it must declare the "
            "type as imported rather than exporting a conflicting definition.",
        )
        imported = {
            declaration.get("UTTypeIdentifier")
            for declaration in bundle.get("UTImportedTypeDeclarations", ())
        }
        self.assertIn(
            FREECAD_DOCUMENT_UTI,
            imported,
            "VibeCAD still opens FreeCAD documents, so the type must stay "
            "declared as an imported type.",
        )

    def test_bundle_does_not_seize_freecad_document_bindings(self) -> None:
        bundle = _load(BUNDLE_TEMPLATE)
        document_types = bundle.get("CFBundleDocumentTypes", ())
        self.assertTrue(document_types, "VibeCAD must still open its documents.")
        for document_type in document_types:
            with self.subTest(extensions=document_type.get("CFBundleTypeExtensions")):
                self.assertNotIn(
                    "LSIsAppleDefaultForType",
                    document_type,
                    "Claiming to be the system default handler overrides the "
                    "user's choice between VibeCAD and an installed FreeCAD.",
                )


if __name__ == "__main__":
    unittest.main()
