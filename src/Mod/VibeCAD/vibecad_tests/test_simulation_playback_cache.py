# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the production joint callbacks without requiring a Coin context."""
import ast
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import threading
from concurrent.futures import Future
import pytest


def _method(class_name, method_name, filename='JointObject.py', **globals_):
    source = Path(__file__).resolve().parents[2] / 'Assembly' / filename
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = dict(globals_)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in tuple(self.callbacks):
            callback(*args)


@pytest.mark.parametrize('playback_only', [False, True])
def test_generation_keeps_full_solver_and_persisted_playback_entries_distinct(playback_only):
    calls = []
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, generation_state='ready',
        animationTimer=SimpleNamespace(stop=lambda: None),
        _cancelPendingFrame=lambda: None, _setGenerationState=lambda *args: calls.append(args),
        presentation=None, playback_only=playback_only, simFeaturePy=object(),
        assembly=SimpleNamespace(
            startSimulation=lambda sim: calls.append(('full', sim)) or 11,
            startSimulationPlayback=lambda sim: calls.append(('playback', sim)) or 12),
        generationTimer=SimpleNamespace(start=lambda: calls.append('timer')),
        generationFinished=_Signal())
    _method('TaskAssemblyCreateSimulation', 'runKinematicsAsync', 'CommandCreateSimulation.py')(panel)
    assert calls == [('running',), ('playback' if playback_only else 'full', panel.simFeaturePy), 'timer']
    assert panel.generation_request == (12 if playback_only else 11)


@pytest.mark.parametrize('fail', [False, True])
def test_playback_camera_fit_does_not_request_blocking_animation(fail):
    calls = []
    animation = True
    def set_animation(value):
        nonlocal animation
        animation = value
    def fit():
        assert not animation, 'Camera setup requested blocking animation'
        calls.append('fit')
        if fail:
            raise RuntimeError('camera unavailable')
    panel = SimpleNamespace(presentation_visibility=[], requested_camera_method='viewFront',
        view=SimpleNamespace(viewFront=lambda: calls.append(('front', animation)),
                             fitAll=fit, isAnimationEnabled=lambda: animation,
                             setAnimationEnabled=set_animation))
    activate = _method('TaskAssemblyCreateSimulation', '_activatePlaybackPresentation',
                       'CommandCreateSimulation.py')
    if fail:
        with pytest.raises(RuntimeError, match='camera unavailable'):
            activate(panel)
    else:
        activate(panel)
    assert calls == [('front', False), 'fit']
    assert animation, 'Camera setup changed the view animation preference'


def test_joint_marker_updates_use_retained_coin_fields():
    values = []
    class UncachedTransform:
        def __getattr__(self, name):
            raise AssertionError('Repeated Coin field lookup: ' + name)
    class Identity:
        def __mul__(self, placement):
            return placement
    marker = SimpleNamespace(transform=UncachedTransform(),
        _translation=SimpleNamespace(setValue=lambda *args: values.append(('position', args))),
        _rotation=SimpleNamespace(setValue=lambda *args: values.append(('rotation', args))))
    update = _method('SoSwitchMarker', 'set_marker_placement', 'SoSwitchMarker.py',
                     UtilsAssembly=SimpleNamespace(getGlobalPlacement=lambda ref: Identity()))
    placement = SimpleNamespace(Base=SimpleNamespace(x=1, y=2, z=3),
                                Rotation=SimpleNamespace(Q=(0, 0, 0, 1)))
    update(marker, placement, None)
    assert values == [('position', (1, 2, 3)), ('rotation', (0, 0, 0, 1))]


