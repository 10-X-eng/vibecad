# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Geometric Fill Surface preparation, creation, and verification."""

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


_DEFINITION_FIELDS = frozenset({"boundaries", "style"})
_BOUNDARY_FIELDS = frozenset({"object_name", "edge", "reversed"})
_REQUIRED_BOUNDARY_FIELDS = frozenset({"object_name", "edge"})
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_STYLE_NAMES = {
    "stretched": "Stretched",
    "coons": "Coons",
    "curved": "Curved",
}


@dataclass(frozen=True, slots=True)
class SurfaceGeomFillBoundary:
    object_ref: NativeObjectRef
    edge: str
    reversed: bool


@dataclass(frozen=True, slots=True)
class SurfaceGeomFillSpec:
    boundaries: tuple[SurfaceGeomFillBoundary, ...]
    style: str


@dataclass(frozen=True, slots=True)
class PreparedSurfaceGeomFillBoundary:
    spec: SurfaceGeomFillBoundary
    element: CurrentPartElement


@dataclass(frozen=True, slots=True)
class PreparedSurfaceGeomFill:
    spec: SurfaceGeomFillSpec
    boundaries: tuple[PreparedSurfaceGeomFillBoundary, ...]


def prepare_surface_geometric_fill(
    document_uid: str,
    value: Mapping[str, Any],
) -> SurfaceGeomFillSpec:
    if not isinstance(value, Mapping) or set(value) - _DEFINITION_FIELDS or "boundaries" not in value:
        raise NativeModelError(
            "A Geometric Fill Surface definition requires exact boundaries and known controls."
        )
    raw_boundaries = value["boundaries"]
    if not isinstance(raw_boundaries, list) or not 2 <= len(raw_boundaries) <= 4:
        raise NativeModelError("Geometric Fill Surface requires 2 to 4 exact boundary edges.")
    boundaries = []
    for index, raw in enumerate(raw_boundaries, start=1):
        if (
            not isinstance(raw, Mapping)
            or not _REQUIRED_BOUNDARY_FIELDS <= set(raw)
            or not set(raw) <= _BOUNDARY_FIELDS
        ):
            raise NativeModelError(
                f"Geometric Fill Surface boundary {index} has invalid fields."
            )
        edge = str(raw["edge"] or "")
        if _EDGE_NAME.fullmatch(edge) is None:
            raise NativeModelError(
                f"Geometric Fill Surface boundary {index} requires exact EdgeN."
            )
        reversed_value = raw.get("reversed", False)
        if type(reversed_value) is not bool:
            raise NativeModelError(
                f"Geometric Fill Surface boundary {index} reversed must be boolean."
            )
        boundaries.append(
            SurfaceGeomFillBoundary(
                NativeObjectRef(document_uid, str(raw["object_name"] or "")),
                edge,
                reversed_value,
            )
        )
    identities = tuple(
        (boundary.object_ref.object_name, boundary.edge) for boundary in boundaries
    )
    if len(identities) != len(set(identities)):
        raise NativeModelError("Geometric Fill Surface boundaries must be distinct.")
    style = str(value.get("style", "stretched") or "")
    if style not in _STYLE_NAMES:
        raise NativeModelError("Geometric Fill Surface style is invalid.")
    return SurfaceGeomFillSpec(tuple(boundaries), style)


def preflight_surface_geometric_fill(
    document: Any,
    spec: SurfaceGeomFillSpec,
) -> PreparedSurfaceGeomFill:
    if not isinstance(spec, SurfaceGeomFillSpec):
        raise TypeError("spec must be a SurfaceGeomFillSpec")
    prepared = []
    identities = []
    for index, boundary in enumerate(spec.boundaries, start=1):
        element = resolve_current_part_element(
            document,
            boundary.object_ref,
            subelement=boundary.edge,
            operation=f"Geometric Fill Surface boundary {index}",
        )
        derived = getattr(element.target, "isDerivedFrom", None)
        if (
            str(element.shape.ShapeType) != "Edge"
            or not callable(derived)
            or not derived("Part::Feature")
        ):
            raise NativeModelError(
                f"Geometric Fill Surface boundary {index} must be one exact Part edge."
            )
        prepared.append(PreparedSurfaceGeomFillBoundary(boundary, element))
        identities.append((element.target, boundary.edge))
    if len(identities) != len(set(identities)):
        raise NativeModelError(
            "Geometric Fill Surface inputs resolve to duplicate current-History edges."
        )
    return PreparedSurfaceGeomFill(spec, tuple(prepared))


def _prepared_is_exact(document: Any, prepared: PreparedSurfaceGeomFill) -> bool:
    return all(
        current_part_element_is_exact(document, boundary.element)
        for boundary in prepared.boundaries
    )


def create_surface_geometric_fill(
    document: Any,
    *,
    label: str,
    prepared: PreparedSurfaceGeomFill,
) -> NativeMutationDraft:
    import PartGui

    if not isinstance(prepared, PreparedSurfaceGeomFill):
        raise TypeError("prepared must be a PreparedSurfaceGeomFill")
    if not _prepared_is_exact(document, prepared):
        raise NativeModelError("A Geometric Fill Surface boundary changed after preflight.")
    result = document.addObject("Surface::GeomFillSurface", "Surface")
    if result is None or str(getattr(result, "TypeId", "")) != "Surface::GeomFillSurface":
        raise NativeModelError(
            "The Geometric Fill Surface factory returned the wrong object type."
        )
    result.Label = label
    result.BoundaryList = [
        (boundary.element.target, [boundary.spec.edge])
        for boundary in prepared.boundaries
    ]
    result.ReversedList = [boundary.spec.reversed for boundary in prepared.boundaries]
    result.FillType = _STYLE_NAMES[prepared.spec.style]
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
            else "Geometric Fill Surface produced no valid face."
        )
    PartGui.publishDesignDefinitionBlock((result,))
    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def _expected_links(
    prepared: PreparedSurfaceGeomFill,
) -> tuple[tuple[Any, tuple[str, ...]], ...]:
    return tuple(
        (boundary.element.target, (boundary.spec.edge,))
        for boundary in prepared.boundaries
    )


def verify_surface_geometric_fill(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import PartDesign

    prepared: PreparedSurfaceGeomFill = draft.value["prepared"]
    result = draft.value["result"]
    shape = result.Shape
    if (
        document.getObject(result.Name) is not result
        or result.TypeId != "Surface::GeomFillSurface"
        or str(result.Label) != draft.value["label"]
        or result.getParentGeoFeatureGroup() is not None
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or str(shape.ShapeType) != "Face"
        or len(shape.Faces) != 1
        or flatten_link_sub_list(result.BoundaryList) != _expected_links(prepared)
        or tuple(bool(value) for value in result.ReversedList)
        != tuple(boundary.spec.reversed for boundary in prepared.boundaries)
        or str(result.FillType) != _STYLE_NAMES[prepared.spec.style]
        or not _prepared_is_exact(document, prepared)
    ):
        raise NativeModelError(
            "Geometric Fill Surface failed its exact retained postcondition."
        )
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
    ):
        raise NativeModelError("Geometric Fill Surface lost its History or Design identity.")
    PartDesign.validateDesign(result)
    return {
        "root": object_reference(result),
        "boundary_count": len(prepared.boundaries),
        "style": prepared.spec.style,
        "reversed_count": sum(
            boundary.spec.reversed for boundary in prepared.boundaries
        ),
        "edge_count": len(shape.Edges),
        "area_mm2": float(shape.Area),
    }
