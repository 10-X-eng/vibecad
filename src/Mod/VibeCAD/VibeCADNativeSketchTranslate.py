# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of the human Sketch Translate / array command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTranslateState import (
    SketchTranslatePlan,
    SketchTranslateSnapshot,
    capture_translate_snapshot,
    parse_translate_diagnostic,
    require_pure_translate_diagnostic,
    require_translate_snapshot_unchanged,
    verify_translate_state,
)
from VibeCADNativeSketchTranslateTarget import (
    LABEL,
    SketchTranslateSpec,
    prepare_sketch_translate,
)
from VibeCADNativeTargets import object_identity


OPERATION = "translate"


@dataclass(frozen=True, slots=True)
class PreparedSketchTranslate:
    snapshot: SketchTranslateSnapshot
    plan: SketchTranslatePlan


@dataclass(frozen=True, slots=True)
class AppliedSketchTranslate:
    prepared: PreparedSketchTranslate
    receipt: Any


def _diagnose(snapshot: SketchTranslateSnapshot) -> Any:
    method = getattr(snapshot.target.sketch, "diagnoseTranslate", None)
    if not callable(method):
        raise NativeSketchError(f"{LABEL} feasibility is unavailable.")
    spec = snapshot.spec
    try:
        import FreeCAD as App

        return method(
            list(spec.geometry_indices),
            App.Vector(*spec.first_translation_mm, 0.0),
            spec.copy_count,
            App.Vector(*spec.second_translation_mm, 0.0),
            spec.row_count,
            spec.equalize_dimensional_constraints,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} target and array options."
        ) from exc


def prepare_translate(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTranslateSpec:
    return prepare_sketch_translate(document_uid, value)


def preflight_translate(
    context: NativeRuntimeContext,
    spec: SketchTranslateSpec,
) -> PreparedSketchTranslate:
    snapshot = capture_translate_snapshot(context, spec)
    plan = parse_translate_diagnostic(_diagnose(snapshot), snapshot)
    require_pure_translate_diagnostic(snapshot)
    return PreparedSketchTranslate(snapshot, plan)


def create_translate(
    document: Any,
    prepared: PreparedSketchTranslate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchTranslate):
        raise TypeError("prepared must be exact Sketch Translate state")
    sketch = require_translate_snapshot_unchanged(document, prepared.snapshot)
    current_plan = parse_translate_diagnostic(
        _diagnose(prepared.snapshot), prepared.snapshot
    )
    require_pure_translate_diagnostic(prepared.snapshot)
    if current_plan != prepared.plan:
        changed = tuple(
            field
            for field in (
                "identity",
                "geometry_records",
                "constraint_records",
                "external_reference_records",
                "external_geometry_records",
                "expression_records",
                "degrees_of_freedom",
            )
            if getattr(current_plan, field) != getattr(prepared.plan, field)
        )
        raise NativeSketchError(
            f"The exact {LABEL} result changed after preflight: "
            + ", ".join(changed)
            + "."
        )
    method = getattr(sketch, "translateExact", None)
    if not callable(method):
        raise NativeSketchError(f"Exact {LABEL} execution is unavailable.")
    spec = prepared.snapshot.spec
    try:
        import FreeCAD as App

        receipt = method(
            list(spec.geometry_indices),
            App.Vector(*spec.first_translation_mm, 0.0),
            spec.copy_count,
            App.Vector(*spec.second_translation_mm, 0.0),
            spec.row_count,
            spec.equalize_dimensional_constraints,
        )
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {LABEL} operation."
        ) from exc
    return NativeMutationDraft(
        value=AppliedSketchTranslate(prepared, receipt),
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_translate(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    applied = draft.value
    if not isinstance(applied, AppliedSketchTranslate):
        raise TypeError("draft must contain applied Sketch Translate state")
    spec = applied.prepared.snapshot.spec
    (
        sketch,
        created_geometry,
        deleted_geometry,
        created_constraints,
        deleted_constraints,
    ) = verify_translate_state(
        document,
        applied.prepared.snapshot,
        applied.prepared.plan,
        applied.receipt,
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "mode": "move" if spec.copy_count == 0 else "array",
            "input_geometry_count": len(spec.geometry_indices),
            "copy_count": spec.copy_count,
            "row_count": spec.row_count,
            "constraint_mode": spec.constraint_mode,
            "created_geometry_count": len(created_geometry),
            "deleted_geometry_count": len(deleted_geometry),
            "created_constraint_count": len(created_constraints),
            "deleted_constraint_count": len(deleted_constraints),
            "created_geometry_indices": list(created_geometry[:32]),
            "deleted_geometry_indices": list(deleted_geometry[:32]),
            "created_constraint_indices": list(created_constraints[:32]),
            "deleted_constraint_indices": list(deleted_constraints[:32]),
        },
    )
