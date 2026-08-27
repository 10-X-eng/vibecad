# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for shared isolated Mesh segmentation analysis."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshSegmentationJob import make_request, run_mesh_segmentation
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshSegment import create_mesh_segment, verify_mesh_segment
from VibeCADNativeMeshSegments import capture_background_mesh_segment
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_MeshSegmentationUi"] = {}
_STATUS: dict[str, "_MeshSegmentationStatusUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, captured: Any) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The Mesh document closed while segmentation was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing Mesh segments.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )
    if not all(
        mesh_target_still_exact(document, target) for target in captured.targets
    ):
        raise NativeMeshError(
            "A source Mesh changed while segmentation was running; no stale result was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _commit(document: Any, result: Any) -> Mapping[str, Any]:
    prepared = result.prepared
    response = run_human_mutation(
        document=document,
        transaction_name={
            "merge": "Merge Meshes",
            "split_components": "Split Mesh Components",
            "mesh_segmentation": "Segment Mesh by Curvature",
            "segmentation_best_fit": "Segment Mesh by Best Fit",
            "reverse_segmentation": "Segment Mesh by Planar Surfaces",
            "segmentation_manual": "Segment Selected Mesh Facets",
            "segmentation_from_components": "Segment Mesh Components",
            "mesh_boundary": "Create Mesh Boundaries",
        }[prepared.operation],
        mutate=lambda current: create_mesh_segment(current, prepared),
        verify=verify_mesh_segment,
    )
    output_names = [item["object_name"] for item in response.get("results", ())]
    controller = response.get("operation_controller")
    if isinstance(controller, Mapping):
        output_names.append(str(controller.get("object_name") or ""))
    return {
        "segmentation": response,
        "output_names": [name for name in output_names if name],
        "cache_hit": bool(result.cache_hit),
        "background_prepared": True,
    }


class _MeshSegmentationUi:
    def __init__(self, document: Any, captured: Any, manager: Any) -> None:
        self.document = document
        self.captured = captured
        self.manager = manager
        self.job_id = ""
        self.launch_selection = tuple(
            str(obj.Name) for obj in Gui.Selection.getSelection(str(document.Name))
        )
        self.dialog = QtWidgets.QProgressDialog(
            "Analyzing Mesh segments",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Mesh Segmentation")
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self.dialog)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def start(self) -> str:
        request = make_request(self.captured)
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name=f"mesh.segment.{self.captured.operation}.human",
            prepare=lambda cancelled, progress: run_mesh_segmentation(
                request,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: _validate(self.document, self.captured),
            commit=lambda result: _commit(self.document, result),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh segments",
            changes_document=True,
            document_change_resolver=lambda result: bool(
                result["segmentation"].get("changed", True)
            ),
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh segmentation")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh segmentation: {snapshot.progress_message}"
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
            output_names = list(result.get("output_names") or [])
            current_selection = tuple(
                str(obj.Name)
                for obj in Gui.Selection.getSelection(str(self.document.Name))
            ) if _document_is_live(self.document) else ()
            if output_names and current_selection == self.launch_selection:
                Gui.Selection.clearSelection()
                for name in output_names:
                    obj = self.document.getObject(str(name))
                    if obj is not None:
                        Gui.Selection.addSelection(obj)
            App.Console.PrintMessage("Mesh segmentation completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage(
                "Mesh segmentation completed",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh segmentation cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage(
                "Mesh segmentation cancelled",
                10000,
            )
        else:
            clean = str(message or "Mesh segmentation failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Mesh segmentation failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage(
                "Mesh segmentation failed",
                10000,
            )
        self.dialog.deleteLater()


class _MeshSegmentationStatusUi:
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
            f"Mesh segmentation: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            self._finish(str(snapshot.phase))

    def _finish(self, phase: str) -> None:
        self.timer.stop()
        _STATUS.pop(self.job_id, None)
        message = {
            "completed": "Mesh segmentation completed",
            "cancelled": "Mesh segmentation cancelled",
        }.get(phase, "Mesh segmentation failed")
        Gui.getMainWindow().statusBar().showMessage(message, 10000)
        self.timer.deleteLater()


def watch_mesh_segmentation_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _MeshSegmentationStatusUi(manager, clean).start()


def start_mesh_segmentation(
    sources: Sequence[Any],
    operation: str,
    arguments_json: str,
) -> str:
    meshes = tuple(sources)
    if not meshes:
        raise NativeMeshError("Select at least one Mesh to segment.")
    document = getattr(meshes[0], "Document", None)
    if document is None or any(
        getattr(mesh, "Document", None) is not document for mesh in meshes
    ):
        raise NativeMeshError("Every Mesh segmentation source must belong to one document.")
    try:
        settings = json.loads(str(arguments_json or "{}"))
    except ValueError as exc:
        raise NativeMeshError("Mesh segmentation settings are invalid.") from exc
    if not isinstance(settings, dict):
        raise NativeMeshError("Mesh segmentation settings must be an object.")
    exact = [
        {
            "object_name": str(mesh.Name),
            "expected_state_sha256": str(mesh_object_state(mesh)["state_sha256"]),
        }
        for mesh in meshes
    ]
    native_operation = str(operation or "").strip()
    values = dict(settings)
    if native_operation == "merge":
        values["sources"] = exact
    elif native_operation == "mesh_boundary":
        values["targets"] = [
            {
                **target,
                "label": f"{str(mesh.Label)} Boundary",
            }
            for target, mesh in zip(exact, meshes)
        ]
    elif native_operation == "segmentation_from_components":
        values["targets"] = exact
    elif len(exact) == 1:
        values["target"] = exact[0]
    else:
        raise NativeMeshError("This Mesh segmentation operation requires one source.")
    captured = capture_background_mesh_segment(
        document,
        str(document.Uid),
        native_operation,
        values,
    )
    VibeCADGui._ensure_document_thread_invoker()
    runner = _MeshSegmentationUi(
        document,
        captured,
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc),
            error_code="NATIVE_MESH_SEGMENTATION_QUEUE_FAILED",
        ) from exc
