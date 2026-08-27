# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen normalized legacy/host parity oracle for supported FEM solvers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_impl.analysis_fem_adapter as adapter
import VibeCADNativeAnalyzeSolverExecution as legacy


FIXTURE = Path(__file__).with_name("fixtures") / "analysis_fem_parity_v1.json"


def _oracle() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _request(tmp_path: Path, kind: str, expected: dict) -> legacy.SolverExecutionRequest:
    (tmp_path / "case.inp").write_bytes(b"frozen FEM input\n")
    sealed = adapter.seal_directory(tmp_path)
    solver = SimpleNamespace(Name="Solver", ID=17, TypeId=expected["type_id"])
    target = SimpleNamespace(
        kind=kind,
        expected_state_sha256="a" * 64,
        solver=solver,
    )
    return legacy.SolverExecutionRequest(
        target=target,
        implementation="pipeline",
        history_operations=(solver,),
        working_directory=str(tmp_path),
        commands=tuple(
            (program, tuple(arguments))
            for program, arguments in expected["commands"]
        ),
        environment={"OMP_NUM_THREADS": "2", "VIBECAD_PARITY": "exact"},
        timeout_seconds=expected["timeout_seconds"],
        input_sha256=sealed.sha256,
        input_file_count=sealed.file_count,
        keep_results=False,
        importer_state={"result_format": "fixture"},
    )


@pytest.mark.parametrize("kind", ("calculix", "elmer", "z88", "mystran"))
def test_supported_fem_host_path_matches_frozen_legacy_oracle(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _oracle()["solvers"][kind]
    request = _request(tmp_path, kind, expected)
    prepared = adapter.PreparedFEMSolverExecution(
        adapter._prepared_contract(request, document_uid="document-uid"),
        request,
    )
    progress: list[list[object]] = []
    provider_identity: dict[str, object] = {}

    def provider_run(commands, **kwargs):
        provider_identity.update(kwargs)
        provider_identity["commands"] = commands
        total = len(expected["stages"])
        for stage in range(1, total + 1):
            kwargs["stage_started"](stage, total)
        return tuple(
            SimpleNamespace(
                stage=stage,
                program=expected["commands"][stage - 1][0],
                exit_code=exit_code,
            )
            for stage, _program, exit_code in expected["stages"]
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", provider_run)
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail("supported FEM must use host provider"),
    )
    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda percent, message: progress.append([percent, message]),
    )

    assert progress == expected["progress"]
    assert provider_identity["commands"] == request.commands
    assert provider_identity["working_directory"] == request.working_directory
    assert provider_identity["environment"] is request.environment
    assert provider_identity["timeout_seconds"] == expected["timeout_seconds"]
    assert provider_identity["maximum_log_bytes"] == 16 * 1024 * 1024
    assert [
        [item["stage"], item["program"], item["exit_code"]]
        for item in completed.legacy_prepared.stages
    ] == expected["stages"]

    analysis = prepared.analysis
    assert analysis.domain == "fem"
    assert analysis.source_document_uid == "document-uid"
    assert analysis.input_manifest.sha256 == request.input_sha256
    assert analysis.input_manifest.file_count == request.input_file_count
    assert analysis.execution_spec.command_tuples() == request.commands
    assert analysis.execution_spec.timeout_seconds == request.timeout_seconds
    assert analysis.execution_spec.environment_keys == tuple(sorted(request.environment))
    assert analysis.provenance.to_value()["compatibility_mode"] is True
    assert analysis.dependency_snapshot.by_key("solver_state").canonical_digest == "a" * 64


def test_parity_oracle_records_only_reviewed_intentional_differences() -> None:
    oracle = _oracle()
    assert oracle["schema"] == "vibecad-analysis-fem-parity-v1"
    assert oracle["accepted_intentional_differences"] == [
        "Execution is delegated to LocalProcessProvider instead of the legacy private process loop.",
        "A serializable PreparedAnalysis identity is exposed in addition to the legacy transient request.",
        "Progress includes the host-owned input-frozen event at seven percent.",
    ]
    assert set(oracle["solvers"]) == {"calculix", "elmer", "z88", "mystran"}
