# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyJointIntent as intent_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyJointIntent import _coupling_target_connector
from VibeCADNativeAssemblyJointRuntime import _intent_values
from VibeCADNativeAssemblyJointSchema import (
    assembly_belt_capability_definition,
    assembly_gears_capability_definition,
    assembly_rack_pinion_capability_definition,
    assembly_relation_capability_definition,
    assembly_screw_capability_definition,
)


def _branch(definition, operation: str) -> dict:
    operations = tuple(variant.operation for variant in definition.variants)
    branches = definition.provider_schema(operations)["parameters"]["oneOf"]
    return next(
        branch
        for branch in branches
        if branch["properties"]["operation"]["const"] == operation
    )


def test_relation_contract_has_one_compact_natural_discriminator() -> None:
    definition = assembly_relation_capability_definition()

    assert definition.description == (
        "Create a distance, parallel, perpendicular, or angle relation between "
        "endpoints."
    )
    assert tuple(variant.operation for variant in definition.variants) == ("create",)
    schema = definition.provider_schema(("create",))["parameters"]["oneOf"][0]
    assert set(schema["required"]) == {"first", "second", "relation"}
    assert "operation" not in schema["required"]
    assert len(json.dumps(schema, separators=(",", ":"))) < 3_500
    assert schema["properties"]["relation"] == {
        "type": "string",
        "enum": ["distance", "parallel", "perpendicular", "angle"],
    }
    assert schema["properties"]["distance_mm"]["description"] == (
        "Signed distance for a distance relation."
    )
    assert schema["properties"]["angle_degrees"]["description"] == (
        "Angle for an angle relation."
    )


def test_coupling_contract_reuses_exact_prerequisite_joint_sides() -> None:
    expected = {
        "assembly.rack_pinion": (
            assembly_rack_pinion_capability_definition,
            "rack_pinion",
            {
                "slider_joint",
                "rack_component",
                "revolute_joint",
                "pinion_component",
                "pinion_pitch_radius_mm",
            },
        ),
        "assembly.screw": (
            assembly_screw_capability_definition,
            "screw",
            {
                "slider_joint",
                "slider_component",
                "revolute_joint",
                "revolute_component",
                "lead_mm",
            },
        ),
        "assembly.belt": (
            assembly_belt_capability_definition,
            "belt",
            {
            "first_joint",
            "first_component",
            "second_joint",
            "second_component",
            "first_pulley_radius_mm",
            "second_pulley_radius_mm",
            },
        ),
        "assembly.gears": (
            assembly_gears_capability_definition,
            "gears",
            {
            "first_joint",
            "first_component",
            "second_joint",
            "second_component",
            "first_pitch_radius_mm",
            "second_pitch_radius_mm",
            },
        ),
    }
    for name, (factory, operation, required) in expected.items():
        definition = factory()
        assert definition.name == name
        assert tuple(variant.operation for variant in definition.variants) == (
            operation,
        )
        schema = _branch(definition, operation)
        assert set(schema["required"]) == required
        for field in required - {
            "pinion_pitch_radius_mm",
            "lead_mm",
            "first_pulley_radius_mm",
            "second_pulley_radius_mm",
            "first_pitch_radius_mm",
            "second_pitch_radius_mm",
        }:
            reference_schema = dict(schema["properties"][field])
            reference_schema.pop("description", None)
            assert reference_schema == {
                "type": "object",
                "properties": {
                    "object_name": {
                        "type": "string",
                        "maxLength": 128,
                        "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
                    }
                },
                "required": ["object_name"],
                "additionalProperties": False,
            }
        assert schema["additionalProperties"] is False
    belt = _branch(assembly_belt_capability_definition(), "belt")["properties"]
    assert belt["first_pulley_radius_mm"]["description"] == "First pulley radius."
    assert belt["second_pulley_radius_mm"]["description"] == "Second pulley radius."
    gears = _branch(assembly_gears_capability_definition(), "gears")["properties"]
    assert gears["first_pitch_radius_mm"]["description"] == "First gear pitch radius."
    assert gears["second_pitch_radius_mm"]["description"] == "Second gear pitch radius."
    assert assembly_belt_capability_definition().description == (
        "Couple two pulley rotations in the same direction."
    )
    assert assembly_gears_capability_definition().description == (
        "Couple two gear rotations in opposite directions."
    )
    screw = _branch(assembly_screw_capability_definition(), "screw")["properties"]
    assert screw["slider_joint"]["description"] == "Slider joint."
    assert screw["slider_component"]["description"] == (
        "Moving component of slider_joint."
    )
    assert screw["revolute_joint"]["description"] == "Coaxial Revolute joint."
    assert screw["revolute_component"]["description"] == (
        "Moving component of revolute_joint."
    )
    assert assembly_screw_capability_definition().description == (
        "Couple a Slider and coaxial Revolute joint by screw lead."
    )
    assert screw["lead_mm"]["description"] == "Axial travel per revolution."
    rack = _branch(
        assembly_rack_pinion_capability_definition(), "rack_pinion"
    )["properties"]
    assert rack["slider_joint"]["description"] == "Rack Slider joint."
    assert rack["rack_component"]["description"] == "Moving rack component."
    assert rack["revolute_joint"]["description"] == "Pinion Revolute joint."
    assert rack["pinion_component"]["description"] == "Moving pinion component."
    assert rack["pinion_pitch_radius_mm"]["description"] == (
        "Pinion pitch radius."
    )
    assert assembly_rack_pinion_capability_definition().description == (
        "Couple a rack Slider and perpendicular pinion Revolute joint by pitch radius."
    )


