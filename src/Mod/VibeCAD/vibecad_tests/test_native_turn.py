# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeSurface import NativeSurfaceChanged, SURFACE_CHANGED
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
