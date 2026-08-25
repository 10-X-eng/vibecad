# SPDX-License-Identifier: LGPL-2.1-or-later

"""FEM compatibility adapter onto the host Analysis contract boundary.

This migration adapter deliberately leaves the proven FEM implementation
underneath. It creates a domain-neutral, serializable PreparedAnalysis identity
for the host seam while retaining the legacy request only as transient in-process
state until later migration stages move orchestration and publication ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tool_impl.analysis_artifacts import AnalysisArtifactError, seal_directory
from tool_impl.analysis_contracts import (
    AnalysisCommand,
    AnalysisContractError,
    DependencyRecord,
    DependencySnapshot,
    ExecutionSpec,
    PreparedAnalysis,
    PreparedInputManifest,
    environment_sha256,
    json_sha256,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
import VibeCADNativeAnalyzeSolverExecution as _legacy


FEM_ANALYSIS_ADAPTER_ID = "vibecad.native.analyze.fem"
FEM_ANALYSIS_ADAPTER_VERSION = "legacy-compat-v1"


def _history_identity(history_operations: tuple[Any, ...]) -> list[list[Any]]:
    return [
        [
            str(getattr(value, "Name", "") or ""),
            int(getattr(value, "ID", 0) or 0),
            str(getattr(value, "TypeId", "") or ""),
        ]
        for value in history_operations
    ]


def _seal_legacy_input(request: _legacy.SolverExecutionRequest) -> Any:
    """Re-seal the legacy FEM input with the host primitive and prove parity."""

    try:
        sealed = seal_directory(request.working_directory)
    except AnalysisArtifactError as exc:
        if exc.reason == "unsafe_symlink":
            raise NativeAnalyzeError(
                "A detached FEM input contains an unsafe symbolic link."
            ) from exc
        if exc.reason == "bounds":
            raise NativeAnalyzeError(
                "The detached FEM input exceeds 4096 files or 4 GiB.",
                error_code="NATIVE_ANALYZE_SOLVER_INPUT_LIMIT",
            ) from exc
        if exc.reason == "empty":
            raise NativeAnalyzeError(
                "The FEM solver input writer produced no artifacts."
            ) from exc
        raise NativeAnalyzeError(
            "The detached FEM input could not be sealed for execution."
        ) from exc

    if (
        sealed.sha256 != request.input_sha256
        or sealed.file_count != request.input_file_count
    ):
        raise AnalysisContractError(
            "Host Analysis sealing does not match the legacy FEM input identity."
        )
    return sealed


def _prepared_contract(
    request: _legacy.SolverExecutionRequest,
    *,
    document_uid: str,
) -> PreparedAnalysis:
    target = request.target
    solver = target.solver
    sealed = _seal_legacy_input(request)
    dependencies = DependencySnapshot(
        (
            DependencyRecord(
                key="solver_state",
                kind="fem_solver_state",
                canonical_digest=str(target.expected_state_sha256),
                human_summary="Exact prepared FEM solver state",
            ),
            DependencyRecord(
                key="history_operations",
                kind="fem_history_sequence",
                canonical_digest=json_sha256(
                    _history_identity(request.history_operations)
                ),
                human_summary="Exact History operation sequence at preparation",
            ),
            DependencyRecord(
                key="keep_results_on_rerun",
                kind="fem_result_retention_preference",
                canonical_digest=json_sha256(bool(request.keep_results)),
                human_summary="KeepResultsOnReRun preference at preparation",
            ),
        )
    )
    execution = ExecutionSpec(
        provider_id="local-process",
        commands=tuple(
            AnalysisCommand(
                str(program),
                tuple(str(argument) for argument in arguments),
            )
            for program, arguments in request.commands
        ),
        timeout_seconds=int(request.timeout_seconds),
        environment_keys=tuple(
            sorted(str(key) for key in request.environment)
        ),
        environment_sha256=environment_sha256(request.environment),
    )
    return PreparedAnalysis.create(
        domain="fem",
        adapter_id=FEM_ANALYSIS_ADAPTER_ID,
        adapter_version=FEM_ANALYSIS_ADAPTER_VERSION,
        source_document_uid=document_uid,
        source_summary={
            "solver_object_name": str(getattr(solver, "Name", "") or ""),
            "solver_kind": str(target.kind),
            "implementation": str(request.implementation),
        },
        dependency_snapshot=dependencies,
        input_manifest=PreparedInputManifest(
            storage_reference=sealed.root,
            sha256=sealed.sha256,
            file_count=sealed.file_count,
        ),
        execution_spec=execution,
        expected_outputs=("fem_result_graph",),
        publication_descriptor={
            "solver_kind": str(target.kind),
            "implementation": str(request.implementation),
            "importer_state_keys": sorted(
                str(key) for key in request.importer_state
            ),
        },
        provenance={
            "legacy_module": "VibeCADNativeAnalyzeSolverExecution",
            "input_sha256": str(request.input_sha256),
            "input_total_bytes": sealed.total_bytes,
            "input_digest_algorithm": sealed.digest_algorithm,
            "compatibility_mode": True,
        },
    )


@dataclass(frozen=True, slots=True)
class PreparedFEMSolverExecution:
    analysis: PreparedAnalysis
    legacy_request: _legacy.SolverExecutionRequest

    @property
    def target(self) -> Any:
        return self.legacy_request.target


@dataclass(frozen=True, slots=True)
class CompletedFEMSolverExecution:
    analysis: PreparedAnalysis
    legacy_prepared: Any


def prepare_solver_execution_request(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    timeout_seconds: Any,
) -> PreparedFEMSolverExecution:
    request = _legacy.prepare_solver_execution_request(
        document,
        document_uid,
        target=target,
        timeout_seconds=timeout_seconds,
    )
    try:
        analysis = _prepared_contract(request, document_uid=document_uid)
    except Exception:
        _legacy.discard_solver_execution_request(request)
        raise
    return PreparedFEMSolverExecution(
        analysis=analysis,
        legacy_request=request,
    )


def discard_solver_execution_request(
    request: PreparedFEMSolverExecution,
) -> None:
    if not isinstance(request, PreparedFEMSolverExecution):
        raise TypeError("request must be PreparedFEMSolverExecution")
    _legacy.discard_solver_execution_request(request.legacy_request)


def run_solver_execution(
    request: PreparedFEMSolverExecution,
    *,
    cancelled: Any,
    progress: Any,
) -> CompletedFEMSolverExecution:
    if not isinstance(request, PreparedFEMSolverExecution):
        raise TypeError("request must be PreparedFEMSolverExecution")
    prepared = _legacy.run_solver_execution(
        request.legacy_request,
        cancelled=cancelled,
        progress=progress,
    )
    return CompletedFEMSolverExecution(
        analysis=request.analysis,
        legacy_prepared=prepared,
    )


def commit_solver_execution(
    document: Any,
    prepared: CompletedFEMSolverExecution,
) -> Any:
    if not isinstance(prepared, CompletedFEMSolverExecution):
        raise TypeError("prepared must be CompletedFEMSolverExecution")
    return _legacy.commit_solver_execution(document, prepared.legacy_prepared)


def verify_solver_execution(document: Any, draft: Any) -> Mapping[str, Any]:
    return _legacy.verify_solver_execution(document, draft)