def _playback_controls(**namespace):
    source = Path(__file__).resolve().parents[2] / 'Assembly' / 'CommandCreateSimulation.py'
    names = {'findSimulationPlayback', 'controlSimulationPlaybackAsync', '_simulationTaskDialog',
             '_simulationRequestedFrame', '_simulationFrameTime', 'openSimulationAsync'}
    methods = [node for node in ast.parse(source.read_text(encoding='utf-8')).body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace['Future'] = Future
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    assert names <= namespace.keys(), 'Exact-player reuse helpers are missing'
    return namespace


def test_async_open_completes_when_generation_already_presented_the_last_frame():
    calls = []
    simulation = object()
    panel = SimpleNamespace(
        generationFinished=_Signal(), frameFinished=_Signal(), playbackClosed=_Signal(),
        rejectPlaybackRequested=_Signal(), generation_error='', frame_error='',
        assembly=SimpleNamespace(numberOfFrames=lambda: 6),
        form=SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 5)),
        runKinematicsAsync=lambda: panel.generationFinished.emit(True),
        setFrameValue=lambda frame: calls.append(('frame', frame)),
        animationTimerStartForward=lambda: calls.append('play'),
        graphics_frame_active=True,
    )
    dialog = object()
    controls = _playback_controls(
        openSimulation=lambda *args, **kwargs: panel,
        Gui=SimpleNamespace(
            Control=SimpleNamespace(activeTaskDialog=lambda: dialog)
        ),
    )
    result = controls['openSimulationAsync'](simulation, autoplay=True)
    assert result.done()
    assert result.result() is panel
    assert calls == ['play']


def test_player_control_waits_for_seek_before_play_and_preserves_player_on_failure():
    calls = []
    pending = Future()
    panel = SimpleNamespace(
        simFeaturePy=SimpleNamespace(aTimeStart=SimpleNamespace(Value=0),
                                     cTimeStepOutput=SimpleNamespace(Value=0.1)),
        assembly=SimpleNamespace(numberOfFrames=lambda: 22),
        form=SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 21)),
        requestFrameAsync=lambda frame: calls.append(frame) or pending,
        animationTimerStartForward=lambda: calls.append('play'),
    )
    control = _playback_controls()['controlSimulationPlaybackAsync']
    result = control(panel, time_seconds=1.0, autoplay=True)
    assert calls == [11] and not result.done()
    pending.set_result(11)
    assert result.result() is panel and calls == [11, 'play']
    pending = Future()
    result = control(panel, time_seconds=0.0, autoplay=True)
    pending.set_exception(RuntimeError('edited inputs'))
    with pytest.raises(RuntimeError, match='edited inputs'):
        result.result()
    assert calls == [11, 'play', 1]
    pending = Future()
    result = control(panel, time_seconds=0.2)
    result.cancel()
    assert pending.cancelled()


def test_active_player_lookup_requires_exact_dialog_objects_and_presentation():
    simulation, presentation, hidden, form = object(), object(), object(), object()
    panel = SimpleNamespace(simFeaturePy=simulation, form=form, _ownsLiveTaskContext=lambda: True)
    binding = (lambda: panel, presentation, (hidden,), 'front')
    namespace = _playback_controls(
        _simulationPlayback=binding,
        Gui=SimpleNamespace(Control=SimpleNamespace(activeTaskDialog=lambda:
            SimpleNamespace(getDialogContent=lambda: [form]))))
    find = namespace['findSimulationPlayback']
    args = dict(presentation=presentation, hidden_components=[hidden], camera='front')
    assert find(simulation, **args) is panel
    assert find(object(), **args) is None
    assert find(simulation, **dict(args, camera='top')) is None
    assert find(simulation, **dict(args, hidden_components=[object()])) is None
    panel._ownsLiveTaskContext = lambda: False
    assert find(simulation, **args) is None
    panel._ownsLiveTaskContext = lambda: True
    namespace['Gui'].Control.activeTaskDialog = lambda: SimpleNamespace(getDialogContent=lambda: [
        SimpleNamespace(isAncestorOf=lambda widget: False)])
    assert find(simulation, **args) is None


def test_cancellation_uses_hosted_form_not_transient_native_wrapper():
    calls = []
    form = object()
    panel = SimpleNamespace(form=form, _closing=False)
    current = SimpleNamespace(getDialogContent=lambda: [form],
                              reject=lambda: calls.append('reject'))
    namespace = _playback_controls(
        Gui=SimpleNamespace(Control=SimpleNamespace(activeTaskDialog=lambda: current)))
    cancel = _method('TaskAssemblyCreateSimulation', '_rejectOwnedPlayback',
                     filename='CommandCreateSimulation.py',
                     QtCore=SimpleNamespace(Slot=lambda *a: lambda function: function),
                     _simulationTaskDialog=namespace['_simulationTaskDialog'])
    cancel(panel, object())  # same native task, a different Python wrapper
    assert calls == ['reject']
    current.getDialogContent = lambda: []  # a replacement task must be untouched
    cancel(panel, object())
    assert calls == ['reject']


