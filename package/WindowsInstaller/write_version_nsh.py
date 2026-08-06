# SPDX-License-Identifier: LGPL-2.1-or-later
#
# this script is meant to be called by nsis installer scripts, it gets version information
# from freecad and writes version.nsh file in the directory the script is located at
import datetime
import os

def render_version_defines(version, *, suffix: str, build: str, year: int) -> str:
    # Keep the module reference local because importing FreeCAD mutates names in
    # the embedded interpreter's __main__ namespace.
    import re as regex

    if not regex.fullmatch(r"[A-Za-z0-9.-]*", suffix):
        raise ValueError(f"Unsafe VibeCAD version suffix: {suffix!r}")
    build_number = int(build)
    if build_number < 0:
        raise ValueError("VibeCAD build number must be non-negative")
    return f'''\
!define COPYRIGHT_YEAR {year}
!define APP_VERSION_MAJOR "{version[0]}"
!define APP_VERSION_MINOR "{version[1]}"
!define APP_VERSION_PATCH "{version[2]}"
!define APP_VERSION_SUFFIX "{suffix}"
!define APP_VERSION_BUILD {build_number}
!define APP_VERSION_REVISION "{version[3].split()[0]}"
'''


def main() -> None:
    # Importing FreeCAD mutates names in the embedded interpreter's __main__
    # namespace, so capture everything needed from standard-library modules
    # before loading the extension.
    year = datetime.date.today().year
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.nsh")
    import FreeCAD

    content = render_version_defines(
        FreeCAD.Version(),
        suffix=FreeCAD.ConfigGet("BuildVersionSuffix"),
        build=FreeCAD.ConfigGet("BuildVersion"),
        year=year,
    )

    with open(filepath, "w", encoding="utf-8") as file:
        file.writelines(content)


if __name__ == "__main__":
    main()
