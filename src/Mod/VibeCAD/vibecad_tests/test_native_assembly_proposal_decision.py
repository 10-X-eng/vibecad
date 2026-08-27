# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyProposalDecision as decision_module
from VibeCADNativeAssemblyProposalDecision import (
    NativeAssemblyProposalDecisionError,
    ASSEMBLY_PROPOSAL_DECISION_CAPABILITY_NAME,
    PROP_PROPOSAL_DECISIONS,
    apply_proposal_decision,
    prepare_proposal_decision,
    read_proposal_decision_log,
    record_proposal_decision_native,
    verify_proposal_decision,
)
from VibeCADAssemblyPlanning import SCENARIO_SCHEMA, propose_joints
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _scenario() -> dict:
    return {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": "proposal-decisions",
        "occurrences": [
            {"persistent_id": "occ.a"},
            {"persistent_id": "occ.b"},
        ],
        "interfaces": [
            {
                "persistent_id": "if.a",
                "occurrence_id": "occ.a",
                "name": "MountA",
                "kind": "axis",
                "compatibility": "mount-v1",
                "allowed_joints": ["revolute"],
            },
            {
                "persistent_id": "if.b",
                "occurrence_id": "occ.b",
                "name": "MountB",
                "kind": "axis",
                "compatibility": "mount-v1",
                "allowed_joints": ["revolute"],
            },
        ],
        "joints": [],
    }


class _Assembly:
    Name = "Assembly"
    PropertiesList = []

    def __init__(self):
        self.PropertiesList = []
        self.editor_modes = {}

    def addProperty(self, _type_id, name, _group, _description):
        self.PropertiesList.append(name)

    def setEditorMode(self, name, mode):
        self.editor_modes[name] = mode


def _prepared(assembly, *, decision="rejected", reason="Wrong design intent"):
    scenario = _scenario()
    proposals = propose_joints(scenario)
    return prepare_proposal_decision(
        assembly,
        scenario,
        proposals,
        proposals["candidates"][0]["proposal_id"],
        decision=decision,
        reason=reason,
        decided_by="engineer@example.test",
    )


def test_rejection_is_revision_bound_persisted_and_idempotent(monkeypatch) -> None:
    assembly = _Assembly()
    prepared = _prepared(assembly)
    monkeypatch.setattr(
        decision_module,
        "object_identity",
        lambda obj: SimpleNamespace(object_name=obj.Name),
    )

    draft = apply_proposal_decision(SimpleNamespace(), prepared)
    result = verify_proposal_decision(SimpleNamespace(), draft)

    assert PROP_PROPOSAL_DECISIONS in assembly.PropertiesList
    assert assembly.editor_modes[PROP_PROPOSAL_DECISIONS] == 1
    assert result["persistence"] == "native-assembly-document"
    assert result["decision_count"] == 1
    assert result["decision"]["decision"] == "rejected"
    assert result["decision"]["reason"] == "Wrong design intent"
    repeated = _prepared(assembly)
    assert repeated.no_op is True
    assert repeated.log_after == prepared.log_after


def test_same_revision_cannot_replace_an_existing_decision() -> None:
    assembly = _Assembly()
    prepared = _prepared(assembly)
    setattr(assembly, PROP_PROPOSAL_DECISIONS, decision_module._canonical(prepared.log_after))

    with pytest.raises(NativeAssemblyProposalDecisionError, match="different decision"):
        second = _scenario()
        proposals = propose_joints(second)
        prepare_proposal_decision(
            assembly,
            second,
            proposals,
            proposals["candidates"][0]["proposal_id"],
            decision="rejected",
            reason="A different rejection",
            decided_by="engineer@example.test",
        )


def test_acceptance_cannot_be_claimed_without_joint_mutation_receipt() -> None:
    with pytest.raises(NativeAssemblyProposalDecisionError, match="mutation receipt"):
        _prepared(_Assembly(), decision="accepted", reason="")


def test_decision_revalidates_exact_proposal_and_requires_rejection_reason() -> None:
    assembly = _Assembly()
    scenario = _scenario()
    proposals = propose_joints(scenario)
    proposal_id = proposals["candidates"][0]["proposal_id"]
    proposals["candidates"][0]["score"] += 1

    with pytest.raises(NativeAssemblyProposalDecisionError, match="altered"):
        prepare_proposal_decision(
            assembly,
            scenario,
            proposals,
            proposal_id,
            decision="rejected",
            reason="No",
            decided_by="engineer",
        )
    proposals = propose_joints(scenario)
    with pytest.raises(NativeAssemblyProposalDecisionError, match="reason"):
        prepare_proposal_decision(
            assembly,
            scenario,
            proposals,
            proposal_id,
            decision="rejected",
            reason="",
            decided_by="engineer",
        )


def test_malformed_persisted_log_fails_closed() -> None:
    assembly = _Assembly()
    setattr(assembly, PROP_PROPOSAL_DECISIONS, '{"schema":"wrong","decisions":[]}')
    with pytest.raises(NativeAssemblyProposalDecisionError, match="bounded schema"):
        read_proposal_decision_log(assembly)


def test_native_owner_routes_decision_through_transaction_and_receipt(monkeypatch) -> None:
    assembly = _Assembly()
    document = SimpleNamespace(Uid="decision-document")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=NativeDocumentStateStore(),
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "assemble",
        edit_or_task_active=lambda: False,
    )
    ticket = NativeCallTicket(
        document.Uid,
        ASSEMBLY_PROPOSAL_DECISION_CAPABILITY_NAME,
        4,
        "proposal-decision-ticket",
    )
    scenario = _scenario()
    proposals = propose_joints(scenario)
    calls = []
    monkeypatch.setattr(decision_module, "read_active_assembly", lambda doc: assembly)
    monkeypatch.setattr(
        decision_module,
        "run_immediate_mutation",
        lambda runtime_context, **kwargs: calls.append((runtime_context, kwargs)) or {
            "receipt": {"revision_before": 4, "revision_after": 5}
        },
    )

    result = record_proposal_decision_native(
        context,
        ticket,
        scenario,
        proposals,
        proposals["candidates"][0]["proposal_id"],
        decision="rejected",
        reason="Conflicts with service access",
        decided_by="engineer",
    )

    assert result["receipt"]["revision_after"] == 5
    assert len(calls) == 1
    runtime_context, options = calls[0]
    assert runtime_context is context
    assert options["ticket"] is ticket
    assert options["transaction_name"] == "Record Assembly Joint Proposal Decision"
