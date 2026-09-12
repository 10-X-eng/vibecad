# SPDX-License-Identifier: LGPL-2.1-or-later
"""Both provider-facing routes retain completed-result semantics."""
from concurrent.futures import Future
import sys
from types import SimpleNamespace
import pytest

from tool_impl.service import assembly_play_simulation as tool


def test_service_playback_returns_completion_not_a_pending_frame(monkeypatch):
    pending = Future()
    document = SimpleNamespace(Uid='doc')
    assembly = SimpleNamespace(Name='Assembly', numberOfFrames=lambda: 22)
    obj = SimpleNamespace(
        VibeCADVibeScriptOutputType='simulation', Document=document,
        Proxy=SimpleNamespace(getAssembly=lambda obj: assembly),
    )
    calls = []
    monkeypatch.setitem(sys.modules, 'FreeCAD', SimpleNamespace(GuiUp=True))
    monkeypatch.setitem(sys.modules, 'FreeCADGui', SimpleNamespace(
        Control=SimpleNamespace(activeTaskDialog=lambda: None)))
    monkeypatch.setitem(sys.modules, 'VibeCADDocumentReferences', SimpleNamespace(
        resolve_reference_target=lambda *a, **kw: obj))
    monkeypatch.setitem(sys.modules, 'CommandCreateSimulation', SimpleNamespace(
        _simulationFrameTime=lambda obj, frame: 0.2,
        openSimulation=lambda *a, **kw: calls.append('synchronous'),
        openSimulationAsync=lambda *a, **kw: pending))
    result = tool.run_async(SimpleNamespace(_active_document=lambda: document),
                            {'document_uid': 'doc', 'object_name': 'Simulation'},
                            time_seconds=0.2, autoplay=False)
    assert isinstance(result, Future) and not result.done() and calls == []
    pending.set_result(SimpleNamespace(form=SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 3))))
    payload = result.result()
    assert payload['ok'] and payload['frame'] == 3 and payload['time_seconds'] == 0.2
    assert payload['assembly'] == {'document_uid': 'doc', 'object_name': 'Assembly'}


def test_session_validates_arguments_before_starting_async_playback(monkeypatch):
    from VibeCADSession import _start_service_tool
    from VibeCADTools import ToolRegistry, ToolArgumentValidationError

    registry = ToolRegistry()
    registry.register_spec(tool.TOOL_SPEC, lambda **args: {'legacy': True})
    service = SimpleNamespace(registry=registry)
    calls = []
    pending = Future()
    monkeypatch.setattr(tool, 'run_async', lambda *args, **kwargs: calls.append(kwargs) or pending)
    with pytest.raises(ToolArgumentValidationError):
        _start_service_tool(service, 'assembly.play_simulation', {'simulation': 'invalid'}, asynchronous=True)
    assert calls == []
    arguments = {'simulation': {'document_uid': 'doc', 'object_name': 'Simulation'}}
    assert _start_service_tool(service, 'assembly.play_simulation', arguments, asynchronous=True) is pending
    assert calls == [arguments]
    assert _start_service_tool(service, 'assembly.play_simulation', arguments, asynchronous=False) == {'legacy': True}


@pytest.mark.parametrize('matching_player', [True, False])
def test_service_seeks_exact_active_player_without_reopening(monkeypatch, matching_player):
    document = SimpleNamespace(Uid='doc')
    obj = SimpleNamespace(VibeCADVibeScriptOutputType='simulation', Document=document)
    panel = object()
    pending = Future()
    calls = []
    monkeypatch.setitem(sys.modules, 'FreeCAD', SimpleNamespace(GuiUp=True))
    monkeypatch.setitem(sys.modules, 'FreeCADGui', SimpleNamespace(
        Control=SimpleNamespace(activeTaskDialog=lambda: object())))
    monkeypatch.setitem(sys.modules, 'VibeCADDocumentReferences', SimpleNamespace(
        resolve_reference_target=lambda *a, **kw: obj))
    monkeypatch.setitem(sys.modules, 'CommandCreateSimulation', SimpleNamespace(
        findSimulationPlayback=lambda *a, **kw: panel if matching_player else None,
        controlSimulationPlaybackAsync=lambda *a, **kw: calls.append((a, kw)) or pending,
        openSimulation=lambda *a, **kw: pytest.fail('Must not reopen an active task'),
        openSimulationAsync=lambda *a, **kw: pytest.fail('Must not regenerate'),
    ))
    monkeypatch.setattr(tool, '_playback_result', lambda *args: {'ok': True, 'frame': 11})
    result = tool.run_async(SimpleNamespace(_active_document=lambda: document),
                            {'document_uid': 'doc', 'object_name': 'Simulation'},
                            time_seconds=1.0, autoplay=False)
    if matching_player:
        assert isinstance(result, Future) and not result.done()
        assert calls == [((panel,), {'autoplay': False, 'time_seconds': 1.0})]
        pending.set_result(panel)
        assert result.result() == {'ok': True, 'frame': 11}
    else:
        assert result['failure_code'] == 'NATIVE_TASK_ACTIVE'
        assert calls == []


@pytest.mark.parametrize('closes', [True, False])
def test_stop_checks_actual_task_contents_after_reject(monkeypatch, closes):
    from tool_impl.service import assembly_stop_simulation as stop
    widget = SimpleNamespace(property=lambda key: {
        'vibecadSavedAssemblySimulationPlayback': True,
        'vibecadSimulationDocumentUid': 'doc',
        'vibecadSimulationObjectName': 'Simulation',
    }.get(key))
    active = True
    def reject():
        nonlocal active
        active = not closes
    def dialog():
        return SimpleNamespace(getDialogContent=lambda: [widget], reject=reject) if active else None
    monkeypatch.setitem(sys.modules, 'FreeCAD', SimpleNamespace(GuiUp=True))
    monkeypatch.setitem(sys.modules, 'FreeCADGui', SimpleNamespace(
        Control=SimpleNamespace(activeTaskDialog=dialog), updateGui=lambda: None))
    result = stop.run(None)
    assert result['ok'] is closes
    if not closes:
        assert result['failure_code'] == 'SIMULATION_PLAYBACK_REMAINED_OPEN'
