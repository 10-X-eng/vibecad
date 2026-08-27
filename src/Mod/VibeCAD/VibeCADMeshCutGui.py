# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for shared retained Mesh cuts and sections."""

from __future__ import annotations

import json
from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshCutJob import make_request, run_mesh_cut
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshCut import (
    create_mesh_cut,
    mesh_cut_still_exact,
    prepare_mesh_cut,
    verify_mesh_cut,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_MeshCutUi"] = {}
_STATUS: dict[str, "_MeshCutStatus"] = {}


def _live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _reference(obj: Any, *, label: str | None = None) -> dict[str, Any]:
    state = mesh_object_state(obj)
    result = {
        "object_name": str(obj.Name),
        "expected_state_sha256": str(state["state_sha256"]),
    }
    if label is not None:
        result["label"] = str(label)
    return result


def _object(document: Any, name: Any, expected: tuple[str, ...]) -> Any:
    obj = document.getObject(str(name or ""))
    if obj is None or not any(
        str(obj.TypeId) == type_id or bool(obj.isDerivedFrom(type_id))
        for type_id in expected
    ):
        raise NativeMeshError("The selected Mesh-cut input is unavailable.")
    return obj


def _human_values(document: Any, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if operation in {"viewport_cut", "viewport_trim"}:
        names = payload.get("targets")
        if not isinstance(names, list) or not names:
            raise NativeMeshError("Select at least one Mesh for the viewport polygon edit.")
        targets = [_object(document, name, ("Mesh::Feature",)) for name in names]
        return {
            "targets": [_reference(target) for target in targets],
            "polygon": payload.get("polygon"),
            "projection_matrix": payload.get("projection_matrix"),
            "mode": str(payload.get("mode") or ""),
        }
    if operation in {"trim_by_plane", "section_by_plane"}:
        target = _object(document, payload.get("target"), ("Mesh::Feature",))
        plane = _object(document, payload.get("plane"), ("Part::Plane",))
        if operation == "trim_by_plane":
            mode = str(payload.get("mode") or "")
            if mode == "keep_below":
                result = {"mode": mode, "result_label": f"{target.Label} Below"}
            elif mode == "keep_above":
                result = {"mode": mode, "result_label": f"{target.Label} Above"}
            elif mode == "split":
                result = {
                    "mode": mode,
                    "below_result_label": f"{target.Label} Below",
                    "above_result_label": f"{target.Label} Above",
                }
            else:
                raise NativeMeshError("The plane-trim side is invalid.")
            return {"target": _reference(target), "plane": _reference(plane), "result": result}
        return {
            "target": _reference(target),
            "plane": _reference(plane),
            "result_label": f"{target.Label} Section",
            "settings": {
                "minimum_length_mm": float(payload.get("minimum_length_mm", 1.0e-7)),
                "connect_edges": bool(payload.get("connect_edges", True)),
            },
        }
    if operation == "cross_sections":
        names = payload.get("targets")
        if not isinstance(names, list) or not names:
            raise NativeMeshError("Select at least one Mesh for cross-sections.")
        targets = [
            _object(document, name, ("Mesh::Feature",))
            for name in names
        ]
        normal = payload.get("normal")
        positions = payload.get("positions_mm")
        if not isinstance(normal, list) or len(normal) != 3 or not isinstance(positions, list):
            raise NativeMeshError("The cross-section planes are invalid.")
        return {
            "targets": [
                _reference(target, label=f"{target.Label} Cross-Sections")
                for target in targets
            ],
            "planes": {
                "normal": {axis: float(value) for axis, value in zip(("x", "y", "z"), normal)},
                "positions_mm": [float(value) for value in positions],
            },
            "settings": {
                "epsilon_mm": float(payload["epsilon_mm"]),
                "connect_edges": bool(payload["connect_edges"]),
            },
        }
    raise NativeMeshError("The requested human Mesh-cut operation is unavailable.")


def _output_names(response: Mapping[str, Any]) -> list[str]:
    names = []
    result = response.get("result")
    if isinstance(result, Mapping) and result.get("object_name"):
        names.append(str(result["object_name"]))
    for value in list(response.get("outputs") or []):
        if isinstance(value, Mapping) and value.get("object_name"):
            names.append(str(value["object_name"]))
    return names


def _commit(document: Any, result: Any) -> Mapping[str, Any]:
    response = run_human_mutation(
        document=document,
        transaction_name={
            "viewport_cut": "Mesh Polygon Cut",
            "viewport_trim": "Mesh Polygon Trim",
            "poly_cut": "Mesh Polygon Cut",
            "poly_trim": "Mesh Polygon Trim",
            "trim_by_plane": "Trim Mesh With Plane",
            "section_by_plane": "Section Mesh With Plane",
            "cross_sections": "Mesh Cross-Sections",
        }[str(result.prepared.operation)],
        mutate=lambda current: create_mesh_cut(current, result.prepared),
        verify=verify_mesh_cut,
    )
    response["background_prepared"] = True
    response["cache_hit"] = bool(result.cache_hit)
    response["output_names"] = _output_names(response)
    return response


class _MeshCutUi:
    def __init__(self, document: Any, prepared: Any, manager: Any) -> None:
        self.document = document
        self.prepared = prepared
        self.manager = manager
        self.job_id = ""
        self.launch_selection = tuple(
            str(obj.Name) for obj in Gui.Selection.getSelection(str(document.Name))
        )
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing Mesh cut", "Cancel", 0, 100, Gui.getMainWindow()
        )
        self.dialog.setWindowTitle("Mesh Cut")
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
            capability_name=f"mesh.cut.{self.prepared.operation}.human",
            prepare=lambda cancelled, progress: run_mesh_cut(
                request, cancelled=cancelled, progress=progress
            ),
            validate_before_commit=self.validate,
            commit=lambda result: _commit(self.document, result),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh cut",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def validate(self) -> None:
        if not _live(self.document):
            raise NativeMeshError("The Mesh-cut document closed while the operation was running.")
        if bool(getattr(self.document, "HasPendingTransaction", False)):
            raise NativeMeshError("Finish the active document operation before publishing the Mesh cut.")
        if not mesh_cut_still_exact(self.document, self.prepared):
            raise NativeMeshError(
                "A Mesh-cut input changed while the operation was running; no stale result was applied.",
                error_code="NATIVE_MESH_STATE_STALE",
            )

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh cut")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self.finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh cut: {snapshot.progress_message}"
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
            if _live(self.document):
                current = tuple(
                    str(obj.Name)
                    for obj in Gui.Selection.getSelection(str(self.document.Name))
                )
                if current == self.launch_selection:
                    Gui.Selection.clearSelection()
                    for name in result.get("output_names", ()):
                        obj = self.document.getObject(str(name))
                        if obj is not None:
                            Gui.Selection.addSelection(obj)
            App.Console.PrintMessage("Mesh cut completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh cut completed", 10000)
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh cut cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh cut cancelled", 10000)
        else:
            clean = str(message or "Mesh cut failed.")
            App.Console.PrintError(clean + "\n")
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Critical,
                "Mesh cut failed",
                clean,
                QtWidgets.QMessageBox.Ok,
                Gui.getMainWindow(),
            )
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            box.open()
        self.dialog.deleteLater()


class _MeshCutStatus:
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
            f"Mesh cut: {snapshot.progress_message}"
        )


def watch_mesh_cut_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _MeshCutStatus(manager, clean).start()


def start_mesh_cut(operation: str, arguments_json: str) -> str:
    try:
        payload = json.loads(str(arguments_json or "{}"))
    except ValueError as exc:
        raise NativeMeshError("The Mesh-cut settings are invalid.") from exc
    if not isinstance(payload, Mapping):
        raise NativeMeshError("The Mesh-cut settings must be an object.")
    document_name = str(payload.get("document") or "")
    try:
        document = App.getDocument(document_name) if document_name else App.ActiveDocument
    except (NameError, RuntimeError):
        document = None
    if document is None or not _live(document):
        raise NativeMeshError("Open a Mesh document before starting a Mesh cut.")
    values = _human_values(document, str(operation), payload)
    prepared = prepare_mesh_cut(document, str(document.Uid), str(operation), values)
    VibeCADGui._ensure_document_thread_invoker()
    try:
        return _MeshCutUi(
            document, prepared, get_service().native_background_manager()
        ).start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc), error_code="NATIVE_MESH_CUT_QUEUE_FAILED"
        ) from exc
