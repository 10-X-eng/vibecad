# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-owned, revision-bound decisions for Assembly joint proposals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from tool_impl.assembly_planning import (
    JOINT_PROPOSAL_SCHEMA,
    normalize_scenario,
    propose_joints,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeAssemblyState import read_active_assembly
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import object_identity


PROPOSAL_DECISION_SCHEMA = "vibecad-assembly-proposal-decision-v1"
PROPOSAL_DECISION_LOG_SCHEMA = "vibecad-assembly-proposal-decision-log-v1"
PROP_PROPOSAL_DECISIONS = "VibeCADJointProposalDecisions"
PROPOSAL_DECISION_GROUP = "VibeCAD Assembly Planning"
ASSEMBLY_PROPOSAL_DECISION_CAPABILITY_NAME = "assembly.proposal_decision"
MAX_PROPOSAL_DECISIONS = 256
MAX_DECISION_REASON_CHARACTERS = 2_000
MAX_DECIDED_BY_CHARACTERS = 160
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DECISION_RECORD_KEYS = {
    "schema", "decision_id", "scenario_id", "graph_revision", "proposal_id",
    "proposal_sha256", "decision", "reason", "decided_by",
}


class NativeAssemblyProposalDecisionError(RuntimeError):
    """A proposal decision is stale, malformed, contradictory, or unpersistable."""


@dataclass(frozen=True, slots=True)
class PreparedProposalDecision:
    assembly: Any
    log_before: dict[str, Any]
    log_after: dict[str, Any]
    record: dict[str, Any]
    no_op: bool


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bounded_text(value: Any, field: str, maximum: int, *, required: bool) -> str:
    text = str(value or "").strip()
    if (required and not text) or len(text) > maximum:
        requirement = f"1 to {maximum}" if required else f"0 to {maximum}"
        raise NativeAssemblyProposalDecisionError(
            f"{field} must contain {requirement} characters."
        )
    return text


def _empty_log() -> dict[str, Any]:
    return {"schema": PROPOSAL_DECISION_LOG_SCHEMA, "decisions": []}


def read_proposal_decision_log(assembly: Any) -> dict[str, Any]:
    """Read and strictly validate the bounded append-only decision log."""

    raw = str(getattr(assembly, PROP_PROPOSAL_DECISIONS, "") or "").strip()
    if not raw:
        return _empty_log()
    try:
        log = json.loads(raw)
    except ValueError as exc:
        raise NativeAssemblyProposalDecisionError(
            "The Assembly proposal-decision log is invalid JSON."
        ) from exc
    if (
        not isinstance(log, dict)
        or set(log) != {"schema", "decisions"}
        or log.get("schema") != PROPOSAL_DECISION_LOG_SCHEMA
        or not isinstance(log.get("decisions"), list)
        or len(log["decisions"]) > MAX_PROPOSAL_DECISIONS
    ):
        raise NativeAssemblyProposalDecisionError(
            "The Assembly proposal-decision log violates its bounded schema."
        )
    seen: set[str] = set()
    for record in log["decisions"]:
        if (
            not isinstance(record, dict)
            or set(record) != _DECISION_RECORD_KEYS
            or record.get("schema") != PROPOSAL_DECISION_SCHEMA
            or record.get("decision") != "rejected"
            or not isinstance(record.get("decision_id"), str)
            or not isinstance(record.get("scenario_id"), str)
            or not isinstance(record.get("graph_revision"), str)
            or not isinstance(record.get("proposal_id"), str)
            or not isinstance(record.get("proposal_sha256"), str)
            or not isinstance(record.get("reason"), str)
            or not isinstance(record.get("decided_by"), str)
        ):
            raise NativeAssemblyProposalDecisionError(
                "The Assembly proposal-decision log contains a malformed record."
            )
        try:
            _bounded_text(record["scenario_id"], "scenario_id", 128, required=True)
            _bounded_text(record["proposal_id"], "proposal_id", 128, required=True)
            _bounded_text(
                record["reason"], "reason", MAX_DECISION_REASON_CHARACTERS,
                required=True,
            )
            _bounded_text(
                record["decided_by"], "decided_by", MAX_DECIDED_BY_CHARACTERS,
                required=True,
            )
        except NativeAssemblyProposalDecisionError as exc:
            raise NativeAssemblyProposalDecisionError(
                "The Assembly proposal-decision log contains a malformed record."
            ) from exc
        decision_id = record["decision_id"]
        identity_source = {
            key: record[key]
            for key in _DECISION_RECORD_KEYS - {"schema", "decision_id"}
        }
        expected_id = hashlib.sha256(
            _canonical(identity_source).encode("utf-8")
        ).hexdigest()
        if (
            _SHA256_RE.fullmatch(decision_id) is None
            or _SHA256_RE.fullmatch(record["graph_revision"]) is None
            or _SHA256_RE.fullmatch(record["proposal_sha256"]) is None
            or decision_id != expected_id
        ):
            raise NativeAssemblyProposalDecisionError(
                "The Assembly proposal-decision log contains a malformed record."
            )
        if decision_id in seen:
            raise NativeAssemblyProposalDecisionError(
                "The Assembly proposal-decision log repeats a decision identity."
            )
        seen.add(decision_id)
    return json.loads(_canonical(log))


