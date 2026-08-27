# SPDX-License-Identifier: LGPL-2.1-or-later

"""Natural first-page and no-template defaults for Native CAM contracts."""

from __future__ import annotations

from VibeCADNativeManufactureInspectSchema import (
    manufacture_inspect_capability_definition,
)
from VibeCADNativeManufactureFocusedInspectSchema import (
    manufacture_focused_inspect_capability_definitions,
)
from VibeCADNativeManufactureFocusedModifySchema import (
    manufacture_focused_modify_capability_definitions,
)
from VibeCADNativeManufactureFocusedPostSchema import (
    manufacture_focused_post_capability_definitions,
)
from VibeCADNativeManufactureJobSchema import manufacture_job_capability_definition
from VibeCADNativeManufactureOperationSchema import (
    manufacture_operation_capability_definition,
)
from VibeCADNativeManufactureFocusedOperationSchema import (
    manufacture_focused_operation_capability_definitions,
)
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeManufactureToolSchema import (
    manufacture_tool_catalog_capability_definition,
    manufacture_tool_capability_definition,
)
from VibeCADNativeManufactureFocusedToolSchema import (
    manufacture_focused_tool_capability_definitions,
)


def _branch(definition, operation: str) -> dict:
    return definition.provider_schema((operation,))["parameters"]["oneOf"][0]


def test_create_job_does_not_echo_host_state_or_template_sentinels() -> None:
    definition = manufacture_job_capability_definition()
    branch = _branch(definition, "create_job")
    template_branch = _branch(definition, "create_job_from_template")

    assert set(branch["properties"]) == {"operation", "label", "models"}
    assert set(branch["required"]) == {"label", "models"}
    assert set(template_branch["properties"]) == {
        "operation",
        "label",
        "models",
        "template",
    }
    assert set(template_branch["required"]) == {
        "label",
        "models",
        "template",
    }
    model = branch["properties"]["models"]["items"]
    assert set(model["properties"]) == {
        "object_name",
        "expected_state_sha256",
        "replace_in_history",
    }
    assert set(model["required"]) == {
        "object_name",
        "expected_state_sha256",
        "replace_in_history",
    }


def test_catalog_and_setup_reads_default_to_the_first_bounded_page() -> None:
    catalog = _branch(
        manufacture_tool_catalog_capability_definition(),
        "list_tools",
    )
    setups = _branch(manufacture_inspect_capability_definition(), "list_setups")
    job = _branch(manufacture_inspect_capability_definition(), "read_job")

    assert catalog["required"] == []
    assert "expected_catalog_state_sha256" not in catalog["properties"]
    assert catalog["properties"]["offset"]["default"] == 0
    assert catalog["properties"]["page_size"]["default"] == 32
    assert setups["required"] == []
    assert setups["properties"]["query"]["default"] == ""
    assert setups["properties"]["offset"]["default"] == 0
    assert setups["properties"]["page_size"]["default"] == 32
    assert set(job["required"]) == {"target"}
    assert job["properties"]["operation_offset"]["default"] == 0
    assert job["properties"]["page_size"]["default"] == 32


def test_adding_a_catalog_tool_only_requires_the_two_exact_owners() -> None:
    branch = _branch(
        manufacture_tool_capability_definition(),
        "create_controller",
    )

    assert set(branch["required"]) == {"job_target", "catalog_tool"}
    assert branch["properties"]["tool_property_changes"]["default"] == []


def test_tool_mutations_publish_as_three_focused_tools() -> None:
    definitions = {
        definition.name: definition
        for definition in manufacture_focused_tool_capability_definitions()
    }
    expected = {
        "manufacture.add_tool": "create_controller",
        "manufacture.set_controller": "update_controller",
        "manufacture.update_tool": "update_tool_bit",
    }

    assert {
        name: definition.variants[0].operation
        for name, definition in definitions.items()
    } == expected
    for definition in definitions.values():
        schema = provider_visible_native_schema(
            definition.provider_schema((definition.variants[0].operation,))
        )
        branch = schema["parameters"]["oneOf"][0]
        assert "operation" not in branch["properties"]


def test_model_geometry_read_is_exact_paged_and_drilling_aware() -> None:
    definition = manufacture_inspect_capability_definition()
    branch = _branch(definition, "read_model_geometry")

    assert set(branch["required"]) == {"target", "elements"}
    assert branch["properties"]["elements"]["enum"] == [
        "faces",
        "edges",
        "drillable",
    ]
    assert branch["properties"]["offset"]["default"] == 0
    assert branch["properties"]["page_size"]["default"] == 32
    assert next(
        variant
        for variant in definition.variants
        if variant.operation == "read_model_geometry"
    ).background_required is True


