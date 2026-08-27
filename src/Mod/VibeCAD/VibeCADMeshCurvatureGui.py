# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared isolated Mesh curvature path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshCurvatureJob import make_request, run_mesh_curvature
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshCurvature import (
    create_mesh_curvature,
    prepare_mesh_curvature,
    verify_mesh_curvature,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_CurvatureUi"] = {}
_STATUS: dict[str, "_CurvatureStatus"] = {}


def _live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, prepared: Any) -> None:
    if not _live(document):
        raise NativeMeshError("The Mesh document closed while curvature was running.")
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError("Finish the active document operation before publishing curvature.")
    if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "A source Mesh changed while curvature was running; no stale result was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _commit(document: Any, result: Any) -> Mapping[str, Any]:
    response = run_human_mutation(
        document=document,
        transaction_name="Calculate Mesh Curvature",
        mutate=lambda current: create_mesh_curvature(current, result.prepared),
        verify=verify_mesh_curvature,
    )
    return {
        "curvature": response,
        "output_names": [item["object_name"] for item in response["results"]],
        "cache_hit": bool(result.cache_hit),
        "background_prepared": True,
    }


class _CurvatureUi:
    def __init__(self, document: Any, prepared: Any, manager: Any) -> None:
        self.document = document
        self.prepared = prepared
        self.manager = manager
        self.job_id = ""
        self.launch_selection = tuple(
            str(obj.Name) for obj in Gui.Selection.getSelection(str(document.Name))
        )
        self.dialog = QtWidgets.QProgressDialog(
            "Calculating Mesh curvature", "Cancel", 0, 100, Gui.getMainWindow()
        )
        self.dialog.setWindowTitle("Mesh Curvature")
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self.dialog)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def start(self) -> str:
        request = make_request(self.prepared)
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name="mesh.curvature.vertex_curvature.human",
            prepare=lambda cancelled, progress: run_mesh_curvature(
                request, cancelled=cancelled, progress=progress
            ),
            validate_before_commit=lambda: _validate(self.document, self.prepared),
            commit=lambda result: _commit(self.document, result),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh curvature",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh curvature")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self.finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh curvature: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            error = dict(snapshot.error or {})
            self.finish(
                str(snapshot.phase),
                str(error.get("message") or snapshot.progress_message),
                snapshot.result,
            )

    def finish(self, phase: str, message: str, result: Mapping[str, Any] | None) -> None:
        self.timer.stop()
        self.dialog.close()
        _ACTIVE.pop(self.job_id, None)
        if phase == "completed" and result is not None:
            current = tuple(
                str(obj.Name)
                for obj in Gui.Selection.getSelection(str(self.document.Name))
            ) if _live(self.document) else ()
            if current == self.launch_selection:
                Gui.Selection.clearSelection()
                for name in result.get("output_names", ()):
                    obj = self.document.getObject(str(name))
                    if obj is not None:
                        Gui.Selection.addSelection(obj)
            App.Console.PrintMessage("Mesh curvature completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh curvature completed", 10000)
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh curvature cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh curvature cancelled", 10000)
        else:
            clean = str(message or "Mesh curvature failed.")
            App.Console.PrintError(clean + "\n")
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Critical,
                "Mesh curvature failed",
                clean,
                QtWidgets.QMessageBox.Ok,
                Gui.getMainWindow(),
            )
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            box.open()
        self.dialog.deleteLater()


class _CurvatureStatus:
    def __init__(self, manager: Any, job_id: str) -> None:
        self.manager = manager
        self.job_id = job_id
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
            snapshot = None
        if snapshot is None or snapshot.terminal:
            self.timer.stop()
            _STATUS.pop(self.job_id, None)
            self.timer.deleteLater()
            return
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh curvature: {snapshot.progress_message}"
        )


def watch_mesh_curvature_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _CurvatureStatus(manager, clean).start()


def start_mesh_curvature(sources: Sequence[Any]) -> str:
    meshes = tuple(sources)
    if not meshes:
        raise NativeMeshError("Select at least one non-empty Mesh.")
    document = getattr(meshes[0], "Document", None)
    if document is None or any(getattr(mesh, "Document", None) is not document for mesh in meshes):
        raise NativeMeshError("Every Mesh curvature source must belong to one document.")
    targets = []
    for mesh in meshes:
        state = mesh_object_state(mesh)
        targets.append(
            {
                "object_name": str(mesh.Name),
                "expected_state_sha256": str(state["state_sha256"]),
                "label": f"{str(mesh.Label)} Curvature",
            }
        )
    prepared = prepare_mesh_curvature(document, str(document.Uid), {"targets": targets})
    VibeCADGui._ensure_document_thread_invoker()
    try:
        return _CurvatureUi(
            document, prepared, get_service().native_background_manager()
        ).start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc), error_code="NATIVE_MESH_CURVATURE_QUEUE_FAILED"
        ) from exc
