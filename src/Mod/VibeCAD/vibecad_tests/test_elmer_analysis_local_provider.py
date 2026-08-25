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


def _request(tmp_path: Path, *, kind: str = "elmer") -> legacy.SolverExecutionRequest:
    target = SimpleNamespace(
        kind=kind,
        expected_state_sha256="a" * 64,
        solver=SimpleNamespace(Name="Solver", ID=9, TypeId="Fem::SolverElmer"),
    )
    commands = (
        ("/solver/ElmerGrid", ("14", "2", "case.unv")),
        ("/solver/ElmerGrid", ("2", "2", "case")),
        ("/solver/mpiexec", ("-n", "4", "/solver/ElmerSolver")),
    )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
        history_operations=(target.solver,),
        working_directory=str(tmp_path),
        commands=commands,
        environment={**os.environ, "OMP_NUM_THREADS": "2"},
        timeout_seconds=180,
        input_sha256="b" * 64,
        input_file_count=3,
        keep_results=False,
        importer_state={"result_format": ".pvtu"},
    )


def test_elmer_multistage_runs_through_host_local_provider_with_exact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    progress: list[tuple[int, str]] = []
    provider_calls: list[object] = []

    def provider_run(commands, **kwargs):
        provider_calls.append((commands, kwargs))
        for stage in range(1, 4):
            kwargs["stage_started"](stage, 3)
        return (
            SimpleNamespace(stage=1, program="/solver/ElmerGrid", exit_code=0),
            SimpleNamespace(stage=2, program="/solver/ElmerGrid", exit_code=0),
            SimpleNamespace(stage=3, program="/solver/mpiexec", exit_code=0),
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "migrated Elmer path must not use legacy runner"
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
        (12, "Running Elmer stage 1/3"),
        (33, "Running Elmer stage 2/3"),
        (55, "Running Elmer stage 3/3"),
        (84, "Elmer result artifacts ready"),
    ]
    assert completed.legacy_prepared.request is request
    assert completed.legacy_prepared.stages == (
        {"stage": 1, "program": "ElmerGrid", "exit_code": 0},
        {"stage": 2, "program": "ElmerGrid", "exit_code": 0},
        {"stage": 3, "program": "mpiexec", "exit_code": 0},
    )


def test_elmer_timeout_mapping_is_legacy_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)

    def timeout(*_args, **_kwargs):
        raise ExternalProcessError(
            "timeout",
            stage=3,
            program="/solver/ElmerSolver",
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", timeout)
    monkeypatch.setattr(legacy, "discard_solver_execution_request", lambda _value: None)

    with pytest.raises(NativeAnalyzeError) as caught:
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_ANALYZE_SOLVER_TIMEOUT",
        "message": "Elmer exceeded timeout_seconds before producing results.",
    }


@pytest.mark.parametrize("kind", ["z88", "mystran"])
def test_unmigrated_fem_solvers_remain_on_legacy_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    request = _request(tmp_path, kind=kind)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    sentinel = object()
    called: list[object] = []

    monkeypatch.setattr(
        adapter._LOCAL_PROCESS_PROVIDER,
        "run_sequence",
        lambda *_args, **_kwargs: pytest.fail(
            f"{kind} is not migrated in the Elmer PR"
        ),
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
