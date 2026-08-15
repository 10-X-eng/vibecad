# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact ordered-profile implementation for Design Loft."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDesignProfileBase import (
    create_profile_design_operation,
    set_exact_link_list,
)
from VibeCADNativeDesignProfileInput import preflight_profile_inputs, profile_spec
from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    property_link_list_summary,
)
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


@dataclass(frozen=True, slots=True)
class DesignLoftSpec:
    profile: DesignLinkSpec
    sections: tuple[DesignLinkSpec, ...]
    ruled: bool
    closed: bool


def prepare_design_loft(
    document_uid: str,
    values: Mapping[str, Any],
) -> DesignLoftSpec:
    raw_sections = values["sections"]
    if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= 32:
        raise NativeModelError("Design Loft requires 1 to 32 ordered sections.")
    profile = profile_spec(document_uid, values["profile"])
    sections = tuple(profile_spec(document_uid, value) for value in raw_sections)
    keys = [
        (item.object_ref.object_name, item.subelements)
        for item in (profile, *sections)
    ]
    if len(keys) != len(set(keys)):
        raise NativeModelError("Design Loft repeats an exact section reference.")
    closed = bool(values["closed"])
    if closed and len(sections) < 2:
        raise NativeModelError("A closed Design Loft requires at least three profiles.")
    return DesignLoftSpec(profile, sections, bool(values["ruled"]), closed)


def preflight_design_loft(document: Any, spec: DesignLoftSpec) -> None:
    preflight_profile_inputs(document, spec.profile, *spec.sections)


def create_design_loft(
    document: Any,
    *,
    label: str,
    spec: DesignLoftSpec,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        sections = set_exact_link_list(
            operation,
            "Sections",
            spec.sections,
            expected_types=("Part::Part2DObject",),
        )
        operation.Ruled = spec.ruled
        operation.Closed = spec.closed
        return {
            "sections": sections,
            "ruled": spec.ruled,
            "closed": spec.closed,
        }

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        sections = property_link_list_summary(operation.Sections)
        if (
            sections != expected["sections"]
            or bool(operation.Ruled) is not expected["ruled"]
            or bool(operation.Closed) is not expected["closed"]
        ):
            raise NativeModelError("Design Loft parameters changed before commit.")
        return {
            "sections": sections,
            "ruled": bool(operation.Ruled),
            "closed": bool(operation.Closed),
        }

    return create_profile_design_operation(
        document,
        type_id="PartDesign::DesignLoft",
        base_name="Loft",
        label=label,
        profile_spec=spec.profile,
        result_spec=result_spec,
        configure_specific=configure,
        verify_specific=verify,
    )
