"""Disposable packaged-GUI diagnostic; never give this probe an original document."""
import json
import os
import ctypes
from ctypes import wintypes
import subprocess
import uuid
from pathlib import Path
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
import cProfile
from functools import wraps

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets


source = Path(os.environ['VIBECAD_ROUNDTRIP_COPY']).resolve()
output = Path(os.environ['VIBECAD_ROUNDTRIP_REPORT']).resolve()
if source.parent != output.parent or not source.name.startswith('probe-'):
    raise RuntimeError('Use a probe-* document copy beside its diagnostic report')
if App.listDocuments():
    raise RuntimeError('Run in a fresh diagnostic process with no open documents')
with zipfile.ZipFile(source) as archive:
    root = ET.fromstring(archive.read('Document.xml'))
expected_names = sorted(item.attrib['name'] for item in root.findall('./Objects/Object'))
expected_invalid = sorted(item.attrib['name'] for item in root.findall('./Objects/Object')
                          if item.attrib.get('Invalid') == '1')
if not expected_names:
    raise RuntimeError('Source has no native object inventory')
baseline_path = os.environ.get('VIBECAD_ROUNDTRIP_BASELINE')
baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8')) if baseline_path else None

main = Gui.getMainWindow()
profiler = cProfile.Profile() if os.environ.get('VIBECAD_PROFILE_CALLBACKS') else None
if profiler:
    profiler.enable()
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
user32.PostMessageW.restype = ctypes.c_int
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.GetTickCount.restype = wintypes.DWORD
kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
stop_name = 'Local\\VibeCADInputProbe-' + uuid.uuid4().hex
input_stop = kernel32.CreateEventW(None, True, False, stop_name)
if not input_stop:
    raise ctypes.WinError(ctypes.get_last_error())
started = last_tick = time.perf_counter()
phase = 'startup'
document = None
settled_since = None
initial_inventory = None
inventory = {}
inventory_objects = []
inventory_index = 0
command_profile_names = None
pending_input = None
save_finished = False
recompute_finished = False
recompute_started = None
recompute_cpu_started = None
closing_name = None
input_excluded_until = None
trace_clock_offset = None
report = {'ok': False, 'source': str(source), 'events': [], 'gaps': [], 'input': [],
          'expected_count': len(expected_names), 'expected_invalid': expected_invalid,
          'runtime': dict(App.hostRuntimeStatus())}


def check_invalid_flags(expected, actual, allowed_cleared=()):
    cleared = set(expected) - set(actual)
    if set(actual) - set(expected) or cleared - set(allowed_cleared):
        raise RuntimeError('Invalid object flags differ from those already saved in the input archive: '
                           + repr({'expected': expected, 'actual': actual}))
    return sorted(cleared)


def event(name, **values):
    report['events'].append({'event': name, 'seconds': time.perf_counter() - started, **values})
    print('VIBECAD_ROUNDTRIP ' + json.dumps(report['events'][-1]), flush=True)


if profiler:
    # Correlate high-level Python callbacks with native event spans. cProfile's
    # cumulative totals alone cannot identify which queued invocation stalled.
    import VibeCADGui as gui_runtime

    report['python_callbacks'] = []

    def trace_callback(original):
        @wraps(original)
        def measured(*args, **kwargs):
            before = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = 1000 * (time.perf_counter() - before)
                if elapsed >= 8:
                    report['python_callbacks'].append({
                        'name': original.__name__, 'seconds': before - started,
                        'elapsed_ms': elapsed,
                    })
        return measured

    for name in ('_refresh_assistant_for_document_change',
                 '_restore_partdesign_history_rendering',
                 '_migrate_standard_fastener_timeline_resources',
                 '_migrate_partdesign_component_timeline_resources'):
        setattr(gui_runtime, name, trace_callback(getattr(gui_runtime, name)))


class InputProbe(QtCore.QObject):
    def eventFilter(self, watched, incoming):
        global pending_input
        if incoming.type() in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease) and incoming.key() == QtCore.Qt.Key.Key_F23:
            return True
        if incoming.type() in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease) and incoming.key() == QtCore.Qt.Key.Key_F24:
            if pending_input and incoming.type() == QtCore.QEvent.Type.KeyPress:
                posted, posted_phase = pending_input
                report['input'].append({'phase': posted_phase,
                                        'delivered_phase': phase,
                                        'seconds': time.perf_counter() - started,
                                        'milliseconds': 1000 * (time.perf_counter() - posted)})
                pending_input = None
            return True
        return False


input_probe = InputProbe(main)
QtWidgets.QApplication.instance().installEventFilter(input_probe)


