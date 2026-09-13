# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelPartSchema import model_part_capability_definition


EXPECTED_KINDS = (
    "plane",
    "helix",
    "spiral",
    "circle",
    "ellipse",
    "point",
    "line",
    "regular_polygon",
)


def _schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("primitive",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _builder_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("builder",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _make_face_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("make_face",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _ruled_surface_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("ruled_surface",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _cross_sections_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("cross_sections",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _offset_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("offset_3d",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _offset_2d_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("offset_2d",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _projection_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("project_surface",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _compound_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("compound",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _compound_filter_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("compound_filter",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def _defeature_schema_parts():
    definition = model_part_capability_definition()
    schema = definition.provider_schema(("defeature",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def test_part_primitive_contract_matches_every_live_creation_choice() -> None:
    definition, _schema, branch, primitive = _schema_parts()

    assert definition.name == "model.part"
    assert definition.description == (
        "Create standalone curves, surfaces, compounds, and repairs."
    )
    assert branch["properties"]["operation"]["const"] == "primitive"
    assert definition.variants[0].description == (
        "Create a plane, curve, point, or regular polygon."
    )
    assert tuple(primitive["properties"]["kind"]["enum"]) == EXPECTED_KINDS
    variant = definition.variants[0]
    assert variant.action_ids == frozenset({"Part_Primitives"})
    assert variant.surface_ids == frozenset({"model"})
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_part_primitive_requires_explicit_label_placement_and_closed_definition() -> None:
    _definition, _schema, branch, primitive = _schema_parts()

    assert branch["required"] == ["label", "placement", "definition"]
    assert branch["additionalProperties"] is False
    assert primitive["required"] == ["kind"]
    assert primitive["additionalProperties"] is False
    assert primitive["properties"]["handedness"]["enum"] == [
        "right",
        "left",
    ]
    assert primitive["properties"]["sides"] == {
        "type": "integer",
        "minimum": 3,
        "maximum": 1_000,
    }


def test_part_primitive_schema_is_compact_and_has_no_authority_escape_hatch() -> None:
    _definition, schema, _branch, _primitive = _schema_parts()
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    serialized = encoded.decode("utf-8")

    assert len(encoded) < 3_100
    for forbidden in ("selection", "runCommand", "workbench", "ribbon"):
        assert forbidden not in serialized


def test_part_builder_contract_matches_every_live_shape_builder_choice() -> None:
    definition, _schema, branch, builder = _builder_schema_parts()

    assert branch["properties"]["operation"]["const"] == "builder"
    assert builder["properties"]["kind"]["enum"] == [
        "edge_from_vertices",
        "wire_from_edges",
        "face_from_vertices",
        "face_from_edges",
        "shell_from_faces",
        "solid_from_shell",
    ]
    variant = definition.variants[1]
    assert variant.action_ids == frozenset({"Part_Builder"})
    assert variant.exact_target_type == "ExactShapeInputs"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_part_builder_schema_is_closed_bounded_and_exposes_every_real_control() -> None:
    _definition, schema, branch, builder = _builder_schema_parts()

    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert builder["required"] == ["kind"]
    assert builder["additionalProperties"] is False
    assert set(builder["properties"]) == {
        "kind",
        "inputs",
        "source",
        "planar",
        "refine",
        "all_faces",
    }
    inputs = builder["properties"]["inputs"]
    assert (inputs["minItems"], inputs["maxItems"], inputs["uniqueItems"]) == (
        1,
        32,
        True,
    )
    subelements = inputs["items"]["properties"]["subelements"]
    assert (subelements["minItems"], subelements["maxItems"]) == (1, 64)
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_500


def test_make_face_contract_matches_the_live_immediate_command() -> None:
    definition, _schema, branch, make_face = _make_face_schema_parts()

    assert branch["properties"]["operation"]["const"] == "make_face"
    variant = definition.variants[2]
    assert variant.action_ids == frozenset({"Part_MakeFace"})
    assert variant.exact_target_type == "ExactCurrentClosedWireSources"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert make_face["required"] == ["sources"]
    assert make_face["additionalProperties"] is False


def test_make_face_targets_are_whole_object_bounded_and_compact() -> None:
    _definition, schema, _branch, make_face = _make_face_schema_parts()

    sources = make_face["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        32,
        True,
    )
    item = sources["items"]
    assert item["required"] == ["object_name"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {"object_name"}
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_200


def test_ruled_surface_contract_matches_the_live_immediate_command() -> None:
    definition, _schema, branch, ruled = _ruled_surface_schema_parts()

    assert branch["properties"]["operation"]["const"] == "ruled_surface"
    variant = definition.variants[3]
    assert variant.action_ids == frozenset({"Part_RuledSurface"})
    assert variant.exact_target_type == "TwoExactCurrentEdgesOrWires"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert ruled["required"] == ["curves"]
    assert ruled["additionalProperties"] is False


def test_ruled_surface_requires_two_exact_whole_or_subelement_curves() -> None:
    _definition, schema, _branch, ruled = _ruled_surface_schema_parts()

    curves = ruled["properties"]["curves"]
    assert (curves["minItems"], curves["maxItems"], curves["uniqueItems"]) == (
        2,
        2,
        True,
    )
    item = curves["items"]
    assert item["required"] == ["object_name"]
    assert item["additionalProperties"] is False
    assert item["properties"]["subelement"]["pattern"] == (
        r"^(?:Edge|Wire)[1-9][0-9]*$"
    )
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_300


def test_part_cross_sections_contract_matches_every_live_task_control() -> None:
    definition, _schema, branch, cross_sections = _cross_sections_schema_parts()

    assert branch["properties"]["operation"]["const"] == "cross_sections"
    variant = definition.variants[4]
    assert variant.action_ids == frozenset({"Part_CrossSections"})
    assert variant.exact_target_type == "ExactCurrentShapesAndPlaneSeries"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert cross_sections["required"] == ["sources", "plane", "distribution"]
    assert cross_sections["additionalProperties"] is False
    assert set(cross_sections["properties"]) == {
        "sources",
        "plane",
        "distribution",
    }
    distribution = cross_sections["properties"]["distribution"]
    assert set(distribution["properties"]) == {
        "kind",
        "position_mm",
        "count",
        "distance_mm",
        "both_sides",
    }
    assert distribution["properties"]["kind"]["enum"] == ["single", "series"]


def test_part_cross_sections_sources_and_series_are_bounded_and_compact() -> None:
    _definition, schema, _branch, cross_sections = _cross_sections_schema_parts()

    sources = cross_sections["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        32,
        True,
    )
    source = sources["items"]
    assert source["required"] == ["object_name"]
    assert source["additionalProperties"] is False
    subelements = source["properties"]["subelements"]
    assert (
        subelements["minItems"],
        subelements["maxItems"],
        subelements["uniqueItems"],
    ) == (1, 64, True)
    assert subelements["items"]["pattern"].startswith("^(?:Vertex|Edge|Wire|Face")
    distribution = cross_sections["properties"]["distribution"]["properties"]
    assert distribution["count"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10_000,
    }
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 2_100


def test_part_3d_offset_contract_matches_every_live_result_control() -> None:
    definition, _schema, branch, offset = _offset_schema_parts()

    assert branch["properties"]["operation"]["const"] == "offset_3d"
    variant = definition.variants[5]
    assert variant.action_ids == frozenset({"Part_Offset"})
    assert variant.exact_target_type == "ExactCurrentWholeShape"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert offset["required"] == [
        "source",
        "value_mm",
        "mode",
        "join",
        "intersection",
        "self_intersection",
        "fill",
    ]
    assert offset["additionalProperties"] is False
    assert offset["properties"]["mode"]["enum"] == [
        "skin",
        "pipe",
        "recto_verso",
    ]
    assert offset["properties"]["join"]["enum"] == [
        "arc",
        "tangent",
        "intersection",
    ]


def test_part_3d_offset_source_is_whole_exact_bounded_and_compact() -> None:
    _definition, schema, _branch, offset = _offset_schema_parts()

    source = offset["properties"]["source"]
    assert source["required"] == ["object_name"]
    assert source["additionalProperties"] is False
    assert set(source["properties"]) == {"object_name"}
    assert offset["properties"]["value_mm"] == {
        "type": "number",
        "minimum": -1_000_000.0,
        "maximum": 1_000_000.0,
    }
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_400


def test_part_2d_offset_contract_matches_its_narrower_live_task_controls() -> None:
    definition, schema, branch, offset = _offset_2d_schema_parts()

    assert branch["properties"]["operation"]["const"] == "offset_2d"
    variant = definition.variants[6]
    assert variant.action_ids == frozenset({"Part_Offset2D"})
    assert variant.exact_target_type == "ExactCurrentWholePlanarShape"
    assert variant.transaction_behavior == "document"
    assert offset["required"] == [
        "source",
        "value_mm",
        "mode",
        "join",
        "intersection",
        "fill",
    ]
    assert offset["additionalProperties"] is False
    assert offset["properties"]["mode"]["enum"] == ["skin", "pipe"]
    assert "self_intersection" not in offset["properties"]
    assert offset["properties"]["join"]["enum"] == [
        "arc",
        "tangent",
        "intersection",
    ]
    assert len(
        json.dumps(
            schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) < 1_300


def test_part_projection_contract_matches_the_live_retained_task() -> None:
    definition, schema, branch, projection = _projection_schema_parts()

    assert branch["properties"]["operation"]["const"] == "project_surface"
    variant = definition.variants[7]
    assert variant.action_ids == frozenset({"Part_ProjectionOnSurface"})
    assert variant.exact_target_type == "ExactCurrentProjectionGeometry"
    assert variant.transaction_behavior == "document"
    assert projection["required"] == [
        "target",
        "sources",
        "mode",
        "height_mm",
        "offset_mm",
        "direction_xyz",
    ]
    assert projection["additionalProperties"] is False
    assert projection["properties"]["mode"]["enum"] == ["all", "faces", "edges"]
    assert projection["properties"]["height_mm"] == {
        "type": "number",
        "minimum": 0.0,
        "maximum": 999.0,
    }
    assert projection["properties"]["offset_mm"] == {
        "type": "number",
        "minimum": -999.0,
        "maximum": 999.0,
    }
    assert len(
        json.dumps(
            schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) < 1_700


def test_part_projection_targets_and_direction_are_exact_closed_and_bounded() -> None:
    _definition, _schema, _branch, projection = _projection_schema_parts()

    target = projection["properties"]["target"]
    assert target["required"] == ["object_name", "subelement"]
    assert target["additionalProperties"] is False
    assert target["properties"]["subelement"]["pattern"] == r"^Face[1-9][0-9]*$"
    sources = projection["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        64,
        True,
    )
    assert sources["items"]["properties"]["subelement"]["pattern"] == (
        r"^(?:Edge|Wire|Face)[1-9][0-9]*$"
    )
    direction = projection["properties"]["direction_xyz"]
    assert (direction["minItems"], direction["maxItems"]) == (3, 3)
    assert direction["items"] == {
        "type": "number",
        "minimum": -1.0,
        "maximum": 1.0,
    }


def test_part_compound_contract_is_ordered_exact_closed_and_compact() -> None:
    definition, schema, branch, compound = _compound_schema_parts()

    assert branch["properties"]["operation"]["const"] == "compound"
    variant = definition.variants[8]
    assert variant.action_ids == frozenset({"Part_Compound"})
    assert variant.exact_target_type == "ExactCurrentWholeShapes"
    assert variant.transaction_behavior == "document"
    assert compound["required"] == ["sources"]
    assert compound["additionalProperties"] is False
    sources = compound["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        64,
        True,
    )
    assert sources["items"]["required"] == ["object_name"]
    assert sources["items"]["additionalProperties"] is False
    assert len(
        json.dumps(
            schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) < 800


def test_part_compound_filter_contract_covers_every_durable_native_mode() -> None:
    definition, schema, branch, filter_definition = _compound_filter_schema_parts()

    assert branch["properties"]["operation"]["const"] == "compound_filter"
    variant = definition.variants[9]
    assert variant.action_ids == frozenset({"Part_CompoundFilter"})
    assert variant.exact_target_type == "ExactCurrentCompoundAndStencil?"
    assert variant.transaction_behavior == "document"
    assert filter_definition["required"] == ["source", "mode"]
    assert filter_definition["additionalProperties"] is False
    assert filter_definition["properties"]["mode"]["enum"] == [
        "bypass",
        "specific_items",
        "collision",
        "volume",
        "area",
        "length",
        "distance",
    ]
    assert len(
        json.dumps(
            schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) < 1_900


def test_part_compound_filter_selectors_are_typed_closed_and_bounded() -> None:
    _definition, _schema, _branch, filter_definition = (
        _compound_filter_schema_parts()
    )
    selectors = filter_definition["properties"]["selectors"]
    index, slice_schema = selectors["items"]["oneOf"]

    assert (selectors["minItems"], selectors["maxItems"]) == (1, 256)
    assert index == {
        "type": "integer",
        "minimum": -1_000_000,
        "maximum": 1_000_000,
    }
    assert (slice_schema["minItems"], slice_schema["maxItems"]) == (2, 3)
    assert slice_schema["items"]["oneOf"][1] == {"type": "null"}
    window = filter_definition["properties"]["window_percent"]
    assert (window["minItems"], window["maxItems"]) == (2, 2)
    assert filter_definition["properties"]["stencil"]["oneOf"][1] == {
        "type": "null"
    }


def test_part_defeature_contract_is_exact_closed_bounded_and_compact() -> None:
    definition, schema, branch, defeature = _defeature_schema_parts()

    assert branch["properties"]["operation"]["const"] == "defeature"
    variant = definition.variants[10]
    assert variant.action_ids == frozenset({"Part_Defeaturing"})
    assert variant.exact_target_type == "ExactCurrentShapesAndFaces"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    assert branch["required"] == ["label", "definition"]
    assert defeature["required"] == ["sources"]
    assert defeature["additionalProperties"] is False
    sources = defeature["properties"]["sources"]
    assert (sources["minItems"], sources["maxItems"], sources["uniqueItems"]) == (
        1,
        32,
        True,
    )
    source = sources["items"]
    assert source["required"] == ["object_name", "faces"]
    assert source["additionalProperties"] is False
    faces = source["properties"]["faces"]
    assert (faces["minItems"], faces["maxItems"], faces["uniqueItems"]) == (
        1,
        64,
        True,
    )
    assert faces["items"]["pattern"] == r"^Face[1-9][0-9]*$"
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded) < 1_000
    for forbidden in ("selection", "runCommand", "workbench", "ribbon"):
        assert forbidden not in encoded.decode("utf-8")
