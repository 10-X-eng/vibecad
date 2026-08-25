# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_impl.analysis_fem_adapter as adapter
import VibeCADNativeAnalyzeSolverExecution as legacy
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADScriptedProcess import ExternalProcessError


def _request(tmp_path: Path, *, commands=None) -> legacy.SolverExecutionRequest:
    target = SimpleNamespace(
        kind="z88",
        expected_state_sha256="c" * 64,
        solver=SimpleNamespace(Name="Solver", ID=11, TypeId="Fem::SolverZ88"),
    )
    if commands is None:
        commands = (
            ("/solver/z88r", ("-t", "-choly")),
            ("/solver/z88r", ("-c", "-choly")),
        )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
        history_operations=(target.solver,),
        working_directory=str(tmp_path),
        commands=tuple(commands),
        environment=dict(os.environ),
        timeout_seconds=240,
        input_sha256="d" * 64,
        input_file_count=4,
        keep_results=False,
        importer_state={},
    )


def test_z88_two_stage_runs_through_host_local_provider_with_exact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    progress: list[tuple[int, str]] = []
    provider_calls: list[object] = []

    def provider_run(commands, **kwargs):
        provider_calls.append((commands, kwargs))
        kwargs["stage_started"](1, 2)
        kwargs["stage_started"](2, 2)
        return (
            SimpleNamespace(stage=1, program="/solver/z88r", exit_code=0),
            SimpleNamespace(stage=2, program="/solver/z88r", exit_code=0),
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "migrated Z88 path must not use legacy runner"
        ),
    )

    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
    )

    assert len(provider_calls) == 1
    commands, kwargs = provider_calls[0]
    assert commands == request.commands
    assert kwargs["working_directory"] == request.working_directory
    assert kwargs["environment"] is request.environment
    assert kwargs["timeout_seconds"] == request.timeout_seconds
    assert kwargs["maximum_log_bytes"] == 16 * 1024 * 1024
    assert progress == [
        (7, "FEM solver input frozen"),
        (12, "Running Z88 stage 1/2"),
        (44, "Running Z88 stage 2/2"),
        (84, "Z88 result artifacts ready"),
    ]
    assert completed.legacy_prepared.request is request
    assert completed.legacy_prepared.stages == (
        {"stage": 1, "program": "z88r", "exit_code": 0},
        {"stage": 2, "program": "z88r", "exit_code": 0},
    )


def test_z88_single_test_stage_preserves_one_stage_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path,
        commands=(("/solver/z88r", ("-t", "-choly")),),
    )
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    progress: list[tuple[int, str]] = []

    def provider_run(commands, **kwargs):
        assert commands == request.commands
        kwargs["stage_started"](1, 1)
        return (SimpleNamespace(stage=1, program="/solver/z88r", exit_code=0),)

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "single-stage Z88 test mode must use host provider"
        ),
    )

    adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
    )

    assert progress == [
        (7, "FEM solver input frozen"),
        (12, "Running Z88 stage 1/1"),
        (84, "Z88 result artifacts ready"),
    ]


def test_z88_backend_failure_mapping_is_legacy_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)

    def fail(*_args, **_kwargs):
        raise ExternalProcessError(
            "backend_failed",
            stage=2,
            program="/solver/z88r",
            exit_code=17,
            detail="solver rejected input",
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", fail)
    monkeypatch.setattr(legacy, "discard_solver_execution_request", lambda _value: None)

    with pytest.raises(NativeAnalyzeError) as caught:
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
        "message": "Z88 stage 2 exited with code 17: solver rejected input",
    }


def test_mystran_remains_on_legacy_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    request = legacy.SolverExecutionRequest(
        target=SimpleNamespace(
            kind="mystran",
            expected_state_sha256=request.target.expected_state_sha256,
            solver=SimpleNamespace(Name="Solver", ID=12, TypeId="Fem::SolverMystran"),
        ),
        implementation=request.implementation,
        history_operations=request.history_operations,
        working_directory=request.working_directory,
        commands=(("/solver/mystran", ("case.bdf",)),),
        environment=request.environment,
        timeout_seconds=request.timeout_seconds,
        input_sha256=request.input_sha256,
        input_file_count=request.input_file_count,
        keep_results=request.keep_results,
        importer_state={"input_deck": "case"},
    )
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    sentinel = object()
    called: list[object] = []

    monkeypatch.setattr(
        adapter._LOCAL_PROCESS_PROVIDER,
        "run_sequence",
        lambda *_args, **_kwargs: pytest.fail("Mystran is not migrated in this PR"),
    )

    def legacy_run(request_value, *, cancelled, progress):
        called.append(request_value)
        return sentinel

    monkeypatch.setattr(legacy, "run_solver_execution", legacy_run)
    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda _percent, _message: None,
    )

    assert called == [request]
    assert completed.legacy_prepared is sentinel
