# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact view and document behavior for Sketch virtual space."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records_sha256
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import (
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    require_prepared_active_sketch,
)
from VibeCADNativeSketchVirtualSpaceState import (
    FrozenSketchVirtualSpaceState,
    expected_virtual_space_constraint_records,
    read_shown_virtual_space,
    read_sketch_virtual_space_state,
    validate_virtual_space_constraints,
    write_shown_virtual_space,
)
from VibeCADNativeSketchVirtualSpaceTarget import (
    LABEL,
    SketchVirtualSpaceConstraintsTarget,
    SketchVirtualSpaceSpec,
    SketchVirtualSpaceViewTarget,
    prepare_sketch_virtual_space_target,
)
from VibeCADNativeTargets import object_identity, object_reference


@dataclass(frozen=True, slots=True)
class PreparedSketchVirtualSpaceView:
    target: PreparedActiveSketchTarget
    spec: SketchVirtualSpaceSpec
    state: FrozenSketchVirtualSpaceState
    view_target: SketchVirtualSpaceViewTarget
    previous_shown: bool


@dataclass(frozen=True, slots=True)
class PreparedSketchVirtualSpaceConstraints:
    target: PreparedActiveSketchTarget
    spec: SketchVirtualSpaceSpec
    state: FrozenSketchVirtualSpaceState
    constraint_target: SketchVirtualSpaceConstraintsTarget
    resolved_constraints: tuple[dict[str, Any], ...]


PreparedSketchVirtualSpace = (
    PreparedSketchVirtualSpaceView | PreparedSketchVirtualSpaceConstraints
)


