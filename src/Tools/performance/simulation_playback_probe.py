# SPDX-License-Identifier: LGPL-2.1-or-later
"""Profile real native simulation playback in an isolated packaged GUI copy.

Use run-packaged-edit-check.ps1 with a disposable document. Never inject this
probe into a user's session. It opens the largest authenticated simulation,
profiles generation and representative frame changes, and never saves.
Set VIBECAD_SIMULATION_PLAYBACK_ASYNC=1 to measure the nonblocking player;
leave it unset when collecting a comparable legacy baseline.
Set VIBECAD_SIMULATION_EXPORT=1 with the async player to exercise its real
animation-export entry point, native capture and persistent encoding worker.
Set VIBECAD_SIMULATION_AI_PLAYBACK=1 with the async player to use the actual
AI service entry, show its assembly, and exercise Play after exact seeks.
VIBECAD_SIMULATION_READ_NAMES optionally supplies comma-separated object names
for geometry-capture calls between generation and playback, without saving.
VIBECAD_SIMULATION_LIFECYCLE=1 checks hidden joint markers, restored marker
poses, and closing with a pending frame on the same real model.
VIBECAD_SIMULATION_EXPORT_CANCEL=1 with export enabled cancels at frame
preparation, native PNG capture and encoding, then retries a complete export.
VIBECAD_SIMULATION_EXPORT_CLOSE=task or document closes that owner while native
PNG capture is pending, verifying cancellation and cleanup after teardown.
VIBECAD_SIMULATION_REFERENCE_AUDIT optionally supplies a joint-name prefix:
audit those references after opening and exit without generating or playing.
VIBECAD_SIMULATION_PERSISTED_REOPEN=1 closes/reopens the disposable document
after sampling generated poses, then compares all sampled component poses from
persisted playback. Native logs distinguish a cache hit from another solve.
"""
import cProfile
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback
from concurrent.futures import Future
from types import SimpleNamespace
import psutil

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT'])
source = Path(os.environ['VIBECAD_ROUNDTRIP_COPY'])
if source.parent != output.parent or source.name != 'probe-document.FCStd' or App.listDocuments():
    raise RuntimeError('Use an isolated process and disposable probe-document.FCStd')
App.setLogLevel('Assembly', 'Log')

started = time.monotonic()
process = psutil.Process()
state = 'startup'
quiet_since = None
panel = None
frames = []
asynchronous = os.environ.get('VIBECAD_SIMULATION_PLAYBACK_ASYNC') == '1'
export_animation = os.environ.get('VIBECAD_SIMULATION_EXPORT') == '1'
if export_animation:
    # Reject an incomplete diagnostic runtime before opening the large model.
    # Export admission uses psutil; verification decodes the resulting GIF.
    importlib.import_module('psutil')
    importlib.import_module('PIL.Image')
export_cancel_cases = (['frame', 'png', 'encoding']
    if os.environ.get('VIBECAD_SIMULATION_EXPORT_CANCEL') == '1' else [])
export_close = os.environ.get('VIBECAD_SIMULATION_EXPORT_CLOSE', '')
if export_close not in {'', 'task', 'document'}:
    raise RuntimeError('Export close must select task or document')
if export_close:
    export_cancel_cases = ['png']
if export_cancel_cases and not export_animation:
    raise RuntimeError('Export cancellation checks require animation export')
export_case = None
export_controller = None
ai_playback = os.environ.get('VIBECAD_SIMULATION_AI_PLAYBACK') == '1'
ai_reseek = os.environ.get('VIBECAD_SIMULATION_AI_RESEEK') == '1'
lifecycle = os.environ.get('VIBECAD_SIMULATION_LIFECYCLE') == '1'
persisted_reopen = os.environ.get('VIBECAD_SIMULATION_PERSISTED_REOPEN') == '1'
reopened = False
expected_reopen_poses = {}
requested_frame = None
if persisted_reopen and not asynchronous:
    raise RuntimeError('Persisted playback verification requires asynchronous playback')
if lifecycle and not asynchronous:
    raise RuntimeError('The lifecycle probe requires asynchronous playback')
joint_visibility = []
marker_updates = []
original_marker_update = None
play_started = None
captured_panel = None
pending = None
last_pulse = time.monotonic()
report = {'pid': os.getpid(), 'events': [], 'ok': False,
          'asynchronous': asynchronous, 'heartbeat_count': 0, 'max_heartbeat_gap_seconds': 0.0}
