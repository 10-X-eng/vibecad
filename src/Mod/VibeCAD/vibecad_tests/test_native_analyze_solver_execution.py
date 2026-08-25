# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    _prepare_calculix,
    _require_meaningful_result_state,
    _require_no_ignored_elmer_constraints,
)
from VibeCADNativeAnalyzeSolverExecutionProcess import run_solver_processes
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    analyze_solver_execution_capability_definition,
)
from VibeCADNativeBackground import NativeBackgroundCancelled


def _program(path: Path, source: str) -> tuple[str, tuple[str, ...]]:
    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    return sys.executable, (str(script),)


def test_solver_execution_schema_is_one_sharp_background_operation() -> None:
    definition = analyze_solver_execution_capability_definition()
    assert definition.name == "analyze.solver_execution"
    assert tuple(variant.operation for variant in definition.variants) == ("run",)
    variant = definition.variants[0]
    assert variant.background_required
    assert variant.transaction_behavior == "background"
    assert set(variant.parameters["properties"]) == {"target", "timeout_seconds"}


def test_process_sequence_is_exact_bounded_and_shell_free(tmp_path: Path) -> None:
    first = _program(
        tmp_path / "first",
        "from pathlib import Path\n"
        'Path("first.out").write_text("first", encoding="utf-8")\n',
    )
    second = _program(
        tmp_path / "second",
        "import os\n"
        "from pathlib import Path\n"
        'assert os.environ["SAFE_VALUE"] == "exact"\n'
        'assert Path("first.out").is_file()\n'
        'Path("second.out").write_text("second", encoding="utf-8")\n',
    )
    progress = []

    result = run_solver_processes(
        (first, second),
        working_directory=str(tmp_path),
        environment={**os.environ, "SAFE_VALUE": "exact"},
        timeout_seconds=5,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
        backend="Test",
    )

    assert [stage["exit_code"] for stage in result] == [0, 0]
    assert (tmp_path / "second.out").read_text(encoding="utf-8") == "second"
    assert progress[-1] == (84, "Test result artifacts ready")


def test_process_sequence_cooperatively_terminates_on_cancel(tmp_path: Path) -> None:
    program = _program(tmp_path / "slow", "import time\ntime.sleep(30)\n")
    cancelled = threading.Event()

    def trigger() -> None:
        time.sleep(0.15)
        cancelled.set()

    threading.Thread(target=trigger, daemon=True).start()
    with pytest.raises(NativeBackgroundCancelled):
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=cancelled.is_set,
            progress=lambda _percent, _message: None,
            backend="Test",
        )


def test_process_failure_returns_only_bounded_tail(tmp_path: Path) -> None:
    program = _program(
        tmp_path / "fail",
        'import sys\nsys.stdout.write("precise failure")\nraise SystemExit(7)\n',
    )

    with pytest.raises(NativeAnalyzeError, match="code 7: precise failure"):
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="Test",
        )


def test_openfoam_failure_reports_the_fatal_reason_not_the_stack(tmp_path: Path) -> None:
    program = _program(
        tmp_path / "foam-fail",
        'print("banner")\n'
        'print("--> FOAM FATAL ERROR:")\n'
        'print("No coarse levels created; refine the volume mesh.")\n'
        'print("From function Foam::GAMGSolver")\n'
        'print("stack detail")\n'
        "raise SystemExit(1)\n",
    )

    with pytest.raises(NativeAnalyzeError) as raised:
        run_solver_processes(
            (program,),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="Openfoam",
        )

    message = str(raised.value)
    assert "No coarse levels created; refine the volume mesh." in message
    assert "stack detail" not in message


def test_solver_execution_rejects_ignored_elmer_constraints() -> None:
    tool = type(
        "ElmerTool",
        (),
        {"ignored_constraints": (type("Constraint", (), {"Label": "Fixed end"})(),)},
    )()

    with pytest.raises(NativeAnalyzeError, match="Fixed end"):
        _require_no_ignored_elmer_constraints(tool)


def test_calculix_prerequisite_failure_is_provider_actionable() -> None:
    class Tool:
        def prepare(self) -> None:
            raise RuntimeError(
                "CalculiX prerequisites failed:\n"
                "Thermomechanical analysis: No initial temperature defined.\n"
            )

    with pytest.raises(NativeAnalyzeError) as raised:
        _prepare_calculix(Tool())

    assert raised.value.error_code == "NATIVE_ANALYZE_SOLVER_NOT_READY"
    assert str(raised.value) == (
        "CalculiX prerequisites are incomplete: "
        "Thermomechanical analysis: No initial temperature defined."
    )


def test_solver_execution_rejects_empty_result_data() -> None:
    with pytest.raises(NativeAnalyzeError, match="no result fields"):
        _require_meaningful_result_state(
            {"result_kind": "pipeline", "field_count": 0, "data_available": False}
        )

    _require_meaningful_result_state(
        {"result_kind": "result", "field_count": 2}
    )
