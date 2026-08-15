# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Loft preparation, creation, and verification."""

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
    part_profile_type,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"profiles", "solid", "ruled", "closed"})
_PROFILE_NAME = re.compile(r"^(?:Vertex|Edge|Wire|Face)[1-9][0-9]*$")
_MAX_PROFILES = 32
_MAX_DEGREE = 5


@dataclass(frozen=True, slots=True)
class PartLoftProfileSpec:
    object_ref: NativeObjectRef
    subelement: str | None


@dataclass(frozen=True, slots=True)
class PartLoftSpec:
    profiles: tuple[PartLoftProfileSpec, ...]
    solid: bool
    ruled: bool
    closed: bool


@dataclass(frozen=True, slots=True)
class PreparedPartLoft:
    spec: PartLoftSpec
    profiles: tuple[CurrentPartElement, ...]
    profile_types: tuple[str, ...]
    presentations: tuple[Any, ...]
    visible_inputs: tuple[Any, ...]


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Part Loft {name} must be true or false.")
    return value


def _profile_spec(document_uid: str, value: Any) -> PartLoftProfileSpec:
    if not isinstance(value, Mapping) or set(value) not in (
        {"object_name"},
        {"object_name", "subelement"},
    ):
        raise NativeModelError("A Part Loft profile target is invalid.")
    object_name = str(value.get("object_name") or "")
    subelement = str(value.get("subelement") or "") or None
    if subelement is not None and _PROFILE_NAME.fullmatch(subelement) is None:
        raise NativeModelError(
            "A Part Loft subelement must be an exact VertexN, EdgeN, WireN, or FaceN."
        )
    return PartLoftProfileSpec(
        NativeObjectRef(document_uid, object_name),
        subelement,
    )


def prepare_part_loft(document_uid: str, value: Mapping[str, Any]) -> PartLoftSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Loft definition must contain its exact controls.")
    profiles = value["profiles"]
    if not isinstance(profiles, list) or not 2 <= len(profiles) <= _MAX_PROFILES:
        raise NativeModelError("Part Loft requires 2 to 32 ordered profile targets.")
    profile_specs = tuple(_profile_spec(document_uid, item) for item in profiles)
    keys = tuple(
        (profile.object_ref.object_name, profile.subelement)
        for profile in profile_specs
    )
    if len(keys) != len(set(keys)):
        raise NativeModelError("Part Loft profile targets must be distinct.")
    return PartLoftSpec(
        profiles=profile_specs,
        solid=_boolean(value["solid"], "solid"),
        ruled=_boolean(value["ruled"], "ruled"),
        closed=_boolean(value["closed"], "closed"),
    )


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_loft(document: Any, spec: PartLoftSpec) -> PreparedPartLoft:
    import PartGui

    if not isinstance(spec, PartLoftSpec):
        raise TypeError("spec must be a PartLoftSpec")
    profiles = tuple(
        resolve_current_part_element(
            document,
            profile.object_ref,
            subelement=profile.subelement,
            operation="Part Loft profile",
        )
        for profile in spec.profiles
    )
    keys = tuple((profile.target, profile.subelement) for profile in profiles)
    if len(keys) != len(set(keys)):
        raise NativeModelError("Part Loft profiles resolve to duplicate geometry.")
    profile_types = tuple(part_profile_type(profile.shape) for profile in profiles)
    if any(shape_type is None for shape_type in profile_types):
        raise NativeModelError(
            "Each Part Loft profile must resolve to one vertex, edge, wire, or face."
        )

    presentations: list[Any] = []
    visible_inputs: list[Any] = []
    for profile in profiles:
        if _visible(profile.target) and profile.target not in visible_inputs:
            visible_inputs.append(profile.target)
        presentation = PartGui.resolveModelingPresentationObject(profile.target)
        if presentation is None:
            presentation = profile.target
        if _visible(presentation):
            if presentation not in presentations:
                presentations.append(presentation)
            if presentation not in visible_inputs:
                visible_inputs.append(presentation)
    return PreparedPartLoft(
        spec,
        profiles,
        tuple(str(value) for value in profile_types),
        tuple(presentations),
        tuple(visible_inputs),
    )


def _profile_links(profiles: tuple[CurrentPartElement, ...]) -> tuple[Any, ...]:
    return tuple((profile.target, [profile.subelement or ""]) for profile in profiles)


def create_part_loft(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartLoft,
) -> NativeMutationDraft:
    import PartGui

    if any(
        not current_part_element_is_exact(document, profile)
        for profile in prepared.profiles
    ):
        raise NativeModelError("A Part Loft profile changed after preflight.")

    result = document.addObject("Part::Loft", "Loft")
    if result is None or str(getattr(result, "TypeId", "")) != "Part::Loft":
        raise NativeModelError("The Part Loft factory returned the wrong object type.")
    spec = prepared.spec
    result.Label = label
    result.Sections = tuple(profile.target for profile in prepared.profiles)
    if any(profile.subelement for profile in prepared.profiles):
        result.ProfileLinks = _profile_links(prepared.profiles)
    result.Solid = spec.solid
    result.Ruled = spec.ruled
    result.Closed = spec.closed
    result.MaxDegree = _MAX_DEGREE
    result.Linearize = False

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or (spec.solid and not tuple(shape.Solids))
    ):
        raise NativeModelError(
            str(result.getStatusString() or "Part Loft did not produce valid geometry.")
        )

    PartGui.publishDesignDefinitionBlock((result,))
    if prepared.presentations and not PartGui.setModelingReplacedInputs(
        result,
        prepared.presentations,
    ):
        raise NativeModelError("Part Loft could not retain its replaced inputs.")
    for input_object in prepared.visible_inputs:
        input_object.Visibility = False

    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_loft(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    expected_sections = tuple(profile.target for profile in prepared.profiles)
    expected_links = tuple(
        (profile.target, (profile.subelement or "",))
        for profile in prepared.profiles
    )
    has_subelement = any(profile.subelement for profile in prepared.profiles)
    if document.getObject(result.Name) is not result or result.TypeId != "Part::Loft":
        raise NativeModelError("The Part Loft result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Part Loft result changed its label.")
    if tuple(result.Sections) != expected_sections:
        raise NativeModelError("The Part Loft result changed its ordered profiles.")
    if (
        (has_subelement and flatten_link_sub_list(result.ProfileLinks) != expected_links)
        or (not has_subelement and tuple(result.ProfileLinks))
        or bool(result.Solid) is not spec.solid
        or bool(result.Ruled) is not spec.ruled
        or bool(result.Closed) is not spec.closed
        or int(result.MaxDegree) != _MAX_DEGREE
        or bool(result.Linearize)
    ):
        raise NativeModelError("The Part Loft result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or not tuple(shape.Faces)
        or (spec.solid and not tuple(shape.Solids))
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part Loft result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != prepared.presentations
    ):
        raise NativeModelError("The Part Loft Design identity is invalid.")
    for index, profile in enumerate(prepared.profiles):
        if not current_part_element_is_exact(document, profile):
            raise NativeModelError(f"Part Loft profile {index + 1} changed before commit.")
    if any(_visible(input_object) for input_object in prepared.visible_inputs):
        raise NativeModelError("A replaced Part Loft input became visible before commit.")

    return {
        "root": object_reference(result),
        "profile_count": len(prepared.profiles),
        "profile_types": list(prepared.profile_types),
        "solid": spec.solid,
        "ruled": spec.ruled,
        "closed": spec.closed,
        "shape_type": str(shape.ShapeType),
        "face_count": len(shape.Faces),
        "solid_count": len(shape.Solids),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
