#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Initialize git submodules. Extra worktrees often leave a non-git copy in
src/3rdParty/OndselSolver; git submodule update --init then refuses to clone."""

from __future__ import annotations

import argparse
import re
import shutil
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
    text = modules.read_text(encoding="utf-8")
    return [root / match for match in re.findall(r"(?m)^\s*path\s*=\s*(\S+)", text)]


def is_git_checkout(path: Path) -> bool:
    git_entry = path / ".git"
    return git_entry.is_file() or git_entry.is_dir()


def ensure_submodules(root: Path) -> None:
    for path in submodule_paths(root):
        if path.exists() and not is_git_checkout(path):
            print(
                f"removing stale non-git checkout {path.relative_to(root)} "
                "(git submodule update --init cannot clone into it)",
                file=sys.stderr,
            )
            shutil.rmtree(path)
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
