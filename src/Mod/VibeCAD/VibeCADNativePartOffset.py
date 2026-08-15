# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained standalone Part 3D Offset creation and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    close_number,
    copy_part_visual,
    current_part_element_is_exact,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS_3D = frozenset(
    {
        "source",
        "value_mm",
        "mode",
        "join",
        "intersection",
        "self_intersection",
        "fill",
    }
)
_DEFINITION_FIELDS_2D = _DEFINITION_FIELDS_3D - {"self_intersection"}
_MODES = {
    "skin": "Skin",
    "pipe": "Pipe",
    "recto_verso": "RectoVerso",
}
_JOINS = {
    "arc": "Arc",
    "tangent": "Tangent",
    "intersection": "Intersection",
}
_MAX_OFFSET = 1_000_000.0


@dataclass(frozen=True, slots=True)
class PartOffsetSpec:
    source_ref: NativeObjectRef
    value: float
    mode: str
    join: str
    intersection: bool
    self_intersection: bool
    fill: bool
    two_dimensional: bool


@dataclass(frozen=True, slots=True)
class PreparedPartOffset:
    spec: PartOffsetSpec
    source: CurrentPartElement
    presentation: Any
    presentation_was_visible: bool


def _offset_name(two_dimensional: bool) -> str:
    return "Part 2D Offset" if two_dimensional else "Part 3D Offset"


def _finite_offset(value: Any, operation: str) -> float:
    if isinstance(value, bool):
        raise NativeModelError(f"{operation} value must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"{operation} value must be a number.") from exc
    if not math.isfinite(number) or abs(number) > _MAX_OFFSET:
        raise NativeModelError(f"{operation} value is outside its finite range.")
    return number


def _choice(value: Any, choices: Mapping[str, str], name: str, operation: str) -> str:
    choice = str(value or "")
    if choice not in choices:
        allowed = ", ".join(choices)
        raise NativeModelError(f"{operation} {name} must be one of: {allowed}.")
    return choice


def _boolean(value: Any, name: str, operation: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"{operation} {name} must be true or false.")
    return value


def _prepare_part_offset(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    two_dimensional: bool,
) -> PartOffsetSpec:
    operation = _offset_name(two_dimensional)
    expected_fields = _DEFINITION_FIELDS_2D if two_dimensional else _DEFINITION_FIELDS_3D
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise NativeModelError(
            f"A {operation} definition must contain its exact controls."
        )
    source = value["source"]
    if not isinstance(source, Mapping) or set(source) != {"object_name"}:
        raise NativeModelError(f"A {operation} source target is invalid.")
    mode = _choice(value["mode"], _MODES, "mode", operation)
    if two_dimensional and mode == "recto_verso":
        raise NativeModelError("Part 2D Offset mode must be skin or pipe.")
    return PartOffsetSpec(
        source_ref=NativeObjectRef(
            document_uid,
            str(source["object_name"] or ""),
        ),
        value=_finite_offset(value["value_mm"], operation),
        mode=mode,
        join=_choice(value["join"], _JOINS, "join", operation),
        intersection=_boolean(value["intersection"], "intersection", operation),
        self_intersection=(
            False
            if two_dimensional
            else _boolean(value["self_intersection"], "self_intersection", operation)
        ),
        fill=_boolean(value["fill"], "fill", operation),
        two_dimensional=two_dimensional,
    )


def prepare_part_offset(document_uid: str, value: Mapping[str, Any]) -> PartOffsetSpec:
    return _prepare_part_offset(document_uid, value, two_dimensional=False)


def prepare_part_offset_2d(document_uid: str, value: Mapping[str, Any]) -> PartOffsetSpec:
    return _prepare_part_offset(document_uid, value, two_dimensional=True)


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_offset(document: Any, spec: PartOffsetSpec) -> PreparedPartOffset:
    import PartGui

    if not isinstance(spec, PartOffsetSpec):
        raise TypeError("spec must be a PartOffsetSpec")
    source = resolve_current_part_element(
        document,
        spec.source_ref,
        subelement=None,
        operation=f"{_offset_name(spec.two_dimensional)} source",
    )
    if spec.two_dimensional:
        shape = source.shape
        try:
            planar = not shape.Solids and shape.findPlane() is not None
        except Exception:
            planar = False
        if not planar:
            raise NativeModelError(
                "A Part 2D Offset source must be planar and contain no solid."
            )
    presentation = PartGui.resolveModelingPresentationObject(source.target) or source.target
    return PreparedPartOffset(spec, source, presentation, _visible(presentation))


def _create_part_offset(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartOffset,
) -> NativeMutationDraft:
    import PartGui

    if not current_part_element_is_exact(document, prepared.source):
        raise NativeModelError(
            f"The {_offset_name(prepared.spec.two_dimensional)} source changed "
            "after preflight."
        )
    spec = prepared.spec
    type_id = "Part::Offset2D" if spec.two_dimensional else "Part::Offset"
    result = document.addObject(type_id, "Offset2D" if spec.two_dimensional else "Offset")
    if result is None or str(getattr(result, "TypeId", "")) != type_id:
        raise NativeModelError(
            f"The Part {'2D' if spec.two_dimensional else '3D'} Offset factory "
            "returned the wrong type."
        )
    result.Label = label
    result.Source = prepared.source.target
    result.Value = spec.value
    result.Mode = _MODES[spec.mode]
    result.Join = _JOINS[spec.join]
    result.Intersection = spec.intersection
    result.SelfIntersection = spec.self_intersection
    result.Fill = spec.fill

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
    ):
        raise NativeModelError(
            str(
                result.getStatusString()
                or f"{_offset_name(spec.two_dimensional)} did not produce valid geometry."
            )
        )
    copy_part_visual(prepared.source.target, result)
    PartGui.publishDesignDefinitionBlock((result,))
    replaced = (prepared.presentation,) if prepared.presentation_was_visible else ()
    if replaced:
        if not PartGui.setModelingReplacedInputs(result, replaced):
            raise NativeModelError(
                f"{_offset_name(spec.two_dimensional)} could not retain its replaced input."
            )
        prepared.presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "label": label,
            "prepared": prepared,
            "result": result,
            "replaced": replaced,
        },
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def create_part_offset(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartOffset,
) -> NativeMutationDraft:
    if prepared.spec.two_dimensional:
        raise TypeError("A 3D Offset creation requires a 3D specification")
    return _create_part_offset(document, label=label, prepared=prepared)


