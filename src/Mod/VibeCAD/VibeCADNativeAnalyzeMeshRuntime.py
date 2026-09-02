# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for durable FEM mesh definitions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeOwnership import owning_study, study_resource_scope
from VibeCADNativeAnalyzeGmshGeneration import (
    commit_gmsh_generation,
    discard_gmsh_generation_request,
    prepare_gmsh_generation_request,
    run_gmsh_generation,
    verify_gmsh_generation,
)
from VibeCADNativeAnalyzeMeshCreate import (
    create_mesh_definition,
    prepare_mesh_definition_create,
    verify_mesh_definition_create,
)
from VibeCADNativeAnalyzeMeshEdit import (
    prepare_mesh_definition_update,
    update_mesh_definition,
    verify_mesh_definition_update,
)
from VibeCADNativeAnalyzeNetgenGeneration import (
    commit_netgen_generation,
    discard_netgen_generation_request,
    prepare_netgen_generation_request,
    run_netgen_generation,
    verify_netgen_generation,
)
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_CREATE = {"create_gmsh": "gmsh", "create_netgen": "netgen"}
_UPDATE = {"update_gmsh": "gmsh", "update_netgen": "netgen"}
_GENERATE = {"generate_gmsh": "gmsh", "generate_netgen": "netgen"}


def _arguments(arguments: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise NativeAnalyzeError("Analyze mesh arguments must be one object.")
    values = dict(arguments)
    operation = str(values.pop("operation", "") or "")
    if operation in _CREATE:
        required = {"analysis", "source", "label", "settings"}
        kind = _CREATE[operation]
    elif operation in _UPDATE:
        kind = _UPDATE[operation]
        allowed = {"target", "label", "source", "settings"}
        provided = set(values)
        if "target" not in provided or len(provided) < 2 or not provided <= allowed:
            details = []
            if "target" not in provided:
                details.append("missing target")
            if len(provided - {"target"}) < 1:
                details.append("missing at least one editable field")
            extra = sorted(provided - allowed)
            if extra:
                details.append(f"unexpected {', '.join(extra)}")
            raise NativeAnalyzeError(
                "Analyze mesh arguments do not match the selected operation"
                + (f": {'; '.join(details)}." if details else ".")
            )
        return operation, kind, values
    elif operation in _GENERATE:
        required = {"target", "timeout_seconds"}
        kind = _GENERATE[operation]
    else:
        raise NativeAnalyzeError("The Analyze mesh operation is unavailable.")
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise NativeAnalyzeError(
            "Analyze mesh arguments do not match the selected operation"
            + (f": {'; '.join(details)}." if details else ".")
        )
    return operation, kind, values


class NativeAnalyzeMeshRuntime:
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
        operation, kind, values = _arguments(arguments)
        context = self._context
        context.guard()
        if operation in _GENERATE:
            return self._start_generation(kind, values, ticket)
        if operation in _CREATE:
            prepared = prepare_mesh_definition_create(
                context.document,
                context.document_uid,
                kind=kind,
                **values,
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Create FEM {kind.title()} Mesh Definition",
                mutate=lambda document: create_mesh_definition(document, prepared),
                verify=verify_mesh_definition_create,
            )
        target = values.pop("target")
        prepared = prepare_mesh_definition_update(
            context.document,
            context.document_uid,
            kind=kind,
            target=target,
            changes=values,
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name=f"Edit FEM {kind.title()} Mesh Definition",
            mutate=lambda document: update_mesh_definition(document, prepared),
            verify=verify_mesh_definition_update,
        )

    def _start_generation(
        self,
        kind: str,
        values: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        context = self._context
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        if manager is None or dispatcher is None:
            raise NativeAnalyzeError(
                "Background FEM mesh generation is unavailable in this session.",
                error_code="NATIVE_ANALYZE_MESH_BACKGROUND_UNAVAILABLE",
            )
        if kind == "gmsh":
            request = prepare_gmsh_generation_request(
                context.document,
                context.document_uid,
                **values,
            )
            runner = run_gmsh_generation
            committer = commit_gmsh_generation
            verifier = verify_gmsh_generation
            discard = discard_gmsh_generation_request
        else:
            request = prepare_netgen_generation_request(
                context.document,
                context.document_uid,
                **values,
            )
            runner = run_netgen_generation
            committer = commit_netgen_generation
            verifier = verify_netgen_generation
            discard = discard_netgen_generation_request
        scope = study_resource_scope(
            owning_study(context.document, request.target.mesh)
        )

        def prepare(cancelled: Any, progress: Any) -> Any:
            return runner(request, cancelled=cancelled, progress=progress)

        def commit(prepared: Any) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name=f"Generate FEM {kind.title()} Mesh",
                mutate=lambda document: committer(document, prepared),
                verify=verifier,
            )

        def cleanup(_prepared: Any) -> None:
            discard(request)
            context.state.cancel_mutation(ticket)

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"analyze.mesh.generate_{kind}",
                prepare=prepare,
                validate_before_commit=context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Importing verified FEM mesh",
                cleanup=cleanup,
                changes_document=True,
                resource_scope=scope,
            )
        except NativeBackgroundError as exc:
            discard(request)
            raise NativeAnalyzeError(
                str(exc),
                error_code="NATIVE_ANALYZE_MESH_QUEUE_FAILED",
            ) from exc
        except Exception:
            discard(request)
            raise
        return {
            "job": {
                "job_id": str(snapshot.job_id),
                "capability": str(snapshot.capability_name),
                "resource_scope": str(snapshot.resource_scope),
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
