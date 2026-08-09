# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, preflight, execution, and verification for Design Thickness."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeDesignDressupTargets import (
    DesignDressupSelection,
    DesignDressupTarget,
    dressup_target_elements,
    preflight_dressup_selection,
    prepare_dressup_selection,
)
from VibeCADNativeDesignResults import DesignResultSpec, create_design_operation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_MODES = {
    "skin": "Skin",
    "pipe": "Pipe",
    "recto_verso": "RectoVerso",
}
_JOINS = {
    "arc": "Arc",
    "intersection": "Intersection",
}


@dataclass(frozen=True, slots=True)
class DesignThicknessSpec:
    targets: tuple[DesignDressupTarget, ...]
    thickness_mm: float
    direction: str
    mode: str
    join: str
    intersection_handling: bool


def prepare_design_thickness(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignThicknessSpec:
    selection = prepare_dressup_selection(
        document_uid,
        values.get("selection"),
        operation="Thickness",
        allow_all_edges=False,
        allowed_subelement_types=frozenset({"Face"}),
    )
    raw_thickness = values.get("thickness_mm")
    if isinstance(raw_thickness, bool):
        raise NativeModelError("Thickness must be a number.")
    try:
        thickness = float(raw_thickness)
    except (TypeError, ValueError) as exc:
        raise NativeModelError("Thickness must be a number.") from exc
    if not math.isfinite(thickness) or not 0.0 < thickness <= 1_000_000.0:
        raise NativeModelError("Thickness must be finite and greater than zero.")

    direction = str(values.get("direction") or "")
    if direction not in {"inward", "outward"}:
        raise NativeModelError("Thickness direction must be inward or outward.")
    mode = str(values.get("mode") or "")
    if mode not in _MODES:
        raise NativeModelError("Thickness mode must be skin, pipe, or recto_verso.")
    join = str(values.get("join") or "")
    if join not in _JOINS:
        raise NativeModelError("Thickness join must be arc or intersection.")
    intersection_handling = values.get("intersection_handling")
    if not isinstance(intersection_handling, bool):
        raise NativeModelError("Thickness intersection_handling must be boolean.")
    return DesignThicknessSpec(
        selection.targets,
        thickness,
        direction,
        mode,
        join,
        intersection_handling,
    )


def preflight_design_thickness(
    document: Any,
    spec: DesignThicknessSpec,
) -> tuple[Any, ...]:
    return preflight_dressup_selection(
        document,
        DesignDressupSelection(spec.targets, False),
        operation="Thickness",
    )


def _quantity_value(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _verify_thickness(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    spec: DesignThicknessSpec = expected["spec"]
    offsets, elements = dressup_target_elements(spec.targets)
    thickness = _quantity_value(operation.Value)
    base = getattr(operation, "Base", None)
    linked_base = base[0] if isinstance(base, tuple) and base else base
    output_shapes = list(operation.OutputShapes)
    if (
        not math.isclose(thickness, spec.thickness_mm, rel_tol=1.0e-9, abs_tol=1.0e-7)
        or bool(operation.Reversed) is not (spec.direction == "inward")
        or str(operation.Mode) != _MODES[spec.mode]
        or str(operation.Join) != _JOINS[spec.join]
        or bool(operation.Intersection) is not spec.intersection_handling
        or list(operation.TargetElementOffsets) != offsets
        or list(operation.TargetElements) != elements
        or linked_base is not None
        or operation.BaseFeature is not None
        or list(operation.InputBodyIds) != list(operation.OutputBodyIds)
        or list(operation.OutputPreviousInputIndices) != list(range(len(spec.targets)))
        or len(operation.InputStates) != len(spec.targets)
        or len(output_shapes) != len(spec.targets)
    ):
        raise NativeModelError("The Design Thickness controls changed before commit.")
    if any(
        shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
        for shape in output_shapes
    ):
        raise NativeModelError("The Design Thickness produced an invalid Body result.")
    return {
        "thickness_mm": thickness,
        "direction": spec.direction,
        "mode": spec.mode,
        "join": spec.join,
        "intersection_handling": spec.intersection_handling,
        "target_count": len(spec.targets),
        "selected_face_count": len(elements),
    }


def create_design_thickness(
    document: Any,
    *,
    label: str,
    spec: DesignThicknessSpec,
) -> NativeMutationDraft:
    offsets, elements = dressup_target_elements(spec.targets)
    result_spec = DesignResultSpec(
        "modify",
        tuple(target.body for target in spec.targets),
        None,
    )

    def configure(operation: Any) -> Mapping[str, Any]:
        operation.Value = spec.thickness_mm
        operation.Reversed = spec.direction == "inward"
        operation.Mode = _MODES[spec.mode]
        operation.Join = _JOINS[spec.join]
        operation.Intersection = spec.intersection_handling
        operation.TargetElementOffsets = offsets
        operation.TargetElements = elements
        return {"spec": spec}

    return create_design_operation(
        document,
        type_id="PartDesign::DesignThickness",
        base_name="Thickness",
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=_verify_thickness,
        configure_after_targets=True,
    )
