# SPDX-License-Identifier: LGPL-2.1-or-later

import inspect
from pathlib import Path

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeMeshExportRuntime import _FORMAT_SUFFIX
from VibeCADNativeMeshExportSchema import mesh_export_capability_definition
from VibeCADNativeMeshConvert import (
    commit_mesh_conversion,
    verify_committed_mesh_conversion,
)
from VibeCADNativeMeshImport import mesh_import_input_request
from VibeCADNativeMeshInspectSchema import mesh_inspect_capability_definition


def test_native_mesh_io_exposes_kernel_supported_3mf() -> None:
    request = mesh_import_input_request()
    export_parameters = mesh_export_capability_definition().provider_schema(
        ("export_mesh",)
    )["parameters"]["oneOf"][0]

    assert ".3mf" in request.allowed_suffixes
    assert "*.3mf" in request.name_filter
    assert "3mf" in export_parameters["properties"]["format"]["enum"]
    assert _FORMAT_SUFFIX["3mf"] == ".3mf"
    assert set(export_parameters["required"]) == {"target", "format"}
    assert set(export_parameters["properties"]["target"]["required"]) == {
        "object_name",
        "expected_state_sha256",
    }


def test_mesh_evaluation_uses_the_human_strict_default() -> None:
    parameters = mesh_inspect_capability_definition().provider_schema(
        ("evaluation",)
    )["parameters"]["oneOf"][0]

    assert "degeneration_mode" not in parameters["required"]
    operation, values = strict_variant_arguments(
        {"operation": "evaluation", "target": {"object_name": "Mesh"}},
        {"evaluation": frozenset({"target", "degeneration_mode"})},
        defaults={"evaluation": {"degeneration_mode": "strict"}},
    )

    assert operation == "evaluation"
    assert values["degeneration_mode"] == "strict"


def test_mesh_brep_integrity_checks_stay_off_the_document_thread() -> None:
    publication_source = inspect.getsource(commit_mesh_conversion)
    verification_source = inspect.getsource(verify_committed_mesh_conversion)

    assert "shape.isValid(" not in publication_source
    assert "shape.isValid(" not in verification_source

    source = (
        Path(__file__).resolve().parents[3]
        / "Mod"
        / "MeshPart"
        / "App"
        / "FeatureMeshPartOperations.cpp"
    ).read_text(encoding="utf-8")
    detached_branch = source.split("if (!UpdateFromSource.getValue()) {", 1)[1].split(
        "const auto* source = linkedMesh", 1
    )[0]
    assert ".isValid()" not in detached_branch
