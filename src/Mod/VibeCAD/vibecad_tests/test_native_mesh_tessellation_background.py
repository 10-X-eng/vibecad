# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path

import VibeCADNativeMeshConvertSchema as convert_schema_module
from VibeCADMeshTessellationJob import CACHE_SCHEMA
from VibeCADNativeMeshConvertSchema import mesh_convert_capability_definition


SOURCE_ROOT = Path(__file__).resolve().parents[3]


def test_unshipped_shape_tessellation_cache_contract_is_version_one() -> None:
    assert CACHE_SCHEMA == "vibecad-shape-tessellation-cache-v1"


def test_shape_tessellation_contract_requires_background_execution() -> None:
    definition = mesh_convert_capability_definition()
    variant = next(item for item in definition.variants if item.operation == "shape_to_mesh")

    assert variant.background_required


def test_mesh_conversion_uses_the_shared_exact_source_shape() -> None:
    definition = mesh_convert_capability_definition()

    for operation in ("mesh_to_shape", "mesh_to_solid", "curve_on_mesh"):
        variant = next(item for item in definition.variants if item.operation == operation)
        properties = variant.parameters["properties"]
        source = properties["source"]

        assert set(source["required"]) == {
            "object_name",
            "expected_state_sha256",
        }
        assert "expected_state_sha256" not in properties


def test_provider_mesh_to_shape_contract_names_shell_and_solid_directly() -> None:
    definition = convert_schema_module.mesh_to_shape_capability_definition()

    assert definition.name == "mesh.to_shape"
    assert [variant.operation for variant in definition.variants] == [
        "shell",
        "solid",
    ]
    for variant in definition.variants:
        assert variant.parameters["required"] == ["source"]


def test_provider_mesh_from_shape_contract_uses_human_meshing_terms() -> None:
    definition = convert_schema_module.mesh_from_shape_capability_definition()
    variant = definition.variants[0]

    assert definition.name == "mesh.from_shape"
    assert variant.operation == "tessellate"
    assert variant.parameters["required"] == ["source"]
    assert set(variant.parameters["properties"]) == {
        "source",
        "faces",
        "result_label",
        "surface_deviation_mm",
        "angular_deviation_degrees",
        "relative_surface_deviation",
        "preserve_face_colors",
    }
    assert variant.parameters["properties"]["angular_deviation_degrees"] == {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 180.0,
    }


def test_provider_curve_on_mesh_contract_requires_only_source_and_anchors() -> None:
    definition = convert_schema_module.mesh_curve_on_mesh_capability_definition()
    variant = definition.variants[0]

    assert definition.name == "mesh.curve_on_mesh"
    assert variant.operation == "create"
    assert variant.parameters["required"] == ["source", "anchors"]


def test_human_shape_tessellation_uses_shared_background_path() -> None:
    source = (
        SOURCE_ROOT / "Mod" / "MeshPart" / "Gui" / "Tessellation.cpp"
    ).read_text(encoding="utf-8")
    body = source.split("bool Tessellation::processAndCommit(", 1)[1].split(
        "void Tessellation::saveParameters", 1
    )[0]

    assert 'callMemberFunction("start_shape_tessellations"' in body
    assert "Gui::WaitCursor" not in body
    assert "gmsh->process" not in source
    assert "Mesh2ShapeGmsh::writeProject" not in source
    assert "getMeshingParameters" not in source
    assert "__shape__" not in source
    estimate = source.split(
        "void Tessellation::onEstimateMaximumEdgeLengthClicked()", 1
    )[1].split("bool Tessellation::accept()", 1)[0]
    assert "QtConcurrent::run" in estimate
    assert "getBoundBox()" not in estimate.split("QtConcurrent::run", 1)[0]


def test_cached_shape_tessellation_recompute_does_not_repeat_brep_work() -> None:
    header = (
        SOURCE_ROOT / "Mod" / "MeshPart" / "App" / "FeatureMeshPartOperations.h"
    ).read_text(encoding="utf-8")
    source = (
        SOURCE_ROOT / "Mod" / "MeshPart" / "App" / "FeatureMeshPartOperations.cpp"
    ).read_text(encoding="utf-8")

    mesh_from_shape = header.split("class MeshPartExport MeshFromShape", 1)[1].split(
        "class MeshPartExport ShapeFromMesh", 1
    )[0]
    assert "App::PropertyBool UpdateFromSource;" in mesh_from_shape
    execute = source.split("App::DocumentObjectExecReturn* MeshFromShape::execute()", 1)[1].split(
        "PROPERTY_SOURCE(MeshPart::ShapeFromMesh", 1
    )[0]
    assert "Mesher" not in execute
    assert "runGmsh" not in execute
    assert "getTopoShape" not in execute


def test_isolated_worker_calls_low_level_mesher_not_document_recompute() -> None:
    source = (
        SOURCE_ROOT / "Mod" / "VibeCAD" / "VibeCADMeshTessellationChild.py"
    ).read_text(encoding="utf-8")

    assert "MeshPart.meshFromShape" in source
    assert "subprocess.run" in source
    assert 'addObject("MeshPart::MeshFromShape"' not in source
    assert ".recompute(" not in source


def test_document_thread_capture_does_not_traverse_shape_topology_or_bounds() -> None:
    source = (
        SOURCE_ROOT / "Mod" / "VibeCAD" / "VibeCADNativeMeshConvert.py"
    ).read_text(encoding="utf-8")
    capture = source.split("def _shape_for_tessellation", 1)[1].split(
        "def capture_mesh_conversion", 1
    )[0]

    assert ".BoundBox" not in capture
    assert ".Faces" not in capture
    assert ".Edges" not in capture
    assert ".Vertexes" not in capture
    assert "isValid()" not in capture


def test_worker_resolves_selected_faces_before_tessellation() -> None:
    source = (
        SOURCE_ROOT / "Mod" / "VibeCAD" / "VibeCADMeshTessellationChild.py"
    ).read_text(encoding="utf-8")

    assert 'request.get("subelements")' in source
    assert "Part.makeCompound" in source
