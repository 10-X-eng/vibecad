# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for detached FEM solver execution."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    capture_solver_execution_request,
    commit_solver_execution,
    solver_resource_scope,
    validate_captured_solver_execution,
    verify_solver_execution,
)
from VibeCADNativeAnalyzeSolverExecutionInput import (
    create_solver_execution_workspace,
    freeze_solver_execution_snapshot,
    materialize_solver_execution_snapshot,
)
from VibeCADNativeAnalyzeSolverExecutionWorker import (
    execute_frozen_solver_execution,
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
        captured = capture_solver_execution_request(
            context.document,
            context.document_uid,
            **values,
        )
        workspace = create_solver_execution_workspace()

        def prepare(cancelled: Any, progress: Any) -> Any:
            progress(3, "Capturing exact FEM document")
            materialized = dispatcher(
                lambda: materialize_solver_execution_snapshot(
                    context.document,
                    captured,
                    workspace,
                )
            )
            progress(5, "Authenticating exact FEM document snapshot")
            frozen = freeze_solver_execution_snapshot(materialized)
            return execute_frozen_solver_execution(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            context.guard()
            validate_captured_solver_execution(context.document, captured)

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=(f"Import {captured.target.kind.title()} FEM Results"),
                mutate=lambda document: commit_solver_execution(document, prepared),
                verify=verify_solver_execution,
            )

        def cleanup(_prepared: Any) -> None:
            workspace.cleanup()
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name="analyze.solver_execution.run",
                prepare=prepare,
                validate_before_commit=validate,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Importing verified FEM results",
                cleanup=cleanup,
                changes_document=True,
                resource_scope=solver_resource_scope(captured),
            )
        except NativeBackgroundError as exc:
            workspace.cleanup()
            raise NativeAnalyzeError(
                str(exc),
                error_code="NATIVE_ANALYZE_SOLVER_QUEUE_FAILED",
            ) from exc
        except Exception:
            workspace.cleanup()
            raise
        try:
            def watch_status() -> None:
                import FreeCAD as App

                if not bool(getattr(App, "GuiUp", False)):
                    return
                from VibeCADAnalyzeSolverGui import watch_solver_job

                watch_solver_job(
                    manager,
                    str(snapshot.job_id),
                    str(captured.target.kind),
                )

            dispatcher(watch_status)
        except Exception:
            # Status presentation must never invalidate an already accepted job.
            pass
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
                "poll_after_seconds": 30,
                "guidance": (
                    "Continue polling until terminal. Do not cancel solely because "
                    "progress is slow or unchanged."
                ),
            },
        }
