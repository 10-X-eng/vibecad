# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact human-authorized Assembly exports."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyExport import (
    AssemblyAsmtExportSpec,
    NativeAssemblyExportError,
    export_assembly_asmt,
    preflight_assembly_asmt_export,
    require_current_output_ticket,
)
from VibeCADNativeOutput import NativeOutputError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


class NativeAssemblyExportRuntime:
    """Export from one frozen Assemble turn without provider path authority."""

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
                "asmt": frozenset(
                    {
                        "assembly",
                        "expected_state_sha256",
                        "expected_component_count",
                        "expected_grounded_count",
                        "expected_joint_count",
                    }
                )
            },
        )
        if operation != "asmt":
            raise NativeAssemblyExportError(
                "The Assembly export operation is not implemented."
            )
        assembly = values["assembly"]
        if not isinstance(assembly, Mapping) or set(assembly) != {"object_name"}:
            raise NativeAssemblyExportError(
                "assembly must contain one exact object_name."
            )
        try:
            reference = NativeObjectRef(
                self._context.document_uid,
                str(assembly["object_name"]),
            )
        except Exception as exc:
            raise NativeAssemblyExportError(
                "assembly.object_name must identify one exact Assembly."
            ) from exc
        spec = AssemblyAsmtExportSpec(
            assembly_ref=reference,
            expected_state_sha256=values["expected_state_sha256"],
            expected_component_count=values["expected_component_count"],
            expected_grounded_count=values["expected_grounded_count"],
            expected_joint_count=values["expected_joint_count"],
        )
        require_current_output_ticket(self._context, ticket)
        prepared = preflight_assembly_asmt_export(self._context, spec)
        authorizer = self._context.authorize_output
        if authorizer is None:
            raise NativeAssemblyExportError(
                "Human output authorization is unavailable in this VibeCAD session."
            )
        try:
            authorization = authorizer(prepared.output_request)
        except NativeOutputError as exc:
            raise NativeAssemblyExportError(str(exc)) from exc
        if authorization is None:
            raise NativeAssemblyExportError(
                "The human cancelled ASMT output authorization."
            )
        require_current_output_ticket(self._context, ticket)
        return export_assembly_asmt(
            self._context,
            prepared,
            authorization,
            ticket,
        )
