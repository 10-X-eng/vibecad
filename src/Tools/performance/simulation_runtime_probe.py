# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exercise the native detached simulation API in an isolated packaged GUI.

No customer document is opened or saved. The ordinary playback probe separately
measures the large saved model. This probe checks asynchronous completion,
source invalidation, cancellation, and document destruction while work is live.
"""
import json
import os
from pathlib import Path
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore

output = Path(os.environ['VIBECAD_TRACE_PROBE_RESULT'])
report = {'ok': False, 'events': []}
document = None
assembly = None
simulation = None
stage = 'start'
request = None
frame_request = None
frame_expected = None
started = time.monotonic()


def event(name, **data):
    report['events'].append(dict(event=name, seconds=time.monotonic() - started, **data))
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')


def tick():
    global document, assembly, simulation, stage, request, frame_request, frame_expected
    try:
        if stage == 'start':
            if App.listDocuments():
                raise RuntimeError('This probe requires an isolated empty process')
            Gui.activateWorkbench('AssemblyWorkbench')
            import Part
            import JointObject
            import UtilsAssembly
            from CommandCreateSimulation import Simulation
            document = App.newDocument('SimulationRuntimeProbe')
            assembly = document.addObject('Assembly::AssemblyObject', 'Assembly')
            assert callable(getattr(assembly, 'startSimulation', None)), 'Native asynchronous simulation API missing'
            source = document.addObject('Part::Feature', 'Source')
            source.Shape = Part.makeBox(1, 1, 1)
            group = UtilsAssembly.getJointGroup(assembly)
            for index in range(50):
                link = assembly.newObject('App::Link', 'Component')
                link.setLink(source)
                link.LinkPlacement = App.Placement(App.Vector(index * 2, 0, 0), App.Rotation())
                ground = group.newObject('App::FeaturePython', 'Ground')
                JointObject.GroundedJoint(ground, link)
            simulation = assembly.newObject('App::FeaturePython', 'Simulation')
            Simulation(simulation)
            simulation.aTimeStart = 0
            simulation.bTimeEnd = 0.2
            simulation.cTimeStepOutput = 0.1
            before = time.monotonic()
            superseded = assembly.startSimulation(simulation)
            request = assembly.startSimulation(simulation)
            assert isinstance(request, int) and request != superseded
            assembly.cancelSimulation(superseded)
            try:
                assembly.finishSimulation(superseded)
            except RuntimeError as error:
                assert 'superseded' in str(error)
            else:
                raise AssertionError('Superseded caller consumed the current simulation')
            event('request_ownership_preserved')
            event('submitted', callback_seconds=time.monotonic() - before)
            stage = 'generation'
        elif stage == 'generation':
            if not assembly.finishSimulation(request):
                event('owner_event_loop_alive')
                return
            assert assembly.numberOfFrames() >= 3
            assembly.updateForFrame(1)
            frame_expected = [(obj.Name, obj.ID, obj.Placement) for obj in assembly.Group if obj.isDerivedFrom('App::Link')]
            assembly.updateForFrame(2)
            superseded = assembly.requestSimulationFrame(2)
            frame_request = assembly.requestSimulationFrame(1)
            assembly.cancelSimulationFrame(superseded)
            try:
                assembly.finishSimulationFrame(superseded)
            except RuntimeError as error:
                assert 'superseded' in str(error)
            else:
                raise AssertionError('Superseded frame was adopted')
            stage = 'frame'
        elif stage == 'frame':
            if not assembly.finishSimulationFrame(frame_request):
                event('frame_event_loop_alive')
                return
            for name, object_id, expected in frame_expected:
                current = document.getObject(name)
                assert current.ID == object_id and current.Placement.isSame(expected, 1e-10)
            cancelled = assembly.requestSimulationFrame(2)
            assembly.cancelSimulationFrame(cancelled)
            try:
                assembly.finishSimulationFrame(cancelled)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Cancelled frame was adopted')
            event('parallel_frame_matches_synchronous_pose')
            event('generated', frames=assembly.numberOfFrames())
            assembly.startSimulation(simulation)
            simulation.bTimeEnd = 0.3
            stage = 'invalidated'
        elif stage == 'invalidated':
            try:
                complete = assembly.finishSimulation()
            except RuntimeError:
                event('stale_result_rejected')
            else:
                if not complete:
                    return
                raise AssertionError('Changed simulation input was accepted')
            assembly.startSimulation(simulation)
            assembly.cancelSimulation()
            try:
                assembly.finishSimulation()
            except RuntimeError:
                event('cancelled')
            else:
                raise AssertionError('Cancelled simulation was accepted')
            assembly.startSimulation(simulation)
            App.closeDocument(document.Name)
            document = None
            stage = 'closed'
        elif stage == 'closed':
            report['ok'] = True
            event('complete')
            timer.stop()
            Gui.getMainWindow().close()
    except Exception:
        report['error'] = traceback.format_exc()
        event('failed')
        timer.stop()
        if document is not None:
            App.closeDocument(document.Name)
        Gui.getMainWindow().close()


timer = QtCore.QTimer(Gui.getMainWindow())
timer.timeout.connect(tick)
timer.start(50)
