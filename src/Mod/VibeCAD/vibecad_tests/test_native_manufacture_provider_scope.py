# SPDX-License-Identifier: LGPL-2.1-or-later

"""Manufacture provider scope follows exact per-setup document state."""

from __future__ import annotations

from types import SimpleNamespace

import VibeCADNativeManufactureSnapshot as manufacture_snapshot
import VibeCADNativeManufactureInspect as manufacture_inspect
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistry,
    NativeProviderSurface,
)
from VibeCADNativeManufactureFocusedInspectSchema import (
    MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES,
    manufacture_focused_inspect_capability_definitions,
)
from VibeCADNativeManufactureJobSchema import manufacture_job_capability_definition
from VibeCADNativeManufactureModifySchema import (
    manufacture_modify_capability_definition,
)
from VibeCADNativeManufactureOperationSchema import (
    manufacture_operation_capability_definition,
)
from VibeCADNativeManufactureFocusedOperationSchema import (
    MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES,
    manufacture_focused_operation_capability_definitions,
)
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
    manufacture_focused_modify_capability_definitions,
)
from VibeCADNativeManufactureProgramSchema import (
    manufacture_program_capability_definition,
)
from VibeCADNativeManufactureToolSchema import (
    manufacture_tool_catalog_capability_definition,
    manufacture_tool_capability_definition,
)
from VibeCADNativeManufactureFocusedToolSchema import (
    manufacture_focused_tool_capability_definitions,
)
from VibeCADNativeManufactureProviderScope import (
    manufacture_provider_tool_names,
    scope_manufacture_provider_surface,
)
from VibeCADNativeProviderContext import provider_authorized_native_surface
from VibeCADNativeProviderContext import provider_visible_native_state
from VibeCADNativeSurface import NativeSurfaceSnapshot


_AVAILABLE = (
    "state.read",
    "view.control",
    "inspect.query",
    "inspect.compare",
    "document.save",
    "document.undo",
    "native.job",
    "workspace.switch",
    "manufacture.job",
    "manufacture.inspect",
    *MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.values(),
    "manufacture.tool_catalog",
    "manufacture.tool",
    "manufacture.add_tool",
    "manufacture.set_controller",
    "manufacture.update_tool",
    "manufacture.operation",
    *MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES.values(),
    "manufacture.program",
    "manufacture.modify",
    *set(MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES.values()),
    "manufacture.probe",
    "manufacture.template",
    "manufacture.simulation",
    "manufacture.close_simulation",
    "manufacture.simulation_result",
    "manufacture.follow_up_setup",
    "manufacture.camotics",
    "manufacture.post",
    "manufacture.post_job",
    "manufacture.post_selected",
    "manufacture.property_bag",
    "manufacture.area",
    "manufacture.tool_output",
    "robot.trajectory",
    "robot.motion",
    "robot.export",
)


def test_visible_partdesign_body_shadows_its_private_compatibility_link() -> None:
    identity = {
        "VibeCADScriptedEngine": "vibescript:partdesign",
        "VibeCADScriptedModelId": "0123456789abcdef0123456789abcdef",
        "VibeCADScriptedOutputKey": "FixtureBlock",
    }
    body = SimpleNamespace(
        TypeId="PartDesign::Body",
        VibeCADScriptedRole="implementation",
        **identity,
    )
    publication = SimpleNamespace(
        TypeId="App::Link",
        VibeCADScriptedRole="publication",
        **identity,
    )
    independent = SimpleNamespace(
        TypeId="App::Link",
        VibeCADScriptedRole="publication",
        VibeCADScriptedEngine="vibescript:partdesign",
        VibeCADScriptedModelId="fedcba9876543210fedcba9876543210",
        VibeCADScriptedOutputKey="OtherPart",
    )

    shadowed = manufacture_snapshot._shadowed_partdesign_publication_ids(
        (body, publication, independent)
    )

    assert shadowed == {id(publication)}

_SHARED = {
    "state.read",
    "view.control",
    "inspect.query",
    "inspect.compare",
    "document.save",
    "document.undo",
}


