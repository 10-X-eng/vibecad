# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, mutation-free contracts for Assembly joint and sequence planning."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping


SCENARIO_SCHEMA = "vibecad-assembly-scenario-v1"
JOINT_PROPOSAL_SCHEMA = "vibecad-joint-proposals-v1"
SEQUENCE_SCHEMA = "vibecad-assembly-sequence-v1"
SERVICE_SCHEMA = "vibecad-service-plan-v1"
JOINT_ACCEPTANCE_SCHEMA = "vibecad-joint-acceptance-v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_VERDICTS = frozenset({
    "sampled-clear", "continuous-pass", "collision", "inaccessible",
    "unsupported", "indeterminate",
})


class AssemblyPlanningError(ValueError):
    """An Assembly planning input is malformed, stale, or contradictory."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        self.details = dict(details or {})
        super().__init__(message)


def _identifier(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _ID.fullmatch(result):
        raise AssemblyPlanningError(f"{field} is not a stable identifier.")
    return result


def _finite(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssemblyPlanningError(f"{field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise AssemblyPlanningError(f"{field} must be at least {minimum}.")
    return result


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unique_records(values: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise AssemblyPlanningError(f"{field} must be a list.")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            raise AssemblyPlanningError(f"{field} entries must be objects.")
        record = dict(raw)
        identity = _identifier(record.get("persistent_id"), f"{field}.persistent_id")
        if identity in seen:
            raise AssemblyPlanningError(f"{field} contains duplicate identity {identity!r}.")
        seen.add(identity)
        record["persistent_id"] = identity
        records.append(record)
    return records


def normalize_scenario(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonically bind one scenario to its exact graph revision."""

    if not isinstance(value, Mapping) or value.get("schema") != SCENARIO_SCHEMA:
        raise AssemblyPlanningError(f"Scenario schema must be {SCENARIO_SCHEMA!r}.")
    scenario_id = _identifier(value.get("scenario_id"), "scenario_id")
    occurrences = _unique_records(value.get("occurrences"), "occurrences")
    occurrence_ids = {item["persistent_id"] for item in occurrences}
    interfaces = _unique_records(value.get("interfaces", []), "interfaces")
    interface_ids = {item["persistent_id"] for item in interfaces}
    joints = _unique_records(value.get("joints", []), "joints")
    for interface in interfaces:
        owner = _identifier(interface.get("occurrence_id"), "interfaces.occurrence_id")
        if owner not in occurrence_ids:
            raise AssemblyPlanningError(f"Interface owner {owner!r} is not an occurrence.")
        interface["occurrence_id"] = owner
    for joint in joints:
        endpoints = joint.get("interface_ids")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            raise AssemblyPlanningError("Each joint must name exactly two interface_ids.")
        normalized = [_identifier(item, "joints.interface_ids") for item in endpoints]
        if normalized[0] == normalized[1] or any(item not in interface_ids for item in normalized):
            raise AssemblyPlanningError("Joint interface_ids must be distinct known interfaces.")
        joint["interface_ids"] = normalized
    graph = {
        "occurrences": sorted(occurrences, key=lambda item: item["persistent_id"]),
        "interfaces": sorted(interfaces, key=lambda item: item["persistent_id"]),
        "joints": sorted(joints, key=lambda item: item["persistent_id"]),
    }
    computed_revision = _canonical_hash(graph)
    supplied_revision = str(value.get("graph_revision") or "").strip()
    if supplied_revision and supplied_revision != computed_revision:
        raise AssemblyPlanningError(
            "Scenario graph_revision is stale or contradictory.",
            details={"expected": computed_revision, "received": supplied_revision},
        )
    return {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": scenario_id,
        "graph_revision": computed_revision,
        **graph,
    }


