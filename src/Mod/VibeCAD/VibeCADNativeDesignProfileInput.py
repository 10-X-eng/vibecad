# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict parsing and preflight for profile-feature geometry references."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    design_link_from_mapping,
    preflight_design_link,
)
from VibeCADNativeModelErrors import NativeModelError


def profile_spec(document_uid: str, value: Mapping[str, Any]) -> DesignLinkSpec:
    return design_link_from_mapping(
        document_uid,
        value,
        field="regions",
        subelement_kind="profile",
        minimum=0,
        maximum=64,
    )


def axis_spec(document_uid: str, value: Mapping[str, Any]) -> DesignLinkSpec:
    return design_link_from_mapping(
        document_uid,
        value,
        field="subelements",
        subelement_kind="axis",
        minimum=1,
        maximum=1,
    )


def face_spec(document_uid: str, value: Mapping[str, Any]) -> DesignLinkSpec:
    return design_link_from_mapping(
        document_uid,
        value,
        field="subelements",
        subelement_kind="face",
        minimum=1,
        maximum=1,
    )


def shape_spec(document_uid: str, value: Mapping[str, Any]) -> DesignLinkSpec:
    return design_link_from_mapping(
        document_uid,
        value,
        field="subelements",
        subelement_kind="face",
        minimum=0,
        maximum=64,
    )


def path_spec(document_uid: str, value: Mapping[str, Any]) -> DesignLinkSpec:
    return design_link_from_mapping(
        document_uid,
        value,
        field="subelements",
        subelement_kind="edge",
        minimum=1,
        maximum=64,
    )


def vector_from_mapping(value: Mapping[str, Any], *, label: str) -> tuple[float, float, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeModelError(f"The {label} vector is invalid.")
    result = tuple(float(value[name]) for name in ("x", "y", "z"))
    if not all(math.isfinite(number) for number in result):
        raise NativeModelError(f"The {label} vector must contain finite numbers.")
    if math.sqrt(sum(number * number for number in result)) < 1.0e-12:
        raise NativeModelError(f"The {label} vector must be non-zero.")
    return result


def preflight_profile_inputs(
    document: Any,
    profile: DesignLinkSpec,
    *references: DesignLinkSpec | None,
) -> None:
    preflight_design_link(
        document,
        profile,
        expected_types=("Part::Part2DObject",),
    )
    for reference in references:
        if reference is not None:
            preflight_design_link(document, reference)
