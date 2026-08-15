# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Surface Sections preparation, creation, and verification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativePartHistory import (
    CurrentPartElement,
    current_part_element_is_exact,
    flatten_link_sub_list,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"sections"})
_SECTION_FIELDS = frozenset({"object_name", "edge"})
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class SurfaceSection:
    object_ref: NativeObjectRef
    edge: str


@dataclass(frozen=True, slots=True)
class SurfaceSectionsSpec:
    sections: tuple[SurfaceSection, ...]


@dataclass(frozen=True, slots=True)
class PreparedSurfaceSection:
    spec: SurfaceSection
    element: CurrentPartElement


@dataclass(frozen=True, slots=True)
class PreparedSurfaceSections:
    spec: SurfaceSectionsSpec
    sections: tuple[PreparedSurfaceSection, ...]


def prepare_surface_sections(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceSectionsSpec:
    if (
        not isinstance(value, Mapping)
        or set(value) != _DEFINITION_FIELDS
        or not isinstance(value["sections"], list)
        or not 2 <= len(value["sections"]) <= 256
    ):
        raise NativeModelError(
            "Surface Sections requires 2 to 256 ordered exact section edges."
        )
    sections = []
    for index, raw in enumerate(value["sections"], start=1):
        if not isinstance(raw, Mapping) or set(raw) != _SECTION_FIELDS:
            raise NativeModelError(f"Surface section {index} has invalid fields.")
        edge = str(raw["edge"] or "")
        if _EDGE_NAME.fullmatch(edge) is None:
            raise NativeModelError(f"Surface section {index} requires exact EdgeN.")
        sections.append(
            SurfaceSection(
                NativeObjectRef(document_uid, str(raw["object_name"] or "")),
                edge,
            )
        )
    identities = tuple(
        (section.object_ref.object_name, section.edge) for section in sections
    )
    if len(identities) != len(set(identities)):
        raise NativeModelError("Surface section edges must be distinct.")
    return SurfaceSectionsSpec(tuple(sections))


def preflight_surface_sections(
    document: Any,
    spec: SurfaceSectionsSpec,
) -> PreparedSurfaceSections:
    if not isinstance(spec, SurfaceSectionsSpec):
        raise TypeError("spec must be a SurfaceSectionsSpec")
    prepared = []
    identities = []
    for index, section in enumerate(spec.sections, start=1):
        element = resolve_current_part_element(
            document,
            section.object_ref,
            subelement=section.edge,
            operation=f"Surface section {index}",
        )
        derived = getattr(element.target, "isDerivedFrom", None)
        if (
            str(element.shape.ShapeType) != "Edge"
            or not callable(derived)
            or not derived("Part::Feature")
        ):
            raise NativeModelError(
                f"Surface section {index} must be one exact Part edge."
            )
        prepared.append(PreparedSurfaceSection(section, element))
        identities.append((element.target, element.subelement))
    if len(identities) != len(set(identities)):
        raise NativeModelError(
            "Surface section inputs resolve to duplicate current-History edges."
        )
    return PreparedSurfaceSections(spec, tuple(prepared))


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceSections) -> bool:
    return all(
        current_part_element_is_exact(document, section.element)
        for section in prepared.sections
    )


def _expected_links(
    prepared: PreparedSurfaceSections,
) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    return tuple(
        (section.element.target, (section.spec.edge,))
        for section in prepared.sections
    )


def create_surface_sections(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceSections,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedSurfaceSections):
        raise TypeError("prepared must be a PreparedSurfaceSections")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("A Surface section changed after preflight.")
    result = document.addObject("Surface::Sections", "Surface")
    if result is None or str(getattr(result, "TypeId", "")) != "Surface::Sections":
        raise NativeModelError("The Surface Sections factory returned the wrong type.")
    result.Label = label
    result.NSections = [
        (section.element.target, [section.spec.edge])
        for section in prepared.sections
    ]
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
            status
            if status and status != "Valid"
            else "Surface Sections produced no valid face."
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_surface_sections(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedSurfaceSections = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Surface::Sections"
        or str(result.Label) != draft.value["label"]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
        or flatten_link_sub_list(result.NSections) != _expected_links(prepared)
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError("Surface Sections failed its retained postcondition.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
    ):
        raise NativeModelError("Surface Sections lost its History or Design identity.")
    PartDesign.validateDesign(result)
    return {
        "root": object_reference(result),
        "section_count": len(prepared.sections),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
    }
