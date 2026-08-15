# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact standalone Part Sweep preparation, creation, and verification."""

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
    link_sub,
    part_profile_type,
    resolve_current_part_element,
)
from VibeCADNativeTargets import NativeObjectRef, object_identity, object_reference


_DEFINITION_FIELDS = frozenset({"profiles", "path", "solid", "frenet"})
_PROFILE_NAME = re.compile(r"^(?:Vertex|Edge|Wire|Face)[1-9][0-9]*$")
_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_MAX_PROFILES = 32
_MAX_PATH_EDGES = 64
_TRANSITION = "Right corner"


@dataclass(frozen=True, slots=True)
class PartSweepProfileSpec:
    object_ref: NativeObjectRef
    subelement: str | None


@dataclass(frozen=True, slots=True)
class PartSweepPathSpec:
    object_ref: NativeObjectRef
    subelements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PartSweepSpec:
    profiles: tuple[PartSweepProfileSpec, ...]
    path: PartSweepPathSpec
    solid: bool
    frenet: bool


@dataclass(frozen=True, slots=True)
class PreparedPartSweep:
    spec: PartSweepSpec
    profiles: tuple[CurrentPartElement, ...]
    path: CurrentPartElement
    path_edges: tuple[CurrentPartElement, ...]
    profile_types: tuple[str, ...]
    path_type: str
    presentations: tuple[Any, ...]
    visible_inputs: tuple[Any, ...]


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise NativeModelError(f"Part Sweep {name} must be true or false.")
    return value


def _profile_spec(document_uid: str, value: Any) -> PartSweepProfileSpec:
    if not isinstance(value, Mapping) or set(value) not in (
        {"object_name"},
        {"object_name", "subelement"},
    ):
        raise NativeModelError("A Part Sweep profile target is invalid.")
    subelement = str(value.get("subelement") or "") or None
    if subelement is not None and _PROFILE_NAME.fullmatch(subelement) is None:
        raise NativeModelError(
            "A Part Sweep profile subelement must be an exact VertexN, EdgeN, WireN, or FaceN."
        )
    return PartSweepProfileSpec(
        NativeObjectRef(document_uid, str(value.get("object_name") or "")),
        subelement,
    )


def _path_spec(document_uid: str, value: Any) -> PartSweepPathSpec:
    if not isinstance(value, Mapping) or set(value) not in (
        {"object_name"},
        {"object_name", "subelements"},
    ):
        raise NativeModelError("A Part Sweep path target is invalid.")
    has_subelements = "subelements" in value
    raw_subelements = value.get("subelements", [])
    if not isinstance(raw_subelements, list):
        raise NativeModelError("Part Sweep path subelements must be an ordered list.")
    subelements = tuple(str(item or "") for item in raw_subelements)
    if (
        (has_subelements and not 1 <= len(subelements) <= _MAX_PATH_EDGES)
        or len(subelements) != len(set(subelements))
        or any(_EDGE_NAME.fullmatch(item) is None for item in subelements)
    ):
        raise NativeModelError(
            "Part Sweep path subelements must be 1 to 64 distinct exact EdgeN references."
        )
    return PartSweepPathSpec(
        NativeObjectRef(document_uid, str(value.get("object_name") or "")),
        subelements,
    )