def prepare_proposal_decision(
    assembly: Any,
    scenario: Mapping[str, Any],
    proposals: Mapping[str, Any],
    proposal_id: str,
    *,
    decision: str,
    reason: str,
    decided_by: str,
) -> PreparedProposalDecision:
    """Revalidate one exact proposal and prepare an append-only document record."""

    normalized = normalize_scenario(scenario)
    if proposals.get("schema") != JOINT_PROPOSAL_SCHEMA:
        raise NativeAssemblyProposalDecisionError("Joint proposals use an unsupported schema.")
    if (
        proposals.get("scenario_id") != normalized["scenario_id"]
        or proposals.get("graph_revision") != normalized["graph_revision"]
    ):
        raise NativeAssemblyProposalDecisionError(
            "Proposal decisions require the current graph revision."
        )
    canonical = propose_joints(normalized, max_candidates=512)
    selected_id = str(proposal_id or "").strip()
    expected = {
        item["proposal_id"]: item for item in canonical["candidates"]
    }.get(selected_id)
    supplied = {
        str(item.get("proposal_id") or ""): item
        for item in proposals.get("candidates", ())
        if isinstance(item, Mapping)
    }.get(selected_id)
    if expected is None or supplied != expected:
        raise NativeAssemblyProposalDecisionError(
            "The decided proposal is missing, stale, or altered."
        )
    clean_decision = str(decision or "").strip().lower()
    if clean_decision != "rejected":
        raise NativeAssemblyProposalDecisionError(
            "Only rejected proposals are recorded here; accepted proposals require "
            "the Assembly joint mutation receipt."
        )
    clean_reason = _bounded_text(
        reason,
        "reason",
        MAX_DECISION_REASON_CHARACTERS,
        required=True,
    )
    clean_actor = _bounded_text(
        decided_by,
        "decided_by",
        MAX_DECIDED_BY_CHARACTERS,
        required=True,
    )
    proposal_hash = hashlib.sha256(_canonical(expected).encode("utf-8")).hexdigest()
    identity_source = {
        "scenario_id": normalized["scenario_id"],
        "graph_revision": normalized["graph_revision"],
        "proposal_id": selected_id,
        "proposal_sha256": proposal_hash,
        "decision": clean_decision,
        "reason": clean_reason,
        "decided_by": clean_actor,
    }
    decision_id = hashlib.sha256(_canonical(identity_source).encode("utf-8")).hexdigest()
    record = {
        "schema": PROPOSAL_DECISION_SCHEMA,
        "decision_id": decision_id,
        **identity_source,
    }
    before = read_proposal_decision_log(assembly)
    same_proposal = [
        item for item in before["decisions"]
        if item["graph_revision"] == normalized["graph_revision"]
        and item["proposal_id"] == selected_id
    ]
    if same_proposal:
        if len(same_proposal) == 1 and same_proposal[0] == record:
            return PreparedProposalDecision(assembly, before, before, record, True)
        raise NativeAssemblyProposalDecisionError(
            "This proposal already has a different decision for the same graph revision."
        )
    if len(before["decisions"]) >= MAX_PROPOSAL_DECISIONS:
        raise NativeAssemblyProposalDecisionError(
            f"The Assembly already contains {MAX_PROPOSAL_DECISIONS} proposal decisions."
        )
    after = {
        "schema": PROPOSAL_DECISION_LOG_SCHEMA,
        "decisions": [*before["decisions"], record],
    }
    return PreparedProposalDecision(assembly, before, after, record, False)


