# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact reusable-definition references for Native Design features."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeTargets import (
    NativeObjectRef,
    NativeTargetError,
    object_reference,
    resolve_object,
)


_PROFILE_REGION = re.compile(r"^InternalFace[1-9][0-9]*$")
_EDGE = re.compile(r"^Edge[1-9][0-9]*$")
_FACE = re.compile(r"^Face[1-9][0-9]*$")
_AXIS = re.compile(
    r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|Edge[1-9][0-9]*|Face[1-9][0-9]*)$"
)


@dataclass(frozen=True, slots=True)
class DesignLinkSpec:
    object_ref: NativeObjectRef
    subelements: tuple[str, ...]


def sketch_axis_count(sketch: Any) -> int:
    """Return the public Python count of construction axes for a sketch."""
    raw_count = getattr(sketch, "AxisCount", None)
    if raw_count is None:
        getter = getattr(sketch, "getAxisCount", None)
        raw_count = getter() if callable(getter) else 0
    return int(raw_count)


def design_link_from_mapping(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    field: str,
    subelement_kind: str,
    minimum: int,
    maximum: int,
) -> DesignLinkSpec:
    allowed_fields = {"object_name", field}
    if (
        not isinstance(value, Mapping)
        or "object_name" not in value
        or not set(value) <= allowed_fields
        or (minimum and field not in value)
    ):
        raise NativeModelError("A Design geometry reference is invalid.")
    raw = value.get(field, [])
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise NativeModelError("A Design geometry reference has the wrong element count.")
    patterns = {
        "profile": _PROFILE_REGION,
        "edge": _EDGE,
        "face": _FACE,
        "axis": _AXIS,
    }
    pattern = patterns[subelement_kind]
    subelements = tuple(str(name) for name in raw)
    if len(subelements) != len(set(subelements)) or any(
        pattern.fullmatch(name) is None for name in subelements
    ):
        raise NativeModelError("A Design geometry reference contains invalid elements.")
    return DesignLinkSpec(
        NativeObjectRef(document_uid, str(value["object_name"])),
        subelements,
    )


def preflight_design_link(
    document: Any,
    spec: DesignLinkSpec,
    *,
    expected_types: tuple[str, ...] = (),
) -> Any:
    if not isinstance(spec, DesignLinkSpec):
        raise TypeError("spec must be a DesignLinkSpec")
    source = resolve_object(document, spec.object_ref, expected_types=expected_types)
    get_subobject = getattr(source, "getSubObject", None)
    for name in spec.subelements:
        if name in {"H_Axis", "V_Axis", "N_Axis"} or name.startswith("Axis"):
            is_derived = getattr(source, "isDerivedFrom", None)
            if callable(is_derived) and is_derived("Part::Part2DObject"):
                if name.startswith("Axis"):
                    index = int(name[4:])
                    count = sketch_axis_count(source)
                    if index >= count:
                        raise NativeTargetError(
                            "An exact Design sketch axis no longer exists.",
                            exact_target={
                                **spec.object_ref.summary(),
                                "subelement": name,
                            },
                        )
                continue
        if name.startswith(("Edge", "Face")):
            shape = getattr(source, "Shape", None)
            get_element = getattr(shape, "getElement", None)
            try:
                element = get_element(name) if callable(get_element) else None
            except Exception as exc:
                raise NativeTargetError(
                    "An exact Design subelement no longer exists.",
                    exact_target={**spec.object_ref.summary(), "subelement": name},
                ) from exc
            if element is None or bool(getattr(element, "isNull", lambda: False)()):
                raise NativeTargetError(
                    "An exact Design subelement no longer exists.",
                    exact_target={**spec.object_ref.summary(), "subelement": name},
                )
            continue
        try:
            subobject = get_subobject(name) if callable(get_subobject) else None
        except Exception as exc:
            raise NativeTargetError(
                "An exact Design subelement no longer exists.",
                exact_target={**spec.object_ref.summary(), "subelement": name},
            ) from exc
        if subobject is None:
            raise NativeTargetError(
                "An exact Design subelement no longer exists.",
                exact_target={**spec.object_ref.summary(), "subelement": name},
            )
    return source


def resolve_definition_link(
    operation: Any,
    spec: DesignLinkSpec,
    *,
    expected_types: tuple[str, ...] = (),
) -> tuple[Any, list[str]]:
    import PartDesign

    source = preflight_design_link(
        operation.Document,
        spec,
        expected_types=expected_types,
    )
    resolved, canonical = PartDesign.resolveDesignDefinitionSubelementReference(
        operation,
        source,
        list(spec.subelements),
    )
    names = [str(name) for name in list(canonical or [])]
    if resolved is None or getattr(resolved, "Document", None) is not operation.Document:
        raise NativeModelError("A Design geometry reference did not resolve in History.")
    return resolved, names


def link_summary(value: tuple[Any, list[str]]) -> dict[str, Any]:
    obj, subelements = value
    return {
        "object": object_reference(obj),
        "subelements": [str(name) for name in subelements if str(name)],
    }


def property_link_summary(value: Any) -> dict[str, Any]:
    obj, subelements = value
    return link_summary((obj, [str(name) for name in list(subelements or [])]))


def property_link_list_summary(value: Any) -> list[dict[str, Any]]:
    return [
        link_summary((obj, [str(name) for name in list(subelements or [])]))
        for obj, subelements in list(value or [])
    ]
