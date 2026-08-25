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

from VibeCADAnalysisContracts import (
    AnalysisCommand,
    DependencyRecord,
    DependencySnapshot,
    ExecutionSpec,
    PreparedAnalysis,
    PreparedInputManifest,
    environment_sha256,
    json_sha256,
)
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


def _prepared_contract(
    request: _legacy.SolverExecutionRequest,
    *,
    document_uid: str,
) -> PreparedAnalysis:
    target = request.target
    solver = target.solver
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
            storage_reference=str(request.working_directory),
            sha256=str(request.input_sha256),
            file_count=int(request.input_file_count),
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
