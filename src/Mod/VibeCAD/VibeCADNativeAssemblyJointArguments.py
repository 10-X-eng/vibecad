# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict shared argument decoding for Native Assembly joints."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeAssemblyJointConnectors import JointConnectorSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativePartPrimitives import part_placement_from_mapping
from VibeCADNativeTargets import NativeObjectRef


def joint_count(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
    maximum: int = 100_000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise error_type(
            f"{field} must be an integer from 0 through {maximum}."
        )
    return value


def joint_bool(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> bool:
    if type(value) is not bool:
        raise error_type(f"{field} must be true or false.")
    return value


def joint_object_ref(
    document_uid: str,
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise error_type(
            f"{field} must be one exact current-document object reference."
        )
    return NativeObjectRef(document_uid, str(value.get("object_name") or ""))


def joint_placement(
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> Any:
    try:
        return part_placement_from_mapping(value)
    except (NativeModelError, KeyError, TypeError, ValueError) as exc:
        raise error_type(
            f"{field} must contain a finite origin and non-zero axis rotation."
        ) from exc


def joint_connector(
    document_uid: str,
    value: Any,
    field: str,
    error_type: type[RuntimeError],
) -> JointConnectorSpec:
    required = {
        "component",
        "element_path",
        "anchor_path",
        "offset",
        "expected_component_placement",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise error_type(
            f"{field} must contain one exact component-rooted connector."
        )
    element_path = value["element_path"]
    anchor_path = value["anchor_path"]
    if not isinstance(element_path, str) or not isinstance(anchor_path, str):
        raise error_type(f"{field} connector paths must be strings.")
    return JointConnectorSpec(
        component_ref=joint_object_ref(
            document_uid,
            value["component"],
            f"{field}.component",
            error_type,
        ),
        element_path=element_path,
        anchor_path=anchor_path,
        offset=joint_placement(value["offset"], f"{field}.offset", error_type),
        expected_component_placement=joint_placement(
            value["expected_component_placement"],
            f"{field}.expected_component_placement",
            error_type,
        ),
    )


def joint_label(value: Any, error_type: type[RuntimeError]) -> str:
    if not isinstance(value, str):
        raise error_type("label must be text.")
    label = value.strip()
    if not label or len(label) > 160:
        raise error_type(
            "label must contain 1 to 160 non-whitespace characters."
        )
    return label


def joint_limit(
    value: Any,
    field: str,
    value_key: str,
    converter: Callable[[Any, str], float],
    error_type: type[RuntimeError],
) -> tuple[bool, float]:
    if not isinstance(value, Mapping) or set(value) != {"enabled", value_key}:
        raise error_type(f"{field} must contain enabled and {value_key}.")
    return (
        joint_bool(value["enabled"], f"{field}.enabled", error_type),
        converter(value[value_key], f"{field}.{value_key}"),
    )


def joint_limit_pair(
    value: Any,
    field: str,
    value_key: str,
    converter: Callable[[Any, str], float],
    error_type: type[RuntimeError],
) -> tuple[bool, float, bool, float]:
    if not isinstance(value, Mapping) or set(value) != {"minimum", "maximum"}:
        raise error_type(
            f"{field} must contain exact minimum and maximum states."
        )
    minimum_enabled, minimum = joint_limit(
        value["minimum"],
        f"{field}.minimum",
        value_key,
        converter,
        error_type,
    )
    maximum_enabled, maximum = joint_limit(
        value["maximum"],
        f"{field}.maximum",
        value_key,
        converter,
        error_type,
    )
    return minimum_enabled, minimum, maximum_enabled, maximum
