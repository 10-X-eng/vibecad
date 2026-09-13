# SPDX-License-Identifier: LGPL-2.1-or-later
"""Check live native status text while a background step has no new percentage."""

import json
import os
from pathlib import Path
import threading
import time

import FreeCAD as App
import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets

output = Path(os.environ["VIBECAD_TRACE_PROBE_RESULT"])
main = Gui.getMainWindow()
status_label = main.statusBar().findChild(QtWidgets.QLabel, "actionLabel")
assert status_label is not None, "Native status-bar activity label is missing"
release = threading.Event()
started = threading.Event()
finished = threading.Event()
report = {"ok": False, "messages": []}


def work():
    progress = App.Base.ProgressIndicator()
    try:
        progress.start("Background progress diagnostic", 10)
        started.set()
        release.wait()
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        progress.stop()
        finished.set()


worker = threading.Thread(target=work)
begin = None


def start_probe():
    global begin
    # Leave command-line file processing first: its outer progress scope would
    # otherwise swallow this deliberately nested diagnostic scope.
    begin = time.monotonic()
    worker.start()


def tick():
    if begin is None:
        return
    elapsed = time.monotonic() - begin
    message = status_label.text()
    report["messages"].append({"seconds": elapsed, "text": message})
    if not release.is_set() and elapsed >= 3:
        report["ok"] = (started.is_set() and "Background progress diagnostic" in message
                        and "Elapsed:" in message and "0 / 10" in message)
        if not report["ok"]:
            report.setdefault("error", "Missing live elapsed/count status before first completed step")
        release.set()
    if finished.is_set():
        timer.stop()
        worker.join()
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PROGRESS_STATUS " + json.dumps(report), flush=True)
        main.close()


timer = QtCore.QTimer(main)
timer.setInterval(250)
timer.timeout.connect(tick)
timer.start()
QtCore.QTimer.singleShot(0, start_probe)
