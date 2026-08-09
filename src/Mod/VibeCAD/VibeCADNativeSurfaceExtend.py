# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Surface Extend Face preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
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
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_FIELDS = frozenset(
    {
        "object_name",
        "face",
        "u_negative",
        "u_positive",
        "u_symmetric",
        "v_negative",
        "v_positive",
        "v_symmetric",
        "tolerance",
        "samples_u",
        "samples_v",
    }
)
_REQUIRED_FIELDS = frozenset({"object_name", "face"})
_FACE_NAME = re.compile(r"^Face[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class SurfaceExtendSpec:
    object_ref: NativeObjectRef
    face: str
    u_negative: float
    u_positive: float
    u_symmetric: bool
    v_negative: float
    v_positive: float
    v_symmetric: bool
    tolerance: float
    samples_u: int
    samples_v: int


@dataclass(frozen=True, slots=True)
class PreparedSurfaceExtend:
    spec: SurfaceExtendSpec
    element: CurrentPartElement


def _number(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in (int, float):
        raise NativeModelError(f"Surface Extend {name} must be numeric.")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise NativeModelError(f"Surface Extend {name} is outside its finite range.")
    return number


def _samples(value: Any, *, name: str) -> int:
    if type(value) is not int or not 2 <= value <= 512:
        raise NativeModelError(f"Surface Extend {name} must be an integer from 2 to 512.")
    return value


def _boolean(value: Any, *, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Surface Extend {name} must be boolean.")
    return value


def prepare_surface_extend(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceExtendSpec:
    if (
        not isinstance(value, Mapping)
        or not _REQUIRED_FIELDS <= set(value)
        or not set(value) <= _FIELDS
    ):
        raise NativeModelError("Surface Extend requires one exact face and known controls.")
    face = str(value["face"] or "")
    if _FACE_NAME.fullmatch(face) is None:
        raise NativeModelError("Surface Extend requires exact FaceN.")
    u_negative = _number(
        value.get("u_negative", 0.05),
        name="u_negative",
        minimum=-0.5,
        maximum=10.0,
    )
    u_positive = _number(
        value.get("u_positive", 0.05),
        name="u_positive",
        minimum=-0.5,
        maximum=10.0,
    )
    v_negative = _number(
        value.get("v_negative", 0.05),
        name="v_negative",
        minimum=-0.5,
        maximum=10.0,
    )
    v_positive = _number(
        value.get("v_positive", 0.05),
        name="v_positive",
        minimum=-0.5,
        maximum=10.0,
    )
    u_symmetric = _boolean(value.get("u_symmetric", True), name="u_symmetric")
    v_symmetric = _boolean(value.get("v_symmetric", True), name="v_symmetric")
    if u_symmetric and u_negative != u_positive:
        raise NativeModelError(
            "Symmetric Surface Extend U values must be equal."
        )
    if v_symmetric and v_negative != v_positive:
        raise NativeModelError(
            "Symmetric Surface Extend V values must be equal."
        )
    return SurfaceExtendSpec(
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
        face,
        u_negative,
        u_positive,
        u_symmetric,
        v_negative,
        v_positive,
        v_symmetric,
        _number(
            value.get("tolerance", 0.1),
            name="tolerance",
            minimum=0.0,
            maximum=10.0,
        ),
        _samples(value.get("samples_u", 32), name="samples_u"),
        _samples(value.get("samples_v", 32), name="samples_v"),
    )


def preflight_surface_extend(
    document: Any,
    spec: SurfaceExtendSpec,
) -> PreparedSurfaceExtend:
    if not isinstance(spec, SurfaceExtendSpec):
        raise TypeError("spec must be a SurfaceExtendSpec")
    element = resolve_current_part_element(
        document,
        spec.object_ref,
        subelement=spec.face,
        operation="Surface Extend face",
    )
    derived = getattr(element.target, "isDerivedFrom", None)
    if (
        str(element.shape.ShapeType) != "Face"
        or not callable(derived)
        or not derived("Part::Feature")
    ):
        raise NativeModelError("Surface Extend requires one exact Part face.")
    return PreparedSurfaceExtend(spec, element)


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceExtend) -> bool:
    return current_part_element_is_exact(document, prepared.element)


def _expected_link(prepared: PreparedSurfaceExtend):
    return prepared.element.target, (prepared.spec.face,)


def _controls_match(result: Any, spec: SurfaceExtendSpec) -> bool:
    return (
        float(result.ExtendUNeg) == spec.u_negative
        and float(result.ExtendUPos) == spec.u_positive
        and bool(result.ExtendUSymetric) is spec.u_symmetric
        and float(result.ExtendVNeg) == spec.v_negative
        and float(result.ExtendVPos) == spec.v_positive
        and bool(result.ExtendVSymetric) is spec.v_symmetric
        and float(result.Tolerance) == spec.tolerance
        and int(result.SampleU) == spec.samples_u
        and int(result.SampleV) == spec.samples_v
    )


def create_surface_extend(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceExtend,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedSurfaceExtend):
        raise TypeError("prepared must be a PreparedSurfaceExtend")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("The Surface Extend face changed after preflight.")
    spec = prepared.spec
    result = document.addObject("Surface::Extend", "Surface")
    if result is None or str(getattr(result, "TypeId", "")) != "Surface::Extend":
        raise NativeModelError("The Surface Extend factory returned the wrong type.")
    result.Label = label
    result.ExtendUSymetric = False
    result.ExtendVSymetric = False
    result.ExtendUNeg = spec.u_negative
    result.ExtendUPos = spec.u_positive
    result.ExtendVNeg = spec.v_negative
    result.ExtendVPos = spec.v_positive
    result.ExtendUSymetric = spec.u_symmetric
    result.ExtendVSymetric = spec.v_symmetric
    result.Tolerance = spec.tolerance
    result.SampleU = spec.samples_u
    result.SampleV = spec.samples_v
    result.Face = (prepared.element.target, [spec.face])
    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
    ):
        status = str(result.getStatusString() or "")
        raise NativeModelError(
            status if status and status != "Valid" else "Surface Extend produced no valid face."
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_surface_extend(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedSurfaceExtend = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Surface::Extend"
        or str(result.Label) != draft.value["label"]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
        or link_sub(result.Face) != _expected_link(prepared)
        or not _controls_match(result, prepared.spec)
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError("Surface Extend failed its retained postcondition.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
    ):
        raise NativeModelError("Surface Extend lost its History or Design identity.")
    PartDesign.validateDesign(result)
    spec = prepared.spec
    return {
        "root": object_reference(result),
        "source": object_reference(prepared.element.target),
        "face": spec.face,
        "u_extension": [spec.u_negative, spec.u_positive, spec.u_symmetric],
        "v_extension": [spec.v_negative, spec.v_positive, spec.v_symmetric],
        "sample_grid": [spec.samples_u, spec.samples_v],
        "tolerance": spec.tolerance,
        "area_mm2": float(shape.Area),
    }
