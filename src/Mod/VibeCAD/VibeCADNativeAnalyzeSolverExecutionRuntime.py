# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for detached FEM solver execution."""

from __future__ import annotations

from typing import Any, Mapping

from analysis_fem_execution_route import (
    ANALYSIS_RUNTIME_FEM,
    LEGACY_FEM_EXECUTION,
    current_fem_execution_route,
)
import VibeCADNativeAnalyzeSolverExecution as legacy_solver_execution
from tool_impl.analysis_fem_adapter import (
    adopt_isolated_solver_execution,
    commit_solver_execution,
    verify_solver_execution,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    capture_solver_execution_request,
    validate_captured_solver_execution,
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
        execution_route = current_fem_execution_route()
        workspace = None
        request = None

        if execution_route == LEGACY_FEM_EXECUTION:
            request = legacy_solver_execution.prepare_solver_execution_request(
                context.document,
                context.document_uid,
                **values,
            )

            def prepare(cancelled: Any, progress: Any) -> Any:
                return legacy_solver_execution.run_solver_execution(
                    request,
                    cancelled=cancelled,
                    progress=progress,
                )

            def validate() -> None:
                context.guard()

            mutate = lambda document, prepared: (
                legacy_solver_execution.commit_solver_execution(document, prepared)
            )
            verify = legacy_solver_execution.verify_solver_execution
            target_kind = str(request.target.kind)

            def cleanup_route() -> None:
                legacy_solver_execution.discard_solver_execution_request(request)

        elif execution_route == ANALYSIS_RUNTIME_FEM:
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
                prepared = execute_frozen_solver_execution(
                    frozen,
                    cancelled=cancelled,
                    progress=progress,
                )
                return adopt_isolated_solver_execution(
                    prepared,
                    document_uid=context.document_uid,
                )

            def validate() -> None:
                context.guard()
                validate_captured_solver_execution(context.document, captured)

            mutate = lambda document, prepared: commit_solver_execution(
                document, prepared
            )
            verify = verify_solver_execution
            target_kind = str(captured.target.kind)

            def cleanup_route() -> None:
                workspace.cleanup()

        else:  # The route helper validates this invariant; fail closed if corrupted.
            raise NativeAnalyzeError(
                "The internal FEM execution route is invalid.",
                error_code="NATIVE_ANALYZE_SOLVER_ROUTE_INVALID",
            )

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=(f"Import {target_kind.title()} FEM Results"),
                mutate=lambda document: mutate(document, prepared),
                verify=verify,
            )

        def cleanup(_prepared: Any) -> None:
            cleanup_route()
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
            )
        except NativeBackgroundError as exc:
            cleanup_route()
            raise NativeAnalyzeError(
                str(exc),
                error_code="NATIVE_ANALYZE_SOLVER_QUEUE_FAILED",
            ) from exc
        except Exception:
            cleanup_route()
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
                    target_kind,
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
