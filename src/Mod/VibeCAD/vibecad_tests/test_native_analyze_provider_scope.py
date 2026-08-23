# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider scope follows durable Analyze study state, not geometry guesses."""

from __future__ import annotations

from VibeCADNativeAnalyzeProviderScope import (
    analyze_provider_tool_names,
    scope_analyze_provider_surface,
)
from VibeCADNativeAnalyzeFluidSchema import analyze_fluid_capability_definition
from VibeCADNativeAnalyzeFaceSchema import analyze_face_capability_definition
from VibeCADNativeAnalyzeInspectSchema import analyze_inspect_capability_definition
from VibeCADNativeAnalyzeModelSchema import analyze_model_capability_definition
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
)
from VibeCADNativeProviderContext import provider_authorized_native_surface
from VibeCADNativeSurface import NativeSurfaceSnapshot


_SHARED = {
    "core.capture_view_screenshot",
    "document.query",
    "document.save",
    "document.undo",
    "object.properties",
    "selection.query",
    "view.control",
    "workspace.switch",
}

_ANALYZE = {
    "analyze.model",
    "analyze.faces",
    "analyze.inspect",
    "analyze.geometry",
    "analyze.electromagnetic",
    "analyze.fluid",
    "analyze.geometrical",
    "analyze.support",
    "analyze.connection",
    "analyze.load",
    "analyze.thermal",
    "analyze.mesh",
    "analyze.mesh_field",
    "analyze.mesh_output",
    "analyze.mesh_refinement",
    "analyze.structured_mesh",
    "analyze.solver",
    "analyze.solver_control",
    "analyze.solver_execution",
    "analyze.equation",
    "analyze.results",
    "analyze.presentation",
    "analyze.post",
    "analyze.post_function",
    "analyze.visualization",
}

_AVAILABLE = tuple(sorted(_SHARED | _ANALYZE))


def _domain(
    *physics: str,
    analysis_count: int = 1,
    mesh_count: int = 0,
    generated_mesh_count: int = 0,
    solver_count: int = 0,
    result_count: int = 0,
    geometry_source_count: int = 0,
) -> dict:
    workflows = []
    if analysis_count:
        workflows.append(
            {
                "study": (
                    {
                        "declared": True,
                        "physics": list(physics),
                        "regime": "steady",
                        "schema_version": 1,
                    }
                    if physics
                    else {"declared": False}
                ),
                "study_inventory": {
                    "mesh_definition_count": mesh_count,
                    "generated_mesh_count": generated_mesh_count,
                    "solver_count": solver_count,
                    "result_count": result_count,
                },
            }
        )
    return {
        "kind": "analyze",
        "analysis_count": analysis_count,
        "analysis_workflow_count": analysis_count,
        "analysis_workflows": workflows,
        "geometry_source_count": geometry_source_count,
        "provider_scope": {
            "analysis_count": analysis_count,
            "undeclared_analysis_count": analysis_count if not physics else 0,
            "physics": list(physics),
            "mesh_definition_count": mesh_count,
            "generated_mesh_count": generated_mesh_count,
            "solver_count": solver_count,
            "result_count": result_count,
        },
    }


def _names(domain: dict) -> set[str]:
    return set(analyze_provider_tool_names(domain, _AVAILABLE))


def test_new_analysis_surface_contains_only_setup_and_observation() -> None:
    names = _names(_domain(analysis_count=0))

    assert {"analyze.model", "analyze.inspect"} <= names
    assert not ({"analyze.fluid", "analyze.load", "analyze.mesh"} & names)
    assert "workspace.switch" not in names
    assert (_SHARED - {"workspace.switch"}) <= names


def test_face_reader_appears_only_when_exact_geometry_exists() -> None:
    assert "analyze.faces" not in _names(_domain(analysis_count=0))
    assert "analyze.faces" in _names(
        _domain(analysis_count=0, geometry_source_count=1)
    )


def test_declared_study_exposes_only_its_physics_and_core_lifecycle() -> None:
    fluid = _names(_domain("fluid"))
    mechanical = _names(_domain("mechanical"))

    assert {
        "analyze.fluid",
        "analyze.geometry",
        "analyze.mesh",
        "analyze.solver",
    } <= fluid
    assert not ({"analyze.load", "analyze.support", "analyze.thermal"} & fluid)
    assert {"analyze.load", "analyze.support", "analyze.connection"} <= mechanical
    assert not (
        {"analyze.fluid", "analyze.thermal", "analyze.electromagnetic"} & mechanical
    )


def test_advanced_mesh_solver_and_post_tools_follow_exact_artifacts() -> None:
    declared = _names(_domain("thermal"))
    meshed = _names(_domain("thermal", mesh_count=1, generated_mesh_count=1))
    solved = _names(
        _domain(
            "thermal",
            mesh_count=1,
            generated_mesh_count=1,
            solver_count=1,
            result_count=1,
        )
    )

    assert not ({"analyze.mesh_field", "analyze.mesh_output"} & declared)
    assert {
        "analyze.mesh_field",
        "analyze.mesh_refinement",
        "analyze.structured_mesh",
    } <= meshed
    assert "analyze.solver_control" not in meshed
    assert {
        "analyze.solver_control",
        "analyze.equation",
        "analyze.solver_execution",
    } <= solved
    assert {
        "analyze.results",
        "analyze.presentation",
        "analyze.post",
        "analyze.post_function",
        "analyze.visualization",
    } <= solved


