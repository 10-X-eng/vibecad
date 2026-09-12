#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Initialize submodules while preserving non-git copies from extra worktrees.

Copies are moved into .git-submodule-backups/<unique-name>/checkout before
Git runs. Backups remain available even if cloning fails; inspect and remove
them manually when their contents are no longer needed. Existing Git checkouts
are passed directly to git submodule update, preserving Git's dirty-file checks.
"""

from __future__ import annotations

import argparse
import tempfile
import subprocess
import sys
from pathlib import Path


def _git_run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, check=True, text=True)


git_run = _git_run


def submodule_paths(root: Path) -> list[Path]:
    modules = root / ".gitmodules"
    if not modules.is_file():
        return []
    result = subprocess.run(
        ["git", "config", "--null", "--file", str(modules),
         "--get-regexp", r"^submodule\..*\.path$"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode == 1:
        return []
    result.check_returncode()
    return [root / entry.split("\n", 1)[1]
            for entry in result.stdout.split("\0") if entry]


def is_git_checkout(path: Path) -> bool:
    git_entry = path / ".git"
    return git_entry.is_file() or git_entry.is_dir()


def ensure_submodules(root: Path) -> None:
    root = root.resolve()
    paths = submodule_paths(root)
    backup_root = root / ".git-submodule-backups"
    # Validate the entire list before moving anything. Git would reject these
    # paths, so repair must not touch them before invoking Git either.
    for path in paths:
        relative = path.relative_to(root)
        if (not relative.parts or ".." in relative.parts
                or relative.parts[0] in (".git", backup_root.name)):
            raise ValueError(f"Invalid submodule path: {path}")
        for candidate in (path, *path.parents):
            if candidate == root:
                break
            if candidate.is_symlink():
                raise ValueError(f"Submodule path traverses a symlink: {candidate}")
    if backup_root.is_symlink():
        raise ValueError(f"Submodule backup directory is a symlink: {backup_root}")
    for path in paths:
        if path.exists() and not is_git_checkout(path):
            backup_root.mkdir(exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix=path.name + "-", dir=backup_root)) / "checkout"
            # Rename on the same filesystem; a failed move must leave the
            # original intact. Never recursively delete or overwrite a copy.
            path.rename(backup)
            print(
                f"preserved non-git checkout {path.relative_to(root)} at {backup}; "
                "review this backup before removing it manually",
                file=sys.stderr,
            )
    git_run(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=root,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    ensure_submodules(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
