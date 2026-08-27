# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from VibeCADAnalysisContracts import AnalysisContractError
from VibeCADEngineeringContracts import (
    ContentDescriptor,
    EngineeringIdentity,
    EngineeringResultEnvelope,
    FindingEnvelope,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    canonical_payload,
)


DOMAINS = ("native", "fem", "aero", "manufacture", "assembly", "robot")


def _identity(kind: str, value: str, owner: str = "vibecad") -> EngineeringIdentity:
    return EngineeringIdentity("vibecad", owner, kind, value, "1")


def _result(domain: str, *, minor: int = 0) -> EngineeringResultEnvelope:
    source = _identity("document", f"doc-{domain}", "native")
    result = _identity("result", f"result-{domain}", domain)
    activity = _identity("activity", f"activity-{domain}", domain)
    artifact = ContentDescriptor(
        "application/json", "sha256", "a" * 64, 17, "primary-result", "v1"
    )
    finding = FindingEnvelope(
        f"finding-{domain}", "rule-1", "vibecad-verifier", domain,
        "pass", "note", "bounded", "Representative bounded finding",
        (source,), (artifact,), "", "current", "engineering-evidence-only",
    )
    graph = ProvenanceGraph(
        f"graph-{domain}",
        (
            ProvenanceNode(source.canonical, "entity", canonical_payload({"domain": domain})),
            ProvenanceNode(activity.canonical, "activity", canonical_payload({"adapter": domain})),
            ProvenanceNode(result.canonical, "entity", canonical_payload({"status": "solved"})),
            ProvenanceNode("agent:vibecad", "agent", canonical_payload({"role": "host"})),
        ),
        (
            ProvenanceEdge("edge-used", "used", activity.canonical, source.canonical),
            ProvenanceEdge("edge-generated", "generated", result.canonical, activity.canonical),
            ProvenanceEdge("edge-associated", "associated", activity.canonical, "agent:vibecad", "runtime"),
        ),
    )
    return EngineeringResultEnvelope(
        1, minor, result, activity, domain, f"adapter.{domain}", "attempt-1",
        "solved", "model-unqualified", "current", "unpublished", source,
        "b" * 64, (artifact,), canonical_payload({"count": 1}), (finding,),
        graph, canonical_payload({"domain_specific": {"domain": domain}}),
    )


@pytest.mark.parametrize("domain", DOMAINS)
def test_cross_domain_round_trip_preserves_opaque_payload_and_axes(domain: str) -> None:
    original = _result(domain)
    encoded = original.to_canonical_json()
    restored = EngineeringResultEnvelope.from_canonical_json(encoded)

    assert restored == original
    assert encoded == restored.to_canonical_json()
    assert restored.execution_status == "solved"
    assert restored.verification_verdict == "model-unqualified"
    assert restored.currentness == "current"
    assert restored.publication_state == "unpublished"
    assert restored.domain_payload.to_value()["domain_specific"]["domain"] == domain


def test_additive_minor_version_is_readable_but_unknown_major_is_refused() -> None:
    assert EngineeringResultEnvelope.from_canonical_json(
        _result("fem", minor=7).to_canonical_json()
    ).contract_minor == 7
    value = json.loads(_result("fem").to_canonical_json())
    value["contract_major"] = 2
    with pytest.raises(AnalysisContractError, match="major version"):
        EngineeringResultEnvelope.from_dict(value)


def test_identity_types_cannot_be_substituted() -> None:
    _identity("result", "one").require_same_type(_identity("result", "two"))
    with pytest.raises(AnalysisContractError, match="not substitutable"):
        _identity("result", "one").require_same_type(_identity("document", "one"))


def test_duplicate_graph_and_finding_ids_are_refused() -> None:
    result = _result("assembly")
    node = result.provenance.nodes[0]
    with pytest.raises(AnalysisContractError, match="node IDs"):
        ProvenanceGraph("duplicate", (node, node), ())
    finding = result.findings[0]
    with pytest.raises(AnalysisContractError, match="finding IDs"):
        EngineeringResultEnvelope(
            result.contract_major, result.contract_minor, result.result_id,
            result.activity_id, result.domain, result.adapter_id,
            result.provider_attempt_id, result.execution_status,
            result.verification_verdict, result.currentness,
            result.publication_state, result.source_identity,
            result.dependency_digest, result.artifacts, result.summary_metrics,
            (finding, finding), result.provenance, result.domain_payload,
        )


@pytest.mark.parametrize("payload", (
    {"api_token": "do-not-store"},
    {"nested": {"password": "do-not-store"}},
    {"credential-id": "do-not-store"},
))
def test_secret_bearing_payload_fields_are_refused(payload) -> None:
    with pytest.raises(AnalysisContractError, match="Secret-bearing"):
        canonical_payload(payload)


def test_non_json_live_objects_and_non_finite_values_are_refused() -> None:
    with pytest.raises(AnalysisContractError):
        canonical_payload({"document": object()})
    with pytest.raises(AnalysisContractError):
        canonical_payload({"metric": float("nan")})


@pytest.mark.parametrize("path", ("C:\\Temp\\solver.out", "/tmp/solver.out", "\\\\host\\share\\result"))
def test_absolute_paths_are_refused_from_common_payloads(path: str) -> None:
    with pytest.raises(AnalysisContractError, match="Absolute paths"):
        canonical_payload({"working_path": path})


def test_dangling_provenance_edge_is_refused() -> None:
    node = ProvenanceNode("entity:one", "entity", canonical_payload({}))
    with pytest.raises(AnalysisContractError, match="existing nodes"):
        ProvenanceGraph(
            "graph", (node,),
            (ProvenanceEdge("edge", "derived", "entity:one", "missing"),),
        )
