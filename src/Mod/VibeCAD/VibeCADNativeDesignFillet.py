# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, preflight, execution, and verification for Design Fillet."""

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
from VibeCADNativeDesignResults import (
    DesignResultSpec,
    create_design_operation,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


@dataclass(frozen=True, slots=True)
class DesignFilletSpec:
    targets: tuple[DesignDressupTarget, ...]
    use_all_edges: bool
    radius_mm: float


def prepare_design_fillet(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignFilletSpec:
    selection = prepare_dressup_selection(
        document_uid,
        values.get("selection"),
        operation="Fillet",
    )
    raw_radius = values.get("radius_mm", 0.0)
    if isinstance(raw_radius, bool):
        raise NativeModelError("A Fillet radius must be a number.")
    radius = float(raw_radius)
    if not math.isfinite(radius) or not 0.0 < radius <= 1_000_000.0:
        raise NativeModelError("A Fillet radius must be finite and greater than zero.")
    return DesignFilletSpec(selection.targets, selection.use_all_edges, radius)


def preflight_design_fillet(document: Any, spec: DesignFilletSpec) -> tuple[Any, ...]:
    return preflight_dressup_selection(
        document,
        DesignDressupSelection(spec.targets, spec.use_all_edges),
        operation="Fillet",
    )


def _verify_fillet(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    spec: DesignFilletSpec = expected["spec"]
    offsets, elements = dressup_target_elements(spec.targets)
    radius = float(getattr(operation.Radius, "Value", operation.Radius))
    base = getattr(operation, "Base", None)
    linked_base = base[0] if isinstance(base, tuple) and base else base
    output_shapes = list(operation.OutputShapes)
    if (
        not math.isclose(radius, spec.radius_mm, rel_tol=1.0e-9, abs_tol=1.0e-7)
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
        raise NativeModelError("The Design Fillet controls changed before commit.")
    if any(
        shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
        for shape in output_shapes
    ):
        raise NativeModelError("The Design Fillet produced an invalid Body result.")
    return {
        "radius_mm": radius,
        "selection_mode": "all_edges" if spec.use_all_edges else "explicit",
        "target_count": len(spec.targets),
        "selected_reference_count": len(elements),
    }


def create_design_fillet(
    document: Any,
    *,
    label: str,
    spec: DesignFilletSpec,
) -> NativeMutationDraft:
    offsets, elements = dressup_target_elements(spec.targets)
    result_spec = DesignResultSpec(
        "modify",
        tuple(target.body for target in spec.targets),
        None,
    )

    def configure(operation: Any) -> Mapping[str, Any]:
        operation.Radius = spec.radius_mm
        operation.UseAllEdges = spec.use_all_edges
        operation.TargetElementOffsets = offsets
        operation.TargetElements = elements
        return {"spec": spec}

    return create_design_operation(
        document,
        type_id="PartDesign::DesignFillet",
        base_name="Fillet",
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=_verify_fillet,
        configure_after_targets=True,
    )
