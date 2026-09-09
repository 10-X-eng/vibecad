# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace
from concurrent.futures import Future

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
        document_thread_dispatch=lambda operation: operation(),
    )


def test_async_runtime_preserves_pending_observation_until_completion(monkeypatch):
    context = _context()
    pending = Future()
    monkeypatch.setattr(runtime_module, 'open_native_assembly_playback_async',
                        lambda *args: pending, raising=False)
    observed = []
    monkeypatch.setattr(context.state, 'cancel_mutation', lambda ticket: observed.append(ticket))
    ticket = context.state.begin_call(context.document_uid, 'assembly.playback')
    runtime = NativeAssemblyPlaybackRuntime(context)
    result = runtime.control_async(
        {'operation': 'show', 'simulation': {'object_name': 'Simulation'}}, ticket=ticket
    )
    assert result is pending and observed == []
    pending.set_result({'operation': 'show'})
    assert observed == [ticket]


def test_pending_playback_dispatch_failure_does_not_leave_a_waiter_running():
    pending = Future()
    def disconnected(operation):
        raise RuntimeError('document owner closed')
    context = SimpleNamespace(document_thread_dispatch=disconnected)
    result = playback_module._playback_completion(context, pending, lambda value: value)
    pending.set_result({'frame': 3})
    with pytest.raises(RuntimeError, match='document owner closed'):
        result.result(timeout=0)


def test_cancel_after_player_ready_closes_only_the_unaccepted_launch():
    callbacks = []
    pending = Future()
    result = playback_module._playback_completion(
        SimpleNamespace(document_thread_dispatch=callbacks.append), pending,
        lambda value: pytest.fail('Cancelled launch must not be registered'),
        cancel_completed=lambda: callbacks.append('closed owned player'),
    )
    pending.set_result(object())
    result.cancel()
    assert len(callbacks) == 2
    callbacks[0]()
    callbacks[1]()
    assert callbacks[-1] == 'closed owned player' and result.cancelled()


def test_native_launch_accepts_fresh_wrappers_for_the_same_task(monkeypatch):
    context = _context()
    assembly, simulation = SimpleNamespace(numberOfFrames=lambda: 22), object()
    form = SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 3),
                           destroyed=SimpleNamespace(connect=lambda callback: None))
    panel = SimpleNamespace(form=form, playback_only=True, assembly=assembly,
                            simFeaturePy=simulation, document_was_modified=False,
                            _ownsLiveTaskContext=lambda: True)
    state = SimpleNamespace(solver_state=None, components=(), grounded_joints=(),
                            regular_joints=(), simulation_records=())
    calls = []
    monkeypatch.setattr(playback_module, '_validate_open', lambda *args: (state, assembly, simulation, 3, 2.2))
    monkeypatch.setattr(playback_module, '_document_graph', lambda doc: ())
    monkeypatch.setattr(playback_module, '_active_camera', lambda doc: '')
    monkeypatch.setattr(playback_module, '_gui_modified', lambda doc: False)
    monkeypatch.setattr(playback_module, '_transaction_open', lambda doc: False)
    monkeypatch.setattr(playback_module, 'read_current_selection', lambda doc: ())
    monkeypatch.setattr(playback_module, 'capture_assembly_simulation_state', lambda obj: state)
    monkeypatch.setattr(playback_module, 'read_active_assembly', lambda doc: assembly)
    monkeypatch.setattr(playback_module, 'same_assembly', lambda a, b: a is b)
    monkeypatch.setattr(playback_module, '_status', lambda session, operation: {'ok': True})
    monkeypatch.setattr(playback_module, '_active_task_dialog', lambda: SimpleNamespace(
        getDialogContent=lambda: [form], reject=lambda: calls.append('reject')))
    pending = Future()
    result = playback_module.open_native_assembly_playback_async(
        context, SimpleNamespace(mode='hold'), opener=lambda *args: pending)
    try:
        pending.set_result(panel)
        assert result.result() == {'ok': True}
        assert calls == []
    finally:
        playback_module._SESSIONS.pop(context.document_uid, None)


