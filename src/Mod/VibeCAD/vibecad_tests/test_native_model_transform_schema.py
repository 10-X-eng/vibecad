# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from VibeCADNativeDesignCircularPattern import prepare_design_circular_pattern
from VibeCADNativeDesignLinearPattern import prepare_design_linear_pattern
from VibeCADNativeDesignMirror import prepare_design_mirror
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelTransformSchema import model_transform_capability_definition


def _values() -> dict[str, object]:
    return {
        "label": "Exact Mirror",
        "source": {
            "kind": "feature",
            "operation": {"object_name": "AdditiveSource"},
            "targets": [
                {"object_name": "FirstBody"},
                {"object_name": "SecondBody"},
            ],
        },
        "definition": {
            "kind": "mirror",
            "plane": {
                "kind": "explicit",
                "origin_mm": {"x": 2.0, "y": 0.0, "z": 0.0},
                "normal": {"x": 1.0, "y": 0.0, "z": 0.0},
            },
        },
    }


def test_transform_contract_uses_one_compact_typed_pattern_variant() -> None:
    definition = model_transform_capability_definition()
    branch = definition.provider_schema(("pattern",))["parameters"]["oneOf"][0]
    variant = definition.variants[0]

    assert definition.name == "model.transform"
    assert variant.operation == "pattern"
    assert variant.action_ids == frozenset(
        {
            "PartDesign_DesignMirror",
            "PartDesign_DesignLinearPattern",
            "PartDesign_DesignCircularPattern",
        }
    )
    assert branch["required"] == ["label", "source", "definition"]
    assert branch["properties"]["operation"]["const"] == "pattern"
    assert branch["additionalProperties"] is False
    sources = branch["properties"]["source"]["oneOf"]
    assert [item["properties"]["kind"]["const"] for item in sources] == [
        "body",
        "feature",
    ]
    assert sources[1]["properties"]["targets"]["minItems"] == 1
    assert sources[1]["properties"]["targets"]["maxItems"] == 16
    definitions = branch["properties"]["definition"]["oneOf"]
    assert [item["properties"]["kind"]["const"] for item in definitions] == [
        "mirror",
        "linear",
        "circular",
    ]


def test_provider_transform_definition_names_fields_for_each_kind() -> None:
    definition = model_transform_capability_definition()
    compact = definition.provider_schema(("pattern", "scale"))["parameters"]
    fields = compact["properties"]["definition"]["description"]

    for expected in (
        "mirror=plane",
        "linear=direction,spacing_mm,occurrences,centered",
        "circular=axis,angle_degrees,occurrences,reversed",
        "uniform=factor,center_mm",
        "non_uniform=x_factor,y_factor,z_factor,center_mm",
    ):
        assert expected in fields


def test_scale_contract_matches_the_fixed_modify_task_controls() -> None:
    definition = model_transform_capability_definition()
    branch = definition.provider_schema(("scale",))["parameters"]["oneOf"][0]
    variant = next(item for item in definition.variants if item.operation == "scale")

    assert variant.action_ids == frozenset({"PartDesign_Scale"})
    assert variant.exact_target_type == "Body[] + ScaleDefinition"
    assert branch["required"] == ["label", "targets", "definition"]
    assert branch["properties"]["operation"]["const"] == "scale"
    assert branch["additionalProperties"] is False
    assert branch["properties"]["targets"]["minItems"] == 1
    assert branch["properties"]["targets"]["maxItems"] == 16
    modes = branch["properties"]["definition"]["oneOf"]
    assert [item["properties"]["kind"]["const"] for item in modes] == [
        "uniform",
        "non_uniform",
    ]
    assert modes[0]["required"] == ["kind", "factor", "center_mm"]
    assert modes[1]["required"] == [
        "kind",
        "x_factor",
        "y_factor",
        "z_factor",
        "center_mm",
    ]
    factor = modes[0]["properties"]["factor"]
    assert factor == {"type": "number", "minimum": 1.0e-6, "maximum": 1.0e6}
    assert all(item["additionalProperties"] is False for item in modes)


def test_mirror_plane_contract_matches_numeric_datum_sketch_and_face_controls() -> None:
    branch = model_transform_capability_definition().provider_schema(("pattern",))[
        "parameters"
    ]["oneOf"][0]
    mirror = branch["properties"]["definition"]["oneOf"][0]
    planes = mirror["properties"]["plane"]["oneOf"]

    assert [item["properties"]["kind"]["const"] for item in planes] == [
        "explicit",
        "object",
        "subelement",
    ]
    assert planes[0]["required"] == ["kind", "origin_mm", "normal"]
    assert planes[2]["properties"]["subelement"]["pattern"] == (
        r"^(?:Face[1-9][0-9]*|N_Axis)$"
    )
    assert all(item["additionalProperties"] is False for item in planes)


