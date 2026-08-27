# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real ribbon-command gate for exact non-blocking Mesh export."""

from __future__ import annotations

from pathlib import Path
import hashlib
import struct
import sys
import tempfile
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeMeshState import mesh_geometry_sha256


def _process_events(rounds: int = 4) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _tetrahedron():
    a = App.Vector(0.0, 0.0, 0.0)
    b = App.Vector(8.0, 0.0, 0.0)
    c = App.Vector(0.0, 7.0, 0.0)
    d = App.Vector(0.0, 0.0, 6.0)
    return Mesh.Mesh([(a, c, b), (a, b, d), (b, c, d), (c, a, d)])


def _legacy_geometry_sha256(mesh) -> str:
    points, facets = mesh.Topology
    digest = hashlib.sha256()
    digest.update(struct.pack("!QQ", len(points), len(facets)))
    for point in points:
        digest.update(struct.pack("!ddd", float(point.x), float(point.y), float(point.z)))
    for facet in facets:
        digest.update(struct.pack("!QQQ", *(int(index) for index in facet)))
    segment_count = int(mesh.countSegments())
    digest.update(struct.pack("!Q", segment_count))
    for index in range(segment_count):
        segment = tuple(int(value) for value in mesh.getSegment(index))
        digest.update(struct.pack("!Q", len(segment)))
        for facet_index in segment:
            digest.update(struct.pack("!Q", facet_index))
    return digest.hexdigest()


def _large_grid(columns: int = 150, rows: int = 150):
    facets = []
    for row in range(rows):
        y0 = float(row)
        y1 = float(row + 1)
        for column in range(columns):
            x0 = float(column)
            x1 = float(column + 1)
            lower_left = App.Vector(x0, y0, 0.0)
            lower_right = App.Vector(x1, y0, 0.0)
            upper_left = App.Vector(x0, y1, 0.0)
            upper_right = App.Vector(x1, y1, 0.0)
            facets.append((lower_left, lower_right, upper_right))
            facets.append((lower_left, upper_right, upper_left))
    return Mesh.Mesh(facets)


def _accept_file(path: Path, suffix: str) -> None:
    attempts = {"remaining": 2000}

    def confirm_overwrite(remaining: int = 200) -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QMessageBox) or not widget.isVisible():
                continue
            button = widget.button(QtWidgets.QMessageBox.Yes)
            (button.click if button is not None else widget.accept)()
            return
        if remaining > 0:
            QtCore.QTimer.singleShot(5, lambda: confirm_overwrite(remaining - 1))

    def accept() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QFileDialog) or not widget.isVisible():
                continue
            widget.setDirectory(str(path.parent))
            for name_filter in widget.nameFilters():
                if f"*.{suffix}" in name_filter.casefold():
                    widget.selectNameFilter(name_filter)
                    break
            line_edit = widget.findChild(QtWidgets.QLineEdit, "fileNameEdit")
            if line_edit is None:
                continue
            line_edit.setText(path.name)
            if path.exists():
                QtCore.QTimer.singleShot(0, confirm_overwrite)
            widget.accept()
            return
        attempts["remaining"] -= 1
        if attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, accept)

    QtCore.QTimer.singleShot(0, accept)


def _select(source) -> None:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    _process_events(2)


def _latest(document):
    return get_service().native_background_manager().latest_document_snapshot(
        str(document.Uid),
        capability_prefix="mesh.export.export_mesh.human",
    )


def _wait(document, job_id: str, *, timeout: float = 90.0):
    manager = get_service().native_background_manager()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _process_events(2)
        snapshot = manager.snapshot(job_id)
        if snapshot.terminal:
            _process_events(8)
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"Background Mesh export {job_id} did not finish")


