# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import pytest

import VibeCADScriptedProcess as scripted_process
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecutionProcess import run_solver_processes
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    analyze_solver_execution_capability_definition,
)
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeBackgroundSchema import native_background_capability_definition
from VibeCADScriptedProcess import ExternalProcessError, run_process_sequence


def _program(path: Path, source: str) -> str:
    path.write_text("#!/bin/sh\n" + source, encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def test_solver_execution_schema_is_one_sharp_background_operation() -> None:
    definition = analyze_solver_execution_capability_definition()
    assert definition.name == "analyze.solver_execution"
    assert tuple(variant.operation for variant in definition.variants) == ("run",)
    variant = definition.variants[0]
    assert variant.background_required
    assert variant.transaction_behavior == "background"
    assert set(variant.parameters["properties"]) == {"target", "timeout_seconds"}


def test_host_job_control_is_available_on_aero_surface() -> None:
    definition = native_background_capability_definition()
    assert definition.name == "native.job"
    assert tuple(variant.operation for variant in definition.variants) == (
        "status",
        "cancel",
    )
    for variant in definition.variants:
        assert "aero" in variant.surface_ids
        assert variant.transaction_behavior == "none"
        assert variant.background_required is False


def test_process_sequence_is_exact_bounded_and_shell_free(tmp_path: Path) -> None:
    first = _program(tmp_path / "first", "printf first > first.out\n")
    second = _program(
        tmp_path / "second",
        'test "$SAFE_VALUE" = exact && test -f first.out && printf second > second.out\n',
    )
    progress = []

    result = run_solver_processes(
        ((first, ()), (second, ())),
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


def test_shared_process_sequence_reports_domain_neutral_timeout(tmp_path: Path) -> None:
    program = _program(tmp_path / "slow-generic", "sleep 30\n")

    with pytest.raises(ExternalProcessError) as caught:
        run_process_sequence(
            ((program, ()),),
            working_directory=tmp_path,
            environment=os.environ,
            timeout_seconds=1,
            cancellation_check=lambda: False,
            maximum_log_bytes=1024,
            poll_seconds=0.01,
        )

    assert caught.value.reason == "timeout"
    assert caught.value.stage == 1
    assert caught.value.exit_code is None


def test_shared_process_sequence_checks_output_bound_after_fast_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FastExitProcess:
        def __init__(self, *_args, stdout, **_kwargs) -> None:
            self._stdout = stdout
            self._polls = 0
            self.returncode = None

        def poll(self) -> int | None:
            self._polls += 1
            if self._polls == 1:
                return None
            self._stdout.write(b"x" * 2048)
            self._stdout.flush()
            self.returncode = 0
            return 0

    monkeypatch.setattr(scripted_process.subprocess, "Popen", FastExitProcess)

    with pytest.raises(ExternalProcessError) as caught:
        scripted_process.run_process_sequence(
            (("fast-exit", ()),),
            working_directory=tmp_path,
            environment=os.environ,
            timeout_seconds=5,
            cancellation_check=lambda: False,
            maximum_log_bytes=1024,
            poll_seconds=0.001,
        )

    assert caught.value.reason == "output_limit"
    assert caught.value.stage == 1
    assert caught.value.exit_code is None


def test_process_sequence_cooperatively_terminates_on_cancel(tmp_path: Path) -> None:
    program = _program(tmp_path / "slow", "sleep 30\n")
    cancelled = threading.Event()

    def trigger() -> None:
        time.sleep(0.15)
        cancelled.set()

    threading.Thread(target=trigger, daemon=True).start()
    with pytest.raises(NativeBackgroundCancelled):
        run_solver_processes(
            ((program, ()),),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=cancelled.is_set,
            progress=lambda _percent, _message: None,
            backend="Test",
        )


def test_fem_solve_is_model_unqualified() -> None:
    from VibeCADNativeAnalyzeAnalysis import stamp_created_fem_graph
    from VibeCADNativeAnalyzeSolverExecution import stamp_solver_execution_unqualified

    stamped = stamp_solver_execution_unqualified(
        {"result": {"object_name": "CCX_Results"}}
    )
    assert stamped["claim_ceiling"] == "model_unqualified"
    assert stamped["solved"] is True
    assert stamped["qualified"] is False
    created = stamp_created_fem_graph({"created_analysis": {"name": "Analysis"}})
    assert created["claim_ceiling"] == "not_solved"
    assert created["claim_ceiling"] != stamped["claim_ceiling"]


def test_process_failure_returns_only_bounded_tail(tmp_path: Path) -> None:
    program = _program(tmp_path / "fail", "printf 'precise failure'; exit 7\n")

    with pytest.raises(NativeAnalyzeError, match="code 7: precise failure"):
        run_solver_processes(
            ((program, ()),),
            working_directory=str(tmp_path),
            environment=os.environ,
            timeout_seconds=5,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
            backend="Test",
        )
