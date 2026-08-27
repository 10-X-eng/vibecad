# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADAssemblyPlanning import (
    AssemblyPlanningError,
    SCENARIO_SCHEMA,
    accept_joint_proposal_native,
    propose_joints,
)
from VibeCADNativeAssemblyIdentity import (
    IDENTITY_KIND_PROPERTY,
    IDENTITY_PROPERTY,
    IDENTITY_SCHEMA,
    IDENTITY_SCHEMA_PROPERTY,
)
from VibeCADNativeAssemblyJointBindings import ASSEMBLY_JOINT_CAPABILITY_NAME
from VibeCADNativeAssemblyJointRuntime import NativeAssemblyJointRuntime
from VibeCADNativeState import NativeCallTicket
import VibeCADReferenceContracts as reference_contracts


_OCCURRENCE_A = "11111111-1111-4111-8111-111111111111"
_OCCURRENCE_B = "22222222-2222-4222-8222-222222222222"
_INTERFACE_A = "33333333-3333-4333-8333-333333333333"
_INTERFACE_B = "44444444-4444-4444-8444-444444444444"


def _identity_object(name: str, persistent_id: str, kind: str):
    return SimpleNamespace(
        Name=name,
        PropertiesList=[
            IDENTITY_PROPERTY,
            IDENTITY_KIND_PROPERTY,
            IDENTITY_SCHEMA_PROPERTY,
        ],
        **{
            IDENTITY_PROPERTY: persistent_id,
            IDENTITY_KIND_PROPERTY: kind,
            IDENTITY_SCHEMA_PROPERTY: IDENTITY_SCHEMA,
        },
    )


def _occurrence(name: str, persistent_id: str, interface_id: str, interface_name: str):
    occurrence = _identity_object(name, persistent_id, "occurrence")
    interface = _identity_object(f"{name}LCS", interface_id, "interface")
    setattr(interface, reference_contracts.PROP_NATIVE_INTERFACE_NAME, interface_name)
    occurrence.Group = [interface]
    return occurrence


def _scenario() -> dict:
    return {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": "native-live-owner",
        "occurrences": [
            {"persistent_id": _OCCURRENCE_A},
            {"persistent_id": _OCCURRENCE_B},
        ],
        "interfaces": [
            {
                "persistent_id": _INTERFACE_A,
                "occurrence_id": _OCCURRENCE_A,
                "name": "MountA",
                "kind": "axis",
                "compatibility": "mount-v1",
                "allowed_joints": ["revolute"],
                "geometry_binding": {"status": "current"},
            },
            {
                "persistent_id": _INTERFACE_B,
                "occurrence_id": _OCCURRENCE_B,
                "name": "MountB",
                "kind": "axis",
                "compatibility": "mount-v1",
                "allowed_joints": ["revolute"],
                "geometry_binding": {"status": "current"},
            },
        ],
        "joints": [],
    }


def _runtime():
    document = SimpleNamespace(
        Uid="native-planning-document",
        Objects=[
            _occurrence("OccurrenceA", _OCCURRENCE_A, _INTERFACE_A, "MountA"),
            _occurrence("OccurrenceB", _OCCURRENCE_B, _INTERFACE_B, "MountB"),
        ],
    )
    runtime = object.__new__(NativeAssemblyJointRuntime)
    runtime._context = SimpleNamespace(
        document=document,
        document_uid=document.Uid,
    )
    ticket = NativeCallTicket(
        document.Uid,
        ASSEMBLY_JOINT_CAPABILITY_NAME,
        7,
        "native-planning-ticket",
    )
    return runtime, ticket


def test_native_acceptance_resolves_persisted_occurrences_and_uses_joint_runtime(
    monkeypatch,
) -> None:
    source = _scenario()
    proposals = propose_joints(source)
    runtime, ticket = _runtime()
    calls = []
    monkeypatch.setattr(
        reference_contracts,
        "resolve_component_interface",
        lambda component, name: {
            "selection": {"native_lcs": name},
            "resolved": {"geometry_binding": {"status": "current"}},
        },
    )
    monkeypatch.setattr(
        NativeAssemblyJointRuntime,
        "mutate_joint",
        lambda self, arguments, *, ticket: calls.append((arguments, ticket)) or {
            "receipt": {
                "capability": ASSEMBLY_JOINT_CAPABILITY_NAME,
                "revision_before": 7,
                "revision_after": 8,
            }
        },
    )

    result = accept_joint_proposal_native(
        source,
        proposals,
        proposals["candidates"][0]["proposal_id"],
        runtime=runtime,
        ticket=ticket,
    )

    assert result["mutation_owner"] == "native-assembly"
    assert result["receipt"]["revision_after"] == 8
    assert len(calls) == 1
    arguments, used_ticket = calls[0]
    assert used_ticket is ticket
    assert arguments["operation"] == "create"
    assert arguments["joint_type"] == "revolute"
    assert {arguments["first"]["component"], arguments["second"]["component"]} == {
        "OccurrenceA", "OccurrenceB",
    }
    assert {arguments["first"]["connector"], arguments["second"]["connector"]} == {
        "MountA", "MountB",
    }


def test_native_acceptance_rejects_live_geometry_change_before_mutation(monkeypatch) -> None:
    source = _scenario()
    proposals = propose_joints(source)
    runtime, ticket = _runtime()
    calls = []
    monkeypatch.setattr(
        reference_contracts,
        "resolve_component_interface",
        lambda component, name: {
            "resolved": {"geometry_binding": {"status": "stale"}},
        },
    )
    monkeypatch.setattr(
        NativeAssemblyJointRuntime,
        "mutate_joint",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(AssemblyPlanningError, match="currentness changed"):
        accept_joint_proposal_native(
            source,
            proposals,
            proposals["candidates"][0]["proposal_id"],
            runtime=runtime,
            ticket=ticket,
        )
    assert calls == []
