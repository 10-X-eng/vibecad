# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for exact Native Drawing page operations."""

from __future__ import annotations

import json

from VibeCADNativeDrawingPageSchema import drawing_page_capability_definition


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_page_variants_are_closed_exact_and_path_private() -> None:
    definition = drawing_page_capability_definition()
    operations = tuple(variant.operation for variant in definition.variants)
    schema = definition.provider_schema(operations)

    assert operations == (
        "page_default",
        "page_template",
        "fill_template_fields",
        "redraw_page",
    )
    assert tuple(
        next(iter(variant.action_ids)) for variant in definition.variants
    ) == (
        "TechDraw_PageDefault",
        "TechDraw_PageTemplate",
        "TechDraw_FillTemplateFields",
        "TechDraw_RedrawPage",
    )
    assert tuple(variant.exact_target_type for variant in definition.variants) == (
        "NewDrawingPageWithConfiguredTemplate",
        "HumanAuthorizedSvgTemplateForNewDrawingPage",
        "ExactDrawingPageAndEditableTemplateFields",
        "ExactDrawingPageAndActiveViewGraph",
    )
    assert all(variant.surface_ids == frozenset({"drawing"}) for variant in definition.variants)
    assert tuple(variant.transaction_behavior for variant in definition.variants) == (
        "document",
        "document",
        "document",
        "background",
    )
    assert tuple(variant.background_required for variant in definition.variants) == (
        False,
        False,
        False,
        True,
    )
    parameters = schema["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["operation"]
    assert set(parameters["properties"]) == {"operation", "page", "updates"}

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded


def test_template_field_edit_requires_stale_state_and_value_guards() -> None:
    definition = drawing_page_capability_definition()
    schema = definition.provider_schema(("fill_template_fields",))
    branch = _branch(schema, "fill_template_fields")
    page = branch["properties"]["page"]
    updates = branch["properties"]["updates"]
    update = updates["items"]

    assert branch["required"] == ["operation", "page", "updates"]
    assert page["required"] == ["object_name", "expected_state_sha256"]
    assert page["additionalProperties"] is False
    assert updates["minItems"] == 1
    assert updates["maxItems"] == 64
    assert update["required"] == ["field_name", "expected_value", "value"]
    assert update["additionalProperties"] is False


def test_page_redraw_requires_one_exact_page_and_no_worker_paths() -> None:
    definition = drawing_page_capability_definition()
    schema = definition.provider_schema(("redraw_page",))
    branch = _branch(schema, "redraw_page")

    assert branch["required"] == ["operation", "page"]
    assert branch["additionalProperties"] is False
    assert branch["properties"]["page"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    assert branch["properties"]["page"]["additionalProperties"] is False
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "path" not in encoded
    assert "snapshot" not in encoded
