# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for explicit Drawing view position locks."""

from __future__ import annotations

import json

from VibeCADNativeActionManifest import _plan
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingViewLockBindings import (
    register_drawing_view_lock_capability_implementation,
)
from VibeCADNativeDrawingViewLockSchema import (
    DRAWING_VIEW_LOCK_CAPABILITY_NAME,
    DRAWING_VIEW_LOCK_OPERATIONS,
    drawing_view_lock_capability_definition,
    register_drawing_view_lock_capability_definition,
)
from VibeCADRibbonSurface import RibbonAction


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"].get("const") == operation
    )


def test_view_lock_schema_is_closed_explicit_exact_and_bounded() -> None:
    definition = drawing_view_lock_capability_definition()
    schema = definition.provider_schema(DRAWING_VIEW_LOCK_OPERATIONS)

    assert DRAWING_VIEW_LOCK_OPERATIONS == ("set", "read_page")
    assert definition.primary_classification == "mutation"
    assert len(schema["parameters"]["oneOf"]) == 2

    set_branch = _branch(schema, "set")
    assert set_branch["additionalProperties"] is False
    views = set_branch["properties"]["views"]
    assert views["minItems"] == 1
    assert views["maxItems"] == 32
    change = views["items"]
    assert change["additionalProperties"] is False
    assert change["properties"]["locked"]["type"] == "boolean"
    assert "toggle" in change["properties"]["locked"]["description"]

    read_branch = _branch(schema, "read_page")
    assert read_branch["additionalProperties"] is False
    assert read_branch["properties"]["offset"]["maximum"] == 512
    assert read_branch["properties"]["page_size"]["maximum"] == 48

    set_variant, read_variant = definition.variants
    assert set_variant.action_ids == frozenset(
        {"TechDraw_ExtensionLockUnlockView"}
    )
    assert set_variant.exact_target_type == (
        "ExactDrawingPageAndExplicitViewLockStates"
    )
    assert set_variant.transaction_behavior == "document"
    assert read_variant.transaction_behavior == "none"
    assert read_variant.provider_supplemental is True

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024


def test_view_lock_action_resolves_to_the_explicit_set_variant() -> None:
    plan = _plan(
        "drawing",
        "Attributes",
        RibbonAction(
            command_id="TechDraw_ExtensionLockUnlockView",
            label="Lock/Unlock View",
            available=True,
            kind="command",
        ),
    )
    assert (
        plan.capability_family,
        plan.operation_variant,
        plan.exact_target_type,
        plan.transaction_behavior,
        plan.background_required,
    ) == (
        DRAWING_VIEW_LOCK_CAPABILITY_NAME,
        "set",
        "ExactDrawingPageAndExplicitViewLockStates",
        "document",
        False,
    )


def test_view_lock_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_view_lock_capability_definition(registry)
    register_drawing_view_lock_capability_implementation(registry)

    assert registry.definition_names == (DRAWING_VIEW_LOCK_CAPABILITY_NAME,)
    assert registry.implementation_names == registry.definition_names