def _job(
    name: str,
    *,
    tools: int = 0,
    operations: int = 0,
    simulation_ready: bool = False,
    post_ready: bool = False,
) -> dict:
    return {
        "object_name": name,
        "label": name,
        "state_sha256": (name[-1].lower() * 64),
        "counts": {
            "models": 1,
            "tools": tools,
            "operations": operations,
            "active_operations": operations,
        },
        "readiness": {
            "simulation": {"ready": simulation_ready},
            "post": {"ready": post_ready},
        },
    }


def _domain(*jobs: dict, active: dict | None = None) -> dict:
    return {
        "kind": "manufacture",
        "job_count": len(jobs),
        "jobs": list(jobs),
        "jobs_truncated": False,
        "active_job": active,
        "active_job_resolution": "selection" if active else "choose_job",
        "model_candidate_count": 1,
    }


def _surface() -> NativeProviderSurface:
    snapshot = NativeSurfaceSnapshot(
        surface_id="manufacture",
        revision=4,
        manifest_sha256="a" * 64,
        command_ids=("CAM_Job",),
        available_command_ids=("CAM_Job",),
        unavailable_command_ids=(),
    )
    return NativeProviderSurface(
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
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )


def _definition_surface(definitions: tuple) -> tuple[NativeCapabilityRegistry, NativeProviderSurface]:
    registry = NativeCapabilityRegistry()
    for definition in definitions:
        registry.register_definition(definition)
    base = _surface()
    return registry, NativeProviderSurface(
        snapshot=base.snapshot,
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


def test_blank_document_exposes_setup_creation_and_bounded_discovery_only() -> None:
    names = set(manufacture_provider_tool_names(_domain(), _AVAILABLE))

    assert names == _SHARED | {
        "manufacture.job",
        "manufacture.setups",
        "manufacture.tool_catalog",
    }


def test_retained_stock_exposes_only_the_explicit_follow_up_setup_tool() -> None:
    domain = _domain()
    domain["remaining_stock_result_count"] = 1

    names = set(manufacture_provider_tool_names(domain, _AVAILABLE))

    assert names == _SHARED | {
        "manufacture.job",
        "manufacture.setups",
        "manufacture.tool_catalog",
        "manufacture.follow_up_setup",
        "manufacture.remaining_stock",
    }


def test_selected_setup_exposes_only_lifecycle_supported_by_its_state() -> None:
    setup = _job("SetupA", tools=1, operations=0)
    names = set(
        manufacture_provider_tool_names(
            _domain(setup, active=setup),
            _AVAILABLE,
        )
    )

    assert {
        "manufacture.tool",
        "manufacture.face",
        "manufacture.pocket",
        "manufacture.profile",
        "manufacture.drill",
        "manufacture.program",
        "manufacture.probe",
        "manufacture.template",
    } <= names
    assert {
        "manufacture.add_tool",
        "manufacture.set_controller",
        "manufacture.update_tool",
    } <= names
    assert "manufacture.modify" not in names
    assert "manufacture.simulation" not in names
    assert "manufacture.close_simulation" not in names
    assert "manufacture.simulation_result" not in names
    assert "manufacture.camotics" not in names
    assert "manufacture.post" not in names
    assert "manufacture.post_job" not in names
    assert "manufacture.post_selected" not in names
    assert "native.job" not in names
    assert not any(name.startswith("robot.") for name in names)


def test_valid_paths_add_correction_simulation_and_post_without_hiding_setup_tools() -> None:
    setup = _job(
        "SetupB",
        tools=2,
        operations=4,
        simulation_ready=True,
        post_ready=True,
    )
    names = set(
        manufacture_provider_tool_names(
            _domain(setup, active=setup),
            _AVAILABLE,
        )
    )

    assert {
        "manufacture.tool",
        "manufacture.program",
        "manufacture.operations",
        "manufacture.dressup",
        "manufacture.simulation",
        "manufacture.simulation_result",
        "manufacture.camotics",
        "manufacture.post_job",
        "manufacture.post_selected",
    } <= names
    assert "manufacture.close_simulation" not in names
    assert "manufacture.post" not in names


def test_active_native_simulation_exposes_exact_close_and_safe_reads_only() -> None:
    setup = _job(
        "SetupSimulation",
        tools=2,
        operations=4,
        simulation_ready=True,
        post_ready=True,
    )
    domain = _domain(setup, active=setup)
    domain["active_simulation"] = {
        "mode": "gl",
        "simulation_id": "1" * 32,
        "job": setup["object_name"],
    }

    names = set(manufacture_provider_tool_names(domain, _AVAILABLE))

    assert names == {
        "state.read",
        "inspect.query",
        "inspect.compare",
        "manufacture.setups",
        "manufacture.read_setup",
        "manufacture.setup_options",
        "manufacture.validate",
        "manufacture.tool_catalog",
        "manufacture.close_simulation",
    }


def test_provider_state_retains_exact_active_simulation_identity() -> None:
    state = {
        "surface_id": "manufacture",
        "document": {"document_uid": "document-a"},
        "domain": {
            **_domain(),
            "active_simulation": {
                "mode": "gl",
                "simulation_id": "2" * 32,
                "job": "SetupA",
            },
        },
    }

    compact = provider_visible_native_state(state)

    assert compact["domain"]["active_simulation"] == {
        "mode": "gl",
        "simulation_id": "2" * 32,
        "job": "SetupA",
    }


def test_unselected_independent_setups_keep_explicit_target_lifecycle_available() -> None:
    roughing = _job("SetupC", tools=1, operations=2, simulation_ready=True)
    engraving = _job("SetupD", tools=1, operations=0)

    names = set(
        manufacture_provider_tool_names(
            _domain(roughing, engraving),
            _AVAILABLE,
        )
    )

    assert {
        "manufacture.tool",
        "manufacture.program",
        "manufacture.operations",
        "manufacture.dressup",
        "manufacture.simulation",
    } <= names
    assert "manufacture.close_simulation" not in names
    assert "manufacture.post" not in names
    assert "manufacture.post_job" not in names
    assert "manufacture.post_selected" not in names


def test_selected_busy_setup_exposes_status_and_safe_reads_only() -> None:
    setup = _job("SetupBusy", tools=1, operations=2)
    domain = _domain(setup, active=setup)
    domain["background_jobs"] = [
        {
            "job_id": "background-a",
            "capability": "manufacture.path.pocket_shape",
            "resource_scope": "manufacture:SetupBusy",
            "phase": "preparing",
            "progress_percent": 40,
            "progress_message": "Generating CAM path",
            "terminal": False,
            "cancel_requested": False,
        }
    ]

    names = set(manufacture_provider_tool_names(domain, _AVAILABLE))

    assert names == {"state.read", "native.job"}


def test_busy_unrelated_setup_does_not_hide_selected_setup_tools() -> None:
    first = _job("SetupA", tools=1, operations=1)
    second = _job("SetupB", tools=1, operations=0)
    domain = _domain(first, second, active=second)
    domain["background_jobs"] = [
        {
            "job_id": "background-a",
            "capability": "manufacture.path.profile",
            "resource_scope": "manufacture:SetupA",
            "phase": "preparing",
            "progress_percent": 20,
            "progress_message": "Generating CAM path",
            "terminal": False,
            "cancel_requested": False,
        }
    ]

    names = set(manufacture_provider_tool_names(domain, _AVAILABLE))

    assert "manufacture.operation" not in names
    assert "manufacture.pocket" in names
    assert "document.save" in names


def test_provider_authorization_applies_manufacture_scope_after_human_ribbon() -> None:
    setup = _job("SetupE", tools=1)
    surface = _surface()

    projected = provider_authorized_native_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain(setup, active=setup)},
    )

    assert projected.snapshot is surface.snapshot
    assert "workspace.switch" not in projected.tool_names
    assert "manufacture.operation" not in projected.tool_names
    assert "manufacture.simulation" not in projected.tool_names
    assert tuple(schema["name"] for schema in projected.schemas) == projected.tool_names


