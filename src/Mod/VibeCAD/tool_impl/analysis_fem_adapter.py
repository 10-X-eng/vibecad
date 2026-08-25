# SPDX-License-Identifier: LGPL-2.1-or-later

"""FEM compatibility adapter onto the host Analysis contract boundary.

This migration adapter deliberately leaves the proven FEM implementation
underneath. It creates a domain-neutral, serializable PreparedAnalysis identity
for the host seam while retaining the legacy request only as transient in-process
state until later migration stages move orchestration and publication ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
from tool_impl.analysis_local_provider import LocalProcessProvider
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import ExternalProcessCancelled, ExternalProcessError
import VibeCADNativeAnalyzeSolverExecution as _legacy


FEM_ANALYSIS_ADAPTER_ID = "vibecad.native.analyze.fem"
FEM_ANALYSIS_ADAPTER_VERSION = "legacy-compat-v1"
MAX_SOLVER_LOG_BYTES = 16 * 1024 * 1024
_LOCAL_PROCESS_PROVIDER = LocalProcessProvider()


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


def _uses_host_local_provider(request: _legacy.SolverExecutionRequest) -> bool:
    """All currently supported FEM solver execution uses the host local provider."""

    return str(request.target.kind) in {"calculix", "elmer", "z88", "mystran"}


def _run_local_solver(
    request: _legacy.SolverExecutionRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> _legacy.PreparedSolverExecution:
    """Run a migrated FEM solver through the host provider with legacy-exact mapping."""

    commands = request.commands
    backend = request.target.kind.title()
    if not commands:
        raise NativeAnalyzeError("The detached FEM solver has no executable command.")
    if not Path(request.working_directory).is_dir():
        raise NativeAnalyzeError(
            f"{backend} stage 1 could not be started.",
            error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
        )

    def stage_started(stage: int, total: int) -> None:
        base_progress = 12 + int(65 * (stage - 1) / total)
        progress(base_progress, f"Running {backend} stage {stage}/{total}")

    try:
        stages = _LOCAL_PROCESS_PROVIDER.run_sequence(
            commands,
            working_directory=request.working_directory,
            environment=request.environment,
            timeout_seconds=request.timeout_seconds,
            cancellation_check=cancelled,
            log_name=lambda stage: f"solver-{stage}.log",
            stage_started=stage_started,
            maximum_log_bytes=MAX_SOLVER_LOG_BYTES,
        )
    except ExternalProcessCancelled as exc:
        raise NativeBackgroundCancelled() from exc
    except ExternalProcessError as exc:
        if exc.reason == "timeout":
            raise NativeAnalyzeError(
                f"{backend} exceeded timeout_seconds before producing results.",
                error_code="NATIVE_ANALYZE_SOLVER_TIMEOUT",
            ) from exc
        if exc.reason == "output_limit":
            raise NativeAnalyzeError(
                f"{backend} exceeded the 16 MiB diagnostic-output bound.",
                error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
            ) from exc
        if exc.reason == "start_failed":
            raise NativeAnalyzeError(
                f"{backend} stage {exc.stage} could not be started.",
                error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
            ) from exc
        suffix = f": {exc.detail}" if exc.detail else ""
        raise NativeAnalyzeError(
            f"{backend} stage {exc.stage} exited with code {exc.exit_code}{suffix}",
            error_code="NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
        ) from exc

    progress(84, f"{backend} result artifacts ready")
    summaries = tuple(
        {
            "stage": stage.stage,
            "program": Path(stage.program).name,
            "exit_code": stage.exit_code,
        }
        for stage in stages
    )
    return _legacy.PreparedSolverExecution(request, summaries)


def run_solver_execution(
    request: PreparedFEMSolverExecution,
    *,
    cancelled: Any,
    progress: Any,
) -> CompletedFEMSolverExecution:
    if not isinstance(request, PreparedFEMSolverExecution):
        raise TypeError("request must be PreparedFEMSolverExecution")
    legacy_request = request.legacy_request
    if not _uses_host_local_provider(legacy_request):
        prepared = _legacy.run_solver_execution(
            legacy_request,
            cancelled=cancelled,
            progress=progress,
        )
    else:
        try:
            progress(7, "FEM solver input frozen")
            prepared = _run_local_solver(
                legacy_request,
                cancelled=cancelled,
                progress=progress,
            )
        except Exception:
            _legacy.discard_solver_execution_request(legacy_request)
            raise
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
