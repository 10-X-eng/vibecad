# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Extrude preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartSource,
    close_number,
    copy_part_visual,
    current_part_source_is_exact,
    grouped_result_labels,
    is_part_2d,
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
    {
        "sources",
        "direction",
        "length_along_mm",
        "length_against_mm",
        "symmetric",
        "reversed",
        "taper_along_degrees",
        "taper_against_degrees",
        "solid",
    }
)
_DIRECTION_FIELDS = {
    "normal": frozenset({"kind"}),
    "custom": frozenset({"kind", "vector"}),
    "edge": frozenset({"kind", "edge"}),
}
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_TOLERANCE = 1.0e-8
_MAX_SOURCES = 32
_MAX_LENGTH = 1_000_000.0
_MAX_TAPER = 89.9


@dataclass(frozen=True, slots=True)
class PartExtrudeEdgeSpec:
    object_ref: NativeObjectRef
    subelement: str


@dataclass(frozen=True, slots=True)
class PartExtrudeSpec:
    source_refs: tuple[NativeObjectRef, ...]
    direction_kind: str
    direction_vector: tuple[float, float, float] | None
    direction_edge: PartExtrudeEdgeSpec | None
    length_along: float
    length_against: float
    symmetric: bool
    reversed: bool
    taper_along: float
    taper_against: float
    solid: bool


@dataclass(frozen=True, slots=True)
class PreparedPartExtrude:
    spec: PartExtrudeSpec
    sources: tuple[CurrentPartSource, ...]
    direction_target: Any | None
    direction_shape: Any | None


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Part Extrude {name} must be true or false.")
    return value