def test_transform_contract_has_no_result_override_or_retired_escape_hatch() -> None:
    serialized = json.dumps(
        model_transform_capability_definition().provider_schema(("pattern",)),
        sort_keys=True,
    )
    for forbidden in (
        "result_mode",
        "base_feature",
        "body_tip",
        "selection",
        "runCommand",
        "workbench",
        "support_transform",
    ):
        assert forbidden not in serialized

    scale = json.dumps(
        model_transform_capability_definition().provider_schema(("scale",)),
        sort_keys=True,
    )
    for forbidden in (
        "result_mode",
        "destination_component",
        "selection",
        "runCommand",
        "workbench",
    ):
        assert forbidden not in scale


def test_linear_contract_matches_every_current_task_control() -> None:
    branch = model_transform_capability_definition().provider_schema(("pattern",))[
        "parameters"
    ]["oneOf"][0]
    linear = branch["properties"]["definition"]["oneOf"][1]
    directions = linear["properties"]["direction"]["oneOf"]

    assert linear["required"] == [
        "kind",
        "direction",
        "spacing_mm",
        "occurrences",
        "centered",
    ]
    assert [item["properties"]["kind"]["const"] for item in directions] == [
        "explicit",
        "object",
        "subelement",
    ]
    assert linear["properties"]["spacing_mm"]["maximum"] == 1.0e9
    assert linear["properties"]["occurrences"] == {
        "type": "integer",
        "minimum": 2,
        "maximum": 10000,
    }
    assert directions[2]["properties"]["subelement"]["pattern"] == (
        r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|Edge[1-9][0-9]*)$"
    )


def test_circular_contract_matches_every_current_task_control() -> None:
    branch = model_transform_capability_definition().provider_schema(("pattern",))[
        "parameters"
    ]["oneOf"][0]
    circular = branch["properties"]["definition"]["oneOf"][2]
    axes = circular["properties"]["axis"]["oneOf"]

    assert circular["required"] == [
        "kind",
        "axis",
        "angle_degrees",
        "occurrences",
        "reversed",
    ]
    assert [item["properties"]["kind"]["const"] for item in axes] == [
        "global_axis",
        "explicit",
        "object",
        "subelement",
    ]
    assert axes[0]["required"] == ["kind", "axis"]
    assert axes[1]["required"] == ["kind", "origin_mm", "direction"]
    assert circular["properties"]["angle_degrees"] == {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 360.0,
    }
    assert circular["properties"]["occurrences"] == {
        "type": "integer",
        "minimum": 2,
        "maximum": 10000,
    }
    assert axes[3]["properties"]["subelement"]["pattern"] == (
        r"^(?:H_Axis|V_Axis|N_Axis|Axis[0-9]+|Edge[1-9][0-9]*)$"
    )


def test_mirror_parser_preserves_exact_feature_source_targets_and_plane() -> None:
    prepared = prepare_design_mirror("document-a", _values())

    assert prepared.source.kind == "feature"
    assert prepared.source.source_ref.object_name == "AdditiveSource"
    assert [item.object_name for item in prepared.source.target_refs] == [
        "FirstBody",
        "SecondBody",
    ]
    assert prepared.plane.kind == "explicit"
    assert prepared.plane.origin == (2.0, 0.0, 0.0)
    assert prepared.plane.normal == (1.0, 0.0, 0.0)


def test_mirror_parser_preserves_body_and_exact_reference_forms() -> None:
    values = _values()
    values["source"] = {
        "kind": "body",
        "body": {"object_name": "SourceBody"},
    }
    values["definition"] = {
        "kind": "mirror",
        "plane": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Face4",
        },
    }

    prepared = prepare_design_mirror("document-a", values)

    assert prepared.source.kind == "body"
    assert prepared.source.source_ref.object_name == "SourceBody"
    assert prepared.source.target_refs == ()
    assert prepared.plane.reference.object_ref.object_name == "ReferenceState"
    assert prepared.plane.reference.subelements == ("Face4",)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda values: values["definition"]["plane"].update(
                normal={"x": 0.0, "y": 0.0, "z": 0.0}
            ),
            "non-zero",
        ),
        (
            lambda values: values["definition"]["plane"].update(
                normal={"x": True, "y": 0.0, "z": 0.0}
            ),
            "contain numbers",
        ),
        (
            lambda values: values["source"]["targets"].append(
                {"object_name": "FirstBody"}
            ),
            "repeats",
        ),
        (
            lambda values: values["definition"].update(kind="linear"),
            "not Mirror",
        ),
    ),
)
def test_mirror_parser_rejects_ambiguous_or_invalid_contracts(mutate, message) -> None:
    values = _values()
    mutate(values)

    with pytest.raises(NativeModelError, match=message):
        prepare_design_mirror("document-a", values)


