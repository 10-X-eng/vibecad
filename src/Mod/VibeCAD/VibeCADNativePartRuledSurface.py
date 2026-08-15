# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Ruled Surface preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    link_sub,
    resolve_current_part_element,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
)


_DEFINITION_FIELDS = frozenset({"curves"})
_CURVE_NAME = re.compile(r"^(?:Edge|Wire)[1-9][0-9]*$")
_CURVE_TYPES = frozenset({"Edge", "Wire"})
_ORIENTATION = "Automatic"


@dataclass(frozen=True, slots=True)
class PartRuledCurveSpec:
    object_ref: NativeObjectRef
    subelement: str | None


@dataclass(frozen=True, slots=True)
class PartRuledSurfaceSpec:
    curves: tuple[PartRuledCurveSpec, PartRuledCurveSpec]


@dataclass(frozen=True, slots=True)
class PreparedPartRuledSurface:
    spec: PartRuledSurfaceSpec
    curves: tuple[CurrentPartElement, CurrentPartElement]


def _curve_spec(document_uid: str, value: Any) -> PartRuledCurveSpec:
    if not isinstance(value, Mapping) or set(value) not in (
        {"object_name"},
        {"object_name", "subelement"},
    ):
        raise NativeModelError("A Ruled Surface curve target is invalid.")
    subelement = str(value.get("subelement") or "") or None
    if subelement is not None and _CURVE_NAME.fullmatch(subelement) is None:
        raise NativeModelError("A Ruled Surface subelement must be an exact EdgeN or WireN.")
    return PartRuledCurveSpec(
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
        subelement,
    )


def prepare_part_ruled_surface(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartRuledSurfaceSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Ruled Surface definition must contain its exact curves.")
    curves = value["curves"]
    if not isinstance(curves, list) or len(curves) != 2:
        raise NativeModelError("Ruled Surface requires exactly two curve targets.")
    specs = tuple(_curve_spec(document_uid, curve) for curve in curves)
    if specs[0] == specs[1]:
        raise NativeModelError("Ruled Surface curve targets must be distinct.")
    return PartRuledSurfaceSpec(specs)


def preflight_part_ruled_surface(
    document: Any,
    spec: PartRuledSurfaceSpec,
) -> PreparedPartRuledSurface:
    if not isinstance(spec, PartRuledSurfaceSpec):
        raise TypeError("spec must be a PartRuledSurfaceSpec")
    curves = tuple(
        resolve_current_part_element(
            document,
            curve.object_ref,
            subelement=curve.subelement,
            operation="Ruled Surface curve",
        )
        for curve in spec.curves
    )
    if any(str(curve.shape.ShapeType) not in _CURVE_TYPES for curve in curves):
        raise NativeModelError("Each Ruled Surface target must resolve to one edge or wire.")
    if (
        curves[0].target is curves[1].target
        and curves[0].subelement == curves[1].subelement
    ):
        raise NativeModelError("Ruled Surface curves resolve to duplicate geometry.")
    return PreparedPartRuledSurface(spec, curves)


def _curve_link(curve: CurrentPartElement) -> Any:
    return curve.target, [curve.subelement or ""]


def create_part_ruled_surface(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartRuledSurface,
) -> NativeMutationDraft:
    import PartGui

    if any(
        not current_part_element_is_exact(document, curve)
        for curve in prepared.curves
    ):
        raise NativeModelError("A Ruled Surface curve changed after preflight.")

    result = document.addObject("Part::RuledSurface", "RuledSurface")
    if result is None or str(getattr(result, "TypeId", "")) != "Part::RuledSurface":
        raise NativeModelError("The Ruled Surface factory returned the wrong object type.")
    result.Label = label
    result.Curve1 = _curve_link(prepared.curves[0])
    result.Curve2 = _curve_link(prepared.curves[1])
    result.Orientation = _ORIENTATION

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or tuple(shape.Solids)
    ):
        message = str(
            result.getStatusString()
            or "Ruled Surface did not produce valid surface geometry."
        )
        raise NativeModelError(message)

    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_ruled_surface(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    expected_links = tuple(
        (
            curve.target,
            (curve.subelement or "",),
        )
        for curve in prepared.curves
    )
    if document.getObject(result.Name) is not result or result.TypeId != "Part::RuledSurface":
        raise NativeModelError("The Ruled Surface result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Ruled Surface result changed its label.")
    if (
        (link_sub(result.Curve1), link_sub(result.Curve2)) != expected_links
        or str(result.Orientation) != _ORIENTATION
    ):
        raise NativeModelError("The Ruled Surface result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or tuple(shape.Solids)
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Ruled Surface result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or "VibeCADTimelineReplacedInputs" in result.PropertiesList
    ):
        raise NativeModelError("The source-preserving Ruled Surface identity is invalid.")
    for index, curve in enumerate(prepared.curves):
        if not current_part_element_is_exact(document, curve):
            raise NativeModelError(f"Ruled Surface curve {index + 1} changed before commit.")

    return {
        "root": object_reference(result),
        "curve_types": [str(curve.shape.ShapeType) for curve in prepared.curves],
        "shape_type": str(shape.ShapeType),
        "face_count": len(shape.Faces),
        "area_mm2": float(shape.Area),
    }
