"""Exercise native event attribution in a fresh diagnostic GUI process."""
import json
import os
from pathlib import Path
import time
import traceback

import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets


class TraceReceiver(QtCore.QObject):
    def event(self, event):
        if event.type() == QtCore.QEvent.Type.User:
            time.sleep(0.02)  # Deliberate diagnostic event, not production work.
            return True
        return super().event(event)


def run():
    result = {'ok': False}
    try:
        app = QtWidgets.QApplication.instance()
        def drain():
            return QtCore.QMetaObject.invokeMethod(
                app, 'takePerformanceEvents', QtCore.Qt.ConnectionType.DirectConnection,
                QtCore.Q_RETURN_ARG('QVariantMap'))
        drain()
        receiver = TraceReceiver()
        receiver.setObjectName('performance_trace_contract')
        QtCore.QCoreApplication.sendEvent(receiver, QtCore.QEvent(QtCore.QEvent.Type.User))
        trace = drain()
        records = [item for item in trace['events']
                   if item['receiver_name'] == 'performance_trace_contract']
        assert trace['enabled'] and trace['dropped'] == 0, trace
        assert len(records) == 1 and records[0]['elapsed_ms'] >= 15, records
        assert records[0]['event_type'] == int(QtCore.QEvent.Type.User), records
        assert not drain()['events'], 'Drain retained already-consumed records'
        QtCore.QMetaObject.invokeMethod(
            app, 'recordPerformancePhase', QtCore.Qt.ConnectionType.DirectConnection,
            QtCore.Q_ARG(str, 'diagnostic_phase'), QtCore.Q_ARG('qint64', 20_000_000))
        phases = drain()['events']
        assert len(phases) == 1 and phases[0]['phase'] == 'diagnostic_phase', phases
        assert phases[0]['elapsed_ms'] == 20, phases
        assert not drain()['events'], 'Drain retained already-consumed phases'
        result = {'ok': True, 'record': records[0], 'phase': phases[0]}
    except Exception:
        result['error'] = traceback.format_exc()
    Path(os.environ['VIBECAD_TRACE_PROBE_RESULT']).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('VIBECAD_EVENT_TRACE_PROBE ' + json.dumps(result), flush=True)
    QtCore.QTimer.singleShot(0, Gui.getMainWindow().close)


QtCore.QTimer.singleShot(0, run)