def _linear_values() -> dict[str, object]:
    values = _values()
    values["definition"] = {
        "kind": "linear",
        "direction": {
            "kind": "subelement",
            "object_name": "DirectionSketch",
            "subelement": "H_Axis",
        },
        "spacing_mm": 12.5,
        "occurrences": 4,
        "centered": True,
    }
    return values


def test_linear_parser_preserves_sources_reference_and_every_control() -> None:
    prepared = prepare_design_linear_pattern("document-a", _linear_values())

    assert prepared.source.kind == "feature"
    assert prepared.direction.kind == "subelement"
    assert prepared.direction.reference.object_ref.object_name == "DirectionSketch"
    assert prepared.direction.reference.subelements == ("H_Axis",)
    assert prepared.spacing_mm == 12.5
    assert prepared.occurrences == 4
    assert prepared.centered is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("spacing_mm", True, "must be a number"),
        ("spacing_mm", 0.0, "finite, positive"),
        ("occurrences", True, "must be an integer"),
        ("occurrences", 1, "from 2 to 10000"),
        ("occurrences", 10001, "from 2 to 10000"),
        ("centered", 1, "must be boolean"),
    ),
)
def test_linear_parser_rejects_invalid_task_controls(field, value, message) -> None:
    values = _linear_values()
    values["definition"][field] = value

    with pytest.raises(NativeModelError, match=message):
        prepare_design_linear_pattern("document-a", values)


def test_linear_parser_rejects_zero_or_boolean_direction_vectors() -> None:
    for vector in (
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": True, "y": 0.0, "z": 0.0},
    ):
        values = _linear_values()
        values["definition"]["direction"] = {
            "kind": "explicit",
            "vector": vector,
        }
        with pytest.raises(NativeModelError):
            prepare_design_linear_pattern("document-a", values)


def _circular_values() -> dict[str, object]:
    values = _values()
    values["definition"] = {
        "kind": "circular",
        "axis": {
            "kind": "explicit",
            "origin_mm": {"x": 4.0, "y": 5.0, "z": 0.0},
            "direction": {"x": 0.0, "y": 0.0, "z": 1.0},
        },
        "angle_degrees": 180.0,
        "occurrences": 3,
        "reversed": True,
    }
    return values


def test_circular_parser_preserves_sources_axis_and_every_control() -> None:
    prepared = prepare_design_circular_pattern("document-a", _circular_values())

    assert prepared.source.kind == "feature"
    assert prepared.axis.kind == "explicit"
    assert prepared.axis.origin == (4.0, 5.0, 0.0)
    assert prepared.axis.direction == (0.0, 0.0, 1.0)
    assert prepared.angle_degrees == 180.0
    assert prepared.occurrences == 3
    assert prepared.reversed is True


def test_circular_parser_preserves_exact_reference_axis() -> None:
    values = _circular_values()
    values["definition"]["axis"] = {
        "kind": "subelement",
        "object_name": "AxisBody",
        "subelement": "Edge2",
    }

    prepared = prepare_design_circular_pattern("document-a", values)

    assert prepared.axis.reference.object_ref.object_name == "AxisBody"
    assert prepared.axis.reference.subelements == ("Edge2",)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("angle_degrees", True, "must be a number"),
        ("angle_degrees", 0.0, "finite, positive"),
        ("angle_degrees", 360.1, "at most 360"),
        ("occurrences", True, "must be an integer"),
        ("occurrences", 1, "from 2 to 10000"),
        ("occurrences", 10001, "from 2 to 10000"),
        ("reversed", 1, "must be boolean"),
    ),
)
def test_circular_parser_rejects_invalid_task_controls(field, value, message) -> None:
    values = _circular_values()
    values["definition"][field] = value

    with pytest.raises(NativeModelError, match=message):
        prepare_design_circular_pattern("document-a", values)


def test_circular_parser_rejects_zero_or_boolean_axis_directions() -> None:
    for direction in (
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": False, "y": 0.0, "z": 1.0},
    ):
        values = _circular_values()
        values["definition"]["axis"]["direction"] = direction
        with pytest.raises(NativeModelError):
            prepare_design_circular_pattern("document-a", values)