def _schema_operations(schema: dict) -> set[str]:
    parameters = schema["parameters"]
    branches = parameters.get("oneOf", [parameters])
    result = set()
    for branch in branches:
        operation = branch["properties"]["operation"]
        result.update(operation.get("enum", [operation.get("const")]))
    return result


def test_focused_inspection_tools_follow_exact_setup_content() -> None:
    registry, surface = _definition_surface(
        manufacture_focused_inspect_capability_definitions()
    )

    blank = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain()},
        registry=registry,
    )
    setup = _job("SetupG", tools=1, operations=0)
    configured = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain(setup, active=setup)},
        registry=registry,
    )
    setup_with_path = _job("SetupH", tools=1, operations=1)
    with_path = scope_manufacture_provider_surface(
        surface,
        {
            "surface_id": "manufacture",
            "domain": _domain(setup_with_path, active=setup_with_path),
        },
        registry=registry,
    )

    assert set(blank.tool_names) == {"manufacture.setups"}
    assert set(configured.tool_names) == {
        "manufacture.setups",
        "manufacture.read_setup",
        "manufacture.setup_options",
        "manufacture.validate",
        "manufacture.loop",
        "manufacture.geometry",
        "manufacture.threads",
    }
    assert set(with_path.tool_names) == set(configured.tool_names) | {
        "manufacture.toolpath"
    }

    retained_domain = _domain()
    retained_domain["remaining_stock_result_count"] = 25
    retained = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": retained_domain},
        registry=registry,
    )
    assert set(retained.tool_names) == {
        "manufacture.setups",
        "manufacture.remaining_stock",
    }


