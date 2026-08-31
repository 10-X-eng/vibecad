# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from VibeCADNativeDesignChamfer import prepare_design_chamfer
from VibeCADNativeDesignDraft import prepare_design_draft
from VibeCADNativeDesignFillet import prepare_design_fillet
from VibeCADNativeDesignThickness import prepare_design_thickness
from VibeCADNativeDesignResults import (
    DesignResultSpec,
    result_spec_from_mapping,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelDressupSchema import model_dressup_capability_definition


def _explicit_values() -> dict[str, object]:
    return {
        "label": "Edge Rounds",
        "selection": {
            "kind": "explicit",
            "targets": [
                {
                    "object_name": "BracketBody",
                    "subelements": ["Edge1", "Face2"],
                }
            ],
        },
        "radius_mm": 1.5,
    }


def _chamfer_values(definition: dict[str, object]) -> dict[str, object]:
    values = _explicit_values()
    values.pop("radius_mm")
    values["label"] = "Exact Chamfer"
    values["definition"] = definition
    return values


def _draft_values() -> dict[str, object]:
    return {
        "label": "Exact Draft",
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": "BracketBody", "subelements": ["Face2"]}
            ],
        },
        "angle_degrees": 5.0,
        "neutral_plane": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Face5",
        },
        "pull_direction": {
            "kind": "subelement",
            "object_name": "ReferenceState",
            "subelement": "Edge1",
        },
        "reversed": False,
    }


def _thickness_values() -> dict[str, object]:
    return {
        "label": "Exact Thickness",
        "selection": {
            "kind": "explicit",
            "targets": [
                {"object_name": "BracketBody", "subelements": ["Face6"]}
            ],
        },
        "thickness_mm": 1.25,
        "direction": "inward",
        "mode": "skin",
        "join": "arc",
        "intersection_handling": False,
    }


def test_fillet_contract_matches_current_radius_reference_and_all_edge_controls() -> None:
    definition = model_dressup_capability_definition()
    branch = definition.provider_schema(("fillet",))["parameters"]["oneOf"][0]

    assert definition.name == "model.dressup"
    assert definition.variants[0].action_ids == frozenset({"PartDesign_Fillet"})
    assert branch["required"] == ["label", "selection", "radius_mm"]
    assert branch["additionalProperties"] is False
    selection = branch["properties"]["selection"]
    assert [
        item["properties"]["kind"]["const"] for item in selection["oneOf"]
    ] == ["explicit", "all_edges"]
    explicit = selection["oneOf"][0]["properties"]["targets"]
    assert explicit["minItems"] == 1
    assert explicit["maxItems"] == 16
    assert explicit["items"]["properties"]["subelements"]["maxItems"] == 64


def test_fillet_contract_has_no_gui_or_retired_body_tip_escape_hatch() -> None:
    serialized = json.dumps(
        model_dressup_capability_definition().provider_schema(("fillet",)),
        sort_keys=True,
    )

    for forbidden in (
        "base_feature_name",
        "body_tip",
        "selection_summary",
        "runCommand",
        "workbench",
        "refine",
        "support_transform",
    ):
        assert forbidden not in serialized


def test_chamfer_contract_matches_every_current_task_definition_control() -> None:
    definition = model_dressup_capability_definition()
    branch = definition.provider_schema(("chamfer",))["parameters"]["oneOf"][0]

    assert definition.variants[1].action_ids == frozenset({"PartDesign_Chamfer"})
    assert branch["required"] == ["label", "selection", "definition"]
    assert branch["additionalProperties"] is False
    definitions = branch["properties"]["definition"]["oneOf"]
    assert [item["properties"]["kind"]["const"] for item in definitions] == [
        "equal_distance",
        "two_distances",
        "distance_angle",
    ]
    assert definitions[0]["required"] == ["kind", "size_mm"]
    assert definitions[1]["required"] == [
        "kind",
        "size_mm",
        "second_size_mm",
        "flip_direction",
    ]
    assert definitions[2]["required"] == [
        "kind",
        "size_mm",
        "angle_degrees",
        "flip_direction",
    ]
    assert all(item["additionalProperties"] is False for item in definitions)


def test_chamfer_contract_has_no_gui_or_retired_body_tip_escape_hatch() -> None:
    serialized = json.dumps(
        model_dressup_capability_definition().provider_schema(("chamfer",)),
        sort_keys=True,
    )

    for forbidden in (
        "base_feature_name",
        "body_tip",
        "selection_summary",
        "runCommand",
        "workbench",
        "refine",
        "support_transform",
    ):
        assert forbidden not in serialized


