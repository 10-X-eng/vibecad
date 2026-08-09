# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, preflight, execution, and verification for Design Chamfer."""

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


_CHAMFER_TYPES = {
    "equal_distance": "Equal distance",
    "two_distances": "Two distances",
    "distance_angle": "Distance and Angle",
}


@dataclass(frozen=True, slots=True)
class DesignChamferDefinition:
    kind: str
    size_mm: float
    second_size_mm: float | None
    angle_degrees: float | None
    flip_direction: bool


@dataclass(frozen=True, slots=True)
class DesignChamferSpec:
    targets: tuple[DesignDressupTarget, ...]
    use_all_edges: bool
    definition: DesignChamferDefinition


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NativeModelError(f"Chamfer {field} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Chamfer {field} must be a number.") from exc
    if not math.isfinite(parsed) or not 0.0 < parsed <= 1_000_000.0:
        raise NativeModelError(f"Chamfer {field} must be finite and greater than zero.")
    return parsed


def _definition(value: Any) -> DesignChamferDefinition:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError("A Chamfer definition is invalid.")
    kind = str(value["kind"])
    expected = {
        "equal_distance": {"kind", "size_mm"},
        "two_distances": {
            "kind",
            "size_mm",
            "second_size_mm",
            "flip_direction",
        },
        "distance_angle": {
            "kind",
            "size_mm",
            "angle_degrees",
            "flip_direction",
        },
    }.get(kind)
    if expected is None:
        raise NativeModelError("That Chamfer definition type is unavailable.")
    if set(value) != expected:
        raise NativeModelError(f"The {kind} Chamfer definition is invalid.")
    size = _positive(value["size_mm"], "size")
    if kind == "equal_distance":
        return DesignChamferDefinition(kind, size, None, None, False)
    flip = value["flip_direction"]
    if not isinstance(flip, bool):
        raise NativeModelError("Chamfer flip_direction must be boolean.")
    if kind == "two_distances":
        return DesignChamferDefinition(
            kind,
            size,
            _positive(value["second_size_mm"], "second size"),
            None,
            flip,
        )
    raw_angle = value["angle_degrees"]
    if isinstance(raw_angle, bool):
        raise NativeModelError("Chamfer angle must be a number.")
    try:
        angle = float(raw_angle)
    except (TypeError, ValueError) as exc:
        raise NativeModelError("Chamfer angle must be a number.") from exc
    if not math.isfinite(angle) or not 0.0 < angle < 180.0:
        raise NativeModelError(
            "Chamfer angle must be finite, greater than 0, and less than 180 degrees."
        )
    return DesignChamferDefinition(kind, size, None, angle, flip)


def prepare_design_chamfer(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignChamferSpec:
    selection = prepare_dressup_selection(
        document_uid,
        values.get("selection"),
        operation="Chamfer",
    )
    return DesignChamferSpec(
        selection.targets,
        selection.use_all_edges,
        _definition(values.get("definition")),
    )


def preflight_design_chamfer(
    document: Any,
    spec: DesignChamferSpec,
) -> tuple[Any, ...]:
    return preflight_dressup_selection(
        document,
        DesignDressupSelection(spec.targets, spec.use_all_edges),
        operation="Chamfer",
    )


def _quantity_value(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _verify_chamfer(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    spec: DesignChamferSpec = expected["spec"]
    definition = spec.definition
    offsets, elements = dressup_target_elements(spec.targets)
    actual_type = str(operation.ChamferType)
    size = _quantity_value(operation.Size)
    second_size = _quantity_value(operation.Size2)
    angle = _quantity_value(operation.Angle)
    base = getattr(operation, "Base", None)
    linked_base = base[0] if isinstance(base, tuple) and base else base
    output_shapes = list(operation.OutputShapes)
    values_match = math.isclose(
        size,
        definition.size_mm,
        rel_tol=1.0e-9,
        abs_tol=1.0e-7,
    )
    if definition.second_size_mm is not None:
        values_match = values_match and math.isclose(
            second_size,
            definition.second_size_mm,
            rel_tol=1.0e-9,
            abs_tol=1.0e-7,
        )
    if definition.angle_degrees is not None:
        values_match = values_match and math.isclose(
            angle,
            definition.angle_degrees,
            rel_tol=1.0e-9,
            abs_tol=1.0e-7,
        )
    if (
        actual_type != _CHAMFER_TYPES[definition.kind]
        or not values_match
        or bool(operation.FlipDirection) is not definition.flip_direction
        or bool(operation.UseAllEdges) is not spec.use_all_edges
        or list(operation.TargetElementOffsets) != offsets
        or list(operation.TargetElements) != elements
        or linked_base is not None
        or operation.BaseFeature is not None
        or list(operation.InputBodyIds) != list(operation.OutputBodyIds)
        or list(operation.OutputPreviousInputIndices) != list(range(len(spec.targets)))
        or len(operation.InputStates) != len(spec.targets)
        or len(output_shapes) != len(spec.targets)
    ):
        raise NativeModelError("The Design Chamfer controls changed before commit.")
    if any(
        shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
        for shape in output_shapes
    ):
        raise NativeModelError("The Design Chamfer produced an invalid Body result.")
    result: dict[str, Any] = {
        "definition": definition.kind,
        "size_mm": size,
        "flip_direction": definition.flip_direction,
        "selection_mode": "all_edges" if spec.use_all_edges else "explicit",
        "target_count": len(spec.targets),
        "selected_reference_count": len(elements),
    }
    if definition.second_size_mm is not None:
        result["second_size_mm"] = second_size
    if definition.angle_degrees is not None:
        result["angle_degrees"] = angle
    return result


def create_design_chamfer(
    document: Any,
    *,
    label: str,
    spec: DesignChamferSpec,
) -> NativeMutationDraft:
    offsets, elements = dressup_target_elements(spec.targets)
    result_spec = DesignResultSpec(
        "modify",
        tuple(target.body for target in spec.targets),
        None,
    )

    def configure(operation: Any) -> Mapping[str, Any]:
        definition = spec.definition
        operation.ChamferType = _CHAMFER_TYPES[definition.kind]
        operation.Size = definition.size_mm
        if definition.second_size_mm is not None:
            operation.Size2 = definition.second_size_mm
        if definition.angle_degrees is not None:
            operation.Angle = definition.angle_degrees
        operation.FlipDirection = definition.flip_direction
        operation.UseAllEdges = spec.use_all_edges
        operation.TargetElementOffsets = offsets
        operation.TargetElements = elements
        return {"spec": spec}

    return create_design_operation(
        document,
        type_id="PartDesign::DesignChamfer",
        base_name="Chamfer",
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=_verify_chamfer,
        configure_after_targets=True,
    )
