# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for detached FEM solver execution."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    commit_solver_execution,
    discard_solver_execution_request,
    prepare_solver_execution_request,
    run_solver_execution,
    verify_solver_execution,
)
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeAnalyzeSolverExecutionRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        _operation, values = strict_variant_arguments(
            arguments,
            {"run": frozenset({"target", "timeout_seconds"})},
        )
        context = self._context
        context.guard()
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeAnalyzeError(
                "Background FEM solver execution is unavailable in this session.",
                error_code="NATIVE_ANALYZE_SOLVER_BACKGROUND_UNAVAILABLE",
            )
        request = prepare_solver_execution_request(
            context.document,
            context.document_uid,
            **values,
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_solver_execution(request, cancelled=cancelled, progress=progress)

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Import {request.target.kind.title()} FEM Results",
                mutate=lambda document: commit_solver_execution(document, prepared),
                verify=verify_solver_execution,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="analyze.solver_execution.run",
                prepare=prepare,
                validate_before_commit=context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Importing verified FEM results",
                cleanup=lambda _prepared: discard_solver_execution_request(request),
            )
        except NativeBackgroundError as exc:
            discard_solver_execution_request(request)
            raise NativeAnalyzeError(
                str(exc),
                error_code="NATIVE_ANALYZE_SOLVER_QUEUE_FAILED",
            ) from exc
        except Exception:
            discard_solver_execution_request(request)
            raise
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "phase": str(snapshot.phase),
                "progress_percent": int(snapshot.progress_percent),
                "progress_message": str(snapshot.progress_message),
                "terminal": bool(snapshot.terminal),
            },
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