def propose_joints(
    scenario: Mapping[str, Any],
    *,
    max_candidates: int = 32,
) -> dict[str, Any]:
    """Rank explicit semantic interfaces without authoring or accepting a joint."""

    normalized = normalize_scenario(scenario)
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 512:
        raise AssemblyPlanningError("max_candidates must be an integer from 1 through 512.")
    occupied = {item for joint in normalized["joints"] for item in joint["interface_ids"]}
    candidates: list[dict[str, Any]] = []
    interfaces = normalized["interfaces"]
    for left_index, left in enumerate(interfaces):
        for right in interfaces[left_index + 1:]:
            if left["occurrence_id"] == right["occurrence_id"]:
                continue
            left_joints = set(map(str, left.get("allowed_joints", [])))
            right_joints = set(map(str, right.get("allowed_joints", [])))
            kinds = sorted(left_joints & right_joints)
            compatibility_equal = bool(left.get("compatibility")) and left.get("compatibility") == right.get("compatibility")
            semantic_equal = bool(left.get("kind")) and left.get("kind") == right.get("kind")
            left_fit = left.get("fit")
            right_fit = right.get("fit")
            left_geometry = left.get("geometry_binding")
            right_geometry = right.get("geometry_binding")
            geometry_statuses = [
                str(value.get("status") or "unrecorded")
                if isinstance(value, Mapping) else "unrecorded"
                for value in (left_geometry, right_geometry)
            ]
            fit_declared_both = isinstance(left_fit, Mapping) and isinstance(right_fit, Mapping)
            fit_equal = fit_declared_both and dict(left_fit) == dict(right_fit)
            fit_conflict = fit_declared_both and not fit_equal
            if not kinds or (left.get("compatibility") or right.get("compatibility")) and not compatibility_equal:
                continue
            if fit_conflict:
                continue
            if any(status in {"stale", "invalid"} for status in geometry_statuses):
                continue
            geometry_current = all(status == "current" for status in geometry_statuses)
            score = 100 + (20 if compatibility_equal else 0) + (15 if fit_equal else 0) + (10 if semantic_equal else 0) + (15 if geometry_current else 0)
            if left["persistent_id"] in occupied or right["persistent_id"] in occupied:
                score -= 50
            for joint_kind in kinds:
                candidates.append({
                    "proposal_id": _canonical_hash([normalized["graph_revision"], left["persistent_id"], right["persistent_id"], joint_kind])[:24],
                    "joint_kind": joint_kind,
                    "interface_ids": [left["persistent_id"], right["persistent_id"]],
                    "score": score,
                    "confidence": (
                        "high"
                        if compatibility_equal and semantic_equal and geometry_current
                        else "bounded"
                    ),
                    "evidence": {
                        "compatibility_equal": compatibility_equal,
                        "fit_status": (
                            "equal" if fit_equal else
                            "partial" if isinstance(left_fit, Mapping) != isinstance(right_fit, Mapping)
                            else "undeclared"
                        ),
                        "geometry_currentness": {
                            left["persistent_id"]: geometry_statuses[0],
                            right["persistent_id"]: geometry_statuses[1],
                        },
                        "semantic_kind_equal": semantic_equal,
                        "allowed_by_both": True,
                    },
                    "acceptance": "requires-currentness-check-and-assembly-owner",
                })
    candidates.sort(key=lambda item: (-item["score"], item["joint_kind"], item["interface_ids"]))
    bounded = candidates[:max_candidates]
    ambiguous = len(bounded) > 1 and bounded[0]["score"] == bounded[1]["score"]
    return {
        "schema": JOINT_PROPOSAL_SCHEMA,
        "scenario_id": normalized["scenario_id"],
        "graph_revision": normalized["graph_revision"],
        "status": "no-candidate" if not bounded else ("ambiguous" if ambiguous else "proposed"),
        "mutation_performed": False,
        "candidates": bounded,
        "truncated": len(candidates) > len(bounded),
    }