def test_provider_tool_catalog_is_one_clear_listing_operation() -> None:
    registry, surface = _definition_surface(
        (manufacture_tool_catalog_capability_definition(),)
    )

    projected = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain()},
        registry=registry,
    )

    assert projected.tool_names == ("manufacture.tool_catalog",)
    assert _schema_operations(projected.schemas[0]) == {"list_tools"}


def test_setup_lifecycle_operations_sharpen_as_resources_are_added() -> None:
    definitions = (
        manufacture_job_capability_definition(),
        manufacture_tool_capability_definition(),
        *manufacture_focused_tool_capability_definitions(),
        manufacture_operation_capability_definition(),
        *manufacture_focused_operation_capability_definitions(),
        manufacture_program_capability_definition(),
        manufacture_modify_capability_definition(),
        *manufacture_focused_modify_capability_definitions(),
    )
    registry, surface = _definition_surface(definitions)
    no_tool = _job("SetupI", tools=0, operations=0)
    with_tool = _job("SetupJ", tools=1, operations=0)
    with_path = _job("SetupK", tools=1, operations=2)

    projected_no_tool = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain(no_tool, active=no_tool)},
        registry=registry,
    )
    projected_with_tool = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain(with_tool, active=with_tool)},
        registry=registry,
    )
    projected_with_path = scope_manufacture_provider_surface(
        surface,
        {"surface_id": "manufacture", "domain": _domain(with_path, active=with_path)},
        registry=registry,
    )

    assert set(projected_no_tool.tool_names) == {
        "manufacture.job",
        "manufacture.tool",
        "manufacture.add_tool",
        "manufacture.program",
    }
    no_tool_operations = {
        name: _schema_operations(schema)
        for name, schema in zip(
            projected_no_tool.tool_names,
            projected_no_tool.schemas,
            strict=True,
        )
    }
    assert no_tool_operations["manufacture.tool"] == {"create_controller"}
    assert no_tool_operations["manufacture.program"] == {"comment", "stop"}
    assert no_tool_operations["manufacture.job"] == {
        "configure_stock",
        "create_job",
        "create_job_from_template",
        "orient_workpiece",
        "update_setup",
    }

    assert "manufacture.operation" not in projected_with_tool.tool_names
    assert {
        "manufacture.face",
        "manufacture.pocket",
        "manufacture.profile",
        "manufacture.drill",
        "manufacture.add_tool",
        "manufacture.set_controller",
        "manufacture.update_tool",
    } <= set(projected_with_tool.tool_names)
    with_tool_operations = {
        name: _schema_operations(schema)
        for name, schema in zip(
            projected_with_tool.tool_names,
            projected_with_tool.schemas,
            strict=True,
        )
    }
    assert with_tool_operations["manufacture.tool"] == {
        "create_controller",
        "update_controller",
        "update_tool_bit",
    }
    assert with_tool_operations["manufacture.program"] == {
        "comment",
        "stop",
        "custom",
    }
    assert with_tool_operations["manufacture.job"] == {
        "configure_stock",
        "create_job",
        "create_job_from_template",
        "orient_workpiece",
        "update_setup",
    }
    assert "manufacture.modify" not in projected_with_tool.tool_names
    assert "manufacture.operations" not in projected_with_tool.tool_names
    assert "manufacture.dressup" not in projected_with_tool.tool_names

    assert "manufacture.modify" not in projected_with_path.tool_names
    assert "manufacture.operations" in projected_with_path.tool_names
    assert "manufacture.dressup" in projected_with_path.tool_names
    assert {
        "manufacture.start_point",
        "manufacture.array",
        "manufacture.copy_path",
    } <= set(projected_with_path.tool_names)


