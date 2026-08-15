# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact atomic creation of one native Assembly exploded-view graph."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from VibeCADNativeAssemblyJointConnectors import placement_is_same
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeAssemblyViewState import (
    MAX_VIEW_REFERENCES,
    AssemblyViewState,
    AssemblyViewTarget,
    capture_assembly_view_state,
)
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_VIEW_FAILED = "NATIVE_ASSEMBLY_VIEW_FAILED"


class NativeAssemblyViewError(NativeMutationError):
    """The requested exploded view could not be created exactly."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_VIEW_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblyViewMoveSpec:
    kind: str
    target_refs: tuple[NativeObjectRef, ...]
    movement_transform: Any | None = None
    radial_distance_mm: float | None = None


@dataclass(frozen=True, slots=True)
class AssemblyViewCreateSpec:
    assembly_ref: NativeObjectRef
    label: str
    parts_as_single_solid: bool
    moves: tuple[AssemblyViewMoveSpec, ...]
    expected_view_state_sha256: str
    expected_component_count: int
    expected_target_count: int
    expected_view_count: int


@dataclass(frozen=True, slots=True)
class PreparedAssemblyViewMove:
    spec: AssemblyViewMoveSpec
    targets: tuple[AssemblyViewTarget, ...]
    root: Any


@dataclass(frozen=True, slots=True)
class PreparedAssemblyView:
    spec: AssemblyViewCreateSpec
    state: AssemblyViewState
    moves: tuple[PreparedAssemblyViewMove, ...]
    active_before: Any
    selection_before: dict[str, Any]
    presentation_before: tuple[bool, bool]


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _exact_digest(value: Any) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise NativeAssemblyViewError(
            "expected_view_state_sha256 must be one lowercase SHA-256 digest."
        )
    return digest


def _exact_count(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise NativeAssemblyViewError(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def _presentation_state(assembly: Any) -> tuple[bool, bool]:
    view = getattr(assembly, "ViewObject", None)
    try:
        return bool(view.EnableMovement), bool(view.DraggerVisibility)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeAssemblyViewError(
            "The active Assembly movement presentation is unavailable."
        ) from exc


def _exact_active_assembly(
    document: Any,
    spec: AssemblyViewCreateSpec,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    try:
        assembly = resolve_object(
            document,
            spec.assembly_ref,
            expected_types=("Assembly::AssemblyObject",),
        )
    except Exception as exc:
        raise NativeAssemblyViewError(str(exc)) from exc
    if not same_assembly(assembly, active_reader(document)) or not _timeline_active(
        assembly
    ):
        raise NativeAssemblyViewError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    return assembly


def _validate_move_shape(move: AssemblyViewMoveSpec) -> None:
    if not isinstance(move, AssemblyViewMoveSpec):
        raise TypeError("Every move must be an AssemblyViewMoveSpec")
    if move.kind not in {"normal", "radial"}:
        raise NativeAssemblyViewError(
            "Every exploded-view move must be normal or radial."
        )
    if not move.target_refs:
        raise NativeAssemblyViewError(
            "Every exploded-view move must target at least one movable object."
        )
    if len({reference.object_name for reference in move.target_refs}) != len(
        move.target_refs
    ):
        raise NativeAssemblyViewError(
            "An exploded-view move cannot target the same object twice."
        )
    if move.kind == "normal":
        if move.movement_transform is None or move.radial_distance_mm is not None:
            raise NativeAssemblyViewError(
                "A normal exploded-view move requires only movement_transform."
            )
        try:
            identity = bool(move.movement_transform.isIdentity())
        except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
            raise NativeAssemblyViewError(
                "A normal exploded-view move has an invalid transform."
            ) from exc
        if identity:
            raise NativeAssemblyViewError(
                "A normal exploded-view move must translate or rotate its targets."
            )
    else:
        distance = move.radial_distance_mm
        if (
            move.movement_transform is not None
            or isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or not math.isfinite(float(distance))
            or not 0.0 < float(distance) <= 1_000_000.0
        ):
            raise NativeAssemblyViewError(
                "A radial exploded-view move requires a distance greater than zero and at most 1000000 mm."
            )


def _resolve_moves(
    document: Any,
    state: AssemblyViewState,
    spec: AssemblyViewCreateSpec,
) -> tuple[PreparedAssemblyViewMove, ...]:
    targets = state.targets(spec.parts_as_single_solid)
    by_name = {str(target.obj.Name): target for target in targets}
    result = []
    total_references = 0
    for move in spec.moves:
        _validate_move_shape(move)
        total_references += len(move.target_refs)
        if total_references > MAX_VIEW_REFERENCES:
            raise NativeAssemblyViewError(
                f"Exploded-view moves may contain at most {MAX_VIEW_REFERENCES} target references."
            )
        resolved = []
        for reference in move.target_refs:
            try:
                target = resolve_object(document, reference)
            except Exception as exc:
                raise NativeAssemblyViewError(str(exc)) from exc
            record = by_name.get(str(getattr(target, "Name", "") or ""))
            if record is None or record.obj is not target:
                mode = (
                    "parts-as-single-solid"
                    if spec.parts_as_single_solid
                    else "individual-object"
                )
                raise NativeAssemblyViewError(
                    f"The exact target is not available in the current {mode} Assembly view graph."
                )
            resolved.append(record)
        roots = {id(target.root): target.root for target in resolved}
        if len(roots) != 1:
            raise NativeAssemblyViewError(
                "All targets in one exploded-view move must share one exact selection root."
            )
        result.append(
            PreparedAssemblyViewMove(
                spec=move,
                targets=tuple(resolved),
                root=next(iter(roots.values())),
            )
        )
    return tuple(result)


def preflight_create_assembly_view(
    document: Any,
    spec: AssemblyViewCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblyView:
    """Freeze one exact active Assembly and its complete requested move graph."""

    if not isinstance(spec, AssemblyViewCreateSpec):
        raise TypeError("spec must be an AssemblyViewCreateSpec")
    if not isinstance(spec.assembly_ref, NativeObjectRef):
        raise TypeError("spec.assembly_ref must be a NativeObjectRef")
    if not isinstance(spec.label, str) or not 1 <= len(spec.label.strip()) <= 160:
        raise NativeAssemblyViewError(
            "An exploded-view label must contain 1 to 160 characters."
        )
    if type(spec.parts_as_single_solid) is not bool:
        raise NativeAssemblyViewError("parts_as_single_solid must be true or false.")
    if not isinstance(spec.moves, tuple) or not 1 <= len(spec.moves) <= 256:
        raise NativeAssemblyViewError(
            "An exploded view requires 1 through 256 ordered moves."
        )
    expected_digest = _exact_digest(spec.expected_view_state_sha256)
    expected_components = _exact_count(
        spec.expected_component_count,
        "expected_component_count",
        100_000,
    )
    expected_targets = _exact_count(
        spec.expected_target_count,
        "expected_target_count",
        4_096,
    )
    expected_views = _exact_count(
        spec.expected_view_count,
        "expected_view_count",
        1_024,
    )
    assembly = _exact_active_assembly(document, spec, active_reader)
    try:
        state = capture_assembly_view_state(assembly)
    except Exception as exc:
        raise NativeAssemblyViewError(str(exc)) from exc
    targets = state.targets(spec.parts_as_single_solid)
    if state.component_count < 2:
        raise NativeAssemblyViewError(
            "The human Exploded View action requires at least two Assembly components."
        )
    if (
        state.component_count != expected_components
        or len(targets) != expected_targets
        or len(state.views) != expected_views
    ):
        raise NativeAssemblyViewError(
            "The active Assembly view counts changed; read current Assemble state and retry."
        )
    if state.state_sha256 != expected_digest:
        raise NativeAssemblyViewError(
            "The active Assembly exploded-view state changed; read current Assemble state and retry."
        )
    moves = _resolve_moves(document, state, spec)
    if any(move.spec.kind == "radial" for move in moves) and (
        not math.isfinite(state.assembly_diagonal_mm)
        or state.assembly_diagonal_mm <= 1.0e-12
    ):
        raise NativeAssemblyViewError(
            "A radial exploded view requires finite non-zero Assembly bounds."
        )
    selection_before = selection_reader(document)
    presentation_before = _presentation_state(assembly)
    if not same_assembly(assembly, active_reader(document)):
        raise NativeAssemblyViewError(
            "The human-active Assembly changed during exploded-view preflight."
        )
    return PreparedAssemblyView(
        spec=spec,
        state=state,
        moves=moves,
        active_before=assembly,
        selection_before=selection_before,
        presentation_before=presentation_before,
    )


def _new_document_objects(document: Any, before: tuple[Any, ...]) -> tuple[Any, ...]:
    identities = {id(obj) for obj in before}
    return tuple(
        obj
        for obj in list(getattr(document, "Objects", ()) or ())
        if id(obj) not in identities
    )


def _create_view_feature(document: Any, assembly: Any) -> Any:
    import CommandCreateView

    view = CommandCreateView.createExplodedViewFeature(document, assembly)
    CommandCreateView.ViewProviderExplodedView(view.ViewObject)
    return view


def _create_step_feature(document: Any, assembly: Any, kind: str) -> Any:
    import CommandCreateView

    step = CommandCreateView.createExplodedViewStepFeature(
        document,
        assembly,
        1 if kind == "radial" else 0,
    )
    CommandCreateView.ViewProviderExplodedViewStep(step.ViewObject)
    return step


def _radial_placement(distance_mm: float) -> Any:
    import FreeCAD as App

    return App.Placement(
        App.Vector(float(distance_mm), 0.0, 0.0),
        App.Rotation(),
    )


def _finalize_view(document: Any, view: Any, steps: tuple[Any, ...]) -> None:
    document.finalizeProvisionalTimelineOperationBlock(
        view,
        [*steps, view],
    )


def _copy_placement(placement: Any) -> Any:
    try:
        import FreeCAD as App

        return App.Placement(placement)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        return placement


def _prove_move_effects(
    prepared: PreparedAssemblyView,
    view: Any,
    steps: tuple[Any, ...],
) -> tuple[tuple[int, ...], int]:
    """Exercise the durable graph once, then restore the exact assembled state."""

    import UtilsAssembly

    assembly = prepared.state.assembly
    baseline = UtilsAssembly._saveExactAssemblyPartPlacements(assembly)
    effect_counts = []
    line_count = 0
    try:
        for move, step in zip(prepared.moves, steps, strict=True):
            before = tuple(
                _copy_placement(target.obj.Placement) for target in move.targets
            )
            positions = step.Proxy.applyStep(
                step,
                prepared.state.assembly_center,
                prepared.state.assembly_diagonal_mm,
            )
            changed = sum(
                not placement_is_same(prior, target.obj.Placement)
                for target, prior in zip(move.targets, before, strict=True)
            )
            if changed != len(move.targets) or len(positions) != len(move.targets):
                raise NativeAssemblyViewError(
                    "An exploded-view move did not move every exact target or produce its native explosion line."
                )
            effect_counts.append(changed)
            line_count += len(positions)
    finally:
        try:
            UtilsAssembly._restoreExactAssemblyPartPlacements(assembly, baseline)
        finally:
            view.Proxy._last_applied_placements = []
            for step in steps:
                step.Visibility = False
    return tuple(effect_counts), line_count


def create_assembly_view(
    document: Any,
    spec: AssemblyViewCreateSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
    view_factory: Callable[[Any, Any], Any] = _create_view_feature,
    step_factory: Callable[[Any, Any, str], Any] = _create_step_feature,
    finalizer: Callable[[Any, Any, tuple[Any, ...]], None] = _finalize_view,
) -> NativeMutationDraft:
    """Create the complete accepted human-equivalent view graph in one transaction."""

    prepared = preflight_create_assembly_view(
        document,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    before_objects = tuple(document.Objects)
    assembly = prepared.state.assembly
    view = view_factory(document, assembly)
    if (
        view is None
        or getattr(view, "Document", None) is not document
        or str(getattr(view, "TypeId", "") or "") != "App::FeaturePython"
        or type(getattr(view, "Proxy", None)).__name__ != "ExplodedView"
    ):
        raise NativeAssemblyViewError(
            "The native exploded-view factory returned the wrong operation object."
        )
    view.Label = spec.label.strip()
    steps = []
    for move in prepared.moves:
        step = step_factory(document, assembly, move.spec.kind)
        if (
            step is None
            or getattr(step, "Document", None) is not document
            or str(getattr(step, "TypeId", "") or "") != "App::FeaturePython"
            or type(getattr(step, "Proxy", None)).__name__ != "ExplodedViewStep"
        ):
            raise NativeAssemblyViewError(
                "The native exploded-view factory returned the wrong move object."
            )
        step.MoveType = "Radial" if move.spec.kind == "radial" else "Normal"
        step.MovementTransform = (
            _radial_placement(float(move.spec.radial_distance_mm))
            if move.spec.kind == "radial"
            else _copy_placement(move.spec.movement_transform)
        )
        step.References = [
            move.root,
            [target.selection_path for target in move.targets],
        ]
        steps.append(step)
    step_tuple = tuple(steps)
    view.Group = list(step_tuple)
    try:
        import UtilsAssembly

        for step in step_tuple:
            UtilsAssembly.markTimelineResource(step, view)
            # Match task acceptance order: classify ownership first, then
            # capture the hidden resource state before History publication.
            step.Visibility = False
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeAssemblyViewError(
            "The exploded-view moves could not be assigned to their timeline operation."
        ) from exc
    finalizer(document, view, step_tuple)
    effects, line_count = _prove_move_effects(prepared, view, step_tuple)
    created_objects = _new_document_objects(document, before_objects)
    semantic_objects: tuple[Any, ...] = (*step_tuple, view)
    view_group = next(
        (
            child
            for child in list(getattr(assembly, "Group", ()) or ())
            if str(getattr(child, "TypeId", "") or "") == "Assembly::ViewGroup"
            and view in list(getattr(child, "Group", ()) or ())
        ),
        None,
    )
    if view_group is None:
        raise NativeAssemblyViewError(
            "The native exploded view has no exact Assembly view group."
        )
    if prepared.state.view_group is None:
        semantic_objects = (view_group, *semantic_objects)
    semantic = set(semantic_objects)
    allowed_extra_types = {"App::DocumentTimeline"}
    if not semantic.issubset(set(created_objects)) or any(
        obj not in semantic
        and str(getattr(obj, "TypeId", "") or "") not in allowed_extra_types
        for obj in created_objects
    ):
        raise NativeAssemblyViewError(
            "Exploded-view creation changed objects outside its exact native graph."
        )
    changed = [assembly]
    if prepared.state.view_group is not None:
        changed.append(view_group)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "before_objects": before_objects,
            "created_objects": created_objects,
            "view_group": view_group,
            "view": view,
            "steps": step_tuple,
            "effect_counts": effects,
            "line_count": line_count,
        },
        recompute_targets=(*step_tuple, view, view_group, assembly),
        created=tuple(object_identity(obj) for obj in semantic_objects),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _same_targets(
    expected: tuple[AssemblyViewTarget, ...],
    current: tuple[AssemblyViewTarget, ...],
) -> bool:
    return len(expected) == len(current) and all(
        before.obj is after.obj
        and before.root is after.root
        and before.selection_path == after.selection_path
        and placement_is_same(before.placement, after.placement)
        for before, after in zip(expected, current, strict=True)
    )


def _timeline_block(document: Any, steps: tuple[Any, ...], view: Any) -> bool:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "") or "") != (
        "App::DocumentTimeline"
    ):
        return False
    operations = list(getattr(timeline, "Operations", ()) or ())
    accepted_visibility = list(getattr(timeline, "VisibilityAtEnd", ()) or ())
    if len(accepted_visibility) != len(operations):
        return False
    block = [*steps, view]
    try:
        start = operations.index(steps[0])
    except (IndexError, ValueError):
        return False
    return operations[start : start + len(block)] == block and all(
        not bool(accepted_visibility[start + offset]) for offset in range(len(steps))
    )


def _verify_step(
    prepared: PreparedAssemblyViewMove,
    step: Any,
    view: Any,
) -> bool:
    try:
        references = step.References
        root = references[0]
        paths = list(references[1])
        expected_transform = (
            _radial_placement(float(prepared.spec.radial_distance_mm))
            if prepared.spec.kind == "radial"
            else prepared.spec.movement_transform
        )
        return (
            str(step.TypeId) == "App::FeaturePython"
            and type(step.Proxy).__name__ == "ExplodedViewStep"
            and _timeline_active(step)
            and str(step.VibeCADTimelineRole) == "resource"
            and step.VibeCADTimelineOwner is view
            and str(step.MoveType)
            == ("Radial" if prepared.spec.kind == "radial" else "Normal")
            and root is prepared.root
            and paths == [target.selection_path for target in prepared.targets]
            and placement_is_same(step.MovementTransform, expected_transform)
            and not bool(step.Visibility)
        )
    except (AttributeError, IndexError, ReferenceError, RuntimeError, TypeError):
        return False


def verify_created_assembly_view(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove exact graph, timeline ownership, baseline restoration, and UI state."""

    value = draft.value
    prepared: PreparedAssemblyView = value["prepared"]
    before = prepared.state
    assembly = before.assembly
    view_group = value["view_group"]
    view = value["view"]
    steps = tuple(value["steps"])
    if (
        document.getObject(str(assembly.Name)) is not assembly
        or document.getObject(str(view_group.Name)) is not view_group
        or document.getObject(str(view.Name)) is not view
        or not same_assembly(prepared.active_before, active_reader(document))
        or selection_reader(document) != prepared.selection_before
        or _presentation_state(assembly) != prepared.presentation_before
        or str(getattr(view_group, "TypeId", "") or "") != "Assembly::ViewGroup"
        or view_group not in list(getattr(assembly, "Group", ()) or ())
        or list(getattr(view_group, "Group", ()) or ())[-1:] != [view]
        or str(getattr(view, "TypeId", "") or "") != "App::FeaturePython"
        or type(getattr(view, "Proxy", None)).__name__ != "ExplodedView"
        or str(getattr(view, "Label", "") or "") != prepared.spec.label.strip()
        or str(getattr(view, "VibeCADTimelineRole", "") or "") != "operation"
        or str(getattr(view, "VibeCADTimelineEditCommand", "") or "")
        != "Assembly_EditHistoryOperation"
        or not _timeline_active(view)
        or list(getattr(view, "Group", ()) or ()) != list(steps)
        or len(steps) != len(prepared.moves)
        or not all(
            _verify_step(move, step, view)
            for move, step in zip(prepared.moves, steps, strict=True)
        )
        or not _timeline_block(document, steps, view)
        or list(getattr(view.Proxy, "_last_applied_placements", ()) or ())
    ):
        raise NativeAssemblyViewError(
            "The exploded view failed its exact graph, History, or human-state postcondition."
        )
    created = tuple(value["created_objects"])
    if _new_document_objects(document, tuple(value["before_objects"])) != created:
        raise NativeAssemblyViewError(
            "The exploded-view document graph changed after native creation."
        )
    try:
        current = capture_assembly_view_state(assembly)
    except Exception as exc:
        raise NativeAssemblyViewError(
            "The exploded-view state could not be read before commit."
        ) from exc
    if (
        current.component_count != before.component_count
        or current.assembly_center != before.assembly_center
        or not math.isclose(
            current.assembly_diagonal_mm,
            before.assembly_diagonal_mm,
            rel_tol=1.0e-12,
            abs_tol=1.0e-9,
        )
        or not _same_targets(before.individual_targets, current.individual_targets)
        or not _same_targets(before.solid_targets, current.solid_targets)
        or current.view_group is not view_group
        or current.views != (*before.views, view)
        or len(current.view_records) != len(before.view_records) + 1
        or current.view_records[:-1] != before.view_records
    ):
        raise NativeAssemblyViewError(
            "Exploded-view creation changed the Assembly targets, bounds, or prior view graph."
        )
    final_record = current.view_records[-1]
    if (
        final_record["view"]["object_name"] != str(view.Name)
        or len(final_record["moves"]) != len(steps)
        or tuple(value["effect_counts"])
        != tuple(len(move.targets) for move in prepared.moves)
    ):
        raise NativeAssemblyViewError(
            "The created exploded-view graph changed before commit."
        )
    normal_count = sum(move.spec.kind == "normal" for move in prepared.moves)
    radial_count = len(prepared.moves) - normal_count
    return {
        "operation": "create_view",
        "assembly": object_reference(assembly),
        "view_group": object_reference(view_group),
        "view": object_reference(view),
        "label": str(view.Label),
        "parts_as_single_solid": prepared.spec.parts_as_single_solid,
        "component_count": current.component_count,
        "target_count": len(current.targets(prepared.spec.parts_as_single_solid)),
        "view_count": len(current.views),
        "move_count": len(steps),
        "normal_move_count": normal_count,
        "radial_move_count": radial_count,
        "target_reference_count": sum(len(move.targets) for move in prepared.moves),
        "explosion_line_count": int(value["line_count"]),
        "view_state_sha256": current.state_sha256,
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
        "assembly_placements_restored": True,
    }
