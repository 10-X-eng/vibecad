# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeAnalyzeAssignments import (
    ASSIGNMENT_CATEGORIES,
    compact_assignment_state,
    page_assignment_records,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


def test_compact_assignment_preserves_identity_values_and_exact_targets() -> None:
    record = compact_assignment_state(
        "load",
        {
            "object_name": "ConstraintForce",
            "label": "Fan thrust",
            "type_id": "Fem::ConstraintForce",
            "load_kind": "force",
            "state_sha256": "a" * 64,
            "references": [
                {"object_name": "Blade", "subelements": ["Face12", "Face13"]}
            ],
            "definition": {"magnitude_n": 450.0, "direction": {"kind": "normal"}},
            "resolved_direction": {"x": 1.0, "y": 0.0, "z": 0.0},
        },
    )

    assert record == {
        "object_name": "ConstraintForce",
        "label": "Fan thrust",
        "type_id": "Fem::ConstraintForce",
        "category": "load",
        "kind": "force",
        "state_sha256": "a" * 64,
        "references": [{"object_name": "Blade", "subelements": ["Face12", "Face13"]}],
        "definition": {"magnitude_n": 450.0, "direction": {"kind": "normal"}},
    }


def test_connection_and_mesh_sources_are_normalized_as_references() -> None:
    connection = compact_assignment_state(
        "connection",
        {
            "object_name": "Contact",
            "label": "Bearing contact",
            "type_id": "Fem::ConstraintContact",
            "connection_kind": "contact",
            "state_sha256": "b" * 64,
            "slave": {"object_name": "Shaft", "subelement": "Face3"},
            "master": {"object_name": "Housing", "subelement": "Face7"},
            "definition": {"friction": {"kind": "frictionless"}},
        },
    )
    mesh = compact_assignment_state(
        "mesh",
        {
            "object_name": "FEMMeshGmsh",
            "label": "Volume mesh",
            "type_id": "Fem::FemMeshObjectPython",
            "mesher": "gmsh",
            "state_sha256": "c" * 64,
            "source": {"object_name": "FluidDomain"},
            "settings": {"element_order": 1},
            "generated": False,
            "topology": {"nodes": 0, "edges": 0, "faces": 0, "volumes": 0},
        },
    )

    assert connection["references"] == [
        {"object_name": "Shaft", "subelements": ["Face3"]},
        {"object_name": "Housing", "subelements": ["Face7"]},
    ]
    assert mesh["references"] == [{"object_name": "FluidDomain", "subelements": []}]
    assert mesh["definition"]["generated"] is False


def test_assignment_pages_are_bounded_and_category_exact() -> None:
    records = tuple(
        {
            "object_name": f"Load{index}",
            "category": "load" if index % 2 else "support",
        }
        for index in range(130)
    )
    page = page_assignment_records(records, category="load", offset=60, page_size=4)

    assert set(ASSIGNMENT_CATEGORIES) >= {"load", "support", "mesh_refinement"}
    assert [item["object_name"] for item in page["assignments"]] == [
        "Load121",
        "Load123",
        "Load125",
        "Load127",
    ]
    assert page["total"] == 65
    assert page["next_offset"] == 64

    with pytest.raises(NativeAnalyzeError, match="category"):
        page_assignment_records(records, category="solver", offset=0, page_size=10)
    with pytest.raises(NativeAnalyzeError, match="page_size"):
        page_assignment_records(records, category="all", offset=0, page_size=65)