class NativeInputProbe(QtCore.QAbstractNativeEventFilter):
    def nativeEventFilter(self, event_type, message):
        native = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if native.message == 0x0100 and native.wParam == 0x86:
            if phase == 'diagnostic_screenshot' or (input_excluded_until is not None
                    and ((native.time - input_excluded_until) & 0xffffffff) >= 0x80000000):
                return False, 0
            # MSG.time is stamped when Windows queues the message, not when
            # Qt finally delivers it. The sender runs outside this process.
            report.setdefault('independent_input', []).append({
                'phase': phase,
                'milliseconds': (kernel32.GetTickCount() - native.time) & 0xffffffff,
                'seconds': time.perf_counter() - started,
            })
        return False, 0


native_input_probe = NativeInputProbe()
QtWidgets.QApplication.instance().installNativeEventFilter(native_input_probe)
input_sender = subprocess.Popen([
    str(Path(App.getHomePath()) / 'bin' / 'pythonw.exe'),
    os.environ['VIBECAD_INPUT_SENDER'],
    str(int(main.winId())), str(os.getpid()), stop_name,
], creationflags=subprocess.CREATE_NO_WINDOW)


def post_input():
    global pending_input
    if pending_input is None:
        pending_input = (time.perf_counter(), phase)
        # Exercise the Windows message dispatcher, not Qt's posted-event queue:
        # a nested loop excluding user input can still drain posted QEvents.
        # F24 is consumed by our application event filter; no desktop focus or
        # physical keyboard state is changed by these window-local messages.
        for message, flags in ((0x0100, 1), (0x0101, 0xC0000001)):
            if not user32.PostMessageW(int(main.winId()), message, 0x87, flags):
                raise ctypes.WinError(ctypes.get_last_error())


def set_phase(value):
    global phase, settled_since
    phase = value
    settled_since = None
    event(value)
    post_input()


def open_copy():
    QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(), QtGui.QFileOpenEvent(str(source)))


def finish(error=None):
    timer.stop()
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(output.with_suffix('.pstats')))
    if input_sender.poll() is not None and error is None:
        error = 'Independent Windows input sender exited before measurement completed'
    kernel32.SetEvent(input_stop)
    kernel32.CloseHandle(input_stop)
    if error is None and not report['input']:
        error = 'Native Windows input was never delivered to the diagnostic event filter'
    report['ok'] = error is None
    report['error'] = error
    report['max_gap_ms'] = max((item['milliseconds'] for item in report['gaps']), default=0)
    report['max_input_ms'] = max((item['milliseconds'] for item in report['input']), default=0)
    report['max_independent_input_ms'] = max(
        (item['milliseconds'] for item in report.get('independent_input', [])), default=0)
    report['responsiveness_ok'] = bool(report.get('independent_input')) and max(
        report['max_gap_ms'], report['max_independent_input_ms']) <= 100
    report['integrity_ok'] = error is None
    report['ok'] = report['integrity_ok'] and report['responsiveness_ok']
    report['validation_scope'] = ('packaged GUI open/history-recompute/save/reopen/close'
                                  if os.environ.get('VIBECAD_EXERCISE_GENERATED_RECOMPUTE')
                                  else 'packaged GUI open/save/reopen/close')
    if error is None and not report['responsiveness_ok']:
        report['error'] = 'Input delivery or the 100 ms responsiveness gate failed; inspect timing records'
    report['runtime_final'] = dict(App.hostRuntimeStatus())
    event('complete', ok=report['ok'], error=report['error'])
    # Persist only after measurement stops: the benchmark must not introduce
    # repeated synchronous filesystem writes into the GUI events it measures.
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if error:
        main.grab().save(str(output.with_suffix('.failure.png')))
    if not error:
        main.close()


class SaveObserver:
    def slotRecomputedDocument(self, recomputed):
        global recompute_finished
        if phase == 'recompute' and recomputed.Name == document.Name:
            recompute_finished = True
            event('recompute_finished')

    def slotChangedObject(self, obj, prop):
        if phase in ('save', 'close'):
            report.setdefault('save_changes', []).append({
                'seconds': time.perf_counter() - started, 'phase': phase,
                'object': obj.Name, 'property': prop,
                'after_finish': save_finished, 'stack': traceback.format_stack(limit=12),
            })

    def slotChangedDocument(self, doc, prop):
        if phase in ('save', 'close'):
            report.setdefault('save_changes', []).append({
                'seconds': time.perf_counter() - started, 'phase': phase,
                'document': doc.Name, 'property': prop,
                'after_finish': save_finished, 'stack': traceback.format_stack(limit=12),
            })

    def slotFinishSaveDocument(self, saved, *args):
        global save_finished
        if phase == 'save' and saved.Name == document.Name:
            save_finished = True
            event('save_finished')


save_observer = SaveObserver()
App.addDocumentObserver(save_observer)


