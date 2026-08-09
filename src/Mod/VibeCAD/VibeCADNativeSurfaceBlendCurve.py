# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained Surface Blend Curve preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    close_number,
    current_part_element_is_exact,
    link_sub,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"start", "end"})
_ENDPOINT_FIELDS = frozenset(
    {"object_name", "edge", "parameter", "continuity", "size"}
)
_REQUIRED_ENDPOINT_FIELDS = frozenset({"object_name", "edge"})
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_CONTINUITIES = {"C0": 0, "G1": 1, "G2": 2, "G3": 3, "G4": 4}


@dataclass(frozen=True, slots=True)
class SurfaceBlendEndpoint:
    object_ref: NativeObjectRef
    edge: str
    parameter: float
    continuity: str
    size: float


@dataclass(frozen=True, slots=True)
class SurfaceBlendCurveSpec:
    start: SurfaceBlendEndpoint
    end: SurfaceBlendEndpoint


@dataclass(frozen=True, slots=True)
class PreparedSurfaceBlendEndpoint:
    spec: SurfaceBlendEndpoint
    element: CurrentPartElement


@dataclass(frozen=True, slots=True)
class PreparedSurfaceBlendCurve:
    spec: SurfaceBlendCurveSpec
    start: PreparedSurfaceBlendEndpoint
    end: PreparedSurfaceBlendEndpoint