def accept_joint_proposal(
    scenario: Mapping[str, Any],
    proposals: Mapping[str, Any],
    proposal_id: str,
    *,
    assembly_owner: Callable[[dict[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Revalidate one proposal and delegate its mutation to the Assembly owner."""

    normalized = normalize_scenario(scenario)
    selected_id = _identifier(proposal_id, "proposal_id")
    if proposals.get("schema") != JOINT_PROPOSAL_SCHEMA:
        raise AssemblyPlanningError("Joint proposals use an unsupported schema.")
    if (
        proposals.get("scenario_id") != normalized["scenario_id"]
        or proposals.get("graph_revision") != normalized["graph_revision"]
    ):
        raise AssemblyPlanningError("Joint proposal acceptance requires the current graph revision.")
    canonical = propose_joints(normalized, max_candidates=512)
    expected = {
        item["proposal_id"]: item for item in canonical["candidates"]
    }.get(selected_id)
    supplied = {
        str(item.get("proposal_id") or ""): item
        for item in proposals.get("candidates", [])
        if isinstance(item, Mapping)
    }.get(selected_id)
    if expected is None or supplied != expected:
        raise AssemblyPlanningError("Selected joint proposal is missing or has been altered.")
    if not callable(assembly_owner):
        raise AssemblyPlanningError("An Assembly mutation owner is required for acceptance.")
    owner_result = assembly_owner(dict(expected))
    if not isinstance(owner_result, Mapping):
        raise AssemblyPlanningError("Assembly owner did not return a mutation result.")
    receipt = owner_result.get("receipt")
    if not isinstance(receipt, Mapping) or not receipt:
        raise AssemblyPlanningError("Assembly owner did not return an ordinary mutation receipt.")
    return {
        "schema": JOINT_ACCEPTANCE_SCHEMA,
        "scenario_id": normalized["scenario_id"],
        "source_graph_revision": normalized["graph_revision"],
        "proposal_id": selected_id,
        "joint_kind": expected["joint_kind"],
        "interface_ids": list(expected["interface_ids"]),
        "mutation_owner": "native-assembly",
        "receipt": dict(receipt),
        "provenance": {
            "source_proposal_schema": JOINT_PROPOSAL_SCHEMA,
            "source_proposal_id": selected_id,
            "source_graph_revision": normalized["graph_revision"],
        },
    }


def _topological_orders(nodes: list[str], edges: list[tuple[str, str]], limit: int) -> list[list[str]]:
    successors: dict[str, set[str]] = defaultdict(set)
    indegree = {node: 0 for node in nodes}
    for before, after in edges:
        if after not in successors[before]:
            successors[before].add(after)
            indegree[after] += 1
    orders: list[list[str]] = []

    def visit(prefix: list[str], remaining: dict[str, int]) -> None:
        if len(orders) >= limit:
            return
        available = sorted(node for node in nodes if node not in prefix and remaining[node] == 0)
        if not available:
            if len(prefix) == len(nodes):
                orders.append(prefix)
            return
        for node in available:
            updated = dict(remaining)
            for successor in successors[node]:
                updated[successor] -= 1
            visit(prefix + [node], updated)

    visit([], indegree)
    return orders


def plan_sequence(
    scenario: Mapping[str, Any],
    constraints: Mapping[str, Any],
    *,
    max_alternatives: int = 8,
) -> dict[str, Any]:
    """Enumerate bounded precedence-valid orders over caller-supplied evidence."""

    normalized = normalize_scenario(scenario)
    if not isinstance(constraints, Mapping):
        raise AssemblyPlanningError("constraints must be an object.")
    if isinstance(max_alternatives, bool) or not isinstance(max_alternatives, int) or not 1 <= max_alternatives <= 64:
        raise AssemblyPlanningError("max_alternatives must be an integer from 1 through 64.")
    expected = str(constraints.get("graph_revision") or "")
    if expected != normalized["graph_revision"]:
        raise AssemblyPlanningError("Sequencing constraints do not target the current graph revision.")
    nodes = [item["persistent_id"] for item in normalized["occurrences"]]
    node_set = set(nodes)
    precedence: list[tuple[str, str]] = []
    for raw in constraints.get("precedence", []):
        if not isinstance(raw, list) or len(raw) != 2:
            raise AssemblyPlanningError("Each precedence constraint must be a two-item list.")
        edge = tuple(_identifier(item, "precedence") for item in raw)
        if edge[0] == edge[1] or any(item not in node_set for item in edge):
            raise AssemblyPlanningError("Precedence constraints must reference distinct occurrences.")
        precedence.append(edge)  # type: ignore[arg-type]
    evidence = constraints.get("step_evidence", {})
    if not isinstance(evidence, Mapping):
        raise AssemblyPlanningError("step_evidence must be an object.")
    verdicts: dict[str, str] = {}
    for node in nodes:
        record = evidence.get(node)
        verdict = str(record.get("verdict") if isinstance(record, Mapping) else "indeterminate")
        if verdict not in _VERDICTS:
            raise AssemblyPlanningError(f"Unsupported sequence verdict {verdict!r}.")
        verdicts[node] = verdict
    orders = _topological_orders(nodes, precedence, max_alternatives + 1)
    if not orders:
        return {
            "schema": SEQUENCE_SCHEMA, "scenario_id": normalized["scenario_id"],
            "graph_revision": normalized["graph_revision"], "status": "invalid-precedence",
            "claim_ceiling": "no-sequence", "alternatives": [], "mutation_performed": False,
        }
    blocking = {"collision", "inaccessible", "unsupported"}
    feasible = [order for order in orders if not any(verdicts[node] in blocking for node in order)]
    status = "no-valid-sequence" if not feasible else "planned"
    claim = "no-sequence" if not feasible else (
        "continuous-pass" if all(verdicts[node] == "continuous-pass" for node in nodes)
        else "sampled-or-indeterminate"
    )
    alternatives = [{
        "sequence_id": _canonical_hash([normalized["graph_revision"], order])[:24],
        "steps": [{"index": index, "occurrence_id": node, "verdict": verdicts[node]} for index, node in enumerate(order)],
    } for order in feasible[:max_alternatives]]
    return {
        "schema": SEQUENCE_SCHEMA, "scenario_id": normalized["scenario_id"],
        "graph_revision": normalized["graph_revision"], "status": status,
        "claim_ceiling": claim, "alternatives": alternatives,
        "truncated": len(feasible) > len(alternatives), "mutation_performed": False,
    }


def plan_service(
    scenario: Mapping[str, Any],
    sequence: Mapping[str, Any],
    *,
    target_occurrence_ids: Iterable[str],
    protected_occurrence_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Project a current sequence into bounded reverse service alternatives."""

    normalized = normalize_scenario(scenario)
    if sequence.get("schema") != SEQUENCE_SCHEMA or sequence.get("graph_revision") != normalized["graph_revision"]:
        raise AssemblyPlanningError("Service planning requires a current G10 sequence.")
    targets = sorted({_identifier(item, "target_occurrence_ids") for item in target_occurrence_ids})
    protected = {_identifier(item, "protected_occurrence_ids") for item in protected_occurrence_ids}
    known = {item["persistent_id"] for item in normalized["occurrences"]}
    if not targets or any(item not in known for item in targets) or any(item not in known for item in protected):
        raise AssemblyPlanningError("Service targets and protected components must be known occurrences.")
    if protected.intersection(targets):
        raise AssemblyPlanningError("A service target cannot also be protected.")
    plans: list[dict[str, Any]] = []
    for alternative in sequence.get("alternatives", []):
        steps = list(alternative.get("steps", []))
        positions = {step.get("occurrence_id"): index for index, step in enumerate(steps)}
        if any(target not in positions for target in targets):
            continue
        cutoff = min(positions[target] for target in targets)
        removal = [step for step in reversed(steps[cutoff:])]
        if any(step.get("occurrence_id") in protected for step in removal):
            continue
        plans.append({
            "service_plan_id": _canonical_hash([alternative.get("sequence_id"), targets, [step.get("occurrence_id") for step in removal]])[:24],
            "source_sequence_id": alternative.get("sequence_id"),
            "target_occurrence_ids": targets,
            "removal_steps": removal,
            "objective": {"removed_occurrence_count": len(removal)},
        })
    plans.sort(key=lambda item: (item["objective"]["removed_occurrence_count"], item["service_plan_id"]))
    minimum = plans[0]["objective"]["removed_occurrence_count"] if plans else None
    optimal = [item for item in plans if item["objective"]["removed_occurrence_count"] == minimum]
    return {
        "schema": SERVICE_SCHEMA, "scenario_id": normalized["scenario_id"],
        "graph_revision": normalized["graph_revision"],
        "status": "planned" if optimal else "no-valid-service-plan",
        "claim_ceiling": "bounded-model-only", "plans": optimal,
        "equal_optima": len(optimal) > 1, "mutation_performed": False,
    }
