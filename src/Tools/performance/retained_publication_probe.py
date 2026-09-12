# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replay a retained Assembly result in an isolated disposable GUI document.

Set VIBECAD_PUBLICATION_ATTEMPT to an ignored COPY of the worker attempt and
VIBECAD_PUBLICATION_MANIFEST to a copied program.json. Launch with the packaged
edit-check runner and a disposable document. No solver, acceptance, or save runs.
The production validator, publisher, observer batching and Qt dispatcher run
unchanged. Profiles separate detached validation from GUI publication callbacks.
Set VIBECAD_PUBLICATION_WORKING_CANDIDATE=1 to replay an unaccepted candidate
against its accepted document revision; source, inputs and revisions are checked.
Set VIBECAD_PUBLICATION_CANCEL_AFTER to an item count to cancel real publication,
check rollback in the disposable document, then retry the same candidate.
Set VIBECAD_PUBLICATION_PROFILE=0 for timing without cProfile overhead; native
callback timing, progress and independent Windows input measurements remain on.
"""
import cProfile
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT']).resolve()
source = Path(os.environ['VIBECAD_ROUNDTRIP_COPY']).resolve()
attempt = Path(os.environ['VIBECAD_PUBLICATION_ATTEMPT']).resolve()
manifest_path = Path(os.environ['VIBECAD_PUBLICATION_MANIFEST']).resolve()
if source.parent != output.parent or source.name != 'probe-document.FCStd' or App.listDocuments():
    raise RuntimeError('Use an isolated process and disposable probe-document.FCStd')
if not attempt.is_relative_to(output.parent.parent) or not manifest_path.is_relative_to(output.parent.parent):
    raise RuntimeError('Worker artifacts must be copies within portable-edit-checks')

started = time.monotonic()
stage = 'startup'
quiet_since = None
worker = None
profiling = os.environ.get('VIBECAD_PUBLICATION_PROFILE', '1') != '0'
lock = threading.Lock()
report = {'ok': False, 'events': [], 'gui_callbacks': [], 'native_events': [],
          'max_heartbeat_gap': 0.0, 'independent_input': [], 'profiling': profiling}
report['native_build'] = App.ConfigGet('BuildRevisionHash')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from window_input_probe import WindowInputProbe
from async_report_writer import AsyncReportWriter
report_writer = AsyncReportWriter(output)
report_written = None
input_probe = WindowInputProbe(Gui.getMainWindow(), lambda milliseconds:
    report['independent_input'].append(dict(
        phase=stage, seconds=time.monotonic() - started, milliseconds=milliseconds)))
input_files = (source, manifest_path, attempt / 'request.json', attempt / 'result.json')
input_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in input_files]
gui_profile = cProfile.Profile()
last_heartbeat = time.monotonic()
last_report_write = 0.0
last_progress_phase = None
cancel_after = int(os.environ.get('VIBECAD_PUBLICATION_CANCEL_AFTER', '0'))
cancel_requested = threading.Event()


def event(name, **values):
    global last_report_write, last_progress_phase, report_written
    with lock:
        now = time.monotonic()
        report['events'].append(dict(event=name, seconds=now - started, **values))
        phase = values.get('phase')
        # Full pretty-printed snapshots per object turn the probe itself into
        # quadratic work. Retain every event, but serialize only at boundaries
        # or once per second using the compact native JSON encoder.
        if name != 'progress' or (phase is not None and phase != last_progress_phase) or now - last_report_write >= 1:
            snapshot = {key: list(value) if isinstance(value, list) else value
                        for key, value in report.items()}
            report_written = report_writer.submit(snapshot)
            last_report_write = now
        if name == 'progress' and phase is not None:
            last_progress_phase = phase


def heartbeat():
    global last_heartbeat
    now = time.monotonic()
    with lock:
        report['max_heartbeat_gap'] = max(report['max_heartbeat_gap'], now - last_heartbeat)
    last_heartbeat = now


def progress(value):
    event('progress', **{key: value[key] for key in
          ('phase', 'message', 'completed', 'total', 'elapsed_ms') if key in value})
    if cancel_after and int(value.get('completed', 0)) >= cancel_after:
        cancel_requested.set()


def rollback_inventory(dispatch, service):
    """Capture logical identity, links and poses in owner-thread-sized steps."""
    from VibeCADVibeScriptDomains import PROP_PROGRAM_REVISION
    names = dispatch(lambda: [obj.Name for obj in service._active_document().Objects])
    result = {}
    for name in names:
        def capture(name=name):
            document = service._active_document()
            obj = document.getObject(name)
            if obj is None:
                raise RuntimeError('Object disappeared during rollback inventory: ' + name)
            placement = getattr(obj, 'Placement', None)
            return dict(type=obj.TypeId, label=obj.Label,
                links=sorted((str(target.Document.Uid), target.Name) for target in obj.OutList),
                placement=None if placement is None else (
                    tuple(placement.Base), tuple(placement.Rotation.Q)),
                revision=getattr(obj, PROP_PROGRAM_REVISION, None),
                visibility=bool(obj.ViewObject.Visibility) if obj.ViewObject else None)
        result[name] = dispatch(capture)
    return result


def run(prepared, execution, service, adapter, dispatch):
    try:
        import VibeCADVibeScriptDomainRuntime as runtime
        before = time.monotonic()
        if profiling:
            validator = cProfile.Profile()
            validated = validator.runcall(adapter.validate_result, prepared, execution)
            validator.dump_stats(str(output.with_name('validation.pstats')))
        else:
            validated = adapter.validate_result(prepared, execution)
        event('validated', elapsed_seconds=time.monotonic() - before,
              outputs=len(validated['outputs']), members=len(validated.get('assembly_members', [])))

        def invoke(operation):
            def profiled():
                begin = time.monotonic()
                try:
                    return operation()
                finally:
                    with lock:
                        report['gui_callbacks'].append(time.monotonic() - begin)
            return dispatch(profiled)

        before = time.monotonic()
        if cancel_after:
            from VibeCADCooperativeExecution import CooperativeExecutionCancelled
            original = rollback_inventory(invoke, service)
            event('cancellation_replay_started', after_items=cancel_after)
            try:
                adapter.publish_cooperatively(service, prepared, validated,
                    document_thread_dispatch=invoke,
                    cancellation_check=cancel_requested.is_set,
                    progress_callback=progress)
            except CooperativeExecutionCancelled:
                event('publication_cancelled')
            else:
                raise RuntimeError('Publication completed without exercising cancellation')
            invoke(lambda: service._active_document().waitForPresentationReady)()
            restored = rollback_inventory(invoke, service)
            if restored != original:
                changed = sorted(name for name in original.keys() | restored.keys()
                                 if original.get(name) != restored.get(name))
                raise RuntimeError('Publication rollback changed objects: ' + repr(changed[:20]))
            report['rollback_ok'] = True
            # Rollback may advance the native revision even when values match.
            # Obtain the new revision through the real owner-thread service.
            prepared = dict(prepared, document_revision=invoke(
                lambda: str(service.provider_document_revision())))
            validated = adapter.validate_result(prepared, execution)
            event('rollback_verified_retry_started', objects=len(original))
            before = time.monotonic()
        adapter.publish_cooperatively(service, prepared, validated,
            document_thread_dispatch=invoke, cancellation_check=lambda: False,
            progress_callback=progress)
        event('published', elapsed_seconds=time.monotonic() - before)
        report['publication_ok'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
        event('failed', error=report['error'])


def tick():
    global stage, quiet_since, worker, last_heartbeat
    try:
        input_probe.check()
        # Attribute native Qt callbacks as well as Python-dispatched adoption.
        # The edit-check runner enables the application's existing tracer.
        trace = QtCore.QMetaObject.invokeMethod(
            QtWidgets.QApplication.instance(), 'takePerformanceEvents',
            QtCore.Qt.ConnectionType.DirectConnection,
            QtCore.Q_RETURN_ARG('QVariantMap'))
        with lock:
            report['native_events'].extend(
                dict(item, probe_stage=stage, observed_seconds=time.monotonic() - started)
                for item in trace['events'])
            report['native_events_dropped'] = report.get('native_events_dropped', 0) + trace['dropped']
        if QtWidgets.QApplication.activeModalWidget():
            raise RuntimeError('Unexpected modal dialog in isolated publication probe')
        if stage == 'startup':
            stage = 'opening'
            QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(),
                                             QtGui.QFileOpenEvent(str(source)))
            event('open_requested')
        elif stage == 'opening':
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
            import VibeCADGui
            import VibeCADVibeScriptDomains as contracts
            from VibeCADCore import get_service
            from VibeCADModelingSurface import resolve_service_surface
            from VibeCADVibeScriptDomainRuntime import AssemblyDomainAdapter
            Gui.activateWorkbench('AssemblyWorkbench')
            service = get_service()
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            request = json.loads((attempt / 'request.json').read_text(encoding='utf-8'))
            execution = json.loads((attempt / 'result.json').read_text(encoding='utf-8'))
            assert request['domain'] == 'assembly' and execution['ok']
            working_candidate = os.environ.get('VIBECAD_PUBLICATION_WORKING_CANDIDATE') == '1'
            assert request['revision'] == manifest[
                'working_revision' if working_candidate else 'accepted_revision']
            assert request['program_id'] == manifest['program_id']
            assert request['document_uid'] == str(doc.Uid)
            owners = [obj for obj in doc.Objects if
                      getattr(obj, contracts.PROP_PROGRAM_ID, '') == request['program_id']
                      and getattr(obj, contracts.PROP_PROGRAM_CONTRACT, '')]
            assert len(owners) == 1, 'Expected exactly one portable contract owner'
            assert getattr(owners[0], contracts.PROP_PROGRAM_REVISION) == manifest['accepted_revision']
            pack = contracts.get_vibescript_pack('AssemblyWorkbench')
            portable_contract = getattr(owners[0], contracts.PROP_PROGRAM_CONTRACT)
            references = manifest.get('resolved_references', [])
            if working_candidate:
                candidate = manifest['latest_candidate']
                assert candidate['revision'] == request['revision']
                assert candidate['base_revision'] == manifest['accepted_revision']
                assert request['source'] == manifest['source']
                assert request['inputs'] == manifest['inputs']
                assert request['expected_outputs'] == manifest['expected_outputs']
                clean = contracts.validate_program_contract(pack, source=request['source'],
                    input_schema=manifest['input_schema'], inputs=request['inputs'],
                    expected_outputs=request['expected_outputs'])
                contract_revision = contracts.program_revision(domain=pack.domain, **clean)
                references = candidate.get('resolved_references', [])
                revision = (contracts.program_revision_with_references(
                    contract_revision=contract_revision, references=references)
                    if references else contract_revision)
                assert revision == request['revision'], 'Retained candidate contract digest mismatch'
                portable_contract = contracts.encode_document_program_contract(pack,
                    program_id=request['program_id'], label=manifest['label'],
                    revision=revision, **clean)
            surface = resolve_service_surface(service, service.active_workbench_name())
            assert surface.available, surface.unavailable_reason
            prepared = dict(pack=pack,
                staging=attempt, expected_outputs=request['expected_outputs'],
                worker_request=request, resolved_references=references,
                program_id=request['program_id'], program_name=manifest['label'],
                revision=request['revision'], document_name=doc.Name, document_uid=str(doc.Uid),
                document_revision=str(service.provider_document_revision()),
                surface=dict(workbench=surface.workbench, engine=surface.engine, surface_id=surface.surface_id),
                document_program_contract=portable_contract)
            VibeCADGui._ensure_document_thread_invoker()
            report['max_heartbeat_gap'] = 0.0
            last_heartbeat = time.monotonic()
            event('replay_started', objects=len(doc.Objects))
            stage = 'publishing'
            # Profile the entire owner thread through validation as well as apply.
            # A profiler around only adoption misses timer/queued callback stalls.
            if profiling:
                gui_profile.enable()
            worker = threading.Thread(target=run, args=(prepared, execution, service,
                AssemblyDomainAdapter(pack=prepared['pack']),
                VibeCADGui._dispatch_to_document_thread), daemon=True)
            worker.start()
        elif stage == 'publishing' and not worker.is_alive():
            if not report.get('publication_ok'):
                raise RuntimeError(report.get('error', 'Publication did not succeed'))
            stage = 'settling'
            quiet_since = None
            event('publication_worker_returned')
        elif stage == 'settling':
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
            if profiling:
                gui_profile.disable()
                gui_profile.dump_stats(str(output.with_name('main-thread.pstats')))
            stage = 'finished'
            assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in input_files] == input_hashes
            assert any(item['phase'] == 'publishing' for item in report['independent_input']), \
                'No independent Windows input was measured during publication'
            report['ok'] = True
            event('complete', objects=len(App.ActiveDocument.Objects))
            stage = 'reporting'
        elif stage == 'reporting':
            if not report_written.done():
                return
            report_written.result()
            report_writer.close()
            stage = 'finished'
            timer.stop()
            pulse.stop()
            input_probe.close()
            App.closeDocument(App.ActiveDocument.Name)
            Gui.getMainWindow().close()
    except Exception:
        report['ok'] = False
        report['error'] = traceback.format_exc()
        event('failed', error=report['error'])
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