def test_draft_contract_matches_face_angle_reference_and_reverse_controls() -> None:
    definition = model_dressup_capability_definition()
    branch = definition.provider_schema(("draft",))["parameters"]["oneOf"][0]

    assert definition.variants[2].action_ids == frozenset({"PartDesign_Draft"})
    assert branch["required"] == [
        "label",
        "selection",
        "angle_degrees",
        "neutral_plane",
        "pull_direction",
        "reversed",
    ]
    assert branch["additionalProperties"] is False
    selection = branch["properties"]["selection"]
    assert selection["properties"]["kind"]["const"] == "explicit"
    target = selection["properties"]["targets"]["items"]
    assert target["properties"]["subelements"]["items"]["pattern"] == (
        r"^Face[1-9][0-9]*$"
    )
    for field in ("neutral_plane", "pull_direction"):
        references = branch["properties"][field]["oneOf"]
        assert [item["properties"]["kind"]["const"] for item in references] == [
            "automatic",
            "object",
            "subelement",
        ]


def test_draft_contract_has_no_gui_or_retired_body_tip_escape_hatch() -> None:
    serialized = json.dumps(
        model_dressup_capability_definition().provider_schema(("draft",)),
        sort_keys=True,
    )
    for forbidden in (
        "base_feature_name",
        "body_tip",
        "selection_summary",
        "runCommand",
        "workbench",
        "refine",
        "support_transform",
    ):
        assert forbidden not in serialized


def test_thickness_contract_matches_every_current_task_control() -> None:
    definition = model_dressup_capability_definition()
    branch = definition.provider_schema(("thickness",))["parameters"]["oneOf"][0]

    assert definition.variants[3].action_ids == frozenset({"PartDesign_Thickness"})
    assert branch["required"] == [
        "label",
        "selection",
        "thickness_mm",
        "direction",
        "mode",
        "join",
        "intersection_handling",
    ]
    assert branch["additionalProperties"] is False
    selection = branch["properties"]["selection"]
    assert selection["properties"]["kind"]["const"] == "explicit"
    assert selection["properties"]["targets"]["items"]["properties"][
        "subelements"
    ]["items"]["pattern"] == r"^Face[1-9][0-9]*$"
    assert branch["properties"]["direction"]["enum"] == ["inward", "outward"]
    assert branch["properties"]["mode"]["enum"] == [
        "skin",
        "pipe",
        "recto_verso",
    ]
    assert branch["properties"]["join"]["enum"] == ["arc", "intersection"]


def test_thickness_contract_has_no_retired_body_tip_escape_hatch() -> None:
    serialized = json.dumps(
        model_dressup_capability_definition().provider_schema(("thickness",)),
        sort_keys=True,
    )
    for forbidden in (
        "base_feature_name",
        "body_tip",
        "selection_summary",
        "runCommand",
        "workbench",
        "refine",
        "support_transform",
    ):
        assert forbidden not in serialized


def test_fillet_parser_preserves_one_group_per_exact_body() -> None:
    prepared = prepare_design_fillet("document-a", _explicit_values())

    assert prepared.radius_mm == 1.5
    assert prepared.use_all_edges is False
    assert prepared.targets[0].body.object_name == "BracketBody"
    assert prepared.targets[0].subelements == ("Edge1", "Face2")

    all_edges = _explicit_values()
    all_edges["selection"] = {
        "kind": "all_edges",
        "targets": [{"object_name": "BracketBody"}],
    }
    prepared = prepare_design_fillet("document-a", all_edges)
    assert prepared.use_all_edges is True
    assert prepared.targets[0].subelements == ()


def test_thickness_parser_preserves_exact_faces_and_all_task_controls() -> None:
    prepared = prepare_design_thickness("document-a", _thickness_values())

    assert prepared.targets[0].body.object_name == "BracketBody"
    assert prepared.targets[0].subelements == ("Face6",)
    assert prepared.thickness_mm == 1.25
    assert prepared.direction == "inward"
    assert prepared.mode == "skin"
    assert prepared.join == "arc"
    assert prepared.intersection_handling is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("thickness_mm", True),
        ("thickness_mm", 0.0),
        ("direction", "reverse"),
        ("mode", "solid"),
        ("join", "tangent"),
        ("intersection_handling", 1),
    ),
)
def test_thickness_parser_rejects_out_of_contract_controls(field, value) -> None:
    values = _thickness_values()
    values[field] = value
    with pytest.raises(NativeModelError):
        prepare_design_thickness("document-a", values)


def test_thickness_parser_rejects_non_face_targets() -> None:
    values = _thickness_values()
    values["selection"]["targets"][0]["subelements"] = ["Edge1"]
    with pytest.raises(NativeModelError, match="FaceN"):
        prepare_design_thickness("document-a", values)


def test_fillet_parser_rejects_duplicate_bodies_and_invalid_subelements() -> None:
    values = _explicit_values()
    first = values["selection"]["targets"][0]
    values["selection"]["targets"].append(
        {"object_name": "BracketBody", "subelements": ["Edge3"]}
    )
    with pytest.raises(NativeModelError, match="repeat the same target Body"):
        prepare_design_fillet("document-a", values)

    first["subelements"] = ["Vertex1"]
    values["selection"]["targets"].pop()
    with pytest.raises(NativeModelError, match="EdgeN or FaceN"):
        prepare_design_fillet("document-a", values)

    values = _explicit_values()
    values["radius_mm"] = True
    with pytest.raises(NativeModelError, match="radius must be a number"):
        prepare_design_fillet("document-a", values)


