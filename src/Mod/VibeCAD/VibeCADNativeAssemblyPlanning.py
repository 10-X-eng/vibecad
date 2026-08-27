# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind accepted Assembly plans to the ordinary Native joint mutation owner."""

from __future__ import annotations

from typing import Any, Mapping

from tool_impl.assembly_planning import (
    AssemblyPlanningError,
    accept_joint_proposal,
    normalize_scenario,
)
from VibeCADNativeAssemblyIdentity import (
    NativeAssemblyIdentityError,
    read_persistent_identity,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeState import NativeCallTicket
import VibeCADReferenceContracts as reference_contracts


_NATIVE_PLANNED_JOINTS = frozenset({
    "fixed", "revolute", "cylindrical", "slider", "ball",
})


def _identity_objects(document: Any, expected_kind: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for obj in list(getattr(document, "Objects", ()) or ()):
        try:
            identity = read_persistent_identity(obj)
        except NativeAssemblyIdentityError as exc:
            raise AssemblyPlanningError(str(exc)) from exc
        if identity is None or identity["kind"] != expected_kind:
            continue
        persistent_id = identity["persistent_id"]
        if persistent_id in result:
            raise AssemblyPlanningError(
                f"The live document repeats {expected_kind} identity {persistent_id!r}."
            )
        result[persistent_id] = obj
    return result


def _interface_name(component: Any, interface_id: str, record: Mapping[str, Any]) -> str:
    claimed = str(record.get("name") or record.get("interface_name") or "").strip()
    if not claimed:
        raise AssemblyPlanningError(
            "Native proposal acceptance requires each interface record to carry its published name."
        )
    matches = []
    for resource in list(getattr(component, "Group", ()) or ()):
        try:
            identity = read_persistent_identity(resource)
        except NativeAssemblyIdentityError as exc:
            raise AssemblyPlanningError(str(exc)) from exc
        if (
            identity is not None
            and identity["kind"] == "interface"
            and identity["persistent_id"] == interface_id
        ):
            matches.append(resource)
    if len(matches) != 1:
        raise AssemblyPlanningError(
            f"Interface identity {interface_id!r} must resolve exactly once on its occurrence."
        )
    live_name = str(
        getattr(matches[0], reference_contracts.PROP_NATIVE_INTERFACE_NAME, "") or ""
    ).strip()
    if not live_name or live_name != claimed:
        raise AssemblyPlanningError(
            f"Interface identity {interface_id!r} no longer has its graph-bound published name."
        )
    return live_name


def _native_owner(
    runtime: NativeAssemblyJointRuntime,
    ticket: NativeCallTicket,
    scenario: Mapping[str, Any],
):
    normalized = normalize_scenario(scenario)
    document = runtime._context.document
    occurrences = _identity_objects(document, "occurrence")
    interfaces = {
        item["persistent_id"]: item for item in normalized["interfaces"]
    }

    def owner(proposal: dict[str, Any]) -> Mapping[str, Any]:
        joint_kind = str(proposal.get("joint_kind") or "")
        if joint_kind not in _NATIVE_PLANNED_JOINTS:
            raise AssemblyPlanningError(
                f"Native planned acceptance does not yet support {joint_kind!r}."
            )
        endpoint_values = []
        for interface_id in proposal.get("interface_ids", ()):
            interface = interfaces.get(str(interface_id))
            if interface is None:
                raise AssemblyPlanningError("A proposed interface is absent from the current graph.")
            occurrence_id = str(interface.get("occurrence_id") or "")
            component = occurrences.get(occurrence_id)
            if component is None:
                raise AssemblyPlanningError(
                    f"Occurrence identity {occurrence_id!r} is absent from the live document."
                )
            name = _interface_name(component, str(interface_id), interface)
            try:
                live = reference_contracts.resolve_component_interface(component, name)
            except reference_contracts.ReferenceContractError as exc:
                raise AssemblyPlanningError(str(exc)) from exc
            expected_status = str(
                dict(interface.get("geometry_binding") or {}).get("status")
                or "unrecorded"
            )
            live_status = str(
                dict(dict(live.get("resolved") or {}).get("geometry_binding") or {}).get("status")
                or "unrecorded"
            )
            if live_status != expected_status or live_status in {"stale", "invalid"}:
                raise AssemblyPlanningError(
                    f"Interface {interface_id!r} geometry currentness changed before acceptance."
                )
            endpoint_values.append({
                "component": str(component.Name),
                "connector_type": "interface",
                "connector": name,
            })
        if len(endpoint_values) != 2:
            raise AssemblyPlanningError("A planned Native joint requires exactly two interfaces.")
        return runtime.mutate_joint(
            {
                "operation": "create",
                "joint_type": joint_kind,
                "first": endpoint_values[0],
                "second": endpoint_values[1],
                "label": f"VibeCAD {joint_kind.replace('_', ' ').title()} Joint",
            },
            ticket=ticket,
        )

    return owner


def accept_joint_proposal_native(
    scenario: Mapping[str, Any],
    proposals: Mapping[str, Any],
    proposal_id: str,
    *,
    runtime: NativeAssemblyJointRuntime,
    ticket: NativeCallTicket,
) -> dict[str, Any]:
    """Revalidate and execute one proposal through the live Native Assembly owner."""

    if not isinstance(runtime, NativeAssemblyJointRuntime):
        raise AssemblyPlanningError("A Native Assembly joint runtime is required.")
    if not isinstance(ticket, NativeCallTicket):
        raise AssemblyPlanningError("A Native Assembly call ticket is required.")
    if ticket.capability_name != ASSEMBLY_JOINT_CAPABILITY_NAME:
        raise AssemblyPlanningError("The call ticket is not authorized for Assembly joints.")
    if ticket.document_uid != runtime._context.document_uid:
        raise AssemblyPlanningError("The call ticket belongs to a different document.")
    return accept_joint_proposal(
        scenario,
        proposals,
        proposal_id,
        assembly_owner=_native_owner(runtime, ticket, scenario),
    )
