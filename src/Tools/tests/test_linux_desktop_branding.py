# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise the actual AppDir branding block and Debian package contents."""

import configparser
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "package/rattler-build/linux/create_bundle.sh"
DEB = ROOT / "package/linux/build_deb_from_appdir.sh"
ICON = ROOT / "src/Gui/Icons/vibecad.svg"


class TestLinuxDesktopBranding(unittest.TestCase):
    def assert_desktop(self, path, executable):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path)
        desktop = parser["Desktop Entry"]
        self.assertEqual(desktop["Name"], "VibeCAD")
        self.assertEqual(desktop["Icon"], "vibecad")
        self.assertEqual(desktop["StartupWMClass"], "VibeCAD")
        self.assertEqual(desktop["Exec"], executable + " %F")
        self.assertIn("application/x-extension-fcstd;", desktop["MimeType"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux AppDir packaging")
    def test_appdir_uses_vibecad_assets(self):
        text = BUNDLE.read_text(encoding="utf-8")
        block = text.split('echo -e "\\nCopying Icon and Desktop file"', 1)[1]
        block = block.split("# Remove __pycache__", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "AppDir/usr"
            desktops = prefix / "share/applications"
            icons = prefix / "share/icons/hicolor/scalable/apps"
            desktops.mkdir(parents=True)
            icons.mkdir(parents=True)
            (desktops / "org.freecad.FreeCAD.desktop").write_text(
                "[Desktop Entry]\nName=FreeCAD\nExec=FreeCAD\nIcon=org.freecad.FreeCAD\n"
            )
            (icons / "org.freecad.FreeCAD.svg").write_text("old logo")
            subprocess.run(
                ["bash", "-e", "-c", 'repo_root="$1"; conda_env=AppDir/usr\n' + block, "branding", str(ROOT)],
                cwd=root, check=True,
            )
            self.assert_desktop(root / "AppDir/vibecad.desktop", "AppRun - --single-instance")
            self.assertEqual((root / "AppDir/vibecad.svg").read_bytes(), ICON.read_bytes())
            self.assertEqual(list((root / "AppDir").glob("*.desktop")), [root / "AppDir/vibecad.desktop"])

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("dpkg-deb"), "Requires Linux dpkg-deb")
    def test_debian_package_has_correct_icon_even_with_an_older_appdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            appdir = root / "AppDir"
            appdir.mkdir()
            launcher = appdir / "AppRun"
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            (appdir / "org.freecad.FreeCAD.svg").write_text("old logo")
            output = root / "output"
            subprocess.run([
                "bash", str(DEB), "--appdir", str(appdir), "--output-dir", str(output),
                "--version", "26.3.1", "--arch", "amd64",
            ], check=True, capture_output=True, text=True)
            extracted = root / "extracted"
            subprocess.run(["dpkg-deb", "-x", str(next(output.glob("*.deb"))), str(extracted)], check=True)
            self.assert_desktop(extracted / "usr/share/applications/vibecad.desktop", "vibecad")
            self.assertEqual(
                (extracted / "usr/share/icons/hicolor/scalable/apps/vibecad.svg").read_bytes(),
                ICON.read_bytes(),
            )

    def test_application_desktop_id_matches_the_installed_entry(self):
        source = (ROOT / "src/Main/MainGui.cpp").read_text(encoding="utf-8")
        identifier = re.search(r'Config\(\)\["DesktopFileName"\] = "([^"]+)"', source).group(1)
        self.assertEqual(identifier, "vibecad")

    @unittest.skipUnless(shutil.which("cmake") and shutil.which("ninja"), "Requires CMake and Ninja")
    def test_native_install_provides_the_same_desktop_id_and_icon(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/Tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "src/Tools/freecad-thumbnailer.in", root / "src/Tools/freecad-thumbnailer.in")
            (root / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.16)\n'
                'project(DesktopBranding LANGUAGES NONE)\n'
                'set(CMAKE_INSTALL_LIBDIR lib)\n'
                f'add_subdirectory("{ROOT.as_posix()}/src/XDGData" xdg)\n'
            )
            build = root / "build"
            installed = root / "installed"
            subprocess.run(["cmake", "-G", "Ninja", "-S", str(root), "-B", str(build),
                            f"-DCMAKE_INSTALL_PREFIX={installed}"], check=True)
            subprocess.run(["cmake", "--install", str(build)], check=True)
            self.assert_desktop(installed / "share/applications/vibecad.desktop", "FreeCAD - --single-instance")
            legacy = configparser.ConfigParser(interpolation=None)
            legacy.read(installed / "share/applications/org.freecad.FreeCAD.desktop")
            self.assertEqual(legacy["Desktop Entry"]["NoDisplay"], "true")
            self.assertEqual(legacy["Desktop Entry"]["Name"], "VibeCAD")
            self.assertEqual(
                (installed / "share/icons/hicolor/scalable/apps/vibecad.svg").read_bytes(),
                ICON.read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
