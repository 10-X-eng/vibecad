# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run in a disposable GUI: real 50-output create/patch/input-edit/rollback probe.

Set VIBECAD_TRACE_PROBE_RESULT to an ignored JSON output path. Uses the running
build's modules and worker runtime. Creates and closes only its own document.
"""

import FreeCAD as App
import FreeCADGui as Gui
import json
import os
import sys
import time
import traceback
import threading
from pathlib import Path
from PySide import QtCore

# Reuse the native integration fixture; import production modules from the running build.
fixture_root = Path(__file__).resolve().parents[2] / 'Mod/VibeCAD'
# The integration fixture supports source-only tests by adding MODULE_ROOT.
# Append it first so that helper does not put sources ahead of the packaged runtime.
sys.path.append(str(fixture_root))
sys.path.append(str(fixture_root / 'vibecad_tests'))
from partdesign_vibescript_api_integration import _Service
import VibeCADGui as gui
from VibeCADSession import _run_domain_vibescript_tool

class Probe:
    def __init__(self):
        self.root = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT']).parent
        home = Path(App.getHomePath()).resolve()
        self.modules = {name: str(Path(sys.modules[name].__file__).resolve()) for name in
                        ('VibeCADGui', 'VibeCADSession', 'VibeCADVibeScriptDomainRuntime',
                         'VibeCADVibeScriptDomainPublication')}
        if any(not Path(path).is_relative_to(home) for path in self.modules.values()):
            raise RuntimeError('The probe must use the running build, not checkout modules: ' + str(self.modules))
        self.doc = App.newDocument('ParallelPatchProbe')
        self.doc.UndoMode = True
        self.doc.commitTransaction()
        self.service = _Service(self.doc, self.root / 'project')
        self.events = []
        self.result = None
        self.heartbeats = []
        self.last = time.monotonic()
        self.phase = 'startup'
        self.cancel = False
        self.cancel_at_output = None
        self.gaps = []
        gui._ensure_document_thread_invoker()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(20)
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def progress(self, event):
        self.events.append(event)
        if (self.cancel_at_output is not None and event.get('phase') == 'publishing'
                and event.get('completed', 0) >= self.cancel_at_output):
            self.cancel = True

    def call(self, operation, args, *, expect_success=True):
        self.phase = operation
        started = time.monotonic()
        result = _run_domain_vibescript_tool(
            self.service, 'vibescript.partdesign.' + operation, args,
            document_thread_dispatch=gui._dispatch_to_document_thread,
            cancellation_check=lambda: self.cancel,
            progress_callback=self.progress)
        (self.root / (operation + '.json')).write_text(json.dumps(result, indent=2, default=str))
        print('PATCH_PIPELINE', operation, time.monotonic()-started, result.get('ok'), flush=True)
        if expect_success and result.get('ok') is not True:
            raise RuntimeError(str(result))
        return result

    def verify(self, result, width, height):
        self.phase = 'test_geometry_verification'
        def inspect():
            outputs = result['live_outputs']
            assert len(outputs) == 50, len(outputs)
            names = []
            for i in range(50):
                obj = self.doc.getObject(outputs[f'Part{i}']['object_name'])
                assert obj is not None
                assert abs(obj.Shape.Volume - (width+i)*2*height) < 1e-7, (i, obj.Shape.Volume)
                names.append(obj.Name)
            return names
        return gui._dispatch_to_document_thread(inspect)

    def run(self):
        try:
            source = 'height = 3.0\nresult = {\n' + ''.join(
                f"'Part{i}': api.body(api.box(inputs['width']+{i}, 2, height), label='Part{i}'),\n"
                for i in range(50)) + '}\n'
            result = self.call('create_program', {
                'program_name':'Parallel patch probe', 'source':source,
                'input_schema':{'type':'object','properties':{'width':{'type':'number','exclusiveMinimum':0}},
                                'required':['width'],'additionalProperties':False},
                'inputs':{'width':1},
                'expected_outputs':[{'name':f'Part{i}','type':'solid'} for i in range(50)]})
            names = self.verify(result, 1, 3)
            def edit_args():
                return {'program_id': result['program_id'], 'expected_revision':result['working_revision']}
            result = self.call('apply_patch', dict(edit_args(), patch=
                '*** Begin Patch\n*** Update File: source.py\n@@\n-height = 3.0\n+height = 4.0\n*** End Patch'))
            assert self.verify(result, 1, 4) == names
            result = self.call('set_inputs', dict(edit_args(), patch={'width':2}))
            assert self.verify(result, 2, 4) == names
            self.cancel_at_output = 10
            cancelled = self.call('apply_patch', dict(edit_args(), patch=
                '*** Begin Patch\n*** Update File: source.py\n@@\n-height = 4.0\n+height = 5.0\n*** End Patch'), expect_success=False)
            assert self.cancel and cancelled.get('ok') is not True, cancelled
            assert self.verify(result, 2, 4) == names
            self.result = {'ok':True,'outputs':50,'operations':['create_program','apply_patch','set_inputs','cancelled_patch_rollback']}
        except Exception:
            self.result = {'ok':False,'error':traceback.format_exc()}

    def tick(self):
        now = time.monotonic()
        if now-self.last > 0.1:
            self.gaps.append({'seconds':now-self.last, 'phase':self.phase,
                              'last_event':self.events[-1] if self.events else None})
        self.heartbeats.append(now-self.last)
        self.last = now
        if self.result is None:
            return
        # Cancellation rolls the model back before the queued display updates
        # finish. Wait for those owners before asking the diagnostic GUI to close.
        if any(bool(getattr(self.doc, name, False)) for name in
               ('Recomputing', 'RecomputePending', 'CooperativeMutationActive',
                'PresentationUpdateActive')):
            return
        self.timer.stop()
        self.thread.join()
        self.result['heartbeat_max_seconds'] = max(self.heartbeats)
        self.result['events'] = self.events
        self.result['gaps'] = self.gaps
        self.result['runtime_modules'] = self.modules
        Path(os.environ['VIBECAD_TRACE_PROBE_RESULT']).write_text(json.dumps(self.result, indent=2, default=str))
        print('PATCH_DONE', self.result['ok'], self.result.get('error',''), flush=True)
        Gui.getDocument(self.doc.Name).Modified = False
        App.closeDocument(self.doc.Name)
        Gui.getMainWindow().close()

probe = None
def start():
    global probe
    probe = Probe()
QtCore.QTimer.singleShot(1500, start)
