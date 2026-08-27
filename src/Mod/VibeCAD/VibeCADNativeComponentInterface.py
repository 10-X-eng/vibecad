# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preflight, mutation, and proof for native component interfaces."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping

from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeAssemblyIdentity import assign_persistent_identity
from VibeCADNativeTargets import (
    NativeObjectRef,
    document_uid,
    object_identity,
    object_reference,
    resolve_object,
)
from VibeCADReferenceContracts import (
    NativeInterfaceSpec,
    PROP_NATIVE_INTERFACE,
    PROP_NATIVE_INTERFACE_ALLOWED_JOINTS,
    PROP_NATIVE_INTERFACE_COMPATIBILITY,
    PROP_NATIVE_INTERFACE_KIND,
    PROP_NATIVE_INTERFACE_NAME,
    ReferenceContractError,
    connector_frame_placement,
    is_native_coordinate_system,
    native_interface_definitions,
    prepare_native_interface,
    publish_native_interface,
)


_INTERFACE_PROPERTIES = (
    PROP_NATIVE_INTERFACE,
    PROP_NATIVE_INTERFACE_NAME,
    PROP_NATIVE_INTERFACE_KIND,
    PROP_NATIVE_INTERFACE_ALLOWED_JOINTS,
    PROP_NATIVE_INTERFACE_COMPATIBILITY,
)
_LCS_TYPES = (
    "App::LocalCoordinateSystem",
    "PartDesign::CoordinateSystem",
    "Part::LocalCoordinateSystem",
)
MAX_COMPONENT_INTERFACE_TARGETS = 48


class NativeComponentInterfaceError(NativeMutationError):
    def __init__(self, message: str) -> None:
        super().__init__("NATIVE_COMPONENT_INVALID", message)


@dataclass(frozen=True, slots=True)
class PreparedComponentInterface:
    component_ref: NativeObjectRef
    lcs_ref: NativeObjectRef
    spec: NativeInterfaceSpec
    initial_state: tuple[tuple[bool, Any], ...]


def _copy_reference(obj: Any) -> dict[str, str]:
    return {"object_name": str(obj.Name)}


def _label(obj: Any) -> str | None:
    label = str(getattr(obj, "Label", "") or "").strip()
    return label[:160] if label and label != str(obj.Name) else None


def _published_interface(component: Any, lcs: Any) -> dict[str, Any] | None:
    for name, definition in native_interface_definitions(component).items():
        selection = dict(definition.get("selection") or {})
        if selection.get("native_lcs") != str(lcs.Name):
            continue
        connector = dict(definition.get("connector") or {})
        return {"name": name, **connector}
    return None


def read_component_interface_targets(
    document: Any,
    *,
    guard: Callable[[], None],
) -> dict[str, Any]:
    """List exact component/LCS pairs accepted by interface publication."""

    if not callable(guard):
        raise TypeError("guard must be callable")
    guard()
    objects_before = tuple(getattr(document, "Objects", ()) or ())
    targets = []
    truncated = False
    for component in objects_before:
        if str(getattr(component, "VibeCADVibeScriptProgramId", "") or ""):
            continue
        for lcs in list(getattr(component, "Group", ()) or ()):
            if not is_native_coordinate_system(lcs):
                continue
            if len(targets) >= MAX_COMPONENT_INTERFACE_TARGETS:
                truncated = True
                break
            target = {
                "component": _copy_reference(component),
                "lcs": _copy_reference(lcs),
            }
            component_label = _label(component)
            if component_label is not None:
                target["component_label"] = component_label
            lcs_label = _label(lcs)
            if lcs_label is not None:
                target["lcs_label"] = lcs_label
            published = _published_interface(component, lcs)
            if published is not None:
                target["published_interface"] = published
            targets.append(target)
        if truncated:
            break
    guard()
    if tuple(getattr(document, "Objects", ()) or ()) != objects_before:
        raise NativeComponentInterfaceError(
            "The document changed while reading component LCS resources."
        )
    return {
        "operation": "find_component_interfaces",
        "target_count": len(targets),
        "targets": targets,
        **({"truncated": True} if truncated else {}),
    }