def test_invalid_or_truncated_state_fails_closed_to_discovery() -> None:
    malformed = _domain(_job("SetupF"))
    malformed["jobs_truncated"] = True
    malformed["job_count"] = 100

    names = set(manufacture_provider_tool_names(malformed, _AVAILABLE))

    assert names == _SHARED | {
        "manufacture.job",
        "manufacture.setups",
        "manufacture.tool_catalog",
    }


def test_snapshot_publishes_readiness_for_every_visible_setup(monkeypatch) -> None:
    first = SimpleNamespace(Name="SetupOne", Document=None)
    second = SimpleNamespace(Name="SetupTwo", Document=None)
    document = SimpleNamespace(Objects=[first, second], Uid="document-a")
    first.Document = document
    second.Document = document

    monkeypatch.setattr(manufacture_snapshot, "is_job", lambda obj: obj in (first, second))
    monkeypatch.setattr(
        manufacture_snapshot,
        "job_state",
        lambda job, **_limits: _job(
            job.Name,
            tools=1,
            operations=1 if job is first else 0,
        ),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "build_active_job_summary",
        lambda _document, job, state: {
            **state,
            "readiness": {
                "simulation": {"ready": job is first},
                "post": {"ready": job is second},
            },
            "toolpath_validity": {"all_active_valid": job is first},
        },
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "resolve_active_job",
        lambda *_args: (None, "choose_job"),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_job_creation_environment",
        lambda: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_tool_catalog",
        lambda: SimpleNamespace(page=lambda *_args: {}),
    )
    monkeypatch.setattr(manufacture_snapshot, "property_bag_snapshot", lambda _doc: {})
    monkeypatch.setattr(manufacture_snapshot, "area_snapshot", lambda _doc: {})
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_setup_state",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_tool_shape_inventory",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_trajectory_state",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )

    result = manufacture_snapshot.build_manufacture_snapshot(
        document,
        selection={"items": []},
        background_jobs=(
            SimpleNamespace(
                job_id="background-a",
                document_uid="document-a",
                capability_name="manufacture.path.profile",
                resource_scope="manufacture:SetupOne",
                phase="preparing",
                progress_percent=35,
                progress_message="Generating CAM path",
                terminal=False,
                cancel_requested=False,
            ),
        ),
    )

    assert result["active_job"] is None
    assert [setup["readiness"] for setup in result["jobs"]] == [
        {
            "simulation": {"ready": True},
            "post": {"ready": False},
        },
        {
            "simulation": {"ready": False},
            "post": {"ready": True},
        },
    ]
    assert result["background_jobs"] == [
        {
            "job_id": "background-a",
            "capability": "manufacture.path.profile",
            "resource_scope": "manufacture:SetupOne",
            "phase": "preparing",
            "progress_percent": 35,
            "progress_message": "Generating CAM path",
            "terminal": False,
            "cancel_requested": False,
        }
    ]


