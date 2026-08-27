# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
    NativeProviderSurface,
    _provider_schema_operations,
    provider_visible_native_schema,
)
from VibeCADNativeAnalyzeInspectSchema import (
    ANALYZE_INSPECT_CAPABILITY_NAME,
    analyze_inspect_capability_definition,
)
from VibeCADNativeSurface import (
    NativeSurfaceChanged,
    NativeSurfaceSnapshot,
    SURFACE_CHANGED,
)
from VibeCADNativeTurn import (
    NATIVE_TURN_UNAVAILABLE,
    NativeTurnChanged,
    NativeTurnUnavailable,
    freeze_native_turn,
    require_frozen_native_turn,
)

from vibecad_tests.test_native_capability_registry import (
    _focused_inventory_by_surface,
    _inspection_definition,
    _primitive_definition,
    _register_complete,
)
from vibecad_tests.test_ribbon_surface import _Controller, _manifest


@pytest.fixture
def focused_inventory(monkeypatch) -> None:
    import VibeCADNativeActionManifest as action_manifest_module

    monkeypatch.setattr(
        action_manifest_module,
        "KNOWN_ACTIONS_BY_SURFACE",
        _focused_inventory_by_surface(),
    )


def test_incomplete_production_registry_cannot_start_a_native_turn() -> None:
    with pytest.raises(NativeTurnUnavailable) as caught:
        freeze_native_turn(_Controller(_manifest(), revision=6))

    assert caught.value.failure() == {
        "error_code": NATIVE_TURN_UNAVAILABLE,
        "message": "Native mode is not yet complete for this ribbon.",
    }


def test_complete_surface_freezes_exact_ribbon_and_schema_identity(
    focused_inventory,
) -> None:
    snapshot = freeze_native_turn(
        _Controller(_manifest(), revision=6),
        _register_complete(),
    )

    assert snapshot.surface.surface_id == "model"
    assert snapshot.surface.revision == 6
    assert snapshot.tool_names == ("model.primitive", "inspect.query")
    assert len(snapshot.schema_sha256) == 64
    assert tuple(schema["name"] for schema in snapshot.provider_schemas) == (
        "model.primitive",
        "inspect.query",
    )
    assert snapshot.summary() == {
        "mode": "native",
        "surface_id": "model",
        "surface_revision": 6,
        "schema_sha256": snapshot.schema_sha256,
        "tool_count": 2,
    }


def test_unchanged_turn_reauthorizes_exactly(focused_inventory) -> None:
    controller = _Controller(_manifest(), revision=6)
    registry = _register_complete()
    expected = freeze_native_turn(controller, registry)

    assert require_frozen_native_turn(expected, controller, registry) == expected


def test_provider_turn_can_freeze_an_exact_subset_of_a_complete_surface(
    focused_inventory,
) -> None:
    controller = _Controller(_manifest(), revision=6)
    registry = _register_complete()

    expected = freeze_native_turn(
        controller,
        registry,
        tool_names=("inspect.query",),
    )

    assert expected.tool_names == ("inspect.query",)
    assert tuple(schema["name"] for schema in expected.provider_schemas) == (
        "inspect.query",
    )
    assert require_frozen_native_turn(expected, controller, registry) == expected


