# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, reference resolution, execution, and verification for Design Draft."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from VibeCADNativeDesignBodies import is_valid_solid_shape
from VibeCADNativeDesignDressupTargets import (
    DesignDressupSelection,
    DesignDressupTarget,
    dressup_target_elements,
    preflight_dressup_selection,
    prepare_dressup_selection,
)
from VibeCADNativeDesignResults import DesignResultSpec, create_design_operation
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_SUBELEMENT = re.compile(r"^(Face|Edge)[1-9][0-9]*$")


@dataclass(frozen=True, slots=True)
class DesignDraftReference:
    kind: str
    object: NativeObjectRef | None
    subelement: str | None


@dataclass(frozen=True, slots=True)
class DesignDraftSpec:
    targets: tuple[DesignDressupTarget, ...]
    angle_degrees: float
    reversed: bool
    neutral_plane: DesignDraftReference
    pull_direction: DesignDraftReference


def _reference(
    document_uid: str,
    value: Any,
    *,
    name: str,
    allowed_subelements: frozenset[str],
) -> DesignDraftReference:
    if not isinstance(value, Mapping) or "kind" not in value:
        raise NativeModelError(f"A Draft {name} definition is invalid.")
    kind = str(value["kind"])
    expected = {
        "automatic": {"kind"},
        "object": {"kind", "object_name"},
        "subelement": {"kind", "object_name", "subelement"},
    }.get(kind)
    if expected is None or set(value) != expected:
        raise NativeModelError(f"A Draft {name} definition is invalid.")
    if kind == "automatic":
        return DesignDraftReference(kind, None, None)
    object_ref = NativeObjectRef(document_uid, str(value["object_name"]))
    if kind == "object":
        return DesignDraftReference(kind, object_ref, None)
    subelement = str(value["subelement"])
    if _SUBELEMENT.fullmatch(subelement) is None or not any(
        subelement.startswith(prefix) for prefix in allowed_subelements
    ):
        allowed = " or ".join(f"{item}N" for item in sorted(allowed_subelements))
        raise NativeModelError(f"A Draft {name} requires an exact {allowed} reference.")
    return DesignDraftReference(kind, object_ref, subelement)