class SaveGuiObserver:
    def slotChangedObject(self, vp, prop):
        if phase in ('save', 'close'):
            report.setdefault('save_gui_changes', []).append({
                'seconds': time.perf_counter() - started, 'phase': phase,
                'object': vp.Object.Name, 'property': prop,
                'after_finish': save_finished, 'stack': traceback.format_stack(limit=12),
            })


save_gui_observer = SaveGuiObserver()
Gui.addDocumentObserver(save_gui_observer)


def quiet():
    return not any((document.Restoring, document.Recomputing, document.RecomputePending,
                    document.CooperativeMutationActive, document.PresentationUpdateActive))


def snapshot_step():
    global inventory_index, command_profile_names
    deadline = time.perf_counter() + 0.004
    while inventory_index < len(inventory_objects):
        obj = inventory_objects[inventory_index]
        inventory[obj.Name] = {
            'type': obj.TypeId,
            'links': sorted(['$self' if target.Document == document else target.Document.Name, target.Name]
                            for target in obj.OutList),
            'state': list(obj.State),
        }
        inventory_index += 1
        if time.perf_counter() >= deadline:
            return False
    if phase == 'inventory_open' and os.environ.get('VIBECAD_PROFILE_COMMANDS'):
        if command_profile_names is None:
            command_profile_names = list(Gui.Command.listAll())
            report['command_checks'] = []
        while command_profile_names:
            name = command_profile_names.pop()
            command = Gui.Command.get(name)
            if command.getAction():
                before = time.perf_counter()
                active = command.isActive()
                report['command_checks'].append({
                    'name': name, 'active': active,
                    'milliseconds': 1000 * (time.perf_counter() - before),
                })
            if time.perf_counter() >= deadline:
                return False
    return True


