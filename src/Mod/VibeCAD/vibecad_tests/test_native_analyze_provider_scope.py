# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider scope follows durable Analyze study state, not geometry guesses."""

from __future__ import annotations

from VibeCADNativeAnalyzeProviderScope import (
    analyze_provider_tool_names,
    scope_analyze_provider_surface,
)
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
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