def _run() -> None:
    document = None
    temporary = None
    preference = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
    native_dialog_before = preference.GetBool("DontUseNativeDialog", False)
    exit_code = 1
    try:
        Gui.activateWorkbench("MeshWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-mesh-export-command-")
        root = Path(temporary.name)
        document = App.newDocument("MeshExportCommandGate")
        document.UndoMode = 1
        document.saveAs(str(root / "mesh-export-command.FCStd"))
        preference.SetBool("DontUseNativeDialog", True)

        source = document.addObject("Mesh::Feature", "ExportSource")
        source.Label = "Placed export source"
        source.Mesh = _tetrahedron()
        source.Placement.Base = App.Vector(100.0, 20.0, 30.0)
        source.addProperty("App::PropertyColorList", "FaceColors")
        source.FaceColors = [
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 1.0, 0.0),
        ]
        document.recompute()
        assert mesh_geometry_sha256(source.Mesh) == _legacy_geometry_sha256(source.Mesh)
        digest_probe = source.Mesh.copy()
        digest_probe.Placement = App.Placement(
            App.Vector(3.0, -2.0, 5.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 17.0),
        )
        digest_probe.addSegment([0, 2])
        assert mesh_geometry_sha256(digest_probe) == _legacy_geometry_sha256(digest_probe)
        document.save()
        undo_before = int(document.UndoCount)

        success_path = root / "placed.ply"
        _select(source)
        _accept_file(success_path, "ply")
        started_at = time.monotonic()
        Gui.runCommand("Mesh_Export", 0)
        dispatch_seconds = time.monotonic() - started_at
        assert dispatch_seconds < 2.0, dispatch_seconds
        started = _latest(document)
        assert started is not None
        completed = _wait(document, started.job_id)
        assert completed.phase == "completed", completed.error
        assert success_path.is_file()
        exported = Mesh.read(str(success_path))
        assert exported.CountFacets == source.Mesh.CountFacets
        bounds = exported.BoundBox
        assert abs(bounds.XMin - 100.0) < 1e-6
        assert abs(bounds.YMin - 20.0) < 1e-6
        assert abs(bounds.ZMin - 30.0) < 1e-6
        result_source = dict((completed.result or {}).get("source") or {})
        assert result_source.get("color_binding") == "per_face", result_source
        assert int(document.UndoCount) == undo_before

        large = document.addObject("Mesh::Feature", "LargeExportSource")
        large.Mesh = _large_grid()
        document.recompute()
        hash_started = time.monotonic()
        geometry_sha256 = mesh_geometry_sha256(large.Mesh)
        hash_seconds = time.monotonic() - hash_started
        assert len(geometry_sha256) == 64
        assert hash_seconds < 0.05, hash_seconds
        document.save()

        cancelled_path = root / "cancelled.ply"
        _select(large)
        _accept_file(cancelled_path, "ply")
        started_at = time.monotonic()
        Gui.runCommand("Mesh_Export", 0)
        cancel_dispatch_seconds = time.monotonic() - started_at
        assert cancel_dispatch_seconds < 2.0, cancel_dispatch_seconds
        cancel_started = _latest(document)
        assert cancel_started is not None and cancel_started.job_id != started.job_id
        assert get_service().native_background_manager().cancel(cancel_started.job_id)
        cancelled = _wait(document, cancel_started.job_id)
        assert cancelled.phase == "cancelled", cancelled
        assert not cancelled_path.exists()

        stale_path = root / "stale.ply"
        stale_path.write_bytes(b"original destination")
        _select(large)
        _accept_file(stale_path, "ply")
        Gui.runCommand("Mesh_Export", 0)
        stale_started = _latest(document)
        assert stale_started is not None and stale_started.job_id != cancel_started.job_id
        large.Placement.Base.x += 1.0
        document.recompute()
        stale = _wait(document, stale_started.job_id)
        assert stale.phase == "failed", stale
        assert stale_path.read_bytes() == b"original destination"
        error = dict(stale.error or {})
        assert error.get("error_code") == "NATIVE_MESH_STATE_STALE", error

        print(
            "VIBECAD_MESH_EXPORT_COMMAND_GUI_OK "
            f"dispatch_ms={int(dispatch_seconds * 1000)} "
            f"cancel_dispatch_ms={int(cancel_dispatch_seconds * 1000)} "
            f"hash_ms={int(hash_seconds * 1000)} placement=true colors=per_face "
            "cancelled=true stale_preserved=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        preference.SetBool("DontUseNativeDialog", native_dialog_before)
        Gui.Selection.clearSelection()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        QtCore.QTimer.singleShot(0, lambda: QtWidgets.QApplication.exit(exit_code))


QtCore.QTimer.singleShot(0, _run)