def _async_open_fixture():
    source = Path(__file__).resolve().parents[2] / 'Assembly' / 'CommandCreateSimulation.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    methods = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name in {'openSimulationAsync', '_simulationFrameTime', '_simulationRequestedFrame'}]
    calls = []
    dialog = object()
    simulation = SimpleNamespace(aTimeStart=SimpleNamespace(Value=0), cTimeStepOutput=SimpleNamespace(Value=0.1))
    panel = SimpleNamespace(
        generationFinished=_Signal(), frameFinished=_Signal(), playbackClosed=_Signal(),
        rejectPlaybackRequested=_Signal(), generation_error='', frame_error='',
        assembly=SimpleNamespace(numberOfFrames=lambda: 22),
        runKinematicsAsync=lambda: calls.append('generate'),
        setFrameValue=lambda frame: calls.append(('frame', frame)),
        onFrameChanged=lambda frame: calls.append(('request', frame)),
        animationTimerStartForward=lambda: calls.append('play'),
        form=SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 21)),
    )
    def reject(owner):
        calls.append(('reject', owner))
        panel.playbackClosed.emit()
    panel.rejectPlaybackRequested.connect(reject)
    namespace = dict(Future=Future, openSimulation=lambda *a, **kw: panel,
                     Gui=SimpleNamespace(Control=SimpleNamespace(activeTaskDialog=lambda: dialog)))
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    assert 'openSimulationAsync' in namespace, 'Nonblocking simulation opener is missing'
    return namespace['openSimulationAsync'], simulation, panel, dialog, calls


def test_async_player_launch_waits_for_exact_displayed_frame():
    launch, simulation, panel, _, calls = _async_open_fixture()
    result = launch(simulation, time_seconds=0.2, autoplay=True)
    assert calls == ['generate'] and not result.done()
    panel.generationFinished.emit(True)
    assert calls == ['generate', ('frame', 3)] and not result.done()
    panel.frameFinished.emit(21, True)
    assert not result.done()
    panel.frameFinished.emit(3, True)
    assert result.result() is panel and calls[-1] == 'play'
    assert not panel.generationFinished.callbacks and not panel.frameFinished.callbacks
    assert not panel.playbackClosed.callbacks


@pytest.mark.parametrize('failure', ['generation', 'frame', 'closed', 'cancel'])
def test_async_player_launch_terminates_on_failure_or_close(failure):
    launch, simulation, panel, dialog, calls = _async_open_fixture()
    result = launch(simulation, time_seconds=0.2)
    if failure == 'generation':
        panel.generation_error = 'invalid motion'
        panel.generationFinished.emit(False)
    elif failure == 'frame':
        panel.generationFinished.emit(True)
        panel.frame_error = 'stale inputs'
        panel.frameFinished.emit(3, False)
    elif failure == 'closed':
        panel.playbackClosed.emit()
    else:
        result.cancel()
    assert result.done()
    if failure != 'cancel':
        with pytest.raises(RuntimeError):
            result.result()
    if failure != 'closed':
        assert ('reject', dialog) in calls
    assert not panel.generationFinished.callbacks and not panel.frameFinished.callbacks
    assert not panel.playbackClosed.callbacks


def test_exact_frame_future_waits_for_adoption_and_reports_interruption():
    calls = []
    class Slider:
        def blockSignals(self, blocked):
            return False
        def setValue(self, frame):
            calls.append(('slider', frame))
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, background_frames=True, frame_request=None,
        assembly=SimpleNamespace(numberOfFrames=lambda: 22), frame_error='',
        frameFinished=_Signal(), playbackClosed=_Signal(), cancelFrameRequested=_Signal(),
        stopAnimation=lambda: calls.append('stop'), _cancelPendingFrame=lambda: None,
        form=SimpleNamespace(frameSlider=Slider()),
    )
    def request(frame):
        calls.append(('request', frame))
        panel.frame_request = 88
    panel.onFrameChanged = request
    seek = _method('TaskAssemblyCreateSimulation', 'requestFrameAsync',
                   filename='CommandCreateSimulation.py', Future=Future)
    result = seek(panel, 3)
    assert not result.done() and calls[-1] == ('request', 3)
    panel.frameFinished.emit(3, True)
    assert result.result() == 3 and not panel.frameFinished.callbacks
    result = seek(panel, 4)
    panel.frame_error = 'superseded'
    panel.frameFinished.emit(4, False)
    with pytest.raises(RuntimeError, match='superseded'):
        result.result()
    result = seek(panel, 5)
    panel.playbackClosed.emit()
    with pytest.raises(RuntimeError, match='closed'):
        result.result()
    with pytest.raises(RuntimeError, match='out of range'):
        seek(panel, 0)
    calls.clear()
    result = seek(panel, 0, include_input=True)
    assert ('slider', 0) not in calls
    panel.frameFinished.emit(0, True)
    assert result.result() == 0


