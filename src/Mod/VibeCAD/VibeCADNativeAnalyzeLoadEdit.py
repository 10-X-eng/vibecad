# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact FEM mechanical loads."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeLoadCreate import (
    PreparedDirectionReference,
    direction_still_exact,
    direction_value,
    expected_load_definition,
    load_label,
    prepare_centrifugal_axis,
    prepare_centrifugal_scope,
    prepare_force_direction,
)
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeLoadValues import (
    PreparedLoadValues,
    apply_load_values,
    prepare_load_values,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometryReference,
    PreparedLoadTarget,
    geometry_references_still_exact,
    load_target_still_exact,
    prepare_geometry_references,
    prepare_load_target,
    reference_value,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedLoadUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedLoadTarget
    analysis: Any
    analysis_state_sha256: str
    references: tuple[PreparedGeometryReference, ...]
    direction: PreparedDirectionReference | None
    axis: PreparedGeometryReference | None
    scope_kind: str | None
    kind: str
    label: str
    values: PreparedLoadValues
    values_changed: bool


def _owner_analysis(document: Any, load: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and load in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The FEM load must belong to exactly one analysis.")
    return owners[0]


def _require_current_history(document: Any, load: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        load not in operations
        or str(getattr(load, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(load, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM load is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(load))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM load is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _reference_payload(value: Any) -> list[dict[str, Any]]:
    result = []
    for raw in tuple(getattr(value, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError("The exact FEM load has malformed geometry references.")
        source, names = raw
        names = (names,) if isinstance(names, str) else tuple(names or ())
        result.append(
            {
                "object_name": str(source.Name),
                "expected_state_sha256": mesh_object_state(source)["state_sha256"],
                "subelements": [str(name) for name in names],
            }
        )
    return result


def _direction_payload(load: Any) -> dict[str, Any]:
    value = load.Direction
    if value is None:
        return {"kind": "normal", "reversed": bool(load.Reversed)}
    if not isinstance(value, tuple) or len(value) != 2 or value[0] is None:
        raise NativeAnalyzeError("The exact force has a malformed direction reference.")
    source, raw_names = value
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    if len(names) > 1:
        raise NativeAnalyzeError("The exact force has more than one direction subelement.")
    return {
        "kind": "reference",
        "object_name": str(source.Name),
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": str(names[0]) if names else "",
        "reversed": bool(load.Reversed),
    }


def _axis_payload(load: Any) -> dict[str, Any]:
    values = tuple(load.RotationAxis or ())
    if len(values) != 1:
        raise NativeAnalyzeError("The exact centrifugal load has no singular axis.")
    source, raw_names = values[0]
    names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
    if len(names) != 1:
        raise NativeAnalyzeError("The exact centrifugal axis is malformed.")
    return {
        "object_name": str(source.Name),
        "expected_state_sha256": mesh_object_state(source)["state_sha256"],
        "subelement": str(names[0]),
    }


def _current_load_values(kind: str, state: Mapping[str, Any]) -> dict[str, Any]:
    definition = state["definition"]
    if kind == "force":
        return {
            "force_n": definition["force_n"],
            "reversed": definition["direction"]["reversed"],
        }
    if kind == "centrifugal":
        return {"rotation_frequency_hz": definition["rotation_frequency_hz"]}
    return dict(definition)


def prepare_load_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedLoadUpdate:
    prepared_target = prepare_load_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    load = prepared_target.load
    _require_current_history(document, load)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError("changes must be one non-empty FEM load edit object.")
    allowed_by_kind = {
        "force": {"label", "references", "force_n", "direction"},
        "pressure": {"label", "references", "pressure_pa", "reversed"},
        "centrifugal": {"label", "axis", "scope", "rotation_frequency_hz"},
        "gravity": {"label", "acceleration_m_s2", "direction"},
    }
    allowed = allowed_by_kind[kind]
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(f"changes accepts only {', '.join(sorted(allowed))}.")
    current_state = load_state(load)
    current_values = _current_load_values(kind, current_state)
    raw_direction = None
    if kind == "force":
        raw_direction = changes.get("direction", _direction_payload(load))
        if not isinstance(raw_direction, Mapping) or "reversed" not in raw_direction:
            raise NativeAnalyzeError(
                "changes.direction must include kind and reversed."
            )
        values_raw = {
            "force_n": changes.get("force_n", current_values["force_n"]),
            "reversed": raw_direction["reversed"],
        }
        values_changed = "force_n" in changes or "direction" in changes
    elif kind == "pressure":
        values_raw = {
            "pressure_pa": changes.get(
                "pressure_pa",
                current_values["pressure_pa"],
            ),
            "reversed": changes.get("reversed", current_values["reversed"]),
        }
        values_changed = "pressure_pa" in changes or "reversed" in changes
    elif kind == "centrifugal":
        values_raw = {
            "rotation_frequency_hz": changes.get(
                "rotation_frequency_hz",
                current_values["rotation_frequency_hz"],
            )
        }
        values_changed = "rotation_frequency_hz" in changes
    else:
        values_raw = {
            "acceleration_m_s2": changes.get(
                "acceleration_m_s2",
                current_values["acceleration_m_s2"],
            ),
            "direction": changes.get("direction", current_values["direction"]),
        }
        values_changed = (
            "acceleration_m_s2" in changes or "direction" in changes
        )
    values = prepare_load_values(kind, values_raw)
    label = (
        load_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(load.Label)
    )
    references: tuple[PreparedGeometryReference, ...] = ()
    direction = None
    axis = None
    scope_kind = None
    if kind == "force":
        references = prepare_geometry_references(
            document,
            document_uid,
            changes.get("references", _reference_payload(load)),
            allowed_kinds=frozenset({"Vertex", "Edge", "Face"}),
        )
        direction = prepare_force_direction(
            document,
            document_uid,
            {key: value for key, value in raw_direction.items() if key != "reversed"},
        )
    elif kind == "pressure":
        references = prepare_geometry_references(
            document,
            document_uid,
            changes.get("references", _reference_payload(load)),
            allowed_kinds=frozenset({"Edge", "Face"}),
        )
    elif kind == "centrifugal":
        axis = prepare_centrifugal_axis(
            document,
            document_uid,
            changes.get("axis", _axis_payload(load)),
        )
        current_references = _reference_payload(load)
        current_scope = (
            {"kind": "selected_geometry", "references": current_references}
            if current_references
            else {"kind": "all_bodies"}
        )
        scope_kind, references = prepare_centrifugal_scope(
            document,
            document_uid,
            changes.get("scope", current_scope),
        )
    if kind in {"force", "pressure"} and not references:
        raise NativeAnalyzeError(
            f"A {kind} load requires at least one exact geometry reference."
        )
    owner = _owner_analysis(document, load)
    prepared = PreparedLoadUpdate(
        creation_boundary(document),
        prepared_target,
        owner,
        analysis_state(owner)["state_sha256"],
        references,
        direction,
        axis,
        scope_kind,
        kind,
        label,
        values,
        values_changed,
    )
    if (
        label == str(load.Label)
        and references_match(load, references)
        and current_state["definition"] == expected_load_definition(prepared)
    ):
        raise NativeAnalyzeError(
            "The requested FEM load edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return prepared


def update_load(document: Any, prepared: PreparedLoadUpdate) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedLoadUpdate):
        raise TypeError("prepared must be a PreparedLoadUpdate")
    require_boundary(document, prepared.boundary)
    if not load_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM load changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after load edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Load reference geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.axis is not None and not geometry_references_still_exact((prepared.axis,)):
        raise NativeAnalyzeError(
            "Centrifugal axis geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not direction_still_exact(prepared.direction):
        raise NativeAnalyzeError(
            "Force-direction geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    load = prepared.target.load
    load.Label = prepared.label
    if prepared.values_changed:
        apply_load_values(load, prepared.values)
    load.References = reference_value(prepared.references)
    if prepared.kind == "force":
        load.Direction = direction_value(prepared.direction)
    elif prepared.kind == "centrifugal":
        assert prepared.axis is not None
        load.RotationAxis = reference_value((prepared.axis,))
    return NativeMutationDraft(
        value={"load": load, "prepared": prepared},
        recompute_targets=(load,),
        changed=(object_identity(load),),
    )


def _force_direction_usable(load: Any, kind: str) -> bool:
    if kind != "force":
        return True
    vector = load.DirectionVector
    length = math.sqrt(sum(float(vector[index]) ** 2 for index in range(3)))
    return math.isfinite(length) and length > 1.0e-12


def verify_load_update(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    load = draft.value["load"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = load_state(load)
    checks = {
        "live object": is_live(document, load),
        "label": str(load.Label) == prepared.label,
        "load kind": state["load_kind"] == prepared.kind,
        "solver values": state["definition"] == expected_load_definition(prepared),
        "geometry references": references_match(load, prepared.references),
        "analysis membership": load in tuple(prepared.analysis.Group or ()),
        "stable analysis membership": analysis_state(prepared.analysis)["state_sha256"]
        == prepared.analysis_state_sha256,
        "current geometry": geometry_references_still_exact(prepared.references),
        "current direction": direction_still_exact(prepared.direction),
        "usable force direction": _force_direction_usable(load, prepared.kind),
        "native validity": bool(load.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        value_detail = ""
        if "solver values" in failures:
            value_detail = (
                f" actual={state['definition']!r}; "
                f"expected={expected_load_definition(prepared)!r};"
            )
        raise NativeAnalyzeError(
            "The FEM load edit failed its exact postcondition: "
            + ", ".join(failures)
            + "."
            + value_detail
        )
    return {"updated_load": state}
