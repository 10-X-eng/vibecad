# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy

import pytest

from VibeCADAssemblyPlanning import (
    AssemblyPlanningError,
    JOINT_PROPOSAL_SCHEMA,
    SCENARIO_SCHEMA,
    SEQUENCE_SCHEMA,
    SERVICE_SCHEMA,
    normalize_scenario,
    plan_sequence,
    plan_service,
    propose_joints,
)


def scenario() -> dict:
    return {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": "assembly.fixture",
        "occurrences": [
            {"persistent_id": "occ.base"},
            {"persistent_id": "occ.arm"},
            {"persistent_id": "occ.pin"},
        ],
        "interfaces": [
            {
                "persistent_id": "if.base.hinge",
                "occurrence_id": "occ.base",
                "kind": "axis",
                "compatibility": "hinge.m8",
                "allowed_joints": ["revolute"],
            },
            {
                "persistent_id": "if.arm.hinge",
                "occurrence_id": "occ.arm",
                "kind": "axis",
                "compatibility": "hinge.m8",
                "allowed_joints": ["revolute"],
            },
            {
                "persistent_id": "if.pin.axis",
                "occurrence_id": "occ.pin",
                "kind": "axis",
                "compatibility": "pin.m8",
                "allowed_joints": ["cylindrical"],
            },
        ],
        "joints": [],
    }


def test_scenario_revision_is_canonical_and_rejects_stale_claim() -> None:
    first = normalize_scenario(scenario())
    reordered = scenario()
    reordered["occurrences"].reverse()
    reordered["interfaces"].reverse()
    assert normalize_scenario(reordered)["graph_revision"] == first["graph_revision"]

    stale = scenario()
    stale["graph_revision"] = "stale"
    with pytest.raises(AssemblyPlanningError, match="stale"):
        normalize_scenario(stale)


def test_joint_proposals_are_deterministic_and_never_mutate() -> None:
    source = scenario()
    before = deepcopy(source)
    first = propose_joints(source)
    second = propose_joints(source)

    assert first == second
    assert source == before
    assert first["schema"] == JOINT_PROPOSAL_SCHEMA
    assert first["status"] == "proposed"
    assert first["mutation_performed"] is False
    assert first["candidates"][0]["joint_kind"] == "revolute"
    assert first["candidates"][0]["acceptance"].endswith("assembly-owner")


def test_joint_proposals_report_ambiguity_and_no_candidate() -> None:
    symmetric = scenario()
    symmetric["occurrences"].append({"persistent_id": "occ.arm2"})
    symmetric["interfaces"].append({
        "persistent_id": "if.arm2.hinge", "occurrence_id": "occ.arm2",
        "kind": "axis", "compatibility": "hinge.m8",
        "allowed_joints": ["revolute"],
    })
    assert propose_joints(symmetric)["status"] == "ambiguous"

    incompatible = scenario()
    incompatible["interfaces"][1]["compatibility"] = "hinge.m10"
    assert propose_joints(incompatible)["status"] == "no-candidate"


def sequence_constraints(source: dict, **verdicts: str) -> dict:
    revision = normalize_scenario(source)["graph_revision"]
    return {
        "graph_revision": revision,
        "precedence": [["occ.base", "occ.arm"], ["occ.arm", "occ.pin"]],
        "step_evidence": {
            identity: {"verdict": verdicts.get(identity, "sampled-clear")}
            for identity in ("occ.base", "occ.arm", "occ.pin")
        },
    }


def test_sequence_distinguishes_sampled_and_continuous_claims() -> None:
    source = scenario()
    sampled = plan_sequence(source, sequence_constraints(source))
    continuous = plan_sequence(
        source,
        sequence_constraints(
            source,
            **{"occ.base": "continuous-pass", "occ.arm": "continuous-pass", "occ.pin": "continuous-pass"},
        ),
    )

    assert sampled["schema"] == SEQUENCE_SCHEMA
    assert sampled["claim_ceiling"] == "sampled-or-indeterminate"
    assert continuous["claim_ceiling"] == "continuous-pass"
    assert continuous["alternatives"][0]["steps"][0]["occurrence_id"] == "occ.base"


def test_sequence_rejects_stale_revision_and_reports_cycles_or_collision() -> None:
    source = scenario()
    stale = sequence_constraints(source)
    stale["graph_revision"] = "old"
    with pytest.raises(AssemblyPlanningError, match="current"):
        plan_sequence(source, stale)

    cyclic = sequence_constraints(source)
    cyclic["precedence"].append(["occ.pin", "occ.base"])
    assert plan_sequence(source, cyclic)["status"] == "invalid-precedence"

    collision = sequence_constraints(source, **{"occ.arm": "collision"})
    result = plan_sequence(source, collision)
    assert result["status"] == "no-valid-sequence"
    assert result["alternatives"] == []


def test_service_plan_is_current_bounded_and_respects_protected_components() -> None:
    source = scenario()
    sequence = plan_sequence(source, sequence_constraints(source))
    service = plan_service(source, sequence, target_occurrence_ids=["occ.arm"])

    assert service["schema"] == SERVICE_SCHEMA
    assert service["status"] == "planned"
    assert service["claim_ceiling"] == "bounded-model-only"
    assert [step["occurrence_id"] for step in service["plans"][0]["removal_steps"]] == [
        "occ.pin", "occ.arm"
    ]

    blocked = plan_service(
        source,
        sequence,
        target_occurrence_ids=["occ.arm"],
        protected_occurrence_ids=["occ.pin"],
    )
    assert blocked["status"] == "no-valid-service-plan"


def test_service_reports_equal_optima_and_refuses_noncurrent_sequence() -> None:
    source = scenario()
    constraints = sequence_constraints(source)
    constraints["precedence"] = []
    sequence = plan_sequence(source, constraints, max_alternatives=6)
    service = plan_service(source, sequence, target_occurrence_ids=["occ.base"])
    assert service["equal_optima"] is True

    stale = dict(sequence, graph_revision="old")
    with pytest.raises(AssemblyPlanningError, match="current"):
        plan_service(source, stale, target_occurrence_ids=["occ.base"])