report['native_build'] = App.ConfigGet('BuildRevisionHash')
report['reference_audit_only'] = os.environ.get('VIBECAD_SIMULATION_REFERENCE_AUDIT', '')
report['independent_input'] = []
sys.path.insert(0, str(Path(__file__).resolve().parent))
from window_input_probe import WindowInputProbe
from async_report_writer import AsyncReportWriter
report_writer = AsyncReportWriter(output)
report_written = None
input_probe = WindowInputProbe(Gui.getMainWindow(), lambda milliseconds:
    report['independent_input'].append(dict(
        phase=state, seconds=time.monotonic() - started, milliseconds=milliseconds)))


class PlaybackChanges:
    def slotChangedObject(self, obj, prop):
        if state not in {'async_generate', 'frame', 'play', 'playing'}:
            return
        if prop in {'Placement', 'LinkPlacement', 'Label', 'Visibility'}:
            return
        # Record potential invalidations without writing a file per notification.
        report.setdefault('non_pose_changes', []).append({
            'seconds': time.monotonic() - started, 'stage': state,
            'object': obj.Name, 'property': prop})


changes = PlaybackChanges()
App.addDocumentObserver(changes)


def open_ai_player(simulation):
    import CommandCreateSimulation
    from VibeCADCore import get_service
    from tool_impl.service import assembly_play_simulation
    original = CommandCreateSimulation.openSimulationAsync

    def observe(*args, **kwargs):
        future = original(*args, **kwargs)
        def capture(done):
            global captured_panel
            if not done.cancelled() and done.exception() is None:
                captured_panel = done.result()
        future.add_done_callback(capture)
        return future

    CommandCreateSimulation.openSimulationAsync = observe
    try:
        return assembly_play_simulation.run_async(
            get_service(),
            simulation={'document_uid': str(simulation.Document.Uid),
                        'object_name': simulation.Name},
            autoplay=False, time_seconds=0)
    finally:
        CommandCreateSimulation.openSimulationAsync = original


def heartbeat():
    global last_pulse
    now = time.monotonic()
    report['max_heartbeat_gap_seconds'] = max(report['max_heartbeat_gap_seconds'], now - last_pulse)
    report['heartbeat_count'] += 1
    last_pulse = now


def watch(name, future):
    before = time.perf_counter()
    cpu = time.process_time()
    def completed(done):
        event(name + '_completed', elapsed_seconds=time.perf_counter() - before,
              cpu_seconds=time.process_time() - cpu, cancelled=done.cancelled())
    future.add_done_callback(completed)
    return future


def event(name, **values):
    global report_written
    entry = dict(event=name, seconds=round(time.monotonic() - started, 6),
                 process_cpu_seconds=time.process_time(), rss_bytes=process.memory_info().rss,
                 **values)
    report['events'].append(entry)
    print('SIMULATION_PLAYBACK ' + json.dumps(entry), flush=True)
    snapshot = {key: list(value) if isinstance(value, list) else value
                for key, value in report.copy().items()}
    report_written = report_writer.submit(snapshot)


def profile(name, function):
    profiler = cProfile.Profile()
    before = time.perf_counter()
    cpu = time.process_time()
    event(name + '_started')
    try:
        return profiler.runcall(function)
    finally:
        profiler.dump_stats(str(output.with_name(name + '.pstats')))
        event(name + '_returned', elapsed_seconds=time.perf_counter() - before,
              cpu_seconds=time.process_time() - cpu)