def test_provider_cancellation_reaches_qt_owner_without_cross_thread_cleanup():
    from PySide6 import QtCore

    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    launch, simulation, panel, dialog, _ = _async_open_fixture()
    observed = []
    owner_thread = threading.get_ident()

    class Owner(QtCore.QObject):
        reject = QtCore.Signal(object)

        @QtCore.Slot(object)
        def rejected(self, exact_dialog):
            observed.append((threading.get_ident(), exact_dialog))
            panel.playbackClosed.emit()

    owner = Owner()
    owner.reject.connect(owner.rejected)
    panel.rejectPlaybackRequested = owner.reject
    result = launch(simulation, time_seconds=0.2)
    thread = threading.Thread(target=result.cancel)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and result.cancelled()
    assert observed == [] and panel.playbackClosed.callbacks
    app.processEvents()
    assert observed == [(owner_thread, dialog)]
    assert not panel.playbackClosed.callbacks and not panel.frameFinished.callbacks


def test_joint_owner_is_reused_until_native_structure_changes():
    calls = []
    owner = SimpleNamespace(Name='Assembly', ID=4)
    document = SimpleNamespace(Uid='doc', generation=1)
    document.getObjectStructureGeneration = lambda: document.generation
    document.getObject = lambda name: owner if name == owner.Name else None
    joint = SimpleNamespace(Document=document, ID=8)
    utils = SimpleNamespace(_document_is_open=lambda doc: doc is document,
        findOwningPartOrAssembly=lambda obj: calls.append(obj) or owner)
    get_owner = _method('Joint', 'getAssembly', UtilsAssembly=utils)
    proxy = SimpleNamespace()
    for _ in range(100):
        assert get_owner(proxy, joint) is owner
    assert len(calls) == 1
    document.generation += 1
    assert get_owner(proxy, joint) is owner
    assert len(calls) == 2
    # A replaced target must not be confused with a saved object identity.
    owner.ID += 1
    assert get_owner(proxy, joint) is owner
    assert len(calls) == 3


def test_hidden_joint_markers_are_deferred_and_refreshed_on_show():
    calls = []
    joint = SimpleNamespace(Reference1='one', Reference2='two',
                            Placement1=1, Placement2=2)
    joint.ViewObject = SimpleNamespace(Visibility=False, Object=joint)
    proxy = SimpleNamespace(switch_JCS1=1, switch_JCS2=2,
        redrawJointPlacement=lambda *args: calls.append(args))
    redraw = _method('ViewProviderJoint', 'redrawJointPlacements')
    proxy.redrawJointPlacements = lambda obj: redraw(proxy, obj)
    redraw(proxy, joint)
    assert calls == []
    joint.Placement1 = 3
    joint.ViewObject.Visibility = True
    changed = _method('ViewProviderJoint', 'onChanged')
    changed(proxy, joint.ViewObject, 'Visibility')
    assert calls == [(1, 3, 'one'), (2, 2, 'two')]


def test_hidden_joint_property_changes_do_not_prepare_markers():
    calls = []
    joint = SimpleNamespace(ViewObject=SimpleNamespace(Visibility=False),
                            Placement1=1, Reference1='one')
    proxy = SimpleNamespace(switch_JCS1=1, redrawJointPlacement=lambda *args: calls.append(args))
    update = _method('ViewProviderJoint', 'updateData')
    update(proxy, joint, 'Placement1')
    assert calls == []


@pytest.mark.parametrize('prop', ['Label', 'Visibility', 'Placement1', 'Placement2',
    'VibeCADVibeScriptDefinition', 'VibeCADProgramRevision', 'VibeCADTimelineRole',
    'VibeCADTimelineOwner', '_GroupTouched'])