def prepare_design_draft(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignDraftSpec:
    selection = prepare_dressup_selection(
        document_uid,
        values.get("selection"),
        operation="Draft",
        allow_all_edges=False,
        allowed_subelement_types=frozenset({"Face"}),
    )
    raw_angle = values.get("angle_degrees")
    if isinstance(raw_angle, bool):
        raise NativeModelError("A Draft angle must be a number.")
    try:
        angle = float(raw_angle)
    except (TypeError, ValueError) as exc:
        raise NativeModelError("A Draft angle must be a number.") from exc
    if not math.isfinite(angle) or not 0.0 < angle < 90.0:
        raise NativeModelError(
            "A Draft angle must be finite, greater than 0, and less than 90 degrees."
        )
    reversed_value = values.get("reversed")
    if not isinstance(reversed_value, bool):
        raise NativeModelError("Draft reversed must be boolean.")
    neutral = _reference(
        document_uid,
        values.get("neutral_plane"),
        name="neutral plane",
        allowed_subelements=frozenset({"Edge", "Face"}),
    )
    pull = _reference(
        document_uid,
        values.get("pull_direction"),
        name="pull direction",
        allowed_subelements=frozenset({"Edge"}),
    )
    if neutral.subelement and neutral.subelement.startswith("Edge") and pull.object is None:
        raise NativeModelError(
            "A Draft neutral-plane edge requires an explicit pull direction."
        )
    return DesignDraftSpec(
        selection.targets,
        angle,
        reversed_value,
        neutral,
        pull,
    )


def _is_derived(obj: Any, type_id: str) -> bool:
    if str(getattr(obj, "TypeId", "")) == type_id:
        return True
    check = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(check(type_id)) if callable(check) else False
    except Exception:
        return False


def _reference_object(
    document: Any,
    reference: DesignDraftReference,
    *,
    name: str,
) -> Any | None:
    if reference.object is None:
        return None
    obj = resolve_object(document, reference.object)
    if reference.subelement is None:
        accepted = (
            ("PartDesign::Plane", "App::Plane", "Part::Part2DObject")
            if name == "neutral plane"
            else ("PartDesign::Line", "App::Line")
        )
        if not any(_is_derived(obj, type_id) for type_id in accepted):
            raise NativeModelError(f"The Draft {name} object has an invalid type.")
        return obj
    if not (
        _is_derived(obj, "Part::Feature")
        or _is_derived(obj, "PartDesign::Body")
    ):
        raise NativeModelError(f"The Draft {name} source must be a shape feature.")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        raise NativeModelError(f"The Draft {name} source has no valid shape.")
    try:
        element = shape.getElement(reference.subelement)
    except Exception as exc:
        raise NativeModelError(
            f"Draft {name} {reference.object.object_name}.{reference.subelement} no longer exists."
        ) from exc
    expected_type = "Face" if reference.subelement.startswith("Face") else "Edge"
    if str(getattr(element, "ShapeType", "")) != expected_type:
        raise NativeModelError(f"The Draft {name} reference changed geometric type.")
    if expected_type == "Face":
        geometry = getattr(element, "Surface", None)
        if str(getattr(geometry, "TypeId", "")) != "Part::GeomPlane":
            raise NativeModelError("A Draft neutral-plane face must be planar.")
    else:
        geometry = getattr(element, "Curve", None)
        if str(getattr(geometry, "TypeId", "")) != "Part::GeomLine":
            raise NativeModelError(f"A Draft {name} edge must be linear.")
    return obj


def preflight_design_draft(document: Any, spec: DesignDraftSpec) -> tuple[Any, ...]:
    bodies = preflight_dressup_selection(
        document,
        DesignDressupSelection(spec.targets, False),
        operation="Draft",
    )
    _reference_object(document, spec.neutral_plane, name="neutral plane")
    _reference_object(document, spec.pull_direction, name="pull direction")
    return bodies


def _resolved_link(
    operation: Any,
    reference: DesignDraftReference,
    *,
    name: str,
) -> Any:
    import PartDesign

    source = _reference_object(operation.Document, reference, name=name)
    if source is None:
        return None
    requested = [reference.subelement] if reference.subelement else []
    resolved, canonical = PartDesign.resolveDesignDefinitionSubelementReference(
        operation,
        source,
        requested,
    )
    names = [str(value) for value in list(canonical or []) if str(value)]
    if resolved is None or getattr(resolved, "Document", None) is not operation.Document:
        raise NativeModelError(f"The Draft {name} did not resolve in current History.")
    if reference.subelement and len(names) != 1:
        raise NativeModelError(f"The Draft {name} did not resolve one exact subelement.")
    if not reference.subelement and names:
        raise NativeModelError(f"The Draft {name} object resolved unexpected geometry.")
    return resolved, names


def _link_identity(value: Any) -> tuple[str | None, tuple[str, ...]]:
    if not value:
        return None, ()
    if isinstance(value, tuple):
        obj = value[0] if value else None
        raw = value[1] if len(value) > 1 else ()
    else:
        obj = value
        raw = ()
    return (
        str(getattr(obj, "Name", "")) or None,
        tuple(str(item) for item in list(raw or ()) if str(item)),
    )


def _placement_signature(value: Any) -> tuple[float, ...]:
    return (
        float(value.Base.x),
        float(value.Base.y),
        float(value.Base.z),
        *(float(component) for component in value.Rotation.Q),
    )


def _reference_summary(
    reference: DesignDraftReference,
    resolved: tuple[str | None, tuple[str, ...]],
) -> dict[str, Any]:
    if reference.object is None:
        return {"mode": "automatic"}
    object_name, subelements = resolved
    result: dict[str, Any] = {"object_name": object_name}
    if subelements:
        result["subelement"] = subelements[0]
    return result


def _verify_draft(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    spec: DesignDraftSpec = expected["spec"]
    offsets, elements = dressup_target_elements(spec.targets)
    angle = float(getattr(operation.Angle, "Value", operation.Angle))
    base = getattr(operation, "Base", None)
    linked_base = base[0] if isinstance(base, tuple) and base else base
    output_shapes = list(operation.OutputShapes)
    neutral_link = _link_identity(operation.NeutralPlane)
    pull_link = _link_identity(operation.PullDirection)
    if (
        not math.isclose(angle, spec.angle_degrees, rel_tol=1.0e-9, abs_tol=1.0e-7)
        or bool(operation.Reversed) is not spec.reversed
        or neutral_link != expected["neutral_link"]
        or pull_link != expected["pull_link"]
        or _placement_signature(operation.NeutralPlaneFrame) != expected["neutral_frame"]
        or _placement_signature(operation.PullDirectionFrame) != expected["pull_frame"]
        or list(operation.TargetElementOffsets) != offsets
        or list(operation.TargetElements) != elements
        or linked_base is not None
        or operation.BaseFeature is not None
        or list(operation.InputBodyIds) != list(operation.OutputBodyIds)
        or list(operation.OutputPreviousInputIndices) != list(range(len(spec.targets)))
        or len(operation.InputStates) != len(spec.targets)
        or len(output_shapes) != len(spec.targets)
    ):
        raise NativeModelError("The Design Draft controls changed before commit.")
    if any(not is_valid_solid_shape(shape) for shape in output_shapes):
        raise NativeModelError("The Design Draft produced an invalid Body result.")
    return {
        "angle_degrees": angle,
        "reversed": spec.reversed,
        "target_count": len(spec.targets),
        "selected_face_count": len(elements),
        "neutral_plane": _reference_summary(spec.neutral_plane, neutral_link),
        "pull_direction": _reference_summary(spec.pull_direction, pull_link),
    }


def create_design_draft(
    document: Any,
    *,
    label: str,
    spec: DesignDraftSpec,
) -> NativeMutationDraft:
    offsets, elements = dressup_target_elements(spec.targets)
    result_spec = DesignResultSpec(
        "modify",
        tuple(target.body for target in spec.targets),
        None,
    )

    def configure(operation: Any) -> Mapping[str, Any]:
        operation.Angle = spec.angle_degrees
        operation.Reversed = spec.reversed
        operation.NeutralPlane = _resolved_link(
            operation,
            spec.neutral_plane,
            name="neutral plane",
        )
        operation.PullDirection = _resolved_link(
            operation,
            spec.pull_direction,
            name="pull direction",
        )
        operation.TargetElementOffsets = offsets
        operation.TargetElements = elements
        return {
            "spec": spec,
            "neutral_link": _link_identity(operation.NeutralPlane),
            "pull_link": _link_identity(operation.PullDirection),
            "neutral_frame": _placement_signature(operation.NeutralPlaneFrame),
            "pull_frame": _placement_signature(operation.PullDirectionFrame),
        }

    return create_design_operation(
        document,
        type_id="PartDesign::DesignDraft",
        base_name="Draft",
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=_verify_draft,
        configure_after_targets=True,
    )
