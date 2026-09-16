# SPDX-License-Identifier: LGPL-2.1-or-later
"""Actual GUI gate for immediate camera fitting before simulation capture.

Run in a disposable empty GUI via the packaged or native diagnostic launcher.
Creates 50 independent visible objects, checks final framing and verifies that
fitAll does not enter a nested event loop. Does not save a document.
"""
import json
import importlib.util
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide6 import QtCore

candidate = os.environ.get('VIBECAD_SIMULATION_SOURCE')
if candidate:
    name = 'CommandCreateSimulation'
    spec = importlib.util.spec_from_file_location(
        name, Path(candidate) / 'Assembly' / 'CommandCreateSimulation.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
from CommandCreateSimulation import TaskAssemblyCreateSimulation

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT'])
if App.listDocuments():
    raise RuntimeError('Use a disposable empty GUI for the camera fit probe')
document = None
state = 'create'
ready_since = None
queued_callback = []
report = {'ok': False}


def tick():
    global document, state, ready_since
    try:
        if state == 'create':
            document = App.newDocument('CameraFitProbe')
            shape = Part.makeBox(10, 10, 10)
            for index in range(50):
                obj = document.addObject('Part::Feature', 'Part')
                obj.Shape = shape
                obj.Placement.Base = App.Vector(index % 10 * 12, index // 10 * 12, 0)
            state = 'ready'
        elif state == 'ready':
            if document.RecomputePending or document.Recomputing or document.PresentationUpdateActive:
                ready_since = None
                return
            if ready_since is None:
                ready_since = time.monotonic()
                return
            if time.monotonic() - ready_since < 1:
                return
            view = Gui.activeDocument().activeView()
            view.setCameraType('Orthographic')
            view.setAnimationEnabled(True)
            camera = view.getCameraNode()
            camera.orientation.setValue(0, 0, 0, 1)
            camera.height.setValue(1)
            QtCore.QTimer.singleShot(0, lambda: queued_callback.append(True))
            started = time.perf_counter()
            panel = SimpleNamespace(view=view, presentation_visibility=[],
                                    requested_camera_method='viewTop')
            TaskAssemblyCreateSimulation._activatePlaybackPresentation(panel)
            report['fit_seconds'] = time.perf_counter() - started
            assert not queued_callback, 'Camera setup entered a nested event loop'
            assert view.isAnimationEnabled(), 'Camera setup changed the view preference'
            width, height = view.getSize()
            fitted_height = camera.height.getValue()
            position = camera.position.getValue().getValue()
            report.update(objects=len(document.Objects), height=fitted_height,
                          position=list(position), animation_preference_preserved=True)
            assert fitted_height >= 58 - 1e-4
            assert fitted_height * width / height >= 118 - 1e-4
            assert abs(position[0] - 59) < 1e-4 and abs(position[1] - 29) < 1e-4, position
            state = 'finish'
        elif state == 'closing':
            if any((document.RecomputePending, document.Recomputing,
                    document.CooperativeMutationActive, document.PresentationUpdateActive)):
                return
            App.closeDocument(document.Name)
            timer.stop()
            Gui.getMainWindow().close()
        elif state == 'finish':
            assert queued_callback, 'Normal event processing did not resume'
            report['ok'] = True
            finish()
    except Exception:
        report['error'] = traceback.format_exc()
        finish()


def finish():
    global state
    output.write_text(json.dumps(report), encoding='utf-8')
    print('SIMULATION_CAMERA_FIT ' + json.dumps(report), flush=True)
    state = 'closing'


timer = QtCore.QTimer(Gui.getMainWindow())
timer.setInterval(100)
timer.timeout.connect(tick)
QtCore.QTimer.singleShot(3000, timer.start)