def test_multiple_studies_compose_declared_physics_without_guessing() -> None:
    domain = _domain("mechanical")
    domain["analysis_count"] = 2
    domain["analysis_workflow_count"] = 2
    domain["analysis_workflows"].append(
        {
            "study": {
                "declared": True,
                "physics": ["thermal"],
                "regime": "transient",
                "schema_version": 1,
            },
            "study_inventory": {
                "mesh_definition_count": 0,
                "generated_mesh_count": 0,
                "solver_count": 0,
                "result_count": 0,
            },
        }
    )

    names = _names(domain)

    assert {"analyze.load", "analyze.support", "analyze.thermal"} <= names
    assert "analyze.fluid" not in names


def test_incomplete_snapshot_fails_closed_to_setup_tools() -> None:
    names = _names(
        {
            "kind": "analyze",
            "analysis_count": 1,
            "analysis_workflow_count": 1,
            "analysis_workflows": [],
        }
    )

    assert names == (_SHARED - {"workspace.switch"}) | {
        "analyze.model",
        "analyze.inspect",
    }


def test_provider_scope_covers_analyses_beyond_the_detailed_snapshot_page() -> None:
    domain = _domain("mechanical")
    domain["analysis_count"] = 1000
    domain["analysis_workflow_count"] = 1000
    domain["provider_scope"] = {
        "analysis_count": 1000,
        "undeclared_analysis_count": 0,
        "physics": ["fluid", "thermal"],
        "mesh_definition_count": 500,
        "generated_mesh_count": 400,
        "solver_count": 300,
        "result_count": 200,
    }

    names = _names(domain)

    assert {"analyze.fluid", "analyze.thermal", "analyze.post"} <= names
    assert not {"analyze.load", "analyze.support"} & names


def test_complete_manifest_is_projected_without_weakening_its_validation() -> None:
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="a" * 64,
        command_ids=("FEM_Analysis",),
        available_command_ids=("FEM_Analysis",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=_AVAILABLE,
        schemas=tuple(
            {
                "name": name,
                "description": name,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
            for name in _AVAILABLE
        ),
        human_only_action_ids=("FEM_Examples",),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    projected = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": _domain("fluid")},
    )

    assert projected.available is True
    assert projected.snapshot is snapshot
    assert projected.human_only_action_ids == surface.human_only_action_ids
    assert set(projected.tool_names) == _names(_domain("fluid"))
    assert tuple(schema["name"] for schema in projected.schemas) == projected.tool_names


def test_human_keeps_ribbon_control_on_every_native_surface() -> None:
    snapshot = NativeSurfaceSnapshot(
        surface_id="model",
        revision=7,
        manifest_sha256="b" * 64,
        command_ids=("PartDesign_DesignExtrude",),
        available_command_ids=("PartDesign_DesignExtrude",),
        unavailable_command_ids=(),
    )
    names = ("model.design", "workspace.switch", "document.query")
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=names,
        schemas=tuple(
            {
                "name": name,
                "description": name,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
            for name in names
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    authorized = provider_authorized_native_surface(surface)

    assert authorized.tool_names == ("model.design", "document.query")


def _schema_operations(schema: dict) -> set[str]:
    parameters = schema["parameters"]
    branches = parameters.get("oneOf", [parameters])
    result = set()
    for branch in branches:
        operation = branch["properties"]["operation"]
        result.update(operation.get("enum", [operation.get("const")]))
    return result


def test_operation_scope_publishes_only_calls_that_match_current_study_state() -> None:
    registry = NativeCapabilityRegistry()
    definitions = (
        analyze_model_capability_definition(),
        analyze_face_capability_definition(),
        analyze_inspect_capability_definition(),
        analyze_fluid_capability_definition(),
    )
    for definition in definitions:
        registry.register_definition(definition)
    snapshot = NativeSurfaceSnapshot(
        surface_id="analyze",
        revision=7,
        manifest_sha256="c" * 64,
        command_ids=("FEM_Analysis",),
        available_command_ids=("FEM_Analysis",),
        unavailable_command_ids=(),
    )
    surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=tuple(definition.name for definition in definitions),
        schemas=tuple(
            definition.provider_schema(
                tuple(variant.operation for variant in definition.variants)
            )
            for definition in definitions
        ),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )

    blank = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": _domain(analysis_count=0)},
        registry=registry,
    )
    fluid_domain = _domain("fluid")
    fluid_domain.update(
        {
            "geometry_source_count": 1,
            "materials": [],
            "fluid_constraints": [],
            "element_definitions": [],
        }
    )
    fluid = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )
    fluid_domain["fluid_constraint_count"] = 1
    fluid_domain["fluid_constraints"] = [
        {"constraint_kind": "fluid_boundary"}
    ]
    editable_fluid = scope_analyze_provider_surface(
        surface,
        {"surface_id": "analyze", "domain": fluid_domain},
        registry=registry,
    )

    blank_operations = {
        schema["name"]: _schema_operations(schema) for schema in blank.schemas
    }
    fluid_operations = {
        schema["name"]: _schema_operations(schema) for schema in fluid.schemas
    }
    editable_fluid_operations = {
        schema["name"]: _schema_operations(schema)
        for schema in editable_fluid.schemas
    }
    assert blank_operations == {
        "analyze.model": {"create_analysis"},
        "analyze.inspect": {"material_catalog"},
    }
    assert fluid_operations["analyze.model"] == {
        "create_analysis",
        "update_study",
        "create_fluid_material",
    }
    assert fluid_operations["analyze.faces"] == {"read"}
    assert {
        operation
        for operation in fluid_operations["analyze.fluid"]
        if operation.startswith("update_")
    } == set()
    assert {
        operation
        for operation in editable_fluid_operations["analyze.fluid"]
        if operation.startswith("update_")
    } == {"update_fluid_boundary"}