def test_unhandled_joint_changes_do_not_resolve_history_or_assembly(prop):
    changed = _method('Joint', 'onChanged',
        App=SimpleNamespace(isRestoring=lambda: pytest.fail('Unrelated joint change queried application')))
    changed(object(), object(), prop)


@pytest.mark.parametrize('prop', ['Label', 'Visibility', 'Reference1', 'JointType',
                                  'VibeCADVibeScriptDefinition'])
def test_unhandled_joint_view_changes_do_not_resolve_gui_objects(prop):
    update = _method('ViewProviderJoint', 'updateData')
    update(object(), object(), prop)


@pytest.mark.parametrize('prop,joint_type,expected', [
    ('JointType', 'Angle', []),
    ('Reference1', 'Fixed', ['recompute']),
    ('Reference2', 'Fixed', ['recompute']),
    ('Offset1', 'Fixed', ['owner', 'placements', 'solve']),
    ('Offset2', 'Fixed', ['owner', 'placements', 'solve']),
    ('Distance', 'Distance', ['owner', 'solve']),
    ('Angle', 'Angle', ['owner', 'prevent_parallel', 'solve']),
])
def test_handled_joint_changes_preserve_edit_actions(prop, joint_type, expected):
    calls = []
    joint = SimpleNamespace(Document=SimpleNamespace(Transacting=False),
        Reference1=object(), Reference2=object(), JointType=joint_type, Angle=30,
        Label='Fixed joint', recompute=lambda: calls.append('recompute'))
    assembly = SimpleNamespace(Type='Assembly')
    proxy = SimpleNamespace(
        getAssembly=lambda obj: calls.append('owner') or assembly,
        updateJCSPlacements=lambda obj: calls.append('placements'),
        preventParallel=lambda obj: calls.append('prevent_parallel'))
    changed = _method('Joint', 'onChanged',
        App=SimpleNamespace(isRestoring=lambda: False),
        _jointInteractionUsable=lambda obj: True,
        JointTypes=['Fixed', 'Angle', 'Distance'],
        TranslatedJointTypes=['Fixed', 'Angle', 'Distance'], JointUsingPreSolve=[],
        solveIfAllowed=lambda obj: calls.append('solve'))
    changed(proxy, joint, prop)
    assert calls == expected
    assert joint.Label == ('Angle joint' if prop == 'JointType' else 'Fixed joint')


def _presentation_scope():
    source = Path(__file__).resolve().parents[2] / 'Assembly' / 'UtilsAssembly.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    names = {'presentationPlacementChanges', 'isPresentationPlacementChange'}
    methods = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(methods) == 2, 'Shared transient placement scope is missing'
    namespace = dict(contextmanager=contextmanager, _presentation_changes=threading.local())
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['presentationPlacementChanges'], namespace['isPresentationPlacementChange']


def test_transient_placement_scope_is_exact_nested_and_thread_local():
    scope, is_transient = _presentation_scope()
    document = object()
    part = SimpleNamespace(Document=document, ID=4)
    other = SimpleNamespace(Document=document, ID=5)
    foreign = SimpleNamespace(Document=object(), ID=4)
    assert not is_transient(part, 'Placement')
    observed = []
    with scope(document, frozenset({4})):
        assert is_transient(part, 'Placement')
        assert is_transient(part, 'LinkPlacement')
        assert not is_transient(part, 'Shape')
        assert not is_transient(other, 'Placement')
        assert not is_transient(foreign, 'Placement')
        thread = threading.Thread(target=lambda: observed.append(is_transient(part, 'Placement')))
        thread.start()
        thread.join()
        try:
            with scope(document, frozenset({5})):
                assert is_transient(other, 'Placement')
                assert not is_transient(part, 'Placement')
                raise ValueError('cancel frame')
        except ValueError:
            pass
        assert is_transient(part, 'Placement')
    assert observed == [False]
    assert not is_transient(part, 'Placement')


def test_transient_placement_scope_restores_native_flag_after_exception():
    scope, _ = _presentation_scope()
    assembly = SimpleNamespace(active=False)
    def set_active(active):
        old, assembly.active = assembly.active, active
        return old
    assembly.setSimulationPresentation = set_active
    try:
        with scope(object(), frozenset(), assembly):
            assert assembly.active
            with scope(object(), frozenset(), assembly):
                raise ValueError('interrupted presentation')
    except ValueError:
        pass
    assert not assembly.active