@pytest.mark.parametrize(
    "operation",
    ("study", "analysis", "validate_assignments"),
)
def test_provider_turn_keeps_exact_authorized_operation_when_visible_schema_is_compact(
    monkeypatch,
    operation,
) -> None:
    import VibeCADNativeTurn as turn_module

    definition = analyze_inspect_capability_definition()
    registry = NativeCapabilityRegistry()
    registry.register_definition(definition)
    exact_schema = definition.provider_schema(
        tuple(variant.operation for variant in definition.variants)
    )
    surface = NativeSurfaceSnapshot(
        "analyze",
        9,
        "a" * 64,
        ("VibeCAD_AnalyzeReadStudy",),
        ("VibeCAD_AnalyzeReadStudy",),
        (),
    )
    provider_surface = NativeProviderSurface(
        snapshot=surface,
        available=True,
        unavailable_reason="",
        tool_names=(ANALYZE_INSPECT_CAPABILITY_NAME,),
        schemas=(exact_schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    visible_schema = provider_visible_native_schema(
        definition.provider_schema((operation,))
    )
    monkeypatch.setattr(
        turn_module,
        "read_active_ribbon_surface",
        lambda _controller: surface,
    )
    monkeypatch.setattr(
        turn_module,
        "resolve_native_provider_surface",
        lambda _surface, _registry: provider_surface,
    )

    frozen = freeze_native_turn(
        object(),
        registry,
        tool_names=(ANALYZE_INSPECT_CAPABILITY_NAME,),
        provider_schemas=(visible_schema,),
        authorized_operations={ANALYZE_INSPECT_CAPABILITY_NAME: (operation,)},
    )

    assert _provider_schema_operations(frozen.provider_schemas[0]) == (operation,)
    assert (
        provider_visible_native_schema(frozen.provider_schemas[0])
        == visible_schema
    )


def test_native_context_keeps_operation_authorization_out_of_model_visible_state(
    monkeypatch,
) -> None:
    import VibeCADNativeProviderContext as provider_context
    import VibeCADSession as session_module

    definition = analyze_inspect_capability_definition()
    registry = NativeCapabilityRegistry()
    registry.register_definition(definition)
    exact_schema = definition.provider_schema(("study",))
    snapshot = NativeSurfaceSnapshot(
        "analyze",
        9,
        "a" * 64,
        ("VibeCAD_AnalyzeReadStudy",),
        ("VibeCAD_AnalyzeReadStudy",),
        (),
    )
    provider_surface = NativeProviderSurface(
        snapshot=snapshot,
        available=True,
        unavailable_reason="",
        tool_names=(ANALYZE_INSPECT_CAPABILITY_NAME,),
        schemas=(exact_schema,),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    monkeypatch.setattr(
        provider_context,
        "resolve_production_native_surface",
        lambda: (registry, provider_surface),
    )
    monkeypatch.setattr(
        provider_context,
        "provider_authorized_native_surface",
        lambda surface, _state, **_kwargs: surface,
    )
    monkeypatch.setattr(
        provider_context,
        "provider_visible_native_state",
        lambda _state: {"surface_id": "analyze"},
    )

    service = type(
        "Service",
        (),
        {
            "provider_context_summary": lambda self: {},
            "active_workbench_name": lambda self: "FemWorkbench",
            "modeling_engine": lambda self: "native",
            "provider_debug_config": lambda self: {"enabled": False},
        },
    )()
    context = session_module._capture_context_for_provider(
        service,
        prepared_native_state={"surface_id": "analyze"},
    )

    assert _provider_schema_operations(context["provider_tool_schemas"][0]) == ()
    authorization = context["_native_turn_authorization"]
    assert authorization["operations_by_tool"] == {
        ANALYZE_INSPECT_CAPABILITY_NAME: ["study"]
    }
    assert (
        authorization["schema_sha256"]
        == context["provider_tool_surface"]["schema_sha256"]
    )
    assert (
        authorization["provider_schema_sha256"]
        == context["provider_tool_surface"]["schema_sha256"]
    )
    assert len(authorization["operation_scope_sha256"]) == 64
    assert "_native_turn_authorization" not in session_module._provider_state_payload(
        context
    )


def test_provider_turn_rejects_a_subset_outside_the_complete_surface(
    focused_inventory,
) -> None:
    with pytest.raises(NativeTurnUnavailable) as caught:
        freeze_native_turn(
            _Controller(_manifest(), revision=6),
            _register_complete(),
            tool_names=("analyze.missing",),
        )

    assert "outside the complete Native surface" in str(caught.value)


def test_ribbon_identity_change_rejects_before_tool_authority(
    focused_inventory,
) -> None:
    controller = _Controller(_manifest(), revision=6)
    registry = _register_complete()
    expected = freeze_native_turn(controller, registry)
    controller.values["VibeCADActiveSurfaceRevision"] = 7

    with pytest.raises(NativeSurfaceChanged) as caught:
        require_frozen_native_turn(expected, controller, registry)

    assert caught.value.failure()["error_code"] == SURFACE_CHANGED


def test_build_environment_change_rejects_the_frozen_turn(
    focused_inventory,
) -> None:
    controller = _Controller(_manifest(), revision=6)
    registry = _register_complete()
    expected = freeze_native_turn(controller, registry)
    controller.values["VibeCADActiveSurfaceEnvironment"]["build_features"][
        "fem_vtk"
    ] = False

    with pytest.raises(NativeSurfaceChanged) as caught:
        require_frozen_native_turn(expected, controller, registry)

    assert caught.value.failure()["error_code"] == SURFACE_CHANGED


def test_schema_change_rejects_the_existing_turn(focused_inventory) -> None:
    controller = _Controller(_manifest(), revision=6)
    expected = freeze_native_turn(controller, _register_complete())
    registry = NativeCapabilityRegistry()
    for definition in (_primitive_definition(), _inspection_definition()):
        if definition.name == "model.primitive":
            definition = NativeCapabilityDefinition(
                name=definition.name,
                description="Create one exact solid feature.",
                primary_classification=definition.primary_classification,
                variants=definition.variants,
            )
        registry.register_definition(definition)
        registry.register_implementation(
            NativeCapabilityImplementation(definition.name, lambda _arguments: {})
        )

    with pytest.raises(NativeTurnChanged) as caught:
        require_frozen_native_turn(expected, controller, registry)

    assert caught.value.failure() == {
        "error_code": SURFACE_CHANGED,
        "message": (
            "The available CAD tools changed after this turn started. "
            "Continue in a new turn."
        ),
        "current_surface": "model",
        "repair": {"resume_next_turn": True},
    }


def test_native_turn_module_has_no_activation_or_execution_api() -> None:
    import VibeCADNativeTurn as module

    public_names = {name for name in vars(module) if not name.startswith("_")}
    forbidden = ("activate", "switch", "dispatch", "run_command", "execute")
    assert not any(
        fragment in name.lower()
        for name in public_names
        for fragment in forbidden
    )
