# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract checks for Mesh visual comparison."""

from __future__ import annotations

from VibeCADNativeActionManifest import (
    _BACKGROUND_COMMAND_IDS,
    _CAPABILITY_OVERRIDES,
    _OPERATION_VARIANT_OVERRIDES,
    _READ_COMMAND_IDS,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    provider_visible_native_schema,
)
from VibeCADNativeInspectionCompareSchema import (
    INSPECTION_COMPARE_CAPABILITY_NAME,
    register_inspection_compare_capability_definition,
)


def test_visual_inspection_is_a_background_mesh_comparison() -> None:
    command = "Inspection_VisualInspection"

    assert command not in _READ_COMMAND_IDS
    assert command in _BACKGROUND_COMMAND_IDS
    assert _CAPABILITY_OVERRIDES[command] == INSPECTION_COMPARE_CAPABILITY_NAME
    assert _OPERATION_VARIANT_OVERRIDES[command] == "compare"


def test_mesh_compare_contract_has_one_obvious_payload() -> None:
    registry = NativeCapabilityRegistry()
    register_inspection_compare_capability_definition(registry)
    definition = registry.definition(INSPECTION_COMPARE_CAPABILITY_NAME)

    assert definition is not None
    assert len(definition.variants) == 1
    assert definition.variants[0].background_required
    schema = provider_visible_native_schema(
        definition.provider_schema(("compare",))
    )["parameters"]["oneOf"][0]
    assert schema["required"] == [
        "actual",
        "nominals",
        "search_radius_mm",
        "tolerance_mm",
    ]
    assert set(schema["properties"]) == {
        "actual",
        "nominals",
        "search_radius_mm",
        "tolerance_mm",
        "require_complete",
        "result_label",
    }
    assert schema["properties"]["actual"]["required"] == [
        "object_name",
    ]
    assert schema["properties"]["nominals"]["items"] == schema["properties"][
        "actual"
    ]
