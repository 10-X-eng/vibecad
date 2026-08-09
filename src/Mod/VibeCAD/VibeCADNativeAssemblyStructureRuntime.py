# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Native Assembly structure operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeAssemblyComponents import (
    AssemblySourceRef,
    CreatePartSpec,
    InsertComponentSpec,
    NativeAssemblyComponentError,
    create_part,
    insert_component,
    preflight_create_part,
    preflight_insert_component,
    verify_created_part,
    verify_inserted_component,
)
from VibeCADNativeAssemblyStructure import (
    AssemblyCreateSpec,
    NativeAssemblyStructureError,
    create_assembly,
    preflight_create_assembly,
    verify_created_assembly,
)
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeDesignResults import placement_from_mapping
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import NativeObjectRef


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAssemblyStructureError(
            "An Assembly label must contain 1 to 160 characters."
        )
    return result


def _expected_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
        raise NativeAssemblyStructureError(
            "expected_assembly_count must be an integer from 0 through 10000."
        )
    return value


def _expected_component_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
        raise NativeAssemblyComponentError(
            "expected_component_count must be an integer from 0 through 100000."
        )
    return value


def _object_ref(document_uid: str, value: Any, field: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeAssemblyComponentError(
            f"{field} must be one exact object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def _source_ref(value: Any) -> AssemblySourceRef:
    required = {"document_uid", "document_name", "object_name", "object_id"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAssemblyComponentError(
            "source must be one exact source reference from current Assemble state."
        )
    object_id = value["object_id"]
    if (
        isinstance(object_id, bool)
        or not isinstance(object_id, int)
        or not 1 <= object_id <= 2_147_483_647
    ):
        raise NativeAssemblyComponentError("source.object_id is invalid.")
    parts = {
        name: str(value[name] or "").strip()
        for name in ("document_uid", "document_name", "object_name")
    }
    if any(not item or len(item) > 128 for item in parts.values()):
        raise NativeAssemblyComponentError("source identity text is invalid.")
    return AssemblySourceRef(
        parts["document_uid"],
        parts["document_name"],
        parts["object_name"],
        object_id,
    )


def _placement(value: Any) -> Any:
    try:
        return placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise NativeAssemblyComponentError(
            "placement must contain a finite origin and non-zero axis rotation."
        ) from exc


class NativeAssemblyStructureRuntime:
    """Execute only structure operations from one frozen Assemble turn."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def _parent_ref(self, value: Any) -> NativeObjectRef | None:
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"object_name"}:
            raise NativeAssemblyStructureError(
                "parent_assembly must be null or one exact Assembly reference."
            )
        return NativeObjectRef(
            self._context.document_uid,
            str(value.get("object_name") or ""),
        )

    def mutate_structure(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_assembly": frozenset(
                    {
                        "label",
                        "parent_assembly",
                        "expected_assembly_count",
                    }
                ),
                "insert_component": frozenset(
                    {
                        "assembly",
                        "source",
                        "label",
                        "placement",
                        "rigid",
                        "expected_component_count",
                    }
                ),
                "create_part": frozenset(
                    {
                        "assembly",
                        "label",
                        "placement",
                        "expected_component_count",
                    }
                ),
            },
        )
        if operation == "create_assembly":
            spec = AssemblyCreateSpec(
                label=_label(values["label"]),
                parent_ref=self._parent_ref(values["parent_assembly"]),
                expected_assembly_count=_expected_count(
                    values["expected_assembly_count"]
                ),
            )
            self._context.guard()
            preflight_create_assembly(self._context.document, spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly",
                mutate=lambda document: create_assembly(document, spec),
                verify=verify_created_assembly,
            )
        assembly_ref = _object_ref(
            self._context.document_uid,
            values["assembly"],
            "assembly",
        )
        expected_components = _expected_component_count(
            values["expected_component_count"]
        )
        if operation == "insert_component":
            rigid = values["rigid"]
            if rigid is not None and not isinstance(rigid, bool):
                raise NativeAssemblyComponentError(
                    "rigid must be true, false, or null."
                )
            insert_spec = InsertComponentSpec(
                assembly_ref=assembly_ref,
                source_ref=_source_ref(values["source"]),
                label=_label(values["label"]),
                placement=_placement(values["placement"]),
                rigid=rigid,
                expected_component_count=expected_components,
            )
            self._context.guard()
            preflight_insert_component(self._context.document, insert_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Insert Native Assembly Component",
                mutate=lambda document: insert_component(document, insert_spec),
                verify=verify_inserted_component,
            )
        if operation == "create_part":
            part_spec = CreatePartSpec(
                assembly_ref=assembly_ref,
                label=_label(values["label"]),
                placement=_placement(values["placement"]),
                expected_component_count=expected_components,
            )
            self._context.guard()
            preflight_create_part(self._context.document, part_spec)
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Assembly Part",
                mutate=lambda document: create_part(document, part_spec),
                verify=verify_created_part,
            )
        raise NativeAssemblyStructureError(
            "The Assembly structure operation is not implemented."
        )
