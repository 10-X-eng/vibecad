# SPDX-License-Identifier: LGPL-2.1-or-later

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from generate_update_manifest import generate_manifest  # noqa: E402


class TestGenerateUpdateManifest(unittest.TestCase):
    def _repo(self, root: Path, *, suffix: str = "RC3", build: int = 17) -> None:
        (root / "version.json").write_text(
            json.dumps(
                {
                    "name": "VibeCAD",
                    "version_major": 26,
                    "version_minor": 3,
                    "version_patch": 1,
                    "version_suffix": suffix,
                    "build_version": build,
                }
            ),
            encoding="utf-8",
        )

    def test_generates_version_build_manifest_for_known_assets(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            self._repo(root)
            installer = assets / (
                "VibeCAD-26.3.1-RC3-build17-Windows-x86_64-installer.exe"
            )
            installer.write_bytes(b"signed installer fixture")
            (assets / f"{installer.name}-SHA256.txt").write_text(
                "ignored checksum sidecar\n", encoding="utf-8"
            )
            appimage = assets / (
                "VibeCAD-26.3.1-RC3-build17-Linux-x86_64.AppImage"
            )
            appimage.write_bytes(b"appimage fixture")

            manifest = generate_manifest(
                root,
                assets,
                repository="10-X-eng/vibecad",
                published_at="2026-08-06T12:30:00-05:00",
            )

            self.assertEqual(manifest["schema"], 1)
            self.assertEqual(manifest["channel"], "preview")
            self.assertEqual(manifest["version"], "26.3.1-RC3")
            self.assertEqual(manifest["build"], 17)
            self.assertEqual(manifest["release_tag"], "v26.3.1-RC3-build17")
            self.assertEqual(manifest["published_at"], "2026-08-06T17:30:00Z")
            self.assertEqual(len(manifest["assets"]), 2)
            installer_entry = next(
                asset
                for asset in manifest["assets"]
                if asset["kind"] == "installer"
            )
            self.assertEqual(installer_entry["platform"], "windows")
            self.assertEqual(installer_entry["architecture"], "x86_64")
            self.assertEqual(
                installer_entry["sha256"],
                hashlib.sha256(b"signed installer fixture").hexdigest(),
            )
            self.assertNotIn("source_sha", manifest)

    def test_final_version_uses_stable_channel(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            self._repo(root, suffix="", build=2)
            (assets / "VibeCAD-26.3.1-build2-Windows-x86_64.7z").write_bytes(
                b"portable"
            )

            manifest = generate_manifest(
                root,
                assets,
                repository="10-X-eng/vibecad",
                published_at="2026-08-06T17:30:00Z",
            )

            self.assertEqual(manifest["channel"], "stable")

    def test_generates_macos_assets_for_both_release_architectures(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            self._repo(root)
            (assets / "VibeCAD-26.3.1-RC3-build17-macOS12-arm64.dmg").write_bytes(
                b"apple silicon dmg"
            )
            (assets / "VibeCAD-26.3.1-RC3-build17-macOS12-x86_64.dmg").write_bytes(
                b"intel dmg"
            )

            manifest = generate_manifest(
                root,
                assets,
                repository="10-X-eng/vibecad",
                published_at="2026-08-06T17:30:00Z",
            )

            macos_assets = {
                (asset["architecture"], asset["kind"])
                for asset in manifest["assets"]
                if asset["platform"] == "macos"
            }
            self.assertEqual(
                macos_assets,
                {("aarch64", "dmg"), ("x86_64", "dmg")},
            )

    def test_rejects_noncanonical_asset_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            self._repo(root)
            (assets / "VibeCAD-deadbeef1234-26.3.1-RC3-Windows-x86_64.7z").write_bytes(
                b"legacy"
            )

            with self.assertRaisesRegex(ValueError, "canonical basename"):
                generate_manifest(
                    root,
                    assets,
                    repository="10-X-eng/vibecad",
                    published_at="2026-08-06T17:30:00Z",
                )

    def test_rejects_naive_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            assets = root / "assets"
            assets.mkdir()
            self._repo(root)
            (assets / "VibeCAD-26.3.1-RC3-build17-Windows-x86_64.7z").write_bytes(
                b"portable"
            )

            with self.assertRaisesRegex(ValueError, "UTC offset"):
                generate_manifest(
                    root,
                    assets,
                    repository="10-X-eng/vibecad",
                    published_at="2026-08-06T17:30:00",
                )


if __name__ == "__main__":
    unittest.main()
