# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyPlayback as playback_module
import VibeCADNativeAssemblyPlaybackRuntime as runtime_module
from VibeCADNativeAssemblyPlayback import NativeAssemblyPlaybackError
from VibeCADNativeAssemblyPlaybackRuntime import NativeAssemblyPlaybackRuntime
from VibeCADNativeAssemblyPlaybackSchema import (
    assembly_playback_capability_definition,
)
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext, NativeRuntimeContextError
from VibeCADNativeSessionFactory import _edit_or_task_active
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "native-assembly-playback-document"
    Name = "PlaybackDocument"


class _Quantity:
    def __init__(self, value: float) -> None:
        self.Value = value


class _Simulation:
    aTimeStart = _Quantity(2.0)
    bTimeEnd = _Quantity(3.0)
    cTimeStepOutput = _Quantity(0.1)


def _context(
    *,
    surface: str = "assemble",
    task_active: bool = False,
) -> NativeRuntimeContext:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("native-assembly-playback-unit")
    return NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: surface,
        edit_or_task_active=lambda: task_active,
    )


def test_schema_exactly_maps_the_complete_player_lifecycle() -> None:
    definition = assembly_playback_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert definition.name == "assembly.simulation"
    assert definition.primary_classification == "view"
    assert tuple(variants) == ("open", "seek", "step", "play", "pause", "close")
    assert all(
        variant.surface_ids == frozenset({"assemble"}) for variant in variants.values()
    )
    assert all(
        variant.transaction_behavior == "presentation" for variant in variants.values()
    )
    assert all(variant.background_required is False for variant in variants.values())
    assert variants["open"].action_ids == frozenset({"AssemblyContextPlaySimulation"})
    assert variants["close"].action_ids == frozenset({"AssemblySimulationClose"})

    schema = definition.provider_schema(tuple(variants))["parameters"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["operation"]["enum"] == list(variants)
    assert schema["properties"]["playback_id"]["pattern"] == r"^[0-9a-f]{32}$"
    assert schema["properties"]["mode"]["enum"] == [
        "hold",
        "forward",
        "backward",
    ]

    registry = build_native_capability_registry()
    assert registry.definition("assembly.simulation") is not None
    assert registry.implementation("assembly.simulation") is not None


def test_runtime_decodes_exact_open_and_control_arguments(monkeypatch) -> None:
    context = _context()
    runtime = NativeAssemblyPlaybackRuntime(context)
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "open_native_assembly_playback",
        lambda received_context, spec: (
            observed.append(("open", received_context, spec)) or {"operation": "open"}
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "control_native_assembly_playback",
        lambda received_context, operation, spec: (
            observed.append((operation, received_context, spec))
            or {"operation": operation}
        ),
    )

    opened = runtime.control(
        {
            "operation": "open",
            "simulation": {"object_name": "Simulation"},
            "expected_simulation_state_sha256": "a" * 64,
            "time_seconds": 0.0,
            "mode": "hold",
        }
    )
    played = runtime.control(
        {
            "operation": "play",
            "simulation": {"object_name": "Simulation"},
            "playback_id": "b" * 32,
            "direction": "backward",
        }
    )

    assert opened == {"operation": "open"}
    assert observed[0][2].simulation_ref.object_name == "Simulation"
    assert observed[0][2].expected_simulation_state_sha256 == "a" * 64
    assert observed[0][2].mode == "hold"
    assert played == {"operation": "play"}
    assert observed[1][2].playback_id == "b" * 32
    assert observed[1][2].direction == "backward"

    with pytest.raises(Exception, match="do not match"):
        runtime.control(
            {
                "operation": "pause",
                "simulation": {"object_name": "Simulation"},
                "playback_id": "b" * 32,
                "extra": True,
            }
        )


def test_output_time_mapping_is_exact_and_bounded() -> None:
    simulation = _Simulation()

    assert playback_module._grid_frame(simulation, 2.0) == (1, 2.0)
    assert playback_module._grid_frame(simulation, 2.3) == (
        4,
        pytest.approx(2.3),
    )
    assert playback_module._grid_frame(simulation, 2.8, frame_count=10) == (
        9,
        pytest.approx(2.8),
    )
    with pytest.raises(NativeAssemblyPlaybackError, match="output-time grid"):
        playback_module._grid_frame(simulation, 2.35)
    with pytest.raises(NativeAssemblyPlaybackError, match="generated range"):
        playback_module._grid_frame(simulation, 2.9, frame_count=10)
    with pytest.raises(NativeAssemblyPlaybackError, match="finite number"):
        playback_module._grid_frame(simulation, float("nan"))


def test_runtime_context_allows_only_owned_playback_controls_during_task(
    monkeypatch,
) -> None:
    context = _context(task_active=True)
    monkeypatch.setattr(
        playback_module,
        "owns_active_native_assembly_playback",
        lambda document: document is context.document,
    )

    with pytest.raises(NativeRuntimeContextError, match="Finish or close"):
        context.guard()
    context.guard(allow_owned_playback=True)

    sketch = _context(surface="sketch.edit", task_active=True)
    sketch.guard()


def test_session_task_guard_ignores_normal_assembly_edit_but_not_dialog_or_sketch() -> (
    None
):
    class Service:
        def __init__(self, summary):
            self.summary = summary

        def task_panel_summary(self):
            return self.summary

    assert (
        _edit_or_task_active(
            Service(
                {
                    "active_dialog": False,
                    "edit_mode": True,
                    "active_sketch": None,
                    "edit_object": {"type": "Assembly::AssemblyObject"},
                }
            )
        )
        is False
    )
    assert (
        _edit_or_task_active(
            Service({"active_dialog": True, "edit_mode": True, "active_sketch": None})
        )
        is True
    )
    assert (
        _edit_or_task_active(
            Service(
                {
                    "active_dialog": False,
                    "edit_mode": True,
                    "active_sketch": None,
                    "edit_object": {"type": "PartDesign::Feature"},
                }
            )
        )
        is True
    )
    assert (
        _edit_or_task_active(
            Service(
                {
                    "active_dialog": False,
                    "edit_mode": True,
                    "active_sketch": "Sketch",
                }
            )
        )
        is True
    )


def test_live_session_uses_task_form_identity_not_transient_dialog_wrapper(
    monkeypatch,
) -> None:
    form = object()

    class Dialog:
        def getDialogContent(self):
            return [form]

    assembly = SimpleNamespace(Name="Assembly")
    simulation = SimpleNamespace(Name="Simulation")

    class Document:
        def getObject(self, name):
            return {"Assembly": assembly, "Simulation": simulation}.get(name)

    panel = SimpleNamespace(
        form=form,
        playback_only=True,
        _ownsLiveTaskContext=lambda: True,
    )
    session = playback_module._PlaybackSession(
        playback_id="a" * 32,
        document=Document(),
        assembly=assembly,
        simulation=simulation,
        panel=panel,
        dialog=Dialog(),
        form=form,
        state_before=None,
        solver_before=None,
        document_objects_before=(),
        visibility_before=(),
        camera_before="",
        modified_before=False,
    )
    # activeTaskDialog() returns a new Python wrapper each time even when the
    # underlying C++ task has not changed.
    monkeypatch.setattr(playback_module, "_active_task_dialog", lambda: Dialog())

    assert playback_module._session_is_live(session) is True


def test_camera_equivalence_ignores_only_renderer_owned_clipping_planes() -> None:
    first = "position 1 2 3\nnearDistance 0.1\nfarDistance 100\nheight 10"
    second = "position 1 2 3\nnearDistance 0.2\nfarDistance 200\nheight 10"
    changed = "position 2 2 3\nnearDistance 0.2\nfarDistance 200\nheight 10"

    assert playback_module._camera_is_same(first, second) is True
    assert playback_module._camera_is_same(first, changed) is False


def test_destroyed_player_cleanup_cannot_drop_a_replacement_session() -> None:
    uid = "native-playback-cleanup-unit"
    session = SimpleNamespace(playback_id="a" * 32)
    playback_module._SESSIONS[uid] = session
    try:
        playback_module._forget_session(uid, "b" * 32)
        assert playback_module._SESSIONS[uid] is session

        playback_module._forget_session(uid, "a" * 32)
        assert uid not in playback_module._SESSIONS
    finally:
        playback_module._SESSIONS.pop(uid, None)
