# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_impl.analysis_fem_adapter as adapter
import VibeCADNativeAnalyzeSolverExecution as legacy
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import ExternalProcessCancelled, ExternalProcessError


def _request(tmp_path: Path) -> legacy.SolverExecutionRequest:
    target = SimpleNamespace(
        kind="mystran",
        expected_state_sha256="e" * 64,
        solver=SimpleNamespace(Name="Solver", ID=13, TypeId="Fem::SolverMystran"),
    )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
        history_operations=(target.solver,),
        working_directory=str(tmp_path),
        commands=(("/solver/mystran", ("case.bdf",)),),
        environment=dict(os.environ),
        timeout_seconds=300,
        input_sha256="f" * 64,
        input_file_count=2,
        keep_results=False,
        importer_state={"input_deck": "case"},
    )


def test_mystran_runs_through_host_local_provider_with_exact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    progress: list[tuple[int, str]] = []
    provider_calls: list[object] = []

    def provider_run(commands, **kwargs):
        provider_calls.append((commands, kwargs))
        kwargs["stage_started"](1, 1)
        return (SimpleNamespace(stage=1, program="/solver/mystran", exit_code=0),)

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "migrated Mystran path must not use legacy runner"
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
    assert kwargs["log_name"](1) == "solver-1.log"
    assert progress == [
        (7, "FEM solver input frozen"),
        (12, "Running Mystran stage 1/1"),
        (84, "Mystran result artifacts ready"),
    ]
    assert completed.legacy_prepared.request is request
    assert completed.legacy_prepared.stages == (
        {"stage": 1, "program": "mystran", "exit_code": 0},
    )


def test_mystran_output_limit_mapping_is_legacy_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)

    def output_limit(*_args, **_kwargs):
        raise ExternalProcessError(
            "output_limit",
            stage=1,
            program="/solver/mystran",
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", output_limit)
    monkeypatch.setattr(legacy, "discard_solver_execution_request", lambda _value: None)

    with pytest.raises(NativeAnalyzeError) as caught:
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
        "message": "Mystran exceeded the 16 MiB diagnostic-output bound.",
    }


def test_mystran_cancellation_maps_to_native_background_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)

    def cancel(*_args, **_kwargs):
        raise ExternalProcessCancelled()

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", cancel)
    monkeypatch.setattr(legacy, "discard_solver_execution_request", lambda _value: None)

    with pytest.raises(NativeBackgroundCancelled):
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: True,
            progress=lambda _percent, _message: None,
        )


def test_all_supported_fem_solvers_are_host_local_provider_migrated() -> None:
    for kind in ("calculix", "elmer", "z88", "mystran"):
        request = SimpleNamespace(target=SimpleNamespace(kind=kind))
        assert adapter._uses_host_local_provider(request) is True
