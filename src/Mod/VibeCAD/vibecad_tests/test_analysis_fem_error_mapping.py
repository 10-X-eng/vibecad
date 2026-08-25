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


def _request(tmp_path: Path) -> legacy.SolverExecutionRequest:
    target = SimpleNamespace(
        kind="calculix",
        expected_state_sha256="a" * 64,
        solver=SimpleNamespace(Name="Solver", ID=7, TypeId="Fem::SolverCalculiX"),
    )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
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


def test_host_provider_start_failure_maps_to_legacy_exact_error_and_discards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    discarded: list[object] = []

    def start_failed(*_args, **_kwargs):
        raise ExternalProcessError(
            "start_failed",
            stage=2,
            program="/solver/ccx",
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", start_failed)
    monkeypatch.setattr(legacy, "discard_solver_execution_request", discarded.append)

    with pytest.raises(NativeAnalyzeError) as caught:
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )

    assert caught.value.failure() == {
        "error_code": "NATIVE_ANALYZE_SOLVER_START_FAILED",
        "message": "Calculix stage 2 could not be started.",
    }
    assert discarded == [request]
