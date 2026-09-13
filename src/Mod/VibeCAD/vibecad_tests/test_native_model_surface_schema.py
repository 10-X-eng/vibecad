# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelSurfaceSchema import model_surface_capability_definition


def _schema_parts():
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("filling",))
    branch = schema["parameters"]["oneOf"][0]
    return definition, schema, branch, branch["properties"]["definition"]


def test_surface_filling_contract_is_exact_closed_and_bounded() -> None:
    definition, schema, branch, filling = _schema_parts()

    assert definition.name == "model.surface"
    assert definition.primary_classification == "mutation"
    variant = definition.variants[0]
    assert variant.operation == "filling"
    assert variant.action_ids == frozenset({"Surface_Filling"})
    assert variant.surface_ids == frozenset({"model"})
    assert variant.exact_target_type == "OrderedExactCurrentFillingConstraints"
    assert branch["required"] == ["label", "definition"]
    assert branch["additionalProperties"] is False
    assert filling["required"] == ["constraints"]
    assert filling["additionalProperties"] is False

    constraints = filling["properties"]["constraints"]
    assert constraints["minItems"] == 1
    assert constraints["maxItems"] == 256
    assert constraints["uniqueItems"] is True
    item = constraints["items"]
    assert item["required"] == ["kind", "object_name", "subelement"]
    assert item["additionalProperties"] is False
    assert item["properties"]["kind"]["enum"] == [
        "boundary_edge",
        "curve_edge",
        "face",
        "point",
    ]
    assert item["properties"]["continuity"]["enum"] == ["C0", "G1", "G2"]
    assert filling["properties"]["degree"] == {
        "type": "integer",
        "minimum": 2,
        "maximum": 25,
    }
    assert filling["properties"]["maximum_segments"]["maximum"] == 10_000
    assert len(json.dumps(schema, separators=(",", ":"))) < 4_000


def test_surface_geometric_fill_contract_is_exact_closed_and_compact() -> None:
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("geom_fill_surface",))
    branch = schema["parameters"]["oneOf"][0]
    geometric_fill = branch["properties"]["definition"]
    variant = definition.variants[1]

    assert variant.operation == "geom_fill_surface"
    assert variant.action_ids == frozenset({"Surface_GeomFillSurface"})
    assert variant.exact_target_type == "OrderedExactCurrentBoundaryEdges"
    assert geometric_fill["required"] == ["boundaries"]
    assert geometric_fill["additionalProperties"] is False
    boundaries = geometric_fill["properties"]["boundaries"]
    assert (boundaries["minItems"], boundaries["maxItems"]) == (2, 4)
    assert boundaries["uniqueItems"] is True
    assert boundaries["items"]["required"] == ["object_name", "edge"]
    assert boundaries["items"]["additionalProperties"] is False
    assert geometric_fill["properties"]["style"]["enum"] == [
        "stretched",
        "coons",
        "curved",
    ]
    assert len(json.dumps(schema, separators=(",", ":"))) < 2_000


def test_surface_sections_contract_is_exact_closed_and_compact() -> None:
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("sections",))
    branch = schema["parameters"]["oneOf"][0]
    sections = branch["properties"]["definition"]["properties"]["sections"]
    variant = definition.variants[2]

    assert variant.operation == "sections"
    assert variant.action_ids == frozenset({"Surface_Sections"})
    assert variant.exact_target_type == "OrderedExactCurrentSectionEdges"
    assert branch["properties"]["definition"]["required"] == ["sections"]
    assert branch["properties"]["definition"]["additionalProperties"] is False
    assert (sections["minItems"], sections["maxItems"]) == (2, 256)
    assert sections["uniqueItems"] is True
    assert sections["items"]["required"] == ["object_name", "edge"]
    assert sections["items"]["additionalProperties"] is False
    assert len(json.dumps(schema, separators=(",", ":"))) < 1_500


def test_surface_extend_contract_is_exact_closed_and_bounded() -> None:
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("extend_face",))
    branch = schema["parameters"]["oneOf"][0]
    extend = branch["properties"]["definition"]
    variant = definition.variants[3]

    assert variant.operation == "extend_face"
    assert variant.action_ids == frozenset({"Surface_ExtendFace"})
    assert variant.exact_target_type == "ExactCurrentFace"
    assert extend["required"] == ["object_name", "face"]
    assert extend["additionalProperties"] is False
    assert extend["properties"]["u_negative"] == {
        "type": "number",
        "minimum": -0.5,
        "maximum": 10,
    }
    tolerance = extend["properties"]["tolerance"]
    assert tolerance["type"] == "number"
    assert (tolerance["minimum"], tolerance["maximum"]) == (0, 10)
    assert extend["properties"]["samples_u"] == {
        "type": "integer",
        "minimum": 2,
        "maximum": 512,
    }
    assert len(json.dumps(schema, separators=(",", ":"))) < 2_000


def test_surface_curve_on_mesh_contract_is_exact_closed_and_bounded() -> None:
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("curve_on_mesh",))
    branch = schema["parameters"]["oneOf"][0]
    curve = branch["properties"]["definition"]
    variant = definition.variants[4]

    assert variant.operation == "curve_on_mesh"
    assert variant.action_ids == frozenset({"Surface_CurveOnMesh"})
    assert variant.exact_target_type == "ExactCurrentMeshAndOrderedPickRays"
    assert variant.background_required is False
    assert curve["required"] == ["object_name", "anchors"]
    assert curve["additionalProperties"] is False
    anchors = curve["properties"]["anchors"]
    assert (anchors["minItems"], anchors["maxItems"]) == (2, 64)
    assert anchors["items"]["required"] == ["origin_mm", "direction"]
    assert anchors["items"]["additionalProperties"] is False
    assert curve["properties"]["maximum_degree"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 8,
    }
    assert curve["properties"]["continuity"]["enum"] == ["C0", "C1", "C2", "C3"]
    assert curve["properties"]["split_angle_degrees"] == {
        "type": "number",
        "minimum": 5,
        "maximum": 180,
    }
    assert len(json.dumps(schema, separators=(",", ":"))) < 1_800


def test_surface_blend_curve_contract_is_exact_closed_and_compact() -> None:
    definition = model_surface_capability_definition()
    schema = definition.provider_schema(("blend_curve",))
    branch = schema["parameters"]["oneOf"][0]
    blend = branch["properties"]["definition"]
    variant = definition.variants[5]

    assert variant.operation == "blend_curve"
    assert variant.action_ids == frozenset({"Surface_BlendCurve"})
    assert variant.exact_target_type == "TwoExactCurrentEdgePoints"
    assert variant.background_required is False
    assert blend["required"] == ["start", "end"]
    assert blend["additionalProperties"] is False
    for name in ("start", "end"):
        endpoint = blend["properties"][name]
        assert endpoint["required"] == ["object_name", "edge"]
        assert endpoint["additionalProperties"] is False
        assert endpoint["properties"]["parameter"] == {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }
        assert endpoint["properties"]["continuity"]["enum"] == [
            "C0",
            "G1",
            "G2",
            "G3",
            "G4",
        ]
        assert endpoint["properties"]["size"] == {
            "type": "number",
            "minimum": -100,
            "maximum": 100,
        }
    assert len(json.dumps(schema, separators=(",", ":"))) < 1_700