def test_generate_button_submits_without_waiting_for_solver():
    calls = []
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, generation_state='idle',
        _cancelPendingFrame=lambda: None,
        presentation=None, simFeaturePy=object(), playback_only=False,
        animationTimer=SimpleNamespace(stop=lambda: calls.append('stop playback')),
        generationTimer=SimpleNamespace(start=lambda: calls.append('watch completion')),
        assembly=SimpleNamespace(startSimulation=lambda sim: calls.append('submit') or 42),
        _setGenerationState=lambda state, error='': calls.append(state),
    )
    generate = _method('TaskAssemblyCreateSimulation', 'runKinematicsAsync',
                       filename='CommandCreateSimulation.py')
    generate(panel)
    assert calls == ['stop playback', 'running', 'submit', 'watch completion']
    assert panel.generation_request == 42


def test_player_cancellation_addresses_only_its_own_request():
    calls = []
    assembly = SimpleNamespace(ID=7, cancelSimulation=lambda request: calls.append(request))
    document = SimpleNamespace(getObject=lambda name: assembly)
    panel = SimpleNamespace(
        generation_request=42, generation_state='running', doc=document,
        assembly_identity=('Assembly', 7, assembly), form=None,
        generationTimer=SimpleNamespace(stop=lambda: None),
        generationFinished=SimpleNamespace(emit=lambda result: None),
    )
    cancel = _method('TaskAssemblyCreateSimulation', 'cancelGeneration',
                     filename='CommandCreateSimulation.py',
                     UtilsAssembly=SimpleNamespace(_document_is_open=lambda doc: True))
    cancel(panel)
    assert calls == [42]
    assert panel.generation_request is None


def test_save_resume_uses_one_transient_frame_application_even_if_slider_changes():
    calls = []
    class Slider:
        blocked = False
        def blockSignals(self, blocked):
            old, self.blocked = self.blocked, blocked
            return old
        def setValue(self, frame):
            if not self.blocked:
                calls.append(('slider callback', frame))
    document = object()
    panel = SimpleNamespace(
        doc=document, _save_playback_state=(3, False, 1),
        _ownsLiveTaskContext=lambda: True, _activatePlaybackPresentation=lambda: None,
        assembly=SimpleNamespace(numberOfFrames=lambda: 5),
        onFrameChanged=lambda frame: calls.append(('transient frame', frame)),
        form=SimpleNamespace(frameSlider=Slider()), gui_doc=SimpleNamespace(Modified=True),
    )
    resume = _method('TaskAssemblyCreateSimulation', 'slotFinishSaveDocument',
                     filename='CommandCreateSimulation.py')
    resume(panel, document, 'unused')
    assert calls == [('transient frame', 3)]
    assert not panel.form.frameSlider.blocked
    assert not panel.gui_doc.Modified


def test_interactive_frame_request_does_not_apply_or_wait():
    calls = []
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, background_frames=True,
        presentation=object(),
        _cancelPendingFrame=lambda: None,
        assembly=SimpleNamespace(requestSimulationFrame=lambda frame: calls.append(('request', frame)) or 71),
        frameTimer=SimpleNamespace(start=lambda: calls.append(('watch',))),
    )
    change = _method('TaskAssemblyCreateSimulation', 'onFrameChanged', filename='CommandCreateSimulation.py')
    change(panel, 12)
    assert calls == [('request', 12), ('watch',)]
    assert panel.frame_request == 71 and panel.requested_frame == 12


def test_interactive_frame_request_applies_cached_graphics_without_worker_polling():
    calls = []
    placement = SimpleNamespace(toMatrix=lambda: 'frame matrix')
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True,
        background_frames=True,
        presentation=None,
        assembly=SimpleNamespace(
            getSimulationFrame=lambda frame: [('AnimatedLink', placement)]
        ),
        gui_doc=SimpleNamespace(
            setPos=lambda name, matrix: calls.append(('transform', name, matrix))
        ),
        frameTimer=SimpleNamespace(start=lambda: calls.append('poll')),
        _cancelPendingFrame=lambda: calls.append('cancel'),
        _showFrameStatus=lambda frame: calls.append(('frame', frame)),
        frameFinished=SimpleNamespace(
            emit=lambda frame, ok: calls.append(('finished', frame, ok))
        ),
        _frameFailed=lambda error: calls.append(('failed', str(error))),
        frame_request=None,
    )
    change = _method(
        'TaskAssemblyCreateSimulation',
        'onFrameChanged',
        filename='CommandCreateSimulation.py',
    )
    change(panel, 12)
    assert calls == [
        ('transform', 'AnimatedLink', 'frame matrix'),
        ('frame', 12),
        ('finished', 12, True),
    ]
    assert panel.graphics_frame_active
    assert panel.frame_request is None


