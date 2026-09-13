# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of one exact durable FEM material operation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeLabels import assign_prepared_label
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    require_boundary,
)
from VibeCADNativeAnalyzeMaterialCreate import (
    native_yield_points,
    prepare_yield_points,
)
from VibeCADNativeAnalyzeMaterials import material_map
from VibeCADNativeAnalyzeState import is_live, material_kind, material_state
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometryReference,
    PreparedMaterialTarget,
    geometry_references_still_exact,
    material_target_still_exact,
    prepare_geometry_references,
    prepare_material_target,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_COMMON_FIELDS = frozenset(
    {
        "label",
        "references",
        "material_uuid",
        "clear_material_uuid",
        "properties",
        "clear_properties",
    }
)
_REINFORCEMENT_FIELDS = frozenset(
    {
        "reinforcement_uuid",
        "clear_reinforcement_uuid",
        "reinforcement_properties",
        "clear_reinforcement_properties",
    }
)
_NONLINEAR_FIELDS = frozenset({"label", "model", "yield_points"})
_MODEL_VALUES = {
    "isotropic_hardening": "isotropic hardening",
    "kinematic_hardening": "kinematic hardening",
}


@dataclass(frozen=True, slots=True)
class PreparedMaterialUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedMaterialTarget
    label: str
    references: tuple[tuple[Any, tuple[str, ...]], ...]
    changed_references: tuple[PreparedGeometryReference, ...] | None
    material: tuple[tuple[str, str], ...]
    material_uuid: str
    reinforcement: tuple[tuple[str, str], ...]
    reinforcement_uuid: str
    model: str
    yield_points: tuple[str, ...]


def _label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        raise NativeAnalyzeError("changes.label must contain 1 to 160 visible characters.")
    return result