def _object_ref(uid: str, value: Any, *, label: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeComponentInterfaceError(
            f"A component-interface {label} target is invalid."
        )
    return NativeObjectRef(uid, str(value["object_name"] or ""))


def _state(lcs: Any) -> tuple[tuple[bool, Any], ...]:
    properties = set(getattr(lcs, "PropertiesList", []) or [])
    return tuple(
        (
            name in properties,
            getattr(lcs, name, None) if name in properties else None,
        )
        for name in _INTERFACE_PROPERTIES
    )


def _desired_connector(spec: NativeInterfaceSpec) -> dict[str, Any]:
    return {
        "kind": spec.kind,
        **(
            {"allowed_joints": list(spec.allowed_joints)}
            if spec.allowed_joints
            else {}
        ),
        **({"compatibility": spec.compatibility} if spec.compatibility else {}),
    }


def _is_exact_publication(component: Any, lcs: Any, spec: NativeInterfaceSpec) -> bool:
    definition = native_interface_definitions(component).get(spec.name)
    if not isinstance(definition, Mapping):
        return False
    selection = dict(definition.get("selection") or {})
    return (
        selection.get("native_lcs") == str(lcs.Name)
        and dict(definition.get("connector") or {}) == _desired_connector(spec)
    )


def _resolve_targets(document: Any, prepared: PreparedComponentInterface):
    component = resolve_object(document, prepared.component_ref)
    lcs = resolve_object(
        document,
        prepared.lcs_ref,
        expected_types=_LCS_TYPES,
    )
    return component, lcs


def prepare_component_interface(
    document: Any,
    values: Mapping[str, Any],
) -> PreparedComponentInterface:
    if not isinstance(values, Mapping) or set(values) != {
        "component",
        "lcs",
        "name",
        "kind",
        "allowed_joints",
        "compatibility",
    }:
        raise NativeComponentInterfaceError(
            "A component-interface publication is invalid."
        )
    uid = document_uid(document)
    component_ref = _object_ref(uid, values["component"], label="component")
    lcs_ref = _object_ref(uid, values["lcs"], label="LCS")
    component = resolve_object(document, component_ref)
    lcs = resolve_object(
        document,
        lcs_ref,
        expected_types=_LCS_TYPES,
    )
    if str(getattr(component, "VibeCADVibeScriptProgramId", "") or ""):
        raise NativeComponentInterfaceError(
            "A VibeScript-owned component must declare interfaces in its source."
        )
    try:
        spec = prepare_native_interface(
            component,
            lcs,
            name=values["name"],
            kind=values["kind"],
            allowed_joints=values["allowed_joints"],
            compatibility=values["compatibility"],
        )
    except (ReferenceContractError, TypeError, ValueError) as exc:
        raise NativeComponentInterfaceError(str(exc)) from exc
    if _is_exact_publication(component, lcs, spec):
        raise NativeComponentInterfaceError(
            "That exact component interface is already published."
        )
    return PreparedComponentInterface(
        component_ref,
        lcs_ref,
        spec,
        _state(lcs),
    )


def publish_component_interface(
    document: Any,
    *,
    prepared: PreparedComponentInterface,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedComponentInterface):
        raise TypeError("prepared must be a PreparedComponentInterface")
    component, lcs = _resolve_targets(document, prepared)
    if _state(lcs) != prepared.initial_state:
        raise NativeComponentInterfaceError(
            "The exact component interface changed after preflight."
        )
    if str(getattr(component, "VibeCADVibeScriptProgramId", "") or ""):
        raise NativeComponentInterfaceError(
            "The component became VibeScript-owned after preflight."
        )
    try:
        current = prepare_native_interface(
            component,
            lcs,
            name=prepared.spec.name,
            kind=prepared.spec.kind,
            allowed_joints=prepared.spec.allowed_joints,
            compatibility=prepared.spec.compatibility,
        )
        if current != prepared.spec:
            raise NativeComponentInterfaceError(
                "The component-interface definition changed after preflight."
            )
        definition = publish_native_interface(
            component,
            lcs,
            name=current.name,
            kind=current.kind,
            allowed_joints=current.allowed_joints,
            compatibility=current.compatibility,
        )
        assign_persistent_identity(lcs, "interface")
    except NativeComponentInterfaceError:
        raise
    except (ReferenceContractError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeComponentInterfaceError(str(exc)) from exc
    return NativeMutationDraft(
        value={
            "component": component,
            "lcs": lcs,
            "spec": prepared.spec,
            "definition": definition,
            "initial_state": prepared.initial_state,
        },
        recompute_targets=(lcs, component),
        changed=(object_identity(lcs),),
    )


def verify_component_interface(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    component = draft.value["component"]
    lcs = draft.value["lcs"]
    spec = draft.value["spec"]
    if (
        document.getObject(component.Name) is not component
        or document.getObject(lcs.Name) is not lcs
        or _state(lcs) == draft.value["initial_state"]
    ):
        raise NativeComponentInterfaceError(
            "The component interface did not change its exact LCS."
        )
    definition = native_interface_definitions(component).get(spec.name)
    if not isinstance(definition, Mapping):
        raise NativeComponentInterfaceError(
            "The component interface was not published before commit."
        )
    selection = dict(definition.get("selection") or {})
    resolved = dict(definition.get("resolved") or {})
    connector = dict(definition.get("connector") or {})
    frame = resolved.get("connector_frame")
    if (
        selection != {"type": "frame", "native_lcs": str(lcs.Name)}
        or connector != _desired_connector(spec)
        or resolved.get("object") != str(component.Name)
        or list(resolved.get("subelements") or [])
        or list(resolved.get("geometry") or [])
        or not isinstance(frame, Mapping)
        or not connector_frame_placement(dict(frame)).isSame(lcs.Placement, 1.0e-12)
        or not bool(getattr(lcs, PROP_NATIVE_INTERFACE))
        or str(getattr(lcs, PROP_NATIVE_INTERFACE_NAME)) != spec.name
        or str(getattr(lcs, PROP_NATIVE_INTERFACE_KIND)) != spec.kind
        or json.loads(str(getattr(lcs, PROP_NATIVE_INTERFACE_ALLOWED_JOINTS)))
        != list(spec.allowed_joints)
        or str(getattr(lcs, PROP_NATIVE_INTERFACE_COMPATIBILITY))
        != spec.compatibility
    ):
        raise NativeComponentInterfaceError(
            "The component interface failed its exact publication postcondition."
        )
    return {
        "verified": True,
        "component": object_reference(component),
        "interface": {
            "name": spec.name,
            "lcs": object_reference(lcs),
            "kind": spec.kind,
            "allowed_joints": list(spec.allowed_joints),
            "compatibility": spec.compatibility,
            "origin_mm": list(frame["origin_mm"]),
            "axis_direction": list(frame["axis_direction"]),
            "x_direction": list(frame["x_direction"]),
        },
    }