def create_part_offset_2d(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartOffset,
) -> NativeMutationDraft:
    if not prepared.spec.two_dimensional:
        raise TypeError("A 2D Offset creation requires a 2D specification")
    return _create_part_offset(document, label=label, prepared=prepared)


def _verify_part_offset(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    type_id = "Part::Offset2D" if spec.two_dimensional else "Part::Offset"
    operation = _offset_name(spec.two_dimensional)
    if document.getObject(result.Name) is not result or result.TypeId != type_id:
        raise NativeModelError(f"The {operation} result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError(f"The {operation} result changed its label.")
    if (
        result.Source is not prepared.source.target
        or not close_number(result.Value, spec.value)
        or str(result.Mode) != _MODES[spec.mode]
        or str(result.Join) != _JOINS[spec.join]
        or bool(result.Intersection) is not spec.intersection
        or bool(result.SelfIntersection) is not spec.self_intersection
        or bool(result.Fill) is not spec.fill
    ):
        raise NativeModelError(f"The {operation} result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError(f"The {operation} result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != draft.value["replaced"]
    ):
        raise NativeModelError(f"The {operation} Design identity is invalid.")
    if not current_part_element_is_exact(document, prepared.source):
        raise NativeModelError(f"The {operation} source changed before commit.")
    expected_visibility = False if draft.value["replaced"] else prepared.presentation_was_visible
    if _visible(prepared.presentation) is not expected_visibility:
        raise NativeModelError(f"The {operation} source visibility changed unexpectedly.")

    return {
        "root": object_reference(result),
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "face_count": len(shape.Faces),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }


def verify_part_offset(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    if draft.value["prepared"].spec.two_dimensional:
        raise TypeError("A 3D Offset verifier requires a 3D specification")
    return _verify_part_offset(document, draft)


def verify_part_offset_2d(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    if not draft.value["prepared"].spec.two_dimensional:
        raise TypeError("A 2D Offset verifier requires a 2D specification")
    return _verify_part_offset(document, draft)
