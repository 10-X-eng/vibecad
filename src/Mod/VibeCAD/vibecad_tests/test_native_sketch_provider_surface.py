# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeSketchConstraintSchema import sketch_constraint_capability_definition
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition
from VibeCADNativeSketchProviderSchema import (
    SKETCH_PROVIDER_CAPABILITY_NAMES,
    sketch_provider_capability_definitions,
)
from VibeCADNativeSketchProviderRuntime import _compact_mutation_result
from VibeCADNativeDispatch import _schema_example


def _composition_paths(value, path="") -> list[str]:
    result = []
    if isinstance(value, dict):
        for name, item in value.items():
            child = f"{path}.{name}" if path else name
            if name in {"oneOf", "anyOf", "allOf"}:
                result.append(child)
            result.extend(_composition_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_composition_paths(item, f"{path}[{index}]"))
    return result


def test_compact_sketch_surface_covers_every_exact_internal_operation_once() -> None:
    definitions = sketch_provider_capability_definitions()
    assert {definition.name for definition in definitions} == (
        SKETCH_PROVIDER_CAPABILITY_NAMES
    )

    published = [
        variant.operation
        for definition in definitions
        for variant in definition.variants
        if definition.name
        not in {
            "sketch.batch",
            "sketch.inspect",
            "sketch.presentation",
            "sketch.control",
            "sketch.finish",
        }
    ]
    exact = {
        *(variant.operation for variant in sketch_geometry_capability_definition().variants),
        *(variant.operation for variant in sketch_constraint_capability_definition().variants),
        "trim",
        "split",
        "extend",
        "delete_geometry",
    }
    assert len(published) == len(set(published))
    assert set(published) == exact


def test_compact_sketch_provider_contract_has_no_nested_union_types_or_count_triplets() -> None:
    definitions = sketch_provider_capability_definitions()
    encoded_size = 0
    for definition in definitions:
        schema = definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        parameters = schema["parameters"]
        root_branches = (
            parameters["oneOf"]
            if set(parameters) == {"oneOf"}
            and isinstance(parameters["oneOf"], list)
            else [parameters]
        )
        for inspected in root_branches:
            assert _composition_paths(inspected) == []
            text = json.dumps(inspected, ensure_ascii=True, separators=(",", ":"))
            assert '"sketch"' not in text
            assert '"expected_geometry_count"' not in text
            assert '"expected_constraint_count"' not in text
            assert '"expected_external_geometry_count"' not in text
            encoded_size += len(text.encode("utf-8"))
    assert encoded_size <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_line_and_polyline_provider_branches_do_not_accept_each_others_fields() -> None:
    definition = next(
        item
        for item in sketch_provider_capability_definitions()
        if item.name == "sketch.draw_line"
    )
    parameters = definition.provider_schema(
        ("create_line", "create_polyline")
    )["parameters"]

    assert len(parameters["oneOf"]) == 2
    validator = Draft202012Validator(parameters)
    line = {
        "operation": "create_line",
        "revision": "sketch-v1:" + ("0" * 64),
        "start_mm": {"x": 0.0, "y": 0.0},
        "end_mm": {"x": 10.0, "y": 0.0},
    }
    polyline = {
        "operation": "create_polyline",
        "revision": "sketch-v1:" + ("0" * 64),
        "vertices_mm": [
            {"x": 0.0, "y": 0.0},
            {"x": 10.0, "y": 0.0},
            {"x": 10.0, "y": 10.0},
        ],
        "closed": True,
    }

    assert not list(validator.iter_errors(line))
    assert not list(validator.iter_errors(polyline))
    assert list(validator.iter_errors({**line, "closed": False}))
    assert list(
        validator.iter_errors(
            {**polyline, "end_mm": {"x": 10.0, "y": 10.0}}
        )
    )

    polyline_branch = next(
        branch
        for branch in parameters["oneOf"]
        if branch["properties"]["operation"].get("const") == "create_polyline"
    )
    repair_example = _schema_example(polyline_branch)
    assert repair_example["closed"] is True
    assert len(repair_example["vertices_mm"]) == 3
    assert not list(Draft202012Validator(polyline_branch).iter_errors(repair_example))


