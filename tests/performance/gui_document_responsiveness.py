"""Measure a real VibeCAD GUI document-open and presentation run.

Launch this file through the complete VibeCAD executable, not FreeCADCmd:

    VIBECAD_UI_PROBE_DOCUMENT=/absolute/model.FCStd \
    VIBECAD_UI_PROBE_LOG=/absolute/result.log \
    VibeCAD.exe /absolute/gui_document_responsiveness.py

The GUI remains open after the measurement so the resulting document can be
inspected interactively. The log records main-event-loop gaps together with the
visible status/progress and process-lifetime worker occupancy.
"""

import os
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtGui, QtWidgets


DOCUMENT = os.environ["VIBECAD_UI_PROBE_DOCUMENT"]
LOG = os.environ["VIBECAD_UI_PROBE_LOG"]
STARTED = time.perf_counter()
last_tick = STARTED
maximum_gap_ms = 0.0
tick_count = 0
open_returned = False
open_requested = False
stable_reported = False
idle_ticks = 0


def write(message):
    elapsed = time.perf_counter() - STARTED
    line = f"{elapsed:9.3f}s {message}\n"
    with open(LOG, "a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
    App.Console.PrintMessage("VIBECAD_REAL_UI " + line)


def visible_progress():
    main = Gui.getMainWindow()
    values = []
    for bar in main.findChildren(QtWidgets.QProgressBar):
        if bar.isVisible():
            values.append(f"{bar.value()}/{bar.maximum()} text={bar.text()!r}")
    status = main.statusBar().currentMessage() if main.statusBar() else ""
    return status, values


def heartbeat():
    global last_tick, maximum_gap_ms, tick_count, idle_ticks, stable_reported, open_returned
    now = time.perf_counter()
    gap_ms = (now - last_tick) * 1000.0
    last_tick = now
    tick_count += 1
    maximum_gap_ms = max(maximum_gap_ms, gap_ms)
    status, progress = visible_progress()
    runtime = App.hostRuntimeStatus()

    if open_requested and not open_returned and not App.isRestoring():
        for document in App.listDocuments().values():
            if os.path.normcase(os.path.abspath(document.FileName)) == os.path.normcase(
                os.path.abspath(DOCUMENT)
            ):
                open_returned = True
                write(
                    f"OPEN_RESTORED name={document.Name!r} label={document.Label!r} "
                    f"objects={len(document.Objects)}"
                )
                break

    if gap_ms >= 50.0:
        write(
            f"EVENT_LOOP_GAP gap_ms={gap_ms:.1f} status={status!r} "
            f"progress={progress!r} runtime={runtime!r}"
        )
    if tick_count % 60 == 0:
        write(
            f"HEARTBEAT max_gap_ms={maximum_gap_ms:.1f} "
            f"open_returned={open_returned} status={status!r} "
            f"progress={progress!r} runtime={runtime!r}"
        )

    if open_returned and not progress and not status:
        idle_ticks += 1
    else:
        idle_ticks = 0
    if not stable_reported and idle_ticks >= 120:
        stable_reported = True
        write(
            f"DISPLAY_STABLE max_gap_ms={maximum_gap_ms:.1f} "
            f"ticks={tick_count} runtime={runtime!r}"
        )


def open_document():
    global open_requested
    write(f"OPEN_BEGIN document={DOCUMENT!r}")
    try:
        if any(
            os.path.normcase(os.path.abspath(document.FileName))
            == os.path.normcase(os.path.abspath(DOCUMENT))
            for document in App.listDocuments().values()
        ):
            raise RuntimeError("The probe document is already open; use a fresh test instance")
        # Exercise the actual application file-open event, which selects the
        # queued native GUI workflow. App.openDocument is a synchronous public
        # scripting API and does not measure the interactive open path.
        QtCore.QCoreApplication.postEvent(
            QtWidgets.QApplication.instance(), QtGui.QFileOpenEvent(DOCUMENT)
        )
        open_requested = True
        write("OPEN_QUEUED")
    except Exception:
        write("OPEN_FAILED\n" + traceback.format_exc())


with open(LOG, "w", encoding="utf-8"):
    pass

timer = QtCore.QTimer(Gui.getMainWindow())
timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
timer.setInterval(16)
timer.timeout.connect(heartbeat)
timer.start()

write(f"PROBE_READY runtime={App.hostRuntimeStatus()!r}")
QtCore.QTimer.singleShot(500, open_document)