def test_provider_operations_route_only_complete_exact_values() -> None:
    gear_values = {
        "first_joint": {"object_name": "Joint"},
        "first_component": {"object_name": "GearOne"},
        "second_joint": {"object_name": "Joint001"},
        "second_component": {"object_name": "GearTwo"},
        "first_pitch_radius_mm": 20.0,
        "second_pitch_radius_mm": 40.0,
    }

    assert _intent_values({"operation": "gears", **gear_values}) == (
        "create_gears",
        {
            "first_joint": "Joint",
            "first_component": "GearOne",
            "second_joint": "Joint001",
            "second_component": "GearTwo",
            "radius1_mm": 20.0,
            "radius2_mm": 40.0,
        },
    )
    screw_values = {
        "slider_joint": {"object_name": "SliderJoint"},
        "slider_component": {"object_name": "Carriage"},
        "revolute_joint": {"object_name": "RevoluteJoint"},
        "revolute_component": {"object_name": "LeadScrew"},
        "lead_mm": 4.0,
    }
    assert _intent_values({"operation": "screw", **screw_values}) == (
        "create_screw",
        {
            "first_joint": "SliderJoint",
            "first_component": "Carriage",
            "second_joint": "RevoluteJoint",
            "second_component": "LeadScrew",
            "thread_pitch_mm": 4.0,
        },
    )
    rack_values = {
        "slider_joint": {"object_name": "RackSlider"},
        "rack_component": {"object_name": "Rack"},
        "revolute_joint": {"object_name": "PinionRevolute"},
        "pinion_component": {"object_name": "Pinion"},
        "pinion_pitch_radius_mm": 20.0,
    }
    assert _intent_values({"operation": "rack_pinion", **rack_values}) == (
        "create_rack_pinion",
        {
            "first_joint": "RackSlider",
            "first_component": "Rack",
            "second_joint": "PinionRevolute",
            "second_component": "Pinion",
            "pitch_radius_mm": 20.0,
        },
    )
    assert _intent_values(
        {
            "operation": "create",
            "first": {
                "component": "PartOne",
                "connector_type": "element",
                "connector": "Face1",
            },
            "second": {
                "component": "PartTwo",
                "connector_type": "element",
                "connector": "Face2",
            },
            "relation": "distance",
            "distance_mm": 12.0,
        }
    )[0] == "create_distance"
    with pytest.raises(NativeArgumentError, match="do not match"):
        _intent_values(
            {
                "operation": "gears",
                "first_joint": "Joint",
                "first_component": "GearOne",
                "second_joint": "Joint001",
                "second_component": "GearTwo",
            }
        )


def test_coupling_target_reuses_one_persisted_prerequisite_side(
    monkeypatch,
) -> None:
    first_component = SimpleNamespace(Name="GearOne")
    second_component = SimpleNamespace(Name="Support")
    first_offset = object()
    second_offset = object()
    joint = SimpleNamespace(
        Name="Joint",
        JointType="Revolute",
        Reference1=[first_component, ["Face6", "Face6"]],
        Reference2=[second_component, ["Origin", "Origin"]],
        Offset1=first_offset,
        Offset2=second_offset,
    )
    objects = {
        first_component.Name: first_component,
        second_component.Name: second_component,
        joint.Name: joint,
    }
    monkeypatch.setattr(
        intent_module,
        "resolve_object",
        lambda document, reference: objects[reference.object_name],
    )
    monkeypatch.setattr(
        intent_module,
        "component_placement",
        lambda component: ("component", component.Name),
    )
    monkeypatch.setattr(
        intent_module,
        "placement_summary",
        lambda placement: {"placement": repr(placement)},
    )

    result = _coupling_target_connector(
        object(),
        "document-uid",
        SimpleNamespace(regular_joints=(joint,)),
        {"joint": joint.Name, "component": first_component.Name},
        "first_gear",
        "Revolute",
    )

    assert result == {
        "component": {"object_name": first_component.Name},
        "element_path": "Face6",
        "anchor_path": "Face6",
        "offset": {"placement": repr(first_offset)},
        "expected_component_placement": {
            "placement": repr(("component", first_component.Name))
        },
    }
