# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtimes for exact CAM tool catalog and mutations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureTool import (
    ToolBitUpdateSpec,
    ToolCatalogTarget,
    ToolControllerCreateSpec,
    ToolControllerSettings,
    ToolControllerUpdateSpec,
    create_tool_controller,
    preflight_tool_bit_update,
    preflight_tool_controller_create,
    preflight_tool_controller_update,
    update_tool_bit,
    update_tool_controller,
    verify_created_tool_controller,
    verify_updated_tool_bit,
    verify_updated_tool_controller,
)
from VibeCADNativeManufactureToolState import (
    capture_tool_catalog,
    catalog_tool_detail,
    resolve_catalog_record,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_READ_VARIANTS = {
    "list_tools": frozenset({"query", "offset", "page_size"}),
    "read_tool": frozenset({"catalog_tool"}),
}
_MUTATION_VARIANTS = {
    "create_controller": frozenset(
        {
            "job_target",
            "catalog_tool",
            "tool_label",
            "tool_property_changes",
            "controller",
        }
    ),
    "update_controller": frozenset({"target", "controller"}),
    "update_tool_bit": frozenset({"target", "label", "property_changes"}),
}


def _controller(value: Mapping[str, Any]) -> ToolControllerSettings:
    return ToolControllerSettings(**dict(value))


def _catalog_target(value: Mapping[str, Any]) -> ToolCatalogTarget:
    return ToolCatalogTarget(
        catalog_id=value["catalog_id"],
        expected_content_sha256=value["expected_content_sha256"],
    )


class NativeManufactureToolCatalogRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context
        self._catalog_state_sha256 = capture_tool_catalog().state_sha256

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            _READ_VARIANTS,
            defaults={
                "list_tools": {"query": "", "offset": 0, "page_size": 32}
            },
        )
        self._context.guard(allow_owned_cam_simulation=True)
        if operation == "list_tools":
            catalog = capture_tool_catalog()
            if catalog.state_sha256 != self._catalog_state_sha256:
                raise NativeManufactureError(
                    "The CAM tool catalog changed after turn start.",
                    error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_STALE",
                    repair={"current_catalog_state_sha256": catalog.state_sha256},
                )
            return catalog.page(
                values["offset"],
                values["page_size"],
                query=values["query"],
            )
        target = values["catalog_tool"]
        catalog, record = resolve_catalog_record(
            target["catalog_id"],
            target["expected_content_sha256"],
        )
        return {
            "catalog_state_sha256": catalog.state_sha256,
            "tool": catalog_tool_detail(record),
        }


class NativeManufactureToolRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            _MUTATION_VARIANTS,
            defaults={
                "create_controller": {
                    "tool_label": None,
                    "tool_property_changes": [],
                    "controller": None,
                }
            },
        )
        context = self._context
        context.guard()
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        current = context.state.current_revision(context.document_uid)
        if current != ticket.expected_revision:
            raise NativeRevisionConflict(ticket.expected_revision, current)
        if operation == "create_controller":
            prepared = preflight_tool_controller_create(
                context.document,
                ToolControllerCreateSpec(
                    job_target=values["job_target"],
                    catalog_tool=_catalog_target(values["catalog_tool"]),
                    tool_label=values["tool_label"],
                    tool_property_changes=tuple(values["tool_property_changes"]),
                    controller=(
                        _controller(values["controller"])
                        if values["controller"] is not None
                        else None
                    ),
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Create Native CAM Tool Controller",
                mutate=partial(create_tool_controller, prepared=prepared),
                verify=verify_created_tool_controller,
            )
        if operation == "update_controller":
            prepared = preflight_tool_controller_update(
                context.document,
                ToolControllerUpdateSpec(
                    target=values["target"],
                    controller=_controller(values["controller"]),
                ),
            )
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Update Native CAM Tool Controller",
                mutate=partial(update_tool_controller, prepared=prepared),
                verify=verify_updated_tool_controller,
            )
        prepared = preflight_tool_bit_update(
            context.document,
            ToolBitUpdateSpec(
                target=values["target"],
                label=values["label"],
                property_changes=tuple(values["property_changes"]),
            ),
        )
        return run_immediate_mutation(
            context,
            ticket=ticket,
            transaction_name="Update Native CAM ToolBit",
            mutate=partial(update_tool_bit, prepared=prepared),
            verify=verify_updated_tool_bit,
        )
