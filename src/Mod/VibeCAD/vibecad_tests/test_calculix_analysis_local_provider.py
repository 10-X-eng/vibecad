# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_impl.analysis_fem_adapter as adapter
import tool_impl.analysis_local_provider as local_provider
import VibeCADNativeAnalyzeSolverExecution as legacy
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import ExternalProcessCancelled, ExternalProcessError


def _request(
    tmp_path: Path,
    *,
    kind: str = "calculix",
    implementation: str = "pipeline",
) -> legacy.SolverExecutionRequest:
    target = SimpleNamespace(
        kind=kind,
        expected_state_sha256="a" * 64,
        solver=SimpleNamespace(Name="Solver", ID=7, TypeId="Fem::SolverCalculiX"),
    )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation=implementation,
        history_operations=(target.solver,),
        working_directory=str(tmp_path),
        commands=(("/solver/ccx", ("-i", "case")),),
        environment={**os.environ, "OMP_NUM_THREADS": "4"},
        timeout_seconds=120,
        input_sha256="b" * 64,
        input_file_count=1,
        keep_results=False,
        importer_state={"input_deck": "case"},
    )


def test_local_process_provider_preserves_exact_process_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = (SimpleNamespace(stage=1, program="/solver/ccx", exit_code=0),)

    def fake_run(commands, **kwargs):
        captured["commands"] = commands
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(local_provider, "run_process_sequence", fake_run)
    provider = local_provider.LocalProcessProvider()
    cancel = lambda: False
    log_name = lambda stage: f"solver-{stage}.log"
    stage_started = lambda _stage, _total: None

    result = provider.run_sequence(
        (("/solver/ccx", ("-i", "case")),),
        working_directory=str(tmp_path),
        environment={"SAFE": "exact"},
        timeout_seconds=120,
        cancellation_check=cancel,
        log_name=log_name,
        stage_started=stage_started,
        maximum_log_bytes=16 * 1024 * 1024,
    )

    assert result is sentinel
    assert captured["commands"] == (("/solver/ccx", ("-i", "case")),)
    assert captured["working_directory"] == str(tmp_path)
    assert captured["environment"] == {"SAFE": "exact"}
    assert captured["timeout_seconds"] == 120
    assert captured["cancellation_check"] is cancel
    assert captured["log_name"] is log_name
    assert captured["stage_started"] is stage_started
    assert captured["maximum_log_bytes"] == 16 * 1024 * 1024


def _assert_calculix_provider_execution(
    request: legacy.SolverExecutionRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    progress: list[tuple[int, str]] = []
    provider_calls: list[object] = []

    def provider_run(commands, **kwargs):
        provider_calls.append((commands, kwargs))
        kwargs["stage_started"](1, 1)
        return (SimpleNamespace(stage=1, program="/solver/ccx", exit_code=0),)

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "migrated CalculiX path must not use legacy runner"
        ),
    )

    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append((percent, message)),
    )

    assert len(provider_calls) == 1
    assert provider_calls[0][0] == request.commands
    assert provider_calls[0][1]["working_directory"] == request.working_directory
    assert provider_calls[0][1]["environment"] is request.environment
    assert provider_calls[0][1]["timeout_seconds"] == request.timeout_seconds
    assert progress == [
        (7, "FEM solver input frozen"),
        (12, "Running Calculix stage 1/1"),
        (84, "Calculix result artifacts ready"),
    ]
    assert completed.legacy_prepared.request is request
    assert completed.legacy_prepared.stages == (
        {"stage": 1, "program": "ccx", "exit_code": 0},
    )


def test_calculix_pipeline_runs_through_host_local_provider_not_legacy_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_calculix_provider_execution(_request(tmp_path), monkeypatch)


def test_ccx_tools_alternate_calculix_runs_through_host_local_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_calculix_provider_execution(
        _request(tmp_path, implementation="ccx_tools"),
        monkeypatch,
    )


def test_calculix_and_mystran_are_both_host_provider_migrated(tmp_path: Path) -> None:
    assert adapter._uses_host_local_provider(_request(tmp_path)) is True
    assert adapter._uses_host_local_provider(_request(tmp_path, kind="mystran")) is True


def test_calculix_timeout_mapping_is_legacy_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)

    def timeout(*_args, **_kwargs):
        raise ExternalProcessError(
            "timeout",
            stage=1,
            program="/solver/ccx",
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
        "message": "Calculix exceeded timeout_seconds before producing results.",
    }


def test_calculix_provider_cancellation_maps_to_native_background_cancel(
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
