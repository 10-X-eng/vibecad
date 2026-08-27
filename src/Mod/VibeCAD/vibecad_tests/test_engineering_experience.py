# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace
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
from VibeCADEngineeringExperience import (
    DomainPresentation,
    EngineeringFieldProjection,
    PresentationMetric,
    governance_role,
    project_engineering_result,
)


DOMAINS = ("native", "fem", "aero", "manufacture", "assembly", "robot")


def identity(owner, kind, value):
    return EngineeringIdentity("vibecad", owner, kind, value, "1")


def result(domain):
    source = identity("native", "document", f"doc-{domain}")
    activity = identity(domain, "activity", f"activity-{domain}")
    result_id = identity(domain, "result", f"result-{domain}")
    artifact = ContentDescriptor("application/json", "sha256", "a" * 64, 12,
                                 "result", "v1")
    finding = FindingEnvelope(
        f"finding-{domain}", "rule-1", "verifier", domain, "pass", "note",
        "bounded", "Domain-owned evidence", (source,), (artifact,), "",
        "current", "engineering-evidence-only",
    )
    provenance = ProvenanceGraph(
        f"graph-{domain}",
        (
            ProvenanceNode(source.canonical, "entity", canonical_payload({"role": "source"})),
            ProvenanceNode(activity.canonical, "activity", canonical_payload({"role": "solve"})),
            ProvenanceNode(result_id.canonical, "entity", canonical_payload({"role": "result"})),
        ),
        (
            ProvenanceEdge("used", "used", activity.canonical, source.canonical),
            ProvenanceEdge("generated", "generated", result_id.canonical, activity.canonical),
        ),
    )
    return EngineeringResultEnvelope(
        1, 0, result_id, activity, domain, f"adapter.{domain}", "attempt-1",
        "succeeded", "pass", "current", "published", source, "b" * 64,
        (artifact,), canonical_payload({"domain_metric": 12}), (finding,),
        provenance, canonical_payload({"owned": {"domain": domain, "exact": True}}),
    )


def presentation():
    return DomainPresentation(
        "Engineering result",
        (PresentationMetric("maximum", "Maximum", 347.8, "MPa", "max"),),
        (EngineeringFieldProjection(
            "vonMises", "Von Mises Stress", "stress.von_mises", "point", 1,
            "MPa", 12.4, 347.8, "scalar", "turbo",
        ),),
        canonical_payload({"page": "results"}),
    )


@pytest.mark.parametrize("domain", DOMAINS)
def test_projection_preserves_domain_payload_and_exact_common_identity(domain):
    envelope = result(domain)
    projected = project_engineering_result(envelope, presentation())

    assert projected["domain"] == domain
    assert projected["result_id"] == envelope.result_id.to_dict()
    assert projected["source_identity"] == envelope.source_identity.to_dict()
    assert projected["domain_payload"] == envelope.domain_payload.to_value()
    assert projected["domain_payload_sha256"] == envelope.domain_payload.sha256()
    assert projected["findings"] == [envelope.findings[0].to_dict()]
    assert projected["provenance"] == envelope.provenance.to_dict()
    assert projected["presentation_only"] is True
    assert set(projected["authority"].values()) == {False}


def test_four_governance_axes_are_independent():
    original = result("fem")
    cases = (
        replace(original, execution_status="failed"),
        replace(original, verification_verdict="indeterminate"),
        replace(original, currentness="stale"),
        replace(original, publication_state="historical"),
    )
    expected = (
        ("failed", "pass", "current", "published"),
        ("succeeded", "indeterminate", "current", "published"),
        ("succeeded", "pass", "stale", "published"),
        ("succeeded", "pass", "current", "historical"),
    )
    for envelope, values in zip(cases, expected):
        axes = project_engineering_result(envelope, presentation())["axes"]
        assert tuple(axes[name]["value"] for name in
                     ("execution", "verification", "currentness", "publication")) == values


def test_scientific_color_map_is_not_derived_from_governance_role():
    projected = project_engineering_result(
        replace(result("fem"), verification_verdict="failed"), presentation()
    )
    assert projected["fields"][0]["default_color_map"] == "turbo"
    assert projected["axes"]["verification"]["role"] == "negative"
    assert "turbo" not in {axis["role"] for axis in projected["axes"].values()}


@pytest.mark.parametrize("axis,value,role", (
    ("execution", "running", "active"),
    ("execution", "cancelled", "historical"),
    ("verification", "indeterminate", "caution"),
    ("currentness", "stale", "caution"),
    ("publication", "authorized", "active"),
))
def test_governance_roles_are_semantic_not_literal_colors(axis, value, role):
    assert governance_role(axis, value) == role


def test_projection_is_bounded_json_without_live_authority_objects():
    projected = project_engineering_result(result("aero"), presentation())
    encoded = json.dumps(projected, sort_keys=True, allow_nan=False)
    for forbidden in ("callback", "document_object", "publish_function", "credential"):
        assert forbidden not in encoded


def test_field_contract_rejects_invalid_ranges_colormaps_and_nonfinite_values():
    with pytest.raises(AnalysisContractError, match="minimum"):
        EngineeringFieldProjection("f", "Field", "scalar", "point", 1, "Pa",
                                   2, 1, "scalar", "turbo")
    with pytest.raises(AnalysisContractError, match="color map"):
        EngineeringFieldProjection("f", "Field", "scalar", "point", 1, "Pa",
                                   1, 2, "scalar", "decorative-rainbow")
    with pytest.raises(AnalysisContractError, match="finite"):
        PresentationMetric("m", "Metric", float("nan"), "Pa")


def test_duplicate_and_unbounded_projection_items_are_refused():
    metric = PresentationMetric("m", "Metric", 1, "Pa")
    with pytest.raises(AnalysisContractError, match="unique"):
        DomainPresentation("Duplicate", (metric, metric))
    with pytest.raises(AnalysisContractError, match="bounded"):
        DomainPresentation("Too many", tuple(
            PresentationMetric(f"m-{index}", "Metric", index, "Pa")
            for index in range(129)
        ))


def test_unknown_status_axis_is_refused():
    with pytest.raises(AnalysisContractError, match="Unknown governance"):
        governance_role("scientific", "red")
