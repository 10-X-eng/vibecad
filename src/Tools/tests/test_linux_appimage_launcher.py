# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CREATE_BUNDLE = REPO_ROOT / "package" / "rattler-build" / "linux" / "create_bundle.sh"
TRACKED_LAUNCHER = REPO_ROOT / "package" / "rattler-build" / "linux" / "AppDir" / "AppRun"
EXCLUDE_HOST_GRAPHICS_LIBRARIES = (
    REPO_ROOT
    / "package"
    / "rattler-build"
    / "scripts"
    / "exclude_appimage_host_graphics_libraries.sh"
)


def generated_launcher() -> str:
    match = re.search(
        r"cat > AppDir/AppRun <<'EOF'\n(?P<body>.*?)\nEOF",
        CREATE_BUNDLE.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError("Linux bundle script has no AppRun heredoc")
    return match.group("body") + "\n"


class TestLinuxAppImageLauncher(unittest.TestCase):
    def test_bundled_libdrm_libraries_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prefix = Path(temp_dir) / "usr"
            library_dir = prefix / "lib"
            library_dir.mkdir(parents=True)

            (library_dir / "libdrm.so.2.125.0").touch()
            (library_dir / "libdrm.so.99.999.0").touch()
            (library_dir / "libdrm_futuregpu.so.42").touch()
            (library_dir / "libdrm.so.2").symlink_to("libdrm.so.2.125.0")
            (library_dir / "libdrm_amdgpu.so.1.125.0").touch()
            (library_dir / "libdrm_amdgpu.so.1").symlink_to(
                "libdrm_amdgpu.so.1.125.0"
            )
            preserved_library = library_dir / "libQt6Core.so.6.8.3"
            preserved_library.touch()

            subprocess.run(
                [str(EXCLUDE_HOST_GRAPHICS_LIBRARIES), str(prefix)],
                check=True,
            )

            self.assertEqual(list(library_dir.glob("libdrm*.so*")), [])
            self.assertTrue(preserved_library.exists())

    def test_generated_launcher_matches_tracked_launcher(self) -> None:
        self.assertEqual(
            generated_launcher(),
            TRACKED_LAUNCHER.read_text(encoding="utf-8"),
        )

    def test_amd_host_library_precedes_bundled_libdrm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            appdir = root / "AppDir"
            binary = appdir / "usr" / "bin" / "freecad"
            vendor = root / "drm" / "card0" / "device" / "vendor"
            host_library = root / "libdrm_amdgpu.so.1"
            output = root / "environment.txt"
            fake_bin = root / "bin"

            binary.parent.mkdir(parents=True)
            vendor.parent.mkdir(parents=True)
            fake_bin.mkdir()
            launcher = appdir / "AppRun"
            launcher.write_text(generated_launcher(), encoding="utf-8")
            binary.write_text(
                '#!/bin/sh\nprintf "%s\\n" "${LD_PRELOAD-}" > "$VIBECAD_TEST_OUTPUT"\n',
                encoding="utf-8",
            )
            (fake_bin / "readlink").write_text(
                '#!/bin/sh\nif [ "$1" = "-f" ]; then '
                'cd "$(dirname "$2")" && printf "%s/%s\\n" "$PWD" "$(basename "$2")"; '
                "else /usr/bin/readlink \"$@\"; fi\n",
                encoding="utf-8",
            )
            vendor.write_text("0x1002\n", encoding="utf-8")
            host_library.touch()
            for executable in (launcher, binary, fake_bin / "readlink"):
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "LD_PRELOAD": "existing.so",
                    "VIBECAD_DRM_ROOT": str(root / "drm"),
                    "VIBECAD_LIBDRM_AMDGPU": str(host_library),
                    "VIBECAD_TEST_OUTPUT": str(output),
                }
            )
            subprocess.run([str(launcher)], env=environment, check=True)

            self.assertEqual(
                output.read_text(encoding="utf-8").strip(),
                f"{host_library}:existing.so",
            )


if __name__ == "__main__":
    unittest.main()
