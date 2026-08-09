# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained projection of Part geometry onto one support face."""

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
    flatten_link_sub_list,
    link_sub,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_FIELDS = frozenset(
    {"target", "sources", "mode", "height_mm", "offset_mm", "direction_xyz"}
)
_REFERENCE_FIELDS = frozenset({"object_name", "subelement"})
_FACE = re.compile(r"^Face[1-9][0-9]*$")
_SOURCE = re.compile(r"^(?:Edge|Wire|Face)[1-9][0-9]*$")
_MODES = {"all": "All", "faces": "Faces", "edges": "Edges"}
_MAX_SOURCES = 64


@dataclass(frozen=True, slots=True)
class PartProjectionReference:
    object_ref: NativeObjectRef
    subelement: str


@dataclass(frozen=True, slots=True)
class PartProjectionSpec:
    target: PartProjectionReference
    sources: tuple[PartProjectionReference, ...]
    mode: str
    height: float
    offset: float
    direction: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class PreparedPartProjection:
    spec: PartProjectionSpec
    target: CurrentPartElement
    sources: tuple[CurrentPartElement, ...]
    presentations: tuple[tuple[Any, bool], ...]


def _reference(
    document_uid: str,
    value: Any,
    *,
    pattern: re.Pattern[str],
    role: str,
) -> PartProjectionReference:
    if not isinstance(value, Mapping) or set(value) != _REFERENCE_FIELDS:
        raise NativeModelError(f"A Part projection {role} reference is invalid.")
    subelement = str(value["subelement"] or "")
    if pattern.fullmatch(subelement) is None:
        raise NativeModelError(f"A Part projection {role} subelement is invalid.")
    return PartProjectionReference(
        NativeObjectRef(document_uid, str(value["object_name"] or "")),
        subelement,
    )


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise NativeModelError(f"Part projection {name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Part projection {name} must be a number.") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise NativeModelError(f"Part projection {name} is outside its finite range.")
    return number


def _direction(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise NativeModelError("Part projection direction_xyz must contain x, y, z.")
    vector = tuple(_number(item, "direction component", -1.0, 1.0) for item in value)
    length = math.sqrt(sum(item * item for item in vector))
    if length <= 1.0e-12:
        raise NativeModelError("Part projection direction must be non-zero.")
    return tuple(item / length for item in vector)


def prepare_part_projection(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartProjectionSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeModelError(
            "A Part projection definition must contain its exact controls."
        )
    raw_sources = value["sources"]
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= _MAX_SOURCES:
        raise NativeModelError("Part projection requires 1 to 64 explicit sources.")
    sources = tuple(
        _reference(
            document_uid,
            item,
            pattern=_SOURCE,
            role="source",
        )
        for item in raw_sources
    )
    source_keys = tuple(
        (item.object_ref.object_name, item.subelement) for item in sources
    )
    if len(source_keys) != len(set(source_keys)):
        raise NativeModelError("Part projection sources must be distinct.")
    mode = str(value["mode"] or "")
    if mode not in _MODES:
        raise NativeModelError("Part projection mode must be all, faces, or edges.")
    return PartProjectionSpec(
        target=_reference(
            document_uid,
            value["target"],
            pattern=_FACE,
            role="target",
        ),
        sources=sources,
        mode=mode,
        height=_number(value["height_mm"], "height", 0.0, 999.0),
        offset=_number(value["offset_mm"], "offset", -999.0, 999.0),
        direction=_direction(value["direction_xyz"]),
    )


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_projection(
    document: Any,
    spec: PartProjectionSpec,
) -> PreparedPartProjection:
    import PartGui

    if not isinstance(spec, PartProjectionSpec):
        raise TypeError("spec must be a PartProjectionSpec")
    target = resolve_current_part_element(
        document,
        spec.target.object_ref,
        subelement=spec.target.subelement,
        operation="Part projection target",
    )
    if str(target.shape.ShapeType) != "Face":
        raise NativeModelError("A Part projection target must resolve to one face.")
    sources = tuple(
        resolve_current_part_element(
            document,
            item.object_ref,
            subelement=item.subelement,
            operation="Part projection source",
        )
        for item in spec.sources
    )
    if any(str(source.shape.ShapeType) not in {"Edge", "Wire", "Face"} for source in sources):
        raise NativeModelError(
            "Part projection sources must resolve to edges, wires, or faces."
        )
    presentations = []
    for element in (target, *sources):
        presentation = PartGui.resolveModelingPresentationObject(element.target) or element.target
        if all(existing[0] is not presentation for existing in presentations):
            presentations.append((presentation, _visible(presentation)))
    return PreparedPartProjection(spec, target, sources, tuple(presentations))


def _inputs_are_exact(document: Any, prepared: PreparedPartProjection) -> bool:
    return current_part_element_is_exact(document, prepared.target) and all(
        current_part_element_is_exact(document, source) for source in prepared.sources
    )


def create_part_projection(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartProjection,
) -> NativeMutationDraft:
    import FreeCAD as App
    import PartGui

    if not _inputs_are_exact(document, prepared):
        raise NativeModelError("A Part projection input changed after preflight.")
    spec = prepared.spec
    result = document.addObject("Part::ProjectOnSurface", "Projection")
    if result is None or result.TypeId != "Part::ProjectOnSurface":
        raise NativeModelError("The Part projection factory returned the wrong type.")
    result.Label = label
    result.SupportFace = (prepared.target.target, [spec.target.subelement])
    result.Projection = [
        (source.target, [item.subelement])
        for source, item in zip(prepared.sources, spec.sources, strict=True)
    ]
    result.Mode = _MODES[spec.mode]
    result.Height = spec.height
    result.Offset = spec.offset
    result.Direction = App.Vector(*spec.direction)

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if recomputed is False or not result.isValid() or shape.isNull() or not shape.isValid():
        raise NativeModelError(
            str(result.getStatusString() or "Part projection did not produce valid geometry.")
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def _vector(value: Any) -> tuple[float, float, float]:
    return tuple(float(getattr(value, axis)) for axis in "xyz")


def verify_part_projection(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    expected_sources = tuple(
        (source.target, (item.subelement,))
        for source, item in zip(prepared.sources, spec.sources, strict=True)
    )
    if document.getObject(result.Name) is not result or result.TypeId != "Part::ProjectOnSurface":
        raise NativeModelError("The Part projection result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Part projection result changed its label.")
    if (
        link_sub(result.SupportFace)
        != (prepared.target.target, (spec.target.subelement,))
        or flatten_link_sub_list(result.Projection) != expected_sources
        or str(result.Mode) != _MODES[spec.mode]
        or not close_number(result.Height, spec.height)
        or not close_number(result.Offset, spec.offset)
        or any(
            not close_number(actual, expected)
            for actual, expected in zip(_vector(result.Direction), spec.direction, strict=True)
        )
    ):
        raise NativeModelError("The Part projection result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part projection result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or "VibeCADTimelineReplacedInputs" in result.PropertiesList
    ):
        raise NativeModelError("The Part projection Design identity is invalid.")
    if not _inputs_are_exact(document, prepared):
        raise NativeModelError("A Part projection input changed before commit.")
    if any(_visible(obj) is not was_visible for obj, was_visible in prepared.presentations):
        raise NativeModelError("Part projection changed an input presentation.")

    return {
        "root": object_reference(result),
        "source_count": len(prepared.sources),
        "shape_type": str(shape.ShapeType),
        "solid_count": len(shape.Solids),
        "face_count": len(shape.Faces),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
