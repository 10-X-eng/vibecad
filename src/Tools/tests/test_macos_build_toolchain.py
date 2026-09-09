# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RECIPE = REPO_ROOT / "package" / "rattler-build" / "recipe.yaml"
PIXI = REPO_ROOT / "pixi.toml"


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

    def test_pixi_macos_uses_libcxx_with_standard_cxx20_stop_token(self) -> None:
        pixi = PIXI.read_text(encoding="utf-8")

        self.assertIn('compilers = ">=1.10,<3"', pixi)
        self.assertNotIn('compilers = ">=1.10,<1.11"', pixi)
        for target in ("osx-64", "osx-arm64"):
            self.assertIn(
                f"[target.{target}.dependencies]\n"
                'compilers = ">=2,<3"\n'
                'libcxx = ">=21,<22"',
                pixi,
            )


if __name__ == "__main__":
    unittest.main()
