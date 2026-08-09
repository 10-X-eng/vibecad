# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Constraint Group creation in the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGroupState import verify_sketch_group_state
from VibeCADNativeSketchGroupTarget import (
    LABEL,
    ResolvedSketchGroup,
    SketchGroupSpec,
    prepare_sketch_group_target,
    resolve_sketch_group,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeSketchState import serialize_sketch_geometry
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchGroup:
    target: PreparedSketchConstraintTarget
    spec: SketchGroupSpec
    resolved: ResolvedSketchGroup


def prepare_sketch_group(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchGroupSpec:
    return prepare_sketch_group_target(document_uid, value)


def preflight_sketch_group(
    context: NativeRuntimeContext,
    spec: SketchGroupSpec,
) -> PreparedSketchGroup:
    if not isinstance(spec, SketchGroupSpec):
        raise TypeError("spec must be a SketchGroupSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    if any(sketch_solver_issues(sketch, LABEL)):
        raise NativeSketchError(
            f"{LABEL} requires a Sketch without current solver issues."
        )
    resolved = resolve_sketch_group(sketch, target, spec)
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or any(sketch_solver_issues(sketch, LABEL))
    ):
        raise NativeSketchError(f"{LABEL} preflight changed the active Sketch.")
    return PreparedSketchGroup(target, spec, resolved)


def _geometry_tag(sketch: Any, index: int) -> str:
    try:
        tag = str(serialize_sketch_geometry(sketch, index).get("tag", "") or "")
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} handle identity is unavailable.") from exc
    if not tag:
        raise NativeSketchError(f"{LABEL} handle has no persistent geometry tag.")
    return tag


def create_sketch_group(
    document: Any,
    prepared: PreparedSketchGroup,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchGroup):
        raise TypeError("prepared must be a PreparedSketchGroup")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Group preflight",
    )
    spec = prepared.spec.target.target
    resolved = prepared.resolved

    import FreeCAD as App
    import Part
    import Sketcher

    try:
        handle_index = int(
            sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(*resolved.handle_start_mm, 0.0),
                    App.Vector(*resolved.handle_end_mm, 0.0),
                ),
                True,
            )
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the {LABEL} handle.") from exc
    if handle_index != spec.expected_geometry_count:
        raise NativeSketchError(f"Sketcher returned an unexpected {LABEL} handle index.")
    handle_tag = _geometry_tag(sketch, handle_index)
    if handle_tag in resolved.member_tags:
        raise NativeSketchError(f"Sketcher reused a {LABEL} member identity.")

    elements = [handle_index, 0]
    for element in resolved.references:
        elements.extend((element.geometry_index, 0))
    try:
        constraint_index = int(
            sketch.addConstraint(Sketcher.Constraint("Group", elements))
        )
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the exact {LABEL} definition.") from exc
    if constraint_index != spec.expected_constraint_count:
        raise NativeSketchError(
            f"Sketcher returned an unexpected {LABEL} constraint index."
        )

    for parent_index in resolved.cleanup_parent_indices:
        try:
            sketch.deleteUnusedInternalGeometry(parent_index)
        except Exception as exc:
            raise NativeSketchError(
                f"{LABEL} could not clean unused internal geometry for member "
                f"{parent_index}."
            ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "handle_tag": handle_tag},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_group(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    handle_tag = draft.value.get("handle_tag") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchGroup) or not isinstance(handle_tag, str):
        raise TypeError("draft must contain exact prepared Group state")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    return verify_sketch_group_state(
        sketch,
        prepared.target,
        prepared.resolved,
        handle_tag=handle_tag,
    )