def apply_proposal_decision(
    _document: Any,
    prepared: PreparedProposalDecision,
) -> NativeMutationDraft:
    """Persist one prepared decision inside the caller-owned Native transaction."""

    assembly = prepared.assembly
    properties = set(getattr(assembly, "PropertiesList", ()) or ())
    if PROP_PROPOSAL_DECISIONS not in properties:
        add_property = getattr(assembly, "addProperty", None)
        if not callable(add_property):
            raise NativeAssemblyProposalDecisionError(
                "The active Assembly cannot persist proposal decisions."
            )
        add_property(
            "App::PropertyString",
            PROP_PROPOSAL_DECISIONS,
            PROPOSAL_DECISION_GROUP,
            "Canonical append-only decisions bound to exact joint proposals and graph revisions.",
        )
    setattr(assembly, PROP_PROPOSAL_DECISIONS, _canonical(prepared.log_after))
    editor_mode = getattr(assembly, "setEditorMode", None)
    if callable(editor_mode):
        editor_mode(PROP_PROPOSAL_DECISIONS, 1)
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(assembly,),
        changed=(object_identity(assembly),),
    )


def verify_proposal_decision(
    _document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove the exact prepared log and return bounded decision evidence."""

    prepared = draft.value
    if not isinstance(prepared, PreparedProposalDecision):
        raise NativeAssemblyProposalDecisionError("Proposal decision verification lost its plan.")
    observed = read_proposal_decision_log(prepared.assembly)
    if observed != prepared.log_after or prepared.record not in observed["decisions"]:
        raise NativeAssemblyProposalDecisionError(
            "The persisted proposal decision does not match its prepared record."
        )
    return {
        "decision": dict(prepared.record),
        "decision_count": len(observed["decisions"]),
        "persistence": "native-assembly-document",
    }


def record_proposal_decision_native(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
    scenario: Mapping[str, Any],
    proposals: Mapping[str, Any],
    proposal_id: str,
    *,
    decision: str,
    reason: str,
    decided_by: str,
) -> dict[str, Any]:
    """Persist one decision through the ordinary Native transaction/receipt runner."""

    if not isinstance(context, NativeRuntimeContext):
        raise NativeAssemblyProposalDecisionError("A Native runtime context is required.")
    if not isinstance(ticket, NativeCallTicket):
        raise NativeAssemblyProposalDecisionError("A Native call ticket is required.")
    if (
        ticket.capability_name != ASSEMBLY_PROPOSAL_DECISION_CAPABILITY_NAME
        or ticket.document_uid != context.document_uid
    ):
        raise NativeAssemblyProposalDecisionError(
            "The call ticket does not authorize proposal decisions in this document."
        )
    context.guard()
    assembly = read_active_assembly(context.document)
    if assembly is None:
        raise NativeAssemblyProposalDecisionError("No Assembly is active.")
    prepared = prepare_proposal_decision(
        assembly,
        scenario,
        proposals,
        proposal_id,
        decision=decision,
        reason=reason,
        decided_by=decided_by,
    )
    if prepared.no_op:
        return {
            "decision": dict(prepared.record),
            "decision_count": len(prepared.log_after["decisions"]),
            "persistence": "native-assembly-document",
            "mutation_performed": False,
        }
    return run_immediate_mutation(
        context,
        ticket=ticket,
        transaction_name="Record Assembly Joint Proposal Decision",
        mutate=lambda document: apply_proposal_decision(document, prepared),
        verify=verify_proposal_decision,
    )
