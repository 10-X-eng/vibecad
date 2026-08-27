# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared process-isolated Mesh boolean path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshBooleanJob import run_mesh_boolean
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshBoolean import (
    capture_mesh_boolean,
    commit_prepared_mesh_boolean,
    verify_prepared_mesh_boolean,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_MeshBooleanUi"] = {}
_STATUS: dict[str, "_MeshBooleanStatusUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, request: Any) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The Mesh document closed while the boolean was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing the Mesh boolean.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )
    if not mesh_target_still_exact(
        document, request.first
    ) or not mesh_target_still_exact(document, request.second):
        raise NativeMeshError(
            "A source Mesh changed while its boolean was running; no stale result was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _commit(document: Any, prepared: Any) -> Mapping[str, Any]:
    result = run_human_mutation(
        document=document,
        transaction_name=f"Mesh {prepared.request.operation.title()}",
        mutate=lambda exact_document: commit_prepared_mesh_boolean(
            exact_document, prepared
        ),
        verify=verify_prepared_mesh_boolean,
    )
    return {
        "boolean": result,
        "output_name": str(result["result"]["object_name"]),
    }


class _MeshBooleanUi:
    def __init__(self, document: Any, request: Any, manager: Any) -> None:
        self.document = document
        self.request = request
        self.manager = manager
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing Mesh boolean",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Mesh Boolean")
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self.dialog)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def start(self) -> str:
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name="mesh.boolean.human",
            prepare=lambda cancelled, progress: run_mesh_boolean(
                self.request,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: _validate(self.document, self.request),
            commit=lambda prepared: _commit(self.document, prepared),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh boolean",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh boolean")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh boolean: {snapshot.progress_message}"
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
            output = (
                self.document.getObject(str(result.get("output_name") or ""))
                if _document_is_live(self.document)
                else None
            )
            Gui.Selection.clearSelection()
            if output is not None:
                Gui.Selection.addSelection(output)
            App.Console.PrintMessage("Mesh boolean completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage(
                "Mesh boolean completed",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh boolean cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh boolean cancelled", 10000)
        else:
            clean = str(message or "Mesh boolean failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Mesh boolean failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage("Mesh boolean failed", 10000)
        self.dialog.deleteLater()


class _MeshBooleanStatusUi:
    def __init__(self, manager: Any, job_id: str) -> None:
        self.manager = manager
        self.job_id = str(job_id)
        self.timer = QtCore.QTimer(Gui.getMainWindow())
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)

    def start(self) -> None:
        _STATUS[self.job_id] = self
        self.poll()
        if self.job_id in _STATUS:
            self.timer.start()

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception:
            self._finish("failed")
            return
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh boolean: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            self._finish(str(snapshot.phase))

    def _finish(self, phase: str) -> None:
        self.timer.stop()
        _STATUS.pop(self.job_id, None)
        message = {
            "completed": "Mesh boolean completed",
            "cancelled": "Mesh boolean cancelled",
        }.get(phase, "Mesh boolean failed")
        Gui.getMainWindow().statusBar().showMessage(message, 10000)
        self.timer.deleteLater()


def watch_mesh_boolean_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _MeshBooleanStatusUi(manager, clean).start()


def start_mesh_boolean(
    sources: Sequence[Any],
    operation: str,
    result_label: str,
) -> str:
    meshes = tuple(sources)
    if len(meshes) != 2 or meshes[0] is meshes[1]:
        raise NativeMeshError("Select exactly two different Meshes for a solid boolean.")
    document = getattr(meshes[0], "Document", None)
    if document is None or getattr(meshes[1], "Document", None) is not document:
        raise NativeMeshError("Both Mesh boolean sources must belong to one active document.")
    native_operation = str(operation or "").strip().lower()
    if native_operation not in {"union", "intersection", "difference"}:
        raise NativeMeshError("Mesh boolean operation must be union, intersection, or difference.")
    values = {
        "first": {
            "object_name": str(meshes[0].Name),
            "expected_state_sha256": str(mesh_object_state(meshes[0])["state_sha256"]),
        },
        "second": {
            "object_name": str(meshes[1].Name),
            "expected_state_sha256": str(mesh_object_state(meshes[1])["state_sha256"]),
        },
        "result_label": str(result_label),
    }
    request = capture_mesh_boolean(
        document,
        str(document.Uid),
        native_operation,
        values,
    )
    VibeCADGui._ensure_document_thread_invoker()
    runner = _MeshBooleanUi(
        document,
        request,
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc),
            error_code="NATIVE_MESH_BOOLEAN_QUEUE_FAILED",
        ) from exc
