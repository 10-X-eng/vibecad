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
INSTALLER_ROOT = SCRIPT_PATH.parent
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
        self.assertIn("!define APP_VERSION_RELEASE_RANK 400003", content)
        self.assertIn("!define APP_VERSION_ORDER_KNOWN 1", content)
        self.assertIn('!define APP_VERSION_REVISION "41234"', content)

    def test_release_rank_orders_supported_channels(self):
        dev, dev_known = MODULE.release_rank("dev")
        beta, beta_known = MODULE.release_rank("beta2")
        rc, rc_known = MODULE.release_rank("RC3")
        final, final_known = MODULE.release_rank("")

        self.assertTrue(all((dev_known, beta_known, rc_known, final_known)))
        self.assertLess(dev, beta)
        self.assertLess(beta, rc)
        self.assertLess(rc, final)

    def test_custom_suffix_remains_buildable_without_guessing_order(self):
        rank, known = MODULE.release_rank("enterprise-preview")
        self.assertEqual(rank, 0)
        self.assertFalse(known)

        content = MODULE.render_version_defines(
            ["26", "3", "1", "41234"],
            suffix="enterprise-preview",
            build="17",
            year=2026,
        )
        self.assertIn("!define APP_VERSION_ORDER_KNOWN 0", content)

    def test_manual_installs_share_the_clean_replacement_transaction(self):
        init = (INSTALLER_ROOT / "include" / "init.nsh").read_text(encoding="utf-8")
        install = (INSTALLER_ROOT / "setup" / "install.nsh").read_text(
            encoding="utf-8"
        )
        configure = (INSTALLER_ROOT / "setup" / "configure.nsh").read_text(
            encoding="utf-8"
        )

        self.assertIn('StrCpy $VibeCADUpdateMode "manual"', init)
        self.assertIn("Function SelectExistingVibeCADInstallMode", init)
        self.assertIn('StrCpy $VibeCADInstalledDisposition "upgrade"', init)
        self.assertIn('${orif} $VibeCADUpdateMode == "manual"', install)
        self.assertIn('Rename "$INSTDIR" "$VibeCADUpdateBackupDir"', install)
        self.assertIn('"UpdateVersion" "${APP_UPDATE_VERSION}"', configure)
        self.assertNotIn('$(AlreadyInstalled)', init)

    def test_in_app_updater_launches_normal_installer_without_flags(self):
        update_gui = (
            INSTALLER_ROOT.parents[1]
            / "src"
            / "Mod"
            / "VibeCAD"
            / "VibeCADUpdateGui.py"
        ).read_text(encoding="utf-8")
        helper = update_gui.split(
            "def _launch_windows_install_helper", 1
        )[1].split("def _launch_appimage_install_helper", 1)[0]

        self.assertIn("$vibecad | Wait-Process", helper)
        self.assertIn("Start-Process -FilePath $Installer", helper)
        self.assertIn('[IO.File]::WriteAllText($Started, "$PID")', helper)
        self.assertIn("wait_for_install_helper_start(started)", helper)
        self.assertLess(
            helper.index('[IO.File]::WriteAllText($Started, "$PID")'),
            helper.index("$vibecad | Wait-Process"),
        )
        self.assertNotIn("-ArgumentList", helper)
        self.assertNotIn("/S", helper)
        self.assertNotIn("/VIBECADUPDATE", helper)
        self.assertNotIn("/VIBECADINSTALLROOT", helper)

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