def tick():
    global last_tick, document, settled_since, initial_inventory
    global inventory, inventory_objects, inventory_index
    global closing_name, input_excluded_until
    global recompute_started, recompute_cpu_started
    global trace_clock_offset
    now = time.perf_counter()
    gap = 1000 * (now - last_tick)
    last_tick = now
    if gap >= 50:
        report['gaps'].append({'phase': phase, 'milliseconds': gap, 'seconds': now - started,
                               'status': main.statusBar().currentMessage()})
    try:
        if os.environ.get('VIBECAD_TRACE_NATIVE_EVENTS'):
            timings = QtCore.QMetaObject.invokeMethod(
                QtWidgets.QApplication.instance(), 'takePerformanceEvents',
                QtCore.Qt.ConnectionType.DirectConnection, QtCore.Q_RETURN_ARG('QVariantMap'))
            if not timings['enabled']:
                raise RuntimeError('Native GUI event tracing is disabled')
            if trace_clock_offset is None:
                trace_clock_offset = timings['clock_ms'] - 1000 * (time.perf_counter() - started)
            for item in timings['events']:
                item['seconds'] = (item['start_ms'] - trace_clock_offset) / 1000
            report.setdefault('gui_events', []).extend(timings['events'])
            report['dropped_gui_events'] = report.get('dropped_gui_events', 0) + timings['dropped']
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal is not None:
            message = modal.text() if isinstance(modal, QtWidgets.QMessageBox) else modal.objectName()
            raise RuntimeError('Diagnostic encountered a modal dialog: ' + modal.windowTitle() + ': ' + message)
        post_input()
        if phase == 'startup':
            set_phase('open')
            open_copy()
        elif phase in ('open', 'reopen'):
            if document is None:
                document = next((doc for doc in App.listDocuments().values()
                                 if doc.FileName and Path(doc.FileName).resolve() == source), None)
            if document is None or not quiet():
                settled_since = None
                return
            if settled_since is None:
                settled_since = now
            elif now - settled_since >= 1:
                event('display_settled', objects=len(document.Objects))
                inventory = {}
                inventory_objects = list(document.Objects)
                inventory_index = 0
                set_phase('inventory_open' if phase == 'open' else 'inventory_reopen')
        elif phase in ('inventory_open', 'inventory_reopen', 'inventory_after_recompute'):
            if not snapshot_step():
                return
            report[phase] = inventory
            if phase == 'inventory_open' and os.environ.get('VIBECAD_ALLOW_TIMELINE_MIGRATION'):
                missing = set(expected_names) - set(inventory)
                added = set(inventory) - set(expected_names)
                # Ordinary FreeCAD documents acquire VibeCAD's native History
                # controller on first open. Admit only that exact migration;
                # never forgive a missing model object or unrelated addition.
                if (not missing and len(added) == 1
                        and all(inventory[name]['type'] == 'App::DocumentTimeline' for name in added)
                        and not any(inventory[name]['type'] == 'App::DocumentTimeline'
                                    for name in expected_names)):
                    report['timeline_migration'] = sorted(added)
                    expected_names.extend(added)
                    expected_names.sort()
            if sorted(inventory) != expected_names:
                raise RuntimeError('Restored object names differ from the input archive: '
                                   + repr({'missing': sorted(set(expected_names) - set(inventory)),
                                           'added': sorted(set(inventory) - set(expected_names))}))
            invalid = sorted(name for name, item in inventory.items() if 'Invalid' in item['state'])
            cleared = check_invalid_flags(
                expected_invalid, invalid,
                json.loads(os.environ.get('VIBECAD_ALLOW_CLEARED_INVALID_OBJECTS', '[]')))
            report.setdefault('cleared_saved_invalid_flags', {})[phase] = cleared
            if phase in ('inventory_open', 'inventory_after_recompute'):
                if phase == 'inventory_after_recompute' and any(
                        inventory[name]['type'] != initial_inventory[name]['type']
                        or inventory[name]['links'] != initial_inventory[name]['links']
                        for name in inventory):
                    raise RuntimeError('History recompute changed object types or links')
                initial_inventory = inventory
                report['opened_inventory'] = inventory
                if baseline is not None:
                    report['baseline_match'] = inventory == baseline['opened_inventory']
                    if not report['baseline_match']:
                        raise RuntimeError('Object types, links, or states differ from the known-good baseline')
                if phase == 'inventory_open' and os.environ.get('VIBECAD_EXERCISE_GENERATED_RECOMPUTE'):
                    blank = document.getObject('Blank000')
                    if blank is None or blank.TypeId != 'Part::Box' or document.getObject('Result199') is None:
                        raise RuntimeError('Recompute exercise requires the generated boolean workload')
                    for index in range(200):
                        document.getObject(f'Blank{index:03d}').touch()
                        document.getObject(f'Bore{index:03d}').touch()
                        document.getObject(f'Result{index:03d}').touch()
                    set_phase('recompute_ready')
                    return
                document.Comment += '\nDisposable performance round-trip probe.'
                set_phase('save')
                before = time.perf_counter()
                Gui.runCommand('Std_Save')
                event('save_command_returned', milliseconds=1000 * (time.perf_counter() - before),
                      finished_before_return=save_finished)
            else:
                report['reopened_inventory'] = inventory
                if inventory != initial_inventory:
                    raise RuntimeError('Object types, links, or states changed during save/reopen')
                # Capture visual evidence outside the measured event-loop interval.
                # QWidget.grab itself renders synchronously and must not be
                # mistaken for a production stall introduced by the document.
                timer.stop()
                set_phase('diagnostic_screenshot')
                before = time.perf_counter()
                if not main.grab().save(str(output.with_suffix('.png'))):
                    raise RuntimeError('Could not capture the restored GUI')
                event('diagnostic_screenshot', milliseconds=1000 * (time.perf_counter() - before))
                input_excluded_until = kernel32.GetTickCount()
                last_tick = time.perf_counter()
                timer.start()
                set_phase('final_close')
                before = time.perf_counter()
                Gui.runCommand('Std_CloseAllWindows')
                event('final_close_returned', milliseconds=1000 * (time.perf_counter() - before))
        elif phase == 'recompute_ready':
            button = main.findChild(QtWidgets.QToolButton, 'VibeCADFeatureTimelineRecompute')
            if button is None:
                raise RuntimeError('History recompute button is missing')
            if button.isEnabled() and quiet():
                set_phase('recompute')
                recompute_started = time.perf_counter()
                recompute_cpu_started = os.times()
                button.click()
                event('recompute_button_returned', milliseconds=1000 * (time.perf_counter() - recompute_started))
        elif phase == 'recompute' and recompute_finished and quiet():
            cpu = os.times()
            report['recompute'] = {
                'wall_seconds': time.perf_counter() - recompute_started,
                'cpu_seconds': cpu.user + cpu.system - recompute_cpu_started.user - recompute_cpu_started.system,
            }
            inventory = {}
            inventory_objects = list(document.Objects)
            inventory_index = 0
            set_phase('inventory_after_recompute')
        elif phase == 'save' and save_finished and quiet():
            set_phase('close')
            closing_name = document.Name
            inventory_objects = []
            before = time.perf_counter()
            Gui.runCommand('Std_CloseAllWindows')
            event('close_command_returned', milliseconds=1000 * (time.perf_counter() - before))
        elif phase == 'save' and not save_finished and quiet():
            raise RuntimeError('Save did not complete and no document work remains: '
                               'the request was cancelled, refused, or failed; round-trip is incomplete')
        elif phase == 'close' and closing_name not in App.listDocuments():
            document = None
            set_phase('reopen')
            open_copy()
        elif phase == 'final_close' and not App.listDocuments():
            finish()
    except Exception:
        finish(traceback.format_exc())


timer = QtCore.QTimer(main)
timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
timer.setInterval(16)
timer.timeout.connect(tick)
timer.start()
event('ready')
