# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of common, reinforced, and nonlinear FEM materials."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    finalize_new_operation_resource,
    publish_operation,
    require_boundary,
    stage_operation_resource_reconciliation,
    verify_operation_block,
    verify_new_operation_resource,
)
from VibeCADNativeAnalyzeMaterials import material_map
from VibeCADNativeAnalyzeState import (
    analysis_state,
    analysis_still_exact,
    is_live,
    material_kind,
    material_state,
    material_without_nonlinear_sha256,
)
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    PreparedMaterialTarget,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    prepare_material_target,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_MODEL_VALUES = {
    "isotropic_hardening": "isotropic hardening",
    "kinematic_hardening": "kinematic hardening",
}


@dataclass(frozen=True, slots=True)
class PreparedMaterialCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    references: tuple[PreparedGeometryReference, ...]
    kind: str
    label: str
    material: tuple[tuple[str, str], ...]
    material_uuid: str
    reinforcement: tuple[tuple[str, str], ...] = ()
    reinforcement_uuid: str = ""


@dataclass(frozen=True, slots=True)
class PreparedNonlinearCreate:
    boundary: AnalyzeCreationBoundary
    base: PreparedMaterialTarget
    base_without_link_sha256: str
    analysis: Any
    analysis_state_sha256: str
    label: str
    model: str
    yield_points: tuple[tuple[float, float], ...]


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAnalyzeError("label must contain 1 to 160 visible characters.")
    return result


