# SPDX-License-Identifier: LGPL-2.1-or-later

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "vibecad-release.yml"
UPDATE_VALIDATION_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "vibecad-update-validate.yml"
)


def _job_source(workflow: str, job: str) -> str:
    match = re.search(
        rf"^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise AssertionError(f"Release workflow has no {job!r} job.")
    return match.group("body")


class TestReleaseWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_preview_platform_jobs_are_best_effort(self) -> None:
        expected = "continue-on-error: ${{ needs.prepare.outputs.prerelease == 'true' }}"
        for job in ("linux", "windows", "macos"):
            with self.subTest(job=job):
                self.assertIn(expected, _job_source(self.workflow, job))

        release = _job_source(self.workflow, "release")
        self.assertIn("always() && needs.prepare.result == 'success'", release)
        self.assertIn("needs.prepare.outputs.prerelease == 'true'", release)
        self.assertIn("pattern: vibecad-*", release)
        self.assertIn("merge-multiple: true", release)
        self.assertIn("Omitting orphan checksum from preview release", release)
        self.assertIn("Omitting unchecked package from preview release", release)

        linux = _job_source(self.workflow, "linux")
        upload = linux.split("- name: Upload Linux release assets", 1)[1]
        self.assertIn("if: always()", upload)

    def test_release_builds_both_macos_architectures(self) -> None:
        macos = _job_source(self.workflow, "macos")
        self.assertIn("runner: macos-15", macos)
        self.assertIn("arch: arm64", macos)
        self.assertIn("runner: macos-15-intel", macos)
        self.assertIn("arch: x86_64", macos)
        self.assertIn("package/rattler-build/osx/VibeCAD-*.dmg", macos)

    def test_stable_release_requires_every_platform(self) -> None:
        release = _job_source(self.workflow, "release")
        for required_pattern in (
            "*-Linux-*.AppImage",
            "*-Linux-*.AppImage.zsync",
            "*-Linux-*.deb",
            "*-Windows-x86_64-installer.exe",
            "*-macOS*-arm64.dmg",
            "*-macOS*-x86_64.dmg",
        ):
            with self.subTest(pattern=required_pattern):
                self.assertIn(required_pattern, release)
        self.assertIn("-o -name '*.dmg'", release)

    def test_windows_update_launcher_is_exercised_on_a_windows_runner(self) -> None:
        workflow = UPDATE_VALIDATION_WORKFLOW.read_text(encoding="utf-8")
        windows = _job_source(workflow, "validate-windows-launch")
        self.assertIn("runs-on: windows-2022", windows)
        self.assertIn(
            "test_windows_detached_helper_executes_powershell_after_parent_exits",
            windows,
        )
        self.assertIn("test_spawn_detached_helper_outlives_the_parent_process", windows)
        self.assertIn(
            "test_in_app_updater_launches_normal_installer_without_flags",
            windows,
        )


if __name__ == "__main__":
    unittest.main()
