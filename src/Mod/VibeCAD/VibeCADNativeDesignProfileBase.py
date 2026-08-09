# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact-profile setup for current Design history operations."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeDesignReferences import (
    DesignLinkSpec,
    link_summary,
    preflight_design_link,
    property_link_summary,
    resolve_definition_link,
)
from VibeCADNativeDesignResults import (
    DesignResultSpec,
    create_design_operation,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


SpecificConfigure = Callable[[Any], Mapping[str, Any]]
SpecificVerify = Callable[[Any, Mapping[str, Any]], Mapping[str, Any]]


def set_exact_link(
    operation: Any,
    property_name: str,
    spec: DesignLinkSpec,
    *,
    expected_types: tuple[str, ...] = (),
) -> dict[str, Any]:
    resolved = resolve_definition_link(
        operation,
        spec,
        expected_types=expected_types,
    )
    setattr(
        operation,
        property_name,
        (resolved[0], resolved[1]) if resolved[1] else resolved[0],
    )
    return link_summary(resolved)


def set_exact_axis_link(
    operation: Any,
    property_name: str,
    spec: DesignLinkSpec,
) -> dict[str, Any]:
    if spec.subelements[0] in {"H_Axis", "V_Axis", "N_Axis"}:
        source = preflight_design_link(
            operation.Document,
            spec,
            expected_types=("Part::Part2DObject",),
        )
        resolved = (source, list(spec.subelements))
        setattr(operation, property_name, resolved)
        return link_summary(resolved)
    return set_exact_link(operation, property_name, spec)


def set_exact_link_list(
    operation: Any,
    property_name: str,
    specs: tuple[DesignLinkSpec, ...],
    *,
    expected_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    resolved = [
        resolve_definition_link(
            operation,
            spec,
            expected_types=expected_types,
        )
        for spec in specs
    ]
    setattr(
        operation,
        property_name,
        [obj if not names else (obj, names) for obj, names in resolved],
    )
    return [link_summary(value) for value in resolved]


def verify_exact_link(
    operation: Any,
    property_name: str,
    expected: Mapping[str, Any],
) -> None:
    actual = property_link_summary(getattr(operation, property_name))
    if actual != dict(expected):
        raise NativeModelError(f"Design {property_name} changed before commit.")


def create_profile_design_operation(
    document: Any,
    *,
    type_id: str,
    base_name: str,
    label: str,
    profile_spec: DesignLinkSpec,
    result_spec: DesignResultSpec,
    configure_specific: SpecificConfigure,
    verify_specific: SpecificVerify,
    configure_after_targets: bool = False,
) -> NativeMutationDraft:
    def configure(operation: Any) -> Mapping[str, Any]:
        profile = set_exact_link(
            operation,
            "Profile",
            profile_spec,
            expected_types=("Part::Part2DObject",),
        )
        feature = configure_specific(operation)
        if not isinstance(feature, Mapping):
            raise NativeModelError("A profile Design feature configuration is invalid.")
        return {"profile": profile, "feature": dict(feature)}

    def verify(operation: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        verify_exact_link(operation, "Profile", expected["profile"])
        feature = verify_specific(operation, expected["feature"])
        if not isinstance(feature, Mapping):
            raise NativeModelError("A profile Design feature verifier is invalid.")
        return {"profile": dict(expected["profile"]), **dict(feature)}

    return create_design_operation(
        document,
        type_id=type_id,
        base_name=base_name,
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=verify,
        configure_after_targets=configure_after_targets,
    )