def _number(value: Any, name: str, *, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Part Extrude {name} must be a number.") from exc
    if not math.isfinite(number) or abs(number) > maximum:
        raise NativeModelError(f"Part Extrude {name} is outside its finite range.")
    return number


def _object_ref(document_uid: str, value: Any, *, name: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A Part Extrude {name} target is invalid.")
    return NativeObjectRef(document_uid, str(value["object_name"] or ""))


def _source_refs(document_uid: str, value: Any) -> tuple[NativeObjectRef, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SOURCES:
        raise NativeModelError("Part Extrude requires 1 to 32 exact source objects.")
    refs = tuple(_object_ref(document_uid, item, name="source") for item in value)
    names = tuple(ref.object_name for ref in refs)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Extrude source objects must be unique.")
    return refs


def _direction(
    document_uid: str,
    value: Any,
) -> tuple[str, tuple[float, float, float] | None, PartExtrudeEdgeSpec | None]:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Part Extrude direction is invalid.")
    values = dict(value)
    kind = str(values.get("kind") or "").strip()
    expected = _DIRECTION_FIELDS.get(kind)
    if expected is None or set(values) != expected:
        raise NativeModelError("The Part Extrude direction fields do not match its kind.")
    if kind == "custom":
        vector = values["vector"]
        if not isinstance(vector, Mapping) or set(vector) != {"x", "y", "z"}:
            raise NativeModelError("A custom Part Extrude vector is invalid.")
        components = tuple(
            _number(vector[axis], f"direction {axis}", maximum=_MAX_LENGTH)
            for axis in "xyz"
        )
        if math.sqrt(sum(component * component for component in components)) <= _TOLERANCE:
            raise NativeModelError("A custom Part Extrude vector must be non-zero.")
        return kind, components, None
    if kind == "edge":
        edge = values["edge"]
        if not isinstance(edge, Mapping) or set(edge) != {
            "object_name",
            "subelement",
        }:
            raise NativeModelError("A Part Extrude direction edge is invalid.")
        subelement = str(edge["subelement"] or "")
        if _EDGE_NAME.fullmatch(subelement) is None:
            raise NativeModelError("A Part Extrude direction requires an exact EdgeN.")
        return (
            kind,
            None,
            PartExtrudeEdgeSpec(
                NativeObjectRef(document_uid, str(edge["object_name"] or "")),
                subelement,
            ),
        )
    return kind, None, None


def prepare_part_extrude(document_uid: str, value: Mapping[str, Any]) -> PartExtrudeSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Extrude definition must contain its exact controls.")
    values = dict(value)
    direction_kind, direction_vector, direction_edge = _direction(
        document_uid,
        values["direction"],
    )
    length_along = _number(
        values["length_along_mm"],
        "length along",
        maximum=_MAX_LENGTH,
    )
    length_against = _number(
        values["length_against_mm"],
        "length against",
        maximum=_MAX_LENGTH,
    )
    symmetric = _boolean(values["symmetric"], "symmetric")
    if (
        not symmetric
        and abs(length_along + length_against) <= _TOLERANCE
        and abs(length_along - length_against) > _TOLERANCE
    ):
        raise NativeModelError("Part Extrude total length must be non-zero.")
    taper_along = _number(
        values["taper_along_degrees"],
        "taper along",
        maximum=_MAX_TAPER,
    )
    taper_against = _number(
        values["taper_against_degrees"],
        "taper against",
        maximum=_MAX_TAPER,
    )
    return PartExtrudeSpec(
        source_refs=_source_refs(document_uid, values["sources"]),
        direction_kind=direction_kind,
        direction_vector=direction_vector,
        direction_edge=direction_edge,
        length_along=length_along,
        length_against=length_against,
        symmetric=symmetric,
        reversed=_boolean(values["reversed"], "reversed"),
        taper_along=taper_along,
        taper_against=taper_against,
        solid=_boolean(values["solid"], "solid"),
    )


def _preflight_normal(source: CurrentPartSource) -> None:
    if is_part_2d(source.target):
        return
    try:
        plane = source.shape.findPlane()
    except Exception as exc:
        raise NativeModelError(
            "Part Extrude cannot determine a normal for one source."
        ) from exc
    if plane is None:
        raise NativeModelError("Part Extrude normal mode requires planar source shapes.")


def _preflight_edge(
    document: Any,
    edge_spec: PartExtrudeEdgeSpec,
) -> tuple[Any, Any]:
    resolved = resolve_current_part_edge(
        document,
        edge_spec.object_ref,
        subelement=edge_spec.subelement,
        operation="Part Extrude direction",
    )
    edge = resolved.shape
    if (
        edge is None
        or edge.isNull()
        or str(edge.ShapeType) != "Edge"
        or str(getattr(getattr(edge, "Curve", None), "TypeId", ""))
        != "Part::GeomLine"
        or float(edge.Length) <= _TOLERANCE
    ):
        raise NativeModelError("Part Extrude direction edge must be a non-zero straight edge.")
    return resolved.target, edge


def preflight_part_extrude(document: Any, spec: PartExtrudeSpec) -> PreparedPartExtrude:
    if not isinstance(spec, PartExtrudeSpec):
        raise TypeError("spec must be a PartExtrudeSpec")
    sources = tuple(
        resolve_current_part_source(document, reference, operation="Part Extrude")
        for reference in spec.source_refs
    )
    targets = tuple(source.target for source in sources)
    if len(targets) != len(set(targets)):
        raise NativeModelError("Part Extrude sources resolve to duplicate current shapes.")
    if spec.direction_kind == "normal":
        for source in sources:
            _preflight_normal(source)
    direction_target = None
    direction_shape = None
    if spec.direction_edge is not None:
        direction_target, direction_shape = _preflight_edge(document, spec.direction_edge)
    return PreparedPartExtrude(spec, sources, direction_target, direction_shape)


def _set_angle(obj: Any, property_name: str, value: float) -> None:
    setattr(obj, property_name, f"{value:.17g} deg")


def create_part_extrude(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartExtrude,
) -> NativeMutationDraft:
    import FreeCAD as App
    import PartGui

    if any(
        not current_part_source_is_exact(document, source)
        for source in prepared.sources
    ):
        raise NativeModelError("A Part Extrude source changed after preflight.")
    spec = prepared.spec
    results = []
    mode = {"normal": "Normal", "custom": "Custom", "edge": "Edge"}[
        spec.direction_kind
    ]
    result_labels = grouped_result_labels(label, len(prepared.sources))
    for source, result_label in zip(
        prepared.sources,
        result_labels,
        strict=True,
    ):
        result = document.addObject("Part::Extrusion", "Extrude")
        if result is None or str(getattr(result, "TypeId", "")) != "Part::Extrusion":
            raise NativeModelError("The Part Extrude factory returned the wrong object type.")
        result.Label = result_label
        result.Base = source.target
        result.DirMode = mode
        if spec.direction_vector is not None:
            result.Dir = App.Vector(*spec.direction_vector)
        if spec.direction_edge is not None:
            result.DirLink = (
                prepared.direction_target,
                [spec.direction_edge.subelement],
            )
        else:
            result.DirLink = None
        result.LengthFwd = spec.length_along
        result.LengthRev = spec.length_against
        result.Solid = spec.solid
        result.Reversed = spec.reversed
        result.Symmetric = spec.symmetric
        _set_angle(result, "TaperAngle", spec.taper_along)
        _set_angle(result, "TaperAngleRev", spec.taper_against)
        copy_part_visual(source.target, result)
        results.append(result)

    recomputed = document.recompute(results, True, True)
    if recomputed is False:
        raise NativeModelError("Part Extrude results failed to recompute.")
    for result in results:
        shape = result.Shape
        if not result.isValid() or shape.isNull() or not shape.isValid():
            raise NativeModelError(
                str(result.getStatusString() or "Part Extrude produced an invalid shape.")
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
            raise NativeModelError("Part Extrude could not retain its replaced inputs.")
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


def verify_part_extrude(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    results = draft.value["results"]
    root = results[-1]
    mode = {"normal": "Normal", "custom": "Custom", "edge": "Edge"}[
        spec.direction_kind
    ]
    expected_edge = (
        (prepared.direction_target, (spec.direction_edge.subelement,))
        if spec.direction_edge is not None
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
            or result.TypeId != "Part::Extrusion"
        ):
            raise NativeModelError(
                f"Part Extrude result {index + 1} lost its exact object identity."
            )
        if str(result.Label) != draft.value["result_labels"][index]:
            raise NativeModelError(
                f"Part Extrude result {index + 1} changed its label."
            )
        if result.Base is not source.target:
            raise NativeModelError(
                f"Part Extrude result {index + 1} changed its source."
            )
        if str(result.DirMode) != mode or link_sub(result.DirLink) != expected_edge:
            raise NativeModelError(
                f"Part Extrude result {index + 1} changed its direction controls."
            )
        if (
            not close_number(property_number(result.LengthFwd), spec.length_along)
            or not close_number(property_number(result.LengthRev), spec.length_against)
            or bool(result.Solid) is not spec.solid
            or bool(result.Reversed) is not spec.reversed
            or bool(result.Symmetric) is not spec.symmetric
            or not close_number(property_number(result.TaperAngle), spec.taper_along)
            or not close_number(property_number(result.TaperAngleRev), spec.taper_against)
        ):
            raise NativeModelError(
                f"Part Extrude result {index + 1} changed its exact parameters."
            )
        if (
            not result.isValid()
            or shape.isNull()
            or not shape.isValid()
            or result.getParentGeoFeatureGroup() is not None
        ):
            raise NativeModelError(
                f"Part Extrude result {index + 1} is not a valid root-level shape."
            )
        if (
            str(getattr(result, "VibeCADTimelineRole", "") or "")
            != expected_role
            or (result is root and owner is not None)
            or (result is not root and owner is not root)
        ):
            raise NativeModelError(
                f"Part Extrude result {index + 1} has invalid History ownership."
            )
        if not current_part_source_is_exact(document, source):
            raise NativeModelError(
                f"Part Extrude source {index + 1} changed before commit."
            )
        if spec.direction_vector is not None:
            actual = tuple(float(getattr(result.Dir, axis)) for axis in "xyz")
            if any(
                not close_number(value, expected)
                for value, expected in zip(
                    actual,
                    spec.direction_vector,
                    strict=True,
                )
            ):
                raise NativeModelError("The custom Part Extrude direction changed.")

    if (
        not str(getattr(root, "VibeCADDefinitionId", "") or "")
        or not str(getattr(root, "DesignId", "") or "")
    ):
        raise NativeModelError("The Part Extrude Design identity did not persist.")
    expected_presentations = tuple(draft.value["presentations"])
    actual_presentations = tuple(
        getattr(root, "VibeCADTimelineReplacedInputs", ()) or ()
    )
    if actual_presentations != expected_presentations:
        raise NativeModelError("The Part Extrude replaced-input set changed.")

    shapes = tuple(result.Shape for result in results)
    shape_types = tuple(dict.fromkeys(str(shape.ShapeType) for shape in shapes))
    return {
        "root": object_reference(root),
        "source_count": len(prepared.sources),
        "result_count": len(results),
        "resource_count": len(results) - 1,
        "direction_mode": spec.direction_kind,
        "solid": spec.solid,
        "shape_types": list(shape_types),
        "total_length_mm": sum(float(shape.Length) for shape in shapes),
        "total_area_mm2": sum(float(shape.Area) for shape in shapes),
        "total_volume_mm3": sum(float(shape.Volume) for shape in shapes),
    }