def prepare_part_sweep(document_uid: str, value: Mapping[str, Any]) -> PartSweepSpec:
    if not isinstance(value, Mapping) or set(value) != _DEFINITION_FIELDS:
        raise NativeModelError("A Part Sweep definition must contain its exact controls.")
    profiles = value["profiles"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= _MAX_PROFILES:
        raise NativeModelError("Part Sweep requires 1 to 32 ordered profile targets.")
    profile_specs = tuple(_profile_spec(document_uid, item) for item in profiles)
    profile_keys = tuple(
        (profile.object_ref.object_name, profile.subelement)
        for profile in profile_specs
    )
    if len(profile_keys) != len(set(profile_keys)):
        raise NativeModelError("Part Sweep profile targets must be distinct.")
    path = _path_spec(document_uid, value["path"])
    for profile in profile_specs:
        if profile.object_ref.object_name != path.object_ref.object_name:
            continue
        if (
            profile.subelement is None
            or not path.subelements
            or profile.subelement in path.subelements
        ):
            raise NativeModelError(
                "The same geometry cannot be both a Part Sweep profile and its path."
            )
    return PartSweepSpec(
        profiles=profile_specs,
        path=path,
        solid=_boolean(value["solid"], "solid"),
        frenet=_boolean(value["frenet"], "frenet"),
    )


def _normalized_path(shape: Any, edges: tuple[CurrentPartElement, ...]) -> Any:
    import Part

    if edges:
        try:
            return Part.Wire([edge.shape for edge in edges])
        except Exception as exc:
            raise NativeModelError(
                "Part Sweep path edges must form one connected wire."
            ) from exc
    shape_type = str(shape.ShapeType)
    if shape_type in {"Edge", "Wire"}:
        return shape
    if shape_type != "Compound":
        raise NativeModelError("A Part Sweep path must be one edge or connected wire.")
    children = tuple(shape.childShapes(False, False))
    if not children or any(
        str(child.ShapeType) not in {"Edge", "Wire"} for child in children
    ):
        raise NativeModelError(
            "A whole-object Part Sweep path compound may contain only edges or wires."
        )
    groups = tuple(Part.sortEdges(list(shape.Edges)))
    if len(groups) != 1:
        raise NativeModelError("A Part Sweep path must form one connected wire.")
    try:
        return Part.Wire(groups[0])
    except Exception as exc:
        raise NativeModelError("A Part Sweep path must form one connected wire.") from exc


def _visible(obj: Any) -> bool:
    try:
        return bool(obj.Visibility)
    except Exception:
        return False


def preflight_part_sweep(document: Any, spec: PartSweepSpec) -> PreparedPartSweep:
    import PartGui

    if not isinstance(spec, PartSweepSpec):
        raise TypeError("spec must be a PartSweepSpec")
    profiles = tuple(
        resolve_current_part_element(
            document,
            profile.object_ref,
            subelement=profile.subelement,
            operation="Part Sweep profile",
        )
        for profile in spec.profiles
    )
    resolved_keys = tuple((profile.target, profile.subelement) for profile in profiles)
    if len(resolved_keys) != len(set(resolved_keys)):
        raise NativeModelError("Part Sweep profiles resolve to duplicate geometry.")
    profile_types = tuple(part_profile_type(profile.shape) for profile in profiles)
    if any(shape_type is None for shape_type in profile_types):
        raise NativeModelError(
            "Each Part Sweep profile must resolve to one vertex, edge, wire, or face."
        )

    path = resolve_current_part_element(
        document,
        spec.path.object_ref,
        subelement=None,
        operation="Part Sweep path",
    )
    path_edges = tuple(
        resolve_current_part_element(
            document,
            spec.path.object_ref,
            subelement=subelement,
            operation="Part Sweep path edge",
        )
        for subelement in spec.path.subelements
    )
    if any(str(edge.shape.ShapeType) != "Edge" for edge in path_edges):
        raise NativeModelError("Every Part Sweep path subelement must resolve to one edge.")
    normalized_path = _normalized_path(path.shape, path_edges)
    if normalized_path is None or normalized_path.isNull() or not normalized_path.isValid():
        raise NativeModelError("Part Sweep path geometry is invalid.")

    for profile in profiles:
        if profile.target is not path.target:
            continue
        if (
            profile.subelement is None
            or not spec.path.subelements
            or profile.subelement in spec.path.subelements
        ):
            raise NativeModelError(
                "The same geometry cannot be both a Part Sweep profile and its path."
            )

    presentations: list[Any] = []
    visible_inputs: list[Any] = []
    operands = tuple(profile.target for profile in profiles) + (path.target,)
    for operand in operands:
        if _visible(operand) and operand not in visible_inputs:
            visible_inputs.append(operand)
        presentation = PartGui.resolveModelingPresentationObject(operand) or operand
        if _visible(presentation):
            if presentation not in presentations:
                presentations.append(presentation)
            if presentation not in visible_inputs:
                visible_inputs.append(presentation)
    return PreparedPartSweep(
        spec,
        profiles,
        path,
        path_edges,
        tuple(str(value) for value in profile_types),
        str(normalized_path.ShapeType),
        tuple(presentations),
        tuple(visible_inputs),
    )


def _profile_links(profiles: tuple[CurrentPartElement, ...]) -> tuple[Any, ...]:
    return tuple((profile.target, [profile.subelement or ""]) for profile in profiles)


def create_part_sweep(
    document: Any,
    *,
    label: str,
    prepared: PreparedPartSweep,
) -> NativeMutationDraft:
    import PartGui

    for index, profile in enumerate(prepared.profiles, start=1):
        if not current_part_element_is_exact(document, profile):
            raise NativeModelError(
                f"Part Sweep profile {index} changed after preflight."
            )
    if not current_part_element_is_exact(document, prepared.path):
        raise NativeModelError("The Part Sweep path changed after preflight.")
    for index, edge in enumerate(prepared.path_edges, start=1):
        if not current_part_element_is_exact(document, edge):
            raise NativeModelError(
                f"Part Sweep path edge {index} changed after preflight."
            )

    result = document.addObject("Part::Sweep", "Sweep")
    if result is None or str(getattr(result, "TypeId", "")) != "Part::Sweep":
        raise NativeModelError("The Part Sweep factory returned the wrong object type.")
    spec = prepared.spec
    result.Label = label
    result.Sections = tuple(profile.target for profile in prepared.profiles)
    if any(profile.subelement for profile in prepared.profiles):
        result.ProfileLinks = _profile_links(prepared.profiles)
    result.Spine = (prepared.path.target, list(spec.path.subelements))
    result.Solid = spec.solid
    result.Frenet = spec.frenet
    result.Transition = _TRANSITION
    result.Linearize = False

    recomputed = document.recompute([result], True, True)
    shape = result.Shape
    if (
        recomputed is False
        or not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or (spec.solid and not tuple(shape.Solids))
    ):
        raise NativeModelError(
            str(result.getStatusString() or "Part Sweep did not produce valid geometry.")
        )

    PartGui.publishDesignDefinitionBlock((result,))
    if prepared.presentations and not PartGui.setModelingReplacedInputs(
        result,
        prepared.presentations,
    ):
        raise NativeModelError("Part Sweep could not retain its replaced inputs.")
    for input_object in prepared.visible_inputs:
        input_object.Visibility = False

    return NativeMutationDraft(
        value={"label": label, "prepared": prepared, "result": result},
        recompute_targets=(result,),
        created=(object_identity(result),),
    )


def verify_part_sweep(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    spec = prepared.spec
    result = draft.value["result"]
    shape = result.Shape
    expected_sections = tuple(profile.target for profile in prepared.profiles)
    expected_links = tuple(
        (profile.target, (profile.subelement or "",))
        for profile in prepared.profiles
    )
    has_profile_subelement = any(profile.subelement for profile in prepared.profiles)
    spine_target, spine_subelements = link_sub(result.Spine)
    if document.getObject(result.Name) is not result or result.TypeId != "Part::Sweep":
        raise NativeModelError("The Part Sweep result lost its identity.")
    if str(result.Label) != draft.value["label"]:
        raise NativeModelError("The Part Sweep result changed its label.")
    if tuple(result.Sections) != expected_sections:
        raise NativeModelError("The Part Sweep result changed its ordered profiles.")
    if (
        (
            has_profile_subelement
            and flatten_link_sub_list(result.ProfileLinks) != expected_links
        )
        or (not has_profile_subelement and tuple(result.ProfileLinks))
        or spine_target is not prepared.path.target
        or spine_subelements != spec.path.subelements
        or bool(result.Solid) is not spec.solid
        or bool(result.Frenet) is not spec.frenet
        or str(result.Transition) != _TRANSITION
        or bool(result.Linearize)
    ):
        raise NativeModelError("The Part Sweep result changed its exact controls.")
    if (
        not result.isValid()
        or shape.isNull()
        or not shape.isValid()
        or (spec.solid and not tuple(shape.Solids))
        or result.getParentGeoFeatureGroup() is not None
    ):
        raise NativeModelError("The Part Sweep result is not valid at Design root.")
    if (
        str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or not str(getattr(result, "VibeCADDefinitionId", "") or "")
        or not str(getattr(result, "DesignId", "") or "")
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        != prepared.presentations
    ):
        raise NativeModelError("The Part Sweep Design identity is invalid.")
    exact_inputs = prepared.profiles + (prepared.path,) + prepared.path_edges
    if any(not current_part_element_is_exact(document, item) for item in exact_inputs):
        raise NativeModelError("A Part Sweep input changed before commit.")
    if any(_visible(input_object) for input_object in prepared.visible_inputs):
        raise NativeModelError("A replaced Part Sweep input became visible before commit.")

    return {
        "root": object_reference(result),
        "profile_count": len(prepared.profiles),
        "profile_types": list(prepared.profile_types),
        "path_type": prepared.path_type,
        "path_edge_count": len(spec.path.subelements) or len(prepared.path.shape.Edges),
        "solid": spec.solid,
        "frenet": spec.frenet,
        "shape_type": str(shape.ShapeType),
        "edge_count": len(shape.Edges),
        "face_count": len(shape.Faces),
        "solid_count": len(shape.Solids),
        "area_mm2": float(shape.Area),
        "volume_mm3": float(shape.Volume),
    }
