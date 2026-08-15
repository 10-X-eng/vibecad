# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Revolve preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartEdge,
    CurrentPartSource,
    close_number,
    copy_part_visual,
    current_part_edge_is_exact,
    current_part_source_is_exact,
    grouped_result_labels,
    link_sub,
    property_number,
    resolve_current_part_edge,
    resolve_current_part_source,
)
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
)


_DEFINITION_FIELDS = frozenset(
    {"sources", "axis", "angle_degrees", "symmetric", "solid"}
)
_AXIS_FIELDS = {
    "custom": frozenset({"kind", "base_mm", "direction"}),
    "edge": frozenset({"kind", "reference"}),
}
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_MAX_SOURCES = 32
_MAX_COORDINATE = 1_000_000.0
_MAX_ANGLE = 360.0
_LINE_TYPE = "Part::GeomLine"
_CIRCLE_TYPE = "Part::GeomCircle"
_LENGTH_TOLERANCE = 1.0e-7
_ANGULAR_TOLERANCE_RADIANS = 1.0e-12


@dataclass(frozen=True, slots=True)
class PartRevolveAxisSpec:
    kind: str
    base: tuple[float, float, float] | None
    direction: tuple[float, float, float] | None
    reference: NativeObjectRef | None
    subelement: str | None


@dataclass(frozen=True, slots=True)
class PartRevolveSpec:
    source_refs: tuple[NativeObjectRef, ...]
    axis: PartRevolveAxisSpec
    angle: float
    symmetric: bool
    solid: bool


@dataclass(frozen=True, slots=True)
class PreparedPartRevolve:
    spec: PartRevolveSpec
    sources: tuple[CurrentPartSource, ...]
    axis_edge: CurrentPartEdge | None
    axis_curve_type: str | None


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Part Revolve {name} must be true or false.")
    return value


