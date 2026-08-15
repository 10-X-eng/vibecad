# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Cross Sections batch creation and verification."""

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
    grouped_result_labels,
    link_sub,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"sources", "plane", "distribution"})
_DISTRIBUTION_FIELDS = {
    "single": frozenset({"kind", "position_mm"}),
    "series": frozenset(
        {"kind", "position_mm", "count", "distance_mm", "both_sides"}
    ),
}
_ELEMENT_NAME = re.compile(
    r"^(?:Vertex|Edge|Wire|Face|Shell|Solid|CompSolid|Compound)[1-9][0-9]*$"
)
_MAX_SOURCES = 32
_MAX_SUBELEMENTS = 64
_MAX_PLANES = 10_000
_MAX_POSITION = 1_000_000.0
_NORMALS = {
    "xy": (0.0, 0.0, 1.0),
    "xz": (0.0, 1.0, 0.0),
    "yz": (1.0, 0.0, 0.0),
}


@dataclass(frozen=True, slots=True)
class PartCrossSectionSourceSpec:
    object_ref: NativeObjectRef
    subelements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PartCrossSectionsSpec:
    sources: tuple[PartCrossSectionSourceSpec, ...]
    plane: str
    distribution: str
    position: float
    count: int
    distance: float
    both_sides: bool
    positions: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PreparedPartCrossSectionSource:
    spec: PartCrossSectionSourceSpec
    target: Any
    elements: tuple[CurrentPartElement, ...]
    presentation: Any
    presentation_was_visible: bool


@dataclass(frozen=True, slots=True)
class PreparedPartCrossSections:
    spec: PartCrossSectionsSpec
    sources: tuple[PreparedPartCrossSectionSource, ...]


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"Part Cross Sections {name} must be a number.") from exc
    if not math.isfinite(number) or abs(number) > _MAX_POSITION:
        raise NativeModelError(f"Part Cross Sections {name} is outside its finite range.")
    return number


def _source_spec(document_uid: str, value: Any) -> PartCrossSectionSourceSpec:
    if not isinstance(value, Mapping) or set(value) not in (
        {"object_name"},
        {"object_name", "subelements"},
    ):
        raise NativeModelError("A Part Cross Sections source target is invalid.")
    raw_subelements = value.get("subelements", [])
    if not isinstance(raw_subelements, list):
        raise NativeModelError("Part Cross Sections subelements must be an ordered list.")
    subelements = tuple(str(item or "") for item in raw_subelements)
    if "subelements" in value and (
        not 1 <= len(subelements) <= _MAX_SUBELEMENTS
        or len(subelements) != len(set(subelements))
        or any(_ELEMENT_NAME.fullmatch(item) is None for item in subelements)
    ):
        raise NativeModelError(
            "Part Cross Sections requires 1 to 64 distinct exact shape subelements."
        )
    return PartCrossSectionSourceSpec(
        NativeObjectRef(document_uid, str(value.get("object_name") or "")),
        subelements,
    )


def _distribution(value: Any) -> tuple[str, float, int, float, bool, tuple[float, ...]]:
    if not isinstance(value, Mapping):
        raise NativeModelError("A Part Cross Sections distribution is invalid.")
    kind = str(value.get("kind") or "")
    expected = _DISTRIBUTION_FIELDS.get(kind)
    if expected is None or set(value) != expected:
        raise NativeModelError(
            "Part Cross Sections distribution fields do not match its kind."
        )
    position = _finite_number(value["position_mm"], "position")
    if kind == "single":
        return kind, position, 1, 0.0, False, (position,)
    count = value["count"]
    if type(count) is not int or not 1 <= count <= _MAX_PLANES:
        raise NativeModelError("Part Cross Sections count must be 1 to 10000.")
    distance = _finite_number(value["distance_mm"], "distance")
    if distance < 0.0:
        raise NativeModelError("Part Cross Sections distance cannot be negative.")
    both_sides = value["both_sides"]
    if type(both_sides) is not bool:
        raise NativeModelError("Part Cross Sections both_sides must be true or false.")
    start = position - 0.5 * (count - 1) * distance if both_sides else position
    positions = tuple(start + index * distance for index in range(count))
    if any(abs(item) > _MAX_POSITION or not math.isfinite(item) for item in positions):
        raise NativeModelError("Part Cross Sections derived positions exceed their range.")
    return kind, position, count, distance, both_sides, positions


def prepare_part_cross_sections(
    document_uid: str,
    value: Mapping[str, Any],
) -> PartCrossSectionsSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError(
            "A Part Cross Sections definition must contain its exact controls."
        )
    raw_sources = value["sources"]
    if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= _MAX_SOURCES:
        raise NativeModelError("Part Cross Sections requires 1 to 32 source selections.")
    sources = tuple(_source_spec(document_uid, item) for item in raw_sources)
    names = tuple(source.object_ref.object_name for source in sources)
    if len(names) != len(set(names)):
        raise NativeModelError("Part Cross Sections source objects must be unique.")
    plane = str(value["plane"] or "")
    if plane not in _NORMALS:
        raise NativeModelError("Part Cross Sections plane must be xy, xz, or yz.")
    distribution, position, count, distance, both_sides, positions = _distribution(
        value["distribution"]
    )
    return PartCrossSectionsSpec(
        sources,
        plane,
        distribution,
        position,
        count,
        distance,
        both_sides,
        positions,
    )


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_cross_sections(
    document: Any,
    spec: PartCrossSectionsSpec,
) -> PreparedPartCrossSections:
    import PartGui

    if not isinstance(spec, PartCrossSectionsSpec):
        raise TypeError("spec must be a PartCrossSectionsSpec")
    prepared_sources = []
    targets = []
    for source in spec.sources:
        names = source.subelements or (None,)
        elements = tuple(
            resolve_current_part_element(
                document,
                source.object_ref,
                subelement=name,
                operation="Part Cross Sections source",
            )
            for name in names
        )
        target = elements[0].target
        if any(element.target is not target for element in elements):
            raise NativeModelError("A Part Cross Sections selection changed its owner.")
        presentation = PartGui.resolveModelingPresentationObject(target) or target
        prepared_sources.append(
            PreparedPartCrossSectionSource(
                source,
                target,
                elements,
                presentation,
                _visible(presentation),
            )
        )
        targets.append(target)
    if len(targets) != len(set(targets)):
        raise NativeModelError("Part Cross Sections sources resolve to duplicate shapes.")
    return PreparedPartCrossSections(spec, tuple(prepared_sources))


