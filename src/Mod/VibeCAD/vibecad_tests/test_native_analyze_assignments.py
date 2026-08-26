# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAnalyzeAssignments as assignments
from VibeCADNativeAnalyzeAssignments import (
    ASSIGNMENT_CATEGORIES,
    assignment_validation_records,
    compact_assignment_state,
    page_assignment_records,
    validate_assignment_coverage,
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


def test_assignment_validation_records_do_not_serialize_generated_mesh_content() -> None:
    source = SimpleNamespace(Name="Body")
    mesh = SimpleNamespace(
        Name="Mesh",
        Label="Volume mesh",
        TypeId="Fem::FemMeshObjectPython",
        Proxy=SimpleNamespace(Type="Fem::FemMeshGmsh"),
        Shape=source,
        FemMesh=SimpleNamespace(
            NodeCount=140228,
            EdgeCount=0,
            FaceCount=0,
            VolumeCount=78832,
            dumpContent=lambda: pytest.fail(
                "assignment validation must not serialize generated mesh content"
            ),
        ),
        MeshRefinementList=(),
        MeshGroupList=(),
    )

    records = assignment_validation_records(SimpleNamespace(Group=(mesh,)))

    assert records == (
        {
            "object_name": "Mesh",
            "label": "Volume mesh",
            "type_id": "Fem::FemMeshObjectPython",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Body", "subelements": []}],
            "definition": {
                "generated": True,
                "topology": {
                    "nodes": 140228,
                    "edges": 0,
                    "faces": 0,
                    "volumes": 78832,
                },
            },
        },
    )


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


def test_assignment_coverage_rejects_references_outside_generated_mesh_domain() -> None:
    records = (
        {
            "object_name": "Mesh",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Body042", "subelements": []}],
            "definition": {"generated": True},
        },
        {
            "object_name": "PETG",
            "category": "material",
            "kind": "solid",
            "references": [
                {"object_name": "Body042", "subelements": ["Solid1"]}
            ],
        },
        {
            "object_name": "Contact",
            "category": "connection",
            "kind": "contact",
            "references": [
                {"object_name": "Body042", "subelements": ["Face1"]},
                {"object_name": "Body043", "subelements": ["Face2"]},
            ],
        },
    )

    result = validate_assignment_coverage(
        records,
        mesh_domains={"Body042": {"Body042"}},
        solid_units={"Body042": {("Body042", "Solid1")}},
    )

    assert result["valid"] is False
    assert result["issue_count"] == 1
    assert result["issues"][0]["object_name"] == "Contact"
    assert "Body043" in result["issues"][0]["message"]


def test_assignment_coverage_requires_material_for_every_meshed_solid() -> None:
    records = (
        {
            "object_name": "Mesh",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Domain", "subelements": []}],
            "definition": {"generated": True},
        },
        {
            "object_name": "PETG",
            "category": "material",
            "kind": "solid",
            "references": [
                {"object_name": "Body042", "subelements": ["Solid1"]}
            ],
        },
    )

    result = validate_assignment_coverage(
        records,
        mesh_domains={"Domain": {"Domain", "Body042", "Body043"}},
        solid_units={
            "Domain": {
                ("Body042", "Solid1"),
                ("Body043", "Solid1"),
            }
        },
    )

    assert result["valid"] is False
    assert result["issue_count"] == 1
    assert result["issues"][0]["object_name"] == "Body043.Solid1"
    assert "no solid material" in result["issues"][0]["message"]


def test_global_material_covers_every_generated_mesh_solid() -> None:
    records = (
        {
            "object_name": "Mesh",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Domain", "subelements": []}],
            "definition": {"generated": True},
        },
        {
            "object_name": "PETG",
            "category": "material",
            "kind": "solid",
            "references": [],
        },
    )

    result = validate_assignment_coverage(
        records,
        mesh_domains={"Domain": {"Domain", "Body042", "Body043"}},
        solid_units={
            "Domain": {
                ("Body042", "Solid1"),
                ("Body043", "Solid1"),
            }
        },
    )

    assert result == {"valid": True, "issue_count": 0, "issues": []}


def test_whole_object_material_covers_all_of_that_objects_solids() -> None:
    records = (
        {
            "object_name": "Mesh",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Domain", "subelements": []}],
            "definition": {"generated": True},
        },
        {
            "object_name": "PETG",
            "category": "material",
            "kind": "solid",
            "references": [{"object_name": "Body042", "subelements": []}],
        },
    )

    result = validate_assignment_coverage(
        records,
        mesh_domains={"Domain": {"Domain", "Body042"}},
        solid_units={
            "Domain": {
                ("Body042", "Solid1"),
                ("Body042", "Solid2"),
            }
        },
    )

    assert result == {"valid": True, "issue_count": 0, "issues": []}


def test_assignment_validation_bounds_details_without_capping_total_count(
    monkeypatch,
) -> None:
    source = SimpleNamespace(
        Name="Domain",
        Shape=SimpleNamespace(Solids=[object()]),
        VibeCADAnalysisDomain=False,
    )
    outside = {
        f"Outside{index}": SimpleNamespace(
            Name=f"Outside{index}",
            Shape=SimpleNamespace(),
        )
        for index in range(70)
    }
    live_assignments = {
        "Mesh": SimpleNamespace(isValid=lambda: True),
        "Steel": SimpleNamespace(isValid=lambda: True),
        **{
            f"Load{index}": SimpleNamespace(isValid=lambda: True)
            for index in range(70)
        },
    }
    document = SimpleNamespace(
        getObject=lambda name: (
            source
            if name == "Domain"
            else outside.get(name) or live_assignments.get(name)
        )
    )
    records = (
        {
            "object_name": "Mesh",
            "category": "mesh",
            "kind": "gmsh",
            "references": [{"object_name": "Domain", "subelements": []}],
            "definition": {"generated": True},
        },
        {
            "object_name": "Steel",
            "category": "material",
            "kind": "solid",
            "references": [],
        },
        *(
            {
                "object_name": f"Load{index}",
                "category": "load",
                "kind": "force",
                "references": [
                    {"object_name": f"Outside{index}", "subelements": []}
                ],
            }
            for index in range(70)
        ),
    )
    monkeypatch.setattr(
        assignments,
        "assignment_validation_records",
        lambda _analysis: records,
    )

    result = assignments.validate_assignments(SimpleNamespace(Document=document))

    assert result["valid"] is False
    assert result["issue_count"] == 70
    assert len(result["issues"]) == 64
    assert result["issues_truncated"] is True
