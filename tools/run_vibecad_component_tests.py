#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run standalone VibeCAD component tests in isolated pytest processes.

The component suites use their own top-level ``tests`` packages, so collecting
multiple suites in one pytest process causes import-name collisions.  Keeping
one process per suite preserves their installed layouts and still provides one
command for contributors.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "aero": Path("src/Mod/VibeCADAero/tests"),
    "print": Path("src/Mod/VibeCADPrint/tests"),
}


def run_suites(
    suite_names: Iterable[str],
    pytest_args: Sequence[str] = (),
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    """Run each requested suite separately and aggregate their exit status."""
    failed: list[str] = []
    for name in suite_names:
        suite = SUITES[name]
        print(f"\n==> VibeCAD {name} tests: {suite}", flush=True)
        command = [sys.executable, "-m", "pytest", "-q", str(suite), *pytest_args]
        completed = runner(command, cwd=REPO_ROOT, check=False)
        if completed.returncode:
            failed.append(name)

    if failed:
        print("\nFailed component suites: " + ", ".join(failed))
        return 1

    print("\nAll requested VibeCAD component suites passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        action="append",
        choices=tuple(SUITES),
        dest="suites",
        help="Run only this suite; repeat to select more than one",
    )
    args, pytest_args = parser.parse_known_args()
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    return run_suites(args.suites or SUITES, pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
