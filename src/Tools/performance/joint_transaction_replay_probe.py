# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise native abort/undo/redo on an isolated copy of a real assembly.

Use the packaged edit-check runner. VIBECAD_JOINT_SOURCE optionally selects a
Python-only candidate module before restore. The solve observer records attempts
without running the expensive erroneous solver during a failing baseline test.
No document or project is saved.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT']).resolve()
source = Path(os.environ['VIBECAD_ROUNDTRIP_COPY']).resolve()
if source.parent != output.parent or source.name != 'probe-document.FCStd' or App.listDocuments():
    raise RuntimeError('Use a fresh process and an isolated document copy')

if os.environ.get('VIBECAD_JOINT_SOURCE'):
    spec = importlib.util.spec_from_file_location('JointObject', os.environ['VIBECAD_JOINT_SOURCE'])
    module = importlib.util.module_from_spec(spec)
    sys.modules['JointObject'] = module
    spec.loader.exec_module(module)

started = time.monotonic()
state = 'startup'
quiet_since = None
steps = None
report = {'ok': False, 'pid': os.getpid(), 'events': [], 'solve_attempts': []}


def event(name, **values):
    report['events'].append(dict(event=name, seconds=time.monotonic() - started, **values))
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')


def exercise(doc):
    import JointObject
    import Preferences
    from VibeCADAssemblySolverPolicy import suspend_joint_autosolve

    joints = [obj for obj in doc.Objects if isinstance(getattr(obj, 'Proxy', None), JointObject.Joint)
              and obj.Reference1 and obj.Reference2 and JointObject._jointInteractionUsable(obj)]
    assert joints, 'No live joint to exercise'
    joint = joints[0]
    original = joint.Offset1.copy()
    changed = original.copy()
    changed.Base.x += 0.125
    before = {obj.Name: obj.Placement.copy() for obj in doc.Objects if 'Placement' in obj.PropertiesList}
    solve = JointObject.solveIfAllowed
    preferences = Preferences.preferences()
    enabled = preferences.GetBool('SolveInJointCreation', True)
    phase = 'edit'

    def observe(assembly, storePrev=False):
        if preferences.GetBool('SolveInJointCreation', True):
            report['solve_attempts'].append(dict(phase=phase, transacting=doc.Transacting))

    JointObject.solveIfAllowed = observe
    try:
        preferences.SetBool('SolveInJointCreation', True)
        doc.openTransaction('Probe joint abort')
        with suspend_joint_autosolve():
            joint.Offset1 = changed
        phase = 'abort'
        now = time.monotonic()
        doc.abortTransaction()
        event('abort', elapsed_seconds=time.monotonic() - now)
        yield
        assert joint.Offset1.isSame(original)
        assert all(doc.getObject(name).Placement.isSame(value) for name, value in before.items())
        doc.openTransaction('Probe joint undo and redo')
        with suspend_joint_autosolve():
            joint.Offset1 = changed
        doc.commitTransaction()
        yield
        phase = 'undo'
        doc.undo()
        yield
        assert joint.Offset1.isSame(original)
        phase = 'redo'
        doc.redo()
        yield
        assert joint.Offset1.isSame(changed)
        phase = 'restore'
        doc.undo()
        yield
        assert joint.Offset1.isSame(original)
        assert not report['solve_attempts'], report['solve_attempts']
        report['ok'] = True
        event('complete', joint=joint.Name, objects=len(doc.Objects))
    finally:
        JointObject.solveIfAllowed = solve
        preferences.SetBool('SolveInJointCreation', enabled)


def tick():
    global state, quiet_since, steps
    try:
        if QtWidgets.QApplication.activeModalWidget() is not None:
            raise RuntimeError('Unexpected modal dialog')
        if state == 'startup':
            state = 'opening'
            QtCore.QCoreApplication.postEvent(QtWidgets.QApplication.instance(), QtGui.QFileOpenEvent(str(source)))
            event('open')
            return
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
        if steps is None:
            steps = exercise(doc)
        try:
            next(steps)
            quiet_since = None
            return
        except StopIteration:
            pass
    except Exception:
        report['error'] = traceback.format_exc()
        event('error')
    timer.stop()
    if App.ActiveDocument:
        App.closeDocument(App.ActiveDocument.Name)
    Gui.getMainWindow().close()


timer = QtCore.QTimer(Gui.getMainWindow())
timer.setInterval(500)
timer.timeout.connect(tick)
QtCore.QTimer.singleShot(5000, timer.start)
event('ready')
