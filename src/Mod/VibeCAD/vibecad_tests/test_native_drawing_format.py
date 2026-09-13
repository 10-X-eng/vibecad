# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing format customization."""

from __future__ import annotations

import json
from pathlib import Path

from VibeCADNativeActionManifest import _plan
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeDrawingFormatBindings import (
    register_drawing_format_capability_implementation,
)
from VibeCADNativeDrawingFormatSchema import (
    DRAWING_FORMAT_CAPABILITY_NAME,
    DRAWING_FORMAT_OPERATIONS,
    drawing_format_capability_definition,
    register_drawing_format_capability_definition,
)
from VibeCADRibbonSurface import RibbonAction
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def test_drawing_format_schema_has_three_closed_exact_branches() -> None:
    definition = drawing_format_capability_definition()
    schema = definition.provider_schema(DRAWING_FORMAT_OPERATIONS)

    assert DRAWING_FORMAT_OPERATIONS == (
        "set_dimension_format",
        "set_balloon_text",
        "apply_iso_286_fit",
    )
    assert definition.primary_classification == "mutation"
    assert not definition.preserve_operation_branches
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_FORMAT_OPERATIONS
    )
    branches = exact_provider_branches(definition, DRAWING_FORMAT_OPERATIONS)
    dimension = branches["set_dimension_format"]
    balloon = branches["set_balloon_text"]
    iso_fit = branches["apply_iso_286_fit"]
    assert dimension["required"] == [
        "operation",
        "dimension",
        "format_spec",
    ]
    assert balloon["required"] == ["operation", "balloon", "text"]
    assert iso_fit["required"] == [
        "operation",
        "dimension",
        "tolerance_class",
    ]
    assert len(iso_fit["properties"]["tolerance_class"]["enum"]) == 20
    for branch, target_name, value_name in (
        (dimension, "dimension", "format_spec"),
        (balloon, "balloon", "text"),
    ):
        assert branch["additionalProperties"] is False
        assert branch["properties"][target_name]["additionalProperties"] is False
        assert branch["properties"][target_name]["required"] == [
            "object_name",
            "expected_format_state_sha256",
        ]
        assert branch["properties"][value_name]["maxLength"] == 512
        assert "minLength" not in branch["properties"][value_name]
        assert "preview" not in branch["properties"]
        assert "property_name" not in branch["properties"]

    assert definition.variants[0].exact_target_type == (
        "ExactDrawingDimensionAndCompleteFormat"
    )
    assert definition.variants[1].exact_target_type == (
        "ExactDrawingBalloonAndLiteralText"
    )
    assert definition.variants[2].exact_target_type == (
        "ExactDrawingDimensionAndIso286ToleranceClass"
    )
    assert not definition.variants[0].provider_supplemental
    assert definition.variants[1].provider_supplemental
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 6 * 1024


def test_customize_format_action_resolves_to_exact_native_family() -> None:
    plan = _plan(
        "drawing",
        "Attributes",
        RibbonAction(
            command_id="TechDraw_ExtensionCustomizeFormat",
            label="Customize Format Label",
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
        DRAWING_FORMAT_CAPABILITY_NAME,
        "set_dimension_format",
        "ExactDrawingDimensionAndCompleteFormat",
        "document",
        False,
    )


def test_drawing_format_registry_is_complete() -> None:
    registry = NativeCapabilityRegistry()
    register_drawing_format_capability_definition(registry)
    register_drawing_format_capability_implementation(registry)
    assert registry.definition_names == (DRAWING_FORMAT_CAPABILITY_NAME,)
    assert registry.implementation_names == registry.definition_names


def test_human_and_native_share_compiled_format_builder() -> None:
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskCustomizeFormat.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "FormatBuilder.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    native = (MOD_ROOT / "VibeCAD" / "VibeCADNativeDrawingFormat.py").read_text(
        encoding="utf-8"
    )
    assert "applyDrawingFormatCustomization(selectedObject" in task
    assert "validateDrawingFormatCustomization(" in task
    assert "FormatSpec.setValue(value)" in builder
    assert "Text.setValue(value)" in builder
    assert "dimension->formatValue(" in builder
    assert '"applyDrawingFormatCustomization"' in binding
    assert "TechDrawGui.applyDrawingFormatCustomization(" in native