def test_cam_inspection_reads_publish_as_focused_tools() -> None:
    definitions = {
        definition.name: definition
        for definition in manufacture_focused_inspect_capability_definitions()
    }
    assert {
        name: definition.variants[0].operation
        for name, definition in definitions.items()
    } == {
        "manufacture.setups": "list_setups",
        "manufacture.read_setup": "read_job",
        "manufacture.setup_options": "search_setup_options",
        "manufacture.validate": "validate_job",
        "manufacture.toolpath": "inspect_toolpath",
        "manufacture.loop": "detect_loop",
        "manufacture.geometry": "read_model_geometry",
        "manufacture.threads": "read_thread_catalog",
    }
    geometry = definitions["manufacture.geometry"]
    schema = provider_visible_native_schema(
        geometry.provider_schema(("read_model_geometry",))
    )
    branch = schema["parameters"]["oneOf"][0]
    assert set(branch["required"]) == {"target", "elements"}
    assert "operation" not in branch["properties"]


def test_cam_operation_edits_publish_as_two_intent_tools() -> None:
    definitions = {
        definition.name: definition
        for definition in manufacture_focused_modify_capability_definitions()
    }
    assert {
        name: tuple(variant.operation for variant in definition.variants)
        for name, definition in definitions.items()
    } == {
        "manufacture.operations": ("set_active", "copy_operations"),
        "manufacture.dressup": (
            "array_dressup",
            "axis_map_dressup",
            "dogbone_dressup",
            "drag_knife_dressup",
            "lead_in_out_dressup",
            "path_boundary_dressup",
            "mirror_dressup",
            "ramp_entry_dressup",
            "tag_dressup",
            "z_correct_dressup",
        ),
    }


def test_cam_post_scopes_publish_as_single_intent_tools() -> None:
    definitions = manufacture_focused_post_capability_definitions()
    assert {
        definition.name: tuple(variant.operation for variant in definition.variants)
        for definition in definitions
    } == {
        "manufacture.post_job": ("complete_job",),
        "manufacture.post_selected": ("selected_operations",),
    }
    for definition in definitions:
        schema = provider_visible_native_schema(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
        )
        assert "operation" not in schema["parameters"]["oneOf"][0]["properties"]


def test_common_milling_operations_inherit_setup_defaults() -> None:
    definition = manufacture_operation_capability_definition()
    facing = _branch(definition, "mill_facing")
    pocket = _branch(definition, "pocket_shape")
    drilling = _branch(definition, "drilling")
    profile = _branch(definition, "profile")

    assert set(facing["properties"]) == {
        "operation",
        "job",
        "tool_controller",
    }
    assert set(facing["required"]) == {"job", "tool_controller"}
    for branch in (pocket, drilling):
        assert set(branch["properties"]) == {
            "operation",
            "job",
            "tool_controller",
            "geometry",
        }
        assert set(branch["required"]) == {
            "job",
            "tool_controller",
            "geometry",
        }
        geometry = branch["properties"]["geometry"]
        assert geometry["type"] == "array"
        assert set(geometry["items"]["required"]) == {"model", "subelements"}
    assert set(profile["properties"]) == {
        "operation",
        "job",
        "tool_controller",
        "geometry",
        "cut_side",
    }
    assert set(profile["required"]) == {
        "job",
        "tool_controller",
        "geometry",
        "cut_side",
    }


def test_common_milling_provider_tools_are_focused_single_operations() -> None:
    definitions = {
        definition.name: definition
        for definition in manufacture_focused_operation_capability_definitions()
    }
    expected = {
        "manufacture.face": {"job", "tool_controller"},
        "manufacture.pocket": {"job", "tool_controller", "geometry"},
        "manufacture.profile": {
            "job",
            "tool_controller",
            "geometry",
            "cut_side",
        },
        "manufacture.drill": {"job", "tool_controller", "geometry"},
    }

    assert set(definitions) == {
        *expected,
        "manufacture.pocket_3d",
        "manufacture.surface",
        "manufacture.waterline",
        "manufacture.rotary_surface",
        "manufacture.helix",
        "manufacture.adaptive",
        "manufacture.slot",
        "manufacture.thread_mill",
        "manufacture.engrave",
        "manufacture.deburr",
        "manufacture.v_carve",
        "manufacture.array",
        "manufacture.copy_path",
        "manufacture.start_point",
    }
    for name, fields in expected.items():
        definition = definitions[name]
        assert len(definition.variants) == 1
        schema = provider_visible_native_schema(
            definition.provider_schema((definition.variants[0].operation,))
        )
        branch = schema["parameters"]["oneOf"][0]
        assert set(branch["properties"]) == fields
        assert set(branch["required"]) == fields