def _pairs(value: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _reference_pairs(material: Any) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    result = []
    for raw in tuple(getattr(material, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            raise NativeAnalyzeError("The exact material has malformed geometry references.")
        result.append((raw[0], tuple(str(value) for value in tuple(raw[1] or ()))))
    return tuple(result)


def _require_true(changes: Mapping[str, Any], field: str) -> bool:
    if field not in changes:
        return False
    if changes[field] is not True:
        raise NativeAnalyzeError(f"changes.{field} must be true when supplied.")
    return True


def _no_uuid_conflict(changes: Mapping[str, Any], value: str, clear: str) -> None:
    if value in changes and clear in changes:
        raise NativeAnalyzeError(
            f"changes.{value} and changes.{clear} are mutually exclusive."
        )


def _no_property_conflict(changes: Mapping[str, Any], update: str, clear: str) -> None:
    updated = set(dict(changes.get(update) or {}))
    cleared = set(changes.get(clear) or ())
    overlap = sorted(updated & cleared)
    if overlap:
        raise NativeAnalyzeError(
            f"changes.{update} and changes.{clear} overlap: {', '.join(overlap)}."
        )


def _prepare_common_map(
    material: Any,
    kind: str,
    changes: Mapping[str, Any],
) -> tuple[dict[str, str], str]:
    _no_uuid_conflict(changes, "material_uuid", "clear_material_uuid")
    _no_property_conflict(changes, "properties", "clear_properties")
    clear_uuid = _require_true(changes, "clear_material_uuid")
    result, uuid = material_map(
        dict(material.Material or {}),
        category="fluid" if kind == "fluid" else "solid",
        current_uuid=str(material.UUID or ""),
        material_uuid=changes.get("material_uuid"),
        properties=changes.get("properties"),
        clear_properties=changes.get("clear_properties"),
    )
    return result, "" if clear_uuid else uuid


def _prepare_reinforcement_map(
    material: Any,
    changes: Mapping[str, Any],
) -> tuple[dict[str, str], str]:
    _no_uuid_conflict(
        changes,
        "reinforcement_uuid",
        "clear_reinforcement_uuid",
    )
    _no_property_conflict(
        changes,
        "reinforcement_properties",
        "clear_reinforcement_properties",
    )
    clear_uuid = _require_true(changes, "clear_reinforcement_uuid")
    result, uuid = material_map(
        dict(material.Reinforcement or {}),
        category="solid",
        current_uuid=str(material.ReinforcementUUID or ""),
        material_uuid=changes.get("reinforcement_uuid"),
        properties=changes.get("reinforcement_properties"),
        clear_properties=changes.get("clear_reinforcement_properties"),
    )
    return result, "" if clear_uuid else uuid


def _material_values(
    document: Any,
    document_uid: str,
    target: PreparedMaterialTarget,
    changes: Mapping[str, Any],
) -> tuple[
    str,
    tuple[tuple[Any, tuple[str, ...]], ...],
    tuple[PreparedGeometryReference, ...] | None,
    dict[str, str],
    str,
    dict[str, str],
    str,
    str,
    tuple[str, ...],
]:
    material = target.material
    kind = target.kind
    allowed = _NONLINEAR_FIELDS if kind == "nonlinear" else _COMMON_FIELDS
    if kind == "reinforced":
        allowed |= _REINFORCEMENT_FIELDS
    if not changes or not set(changes) <= allowed:
        names = ", ".join(sorted(allowed))
        raise NativeAnalyzeError(
            f"changes must contain at least one field valid for {kind}: {names}."
        )
    label = _label(changes["label"]) if "label" in changes else str(material.Label)
    references = _reference_pairs(material)
    changed_references = None
    if "references" in changes:
        changed_references = prepare_geometry_references(
            document,
            document_uid,
            changes["references"],
        )
        references = tuple(reference_value(changed_references))
    matrix = dict(getattr(material, "Material", {}) or {})
    matrix_uuid = str(getattr(material, "UUID", "") or "")
    reinforcement = dict(getattr(material, "Reinforcement", {}) or {})
    reinforcement_uuid = str(getattr(material, "ReinforcementUUID", "") or "")
    model = str(getattr(material, "MaterialModelNonlinearity", "") or "")
    yield_points = tuple(str(value) for value in tuple(getattr(material, "YieldPoints", ()) or ()))
    if kind != "nonlinear":
        matrix, matrix_uuid = _prepare_common_map(material, kind, changes)
        if kind == "reinforced":
            reinforcement, reinforcement_uuid = _prepare_reinforcement_map(
                material,
                changes,
            )
    else:
        if "model" in changes:
            requested_model = str(changes["model"] or "")
            if requested_model not in _MODEL_VALUES:
                raise NativeAnalyzeError(
                    "changes.model must be isotropic_hardening or kinematic_hardening."
                )
            model = _MODEL_VALUES[requested_model]
        if "yield_points" in changes:
            yield_points = tuple(
                native_yield_points(prepare_yield_points(changes["yield_points"]))
            )
    return (
        label,
        references,
        changed_references,
        matrix,
        matrix_uuid,
        reinforcement,
        reinforcement_uuid,
        model,
        yield_points,
    )


def prepare_material_update(
    document: Any,
    document_uid: str,
    *,
    target: Any,
    changes: Any,
) -> PreparedMaterialUpdate:
    prepared_target = prepare_material_target(document, document_uid, target)
    if not isinstance(changes, Mapping):
        raise NativeAnalyzeError("changes must be one typed material edit object.")
    values = _material_values(
        document,
        document_uid,
        prepared_target,
        dict(changes),
    )
    prepared = PreparedMaterialUpdate(
        creation_boundary(document),
        prepared_target,
        values[0],
        values[1],
        values[2],
        _pairs(values[3]),
        values[4],
        _pairs(values[5]),
        values[6],
        values[7],
        values[8],
    )
    material = prepared_target.material
    current = (
        str(material.Label),
        _reference_pairs(material),
        _pairs(dict(getattr(material, "Material", {}) or {})),
        str(getattr(material, "UUID", "") or ""),
        _pairs(dict(getattr(material, "Reinforcement", {}) or {})),
        str(getattr(material, "ReinforcementUUID", "") or ""),
        str(getattr(material, "MaterialModelNonlinearity", "") or ""),
        tuple(str(value) for value in tuple(getattr(material, "YieldPoints", ()) or ())),
    )
    final = (
        prepared.label,
        prepared.references,
        prepared.material,
        prepared.material_uuid,
        prepared.reinforcement,
        prepared.reinforcement_uuid,
        prepared.model,
        prepared.yield_points,
    )
    if current == final:
        raise NativeAnalyzeError(
            "The requested material edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return prepared


def update_material(
    document: Any,
    prepared: PreparedMaterialUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMaterialUpdate):
        raise TypeError("prepared must be a PreparedMaterialUpdate")
    require_boundary(document, prepared.boundary)
    if not material_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM material changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if prepared.changed_references is not None and not geometry_references_still_exact(
        prepared.changed_references
    ):
        raise NativeAnalyzeError(
            "Material reference geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    material = prepared.target.material
    prepared = assign_prepared_label(material, prepared)
    if prepared.target.kind != "nonlinear":
        material.Material = dict(prepared.material)
        material.UUID = prepared.material_uuid
        material.References = list(prepared.references)
        if prepared.target.kind == "reinforced":
            material.Reinforcement = dict(prepared.reinforcement)
            material.ReinforcementUUID = prepared.reinforcement_uuid
    else:
        material.MaterialModelNonlinearity = prepared.model
        material.YieldPoints = list(prepared.yield_points)
    return NativeMutationDraft(
        value={"material": material, "prepared": prepared},
        recompute_targets=(material,),
        changed=(object_identity(material),),
    )


def verify_material_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    material = draft.value["material"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    if (
        not is_live(document, material)
        or material_kind(material) != prepared.target.kind
        or str(material.Label) != prepared.label
        or _reference_pairs(material) != prepared.references
        or _pairs(dict(getattr(material, "Material", {}) or {}))
        != prepared.material
        or str(getattr(material, "UUID", "") or "") != prepared.material_uuid
        or _pairs(dict(getattr(material, "Reinforcement", {}) or {}))
        != prepared.reinforcement
        or str(getattr(material, "ReinforcementUUID", "") or "")
        != prepared.reinforcement_uuid
        or str(getattr(material, "MaterialModelNonlinearity", "") or "")
        != prepared.model
        or tuple(str(value) for value in tuple(getattr(material, "YieldPoints", ()) or ()))
        != prepared.yield_points
        or not bool(material.isValid())
    ):
        raise NativeAnalyzeError("The FEM material edit failed its exact postcondition.")
    if prepared.changed_references is not None and not geometry_references_still_exact(
        prepared.changed_references
    ):
        raise NativeAnalyzeError("Material reference geometry changed before commit.")
    return {"updated_material": material_state(material)}
