# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for derived multipart solid analysis domains."""

from __future__ import annotations

from types import SimpleNamespace

from VibeCADNativeAnalyzeSolidDomainSchema import (
    ANALYZE_SOLID_DOMAIN,
    analyze_solid_domain_capability_definition,
)
from VibeCADNativeRegistry import build_native_capability_registry


def test_solid_domain_provider_contract_is_small_and_explicit() -> None:
    definition = analyze_solid_domain_capability_definition()

    assert definition.name == ANALYZE_SOLID_DOMAIN == "analyze.solid_domain"
    assert definition.description == (
        "Create one meshable domain from separate solid objects."
    )
    assert len(definition.variants) == 1
    variant = definition.variants[0]
    assert variant.operation == "create"
    assert variant.exact_target_type == "CurrentSolidGeometrySources"
    assert variant.transaction_behavior == "document"
    assert not variant.background_required
    assert variant.parameters == {
        "type": "object",
        "properties": {
            "source_names": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                },
                "minItems": 2,
                "maxItems": 256,
                "uniqueItems": True,
                "description": "Separate solid object names.",
            },
            "interface_mode": {
                "type": "string",
                "enum": ["separate", "shared"],
                "description": (
                    "separate retains faces for tie or contact; shared creates "
                    "conformal bonded interfaces."
                ),
            },
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "default": "Solid analysis domain",
            },
        },
        "required": ["source_names", "interface_mode"],
        "additionalProperties": False,
    }


def test_solid_domain_is_registered_as_a_complete_production_capability() -> None:
    registry = build_native_capability_registry()

    assert registry.definition(ANALYZE_SOLID_DOMAIN) is not None
    assert registry.implementation(ANALYZE_SOLID_DOMAIN) is not None


def test_solid_domain_binding_resolves_names_to_exact_current_sources(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSolidDomainBindings as bindings
    from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime

    requests = []
    monkeypatch.setattr(
        bindings,
        "current_target",
        lambda _runtime, name, _reader: {
            "object_name": name,
            "expected_state_sha256": f"state-{name}",
        },
    )
    monkeypatch.setattr(
        NativeAnalyzeModelRuntime,
        "execute",
        lambda _self, request, *, ticket: (
            requests.append((request, ticket))
            or {"domain": {"object_name": "SolidAnalysisDomain"}}
        ),
    )
    runtime = object.__new__(NativeAnalyzeModelRuntime)
    ticket = object()

    result = bindings._execute(
        SimpleNamespace(
            runtime=runtime,
            ticket=ticket,
            arguments={
                "operation": "create",
                "source_names": ["ColumnA", "ColumnB", "Deck"],
                "interface_mode": "shared",
            },
        )
    )

    assert result["domain"]["object_name"] == "SolidAnalysisDomain"
    assert requests == [
        (
            {
                "operation": "create_solid_domain",
                "sources": [
                    {
                        "object_name": "ColumnA",
                        "expected_state_sha256": "state-ColumnA",
                    },
                    {
                        "object_name": "ColumnB",
                        "expected_state_sha256": "state-ColumnB",
                    },
                    {
                        "object_name": "Deck",
                        "expected_state_sha256": "state-Deck",
                    },
                ],
                "interface_mode": "shared",
                "label": "Solid analysis domain",
            },
            ticket,
        )
    ]
