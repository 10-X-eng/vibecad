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
    DRAWING_VIEW_LOCK_CAPABILITY_NAMES,
    drawing_view_lock_capability_definitions,
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
    read_definition, set_definition = drawing_view_lock_capability_definitions()
    assert DRAWING_VIEW_LOCK_CAPABILITY_NAMES == (
        "drawing.view_locks",
        "drawing.set_view_locks",
    )
    read_schema = read_definition.provider_schema(("read",))
    set_schema = set_definition.provider_schema(("set",))
    assert read_definition.primary_classification == "read"
    assert set_definition.primary_classification == "mutation"

    set_branch = _branch(set_schema, "set")
    assert set_branch["additionalProperties"] is False
    views = set_branch["properties"]["views"]
    assert views["minItems"] == 1
    assert views["maxItems"] == 32
    change = views["items"]
    assert change["additionalProperties"] is False
    assert change["properties"]["locked"]["type"] == "boolean"
    assert "toggle" not in change["properties"]["locked"].get("description", "")

    read_branch = _branch(read_schema, "read")
    assert read_branch["additionalProperties"] is False
    assert read_branch["properties"]["offset"]["maximum"] == 512
    assert read_branch["properties"]["offset"]["default"] == 0
    assert "page_size" not in read_branch["properties"]
    assert read_branch["properties"]["page"]["required"] == ["object_name"]

    read_variant = read_definition.variants[0]
    set_variant = set_definition.variants[0]
    assert set_variant.action_ids == frozenset(
        {"TechDraw_ExtensionLockUnlockView"}
    )
    assert set_variant.exact_target_type == (
        "ExactDrawingPageAndExplicitViewLockStates"
    )
    assert set_variant.transaction_behavior == "document"
    assert read_variant.transaction_behavior == "none"
    assert read_variant.provider_supplemental is True

    encoded = json.dumps(
        (read_schema, set_schema), sort_keys=True, separators=(",", ":")
    )
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
        "drawing.set_view_locks",
        "set",
        "ExactDrawingPageAndExplicitViewLockStates",
        "document",
        False,
    )


def test_view_lock_registry_has_one_definition_and_implementation() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_view_lock_capability_definition(registry)
    register_drawing_view_lock_capability_implementation(registry)

    assert registry.definition_names == tuple(sorted(DRAWING_VIEW_LOCK_CAPABILITY_NAMES))
    assert registry.implementation_names == registry.definition_names