def tick():
    global state, quiet_since, panel, frames, pending, play_started
    global joint_visibility, original_marker_update
    global export_case, export_controller
    global reopened, requested_frame
    try:
        input_probe.check()
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal is not None and not (
            state == 'export_wait' and isinstance(modal, QtWidgets.QProgressDialog)
        ):
            raise RuntimeError('Unexpected modal dialog in isolated playback probe')
        if state == 'startup':
            # Explicit Python-only candidate injection in this disposable process.
            # Native verification must still use a complete matching package.
            candidate = os.environ.get('VIBECAD_SIMULATION_SOURCE')
            if candidate:
                import sys
                root = Path(candidate).resolve()
                for name, relative in (
                    ('SoSwitchMarker', 'Assembly/SoSwitchMarker.py'),
                    ('CommandCreateSimulation', 'Assembly/CommandCreateSimulation.py'),
                    ('tool_impl.service.assembly_play_simulation',
                     'VibeCAD/tool_impl/service/assembly_play_simulation.py'),
                ):
                    spec = importlib.util.spec_from_file_location(name, root / relative)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    spec.loader.exec_module(module)
                    if '.' in name:
                        package, attribute = name.rsplit('.', 1)
                        setattr(importlib.import_module(package), attribute, module)
                if 'JointObject' in sys.modules:
                    sys.modules['JointObject'].SoSwitchMarker = sys.modules['SoSwitchMarker'].SoSwitchMarker
                event('python_candidate_loaded')
            state = 'opening'
            QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(),
                                             QtGui.QFileOpenEvent(str(source)))
            event('open_requested')
        elif state == 'opening':
            doc = App.ActiveDocument
            if doc is None or any((doc.Restoring, doc.Recomputing, doc.RecomputePending,
                                  doc.CooperativeMutationActive, doc.PresentationUpdateActive)):
                quiet_since = None
                return
            if quiet_since is None:
                quiet_since = time.monotonic()
                return
            if time.monotonic() - quiet_since < 2:
                return
            if report['reference_audit_only']:
                entries = []
                for joint in doc.Objects:
                    if not joint.Name.startswith(report['reference_audit_only']):
                        continue
                    entry = {'joint': joint.Name, 'state': list(joint.State),
                             'active': doc.isObjectUsableAtCurrentTimelinePosition(joint)}
                    for name in ('Reference1', 'Reference2'):
                        reference = getattr(joint, name, None)
                        target = reference[0] if reference else None
                        linked = target.getLinkedObject(True) if target else None
                        entry[name] = dict(target=target.Name if target else None,
                            subs=list(reference[1]) if reference else [],
                            active=target.Document.isObjectUsableAtCurrentTimelinePosition(target) if target else False,
                            linked=linked.Name if linked else None,
                            linked_active=linked.Document.isObjectUsableAtCurrentTimelinePosition(linked) if linked else False)
                    entries.append(entry)
                assert entries, 'Reference audit did not match any joints'
                event('reference_audit', entries=entries)
                report['ok'] = True
                event('complete')
                state = 'reporting'
                return
            import CommandCreateSimulation
            candidates = [obj for obj in doc.Objects
                          if int(getattr(obj, 'VibeCADPoseCount', 0)) > 0]
            if not candidates:
                raise RuntimeError('No authenticated native simulation in test document')
            simulation = max(candidates, key=lambda obj: int(obj.VibeCADPoseCount))
            event('opened', objects=len(doc.Objects), simulation=simulation.Name,
                  poses=int(simulation.VibeCADPoseCount))
            if ai_playback or export_animation:
                profile('setup_assembly_visibility', lambda: setattr(
                    simulation.Proxy.getAssembly(simulation).ViewObject, 'Visibility', True))
                camera_panel = SimpleNamespace(view=Gui.activeDocument().activeView(),
                    presentation_visibility=[], requested_camera_method='viewAxonometric')
                profile('setup_camera_fit', lambda:
                    CommandCreateSimulation.TaskAssemblyCreateSimulation._activatePlaybackPresentation(
                        camera_panel))
            if asynchronous:
                pending = profile('open_player_submit', lambda: watch('generate_and_display',
                    open_ai_player(simulation) if ai_playback else
                    CommandCreateSimulation.openSimulationAsync(simulation)))
                state = 'async_generate'
            else:
                panel = profile('open_player', lambda: CommandCreateSimulation.openSimulation(simulation))
                state = 'generate'
        elif state == 'async_generate':
            if not pending.done():
                return
            outcome = pending.result()
            if ai_playback:
                assert outcome['ok'], outcome
                panel = captured_panel
            else:
                panel = outcome
            pending = None
            count = int(panel.assembly.numberOfFrames())
            assert count >= 2 and panel.background_frames
            if ai_reseek:
                def unexpected_generation():
                    raise AssertionError('Seeking the live player regenerated the simulation')
                panel.runKinematicsAsync = unexpected_generation
            read_names = os.environ.get('VIBECAD_SIMULATION_READ_NAMES', '').split(',')
            if any(read_names):
                from VibeCADCore import get_service
                from VibeCADGeometryInspection import capture_geometry_read, discard_geometry_read
                for name in filter(None, read_names):
                    captured = capture_geometry_read(get_service(), {
                        'reference': {'document_uid': str(panel.doc.Uid), 'object_name': name},
                        'analysis_level': 'topology', 'include_subelements': False})
                    discard_geometry_read(captured)
                event('geometry_captures_completed', count=len(read_names))
            frames = list(dict.fromkeys([1, max(1, count // 2), count - 1]))
            event('generated', frames=count)
            if lifecycle:
                import JointObject
                joint_visibility = [(obj, bool(obj.ViewObject.Visibility))
                    for obj in panel.assembly.Joints
                    if obj.ViewObject is not None
                    and isinstance(obj.ViewObject.Proxy, JointObject.ViewProviderJoint)]
                assert joint_visibility, 'No joint markers exercised'
                for obj, _visible in joint_visibility:
                    obj.ViewObject.Visibility = False
                original_marker_update = JointObject.ViewProviderJoint.setJCSPosition

                def observe_marker(self, *args):
                    marker_updates.append(self.app_obj.Name)
                    return original_marker_update(self, *args)

                JointObject.ViewProviderJoint.setJCSPosition = observe_marker
                event('joints_hidden', count=len(joint_visibility))
            state = 'frame'
        elif state == 'generate':
            state = 'generating'
            profile('generate', panel.runKinematics)
            count = int(panel.assembly.numberOfFrames())
            assert count >= 2
            frames = list(dict.fromkeys([1, max(1, count // 2), count - 1]))
            event('generated', frames=count)
            state = 'frame'
        elif state == 'frame':
            if pending is not None:
                if not pending.done():
                    return
                outcome = pending.result()
                if ai_reseek:
                    assert outcome['ok'], outcome
                    assert int(panel.form.frameSlider.value()) == outcome['frame']
                    event('ai_reseek_verified', frame=outcome['frame'])
                else:
                    assert int(panel.form.frameSlider.value()) == outcome
                if persisted_reopen:
                    poses = {name: App.Placement(part.Placement)
                             for name, _id, part, _initial in panel.initialPlcs.parts}
                    if reopened:
                        expected = expected_reopen_poses[requested_frame]
                        assert poses.keys() == expected.keys(), 'Reopen changed playback bindings'
                        assert all(placement.isSame(expected[name], 1e-9)
                                   for name, placement in poses.items()), 'Cached playback changed poses'
                        event('persisted_poses_verified', frame=requested_frame, parts=len(poses))
                    else:
                        expected_reopen_poses[requested_frame] = poses
                pending = None
            if frames:
                frame = frames.pop(0)
                requested_frame = frame
                if asynchronous:
                    if ai_reseek:
                        from VibeCADCore import get_service
                        from tool_impl.service import assembly_play_simulation
                        from CommandCreateSimulation import _simulationFrameTime
                        pending = assembly_play_simulation.run_async(
                            get_service(), simulation={
                                'document_uid': str(panel.doc.Uid),
                                'object_name': panel.simFeaturePy.Name},
                            time_seconds=_simulationFrameTime(panel.simFeaturePy, frame),
                            autoplay=False)
                        assert isinstance(pending, Future), pending
                        pending = watch('ai_reseek_' + str(frame), pending)
                    else:
                        pending = profile('frame_' + str(frame) + '_submit', lambda: watch(
                            'frame_' + str(frame), panel.requestFrameAsync(frame)))
                else:
                    profile('frame_' + str(frame), lambda: panel.onFrameChanged(frame))
            else:
                if persisted_reopen and not reopened:
                    state = 'reopen_cleanup'
                    if original_marker_update is not None:
                        import JointObject
                        JointObject.ViewProviderJoint.setJCSPosition = original_marker_update
                        original_marker_update = None
                    def restore_visibility():
                        for joint, visible in joint_visibility:
                            joint.ViewObject.Visibility = visible
                    profile('reopen_restore_joint_visibility', restore_visibility)
                    joint_visibility = []
                    marker_updates.clear()
                    document_name = panel.doc.Name
                    profile('reopen_close_player', Gui.Control.activeTaskDialog().reject)
                    panel = None
                    profile('reopen_close_document', lambda: App.closeDocument(document_name))
                    reopened = True
                    quiet_since = None
                    state = 'opening'
                    QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(),
                                                     QtGui.QFileOpenEvent(str(source)))
                    event('persisted_reopen_requested')
                    return
                state = 'markers' if lifecycle else ('play' if ai_playback else (
                    'export' if export_animation and asynchronous else 'close'))
        elif state == 'markers':
            import JointObject
            import UtilsAssembly
            assert not marker_updates, 'Hidden joint graphics were regenerated'
            JointObject.ViewProviderJoint.setJCSPosition = original_marker_update
            original_marker_update = None
            def show_markers():
                for joint, _visible in joint_visibility:
                    joint.ViewObject.Visibility = True
            profile('show_joint_markers', show_markers)
            verification_started = time.perf_counter()
            checked = 0
            for joint, _visible in joint_visibility:
                proxy = joint.ViewObject.Proxy
                assembly = joint.Proxy.getAssembly(joint)
                for index in (1, 2):
                    ref = getattr(joint, 'Reference' + str(index))
                    if not ref:
                        continue
                    expected = assembly.getGlobalPlacement().inverse() * (
                        UtilsAssembly.getGlobalPlacement(ref)
                        * getattr(joint, 'Placement' + str(index)))
                    marker = getattr(proxy, 'switch_JCS' + str(index))
                    actual = App.Placement(
                        App.Vector(*marker.transform.translation.getValue().getValue()),
                        App.Rotation(*marker.transform.rotation.getValue().getValue()))
                    assert actual.isSame(expected, 1e-4), joint.Name
                    checked += 1
            assert checked, 'No current joint marker poses compared'
            event('joint_markers_verified', count=checked,
                  verification_seconds=time.perf_counter() - verification_started)
            state = 'play' if ai_playback else ('export' if export_animation else 'close')
        elif state == 'play':
            panel.animationTimerStartForward()
            play_started = time.monotonic()
            state = 'playing'
            event('play_started')
        elif state == 'playing':
            assert not panel.frame_error, panel.frame_error
            if time.monotonic() - play_started < 3:
                return
            panel.animationTimer.stop()
            event('play_verified', frame=int(panel.form.frameSlider.value()))
            state = 'export' if export_animation else 'close'
        elif state == 'export':
            import CommandCreateSimulation
            dialog = CommandCreateSimulation.QFileDialog
            destination = output.with_name('simulation.gif')
            export_case = export_cancel_cases.pop(0) if export_cancel_cases else None
            if export_case:
                destination.write_bytes(b'previous accepted animation')
            CommandCreateSimulation.QFileDialog = SimpleNamespace(
                getSaveFileName=lambda *args: (str(destination), 'Animated GIF (*.gif)'))
            try:
                profile('export_submit', panel.saveAnimationAsync)
            finally:
                CommandCreateSimulation.QFileDialog = dialog
            controller = panel.animation_export
            export_controller = controller
            assert controller is not None, 'Export did not submit an asynchronous controller'
            pending = watch('animation_export', Future())
            def export_finished(snapshot, future=pending, controller=controller):
                if future.done():
                    return
                try:
                    if export_close:
                        assert not controller.timer.isActive(), 'Closed export kept polling'
                        assert controller.restore is None, 'Closed player requested a frame restore'
                    future.set_result(snapshot)
                except Exception as error:
                    future.set_exception(error)
            controller.finished.connect(export_finished)
            if export_case:
                def cancel_export(controller=controller, stage=export_case):
                    if controller.cancelled:
                        return
                    snapshot = controller.job.manager.snapshot(controller.job.job_id)
                    event('export_cancel_requested', stage=stage,
                          frame_pending=controller.frame is not None,
                          image_pending=controller.image is not None,
                          worker_active=snapshot.worker_active,
                          progress_message=snapshot.progress_message)
                    if export_close:
                        try:
                            assert controller.image is not None, 'No pending native capture at close'
                            baseline = panel.initialPlcs
                            document_name = panel.doc.Name
                            if export_close == 'task':
                                Gui.Control.activeTaskDialog().reject()
                                assert all(part.Placement.isSame(placement, 1e-9)
                                           for _name, _id, part, placement in baseline.parts), (
                                    'Task close failed to restore original poses')
                            else:
                                App.closeDocument(document_name)
                                assert document_name not in App.listDocuments()
                            assert Gui.Control.activeTaskDialog() is None
                            assert controller.cancelled, 'Owner close did not cancel export'
                            event('export_owner_closed', owner=export_close)
                        except Exception as error:
                            pending.set_exception(error)
                    else:
                        controller.cancel()
                if export_case == 'frame':
                    original_request = panel.requestFrameAsync
                    def request_then_cancel(*args, **kwargs):
                        panel.requestFrameAsync = original_request
                        future = original_request(*args, **kwargs)
                        assert not future.done(), 'Frame preparation was not pending'
                        QtCore.QTimer.singleShot(0, cancel_export)
                        return future
                    panel.requestFrameAsync = request_then_cancel
                elif export_case == 'png':
                    original_png = controller.start_png
                    def capture_then_cancel(*args, **kwargs):
                        controller.start_png = original_png
                        token = original_png(*args, **kwargs)
                        QtCore.QTimer.singleShot(0, cancel_export)
                        return token
                    controller.start_png = capture_then_cancel
                else:
                    controller.progress.connect(lambda _count, message:
                        cancel_export() if message == 'Encoding animation on a background worker'
                        else None)
            state = 'export_wait'
        elif state == 'export_wait':
            # The export progress dialog is nonblocking but window-modal.
            if not pending.done():
                return
            snapshot = pending.result()
            if export_case:
                assert snapshot.phase == 'cancelled', snapshot
                assert not snapshot.worker_active
                assert output.with_name('simulation.gif').read_bytes() == b'previous accepted animation'
                assert not export_controller.staging.exists(), 'Cancelled capture retained staging'
                if export_close:
                    event('export_close_verified', owner=export_close)
                    input_probe.check()
                    assert report['independent_input'], 'No independent input was measured'
                    report['ok'] = True
                    event('complete')
                    state = 'reporting'
                    return
                assert int(panel.form.frameSlider.value()) == export_controller.original_frame
                assert not panel.frame_error, panel.frame_error
                event('export_cancel_verified', stage=export_case,
                      restored_frame=int(panel.form.frameSlider.value()))
                pending = None
                state = 'export'
                return
            assert snapshot.phase == 'completed', snapshot.error
            from PIL import Image, ImageChops
            with Image.open(output.with_name('simulation.gif')) as animation:
                assert animation.n_frames == panel.assembly.numberOfFrames()
                first = animation.convert('RGB')
                changed = 0
                for index in range(1, animation.n_frames):
                    animation.seek(index)
                    if ImageChops.difference(first, animation.convert('RGB')).getbbox():
                        changed += 1
                assert changed, 'Export contains no visible motion relative to frame zero'
                event('export_verified', frames=animation.n_frames, size=animation.size,
                      changed_frames=changed)
            pending = None
            state = 'close'
        elif state == 'close':
            state = 'closing'
            if lifecycle:
                for joint, visible in joint_visibility:
                    joint.ViewObject.Visibility = visible
                baseline = panel.initialPlcs
                pending = panel.requestFrameAsync(1)
                assert not pending.done(), 'The close check needs a pending frame'
                Gui.Control.activeTaskDialog().reject()
                assert pending.done(), 'Closing did not resolve the pending frame'
                assert pending.cancelled() or pending.exception() is not None, (
                    'A frame pending at close was accepted')
                assert Gui.Control.activeTaskDialog() is None
                assert all(part.Placement.isSame(placement, 1e-9)
                           for _name, _id, part, placement in baseline.parts), (
                    'Closing failed to restore the exact original poses')
                event('close_pending_frame_verified', parts=len(baseline.parts))
            elif Gui.Control.activeTaskDialog() is not None:
                Gui.Control.activeTaskDialog().reject()
            input_probe.check()
            assert report['independent_input'], 'No independent Windows input was measured'
            report['ok'] = True
            event('complete')
            state = 'reporting'
        elif state == 'reporting':
            if not report_written.done():
                return
            report_written.result()
            report_writer.close()
            timer.stop()
            pulse.stop()
            input_probe.close()
            App.removeDocumentObserver(changes)
            # Do not save the model or touch any source project artifacts.
            if App.ActiveDocument:
                App.closeDocument(App.ActiveDocument.Name)
            Gui.getMainWindow().close()
    except Exception:
        if original_marker_update is not None:
            import JointObject
            JointObject.ViewProviderJoint.setJCSPosition = original_marker_update
            original_marker_update = None
        report['ok'] = False
        report['error'] = traceback.format_exc()
        event('error', error=report['error'])
        timer.stop()
        pulse.stop()
        input_probe.close()


timer = QtCore.QTimer(Gui.getMainWindow())
timer.setInterval(1000)
timer.timeout.connect(tick)
pulse = QtCore.QTimer(Gui.getMainWindow())
pulse.setInterval(50)
pulse.timeout.connect(heartbeat)
pulse.start()
QtCore.QTimer.singleShot(5000, timer.start)
event('ready')
