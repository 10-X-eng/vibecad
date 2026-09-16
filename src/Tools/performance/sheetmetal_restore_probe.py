# SPDX-License-Identifier: LGPL-2.1-or-later
"""Measure saved-sheet display in a fresh, disposable GUI process.

Set VIBECAD_SHEET_BENCHMARK_SOURCE to a saved document and VIBECAD_TEST_OUTPUT
to a private artifact directory, then launch this macro with a private profile.
The probe copies the source; it never saves a document. It measures initial
open, reopen with Fit All while meshes are pending, and another plain reopen.
Screenshots are captured without forcing recompute or changing visibility.
"""

import json
import os
from pathlib import Path
import shutil
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


output = Path(os.environ["VIBECAD_TEST_OUTPUT"])
output.mkdir(parents=True, exist_ok=True)
source = output / "probe-sheet.FCStd"
if App.listDocuments() or source.exists():
    raise RuntimeError("Use a fresh diagnostic process and output directory")
shutil.copy2(os.environ["VIBECAD_SHEET_BENCHMARK_SOURCE"], source)
cycle = 0
document = None
opened = ready_at = None
inventory = None
last_tick = None
maximum_tick_gap = 0


def record(event, **values):
    with (output / "sheet-restore.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "cycle": cycle,
                                 "seconds": time.monotonic()-opened, **values}) + "\n")


def open_copy():
    global document, opened, ready_at, last_tick, maximum_tick_gap
    opened = last_tick = time.monotonic()
    maximum_tick_gap = 0
    ready_at = None
    try:
        document = App.openDocument(str(source))
        record("open_return", objects=len(document.Objects))
        if cycle == 1:
            active = Gui.activeDocument().activeView()
            active.setAnimationEnabled(False)
            active.fitAll()
            record("fit_during_load", camera=active.getCamera())
        last_tick = time.monotonic()
        timer.start(100)
    except Exception:
        fail()


def fail():
    record("failed", traceback=traceback.format_exc())
    timer.stop()
    QtWidgets.QApplication.instance().exit(1)


def tick():
    global cycle, ready_at, last_tick, maximum_tick_gap, inventory
    try:
        now = time.monotonic()
        maximum_tick_gap = max(maximum_tick_gap, now-last_tick)
        last_tick = now
        sheets = [obj for obj in document.Objects if obj.ViewObject
                  and hasattr(getattr(obj.ViewObject, "Proxy", None), "cached_nodes")]
        visible = [obj for obj in sheets if obj.ViewObject.Visibility]
        if visible and all(obj.ViewObject.Proxy.ready for obj in visible):
            if ready_at is None:
                ready_at = now
                current = [(obj.Name, obj.TypeId, list(obj.State), obj.ViewObject.Visibility,
                            obj.PreparedInputHash, len(obj.Shape.Faces), obj.Shape.Volume)
                           for obj in sheets]
                if inventory is not None and current != inventory:
                    raise RuntimeError("Sheet geometry, states or visibility changed on reopen")
                inventory = current
                record("display_ready", visible=[obj.Name for obj in visible],
                       meshed=[obj.Name for obj in sheets if obj.ViewObject.Proxy.cached_nodes],
                       maximum_display_tick_gap=maximum_tick_gap,
                       camera=Gui.activeDocument().activeView().getCamera())
            elif now-ready_at > .5 and document.isClosable():
                timer.stop()
                Gui.activeDocument().activeView().saveImage(
                    str(output / f"sheet-cycle-{cycle}.png"), 1000, 700, "Current")
                App.closeDocument(document.Name)
                cycle += 1
                if cycle < 3:
                    QtCore.QTimer.singleShot(500, open_copy)
                else:
                    record("complete")
                    QtWidgets.QApplication.instance().exit(0)
        elif now-opened > 180:
            # Diagnostic timeout only; no production job is timed out or altered.
            raise RuntimeError("Visible sheets did not become ready within the diagnostic window")
    except Exception:
        fail()


timer = QtCore.QTimer()
timer.timeout.connect(tick)
QtCore.QTimer.singleShot(1500, open_copy)
