"""Read-only model lifecycle experiment; run only on an isolated disposable copy."""
import ctypes
import gc
import json
import os
from pathlib import Path
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import VibeCADGui as runtime
from PySide6 import QtCore, QtGui, QtWidgets

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT'])
source = Path(os.environ['VIBECAD_ROUNDTRIP_COPY'])
if source.parent != output.parent or source.name != 'probe-document.FCStd' or App.listDocuments():
    raise RuntimeError('Use a fresh isolated instance and disposable probe document')

class Counters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged',
                                          'peak_nonpaged', 'nonpaged', 'pagefile',
                                          'peak_pagefile', 'private')]

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = ctypes.c_void_p
psapi = ctypes.WinDLL('psapi', use_last_error=True)
psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
start = time.monotonic()
phase = 'startup'
cycle = 0
quiet_since = None
phase_start = start
report = {'pid': os.getpid(), 'events': [], 'samples': [], 'ok': False}

def memory():
    c = Counters()
    c.cb = ctypes.sizeof(c)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'private': c.private, 'working_set': c.ws, 'peak_working_set': c.peak_ws}

def event(name, **kwargs):
    entry = {'event': name, 'seconds': time.monotonic()-start, 'cycle': cycle,
             **memory(), **kwargs}
    report['events'].append(entry)
    print('MEMORY_LIFECYCLE ' + json.dumps(entry), flush=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')

class Observer:
    def slotFinishRestoreDocument(self, doc):
        event('finish_restore', objects=len(doc.Objects))
    def slotRecomputedDocument(self, doc):
        event('recomputed', objects=len(doc.Objects))

observer = Observer()
App.addDocumentObserver(observer)
main = Gui.getMainWindow()

def tick():
    global phase, cycle, phase_start, quiet_since
    now = time.monotonic()
    report['samples'].append({'seconds': now-start, 'phase': phase, **memory()})
    try:
        modal = QtWidgets.QApplication.activeModalWidget()
        if modal:
            raise RuntimeError('Unexpected modal: '+modal.windowTitle())
        if phase == 'startup' and now-phase_start >= 10:
            event('startup_idle', runtime=dict(App.hostRuntimeStatus()))
            phase = 'opening'
            cycle = 1
            QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(), QtGui.QFileOpenEvent(str(source)))
        elif phase == 'opening':
            doc = App.ActiveDocument
            busy = doc is None or any((doc.Restoring, doc.Recomputing, doc.RecomputePending,
                                      doc.CooperativeMutationActive, doc.PresentationUpdateActive))
            busy = busy or bool(runtime._pending_document_render_refreshes)
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            elif now-quiet_since >= 5:
                event('opened_idle', objects=len(doc.Objects), undo_bytes=doc.UndoRedoMemSize)
                phase = 'closing'
                App.closeDocument(doc.Name)
                event('close_returned')
                phase_start = now
        elif phase == 'closing' and not App.listDocuments() and now-phase_start >= 10:
            event('closed_idle')
            gc.collect()
            event('closed_after_python_gc')
            if cycle < 2:
                cycle += 1
                phase = 'opening'
                quiet_since = None
                QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(), QtGui.QFileOpenEvent(str(source)))
            else:
                report['ok'] = True
                event('complete')
                timer.stop()
                main.close()
    except Exception:
        report['error'] = traceback.format_exc()
        event('error', error=report['error'])
        timer.stop()

timer = QtCore.QTimer(main)
timer.setInterval(1000)
timer.timeout.connect(tick)
timer.start()
event('ready')
