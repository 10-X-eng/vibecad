# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run SheetMetal regressions in a private Linux GUI, never an existing instance.

Invoke with the build's dependency environment active. No process timeout is
used. The private GUI exits after the tests; artifacts remain in --output.
"""

import argparse
import os
from pathlib import Path
import subprocess


MACRO = '''import os, pathlib, sys, unittest
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore
output = pathlib.Path(os.environ["VIBECAD_TEST_OUTPUT"])
build = pathlib.Path(os.environ["VIBECAD_TEST_BUILD"])
assert os.getpid() == int(os.environ["VIBECAD_TEST_PID"])
assert pathlib.Path("/proc/self/exe").resolve() == build / "bin/FreeCAD"
assert QtCore.QDir.tempPath() == os.environ["TMPDIR"]
assert App.ConfigGet("UserAppData").startswith(os.environ["FREECAD_USER_DATA"])
assert not App.listDocuments(), "The test process must start empty"
for relative in ("Mod/SheetMetal", "Mod/SendCutSendPresets",
                 "Mod/SendCutSendPresets/tests"):
    sys.path.insert(0, str(build / relative))
import PartGui, SheetMetalTools, SheetMetalNewUnfolder
assert pathlib.Path(PartGui.__file__) == build / "Mod/Part/PartGui.so"
for module in (SheetMetalTools, SheetMetalNewUnfolder):
    assert pathlib.Path(module.__file__).parent == build / "Mod/SheetMetal"

class Runner(QtCore.QObject):
    requested = QtCore.Signal()
    def __init__(self):
        super().__init__()
        self.requested.connect(self.run, QtCore.Qt.QueuedConnection)
    def run(self):
        try:
            names = os.environ["VIBECAD_TEST_NAMES"].split(",")
            suite = unittest.defaultTestLoader.loadTestsFromNames(names)
            with (output / "result.log").open("w") as stream:
                result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
            (output / "status").write_text(str(result.wasSuccessful()))
        finally:
            assert os.getpid() == int(os.environ["VIBECAD_TEST_PID"])
            Gui.getMainWindow().close()
runner = Runner()
runner.requested.emit()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="New directory for the private profile and logs")
    parser.add_argument("tests", nargs="*", default=[
        "gui_preset_update", "SMTests.testUnfoldDocument", "SMTests.testUnfoldMapping",
        "SMTests.testEditGeometry",
        "SMTests.testLiveSheetArtifacts",
        "SMTests.testEditableSheet",
        "SMTests.testSheetPreparation",
        "SMTests.testProfileCuts",
        "SMTests.testPresentation",
        "SMTests.testSheetSelection",
        "SMTests.testSheetOperations",
        "SMTests.testSheetGui",
        "SMTests.testSheetTree",
        "SMTests.testSheetHistory",
        "SMTests.testSheetCutHistory",
        "SMTests.testSheetProfileHistory",
        "SMTests.testSheetRibbonHistory",
        "SMTests.testSheetNativeInspect",
        "SMTests.testSheetNativeView",
        "SMTests.testSheetNativeEdit",
        "SMTests.testSheetNativeDispatch",
        "SMTests.testSheetNativeRecovery",
        "SMTests.testSheetNativeCreation",
        "SMTests.testSheetSourceFeatures",
        "SMTests.testSheetFoldSource",
        "SMTests.testSheetReferenceFaces",
        "SMTests.testSheetSourceCreation",
        "SMTests.testSheetSourceForms",
        "SMTests.testRMFGNativeConnection",
        "SMTests.testRMFGExport",
        "SMTests.testRMFGManufacturingGui",
        "SMTests.testRMFGNativeManufacturing",
        "SMTests.testRMFGSavedJobsGui",
        "SMTests.testSheetRecomputeOrigin",
        "SMTests.testSheetLegacyOperations",
    ])
    args = parser.parse_args()
    build = args.build.resolve()
    cache = (build / "CMakeCache.txt").read_text()
    required = ["CMAKE_BUILD_TYPE:STRING=Release", "FREECAD_WARN_ERROR:BOOL=ON"]
    required.extend(f"FREECAD_USE_SANITIZER_{name}:BOOL=OFF"
                    for name in ("ASAN", "LSAN", "MSAN", "TSAN", "UBSAN"))
    for setting in required:
        if setting not in cache:
            parser.error(f"Strict Release setting missing: {setting}")
    for relative in ("bin/FreeCAD", "Mod/Part/PartGui.so", "lib/libFreeCADGui.so",
                     "Mod/SheetMetal/SheetMetalTools.py"):
        if not (build / relative).is_file():
            parser.error(f"Missing built file: {relative}")
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("tmp", "data", "config", "runtime", "home"):
        (output / name).mkdir(mode=0o700)
    macro = output / "sheetmetal.FCMacro"
    macro.write_text(MACRO)
    env = os.environ.copy()
    env.update(
        TMPDIR=str(output / "tmp"), FREECAD_USER_HOME=str(output / "home"),
        FREECAD_USER_DATA=str(output / "data"), FREECAD_USER_TEMP=str(output / "tmp"),
        XDG_CONFIG_HOME=str(output / "config"), XDG_DATA_HOME=str(output / "data"),
        XDG_RUNTIME_DIR=str(output / "runtime"), QT_QPA_PLATFORM="xcb",
        VIBECAD_TEST_OUTPUT=str(output), VIBECAD_TEST_BUILD=str(build),
        VIBECAD_TEST_NAMES=",".join(args.tests),
    )
    # A copied build retains its original CMake RUNPATH entries. Resolve every
    # module dependency from this runtime before those paths, otherwise Python
    # and a GUI module can load different copies of the same application module
    # and disagree about registered C++ types.
    module_library_dirs = sorted({
        str(library.parent) for library in (build / "Mod").rglob("*.so")
    })
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (
        str(build / "lib"), *module_library_dirs, env.get("LD_LIBRARY_PATH"),
    )))
    for name in ("ASAN_OPTIONS", "LSAN_OPTIONS", "UBSAN_OPTIONS", "LD_PRELOAD",
                 "SESSION_MANAGER", "DBUS_SESSION_BUS_ADDRESS"):
        env.pop(name, None)
    command = [
        "xvfb-run", "-a", "bash", "-c", 'export VIBECAD_TEST_PID=$$; exec "$@"',
        "sheetmetal-test", str(build / "bin/FreeCAD"), "-u", str(output / "user.cfg"),
        "-s", str(output / "system.cfg"), "-M", str(build / "Mod/Part"), str(macro),
    ]
    with (output / "launch.log").open("w") as log:
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    (output / "exit").write_text(str(result.returncode))
    print(f"SheetMetal test artifacts: {output}", flush=True)
    status = output / "status"
    return 0 if result.returncode == 0 and status.is_file() and status.read_text() == "True" else 1


if __name__ == "__main__":
    raise SystemExit(main())