def test_read_state_bootstraps_revision_and_every_other_sketch_call_requires_it() -> None:
    definitions = sketch_provider_capability_definitions()
    for definition in definitions:
        for variant in definition.variants:
            properties = variant.parameters["properties"]
            required = set(variant.parameters["required"])
            if definition.name == "sketch.inspect" and variant.operation == "read_state":
                assert properties["revision"]["type"] == "string"
                assert "revision" not in required
            else:
                assert properties["revision"]["pattern"].startswith("^sketch-v1:")
                assert "revision" in required


def test_target_constraints_have_one_focused_exact_contract_each() -> None:
    definitions = {
        definition.name: definition
        for definition in sketch_provider_capability_definitions()
    }
    expected = {
        "sketch.coincident": "constrain_coincident",
        "sketch.perpendicular": "constrain_perpendicular",
        "sketch.tangent": "constrain_tangent",
        "sketch.symmetric": "constrain_symmetric",
    }

    assert not {
        operation
        for operation in expected.values()
    } & {variant.operation for variant in definitions["sketch.constrain"].variants}
    for name, operation in expected.items():
        definition = definitions[name]
        assert tuple(variant.operation for variant in definition.variants) == (operation,)
        parameters = definition.provider_schema((operation,))["parameters"]["oneOf"][0]
        assert parameters["required"] == ["revision", "target"]
        target = parameters["properties"]["target"]
        assert target["type"] == "object"
        assert target["required"] == ["form"]
        assert target["description"].startswith("Fields by form:")


def test_mutation_result_keeps_next_action_state_without_internal_receipt_noise() -> None:
    compact = _compact_mutation_result(
        {
            "assistant_undo_available": True,
            "receipt": {"capability": "sketch.draw_line", "changed": []},
            "sketch": {"object_name": "Sketch"},
            "geometry": {
                "index": 3,
                "geometry_id": 9,
                "kind": "line",
                "construction": False,
                "start_mm": [0.0, 0.0, 0.0],
                "end_mm": [10.0, 0.0, 0.0],
            },
            "geometry_count": 4,
            "constraint_count": 2,
            "profile": {
                "closed_profile": True,
                "face_maker_succeeded": True,
                "closed_wire_count": 1,
                "open_wire_count": 0,
                "support_plane": {"space": "global"},
            },
            "solver": {
                "degrees_of_freedom": 7,
                "fully_constrained": False,
                "conflicting_constraints": [],
                "open_vertices_mm": [],
            },
        }
    )

    assert compact == {
        "assistant_undo_available": True,
        "geometry_count": 4,
        "constraint_count": 2,
        "geometry_ref": {
            "geometry_index": 3,
            "geometry_id": 9,
            "kind": "line",
            "construction": False,
        },
        "profile": {
            "closed": True,
            "face_buildable": True,
            "closed_wire_count": 1,
            "open_wire_count": 0,
        },
        "solver": {
            "degrees_of_freedom": 7,
            "fully_constrained": False,
        },
    }


def test_draw_result_hides_generated_constraints_helpers_and_echoed_dimensions() -> None:
    compact = _compact_mutation_result(
        {
            "assistant_undo_available": True,
            "geometry_refs": [
                {
                    "geometry_index": 0,
                    "geometry_id": 40,
                    "kind": "line",
                    "construction": False,
                }
            ],
            "construction_geometry_refs": [
                {
                    "geometry_index": 1,
                    "geometry_id": 41,
                    "kind": "point",
                    "construction": True,
                }
            ],
            "constraint_refs": [
                {"constraint_index": 0, "type": "Coincident"}
            ],
            "corners_mm": [[-70.0, -48.0, 0.0], [70.0, 48.0, 0.0]],
            "corner_radius_mm": 18.0,
            "geometry_count": 2,
            "constraint_count": 1,
            "profile": {
                "closed_profile": True,
                "face_maker_succeeded": True,
                "closed_wire_count": 1,
                "open_wire_count": 0,
            },
            "solver": {
                "degrees_of_freedom": 0,
                "fully_constrained": True,
            },
        },
        operation="create_rounded_rectangle",
    )

    assert compact == {
        "assistant_undo_available": True,
        "geometry_refs": [
            {
                "geometry_index": 0,
                "geometry_id": 40,
                "kind": "line",
                "construction": False,
            }
        ],
        "geometry_count": 2,
        "constraint_count": 1,
        "profile": {
            "closed": True,
            "face_buildable": True,
            "closed_wire_count": 1,
            "open_wire_count": 0,
        },
        "solver": {
            "degrees_of_freedom": 0,
            "fully_constrained": True,
        },
    }
