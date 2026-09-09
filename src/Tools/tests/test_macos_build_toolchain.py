# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RECIPE = REPO_ROOT / "package" / "rattler-build" / "recipe.yaml"
BUILD_SCRIPT = REPO_ROOT / "package" / "rattler-build" / "build.sh"
PRESET_SELECTOR = (
    REPO_ROOT
    / "package"
    / "rattler-build"
    / "scripts"
    / "select_cmake_preset.sh"
)


class TestMacOSBuildToolchain(unittest.TestCase):
    def test_macos_build_exports_flags_to_cmake(self) -> None:
        # Execute the complete recipe script, replacing external build/install
        # commands with stubs. CMake's stub starts a child shell so a shell-only
        # CXXFLAGS assignment cannot accidentally satisfy the assertion.
        stubs = r'''
function /usr/bin/curl() { :; }
hdiutil() { :; }
sudo() { :; }
diskutil() { :; }
mv() { :; }
cmake() { bash -c 'printf "CMAKE_FLAGS=%s\n" "${CXXFLAGS:-}"'; }
source "$1"
'''
        for platform in ("osx-arm64", "osx-64"):
            for initial_flags in (None, "-DEXISTING_FLAG"):
                with self.subTest(platform=platform, initial_flags=initial_flags):
                    with tempfile.TemporaryDirectory() as prefix:
                        env = os.environ.copy()
                        env.pop("HOST", None)
                        env.pop("CXXFLAGS", None)
                        if initial_flags is not None:
                            env["CXXFLAGS"] = initial_flags
                        env.update(
                            VIBECAD_TARGET_PLATFORM=platform,
                            CCACHE_DIR="",
                            PREFIX=prefix,
                        )
                        result = subprocess.run(
                            ["bash", "-e", "-c", stubs, "test-build", str(BUILD_SCRIPT)],
                            cwd=REPO_ROOT,
                            env=env,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        flags = [
                            line.split("=", 1)[1].split()
                            for line in result.stdout.splitlines()
                            if line.startswith("CMAKE_FLAGS=")
                        ]
                        self.assertEqual(len(flags), 3)
                        for child_flags in flags:
                            self.assertIn("-D_LIBCPP_DISABLE_AVAILABILITY", child_flags)
                            if initial_flags:
                                self.assertIn(initial_flags, child_flags)

    def test_build_setup_from_rattler_source_directory(self) -> None:
        # Rattler runs the recipe script from the copied source root, not
        # from the recipe directory. Execute setup before platform side effects
        # (driver installation, dependency patching, and compilation).
        setup = BUILD_SCRIPT.read_text(encoding="utf-8").split(
            "\nif [[ ${CMAKE_PRESET}", maxsplit=1
        )[0]
        for platform, preset in (
            ("osx-arm64", "conda-macos-release"),
            ("osx-64", "conda-macos-release"),
            ("linux-64", "conda-linux-release"),
        ):
            with self.subTest(platform=platform):
                env = os.environ.copy()
                env.pop("HOST", None)
                env["CCACHE_DIR"] = ""
                env["VIBECAD_TARGET_PLATFORM"] = platform
                result = subprocess.run(
                    ["bash", "-e", "-c", setup + '\nprintf "%s" "$CMAKE_PRESET"'],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, preset)

    def test_macos_uses_libcxx_with_standard_cxx20_stop_token(self) -> None:
        recipe = RECIPE.read_text(encoding="utf-8")

        self.assertIn(
            "- if: osx\n"
            "      then:\n"
            "        - compilers>=2,<3\n"
            "        - libcxx>=21,<22\n"
            "      else:\n"
            "        - compilers>=1.10,<1.11",
            recipe,
        )

        host_requirements = recipe.split("\n  host:\n", maxsplit=1)[1].split(
            "\n  run:\n", maxsplit=1
        )[0]
        self.assertIn(
            "- if: osx\n"
            "      then:\n"
            "        - libcxx>=21,<22",
            host_requirements,
        )

    def test_rattler_target_platform_selects_macos_preset(self) -> None:
        recipe = RECIPE.read_text(encoding="utf-8")
        build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "VIBECAD_TARGET_PLATFORM: ${{ target_platform }}",
            recipe,
        )
        self.assertIn("VIBECAD_TARGET_PLATFORM:-${HOST:-}", build_script)

        for platform in ("osx-arm64", "osx-64", "arm64-apple-darwin20.0.0"):
            with self.subTest(platform=platform):
                result = subprocess.run(
                    ["bash", str(PRESET_SELECTOR), platform],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(result.stdout.strip(), "conda-macos-release")


if __name__ == "__main__":
    unittest.main()