@pytest.mark.parametrize(
    ("definition", "kind", "second_size", "angle", "flip"),
    (
        (
            {"kind": "equal_distance", "size_mm": 1.0},
            "equal_distance",
            None,
            None,
            False,
        ),
        (
            {
                "kind": "two_distances",
                "size_mm": 1.0,
                "second_size_mm": 2.0,
                "flip_direction": True,
            },
            "two_distances",
            2.0,
            None,
            True,
        ),
        (
            {
                "kind": "distance_angle",
                "size_mm": 1.0,
                "angle_degrees": 35.0,
                "flip_direction": False,
            },
            "distance_angle",
            None,
            35.0,
            False,
        ),
    ),
)
def test_chamfer_parser_preserves_each_typed_definition(
    definition,
    kind,
    second_size,
    angle,
    flip,
) -> None:
    prepared = prepare_design_chamfer(
        "document-a",
        _chamfer_values(definition),
    )

    assert prepared.definition.kind == kind
    assert prepared.definition.size_mm == 1.0
    assert prepared.definition.second_size_mm == second_size
    assert prepared.definition.angle_degrees == angle
    assert prepared.definition.flip_direction is flip
    assert prepared.targets[0].subelements == ("Edge1", "Face2")


@pytest.mark.parametrize(
    "definition",
    (
        {"kind": "equal_distance", "size_mm": True},
        {"kind": "equal_distance", "size_mm": 0.0},
        {
            "kind": "equal_distance",
            "size_mm": 1.0,
            "flip_direction": False,
        },
        {
            "kind": "two_distances",
            "size_mm": 1.0,
            "second_size_mm": -1.0,
            "flip_direction": False,
        },
        {
            "kind": "distance_angle",
            "size_mm": 1.0,
            "angle_degrees": True,
            "flip_direction": False,
        },
        {
            "kind": "distance_angle",
            "size_mm": 1.0,
            "angle_degrees": 180.0,
            "flip_direction": False,
        },
    ),
)
def test_chamfer_parser_rejects_out_of_contract_definitions(definition) -> None:
    with pytest.raises(NativeModelError):
        prepare_design_chamfer("document-a", _chamfer_values(definition))


def test_draft_parser_preserves_exact_faces_references_and_automatic_modes() -> None:
    prepared = prepare_design_draft("document-a", _draft_values())

    assert prepared.targets[0].subelements == ("Face2",)
    assert prepared.angle_degrees == 5.0
    assert prepared.reversed is False
    assert prepared.neutral_plane.object.object_name == "ReferenceState"
    assert prepared.neutral_plane.subelement == "Face5"
    assert prepared.pull_direction.subelement == "Edge1"

    values = _draft_values()
    values["neutral_plane"] = {"kind": "automatic"}
    values["pull_direction"] = {"kind": "automatic"}
    values["reversed"] = True
    prepared = prepare_design_draft("document-a", values)
    assert prepared.neutral_plane.object is None
    assert prepared.pull_direction.object is None
    assert prepared.reversed is True


def test_draft_parser_rejects_edges_as_targets_and_invalid_reference_combinations() -> None:
    values = _draft_values()
    values["selection"]["targets"][0]["subelements"] = ["Edge1"]
    with pytest.raises(NativeModelError, match="exact FaceN"):
        prepare_design_draft("document-a", values)

    values = _draft_values()
    values["neutral_plane"] = {
        "kind": "subelement",
        "object_name": "ReferenceState",
        "subelement": "Edge2",
    }
    values["pull_direction"] = {"kind": "automatic"}
    with pytest.raises(NativeModelError, match="requires an explicit pull"):
        prepare_design_draft("document-a", values)

    values = _draft_values()
    values["angle_degrees"] = True
    with pytest.raises(NativeModelError, match="angle must be a number"):
        prepare_design_draft("document-a", values)

    values = _draft_values()
    values["neutral_plane"]["subelement"] = "Facegarbage"
    with pytest.raises(NativeModelError, match="exact EdgeN or FaceN"):
        prepare_design_draft("document-a", values)


def test_modify_is_internal_only_and_cannot_leak_into_selectable_result_schema() -> None:
    assert DesignResultSpec("modify", (), None).native_mode == "Modify"

    with pytest.raises(NativeModelError, match="mode is unavailable"):
        result_spec_from_mapping(
            "document-a",
            {
                "mode": "modify",
                "targets": [{"object_name": "BracketBody"}],
                "destination_component": None,
            },
        )


def test_new_body_result_accepts_the_natural_minimal_form() -> None:
    result = result_spec_from_mapping("document-a", {"mode": "new_body"})

    assert result.mode == "new_body"
    assert result.target_refs == ()
    assert result.destination_component_ref is None
