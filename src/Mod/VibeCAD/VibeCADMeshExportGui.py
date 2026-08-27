# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for exact Mesh export."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshExport import (
    CapturedMeshExport,
    capture_mesh_export,
    human_mesh_export_format,
    mesh_export_request,
    mesh_export_source_still_exact,
    prepare_mesh_export,
)
from VibeCADNativeOutput import NativeOutputError, authorize_native_output_path
import VibeCADGui


_ACTIVE: dict[str, "_MeshExportUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(
    document: Any,
    captured: CapturedMeshExport,
    expected_revision: int,
) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The Mesh document closed while export was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing the Mesh output.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )
    state = get_service().native_document_state_store()
    if state.current_revision(str(document.Uid)) != expected_revision or not (
        mesh_export_source_still_exact(document, captured)
    ):
        raise NativeMeshError(
            "The source Mesh changed while export was running; no stale file was published.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


class _MeshExportUi:
    def __init__(
        self,
        document: Any,
        captured: CapturedMeshExport,
        request: Any,
        authorization: Any,
        expected_revision: int,
        manager: Any,
    ) -> None:
        self.document = document
        self.captured = captured
        self.request = request
        self.authorization = authorization
        self.expected_revision = int(expected_revision)
        self.manager = manager
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing Mesh export",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Export Mesh")
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.setValue(0)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self.dialog)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def start(self) -> str:
        validate = lambda: _validate(
            self.document,
            self.captured,
            self.expected_revision,
        )
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name="mesh.export.export_mesh.human",
            prepare=lambda cancelled, progress: prepare_mesh_export(
                self.captured,
                self.request,
                self.authorization,
                cancelled=cancelled,
                progress=progress,
                guard=lambda: VibeCADGui._dispatch_to_document_thread(validate),
            ),
            validate_before_commit=validate,
            commit=lambda prepared: prepared,
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            changes_document=False,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh export")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh export: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            error = dict(snapshot.error or {})
            self._finish(
                str(snapshot.phase),
                str(error.get("message") or snapshot.progress_message),
                snapshot.result,
            )

    def _finish(
        self,
        phase: str,
        message: str,
        result: Mapping[str, Any] | None,
    ) -> None:
        self.timer.stop()
        self.dialog.close()
        _ACTIVE.pop(self.job_id, None)
        if phase == "completed" and result is not None:
            output = dict(result.get("output") or {})
            file_name = str(output.get("file_name") or "Mesh file")
            App.Console.PrintMessage(f"Exported {file_name} in the background.\n")
            Gui.getMainWindow().statusBar().showMessage(
                f"Exported {file_name}",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh export cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh export cancelled", 10000)
        else:
            clean = str(message or "Mesh export failed.")
            App.Console.PrintError(clean + "\n")
            notice = QtWidgets.QMessageBox(Gui.getMainWindow())
            notice.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            notice.setIcon(QtWidgets.QMessageBox.Critical)
            notice.setWindowTitle("Mesh export failed")
            notice.setText(clean)
            notice.setStandardButtons(QtWidgets.QMessageBox.Ok)
            notice.open()
            Gui.getMainWindow().statusBar().showMessage("Mesh export failed", 10000)
        self.dialog.deleteLater()


def start_mesh_export(source: Any, path: str, format_code: str) -> str:
    document = getattr(source, "Document", None)
    if document is None:
        raise NativeMeshError("The selected Mesh document is unavailable.")
    clean_path = str(path or "")
    format_value = human_mesh_export_format(format_code, clean_path)
    if not Path(clean_path).suffix:
        clean_path += format_value.suggested_suffix
    captured = capture_mesh_export(
        document,
        source,
        expected_state_sha256=None,
        format_value=format_value,
    )
    request = mesh_export_request(
        captured.label,
        format_value,
        selected_suffix=Path(clean_path).suffix,
    )
    try:
        authorization = authorize_native_output_path(request, clean_path)
    except NativeOutputError as exc:
        raise NativeMeshError(str(exc), error_code=exc.code) from exc
    VibeCADGui._ensure_document_thread_invoker()
    state = get_service().native_document_state_store()
    runner = _MeshExportUi(
        document,
        captured,
        request,
        authorization,
        state.current_revision(str(document.Uid)),
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc),
            error_code="NATIVE_MESH_EXPORT_QUEUE_FAILED",
        ) from exc
