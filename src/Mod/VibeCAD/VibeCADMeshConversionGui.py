# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared process-isolated Mesh conversion path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshConversionJob import run_mesh_conversion
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshConvert import (
    capture_mesh_conversion,
    commit_mesh_conversion,
    promote_committed_mesh_conversion_to_body,
    verify_committed_mesh_body,
    verify_committed_mesh_conversion,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import mesh_target_still_exact
from VibeCADNativeMutation import NativeMutationDraft
import VibeCADGui


_ACTIVE: dict[str, "_MeshConversionUi"] = {}
_STATUS: dict[str, "_MeshConversionStatusUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, requests: Sequence[Any]) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The Mesh document closed while conversion was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing Mesh conversions.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )
    if not all(mesh_target_still_exact(document, request.target) for request in requests):
        raise NativeMeshError(
            "A source Mesh changed while its BREP was being prepared; no stale result was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _commit(
    document: Any,
    prepared_values: Sequence[Any],
    *,
    create_partdesign_bodies: bool = False,
) -> Mapping[str, Any]:
    requests = tuple(value.request for value in prepared_values)
    _validate(document, requests)
    document.openTransaction(
        "Convert Mesh to Part Design Body"
        if create_partdesign_bodies
        else "Convert Mesh to Shape"
    )
    try:
        drafts = []
        outputs = []
        for prepared in prepared_values:
            draft = commit_mesh_conversion(document, prepared, publish=False)
            if create_partdesign_bodies:
                draft = promote_committed_mesh_conversion_to_body(
                    document,
                    draft,
                    publish=False,
                )
            if not isinstance(draft, NativeMutationDraft):
                raise RuntimeError("Mesh conversion returned no document change.")
            targets = tuple(dict.fromkeys(draft.recompute_targets))
            if targets and document.recompute(list(targets), True, True) is False:
                raise RuntimeError("A converted Mesh shape failed to recompute.")
            if draft.after_recompute is not None:
                draft.after_recompute(document)
            drafts.append(draft)
            outputs.append(draft.value["result"])
        import MeshGui

        history_owner = (
            MeshGui.publishReplacingOutputs(
                str(document.Name),
                [request.target.source for request in requests],
                outputs,
                "ConvertedMeshBodies",
                "Converted Mesh Bodies",
                "Convert mesh to Part Design Body",
            )
            if create_partdesign_bodies
            else MeshGui.publishSourcePreservingOutputs(
                str(document.Name),
                [request.target.source for request in requests],
                outputs,
                "ConvertedMeshShapes",
                "Converted Mesh Shapes",
                "Convert mesh to shape",
            )
        )
        if create_partdesign_bodies:
            results = [
                verify_committed_mesh_body(
                    document,
                    draft,
                    history_owner=history_owner,
                )
                for draft in drafts
            ]
        else:
            results = [
                verify_committed_mesh_conversion(
                    document,
                    draft,
                    require_operation=len(drafts) == 1,
                )
                for draft in drafts
            ]
        document.commitTransaction()
        return {
            "converted": results,
            "output_names": [str(output.Name) for output in outputs],
            "created_partdesign_bodies": bool(create_partdesign_bodies),
        }
    except Exception:
        document.abortTransaction()
        raise


def _run_batch(requests: Sequence[Any], *, cancelled: Any, progress: Any) -> tuple[Any, ...]:
    count = len(requests)
    results = []
    for index, request in enumerate(requests):
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()

        def report(percent: int, message: str) -> None:
            mapped = 1 + int((index + min(90, max(1, percent)) / 90.0) * 88 / count)
            progress(min(89, mapped), f"{message} ({index + 1}/{count})")

        results.append(
            run_mesh_conversion(
                request,
                cancelled=cancelled,
                progress=report,
            )
        )
    return tuple(results)


class _MeshConversionUi:
    def __init__(
        self,
        document: Any,
        requests: Sequence[Any],
        manager: Any,
        *,
        create_partdesign_bodies: bool = False,
    ) -> None:
        self.document = document
        self.requests = tuple(requests)
        self.manager = manager
        self.create_partdesign_bodies = bool(create_partdesign_bodies)
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            (
                "Preparing Mesh for Part Design"
                if self.create_partdesign_bodies
                else "Preparing Mesh conversion"
            ),
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle(
            "Mesh to Part Design Body"
            if self.create_partdesign_bodies
            else "Mesh Conversion"
        )
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
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name="mesh.convert.human",
            prepare=lambda cancelled, progress: _run_batch(
                self.requests,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: _validate(self.document, self.requests),
            commit=lambda prepared: _commit(
                self.document,
                prepared,
                create_partdesign_bodies=self.create_partdesign_bodies,
            ),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh conversion",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh conversion")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh conversion: {snapshot.progress_message}"
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
            Gui.Selection.clearSelection()
            for name in list(result.get("output_names") or []):
                obj = self.document.getObject(str(name)) if _document_is_live(self.document) else None
                if obj is not None:
                    Gui.Selection.addSelection(obj)
            if bool(result.get("created_partdesign_bodies", False)):
                message = (
                    "Part Design Body created; the source Mesh is retained in History."
                )
            else:
                message = "Mesh conversion completed; source Mesh remains displayed."
            App.Console.PrintMessage(message + "\n")
            Gui.getMainWindow().statusBar().showMessage(message, 10000)
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh conversion cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh conversion cancelled", 10000)
        else:
            clean = str(message or "Mesh conversion failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Mesh conversion failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage("Mesh conversion failed", 10000)
        self.dialog.deleteLater()


class _MeshConversionStatusUi:
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
            f"Mesh conversion: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            self._finish(str(snapshot.phase))

    def _finish(self, phase: str) -> None:
        self.timer.stop()
        _STATUS.pop(self.job_id, None)
        message = {
            "completed": "Mesh conversion completed",
            "cancelled": "Mesh conversion cancelled",
        }.get(phase, "Mesh conversion failed")
        Gui.getMainWindow().statusBar().showMessage(message, 10000)
        self.timer.deleteLater()


def watch_mesh_conversion_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _MeshConversionStatusUi(manager, clean).start()


def start_mesh_conversions(
    sources: Sequence[Any],
    tolerance_mm: float,
    sew_adjacent_faces: bool,
    make_solid: bool,
    create_partdesign_bodies: bool = False,
) -> str:
    if type(create_partdesign_bodies) is not bool:
        raise NativeMeshError("create_partdesign_bodies must be true or false.")
    if create_partdesign_bodies and not (sew_adjacent_faces and make_solid):
        raise NativeMeshError(
            "Part Design Body conversion requires sewing and solid creation."
        )
    meshes = tuple(sources)
    if not meshes:
        raise NativeMeshError("Select at least one Mesh to convert.")
    document = getattr(meshes[0], "Document", None)
    if document is None or any(getattr(mesh, "Document", None) is not document for mesh in meshes):
        raise NativeMeshError("Every selected Mesh must belong to one active document.")
    VibeCADGui._ensure_document_thread_invoker()
    requests = []
    for mesh in meshes:
        state = mesh_object_state(mesh)
        requests.append(
            capture_mesh_conversion(
                document,
                str(document.Uid),
                source={"object_name": str(mesh.Name)},
                expected_state_sha256=str(state["state_sha256"]),
                label=(
                    f"{mesh.Label} Body"
                    if create_partdesign_bodies
                    else f"{mesh.Label} (Shape)"
                ),
                tolerance_mm=float(tolerance_mm),
                sew_adjacent_faces=bool(sew_adjacent_faces),
                make_solid=bool(make_solid),
            )
        )
    runner = _MeshConversionUi(
        document,
        requests,
        get_service().native_background_manager(),
        create_partdesign_bodies=create_partdesign_bodies,
    )
    try:
        return runner.start()
    except NativeBackgroundError:
        raise
