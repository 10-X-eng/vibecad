# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src" / "Tools"))

import ensure_git_submodules as helper  # noqa: E402

CMAKE_3RDPARTY = REPO_ROOT / "src" / "3rdParty" / "CMakeLists.txt"
PIXI = REPO_ROOT / "pixi.toml"
ASMT = (
    REPO_ROOT
    / "src"
    / "3rdParty"
    / "OndselSolver"
    / "OndselSolver"
    / "ASMTAssembly.h"
)


class TestOndselSubmoduleContract(unittest.TestCase):
    def test_cmake_requires_cancellation_api_not_just_cmakelists(self) -> None:
        text = CMAKE_3RDPARTY.read_text(encoding="utf-8")
        self.assertIn("setCancellationCheck", text)
        self.assertIn("setParallelExecutor", text)
        self.assertIn("git submodule update --init", text)
        self.assertIn("ASMTAssembly.h", text)

    def test_pixi_initialize_uses_ensure_helper(self) -> None:
        text = PIXI.read_text(encoding="utf-8")
        self.assertIn("ensure_git_submodules.py", text)

    def test_checked_out_ondsel_exposes_host_hooks(self) -> None:
        header = ASMT.read_text(encoding="utf-8")
        self.assertIn("setCancellationCheck", header)
        self.assertIn("setParallelExecutor", header)

    def test_helper_removes_stale_dir_without_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".gitmodules").write_text(
                '[submodule "src/3rdParty/OndselSolver"]\n'
                "\tpath = src/3rdParty/OndselSolver\n"
                "\turl = https://example.invalid/OndselSolver.git\n",
                encoding="utf-8",
            )
            stale = root / "src" / "3rdParty" / "OndselSolver"
            stale.mkdir(parents=True)
            (stale / "CMakeLists.txt").write_text("stale copy\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 0)

            helper.git_run = fake_run  # type: ignore[method-assign]
            try:
                helper.ensure_submodules(root)
            finally:
                helper.git_run = helper._git_run  # type: ignore[method-assign]
            self.assertFalse(stale.exists())
            self.assertTrue(
                any(c[:3] == ["git", "submodule", "update"] for c in calls)
            )


if __name__ == "__main__":
    unittest.main()