def _pairs(value: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _map(value: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(value)


def prepare_material_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    references: Any,
    material_uuid: Any | None = None,
    properties: Any | None = None,
    reinforcement_uuid: Any | None = None,
    reinforcement_properties: Any | None = None,
) -> PreparedMaterialCreate:
    if kind not in {"solid", "fluid", "reinforced"}:
        raise NativeAnalyzeError("The requested FEM material kind is unavailable.")
    clean_label = _label(label)
    target = prepare_analysis_target(document, document_uid, analysis)
    prepared_references = prepare_geometry_references(
        document,
        document_uid,
        references,
    )
    category = "fluid" if kind == "fluid" else "solid"
    matrix, matrix_uuid = material_map(
        None,
        category=category,
        material_uuid=material_uuid,
        properties=properties,
    )
    matrix.setdefault("Name", clean_label)
    reinforcement: dict[str, str] = {}
    resolved_reinforcement_uuid = ""
    if kind == "reinforced":
        reinforcement, resolved_reinforcement_uuid = material_map(
            None,
            category="solid",
            material_uuid=reinforcement_uuid,
            properties=reinforcement_properties,
        )
        reinforcement.setdefault("Name", clean_label)
    elif reinforcement_uuid is not None or reinforcement_properties is not None:
        raise NativeAnalyzeError(
            "Reinforcement fields are valid only for a reinforced material."
        )
    return PreparedMaterialCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepared_references,
        kind,
        clean_label,
        _pairs(matrix),
        matrix_uuid,
        _pairs(reinforcement),
        resolved_reinforcement_uuid,
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    if kind == "solid":
        return ObjectsFem.makeMaterialSolid(
            document,
            document.getUniqueObjectName("MaterialSolid"),
        )
    if kind == "fluid":
        return ObjectsFem.makeMaterialFluid(
            document,
            document.getUniqueObjectName("MaterialFluid"),
        )
    return ObjectsFem.makeMaterialReinforced(
        document,
        document.getUniqueObjectName("MaterialReinforced"),
    )


def create_material(
    document: Any,
    prepared: PreparedMaterialCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMaterialCreate):
        raise TypeError("prepared must be a PreparedMaterialCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after material preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.references):
        raise NativeAnalyzeError(
            "Material reference geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    material = _factory(document, prepared.kind)
    if material is None or material_kind(material) != prepared.kind:
        raise NativeAnalyzeError("The FEM material factory returned the wrong object type.")
    material.Label = prepared.label
    material.Material = _map(prepared.material)
    material.UUID = prepared.material_uuid
    material.References = reference_value(prepared.references)
    if prepared.kind == "reinforced":
        material.Reinforcement = _map(prepared.reinforcement)
        material.ReinforcementUUID = prepared.reinforcement_uuid
    prepared.analysis.analysis.addObject(material)
    if material not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM material was not added to its analysis.")
    publish_operation(document, prepared.boundary, material)
    return NativeMutationDraft(
        value={"material": material, "prepared": prepared},
        recompute_targets=(material, prepared.analysis.analysis),
        created=(object_identity(material),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def _references_match(
    material: Any,
    references: tuple[PreparedGeometryReference, ...],
) -> bool:
    actual = tuple(getattr(material, "References", ()) or ())
    if len(actual) != len(references):
        return False
    for raw, expected in zip(actual, references):
        if not isinstance(raw, tuple) or len(raw) != 2:
            return False
        if raw[0] is not expected.source or tuple(raw[1] or ()) != expected.subelements:
            return False
    return True


def verify_material_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    material = draft.value["material"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, material)
    expected_members = (*prepared.members_before, material)
    if (
        not is_live(document, material)
        or material_kind(material) != prepared.kind
        or str(material.Label) != prepared.label
        or dict(material.Material or {}) != _map(prepared.material)
        or str(material.UUID or "") != prepared.material_uuid
        or not _references_match(material, prepared.references)
        or tuple(analysis.Group or ()) != expected_members
        or not geometry_references_still_exact(prepared.references)
        or not bool(material.isValid())
    ):
        raise NativeAnalyzeError("The new FEM material failed its exact postcondition.")
    if prepared.kind == "reinforced" and (
        dict(material.Reinforcement or {}) != _map(prepared.reinforcement)
        or str(material.ReinforcementUUID or "") != prepared.reinforcement_uuid
    ):
        raise NativeAnalyzeError(
            "The new reinforced FEM material failed its exact postcondition."
        )
    current_analysis = analysis_state(analysis)
    return {
        "analysis": current_analysis,
        "analysis_target": {
            "object_name": current_analysis["object_name"],
            "expected_state_sha256": current_analysis["state_sha256"],
            "expected_member_count": current_analysis["member_count"],
        },
        "created_material": material_state(material),
    }


def _owner_analysis(document: Any, material: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and material in tuple(obj.Group or ()):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError(
            "The solid material must belong to exactly one FEM analysis."
        )
    return owners[0]


def prepare_yield_points(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise NativeAnalyzeError("yield_points must contain 1 to 128 points.")
    result = []
    previous_strain = -1.0
    for index, point in enumerate(value):
        if not isinstance(point, Mapping) or set(point) != {
            "stress_mpa",
            "plastic_strain",
        }:
            raise NativeAnalyzeError(
                "Each yield point must contain only stress_mpa and plastic_strain."
            )
        stress = point["stress_mpa"]
        strain = point["plastic_strain"]
        if type(stress) not in {int, float} or type(strain) not in {int, float}:
            raise NativeAnalyzeError("Yield-point values must be finite numbers.")
        stress_value = float(stress)
        strain_value = float(strain)
        if (
            not math.isfinite(stress_value)
            or not math.isfinite(strain_value)
            or stress_value <= 0.0
            or stress_value > 1.0e12
            or strain_value < 0.0
            or strain_value > 10.0
        ):
            raise NativeAnalyzeError(
                f"yield_points[{index}] is outside its physical input bound."
            )
        if strain_value <= previous_strain:
            raise NativeAnalyzeError(
                "yield_points plastic_strain values must be strictly increasing."
            )
        previous_strain = strain_value
        result.append((stress_value, strain_value))
    return tuple(result)


def prepare_nonlinear_create(
    document: Any,
    document_uid: str,
    *,
    base_material: Any,
    label: Any,
    model: Any,
    yield_points: Any,
) -> PreparedNonlinearCreate:
    base = prepare_material_target(document, document_uid, base_material)
    if base.kind not in {"solid", "reinforced"}:
        raise NativeAnalyzeError(
            "Nonlinear properties require an exact solid material in an analysis.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    if getattr(base.material, "Nonlinear", None) is not None:
        raise NativeAnalyzeError("The exact solid material already has nonlinear properties.")
    selected_model = str(model or "")
    if selected_model not in _MODEL_VALUES:
        raise NativeAnalyzeError("model must be isotropic_hardening or kinematic_hardening.")
    analysis = _owner_analysis(document, base.material)
    return PreparedNonlinearCreate(
        creation_boundary(document),
        base,
        material_without_nonlinear_sha256(base.material),
        analysis,
        analysis_state(analysis)["state_sha256"],
        _label(label),
        _MODEL_VALUES[selected_model],
        prepare_yield_points(yield_points),
    )


def native_yield_points(points: tuple[tuple[float, float], ...]) -> list[str]:
    return [f"{stress:.17g}, {strain:.17g}" for stress, strain in points]


def create_nonlinear_material(
    document: Any,
    prepared: PreparedNonlinearCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedNonlinearCreate):
        raise TypeError("prepared must be a PreparedNonlinearCreate")
    require_boundary(document, prepared.boundary)
    base = prepared.base.material
    if (
        not material_without_nonlinear_sha256(base)
        == prepared.base_without_link_sha256
        or getattr(base, "Nonlinear", None) is not None
        or not analysis_still_exact(prepared.analysis, prepared.analysis_state_sha256)
    ):
        raise NativeAnalyzeError(
            "The exact material or its analysis changed after nonlinear preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    import ObjectsFem

    old_resources = stage_operation_resource_reconciliation(
        document,
        prepared.boundary,
        base,
    )

    try:
        nonlinear = ObjectsFem.makeMaterialMechanicalNonlinear(
            document,
            base,
            document.getUniqueObjectName("MaterialMechanicalNonlinear"),
        )
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The nonlinear-material factory failed: {exc}",
            error_code="NATIVE_ANALYZE_FACTORY_FAILED",
        ) from exc
    if nonlinear is None or material_kind(nonlinear) != "nonlinear":
        raise NativeAnalyzeError("The nonlinear-material factory returned the wrong type.")
    try:
        nonlinear.Label = prepared.label
        nonlinear.MaterialModelNonlinearity = prepared.model
        nonlinear.YieldPoints = native_yield_points(prepared.yield_points)
    except Exception as exc:
        raise NativeAnalyzeError(
            f"The nonlinear-material properties could not be assigned: {exc}",
            error_code="NATIVE_ANALYZE_PROPERTY_ASSIGNMENT_FAILED",
        ) from exc
    finalize_new_operation_resource(
        document,
        prepared.boundary,
        base,
        old_resources,
        nonlinear,
    )
    return NativeMutationDraft(
        value={
            "nonlinear": nonlinear,
            "old_resources": old_resources,
            "prepared": prepared,
        },
        recompute_targets=(base, nonlinear),
        created=(object_identity(nonlinear),),
        changed=(object_identity(base),),
    )


def verify_nonlinear_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    nonlinear = draft.value["nonlinear"]
    old_resources = draft.value["old_resources"]
    prepared = draft.value["prepared"]
    base = prepared.base.material
    verify_new_operation_resource(
        document,
        prepared.boundary,
        base,
        old_resources,
        nonlinear,
    )
    if (
        not is_live(document, nonlinear)
        or material_kind(nonlinear) != "nonlinear"
        or str(nonlinear.Label) != prepared.label
        or str(nonlinear.MaterialModelNonlinearity) != prepared.model
        or tuple(nonlinear.YieldPoints or ())
        != tuple(native_yield_points(prepared.yield_points))
        or getattr(base, "Nonlinear", None) is not nonlinear
        or nonlinear in tuple(prepared.analysis.Group or ())
        or material_without_nonlinear_sha256(base)
        != prepared.base_without_link_sha256
        or not analysis_still_exact(prepared.analysis, prepared.analysis_state_sha256)
        or not bool(nonlinear.isValid())
    ):
        raise NativeAnalyzeError(
            "The nonlinear FEM material failed its exact postcondition."
        )
    return {
        "base_material": material_state(base),
        "created_material": material_state(nonlinear),
    }