def test_frame_completion_updates_presentation_only_when_ready():
    calls = []
    scope, _ = _presentation_scope()
    ready = [False, True]
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, frame_request=71, requested_frame=12,
        doc=object(), playback_part_ids=frozenset(),
        presentation=object(),
        assembly=SimpleNamespace(finishSimulationFrame=lambda token: ready.pop(0),
                                 setSimulationPresentation=lambda active: False),
        frameTimer=SimpleNamespace(stop=lambda: calls.append('stop')),
        _applyPlaybackPresentation=lambda: calls.append('presentation'),
        _showFrameStatus=lambda frame: calls.append(('frame', frame)),
        frameFinished=SimpleNamespace(emit=lambda frame, ok: calls.append(('finished', frame, ok))),
    )
    finish = _method('TaskAssemblyCreateSimulation', '_finishFrame', filename='CommandCreateSimulation.py',
                     UtilsAssembly=SimpleNamespace(presentationPlacementChanges=scope))
    finish(panel)
    assert calls == [] and panel.frame_request == 71
    finish(panel)
    assert calls == ['presentation', 'stop', ('frame', 12), ('finished', 12, True)]
    assert panel.frame_request is None


def test_interactive_frame_completion_updates_only_graphics_when_ready():
    calls = []
    scope, _ = _presentation_scope()
    ready = [None, [('AnimatedLink', SimpleNamespace(toMatrix=lambda: 'frame matrix'))]]
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, frame_request=71, requested_frame=12,
        presentation=None, doc=object(), playback_part_ids=frozenset(),
        assembly=SimpleNamespace(
            takeSimulationFrame=lambda token: ready.pop(0),
            finishSimulationFrame=lambda token: calls.append('document placements') or False,
            setSimulationPresentation=lambda active: False,
        ),
        gui_doc=SimpleNamespace(
            setPos=lambda name, matrix: calls.append(('transform', name, matrix))
        ),
        frameTimer=SimpleNamespace(stop=lambda: calls.append('stop')),
        _showFrameStatus=lambda frame: calls.append(('frame', frame)),
        frameFinished=SimpleNamespace(
            emit=lambda frame, ok: calls.append(('finished', frame, ok))
        ),
        _frameFailed=lambda error: calls.append(('failed', str(error))),
    )
    finish = _method(
        'TaskAssemblyCreateSimulation',
        '_finishFrame',
        filename='CommandCreateSimulation.py',
        UtilsAssembly=SimpleNamespace(presentationPlacementChanges=scope),
    )
    finish(panel)
    assert calls == [] and panel.frame_request == 71
    finish(panel)
    assert calls == [
        ('transform', 'AnimatedLink', 'frame matrix'),
        'stop',
        ('frame', 12),
        ('finished', 12, True),
    ]
    assert panel.frame_request is None


def test_graphics_only_frame_restores_the_live_document_placement():
    calls = []
    placement = SimpleNamespace(toMatrix=lambda: 'document matrix')
    component = SimpleNamespace(Name='AnimatedLink', ID=9, Placement=placement)
    document = SimpleNamespace(getObject=lambda name: component)
    panel = SimpleNamespace(
        graphics_frame_active=True,
        doc=document,
        gui_doc=SimpleNamespace(
            setPos=lambda name, matrix: calls.append((name, matrix))
        ),
        initialPlcs=SimpleNamespace(
            parts=[('AnimatedLink', 9, component, object())]
        ),
    )
    restore = _method(
        'TaskAssemblyCreateSimulation',
        '_restoreSimulationGraphics',
        filename='CommandCreateSimulation.py',
        UtilsAssembly=SimpleNamespace(_document_is_open=lambda doc: True),
    )
    restore(panel)
    assert calls == [('AnimatedLink', 'document matrix')]
    assert not panel.graphics_frame_active