def _number(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise NativeModelError(f"Blend Curve {name} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise NativeModelError(f"Blend Curve {name} is outside its finite range.")
    return number


def _endpoint(document_uid: str, value: Any, *, name: str) -> SurfaceBlendEndpoint:
    if (
        not isinstance(value, Mapping)
        or not _REQUIRED_ENDPOINT_FIELDS <= set(value)
        or not set(value) <= _ENDPOINT_FIELDS
    ):
        raise NativeModelError(
            f"Blend Curve {name} requires one exact edge and known controls."
        )
    edge = str(value["edge"] or "")
    if _EDGE_NAME.fullmatch(edge) is None:
        raise NativeModelError(f"Blend Curve {name} requires exact EdgeN.")
    continuity = str(value.get("continuity", "G2") or "")
    if continuity not in _CONTINUITIES:
        raise NativeModelError(
            f"Blend Curve {name} continuity must be C0, G1, G2, G3, or G4."
        )
    return SurfaceBlendEndpoint(
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
        edge,
        _number(
            value.get("parameter", 0.0),
            name=f"{name} parameter",
            minimum=0.0,
            maximum=1.0,
        ),
        continuity,
        _number(
            value.get("size", 1.0),
            name=f"{name} size",
            minimum=-100.0,
            maximum=100.0,
        ),
    )


def prepare_surface_blend_curve(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceBlendCurveSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("Blend Curve requires exactly one start and one end.")
    spec = SurfaceBlendCurveSpec(
        _endpoint(document_uid, value["start"], name="start"),
        _endpoint(document_uid, value["end"], name="end"),
    )
    if (
        spec.start.object_ref.object_name,
        spec.start.edge,
    ) == (
        spec.end.object_ref.object_name,
        spec.end.edge,
    ):
        raise NativeModelError("Blend Curve start and end edges must be distinct.")
    return spec


def _preflight_endpoint(
    document: Any,
    endpoint: SurfaceBlendEndpoint,
    *,
    name: str,
) -> PreparedSurfaceBlendEndpoint:
    element = resolve_current_part_element(
        document,
        endpoint.object_ref,
        subelement=endpoint.edge,
        operation=f"Blend Curve {name} edge",
    )
    derived = getattr(element.target, "isDerivedFrom", None)
    if (
        str(element.shape.ShapeType) != "Edge"
        or not callable(derived)
        or not derived("Part::Feature")
    ):
        raise NativeModelError(f"Blend Curve {name} must be one exact Part edge.")
    return PreparedSurfaceBlendEndpoint(endpoint, element)


def preflight_surface_blend_curve(
    document: Any,
    spec: SurfaceBlendCurveSpec,
) -> PreparedSurfaceBlendCurve:
    if not isinstance(spec, SurfaceBlendCurveSpec):
        raise TypeError("spec must be a SurfaceBlendCurveSpec")
    start = _preflight_endpoint(document, spec.start, name="start")
    end = _preflight_endpoint(document, spec.end, name="end")
    if (start.element.target, start.element.subelement) == (
        end.element.target,
        end.element.subelement,
    ):
        raise NativeModelError(
            "Blend Curve inputs resolve to the same current-History edge."
        )
    return PreparedSurfaceBlendCurve(spec, start, end)


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceBlendCurve) -> bool:
    return current_part_element_is_exact(
        document,
        prepared.start.element,
    ) and current_part_element_is_exact(document, prepared.end.element)


def _expected_link(endpoint: PreparedSurfaceBlendEndpoint):
    return endpoint.element.target, (endpoint.spec.edge,)


def _controls_match(result: Any, spec: SurfaceBlendCurveSpec) -> bool:
    return (
        close_number(result.StartParameter, spec.start.parameter)
        and int(result.StartContinuity) == _CONTINUITIES[spec.start.continuity]
        and close_number(result.StartSize, spec.start.size)
        and close_number(result.EndParameter, spec.end.parameter)
        and int(result.EndContinuity) == _CONTINUITIES[spec.end.continuity]
        and close_number(result.EndSize, spec.end.size)
    )


def _curve_degree(shape: Any) -> int:
    try:
        return int(shape.Edges[0].Curve.Degree)
    except Exception as exc:
        raise NativeModelError("Blend Curve did not retain a Bezier curve.") from exc


def create_surface_blend_curve(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceBlendCurve,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedSurfaceBlendCurve):
        raise TypeError("prepared must be a PreparedSurfaceBlendCurve")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("A Blend Curve edge changed after preflight.")
    spec = prepared.spec
    result = document.addObject("Surface::FeatureBlendCurve", "BlendCurve")
    if (
        result is None
        or str(getattr(result, "TypeId", "")) != "Surface::FeatureBlendCurve"
    ):
        raise NativeModelError("The Blend Curve factory returned the wrong type.")
    result.Label = label
    result.StartEdge = (_expected_link(prepared.start)[0], [spec.start.edge])
    result.EndEdge = (_expected_link(prepared.end)[0], [spec.end.edge])
    result.StartParameter = spec.start.parameter
    result.StartContinuity = _CONTINUITIES[spec.start.continuity]
    result.StartSize = spec.start.size
    result.EndParameter = spec.end.parameter
    result.EndContinuity = _CONTINUITIES[spec.end.continuity]
    result.EndSize = spec.end.size
    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Edge"
        or len(shape.Edges) != 1
        or _curve_degree(shape)
        != _CONTINUITIES[spec.start.continuity]
        + _CONTINUITIES[spec.end.continuity]
        + 1
    ):
        status = str(result.getStatusString() or "")
        raise NativeModelError(
            status if status and status != "Valid" else "Blend Curve produced no valid edge."
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def _endpoint_result(endpoint: PreparedSurfaceBlendEndpoint) -> dict[str, Any]:
    return {
        "source": object_reference(_expected_link(endpoint)[0]),
        "edge": endpoint.spec.edge,
        "parameter": endpoint.spec.parameter,
        "continuity": endpoint.spec.continuity,
        "size": endpoint.spec.size,
    }


def verify_surface_blend_curve(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedSurfaceBlendCurve = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    expected_degree = (
        _CONTINUITIES[prepared.spec.start.continuity]
        + _CONTINUITIES[prepared.spec.end.continuity]
        + 1
    )
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Surface::FeatureBlendCurve"
        or str(result.Label) != draft.value["label"]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Edge"
        or len(shape.Edges) != 1
        or _curve_degree(shape) != expected_degree
        or link_sub(result.StartEdge) != _expected_link(prepared.start)
        or link_sub(result.EndEdge) != _expected_link(prepared.end)
        or not _controls_match(result, prepared.spec)
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError("Blend Curve failed its retained postcondition.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
    ):
        raise NativeModelError("Blend Curve lost its History or Design identity.")
    PartDesign.validateDesign(result)
    return {
        "root": object_reference(result),
        "start": _endpoint_result(prepared.start),
        "end": _endpoint_result(prepared.end),
        "degree": expected_degree,
        "length_mm": float(shape.Length),
    }
