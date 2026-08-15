# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM thermal conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometryReference,
    PreparedThermalConditionTarget,
    geometry_references_still_exact,
    prepare_geometry_references,
    prepare_thermal_condition_target,
    reference_value,
    thermal_condition_target_still_exact,
)
from VibeCADNativeAnalyzeThermalCreate import (
    _allowed_reference_kinds,
    _initial_temperature_exists,
    thermal_label,
)
from VibeCADNativeAnalyzeThermalState import thermal_condition_state
from VibeCADNativeAnalyzeThermalValues import (
    PreparedThermalValues,
    apply_thermal_values,
    prepare_thermal_values,
    thermal_family_for_mode,
    thermal_value_fields,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedThermalUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedThermalConditionTarget
    analysis: Any
    analysis_state_sha256: str
    references: tuple[PreparedGeometryReference, ...]
    family: str
    label: str
    values: PreparedThermalValues


def _owner_analysis(document: Any, condition: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and condition in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The FEM thermal condition must belong to exactly one analysis.")
    return owners[0]


def _require_current_history(document: Any, condition: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        condition not in operations
        or str(getattr(condition, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(condition, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM thermal condition is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(condition))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM thermal condition is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _reference_payload(condition: Any) -> list[dict[str, Any]]:
    result = []
    for raw in tuple(getattr(condition, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError("The exact thermal condition has malformed references.")
        source, raw_names = raw
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        result.append(
            {
                "object_name": str(source.Name),
                "expected_state_sha256": mesh_object_state(source)["state_sha256"],
                "subelements": [str(name) for name in names],
            }
        )
    return result


def _updated_values(
    mode: str,
    changes: Mapping[str, Any],
    current_state: Mapping[str, Any],
) -> PreparedThermalValues:
    fields = thermal_value_fields(mode)
    current = (
        current_state["definition"]
        if current_state["thermal_mode"] == mode
        else {}
    )
    missing = [name for name in fields if name not in changes and name not in current]
    if missing:
        raise NativeAnalyzeError(
            f"Changing to {mode} requires {', '.join(missing)} in changes."
        )
    return prepare_thermal_values(
        mode,
        {name: changes.get(name, current[name]) for name in fields},
    )


def prepare_thermal_update(
    document: Any,
    document_uid: str,
    *,
    mode: str,
    target: Any,
    changes: Any,
) -> PreparedThermalUpdate:
    family = thermal_family_for_mode(mode)
    prepared_target = prepare_thermal_condition_target(
        document,
        document_uid,
        target,
        expected_family=family,
    )
    condition = prepared_target.condition
    _require_current_history(document, condition)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError("changes must be one non-empty FEM thermal edit object.")
    allowed = {"label", *thermal_value_fields(mode)}
    if family != "initial_temperature":
        allowed.add("references")
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(f"changes accepts only {', '.join(sorted(allowed))}.")
    state = thermal_condition_state(condition)
    values = _updated_values(mode, changes, state)
    label = (
        thermal_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(condition.Label)
    )
    references: tuple[PreparedGeometryReference, ...] = ()
    if family == "initial_temperature":
        owner = _owner_analysis(document, condition)
        if _initial_temperature_exists(owner, excluding=condition):
            raise NativeAnalyzeError(
                "The analysis contains more than one initial-temperature condition."
            )
    else:
        references = prepare_geometry_references(
            document,
            document_uid,
            changes.get("references", _reference_payload(condition)),
            allowed_kinds=_allowed_reference_kinds(family),
        )
        if not references:
            raise NativeAnalyzeError(
                f"A {mode.replace('_', ' ')} condition requires exact geometry references."
            )
        owner = _owner_analysis(document, condition)
    prepared = PreparedThermalUpdate(
        creation_boundary(document),
        prepared_target,
        owner,
        analysis_state(owner)["state_sha256"],
        references,
        family,
        label,
        values,
    )
    if (
        label == str(condition.Label)
        and state["thermal_mode"] == mode
        and state["definition"] == values.normalized()
        and references_match(condition, references)
    ):
        raise NativeAnalyzeError(
            "The requested FEM thermal edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return prepared


def update_thermal_condition(
    document: Any,
    prepared: PreparedThermalUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedThermalUpdate):
        raise TypeError("prepared must be PreparedThermalUpdate")
    require_boundary(document, prepared.boundary)
    if not thermal_condition_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM thermal condition changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after thermal edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Thermal-condition geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    condition = prepared.target.condition
    condition.Label = prepared.label
    apply_thermal_values(condition, prepared.values)
    if prepared.family != "initial_temperature":
        condition.References = reference_value(prepared.references)
    return NativeMutationDraft(
        value={"condition": condition, "prepared": prepared},
        recompute_targets=(condition,),
        changed=(object_identity(condition),),
    )


def verify_thermal_update(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    condition = draft.value["condition"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = thermal_condition_state(condition)
    checks = {
        "live object": is_live(document, condition),
        "thermal family": state["thermal_family"] == prepared.family,
        "thermal mode": state["thermal_mode"] == prepared.values.mode,
        "label": str(condition.Label) == prepared.label,
        "solver values": state["definition"] == prepared.values.normalized(),
        "geometry references": references_match(condition, prepared.references),
        "analysis membership": condition in tuple(prepared.analysis.Group or ()),
        "stable analysis membership": analysis_state(prepared.analysis)["state_sha256"]
        == prepared.analysis_state_sha256,
        "current geometry": geometry_references_still_exact(prepared.references),
        "native validity": bool(condition.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        detail = ""
        if "solver values" in failures:
            detail = (
                f" actual={state['definition']!r};"
                f" expected={prepared.values.normalized()!r};"
            )
        raise NativeAnalyzeError(
            "The FEM thermal edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
            + detail
        )
    return {"updated_thermal_condition": state}
