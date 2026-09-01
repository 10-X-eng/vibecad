# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound background runtime for complete-Job CAM postprocessing."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostInput import (
    cleanup_post,
    preflight_post,
    preflight_selected_post,
    validate_post_source,
)
from VibeCADNativeManufacturePostWorker import (
    PreparedPostOutput,
    output_requests,
    prepare_post,
    publish_post,
)
from VibeCADNativeOutput import NativeOutputError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "resource_scope": str(snapshot.resource_scope),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
    }


class NativeManufacturePostRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def execute(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "complete_job": frozenset({"job"}),
                "selected_operations": frozenset({"job", "operations"}),
            },
        )
        if operation not in {
            "complete_job",
            "selected_operations",
        } or not isinstance(ticket, NativeCallTicket):
            raise TypeError("CAM postprocessing requires one exact Native call ticket")
        context = self._context
        context.guard()
        revision = context.state.current_revision(context.document_uid)
        if revision != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, revision)
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        authorizer = context.authorize_output
        if manager is None or dispatcher is None or authorizer is None:
            raise NativeManufactureError(
                "Background human-authorized CAM postprocessing is unavailable in this session.",
                error_code="NATIVE_MANUFACTURE_POST_UNAVAILABLE",
            )
        if operation == "complete_job":
            frozen = preflight_post(context.document, job=values["job"])
        else:
            frozen = preflight_selected_post(
                context.document,
                job=values["job"],
                operations=values["operations"],
            )

        def guard() -> None:
            context.guard()
            current = context.state.current_revision(context.document_uid)
            if current != ticket.expected_revision:
                raise NativeRevisionConflict(ticket.expected_revision, current)
            validate_post_source(context.document, frozen)

        def prepare(cancelled: Any, progress: Any) -> PreparedPostOutput:
            return prepare_post(frozen, cancelled=cancelled, progress=progress)

        def commit(prepared: Any) -> Mapping[str, Any]:
            if not isinstance(prepared, PreparedPostOutput):
                raise TypeError("The CAM post worker returned the wrong result type")
            requests = output_requests(prepared)
            authorizations = []
            for request in requests:
                guard()
                try:
                    authorization = authorizer(request)
                except NativeOutputError as exc:
                    raise NativeManufactureError(str(exc), error_code=exc.code) from exc
                if authorization is None:
                    raise NativeManufactureError(
                        "The human cancelled CAM program output authorization; no file was written.",
                        error_code="NATIVE_MANUFACTURE_POST_OUTPUT_CANCELLED",
                    )
                authorizations.append(authorization)
            guard()
            artifacts = publish_post(
                prepared,
                requests,
                tuple(authorizations),
                guard=guard,
            )
            guard()
            result = {
                "operation": operation,
                "job": {
                    "object_name": str(frozen.job_before["object_name"]),
                    "state_sha256": str(frozen.job_before["state_sha256"]),
                    "posted_operation_count": frozen.active_operation_count,
                    "command_count": frozen.command_count,
                },
                "postprocessor": {
                    "name": frozen.postprocessor_name,
                    "source_sha256": frozen.postprocessor_source.sha256,
                    "machine_configured": frozen.use_machine_flow,
                    "machine_config_sha256": frozen.machine_config_sha256,
                },
                "outputs": [artifact.summary() for artifact in artifacts],
                "output_count": len(artifacts),
                "total_size_bytes": prepared.total_size_bytes,
                "document_unchanged": True,
                "history_unchanged": True,
                "selection_unchanged": True,
                "visibility_unchanged": True,
            }
            if operation == "complete_job":
                result["job"]["active_operation_count"] = (
                    frozen.active_operation_count
                )
            else:
                result["operations"] = [
                    {
                        "object_name": name,
                        "state_sha256": state,
                    }
                    for name, state in zip(
                        frozen.selected_operation_names,
                        frozen.selected_operation_state_sha256,
                        strict=True,
                    )
                ]
            return result

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=f"manufacture.post.{operation}",
                prepare=prepare,
                validate_before_commit=guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Requesting human CAM output destinations",
                cleanup=lambda _prepared: cleanup_post(frozen),
                resource_scope=f"manufacture:{frozen.job_name}",
            )
        except NativeBackgroundError as exc:
            cleanup_post(frozen)
            raise NativeManufactureError(
                str(exc),
                error_code="NATIVE_MANUFACTURE_POST_QUEUE_FAILED",
            ) from exc
        except Exception:
            cleanup_post(frozen)
            raise
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": str(snapshot.job_id),
            },
        }