def _sources_are_exact(document: Any, prepared: PreparedPartCrossSections) -> bool:
    return all(
        current_part_element_is_exact(document, element)
        for source in prepared.sources
        for element in source.elements
    )


def create_part_cross_sections(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartCrossSections,
) -> NativeMutationDraft:
    import FreeCAD as App
    import PartGui

    if not _sources_are_exact(document, prepared):
        raise NativeModelError("A Part Cross Sections source changed after preflight.")
    labels = grouped_result_labels(label, len(prepared.sources))
    normal = _NORMALS[prepared.spec.plane]
    results = []
    for source, result_label in zip(prepared.sources, labels, strict=True):
        result = document.addObject("Part::CrossSections", f"{source.target.Name}_cs")
        if result is None or str(getattr(result, "TypeId", "")) != "Part::CrossSections":
            raise NativeModelError(
                "The Part Cross Sections factory returned the wrong object type."
            )
        result.Label = result_label
        result.Source = (source.target, list(source.spec.subelements))
        result.PlaneNormal = App.Vector(*normal)
        result.PlanePositions = list(prepared.spec.positions)
        results.append(result)

    recomputed = document.recompute(results, True, True)
    if recomputed is False:
        raise NativeModelError("Part Cross Sections results failed to recompute.")
    for result in results:
        shape = result.Shape
        if not result.isValid() or shape.isNull() or not shape.isValid():
            raise NativeModelError(
                str(
                    result.getStatusString()
                    or "Part Cross Sections did not produce valid geometry."
                )
            )
    PartGui.publishDesignDefinitionBlock(tuple(results))
    return NativeMutationDraft(
        value={
            "label": label,
            "labels": labels,
            "prepared": prepared,
            "results": tuple(results),
        },
        recompute_targets=tuple(results),
        created=tuple(object_identity(result) for result in results),
    )


def _vector(value: Any) -> tuple[float, float, float]:
    return tuple(float(getattr(value, axis)) for axis in "xyz")


def verify_part_cross_sections(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    results = draft.value["results"]
    root = results[-1]
    normal = _NORMALS[spec.plane]
    for index, (source, result) in enumerate(
        zip(prepared.sources, results, strict=True),
        start=1,
    ):
        shape = result.Shape
        expected_role = "operation" if result is root else "resource"
        owner = getattr(result, "VibeCADTimelineOwner", None)
        if document.getObject(result.Name) is not result or result.TypeId != "Part::CrossSections":
            raise NativeModelError(f"Part Cross Sections result {index} lost its identity.")
        if str(result.Label) != draft.value["labels"][index - 1]:
            raise NativeModelError(f"Part Cross Sections result {index} changed its label.")
        if link_sub(result.Source) != (source.target, source.spec.subelements):
            raise NativeModelError(f"Part Cross Sections result {index} changed its source.")
        if any(
            not close_number(actual, expected)
            for actual, expected in zip(_vector(result.PlaneNormal), normal, strict=True)
        ) or tuple(float(item) for item in result.PlanePositions) != spec.positions:
            raise NativeModelError(f"Part Cross Sections result {index} changed its planes.")
        if (
            not result.isValid()
            or shape.isNull()
            or not shape.isValid()
            or result.getParentGeoFeatureGroup() is not None
        ):
            raise NativeModelError(f"Part Cross Sections result {index} is invalid.")
        if (
            str(getattr(result, "VibeCADTimelineRole", "") or "") != expected_role
            or (result is root and owner is not None)
            or (result is not root and owner is not root)
            or "VibeCADTimelineReplacedInputs" in result.PropertiesList
        ):
            raise NativeModelError(f"Part Cross Sections result {index} ownership is invalid.")
        if any(
            not current_part_element_is_exact(document, element)
            for element in source.elements
        ):
            raise NativeModelError(f"Part Cross Sections source {index} changed before commit.")
        if _visible(source.presentation) is not source.presentation_was_visible:
            raise NativeModelError(f"Part Cross Sections source {index} changed visibility.")
    if (
        not str(getattr(root, "VibeCADDefinitionId", "") or "")
        or not str(getattr(root, "DesignId", "") or "")
    ):
        raise NativeModelError("The Part Cross Sections Design identity did not persist.")

    shapes = tuple(result.Shape for result in results)
    return {
        "root": object_reference(root),
        "source_count": len(prepared.sources),
        "result_count": len(results),
        "resource_count": len(results) - 1,
        "plane": spec.plane,
        "plane_count": len(spec.positions),
        "both_sides": spec.both_sides,
        "total_wire_count": sum(len(shape.Wires) for shape in shapes),
        "total_edge_count": sum(len(shape.Edges) for shape in shapes),
        "total_length_mm": sum(float(shape.Length) for shape in shapes),
    }
