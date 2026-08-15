# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for specialized Native Drawing dimensions."""

from __future__ import annotations

import json

from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_NAME,
    drawing_dimension_capability_definition,
)
from VibeCADNativeDrawingSpecialDimensionSchema import (
    DRAWING_SPECIAL_DIMENSION_OPERATIONS,
)


def _branches() -> dict[str, dict]:
    definition = drawing_dimension_capability_definition()
    schema = definition.provider_schema(DRAWING_SPECIAL_DIMENSION_OPERATIONS)
    return {
        branch["properties"]["operation"]["const"]: branch
        for branch in schema["parameters"]["oneOf"]
    }


def test_special_dimension_schema_has_closed_explicit_variants() -> None:
    definition = drawing_dimension_capability_definition()
    schema = definition.provider_schema(DRAWING_SPECIAL_DIMENSION_OPERATIONS)
    branches = _branches()

    assert definition.preserve_operation_branches is True
    assert tuple(branches) == DRAWING_SPECIAL_DIMENSION_OPERATIONS
    chamfer_required = {
        "operation",
        "label",
        "page",
        "view",
        "first_vertex",
        "second_vertex",
        "label_position_in_view_mm",
    }
    assert all(branch["additionalProperties"] is False for branch in branches.values())
    for operation in ("create_horizontal_chamfer", "create_vertical_chamfer"):
        branch = branches[operation]
        assert set(branch["required"]) == chamfer_required
        for field in ("first_vertex", "second_vertex"):
            vertex = branch["properties"][field]
            assert vertex["additionalProperties"] is False
            assert vertex["properties"]["subelement"]["pattern"] == (
                r"^Vertex(0|[1-9][0-9]*)$"
            )
            assert set(vertex["required"]) == {
                "subelement",
                "expected_element_state_sha256",
            }

    assert definition.name == DRAWING_DIMENSION_CAPABILITY_NAME
    arc_length = branches["create_arc_length_dimension"]
    assert set(arc_length["required"]) == {
        "operation",
        "label",
        "page",
        "view",
        "arc_edge",
        "label_position_in_view_mm",
    }
    arc_edge = arc_length["properties"]["arc_edge"]
    assert arc_edge["additionalProperties"] is False
    assert arc_edge["properties"]["subelement"]["pattern"] == (
        r"^Edge(0|[1-9][0-9]*)$"
    )
    assert set(arc_edge["required"]) == {
        "subelement",
        "expected_element_state_sha256",
    }

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 16 * 1024
