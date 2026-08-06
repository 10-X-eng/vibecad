# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "package"
    / "WindowsInstaller"
    / "write_version_nsh.py"
)
SPEC = importlib.util.spec_from_file_location("write_version_nsh", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TestWindowsInstallerVersion(unittest.TestCase):
    def test_renders_canonical_version_build_defines(self):
        content = MODULE.render_version_defines(
            ["26", "3", "1", "41234"],
            suffix="RC3",
            build="17",
            year=2026,
        )

        self.assertIn('!define APP_VERSION_SUFFIX "RC3"', content)
        self.assertIn("!define APP_VERSION_BUILD 17", content)
        self.assertIn('!define APP_VERSION_REVISION "41234"', content)

    def test_rejects_negative_build(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            MODULE.render_version_defines(
                ["26", "3", "1", "41234"],
                suffix="RC3",
                build="-1",
                year=2026,
            )

    def test_rejects_unsafe_suffix(self):
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            MODULE.render_version_defines(
                ["26", "3", "1", "41234"],
                suffix='RC3"!include bad.nsh',
                build="1",
                year=2026,
            )


if __name__ == "__main__":
    unittest.main()
