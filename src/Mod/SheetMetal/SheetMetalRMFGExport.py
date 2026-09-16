# SPDX-License-Identifier: LGPL-2.1-or-later
"""Asynchronous, revision-checked folded export shared by RMFG UI and tools."""

from concurrent.futures import Future
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from PySide import QtCore

import SheetMetalEditable as Editable
import SheetMetalOperations as Operations
from SheetMetalPresentation import _gui_thread
from SheetMetalRMFGClient import MAX_STEP_BYTES
from SheetMetalRMFGSnapshot import ExportSnapshot


def export_folded(shape, revision, executable):
    """Detached worker work. The caller checks document currency after completion."""
    child = Path(__file__).with_name("SheetMetalRMFGExportChild.py").resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="vibecad-rmfg-export-") as directory:
        workspace = Path(directory)
        source = workspace/"folded.brep"
        shape.exportBrepDetached(str(source))
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        (workspace/"request.json").write_text(json.dumps({
            "schema": "vibecad-rmfg-step-v1", "brep_sha256": digest.hexdigest()}))
        environment = os.environ.copy()
        for variable, name in (("FREECAD_USER_HOME", "home"), ("FREECAD_USER_DATA", "data"),
                               ("FREECAD_USER_TEMP", "tmp"), ("TMPDIR", "tmp"),
                               ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")):
            path = workspace/name
            path.mkdir(exist_ok=True)
            environment[variable] = str(path)
        console = (f"import runpy,sys; sys.argv=[{str(child)!r},{str(workspace)!r}]; "
                   f"worker=runpy.run_path({str(child)!r},run_name='__main__')\n")
        # A dedicated process isolates STEP's global settings. No process
        # deadline or kill timer; closing a consumer only discards publication.
        with (workspace/"worker.log").open("wb") as log:
            process = subprocess.run([str(executable), "-u", str(workspace/"user.cfg"),
                "-s", str(workspace/"system.cfg"), "-c"], input=console.encode("utf-8"),
                stdout=log, stderr=subprocess.STDOUT, cwd=workspace, env=environment,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0)
        result_path = workspace/"result.json"
        if not result_path.is_file() or result_path.stat().st_size > 4096:
            raise RuntimeError("The isolated folded STEP writer returned no usable result")
        result = json.loads(result_path.read_text())
        if process.returncode != 0 or result.get("schema") != "vibecad-rmfg-step-v1" or result.get("ok") is not True:
            raise RuntimeError("The isolated folded STEP writer could not validate the export")
        with (workspace/"folded.step").open("rb") as stream:
            data = stream.read(MAX_STEP_BYTES+1)
        if hashlib.sha256(data).hexdigest() != result.get("step_sha256"):
            raise RuntimeError("The STEP artifact changed after validation")
        return ExportSnapshot(revision, data)


class _Completion(QtCore.QObject):
    ready = QtCore.Signal(object)


class ExportRun(QtCore.QObject):
    def __init__(self, sheet, revision, shape, executable, exporter):
        super().__init__()
        self.future = Future()
        self.finished = False
        self._sheet, self._revision = sheet, revision
        self._completion = completion = _Completion()
        completion.ready.connect(self._finish, QtCore.Qt.QueuedConnection)
        def work():
            try:
                outcome = exporter(shape, revision, executable), None
            except Exception as error:
                outcome = None, error
            try:
                completion.ready.emit(outcome)
            except RuntimeError:
                pass  # Application exit destroyed the receiver.
        self._worker = threading.Thread(target=work, name="RMFG-folded-export", daemon=True)
        self._worker.start()

    def _finish(self, outcome):
        _gui_thread()
        self.finished = True
        if not self.future.set_running_or_notify_cancel():
            return
        value, error = outcome
        try:
            current = Operations.capture_revision(self._sheet).summary()
            Editable.get_state_geometry(self._sheet)
            if current != self._revision:
                raise RuntimeError("The sheet revision changed during export; export its current state")
            if error is not None:
                raise error
            if not isinstance(value, ExportSnapshot) or not value.matches_revision(current):
                raise RuntimeError("The export does not match the current sheet revision")
        except Exception as error:
            self.future.set_exception(error)
        else:
            self.future.set_result(value)


def start_export(sheet, *, expected_revision, exporter=None):
    """Capture the exact prepared folded state on its GUI owner and queue export."""
    _gui_thread()
    revision = Operations.capture_revision(sheet).summary()
    expected = (expected_revision.summary() if isinstance(expected_revision, Operations.SheetRevision)
                else expected_revision)
    if revision != expected:
        raise RuntimeError("The sheet revision changed; inspect its current state before export")
    geometry = Editable.get_state_geometry(sheet)
    # Copy the prepared folded solid, irrespective of the active display mode.
    # The worker never receives a document, feature, view, or selected object.
    shape = geometry.folded.copy()
    from VibeCADIsolatedMeshWorker import freecadcmd_path
    executable = freecadcmd_path()
    return ExportRun(sheet, revision, shape, executable, export_folded if exporter is None else exporter)


class PreparedExportRun:
    """Keep preparation and STEP export bound to the same original revision."""

    def __init__(self, sheet, revision, exporter):
        from SheetMetalPreparation import start_preparation
        self.future, self.finished = Future(), False
        self._sheet, self._revision, self._exporter = sheet, revision, exporter
        self._export_run = None
        self._preparation_run = self._pending = start_preparation(sheet, expected_revision=revision)
        self.future.add_done_callback(self._cancel_pending)
        self._preparation_run.future.add_done_callback(self._prepared)

    def _cancel_pending(self, future):
        if future.cancelled():
            self.finished = True
            self._pending.future.cancel()

    def _prepared(self, future):
        _gui_thread()
        if self.future.cancelled():
            return
        try:
            future.result()
            # The original exporter rechecks revision and geometry before any
            # STEP work starts; do not silently recapture a newer revision.
            self._export_run = self._pending = start_export(
                self._sheet, expected_revision=self._revision, exporter=self._exporter)
            if self.future.cancelled():
                self._export_run.future.cancel()
            self._export_run.future.add_done_callback(self._exported)
        except Exception as error:
            self._finish(None, error)

    def _exported(self, future):
        _gui_thread()
        try:
            value, error = future.result(), None
        except Exception as failure:
            value, error = None, failure
        self._finish(value, error)

    def _finish(self, value, error):
        self.finished = True
        if not self.future.set_running_or_notify_cancel():
            return
        if error is not None:
            self.future.set_exception(error)
        else:
            self.future.set_result(value)


def start_prepared_export(sheet, *, expected_revision, exporter=None):
    """Prepare a restored mapping when needed, then export without model edits.

    Existing prepared states retain the original export path. Keep the returned
    run alive; cancelling its future discards publication at either stage.
    """
    _gui_thread()
    revision = Operations.capture_revision(sheet).summary()
    expected = (expected_revision.summary() if isinstance(expected_revision, Operations.SheetRevision)
                else expected_revision)
    if revision != expected:
        raise RuntimeError("The sheet revision changed; inspect its current state before export")
    try:
        Editable.get_state_geometry(sheet)
    except (RuntimeError, ValueError):
        return PreparedExportRun(sheet, revision, exporter)
    return start_export(sheet, expected_revision=revision, exporter=exporter)
