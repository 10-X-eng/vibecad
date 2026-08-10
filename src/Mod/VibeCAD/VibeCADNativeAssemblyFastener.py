# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standard-fastener insertion into the human-active Assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADFastenerAssembly import (
    AssemblyFastenerGraph,
    assembly_fastener_summary,
    create_assembly_fastener_graph,
    validate_assembly_fastener_graph,
)
from VibeCADNativeAssemblyDiagnosisState import (
    AssemblyDiagnosisState,
    capture_assembly_diagnosis_state,
)
from VibeCADNativeAssemblyJointConnectors import placement_is_same
from VibeCADNativeAssemblySolveState import AssemblySolverState
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFastener import (
    PreparedModelFastener,
    prepare_model_fastener,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_FASTENER_FAILED = "NATIVE_ASSEMBLY_FASTENER_FAILED"


class NativeAssemblyFastenerError(NativeMutationError):
    """The requested Assembly standard fastener could not be created exactly."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_FASTENER_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblyFastenerInsertSpec:
    assembly_ref: NativeObjectRef
    label: str
    definition: Any
    expected_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int


@dataclass(frozen=True, slots=True)
class PreparedAssemblyFastenerInsert:
    spec: AssemblyFastenerInsertSpec
    definition: PreparedModelFastener
    state: AssemblyDiagnosisState
    active_before: Any
    selection_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    timeline_operations_before: tuple[Any, ...]
    timeline_visibility_before: tuple[bool, ...]


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
        raise NativeAssemblyFastenerError(
            "expected_state_sha256 must be one lowercase SHA-256 digest."
        )
    return result


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyFastenerError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _label(value: Any) -> str:
    if not isinstance(value, str):
        raise NativeAssemblyFastenerError("A standard-fastener label must be text.")
    result = value.strip()
    if not 1 <= len(result) <= 160 or any(
        ord(character) < 32 or ord(character) == 127 for character in result
    ):
        raise NativeAssemblyFastenerError(
            "A standard-fastener label must contain 1 through 160 printable characters."
        )
    return result


def _exact_active_assembly(
    document: Any,
    reference: NativeObjectRef,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    try:
        assembly = resolve_object(
            document,
            reference,
            expected_types=("Assembly::AssemblyObject",),
        )
    except Exception as exc:
        raise NativeAssemblyFastenerError(str(exc)) from exc
    if not same_assembly(assembly, active_reader(document)) or not _timeline_active(
        assembly
    ):
        raise NativeAssemblyFastenerError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def _timeline_state(document: Any) -> tuple[tuple[Any, ...], tuple[bool, ...]]:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "") or "") != (
        "App::DocumentTimeline"
    ):
        raise NativeAssemblyFastenerError(
            "The active document has no exact native History."
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        raise NativeAssemblyFastenerError(
            "The active document History presentation is malformed."
        )
    return operations, visibility


def _state_matches_spec(
    state: AssemblyDiagnosisState,
    spec: AssemblyFastenerInsertSpec,
) -> bool:
    return bool(
        state.state_sha256 == spec.expected_state_sha256
        and len(state.components) == spec.expected_component_count
        and len(state.grounded_joints) == spec.expected_grounded_count
        and len(state.regular_joints) == spec.expected_joint_count
    )


def preflight_insert_assembly_fastener(
    document: Any,
    spec: AssemblyFastenerInsertSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyFastenerInsert:
    """Freeze one exact Assembly, catalog definition, and UI state."""

    if not isinstance(spec, AssemblyFastenerInsertSpec):
        raise TypeError("spec must be an AssemblyFastenerInsertSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    clean_spec = AssemblyFastenerInsertSpec(
        assembly_ref=spec.assembly_ref,
        label=_label(spec.label),
        definition=spec.definition,
        expected_state_sha256=_exact_digest(spec.expected_state_sha256),
        expected_component_count=_exact_count(
            spec.expected_component_count,
            "expected_component_count",
            100_000,
        ),
        expected_grounded_count=_exact_count(
            spec.expected_grounded_count,
            "expected_grounded_count",
            256,
        ),
        expected_joint_count=_exact_count(
            spec.expected_joint_count,
            "expected_joint_count",
            256,
        ),
    )
    try:
        definition = prepare_model_fastener(clean_spec.definition)
    except (NativeModelError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyFastenerError(str(exc)) from exc
    assembly = _exact_active_assembly(document, clean_spec.assembly_ref, active_reader)
    try:
        state = capture_assembly_diagnosis_state(assembly)
    except Exception as exc:
        raise NativeAssemblyFastenerError(str(exc)) from exc
    if not _state_matches_spec(state, clean_spec):
        raise NativeAssemblyFastenerError(
            "The active Assembly changed; read current Assemble state and retry."
        )
    operations, visibility = _timeline_state(document)
    selection = selection_reader(document)
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblyFastenerError(
            "The human-active Assembly changed during fastener preflight."
        )
    return PreparedAssemblyFastenerInsert(
        spec=clean_spec,
        definition=definition,
        state=state,
        active_before=assembly,
        selection_before=selection,
        objects_before=tuple(document.Objects),
        timeline_operations_before=operations,
        timeline_visibility_before=visibility,
    )


def _prepared_state_is_current(
    document: Any,
    prepared: PreparedAssemblyFastenerInsert,
    *,
    active_reader: Callable[[Any], Any | None],
    selection_reader: Callable[[Any], dict[str, Any]],
) -> bool:
    assembly = _exact_active_assembly(
        document,
        prepared.spec.assembly_ref,
        active_reader,
    )
    try:
        state = capture_assembly_diagnosis_state(assembly)
    except Exception as exc:
        raise NativeAssemblyFastenerError(str(exc)) from exc
    operations, visibility = _timeline_state(document)
    return bool(
        assembly is prepared.state.assembly
        and state.state_sha256 == prepared.state.state_sha256
        and state.components == prepared.state.components
        and state.grounded_joints == prepared.state.grounded_joints
        and state.regular_joints == prepared.state.regular_joints
        and selection_reader(document) == prepared.selection_before
        and tuple(document.Objects) == prepared.objects_before
        and operations == prepared.timeline_operations_before
        and visibility == prepared.timeline_visibility_before
    )


def insert_assembly_fastener(
    document: Any,
    prepared: PreparedAssemblyFastenerInsert,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> NativeMutationDraft:
    """Create one human-equivalent hidden definition and visible occurrence."""

    if not isinstance(prepared, PreparedAssemblyFastenerInsert):
        raise TypeError("prepared must be a PreparedAssemblyFastenerInsert")
    if not _prepared_state_is_current(
        document,
        prepared,
        active_reader=active_reader,
        selection_reader=selection_reader,
    ):
        raise NativeAssemblyFastenerError(
            "The Assembly, document, or human selection changed before insertion."
        )
    definition = prepared.definition
    try:
        graph = create_assembly_fastener_graph(
            document,
            assembly=prepared.state.assembly,
            label=prepared.spec.label,
            targeted_recompute=True,
            **dict(definition.constructor),
        )
    except Exception as exc:
        raise NativeAssemblyFastenerError(str(exc)) from exc
    if str(graph.identity.get("canonical_key") or "") != str(
        definition.identity.get("canonical_key") or ""
    ):
        raise NativeAssemblyFastenerError(
            "The standard-fastener catalog changed during Assembly insertion."
        )
    created = tuple(
        obj for obj in tuple(document.Objects) if obj not in prepared.objects_before
    )
    if created != (graph.occurrence, graph.source) and set(created) != {
        graph.occurrence,
        graph.source,
    }:
        raise NativeAssemblyFastenerError(
            "Assembly fastener insertion changed objects outside its exact graph."
        )
    return NativeMutationDraft(
        value={"prepared": prepared, "graph": graph, "created": created},
        recompute_targets=(graph.source, graph.occurrence, graph.assembly),
        created=(object_identity(graph.source), object_identity(graph.occurrence)),
        changed=(object_identity(graph.assembly),),
    )


def _existing_solver_state_unchanged(
    before: AssemblySolverState,
    after: AssemblySolverState,
    occurrence: Any,
) -> bool:
    current = {record.obj: record for record in after.records}
    if occurrence not in current or len(after.records) != len(before.records) + 1:
        return False
    for expected in before.records:
        actual = current.get(expected.obj)
        if (
            actual is None
            or actual.obj is not expected.obj
            or not placement_is_same(actual.placement, expected.placement)
            or actual.placement_locks != expected.placement_locks
        ):
            return False
    return True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeAssemblyFastenerError(message)


def verify_inserted_assembly_fastener(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove exact graph publication and preservation before transaction commit."""

    value = draft.value
    prepared: PreparedAssemblyFastenerInsert = value["prepared"]
    graph: AssemblyFastenerGraph = value["graph"]
    identity = validate_assembly_fastener_graph(
        document,
        graph,
        label=prepared.spec.label,
        canonical_key=str(prepared.definition.identity["canonical_key"]),
    )
    _require(
        same_assembly(prepared.active_before, active_reader(document)),
        "The human-active Assembly changed during fastener insertion.",
    )
    _require(
        selection_reader(document) == prepared.selection_before,
        "Human selection changed during fastener insertion.",
    )
    _require(
        tuple(obj for obj in document.Objects if obj not in prepared.objects_before)
        == tuple(value["created"]),
        "The Assembly fastener document graph changed after creation.",
    )
    operations, visibility = _timeline_state(document)
    _require(
        operations
        == (*prepared.timeline_operations_before, graph.source, graph.occurrence)
        and visibility[: len(prepared.timeline_visibility_before)]
        == prepared.timeline_visibility_before,
        "Assembly fastener insertion changed prior History or block order.",
    )
    placement = getattr(graph.occurrence, "Placement", None)
    _require(
        placement is not None
        and callable(getattr(placement, "isIdentity", None))
        and bool(placement.isIdentity()),
        "The inserted Assembly fastener did not retain its initial placement.",
    )
    try:
        current = capture_assembly_diagnosis_state(graph.assembly)
    except Exception as exc:
        raise NativeAssemblyFastenerError(
            "The inserted Assembly fastener state could not be read before commit."
        ) from exc
    before = prepared.state
    _require(
        current.components == (*before.components, graph.occurrence)
        and current.grounded_joints == before.grounded_joints
        and current.regular_joints == before.regular_joints
        and _existing_solver_state_unchanged(
            before.solver_state,
            current.solver_state,
            graph.occurrence,
        ),
        "Assembly fastener insertion changed prior components, joints, or placements.",
    )
    summary = assembly_fastener_summary(graph.assembly, graph.occurrence)
    _require(
        summary is not None
        and summary["canonical_key"] == str(identity["canonical_key"]),
        "The inserted Assembly fastener has no exact provider state.",
    )
    return {
        "operation": "insert_standard_fastener",
        "assembly": object_reference(graph.assembly),
        "occurrence": object_reference(graph.occurrence),
        "definition_source": object_reference(graph.source),
        "label": str(graph.occurrence.Label),
        "fastener": {
            name: summary[name]
            for name in (
                "canonical_key",
                "part_number",
                "standard",
                "nominal_thread",
                "length_mm",
                "model_thread",
                "left_handed",
                "options",
            )
        },
        "component_count": len(current.components),
        "grounded_count": len(current.grounded_joints),
        "joint_count": len(current.regular_joints),
        "assembly_state_sha256": current.state_sha256,
        "initial_placement_identity": True,
    }
