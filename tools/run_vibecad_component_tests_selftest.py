#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Self-test the isolated VibeCAD component test runner."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from types import SimpleNamespace

import run_vibecad_component_tests as component_tests


def main() -> int:
    calls: list[tuple[list[str], object, bool]] = []
    return_codes = iter((0, 3))

    def fake_runner(command: list[str], *, cwd: object, check: bool) -> SimpleNamespace:
        calls.append((command, cwd, check))
        return SimpleNamespace(returncode=next(return_codes))

    output = io.StringIO()
    with redirect_stdout(output):
        result = component_tests.run_suites(
            ("aero", "print"),
            pytest_args=("-x",),
            runner=fake_runner,
        )

    scenarios = {
        "each_suite_uses_a_separate_process": len(calls) == 2,
        "failures_do_not_hide_later_suites": result == 1 and len(calls) == 2,
        "commands_use_the_current_python": all(
            command[:3] == [component_tests.sys.executable, "-m", "pytest"]
            for command, _cwd, _check in calls
        ),
        "commands_target_one_suite_each": [
            command[-2:] for command, _cwd, _check in calls
        ]
        == [
            [str(component_tests.SUITES["aero"]), "-x"],
            [str(component_tests.SUITES["print"]), "-x"],
        ],
        "commands_run_from_repository_root": all(
            cwd == component_tests.REPO_ROOT for _command, cwd, _check in calls
        ),
        "subprocess_errors_are_return_codes": all(
            check is False for _command, _cwd, check in calls
        ),
        "failed_suites_are_reported": "Failed component suites: print"
        in output.getvalue(),
    }
    failed = [name for name, ok in scenarios.items() if not ok]
    if failed:
        print("failed scenarios: " + ", ".join(failed))
        return 1

    print(f"{len(scenarios)} component test runner scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
