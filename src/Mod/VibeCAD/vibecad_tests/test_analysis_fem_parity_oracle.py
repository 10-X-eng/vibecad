# SPDX-License-Identifier: LGPL-2.1-or-later

"""Executable legacy/host lifecycle parity oracle for supported FEM solvers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import tool_impl.analysis_fem_adapter as adapter
import VibeCADNativeAnalyzeSolverExecution as legacy
import VibeCADNativeAnalyzeSolverExecutionProcess as legacy_process
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import (
    NativeBackgroundCancelled,
)
from VibeCADScriptedProcess import (
    ExternalProcessCancelled,
    ExternalProcessError,
    ExternalProcessStage,
)


FIXTURE = Path(__file__).with_name("fixtures") / "analysis_fem_parity_v2.json"
SOLVERS = ("calculix", "elmer", "z88", "mystran")
FAILURES = ("start_failed", "timeout", "output_limit", "backend_failed")


def _oracle() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _request(root: Path, kind: str, expected: dict) -> legacy.SolverExecutionRequest:
    root.mkdir(parents=True)
    (root / "case.inp").write_bytes(b"frozen FEM input\n")
    sealed = adapter.seal_directory(root)
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
        working_directory=str(root),
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


def _successful_sequence(expected: dict, identity: dict, commands, **kwargs):
    identity.update(kwargs)
    identity["commands"] = commands
    total = len(expected["stages"])
    for stage in range(1, total + 1):
        kwargs["stage_started"](stage, total)
    return tuple(
        ExternalProcessStage(
            stage=stage,
            program=expected["commands"][stage - 1][0],
            exit_code=exit_code,
            log_path=str(Path(kwargs["working_directory"]) / f"solver-{stage}.log"),
        )
        for stage, _program, exit_code in expected["stages"]
    )


def _process_failure(reason: str, expected: dict) -> Exception:
    if reason == "cancelled":
        return ExternalProcessCancelled()
    return ExternalProcessError(
        reason,
        stage=1,
        program=expected["commands"][0][0],
        exit_code=17 if reason == "backend_failed" else None,
        detail="normalized failure" if reason == "backend_failed" else "",
    )


@pytest.mark.parametrize("kind", SOLVERS)
def test_supported_fem_host_success_matches_frozen_legacy_oracle(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _oracle()["solvers"][kind]
    legacy_request = _request(tmp_path / "legacy", kind, expected)
    host_request = _request(tmp_path / "host", kind, expected)
    legacy_progress: list[list[object]] = []
    host_progress: list[list[object]] = []
    legacy_identity: dict[str, object] = {}
    host_identity: dict[str, object] = {}

    monkeypatch.setattr(
        legacy_process,
        "run_process_sequence",
        lambda commands, **kwargs: _successful_sequence(
            expected, legacy_identity, commands, **kwargs
        ),
    )
    legacy_completed = legacy.run_solver_execution(
        legacy_request,
        cancelled=lambda: False,
        progress=lambda percent, message: legacy_progress.append([percent, message]),
    )

    monkeypatch.setattr(
        adapter._LOCAL_PROCESS_PROVIDER,
        "run_sequence",
        lambda commands, **kwargs: _successful_sequence(
            expected, host_identity, commands, **kwargs
        ),
    )
    monkeypatch.setattr(
        legacy,
        "run_solver_execution",
        lambda *_args, **_kwargs: pytest.fail("supported FEM must use host provider"),
    )
    prepared = adapter.PreparedFEMSolverExecution(
        adapter._prepared_contract(host_request, document_uid="document-uid"),
        host_request,
    )
    host_completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda percent, message: host_progress.append([percent, message]),
    )

    assert legacy_progress == host_progress == expected["progress"]
    assert legacy_completed.stages == host_completed.legacy_prepared.stages
    assert [
        [item["stage"], item["program"], item["exit_code"]]
        for item in host_completed.legacy_prepared.stages
    ] == expected["stages"]
    for identity, request in (
        (legacy_identity, legacy_request),
        (host_identity, host_request),
    ):
        assert identity["commands"] == request.commands
        assert Path(identity["working_directory"]) == Path(request.working_directory)
        assert identity["environment"] is request.environment
        assert identity["timeout_seconds"] == expected["timeout_seconds"]
        assert identity["maximum_log_bytes"] == 16 * 1024 * 1024

    analysis = prepared.analysis
    assert host_completed.analysis is analysis
    assert analysis.domain == "fem"
    assert analysis.source_document_uid == "document-uid"
    assert analysis.input_manifest.sha256 == host_request.input_sha256
    assert analysis.input_manifest.file_count == host_request.input_file_count
    assert analysis.execution_spec.command_tuples() == host_request.commands
    assert analysis.execution_spec.timeout_seconds == host_request.timeout_seconds
    assert analysis.execution_spec.environment_keys == tuple(
        sorted(host_request.environment)
    )
    assert analysis.provenance.to_value()["compatibility_mode"] is True
    assert (
        analysis.dependency_snapshot.by_key("solver_state").canonical_digest
        == "a" * 64
    )


@pytest.mark.parametrize("kind", SOLVERS)
@pytest.mark.parametrize("reason", FAILURES)
def test_supported_fem_host_failure_and_cleanup_match_legacy_oracle(
    kind: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _oracle()["solvers"][kind]
    legacy_request = _request(tmp_path / "legacy", kind, expected)
    host_request = _request(tmp_path / "host", kind, expected)
    legacy_progress: list[list[object]] = []
    host_progress: list[list[object]] = []
    if reason == "backend_failed":
        for request in (legacy_request, host_request):
            (Path(request.working_directory) / "solver-1.log").write_text(
                "normalized failure\n",
                encoding="utf-8",
            )

    def fail_legacy(*_args, **_kwargs):
        raise _process_failure(reason, expected)

    monkeypatch.setattr(legacy_process, "run_process_sequence", fail_legacy)
    with pytest.raises(NativeAnalyzeError) as legacy_caught:
        legacy.run_solver_execution(
            legacy_request,
            cancelled=lambda: False,
            progress=lambda percent, message: legacy_progress.append(
                [percent, message]
            ),
        )

    def fail_host(*_args, **_kwargs):
        raise _process_failure(reason, expected)

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", fail_host)
    prepared = adapter.PreparedFEMSolverExecution(object(), host_request)
    with pytest.raises(NativeAnalyzeError) as host_caught:
        adapter.run_solver_execution(
            prepared,
            cancelled=lambda: False,
            progress=lambda percent, message: host_progress.append([percent, message]),
        )

    expected_failure = expected["failures"][reason]
    assert legacy_caught.value.failure() == expected_failure
    assert host_caught.value.failure() == expected_failure
    assert legacy_progress == host_progress == [[7, "FEM solver input frozen"]]
    assert not Path(legacy_request.working_directory).exists()
    assert not Path(host_request.working_directory).exists()


@pytest.mark.parametrize("kind", SOLVERS)
def test_supported_fem_host_cancellation_and_cleanup_match_legacy(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _oracle()["solvers"][kind]
    legacy_request = _request(tmp_path / "legacy", kind, expected)
    host_request = _request(tmp_path / "host", kind, expected)

    def cancel(*_args, **_kwargs):
        raise _process_failure("cancelled", expected)

    monkeypatch.setattr(legacy_process, "run_process_sequence", cancel)
    with pytest.raises(NativeBackgroundCancelled):
        legacy.run_solver_execution(
            legacy_request,
            cancelled=lambda: True,
            progress=lambda _percent, _message: None,
        )

    monkeypatch.setattr(adapter._LOCAL_PROCESS_PROVIDER, "run_sequence", cancel)
    with pytest.raises(NativeBackgroundCancelled):
        adapter.run_solver_execution(
            adapter.PreparedFEMSolverExecution(object(), host_request),
            cancelled=lambda: True,
            progress=lambda _percent, _message: None,
        )

    assert not Path(legacy_request.working_directory).exists()
    assert not Path(host_request.working_directory).exists()


@pytest.mark.parametrize("kind", SOLVERS)
def test_supported_fem_host_preserves_legacy_publication_and_discard_seams(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _oracle()["solvers"][kind]
    request = _request(tmp_path / "host", kind, expected)
    prepared = adapter.PreparedFEMSolverExecution(object(), request)
    monkeypatch.setattr(
        adapter._LOCAL_PROCESS_PROVIDER,
        "run_sequence",
        lambda commands, **kwargs: _successful_sequence(
            expected, {}, commands, **kwargs
        ),
    )
    completed = adapter.run_solver_execution(
        prepared,
        cancelled=lambda: False,
        progress=lambda _percent, _message: None,
    )

    document = object()
    draft = object()
    public_result = {"public": "legacy-exact"}
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        legacy,
        "commit_solver_execution",
        lambda actual_document, actual_prepared: calls.update(
            commit=(actual_document, actual_prepared)
        )
        or draft,
    )
    monkeypatch.setattr(
        legacy,
        "verify_solver_execution",
        lambda actual_document, actual_draft: calls.update(
            verify=(actual_document, actual_draft)
        )
        or public_result,
    )
    monkeypatch.setattr(
        legacy,
        "discard_solver_execution_request",
        lambda actual_request: calls.update(discard=actual_request),
    )

    assert adapter.commit_solver_execution(document, completed) is draft
    assert calls["commit"] == (document, completed.legacy_prepared)
    assert adapter.verify_solver_execution(document, draft) is public_result
    assert calls["verify"] == (document, draft)
    adapter.discard_solver_execution_request(prepared)
    assert calls["discard"] is request


def test_parity_oracle_records_reviewed_dimensions_and_differences() -> None:
    oracle = _oracle()
    assert oracle["schema"] == "vibecad-analysis-fem-parity-v2"
    assert oracle["compatibility_dimensions"] == [
        "input digest and file count",
        "direct-argument command sequence",
        "working directory and environment identity",
        "timeout and bounded output",
        "normalized progress and stage summaries",
        "solver state and History dependency identity",
        "public failure codes, messages, and repair data",
        "cancellation and failure cleanup",
        "legacy commit, verification, and discard object identity",
    ]
    assert oracle["accepted_intentional_differences"] == [
        "Execution is delegated to LocalProcessProvider instead of the legacy "
        "private process loop.",
        "A serializable PreparedAnalysis identity is exposed in addition to the "
        "legacy transient request.",
        "Progress includes the host-owned input-frozen event at seven percent.",
    ]
    assert set(oracle["solvers"]) == set(SOLVERS)
