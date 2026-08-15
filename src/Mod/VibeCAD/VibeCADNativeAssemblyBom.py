# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of one native Assembly bill of materials."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from VibeCADNativeAssemblyBomState import (
    MAX_BOM_COLUMNS,
    MAX_BOM_OPERATIONS,
    AssemblyBomState,
    capture_assembly_bom_state,
)
from VibeCADNativeAssemblyJointConnectors import placement_is_same
from VibeCADNativeAssemblySolveState import (
    AssemblySolverState,
    capture_assembly_solver_state,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_BOM_FAILED = "NATIVE_ASSEMBLY_BOM_FAILED"
_PROPERTY_COLUMN = re.compile(r"^\.[A-Za-z_][A-Za-z0-9_]{0,127}$")


class NativeAssemblyBomError(NativeMutationError):
    """The requested Assembly BOM could not be created exactly."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_BOM_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblyBomCreateSpec:
    assembly_ref: NativeObjectRef
    label: str
    columns: tuple[str, ...]
    detail_subassemblies: bool
    detail_parts: bool
    only_parts: bool
    expected_bom_state_sha256: str
    expected_component_count: int
    expected_bom_count: int


@dataclass(frozen=True, slots=True)
class PreparedAssemblyBom:
    spec: AssemblyBomCreateSpec
    state: AssemblyBomState
    columns: tuple[str, ...]
    active_before: Any
    selection_before: dict[str, Any]
    solver_before: AssemblySolverState


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _exact_digest(value: Any) -> str:
    result = str(value or "")
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise NativeAssemblyBomError(
            "expected_bom_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyBomError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _label(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeAssemblyBomError("A BOM label must be text.")
    result = value.strip()
    if not 1 <= len(result) <= 160 or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise NativeAssemblyBomError(
            "A BOM label must contain 1 through 160 printable characters."
        )
    return result


def _columns(values: Any) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not 1 <= len(values) <= MAX_BOM_COLUMNS:
        raise NativeAssemblyBomError(
            f"columns must contain 1 through {MAX_BOM_COLUMNS} ordered names."
        )
    result = []
    for index, raw in enumerate(values):
        if not isinstance(raw, str):
            raise NativeAssemblyBomError(f"columns[{index}] must be text.")
        value = raw.strip()
        if (
            not value
            or len(value) > 129
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or (value.startswith(".") and _PROPERTY_COLUMN.fullmatch(value) is None)
        ):
            raise NativeAssemblyBomError(
                f"columns[{index}] is not a valid native BOM column."
            )
        result.append(value)
    if len(set(result)) != len(result):
        raise NativeAssemblyBomError("BOM column names must be unique.")
    return tuple(result)


def _exact_active_assembly(
    document: Any,
    spec: AssemblyBomCreateSpec,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    try:
        assembly = resolve_object(
            document,
            spec.assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
    except Exception as exc:
        raise NativeAssemblyBomError(str(exc)) from exc
    if not same_assembly(assembly, active_reader(document)) or not _timeline_active(
        assembly
    ):
        raise NativeAssemblyBomError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def preflight_create_assembly_bom(
    document: Any,
    spec: AssemblyBomCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyBom:
    """Freeze one exact active Assembly and complete native BOM request."""

    if not isinstance(spec, AssemblyBomCreateSpec):
        raise TypeError("spec must be an AssemblyBomCreateSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    label = _label(spec.label)
    columns = _columns(spec.columns)
    if any(
        type(value) is not bool
        for value in (
            spec.detail_subassemblies,
            spec.detail_parts,
            spec.only_parts,
        )
    ):
        raise NativeAssemblyBomError(
            "detail_subassemblies, detail_parts, and only_parts must be booleans."
        )
    expected_digest = _exact_digest(spec.expected_bom_state_sha256)
    expected_components = _exact_count(
        spec.expected_component_count,
        "expected_component_count",
        100_000,
    )
    expected_boms = _exact_count(
        spec.expected_bom_count,
        "expected_bom_count",
        MAX_BOM_OPERATIONS,
    )
    assembly = _exact_active_assembly(document, spec, active_reader)
    try:
        state = capture_assembly_bom_state(assembly)
        solver = capture_assembly_solver_state(assembly)
    except Exception as exc:
        raise NativeAssemblyBomError(str(exc)) from exc
    if len(state.components) != expected_components or len(state.boms) != expected_boms:
        raise NativeAssemblyBomError(
            "The active Assembly BOM counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != expected_digest:
        raise NativeAssemblyBomError(
            "The active Assembly BOM state changed; read current Assemble state and retry."
        )
    if not state.components:
        raise NativeAssemblyBomError(
            "Insert at least one active component before creating a bill of materials."
        )
    if len(state.boms) >= MAX_BOM_OPERATIONS:
        raise NativeAssemblyBomError(
            f"The Assembly already contains the {MAX_BOM_OPERATIONS}-BOM Native limit."
        )
    selection = selection_reader(document)
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblyBomError(
            "The human-active Assembly changed during BOM preflight."
        )
    return PreparedAssemblyBom(
        spec=AssemblyBomCreateSpec(
            assembly_ref=spec.assembly_ref,
            label=label,
            columns=columns,
            detail_subassemblies=spec.detail_subassemblies,
            detail_parts=spec.detail_parts,
            only_parts=spec.only_parts,
            expected_bom_state_sha256=expected_digest,
            expected_component_count=expected_components,
            expected_bom_count=expected_boms,
        ),
        state=state,
        columns=columns,
        active_before=assembly,
        selection_before=selection,
        solver_before=solver,
    )


def _create_bom_feature(document: Any, assembly: Any) -> Any:
    from CommandCreateBom import createBomFeature

    return createBomFeature(document, assembly)


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    identities = {id(obj) for obj in before}
    return tuple(
        obj
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if id(obj) not in identities
    )


def _bom_group(assembly: Any) -> Any | None:
    groups = tuple(
        child
        for child in tuple(getattr(assembly, "Group", ()) or ())
        if str(getattr(child, "TypeId", "") or "") == "Assembly::BomGroup"
    )
    return groups[0] if len(groups) == 1 else None


def create_assembly_bom(
    document: Any,
    spec: AssemblyBomCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
    factory: Callable[[Any, Any], Any] = _create_bom_feature,
) -> NativeMutationDraft:
    """Create one accepted human-equivalent native BOM in one transaction."""

    prepared = preflight_create_assembly_bom(
        document,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    before_objects = tuple(document.Objects)
    assembly = prepared.state.assembly
    bom = factory(document, assembly)
    group = _bom_group(assembly)
    if (
        bom is None
        or group is None
        or getattr(bom, "Document", None) is not document
        or getattr(group, "Document", None) is not document
        or str(getattr(bom, "TypeId", "") or "") != "Assembly::BomObject"
        or str(getattr(group, "TypeId", "") or "") != "Assembly::BomGroup"
        or bom not in tuple(getattr(group, "Group", ()) or ())
    ):
        raise NativeAssemblyBomError(
            "The native Assembly BOM factory returned the wrong operation graph."
        )
    bom.Label = prepared.spec.label
    bom.columnsNames = list(prepared.columns)
    bom.detailSubAssemblies = prepared.spec.detail_subassemblies
    bom.detailParts = prepared.spec.detail_parts
    bom.onlyParts = prepared.spec.only_parts
    bom.autoGenerate = True
    created_objects = _new_document_objects(document, before_objects)
    semantic_objects: tuple[Any, ...] = (bom,)
    if prepared.state.bom_group is None:
        semantic_objects = (group, bom)
    semantic = set(semantic_objects)
    if not semantic.issubset(set(created_objects)) or any(
        obj not in semantic
        and str(getattr(obj, "TypeId", "") or "") != "App::DocumentTimeline"
        for obj in created_objects
    ):
        raise NativeAssemblyBomError(
            "BOM creation changed objects outside its exact native graph."
        )
    changed = [assembly]
    if prepared.state.bom_group is not None:
        changed.append(group)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "before_objects": before_objects,
            "created_objects": created_objects,
            "bom_group": group,
            "bom": bom,
        },
        recompute_targets=(bom, group, assembly),
        created=tuple(object_identity(obj) for obj in semantic_objects),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _same_solver_state(
    expected: AssemblySolverState,
    current: AssemblySolverState,
) -> bool:
    if len(expected.records) != len(current.records):
        return False
    return all(
        before.obj is after.obj
        and int(before.obj.ID) == int(after.obj.ID)
        and str(before.obj.TypeId) == str(after.obj.TypeId)
        and placement_is_same(before.placement, after.placement)
        and before.placement_locks == after.placement_locks
        for before, after in zip(expected.records, current.records, strict=True)
    )


def _timeline_contains(document: Any, bom: Any) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "") or "") != (
        "App::DocumentTimeline"
    ):
        return False
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(getattr(timeline, "VisibilityAtEnd", ()) or ())
    if len(operations) != len(visibility):
        return False
    try:
        index = operations.index(bom)
    except ValueError:
        return False
    return bool(visibility[index]) == bool(getattr(bom, "Visibility", True))


def _require_created(condition: bool, message: str) -> None:
    if not condition:
        raise NativeAssemblyBomError(message)


def verify_created_assembly_bom(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove native table generation, History ownership, and preserved state."""

    value = draft.value
    prepared: PreparedAssemblyBom = value["prepared"]
    before = prepared.state
    assembly = before.assembly
    group = value["bom_group"]
    bom = value["bom"]
    try:
        import CommandCreateBom

        owner = CommandCreateBom._findBomAssembly(bom)
    except Exception as exc:
        raise NativeAssemblyBomError(
            "The created BOM has no readable native Assembly owner."
        ) from exc
    _require_created(
        document.getObject(str(assembly.Name)) is assembly
        and document.getObject(str(group.Name)) is group
        and document.getObject(str(bom.Name)) is bom,
        "The created BOM graph is no longer live in its exact document.",
    )
    _require_created(
        owner is assembly,
        "The created BOM does not retain its exact Assembly owner.",
    )
    _require_created(
        same_assembly(prepared.active_before, active_reader(document)),
        "The human-active Assembly changed while creating the BOM.",
    )
    _require_created(
        selection_reader(document) == prepared.selection_before,
        "Human selection changed while creating the BOM.",
    )
    _require_created(
        str(getattr(group, "TypeId", "") or "") == "Assembly::BomGroup"
        and group in tuple(getattr(assembly, "Group", ()) or ())
        and tuple(getattr(group, "Group", ()) or ())[-1:] == (bom,),
        "The created BOM is not the final member of its exact native BOM group.",
    )
    _require_created(
        str(getattr(bom, "TypeId", "") or "") == "Assembly::BomObject"
        and str(getattr(bom, "Label", "") or "") == prepared.spec.label
        and tuple(getattr(bom, "columnsNames", ()) or ()) == prepared.columns,
        "The created BOM did not retain its exact type, label, or ordered columns.",
    )
    _require_created(
        bool(bom.detailSubAssemblies) is prepared.spec.detail_subassemblies
        and bool(bom.detailParts) is prepared.spec.detail_parts
        and bool(bom.onlyParts) is prepared.spec.only_parts
        and bool(getattr(bom, "autoGenerate", False)) is True,
        "The created BOM did not retain its exact traversal settings.",
    )
    _require_created(
        _timeline_active(bom),
        "The created BOM is inactive at the current History position.",
    )
    _require_created(
        _timeline_contains(document, bom),
        "The created BOM has no accepted native History presentation state.",
    )
    if _new_document_objects(document, tuple(value["before_objects"])) != tuple(
        value["created_objects"]
    ):
        raise NativeAssemblyBomError(
            "The BOM document graph changed after native creation."
        )
    try:
        current = capture_assembly_bom_state(assembly)
        solver = capture_assembly_solver_state(assembly)
    except Exception as exc:
        raise NativeAssemblyBomError(
            "The created BOM state could not be read before commit."
        ) from exc
    if (
        current.components != before.components
        or current.source_records != before.source_records
        or current.bom_group is not group
        or current.boms != (*before.boms, bom)
        or len(current.bom_records) != len(before.bom_records) + 1
        or current.bom_records[:-1] != before.bom_records
        or not _same_solver_state(prepared.solver_before, solver)
    ):
        raise NativeAssemblyBomError(
            "BOM creation changed the Assembly source graph, prior BOMs, or placements."
        )
    record = current.bom_records[-1]
    table = record["table"]
    if (
        record["bom"]["object_name"] != str(bom.Name)
        or tuple(record["columns"]) != prepared.columns
        or table["column_count"] != len(prepared.columns)
        or tuple(table["headers"]) != prepared.columns
    ):
        raise NativeAssemblyBomError(
            "The native BOM spreadsheet did not retain its exact ordered columns."
        )
    return {
        "operation": "create_bom",
        "assembly": object_reference(assembly),
        "bom_group": object_reference(group),
        "bom": object_reference(bom),
        "label": str(bom.Label),
        "columns": list(prepared.columns),
        "settings": {
            "detail_subassemblies": prepared.spec.detail_subassemblies,
            "detail_parts": prepared.spec.detail_parts,
            "only_parts": prepared.spec.only_parts,
            "auto_generate": True,
        },
        "component_count": len(current.components),
        "source_node_count": len(current.source_records),
        "bom_count": len(current.boms),
        "row_count": int(table["row_count"]),
        "row_preview": list(table["row_preview"]),
        "rows_truncated": int(table["row_count"]) > len(table["row_preview"]),
        "preview_values_truncated": bool(table["preview_values_truncated"]),
        "table_sha256": str(table["table_sha256"]),
        "bom_state_sha256": current.state_sha256,
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
        "assembly_placements_unchanged": True,
    }