def test_playback_drops_ticks_while_a_frame_is_pending():
    calls = []
    panel = SimpleNamespace(
        form=object(), _ownsLiveTaskContext=lambda: True,
        animationTimer=SimpleNamespace(
            stop=lambda: calls.append('stop'),
            start=lambda delay: calls.append(('start', delay)),
        ),
        startFrm=1, endFrm=21, currentFrm=1, direction=1,
        startTime=0.0, lastFrameTime=0.0, deltaTime=0.1,
        background_frames=True, frame_request=71,
        setFrameValue=lambda frame: calls.append(('request', frame)),
    )
    play = _method(
        'TaskAssemblyCreateSimulation',
        'playAnimation',
        filename='CommandCreateSimulation.py',
        time=SimpleNamespace(time=lambda: 0.35, perf_counter=lambda: 1.0),
    )
    play(panel)
    assert calls == []


def test_playback_advances_one_frame_without_elapsed_time_catch_up():
    calls = []
    class Slider:
        def value(self):
            return 3
        def maximum(self):
            return 20
    panel = SimpleNamespace(
        form=SimpleNamespace(frameSlider=Slider()), _ownsLiveTaskContext=lambda: True,
        animationTimer=SimpleNamespace(
            stop=lambda: calls.append('stop'),
            start=lambda delay: calls.append(('start', delay)),
        ),
        startFrm=1, endFrm=20, currentFrm=1, direction=1,
        startTime=0.0, lastFrameTime=0.0, deltaTime=0.1,
        background_frames=True, frame_request=None,
        setFrameValue=lambda frame: calls.append(frame),
    )
    play = _method(
        'TaskAssemblyCreateSimulation',
        'playAnimation',
        filename='CommandCreateSimulation.py',
        time=SimpleNamespace(time=lambda: 1000.0, perf_counter=lambda: 1.0),
    )
    play(panel)
    assert calls == [4]


def test_playback_pacing_drops_an_early_catch_up_tick():
    calls = []
    clock = iter([10.005, 10.017, 10.021])
    panel = SimpleNamespace(
        form=SimpleNamespace(frameSlider=SimpleNamespace(value=lambda: 3)),
        _ownsLiveTaskContext=lambda: True,
        animationTimer=SimpleNamespace(
            stop=lambda: calls.append('stop'),
            start=lambda delay: calls.append(('start', delay)),
        ),
        startFrm=1, endFrm=20, currentFrm=1, direction=1,
        deltaTime=1.0 / 60.0, lastFrameTime=10.0,
        background_frames=True, frame_request=None,
        setFrameValue=lambda frame: calls.append(('frame', frame)),
    )
    play = _method(
        'TaskAssemblyCreateSimulation',
        'playAnimation',
        filename='CommandCreateSimulation.py',
        time=SimpleNamespace(perf_counter=lambda: next(clock)),
    )
    play(panel)
    assert calls == []
    play(panel)
    assert calls == [('frame', 4)]
    assert panel.lastFrameTime == 10.021


def test_regeneration_requests_last_frame_even_when_slider_did_not_move():
    calls = []
    class Slider:
        blocked = False
        def blockSignals(self, active):
            previous, self.blocked = self.blocked, active
            return previous
        def setMaximum(self, value):
            assert self.blocked
        def setValue(self, value):
            assert self.blocked
    slider = Slider()
    panel = SimpleNamespace(
        _ownsLiveTaskContext=lambda: True, generation_request=3, frame_error='',
        assembly=SimpleNamespace(finishSimulation=lambda token: True, numberOfFrames=lambda: 22),
        generationTimer=SimpleNamespace(stop=lambda: None),
        form=SimpleNamespace(frameSlider=slider, groupBox_player=SimpleNamespace(show=lambda: None),
                             SaveAnimationButton=SimpleNamespace(show=lambda: None)),
        onFrameChanged=lambda frame: calls.append(frame),
        _setGenerationState=lambda *args: None,
        generationFinished=SimpleNamespace(emit=lambda result: calls.append(result)),
    )
    finish = _method('TaskAssemblyCreateSimulation', '_finishKinematicsAsync', filename='CommandCreateSimulation.py')
    finish(panel)
    assert calls == [21, True] and panel.background_frames
    assert not slider.blocked
