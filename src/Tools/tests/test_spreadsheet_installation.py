# SPDX-License-Identifier: LGPL-2.1-or-later
"""Installed Python tests must not shadow the native Spreadsheet extension."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("cmake"), "CMake is required")
class SpreadsheetInstallationTests(unittest.TestCase):
    def test_install_preserves_test_package_without_shadowing_native_module(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.20)\n'
                'project(SpreadsheetInstallTest NONE)\n'
                'include(AddFileDependencies)\n'
                f'include("{ROOT.as_posix()}/cMake/FreeCadMacros.cmake")\n'
                'set(BUILD_GUI ON)\n'
                # Exercise the actual resource/install rules, not native compilation.
                'function(add_subdirectory source)\n'
                '  if(NOT source STREQUAL "App" AND NOT source STREQUAL "Gui")\n'
                '    _add_subdirectory(${ARGV})\n'
                '  endif()\n'
                'endfunction()\n'
                f'add_subdirectory("{ROOT.as_posix()}/src/Mod/Spreadsheet" Spreadsheet)\n',
                encoding="utf-8",
            )
            build, install = project / "build", project / "install"
            for arguments in (
                ["-S", str(project), "-B", str(build)],
                ["--build", str(build)],
                ["--install", str(build), "--prefix", str(install)],
            ):
                result = subprocess.run(["cmake", *arguments], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source = ROOT / "src/Mod/Spreadsheet"
            for root in (build, install):
                module = root / "Mod/Spreadsheet"
                self.assertFalse((module / "__init__.py").exists(), "Shadows Spreadsheet.pyd")
                for relative in ("Init.py", "InitGui.py", "SpreadsheetTests/__init__.py",
                                 "SpreadsheetTests/TestVibeCADRibbonTools.py"):
                    self.assertEqual((module / relative).read_bytes(), (source / relative).read_bytes())
