# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the actual SheetMetal build/install rules without compiling FreeCAD."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("cmake"), "CMake is required")
class SheetMetalInstallationTests(unittest.TestCase):
    def test_install_preserves_geometry_modules_and_runtime_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            project = work / "project"
            project.mkdir()
            (project / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.20)\n'
                'project(SheetMetalInstallTest NONE)\n'
                'include(AddFileDependencies)\n'
                f'include("{ROOT.as_posix()}/cMake/FreeCadMacros.cmake")\n'
                'set(BUILD_TEST ON)\n'
                f'add_subdirectory("{ROOT.as_posix()}/src/Mod/SheetMetal" SheetMetal)\n',
                encoding="utf-8",
            )
            build, install = work / "build", work / "install"
            for arguments in (
                ["-S", str(project), "-B", str(build)],
                ["--build", str(build), "--parallel", "12"],
                ["--install", str(build), "--prefix", str(install)],
            ):
                result = subprocess.run(
                    ["cmake", *arguments], capture_output=True, text=True, check=False
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            source = ROOT / "src/Mod/SheetMetal"
            runtime = install / "Mod/SheetMetal"
            # These include the newer bend-cut dependency and both Unfold engines.
            for name in (
                "InitGui.py", "SheetMetalTools.py", "SheetMetalBaseCmd.py",
                "SheetMetalNewUnfolder.py", "SheetMetalUnfolder.py",
                "SheetMetalUnfoldCmd.py", "SheetMetalBendCuts.py", "LICENSE",
                "package.xml", "UPSTREAM.md", "SMTests/testUnfoldDocument.py",
                "SheetMetalMapping.py", "SMTests/testUnfoldMapping.py",
                "SheetMetalEditGeometry.py", "SMTests/testEditGeometry.py",
                "SheetMetalEditable.py", "SMTests/testEditableSheet.py",
                "SMTests/testProfileCuts.py",
                "SheetMetalPresentation.py", "SMTests/testPresentation.py",
                "SMTests/testSheetSelection.py",
                "SheetMetalOperations.py", "SMTests/testSheetOperations.py",
                "SheetMetalGui.py", "SMTests/testSheetGui.py",
                "SMTests/testSheetTree.py",
                "SMTests/testSheetHistory.py",
                "SMTests/testSheetHistoryStartup.py",
                "SheetMetalCutHistory.py", "SheetMetalCutGui.py", "SMTests/testSheetCutHistory.py",
                "SMTests/testSheetProfileHistory.py",
                "SheetMetalHistoryOperations.py", "SMTests/testSheetRibbonHistory.py",
                "SheetMetalNativeEdit.py", "SMTests/testSheetNativeEdit.py",
                "SheetMetalRMFGJobs.py", "SMTests/testRMFGJobs.py",
                "SheetMetalRMFGManufacturing.py", "SMTests/testRMFGManufacturing.py",
                "SheetMetalRMFGManufacturingGui.py", "SMTests/testRMFGManufacturingGui.py",
            ):
                with self.subTest(file=name):
                    self.assertEqual((runtime / name).read_bytes(), (source / name).read_bytes())
                    self.assertEqual((build / "Mod/SheetMetal" / name).read_bytes(),
                                     (source / name).read_bytes())
            for directory, suffix in (("icons", ".svg"), ("panels", ".ui"),
                                      ("translations", ".qm")):
                files = list((source / "Resources" / directory).glob("*" + suffix))
                self.assertTrue(files, directory)
                for file in files:
                    relative = file.relative_to(source)
                    self.assertEqual((runtime / relative).read_bytes(), file.read_bytes())
            package = ET.parse(runtime / "package.xml").getroot()
            self.assertEqual(package.find("{*}content/{*}workbench/{*}classname").text,
                             "SMWorkbench")


if __name__ == "__main__":
    unittest.main()
