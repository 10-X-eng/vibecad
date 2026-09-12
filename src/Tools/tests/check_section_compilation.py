# SPDX-License-Identifier: LGPL-2.1-or-later
"""Compile section-view regression probes with an existing Ninja build's flags.

Usage: python src/Tools/tests/check_section_compilation.py BUILD_DIR --compiler clang++
Outputs go into a temporary directory; the supplied build is never modified.
The build must be configured from this checkout with dependencies/generated headers
available. --source-dir can instead check another checkout of the same baseline.
"""

import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    build = args.build_dir.resolve()
    source = args.source_dir.resolve()
    cache = (build / "CMakeCache.txt").read_text()
    old_source = next(line.split("=", 1)[1] for line in cache.splitlines()
                      if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL="))
    failures = 0
    with tempfile.TemporaryDirectory(prefix="section-compile-") as temporary:
        for directory, target, filename in (
            ("src/Mod/Part/Gui", "PartGui", "AppPartGui.cpp"),
            ("src/Mod/Part/App", "Part", "SectionGeometry.cpp"),
            ("src/Mod/Part/App", "Part", "GizmoHelper.cpp"),
            ("src/Gui", "FreeCADGui", "NaviCube.cpp"),
            ("src/Gui", "FreeCADGui", "TaskView/TaskView.cpp"),
        ):
            obj = f"{directory}/CMakeFiles/{target}.dir/{filename}.o"
            commands = subprocess.check_output(
                ["ninja", "-C", str(build), "-t", "commands", obj], text=True)
            command = shlex.split(commands.splitlines()[-1])
            # Ninja may prefix the compiler with ccache.
            flags = command[2:] if Path(command[0]).name == "ccache" else command[1:]
            cleaned = []
            iterator = iter(flags)
            for flag in iterator:
                if flag in ("-o", "-MF", "-MT", "-MQ"):
                    next(iterator)
                elif flag in ("-c", "-MD", "-MMD") or flag.endswith("/" + filename):
                    continue
                else:
                    # Keep generated headers in the existing build. Prefer the
                    # checked source, retaining original dependency include paths.
                    if flag.startswith("-I" + old_source + "/src"):
                        cleaned.append(flag.replace(old_source, str(source), 1))
                    cleaned.append(flag)
            production = source / directory / filename
            probe = Path(temporary) / filename
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text(f'#include "{production.as_posix()}"\n' + (
                "// MSVC eagerly instantiates this exported nested destructor.\n"
                "// Force the same completeness requirement on other compilers.\n"
                "struct SectionFaceDrillerProbe : Part::FaceMakerBullseye {\n"
                "    static void destroy(FaceDriller* value) { delete value; }\n"
                "};\n"
                if filename == "SectionGeometry.cpp" else ""))
            # The inline body above is enough to instantiate the destructor.
            result = subprocess.run(
                [args.compiler, *cleaned, "-Werror",
                 "-c", str(probe), "-o", str(Path(temporary) / (filename + ".o"))],
                cwd=build)
            print(f"{filename}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
            failures += result.returncode != 0
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
