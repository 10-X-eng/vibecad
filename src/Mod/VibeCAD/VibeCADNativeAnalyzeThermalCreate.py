# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of FEM thermal conditions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeAnalyzeThermalState import (
    thermal_condition_family,
    thermal_condition_state,
)
from VibeCADNativeAnalyzeThermalValues import (
    PreparedThermalValues,
    apply_thermal_values,
    prepare_thermal_values,
    thermal_family_for_mode,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedThermalCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    family: str
    label: str
    values: PreparedThermalValues


def thermal_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def _allowed_reference_kinds(family: str) -> frozenset[str]:
    if family == "surface_condition":
        return frozenset({"Edge", "Face"})
    if family == "nodal_condition":
        return frozenset({"Vertex", "Edge", "Face"})
    if family == "body_heat_source":
        return frozenset({"Solid", "Face"})
    return frozenset()


def _initial_temperature_exists(analysis: Any, *, excluding: Any = None) -> bool:
    for member in tuple(analysis.Group or ()):
        if member is excluding:
            continue
        try:
            if thermal_condition_family(member) == "initial_temperature":
                return True
        except NativeAnalyzeError:
            continue
    return False


def prepare_thermal_create(
    document: Any,
    document_uid: str,
    *,
    mode: str,
    analysis: Any,
    label: Any,
    values: Any,
    references: Any = None,
) -> PreparedThermalCreate:
    target = prepare_analysis_target(document, document_uid, analysis)
    family = thermal_family_for_mode(mode)
    prepared_references: tuple[PreparedGeometryReference, ...] = ()
    if family == "initial_temperature":
        if references is not None:
            raise NativeAnalyzeError("Initial temperature is analysis-global and accepts no geometry.")
        if _initial_temperature_exists(target.analysis):
            raise NativeAnalyzeError(
                "This FEM analysis already contains its one global initial temperature."
            )
    else:
        prepared_references = prepare_geometry_references(
            document,
            document_uid,
            references,
            allowed_kinds=_allowed_reference_kinds(family),
        )
        if not prepared_references:
            raise NativeAnalyzeError(
                f"A {mode.replace('_', ' ')} condition requires exact geometry references."
            )
    return PreparedThermalCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        family,
        thermal_label(label),
        prepare_thermal_values(mode, values),
    )


def _factory(document: Any, family: str) -> Any:
    import ObjectsFem

    factories = {
        "initial_temperature": (
            "InitialTemperature",
            ObjectsFem.makeConstraintInitialTemperature,
        ),
        "surface_condition": ("HeatFlux", ObjectsFem.makeConstraintHeatflux),
        "nodal_condition": ("Temperature", ObjectsFem.makeConstraintTemperature),
        "body_heat_source": (
            "BodyHeatSource",
            ObjectsFem.makeConstraintBodyHeatSource,
        ),
    }
    try:
        stem, factory = factories[family]
    except KeyError as exc:
        raise NativeAnalyzeError("The requested FEM thermal family is unavailable.") from exc
    return factory(document, document.getUniqueObjectName(stem))


def create_thermal_condition(
    document: Any,
    prepared: PreparedThermalCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedThermalCreate):
        raise TypeError("prepared must be PreparedThermalCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after thermal preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Thermal-condition geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.family == "initial_temperature" and _initial_temperature_exists(
        prepared.analysis.analysis
    ):
        raise NativeAnalyzeError(
            "This FEM analysis already contains its one global initial temperature."
        )
    condition = _factory(document, prepared.family)
    if condition is None or thermal_condition_family(condition) != prepared.family:
        raise NativeAnalyzeError("The FEM thermal factory returned the wrong object type.")
    condition.Label = prepared.label
    apply_thermal_values(condition, prepared.values)
    if prepared.family != "initial_temperature":
        condition.References = reference_value(prepared.references)
    prepared.analysis.analysis.addObject(condition)
    if condition not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM thermal condition was not added to its analysis.")
    publish_operation(document, prepared.boundary, condition)
    return NativeMutationDraft(
        value={"condition": condition, "prepared": prepared},
        recompute_targets=(condition, prepared.analysis.analysis),
        created=(object_identity(condition),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_thermal_create(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    condition = draft.value["condition"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, condition)
    state = thermal_condition_state(condition)
    checks = {
        "live object": is_live(document, condition),
        "thermal family": state["thermal_family"] == prepared.family,
        "thermal mode": state["thermal_mode"] == prepared.values.mode,
        "label": str(condition.Label) == prepared.label,
        "solver values": state["definition"] == prepared.values.normalized(),
        "geometry references": references_match(condition, prepared.references),
        "analysis append order": tuple(analysis.Group or ())
        == (*prepared.members_before, condition),
        "current geometry": geometry_references_still_exact(prepared.references),
        "native validity": bool(condition.isValid()),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise NativeAnalyzeError(
            "The new FEM thermal condition failed its exact postcondition: "
            + ", ".join(failures)
            + "."
        )
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError("The FEM analysis did not record its thermal condition.")
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_thermal_condition": state,
    }