def test_schema_exactly_maps_the_complete_player_lifecycle() -> None:
    definition = assembly_playback_capability_definition()
    variants = {variant.operation: variant for variant in definition.variants}

    assert definition.name == "assembly.playback"
    assert definition.primary_classification == "view"
    assert tuple(variants) == ("show", "seek", "step", "play", "pause", "close")
    assert all(
        variant.surface_ids == frozenset({"assemble"}) for variant in variants.values()
    )
    assert all(
        variant.transaction_behavior == "presentation" for variant in variants.values()
    )
    assert all(variant.background_required is False for variant in variants.values())
    assert variants["show"].action_ids == frozenset({"AssemblyContextPlaySimulation"})
    assert variants["close"].action_ids == frozenset({"AssemblySimulationClose"})

    assert definition.description == (
        "Show a motion study at a time, or seek, step, play, pause, and close "
        "active playback."
    )
    assert variants["show"].description == "Show a motion study at an optional time."
    assert variants["seek"].description == "Move active playback to a time."
    assert variants["step"].description == "Step active playback one frame."
    assert variants["play"].description == "Play active playback forward or backward."
    assert variants["pause"].description == "Pause active playback."
    assert variants["close"].description == "Close active playback."
    provider_parameters = definition.provider_schema(tuple(variants))["parameters"]
    assert provider_parameters["properties"]["operation"]["enum"] == list(variants)
    show_schema = variants["show"].provider_parameters()
    assert set(show_schema["required"]) == {"operation", "simulation"}
    assert "expected_simulation_state_sha256" not in show_schema["properties"]
    assert show_schema["properties"]["mode"]["default"] == "hold"
    assert show_schema["properties"]["mode"]["enum"] == [
        "hold",
        "forward",
        "backward",
    ]
    assert set(variants["seek"].provider_parameters()["required"]) == {
        "operation",
        "playback_id",
        "time_seconds",
    }
    assert set(variants["step"].provider_parameters()["required"]) == {
        "operation",
        "playback_id",
        "direction",
    }
    assert set(variants["play"].provider_parameters()["required"]) == {
        "operation",
        "playback_id",
        "direction",
    }
    assert set(variants["pause"].provider_parameters()["required"]) == {
        "operation",
        "playback_id",
    }
    assert set(variants["close"].provider_parameters()["required"]) == {
        "operation",
        "playback_id",
    }
    assert all(
        "simulation" not in variants[operation].provider_parameters()["properties"]
        for operation in ("seek", "step", "play", "pause", "close")
    )

    registry = build_native_capability_registry()
    assert registry.definition("assembly.playback") is not None
    assert registry.implementation("assembly.playback") is not None


def test_runtime_decodes_exact_open_and_control_arguments(monkeypatch) -> None:
    context = _context()
    runtime = NativeAssemblyPlaybackRuntime(context)
    observed = []
    monkeypatch.setattr(
        runtime_module,
        "open_native_assembly_playback",
        lambda received_context, spec: (
            received_context.state.note_structural_change(received_context.document_uid),
            observed.append(("show", received_context, spec)),
            {"operation": "show"},
        )[-1],
    )
    monkeypatch.setattr(
        runtime_module,
        "control_native_assembly_playback",
        lambda received_context, operation, spec: (
            received_context.state.note_structural_change(received_context.document_uid),
            observed.append((operation, received_context, spec)),
            {"operation": operation},
        )[-1],
    )

    open_ticket = context.state.begin_call(
        context.document_uid,
        "assembly.playback",
    )
    opened = runtime.control(
        {
            "operation": "show",
            "simulation": {"object_name": "Simulation"},
        },
        ticket=open_ticket,
    )
    play_ticket = context.state.begin_call(
        context.document_uid,
        "assembly.playback",
    )
    played = runtime.control(
        {
            "operation": "play",
            "playback_id": "b" * 32,
            "direction": "backward",
        },
        ticket=play_ticket,
    )

    assert opened == {"operation": "show"}
    assert observed[0][2].simulation_ref.object_name == "Simulation"
    assert observed[0][2].time_seconds is None
    assert observed[0][2].mode == "hold"
    assert played == {"operation": "play"}
    assert observed[1][2].playback_id == "b" * 32
    assert observed[1][2].direction == "backward"
    assert context.state.current_revision(context.document_uid) == 0

    failure_ticket = context.state.begin_call(
        context.document_uid,
        "assembly.playback",
    )
    with pytest.raises(Exception, match="do not match"):
        runtime.control(
            {
                "operation": "pause",
                "playback_id": "b" * 32,
                "extra": True,
            },
            ticket=failure_ticket,
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
            Service(
                {
                    "active_dialog": True,
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