def test_focused_setup_keeps_the_exact_counts_required_for_provider_scope(
    monkeypatch,
) -> None:
    setup = SimpleNamespace(Name="FocusedSetup", Document=None)
    document = SimpleNamespace(Objects=[setup])
    setup.Document = document
    state = _job("FocusedSetup", tools=2, operations=3)
    monkeypatch.setattr(manufacture_snapshot, "is_job", lambda obj: obj is setup)
    monkeypatch.setattr(manufacture_snapshot, "job_state", lambda *_args, **_kw: state)
    monkeypatch.setattr(
        manufacture_snapshot,
        "build_active_job_summary",
        lambda _document, _job, exact: {
            "object_name": exact["object_name"],
            "state_sha256": exact["state_sha256"],
            "readiness": exact["readiness"],
            "toolpath_validity": {"all_active_valid": True},
        },
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "resolve_active_job",
        lambda *_args: (setup, "only_job"),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_job_creation_environment",
        lambda: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_tool_catalog",
        lambda: SimpleNamespace(page=lambda *_args: {}),
    )
    monkeypatch.setattr(manufacture_snapshot, "property_bag_snapshot", lambda _doc: {})
    monkeypatch.setattr(manufacture_snapshot, "area_snapshot", lambda _doc: {})
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_setup_state",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_tool_shape_inventory",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )
    monkeypatch.setattr(
        manufacture_snapshot,
        "capture_robot_trajectory_state",
        lambda _doc: SimpleNamespace(summary=lambda: {}),
    )

    result = manufacture_snapshot.build_manufacture_snapshot(document)

    assert result["active_job"]["counts"] == state["counts"]
    assert "manufacture.operation" not in manufacture_provider_tool_names(
        result,
        _AVAILABLE,
    )


def test_setup_catalog_is_searchable_paged_and_returns_exact_targets(monkeypatch) -> None:
    setups = [
        SimpleNamespace(Name="RearOp", Label="Rear operation"),
        SimpleNamespace(Name="TopRough", Label="Top roughing"),
        SimpleNamespace(Name="TopFinish", Label="Top finishing"),
        SimpleNamespace(Name="Fixture", Label="Fixture geometry"),
    ]
    document = SimpleNamespace(Objects=setups)
    states = {
        setup.Name: _job(
            setup.Name,
            tools=index,
            operations=index + 1,
        )
        for index, setup in enumerate(setups)
    }
    for setup in setups:
        states[setup.Name]["label"] = setup.Label
    monkeypatch.setattr(manufacture_inspect, "is_job", lambda obj: obj is not setups[-1])
    state_reads = []

    def read_setup_state(job, **limits):
        state_reads.append((job.Name, limits))
        return states[job.Name]

    monkeypatch.setattr(manufacture_inspect, "job_state", read_setup_state)
    monkeypatch.setattr(
        manufacture_inspect,
        "build_active_job_summary",
        lambda _document, _job, state: {
            "readiness": state["readiness"],
            "toolpath_validity": {"all_active_valid": False},
        },
    )

    first_page = manufacture_inspect.list_setups(
        document,
        query="top",
        offset=0,
        page_size=1,
    )
    second_page = manufacture_inspect.list_setups(
        document,
        query="top",
        offset=1,
        page_size=1,
    )

    assert first_page == {
        "setups": {
            "query": "top",
            "offset": 0,
            "count": 1,
            "total": 2,
            "next_offset": 1,
            "items": [
                {
                    **states["TopRough"],
                    "toolpath_validity": {"all_active_valid": False},
                }
            ],
        }
    }
    assert second_page["setups"]["next_offset"] is None
    assert second_page["setups"]["items"][0]["object_name"] == "TopFinish"
    assert state_reads == [
        (
            "TopRough",
            {"operation_limit": 0, "tool_limit": 0, "model_limit": 0},
        ),
        (
            "TopFinish",
            {"operation_limit": 0, "tool_limit": 0, "model_limit": 0},
        ),
    ]


