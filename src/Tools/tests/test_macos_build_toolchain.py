# SPDX-License-Identifier: LGPL-2.1-or-later

import subprocess
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
