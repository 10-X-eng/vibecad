# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeAuthorityPolicy import (
    POLICY_CLASSES,
    build_native_authority_census,
)
from VibeCADNativeRegistry import build_native_capability_registry


def test_census_classifies_every_registered_operation_exactly_once() -> None:
    registry = build_native_capability_registry()
    census = build_native_authority_census(registry)
    expected = {
        (name, variant.operation)
        for name in registry.definition_names
        for variant in registry.definition(name).variants
    }
    actual = {(item.capability, item.operation) for item in census}

    assert actual == expected | {
        ("agent.compatibility_run", "/v1/run"),
        ("agent.prompt", "/v1/prompt"),
    }
    assert len(actual) == len(census)
    assert {item.policy_class for item in census} <= POLICY_CLASSES
    assert all(item.currentness_inputs for item in census)
    assert all(item.effect_evidence for item in census)
    assert all(item.rollback_behavior for item in census)


def test_schema_preview_apply_contract_is_always_preview_required() -> None:
    registry = build_native_capability_registry()
    census = {
        (item.capability, item.operation): item
        for item in build_native_authority_census(registry)
    }
    preview_count = 0
    for name in registry.definition_names:
        definition = registry.definition(name)
        for variant in definition.variants:
            stage = dict(variant.parameters.get("properties") or {}).get("stage", {})
            values = set(stage.get("enum") or ([stage.get("const")] if "const" in stage else []))
            if {"propose", "apply"} <= values:
                preview_count += 1
                assert census[(name, variant.operation)].policy_class == "preview_required"
    assert preview_count >= 10


def test_domain_and_special_policy_coverage() -> None:
    census = build_native_authority_census(build_native_capability_registry())
    domains = {item.capability.split(".", 1)[0] for item in census}
    assert {"model", "sketch", "assembly", "analyze", "manufacture", "drawing", "robot", "aero", "native"} <= domains
    run = next(item for item in census if item.operation == "/v1/run")
    assert run.policy_class == "privileged_compatibility_execution"
    assert "not a safe Native mutation" in run.reason
    assert {"read_only", "presentation_change", "safe_immediate_mutation",
            "preview_required", "explicit_confirmation_required",
            "human_authorized_export", "external_side_effect",
            "privileged_compatibility_execution"} == {item.policy_class for item in census}