def test_remaining_stock_catalog_pages_beyond_context_limit(monkeypatch) -> None:
    results = [
        SimpleNamespace(Name=f"CutMaterial{index:03d}", Label=f"Rear stock {index:03d}")
        for index in range(40)
    ]
    document = SimpleNamespace(Objects=results)
    monkeypatch.setattr(manufacture_inspect, "is_simulation_result", lambda _obj: True)
    state_reads = []

    def read_result_state(result):
        state_reads.append(result.Name)
        return {
            "object_name": result.Name,
            "label": result.Label,
            "type_id": "Mesh::FeaturePython",
            "state_sha256": f"{int(result.Name[-3:]) + 1:064x}",
            "source_setup": {
                "object_name": f"Job{result.Name[-3:]}",
                "label": f"Rear setup {result.Name[-3:]}",
            },
            "source_current": True,
            "provenance_valid": True,
        }

    monkeypatch.setattr(
        manufacture_inspect,
        "simulation_result_state",
        read_result_state,
    )

    page = manufacture_inspect.list_remaining_stock(
        document,
        query="rear",
        offset=32,
        page_size=8,
    )

    assert page["remaining_stock"]["total"] == 40
    assert page["remaining_stock"]["offset"] == 32
    assert page["remaining_stock"]["next_offset"] is None
    assert [item["object_name"] for item in page["remaining_stock"]["items"]] == [
        f"CutMaterial{index:03d}" for index in range(32, 40)
    ]
    assert state_reads == [f"CutMaterial{index:03d}" for index in range(32, 40)]


def test_provider_state_removes_unadvertised_domains_and_keeps_exact_setup_targets() -> None:
    setup = _job("SetupL", tools=2, operations=3)
    setup.update(
        models=[{"object_name": "Part", "state_sha256": "1" * 64}],
        tools=[
            {
                "object_name": "ToolController",
                "state_sha256": "2" * 64,
                "verbose_internal": "x" * 4000,
            }
        ],
        operations=[
            {
                "object_name": "Pocket",
                "state_sha256": "3" * 64,
                "verbose_internal": "y" * 4000,
            }
        ],
    )
    raw = {
        "surface_id": "manufacture",
        "document": {"document_uid": "document-a", "document_name": "Cam"},
        "structural_revision": 12,
        "working_set": [],
        "domain": {
            **_domain(setup, active=setup),
            "job_creation": {"state_sha256": "4" * 64, "template_count": 0},
            "tool_catalog": {
                "state_sha256": "5" * 64,
                "count": 200,
                "items": [{"definition": "z" * 8000}],
            },
            "property_bags": [{"payload": "p" * 8000}],
            "robot_tool_shapes": {"items": ["r" * 8000]},
            "robot_setup": {"payload": "r" * 8000},
            "robot_trajectories": {"payload": "r" * 8000},
        },
    }

    visible = provider_visible_native_state(raw)

    assert visible["domain"]["tool_catalog"] == {
        "state_sha256": "5" * 64,
        "count": 200,
    }
    assert "property_bags" not in visible["domain"]
    assert "robot_tool_shapes" not in visible["domain"]
    assert "robot_setup" not in visible["domain"]
    assert "robot_trajectories" not in visible["domain"]
    assert visible["domain"]["jobs"][0]["tools"] == [
        {
            "object_name": "ToolController",
            "state_sha256": "2" * 64,
        }
    ]
    assert visible["domain"]["jobs"][0]["operations"] == [
        {"object_name": "Pocket", "state_sha256": "3" * 64}
    ]
    assert visible["domain"]["active_job"]["counts"] == setup["counts"]
    assert "verbose_internal" not in str(visible)
    assert "verbose_internal" in str(raw)