def _number(value: Any, name: str, *, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Part Revolve {name} must be a number.") from exc
    if not math.isfinite(number) or abs(number) > maximum:
        raise NativeModelError(f"Part Revolve {name} is outside its finite range.")
    return number


def _vector(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError(f"A Part Revolve {name} vector is invalid.")
    return tuple(
        _number(value[axis], f"{name} {axis}", maximum=_MAX_COORDINATE)
        for axis in "xyz"
    )


def _object_ref(document_uid: str, value: Any, *, name: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Part Revolve {name} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _source_refs(document_uid: str, value: Any) -> tuple[NativeObjectRef, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SOURCES:
        raise NativeModelError("Part Revolve requires 1 to 32 exact source objects.")
    refs = tuple(_object_ref(document_uid, item, name="source") for item in value)
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Revolve source objects must be unique.")
    return refs


def _axis(document_uid: str, value: Any) -> PartRevolveAxisSpec:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Part Revolve axis is invalid.")
    values = dict(value)
    kind = str(values.get("kind") or "").strip()
    expected = _AXIS_FIELDS.get(kind)
    if expected is None or set(values) != expected:
        raise NativeModelError("The Part Revolve axis fields do not match its kind.")
    if kind == "custom":
        base = _vector(values["base_mm"], "axis base")
        direction = _vector(values["direction"], "axis direction")
        if math.sqrt(sum(component * component for component in direction)) < _LENGTH_TOLERANCE:
            raise NativeModelError("A custom Part Revolve axis direction must be non-zero.")
        return PartRevolveAxisSpec(kind, base, direction, None, None)

    reference = values["reference"]
    if not isinstance(reference, Mapping) or set(reference) not in (
        {"object_name"},
        {"object_name", "subelement"},
    ):
        raise NativeModelError("A Part Revolve axis reference is invalid.")
    subelement = (
        str(reference["subelement"] or "") if "subelement" in reference else None
    )
    if subelement is not None and _EDGE_NAME.fullmatch(subelement) is None:
        raise NativeModelError("A Part Revolve axis subelement requires an exact EdgeN.")
    return PartRevolveAxisSpec(
        kind,
        None,
        None,
        NativeObjectRef(document_uid, str(reference["object_name"] or "")),
        subelement,
    )


def prepare_part_revolve(document_uid: str, value: Mapping[str, Any]) -> PartRevolveSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Revolve definition must contain its exact controls.")
    values = dict(value)
    axis = _axis(document_uid, values["axis"])
    angle = _number(values["angle_degrees"], "angle", maximum=_MAX_ANGLE)
    if axis.kind == "custom" and abs(math.radians(angle)) < _ANGULAR_TOLERANCE_RADIANS:
        raise NativeModelError("A custom-axis Part Revolve angle must be non-zero.")
    return PartRevolveSpec(
        source_refs=_source_refs(document_uid, values["sources"]),
        axis=axis,
        angle=angle,
        symmetric=_boolean(values["symmetric"], "symmetric"),
        solid=_boolean(values["solid"], "solid"),
    )


def _curve_type(edge: CurrentPartEdge) -> str:
    curve_type = str(getattr(getattr(edge.shape, "Curve", None), "TypeId", ""))
    if curve_type not in {_LINE_TYPE, _CIRCLE_TYPE}:
        raise NativeModelError("A Part Revolve axis edge must be a line or circular arc.")
    if float(edge.shape.Length) < _LENGTH_TOLERANCE:
        raise NativeModelError("A Part Revolve axis edge must be non-zero.")
    return curve_type


def preflight_part_revolve(document: Any, spec: PartRevolveSpec) -> PreparedPartRevolve:
    if not isinstance(spec, PartRevolveSpec):
        raise TypeError("spec must be a PartRevolveSpec")
    sources = tuple(
        resolve_current_part_source(document, reference, operation="Part Revolve")
        for reference in spec.source_refs
    )
    targets = tuple(source.target for source in sources)
    if len(targets) != len(set(targets)):
        raise NativeModelError("Part Revolve sources resolve to duplicate current shapes.")

    axis_edge = None
    axis_curve_type = None
    if spec.axis.reference is not None:
        axis_edge = resolve_current_part_edge(
            document,
            spec.axis.reference,
            subelement=spec.axis.subelement,
            operation="Part Revolve axis",
        )
        axis_curve_type = _curve_type(axis_edge)
        if (
            axis_curve_type != _CIRCLE_TYPE
            and abs(math.radians(spec.angle)) < _ANGULAR_TOLERANCE_RADIANS
        ):
            raise NativeModelError(
                "A straight-edge Part Revolve angle must be non-zero."
            )
    return PreparedPartRevolve(spec, sources, axis_edge, axis_curve_type)


def _set_angle(result: Any, value: float) -> None:
    result.Angle = value


def _axis_link(prepared: PreparedPartRevolve) -> Any:
    edge = prepared.axis_edge
    if edge is None:
        return None
    if edge.subelement:
        return edge.target, [edge.subelement]
    return edge.target


def create_part_revolve(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartRevolve,
) -> NativeMutationDraft:
    import FreeCAD as App
    import PartGui

    if any(
        not current_part_source_is_exact(document, source)
        for source in prepared.sources
    ):
        raise NativeModelError("A Part Revolve source changed after preflight.")
    if prepared.axis_edge is not None and not current_part_edge_is_exact(
        document,
        prepared.axis_edge,
    ):
        raise NativeModelError("The Part Revolve axis changed after preflight.")

    spec = prepared.spec
    result_labels = grouped_result_labels(label, len(prepared.sources))
    results = []
    for source, result_label in zip(prepared.sources, result_labels, strict=True):
        result = document.addObject("Part::Revolution", "Revolve")
        if result is None or str(getattr(result, "TypeId", "")) != "Part::Revolution":
            raise NativeModelError("The Part Revolve factory returned the wrong object type.")
        result.Label = result_label
        result.Source = source.target
        if spec.axis.base is not None:
            result.Base = App.Vector(*spec.axis.base)
        if spec.axis.direction is not None:
            result.Axis = App.Vector(*spec.axis.direction)
        result.AxisLink = _axis_link(prepared)
        _set_angle(result, spec.angle)
        result.Symmetric = spec.symmetric
        result.Solid = spec.solid
        copy_part_visual(source.target, result)
        results.append(result)

    recomputed = document.recompute(results, True, True)
    if recomputed is False:
        raise NativeModelError("Part Revolve results failed to recompute.")
    for result in results:
        shape = result.Shape
        if not result.isValid() or shape.isNull() or not shape.isValid():
            raise NativeModelError(
                str(result.getStatusString() or "Part Revolve produced an invalid shape.")
            )

    root = results[-1]
    presentations = []
    for source in prepared.sources:
        presentation = source.presentation
        if presentation is not None and presentation not in presentations:
            try:
                visible = bool(presentation.Visibility)
            except Exception:
                visible = False
            if visible:
                presentations.append(presentation)
    PartGui.publishDesignDefinitionBlock(tuple(results))
    if presentations:
        if not PartGui.setModelingReplacedInputs(root, tuple(presentations)):
            raise NativeModelError("Part Revolve could not retain its replaced inputs.")
        for presentation in presentations:
            presentation.Visibility = False

    return NativeMutationDraft(
        value={
            "label": label,
            "result_labels": result_labels,
            "prepared": prepared,
            "results": tuple(results),
            "presentations": tuple(presentations),
        },
        recompute_targets=tuple(results),
        created=tuple(object_identity(result) for result in results),
    )


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(float(getattr(value, axis)) for axis in "xyz")


def verify_part_revolve(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    results = draft.value["results"]
    root = results[-1]
    expected_link = (
        (
            prepared.axis_edge.target,
            (prepared.axis_edge.subelement,) if prepared.axis_edge.subelement else (),
        )
        if prepared.axis_edge is not None
        else (None, ())
    )
    for index, (source, result) in enumerate(
        zip(prepared.sources, results, strict=True)
    ):
        shape = result.Shape
        expected_role = "operation" if result is root else "resource"
        owner = getattr(result, "VibeCADTimelineOwner", None)
        if (
            document.getObject(result.Name) is not result
            or result.TypeId != "Part::Revolution"
        ):
            raise NativeModelError(
                f"Part Revolve result {index + 1} lost its exact object identity."
            )
        if str(result.Label) != draft.value["result_labels"][index]:
            raise NativeModelError(f"Part Revolve result {index + 1} changed its label.")
        if result.Source is not source.target:
            raise NativeModelError(f"Part Revolve result {index + 1} changed its source.")
        if link_sub(result.AxisLink) != expected_link:
            raise NativeModelError(f"Part Revolve result {index + 1} changed its axis link.")
        if (
            not close_number(property_number(result.Angle), spec.angle)
            or bool(result.Symmetric) is not spec.symmetric
            or bool(result.Solid) is not spec.solid
        ):
            raise NativeModelError(
                f"Part Revolve result {index + 1} changed its exact parameters."
            )
        if spec.axis.base is not None and any(
            not close_number(actual, expected)
            for actual, expected in zip(
                _vector_tuple(result.Base),
                spec.axis.base,
                strict=True,
            )
        ):
            raise NativeModelError("The custom Part Revolve axis base changed.")
        if spec.axis.direction is not None and any(
            not close_number(actual, expected)
            for actual, expected in zip(
                _vector_tuple(result.Axis),
                spec.axis.direction,
                strict=True,
            )
        ):
            raise NativeModelError("The custom Part Revolve axis direction changed.")
        if (
            not result.isValid()
            or shape.isNull()
            or not shape.isValid()
            or result.getParentGeoFeatureGroup() is not None
        ):
            raise NativeModelError(
                f"Part Revolve result {index + 1} is not a valid root-level shape."
            )
        if (
            str(getattr(result, "VibeCADTimelineRole", "") or "") != expected_role
            or (result is root and owner is not None)
            or (result is not root and owner is not root)
        ):
            raise NativeModelError(
                f"Part Revolve result {index + 1} has invalid History ownership."
            )
        if not current_part_source_is_exact(document, source):
            raise NativeModelError(f"Part Revolve source {index + 1} changed before commit.")

    if prepared.axis_edge is not None and not current_part_edge_is_exact(
        document,
        prepared.axis_edge,
    ):
        raise NativeModelError("The Part Revolve axis changed before commit.")
    if (
        not str(getattr(root, "VibeCADDefinitionId", "") or "")
        or not str(getattr(root, "DesignId", "") or "")
    ):
        raise NativeModelError("The Part Revolve Design identity did not persist.")
    expected_presentations = tuple(draft.value["presentations"])
    actual_presentations = tuple(
        getattr(root, "VibeCADTimelineReplacedInputs", ()) or ()
    )
    if actual_presentations != expected_presentations:
        raise NativeModelError("The Part Revolve replaced-input set changed.")

    shapes = tuple(result.Shape for result in results)
    shape_types = tuple(dict.fromkeys(str(shape.ShapeType) for shape in shapes))
    return {
        "root": object_reference(root),
        "source_count": len(prepared.sources),
        "result_count": len(results),
        "resource_count": len(results) - 1,
        "axis_mode": spec.axis.kind,
        "angle_degrees": spec.angle,
        "symmetric": spec.symmetric,
        "solid": spec.solid,
        "shape_types": list(shape_types),
        "total_length_mm": sum(float(shape.Length) for shape in shapes),
        "total_area_mm2": sum(float(shape.Area) for shape in shapes),
        "total_volume_mm3": sum(float(shape.Volume) for shape in shapes),
    }