def prepare_sketch_virtual_space(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchVirtualSpaceSpec:
    return prepare_sketch_virtual_space_target(document_uid, value)


def _freeze(
    sketch: Any,
    spec: SketchVirtualSpaceSpec,
) -> FrozenSketchVirtualSpaceState:
    state = read_sketch_virtual_space_state(sketch, spec)
    if len(state.external_geometry_records) != spec.expected_external_geometry_count:
        raise NativeSketchError(
            "The active Sketch external geometry count changed; read it and retry."
        )
    return state


def preflight_sketch_virtual_space(
    context: NativeRuntimeContext,
    spec: SketchVirtualSpaceSpec,
) -> PreparedSketchVirtualSpace:
    if not isinstance(spec, SketchVirtualSpaceSpec):
        raise TypeError("spec must be a SketchVirtualSpaceSpec")
    target = preflight_active_sketch(context, spec.target)
    state = _freeze(target.sketch, spec)
    requested = spec.virtual_space_target
    if isinstance(requested, SketchVirtualSpaceViewTarget):
        previous = read_shown_virtual_space()
        if previous is not requested.expected_shown_virtual_space:
            raise NativeSketchError(
                f"{LABEL} edit-view state changed; read it and retry."
            )
        return PreparedSketchVirtualSpaceView(
            target,
            spec,
            state,
            requested,
            previous,
        )
    if isinstance(requested, SketchVirtualSpaceConstraintsTarget):
        resolved = validate_virtual_space_constraints(
            target.sketch,
            spec,
            state,
            requested.constraints,
        )
        return PreparedSketchVirtualSpaceConstraints(
            target,
            spec,
            state,
            requested,
            resolved,
        )
    raise TypeError("Unsupported Sketch virtual-space target")


def _require_unchanged(
    document: Any,
    prepared: PreparedSketchVirtualSpace,
    *,
    stage: str,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    if _freeze(sketch, prepared.spec) != prepared.state:
        raise NativeSketchError(f"The active Sketch changed {stage}.")
    return sketch


def _view_result(
    prepared: PreparedSketchVirtualSpaceView,
    *,
    changed: bool,
) -> dict[str, Any]:
    state = prepared.state
    spec = prepared.spec
    return {
        "operation": "set_virtual_space",
        "target_kind": "view",
        "sketch": object_reference(prepared.target.sketch),
        "previous_shown_virtual_space": prepared.previous_shown,
        "shown_virtual_space": prepared.view_target.shown_virtual_space,
        "changed": changed,
        "geometry_count": spec.target.expected_geometry_count,
        "constraint_count": spec.target.expected_constraint_count,
        "external_geometry_count": spec.expected_external_geometry_count,
        "geometry_state_sha256": canonical_sketch_records_sha256(
            state.geometry_records
        ),
        "constraint_state_sha256": canonical_sketch_records_sha256(
            state.constraint_records
        ),
        "external_geometry_state_sha256": canonical_sketch_records_sha256(
            state.external_geometry_records
        ),
    }


def set_sketch_virtual_space_view(
    context: NativeRuntimeContext,
    prepared: PreparedSketchVirtualSpaceView,
) -> dict[str, Any]:
    if not isinstance(prepared, PreparedSketchVirtualSpaceView):
        raise TypeError("prepared must be a PreparedSketchVirtualSpaceView")
    context.guard()
    _require_unchanged(
        context.document,
        prepared,
        stage="during the virtual-space view operation",
    )
    desired = prepared.view_target.shown_virtual_space
    if prepared.previous_shown is desired:
        if read_shown_virtual_space() is not desired:
            raise NativeSketchError(f"{LABEL} edit-view state changed during verification.")
        return _view_result(prepared, changed=False)

    try:
        write_shown_virtual_space(desired)
        context.guard()
        _require_unchanged(
            context.document,
            prepared,
            stage="during the virtual-space view operation",
        )
        if read_shown_virtual_space() is not desired:
            raise NativeSketchError(f"{LABEL} edit-view state changed during verification.")
    except Exception:
        if read_shown_virtual_space() is desired:
            write_shown_virtual_space(prepared.previous_shown)
        raise
    return _view_result(prepared, changed=True)


def create_sketch_virtual_space_constraints(
    document: Any,
    prepared: PreparedSketchVirtualSpaceConstraints,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchVirtualSpaceConstraints):
        raise TypeError(
            "prepared must be a PreparedSketchVirtualSpaceConstraints"
        )
    sketch = _require_unchanged(
        document,
        prepared,
        stage="after virtual-space preflight",
    )
    targets = prepared.constraint_target.constraints
    try:
        for desired in (False, True):
            indices = [
                target.constraint_index
                for target in targets
                if target.virtual_space is desired
            ]
            if indices:
                sketch.setVirtualSpace(indices, desired)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact virtual-space constraint states."
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _changed_constraints(
    prepared: PreparedSketchVirtualSpaceConstraints,
) -> list[dict[str, Any]]:
    return [
        {
            "constraint_index": target.constraint_index,
            "constraint_type": str(record["type"]),
            "previous_virtual_space": target.expected_virtual_space,
            "virtual_space": target.virtual_space,
        }
        for target, record in zip(
            prepared.constraint_target.constraints,
            prepared.resolved_constraints,
            strict=True,
        )
    ]


def verify_sketch_virtual_space_constraints(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchVirtualSpaceConstraints):
        raise TypeError("draft must contain exact prepared virtual-space state")
    sketch = require_prepared_active_sketch(document, prepared.target)
    current = _freeze(sketch, prepared.spec)
    if current.geometry_records != prepared.state.geometry_records:
        raise NativeSketchError(f"{LABEL} changed Sketch geometry.")
    if current.external_geometry_records != prepared.state.external_geometry_records:
        raise NativeSketchError(f"{LABEL} changed external geometry.")
    expected_constraints = expected_virtual_space_constraint_records(
        prepared.state,
        prepared.constraint_target.constraints,
    )
    if current.constraint_records != expected_constraints:
        raise NativeSketchError(
            f"{LABEL} changed constraints beyond the exact requested states."
        )
    if current.expression_records != prepared.state.expression_records:
        raise NativeSketchError(f"{LABEL} changed Sketch expressions.")
    if current.solver_issues != prepared.state.solver_issues:
        raise NativeSketchError(f"{LABEL} changed Sketch solver diagnostics.")
    return sketch_geometry_result(
        sketch,
        {
            "operation": "set_virtual_space",
            "target_kind": "constraints",
            "changed_constraints": _changed_constraints(prepared),
        },
    )
