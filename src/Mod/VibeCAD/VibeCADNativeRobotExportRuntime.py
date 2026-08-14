# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Robot program output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeOutput import NativeOutputError
from VibeCADNativeRobotExport import (
    NativeRobotExportError,
    export_robot_program,
    preflight_robot_export,
    prepare_robot_export_spec,
    require_current_robot_export_ticket,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


_FIELDS = frozenset(
    {
        "robot",
        "trajectory",
        "expected_robot_setup_state_sha256",
        "expected_robot_state_sha256",
        "expected_trajectory_setup_state_sha256",
        "expected_trajectory_state_sha256",
    }
)


class NativeRobotExportRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def export(
        self,
        arguments: Mapping[str, Any],
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "export_kuka_compact": _FIELDS,
                "export_kuka_full": _FIELDS,
            },
        )
        context = self._context
        require_current_robot_export_ticket(context, ticket)
        spec = prepare_robot_export_spec(context.document_uid, operation, values)
        prepared = preflight_robot_export(context, spec)
        authorizer = context.authorize_output
        if authorizer is None:
            raise NativeRobotExportError(
                "Human output authorization is unavailable in this VibeCAD session."
            )
        authorizations = []
        requests = [prepared.output_request]
        if prepared.data_output_request is not None:
            requests.append(prepared.data_output_request)
        for request in requests:
            try:
                authorization = authorizer(request)
            except NativeOutputError as exc:
                raise NativeRobotExportError(str(exc), code=exc.code) from exc
            if authorization is None:
                raise NativeRobotExportError(
                    "The human cancelled KUKA output authorization.",
                    code="NATIVE_ROBOT_EXPORT_CANCELLED",
                )
            authorizations.append(authorization)
        require_current_robot_export_ticket(context, ticket)
        return export_robot_program(
            context,
            prepared,
            authorizations[0],
            ticket,
            data_authorization=(
                authorizations[1] if len(authorizations) == 2 else None
            ),
        )
