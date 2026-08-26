# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for specialized Native Drawing dimensions."""

from __future__ import annotations

import json

from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeDrawingDimensionSchema import (
    DRAWING_DIMENSION_CAPABILITY_BY_OPERATION,
    drawing_dimension_capability_definitions,
)


def _branches() -> dict[str, dict]:
    return {
        definition.variants[0].operation: provider_visible_native_schema(
            definition.provider_schema((definition.variants[0].operation,))
        )["parameters"]["oneOf"][0]
        for definition in drawing_dimension_capability_definitions()
    }


def test_special_dimension_schema_has_closed_explicit_variants() -> None:
    branches = _branches()

    chamfer_required = {
        "label",
        "page",
        "view",
        "first_vertex",
        "second_vertex",
        "label_position_on_page_mm",
        "direction",
    }
    branch = branches["create_chamfer"]
    assert set(branch["required"]) == chamfer_required
    assert branch["properties"]["direction"]["enum"] == ["horizontal", "vertical"]
    for field in ("first_vertex", "second_vertex"):
        vertex = branch["properties"][field]
        assert vertex["additionalProperties"] is False
        assert vertex["properties"]["subelement"]["pattern"] == (
            r"^Vertex(0|[1-9][0-9]*)$"
        )
        assert set(vertex["required"]) == {"subelement"}

    assert DRAWING_DIMENSION_CAPABILITY_BY_OPERATION["create_chamfer"] == (
        "drawing.chamfer_dimension"
    )
    arc_length = branches["create_arc_length_dimension"]
    assert set(arc_length["required"]) == {
        "label",
        "page",
        "view",
        "arc_edge",
        "label_position_on_page_mm",
    }
    arc_edge = arc_length["properties"]["arc_edge"]
    assert arc_edge["additionalProperties"] is False
    assert arc_edge["properties"]["subelement"]["pattern"] == (
        r"^Edge(0|[1-9][0-9]*)$"
    )
    assert set(arc_edge["required"]) == {"subelement"}

    encoded = json.dumps(
        [branches["create_chamfer"], branches["create_arc_length_dimension"]],
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 16 * 1024
