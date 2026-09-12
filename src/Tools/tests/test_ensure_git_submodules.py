# SPDX-License-Identifier: LGPL-2.1-or-later

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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
            backups = list((root / ".git-submodule-backups").glob("*/checkout/CMakeLists.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "stale copy\n")


class TestPreserveSubmoduleFiles(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "repo"
        self.root.mkdir()

    def configure(self, relative):
        (self.root / ".gitmodules").write_text(
            '[submodule "solver"]\n\tpath = ' + relative + '\n\turl = missing\n'
        )

    def test_failed_clone_keeps_every_stale_file_recoverable(self):
        self.configure("solver")
        stale = self.root / "solver"
        stale.mkdir()
        (stale / "my_changes.cpp").write_text("user edits")
        (stale / ".hidden").write_bytes(b"\x00\x01")
        with patch.object(helper, "git_run", side_effect=subprocess.CalledProcessError(1, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                helper.ensure_submodules(self.root)
        backups = list((self.root / ".git-submodule-backups").glob("*/checkout"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "my_changes.cpp").read_text(), "user edits")
        self.assertEqual((backups[0] / ".hidden").read_bytes(), b"\x00\x01")

    def test_paths_outside_repository_are_rejected_before_mutation(self):
        self.configure("../outside")
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "keep").write_text("untouched")
        with patch.object(helper, "git_run") as git:
            with self.assertRaises(ValueError):
                helper.ensure_submodules(self.root)
            git.assert_not_called()
        self.assertEqual((outside / "keep").read_text(), "untouched")

    def test_symlinked_parent_is_not_followed(self):
        self.configure("linked/solver")
        outside = self.root.parent / "outside"
        (outside / "solver").mkdir(parents=True)
        (outside / "solver" / "keep").write_text("untouched")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        with patch.object(helper, "git_run") as git:
            with self.assertRaises(ValueError):
                helper.ensure_submodules(self.root)
            git.assert_not_called()
        self.assertEqual((outside / "solver" / "keep").read_text(), "untouched")

    def test_paths_with_spaces_follow_git_config_parsing(self):
        self.configure('"third party/solver"')
        self.assertEqual(helper.submodule_paths(self.root), [self.root / "third party/solver"])

    def test_existing_git_checkout_is_not_moved(self):
        self.configure("solver")
        checkout = self.root / "solver"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: ../metadata\n")
        (checkout / "my_changes.cpp").write_text("user edits")
        with patch.object(helper, "git_run"):
            helper.ensure_submodules(self.root)
        self.assertEqual((checkout / "my_changes.cpp").read_text(), "user edits")
        self.assertFalse((self.root / ".git-submodule-backups").exists())

    def test_real_linked_worktree_repairs_stale_copy_without_losing_edits(self):
        def git(directory, *args):
            return subprocess.run(
                ["git", "-c", "protocol.file.allow=always", *args], cwd=directory,
                check=True, text=True, capture_output=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                     "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"},
            )
        source = self.root.parent / "solver-origin"
        source.mkdir()
        git(source, "init")
        (source / "upstream.txt").write_text("recorded revision")
        git(source, "add", ".")
        git(source, "commit", "-m", "Solver fixture")
        expected = git(source, "rev-parse", "HEAD").stdout.strip()
        git(self.root, "init")
        git(self.root, "submodule", "add", str(source), "third party/solver")
        git(self.root, "commit", "-am", "Parent fixture")
        checkout = self.root.parent / "linked-worktree"
        git(self.root, "worktree", "add", "--detach", str(checkout))
        stale = checkout / "third party" / "solver"
        stale.mkdir(parents=True, exist_ok=True)
        (stale / "local.cpp").write_text("uncommitted local work")
        with patch.object(helper, "git_run", side_effect=lambda argv, cwd: git(cwd, *argv[1:])):
            helper.ensure_submodules(checkout)
        self.assertEqual(git(stale, "rev-parse", "HEAD").stdout.strip(), expected)
        self.assertEqual((stale / "upstream.txt").read_text(), "recorded revision")
        backups = list((checkout / ".git-submodule-backups").glob("*/checkout/local.cpp"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "uncommitted local work")
        (stale / "upstream.txt").write_text("new local edit")
        with patch.object(helper, "git_run", side_effect=lambda argv, cwd: git(cwd, *argv[1:])):
            helper.ensure_submodules(checkout)
        self.assertEqual((stale / "upstream.txt").read_text(), "new local edit")
        self.assertEqual(list((checkout / ".git-submodule-backups").glob("*/checkout/local.cpp")), backups)


if __name__ == "__main__":
    unittest.main()
