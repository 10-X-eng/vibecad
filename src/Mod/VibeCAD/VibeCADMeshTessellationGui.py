# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared process-isolated shape tessellation path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshTessellationJob import run_shape_tessellation
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeMeshConvert import (
    capture_shape_tessellation,
    commit_shape_tessellation,
    shape_tessellation_source_still_exact,
    verify_shape_tessellation,
)
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMutation import NativeMutationDraft
import VibeCADGui


_ACTIVE: dict[str, "_ShapeTessellationUi"] = {}
_STATUS: dict[str, "_ShapeTessellationStatusUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, requests: Sequence[Any]) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The document closed while shape tessellation was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing tessellated Meshes.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )
    if not all(shape_tessellation_source_still_exact(document, item) for item in requests):
        raise NativeMeshError(
            "A source shape changed while tessellation was running; no stale Mesh was applied.",
            error_code="NATIVE_MESH_STATE_STALE",
        )


def _source_colors(source: Any, *, unique: bool) -> list[tuple[float, float, float, float]]:
    result = []
    try:
        appearances = list(source.ViewObject.ShapeAppearance)
    except Exception:
        appearances = []
    for appearance in appearances:
        try:
            raw = tuple(float(value) for value in appearance.DiffuseColor)
        except Exception:
            continue
        color = (*raw[:3], raw[3] if len(raw) > 3 else 0.0)
        if not unique or color not in result:
            result.append(color)
    return result


def _apply_face_colors(result: Any, source: Any, *, unique: bool) -> None:
    colors = _source_colors(source, unique=unique)
    segment_count = int(result.Mesh.countSegments())
    if segment_count < 1 or segment_count != len(colors):
        return
    facet_colors = [(0.8, 0.8, 0.8, 0.0)] * int(result.Mesh.CountFacets)
    for segment_index, color in enumerate(colors):
        for facet_index in result.Mesh.getSegment(segment_index):
            facet_colors[int(facet_index)] = color
    if "FaceColors" not in set(result.PropertiesList):
        result.addProperty("App::PropertyColorList", "FaceColors")
    result.FaceColors = facet_colors


def _commit(
    document: Any,
    prepared_values: Sequence[Any],
    *,
    apply_face_colors: bool,
    group_face_colors: bool,
) -> Mapping[str, Any]:
    requests = tuple(item.request for item in prepared_values)
    _validate(document, requests)
    document.openTransaction("Mesh From Shape")
    try:
        drafts = []
        outputs = []
        for prepared in prepared_values:
            draft = commit_shape_tessellation(document, prepared, publish=False)
            if not isinstance(draft, NativeMutationDraft):
                raise RuntimeError("Shape tessellation returned no document change.")
            targets = tuple(dict.fromkeys(draft.recompute_targets))
            if targets and document.recompute(list(targets), True, True) is False:
                raise RuntimeError("A tessellated Mesh failed to recompute.")
            if draft.after_recompute is not None:
                draft.after_recompute(document)
            result = draft.value["result"]
            if apply_face_colors:
                _apply_face_colors(
                    result,
                    prepared.request.source,
                    unique=group_face_colors,
                )
            drafts.append(draft)
            outputs.append(result)
        import MeshGui

        MeshGui.publishSourcePreservingOutputs(
            str(document.Name),
            [request.source for request in requests],
            outputs,
            "MeshedShapes",
            "Meshed Shapes",
            "Mesh from shape",
        )
        results = [
            verify_shape_tessellation(
                document,
                draft,
                require_operation=len(drafts) == 1,
            )
            for draft in drafts
        ]
        document.commitTransaction()
        return {
            "tessellated": results,
            "output_names": [str(output.Name) for output in outputs],
        }
    except Exception:
        document.abortTransaction()
        raise


def _run_batch(requests: Sequence[Any], *, cancelled: Any, progress: Any) -> tuple[Any, ...]:
    count = len(requests)
    results = []
    for index, request in enumerate(requests):
        if cancelled():
            raise NativeBackgroundCancelled()

        def report(percent: int, message: str) -> None:
            mapped = 1 + int((index + min(90, max(1, percent)) / 90.0) * 88 / count)
            progress(min(89, mapped), f"{message} ({index + 1}/{count})")

        results.append(
            run_shape_tessellation(request, cancelled=cancelled, progress=report)
        )
    return tuple(results)


class _ShapeTessellationUi:
    def __init__(
        self,
        document: Any,
        requests: Sequence[Any],
        manager: Any,
        *,
        apply_face_colors: bool,
        group_face_colors: bool,
    ) -> None:
        self.document = document
        self.requests = tuple(requests)
        self.manager = manager
        self.apply_face_colors = apply_face_colors
        self.group_face_colors = group_face_colors
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing shape tessellation",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Mesh From Shape")
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
            capability_name="mesh.convert.shape_to_mesh.human",
            prepare=lambda cancelled, progress: _run_batch(
                self.requests,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: _validate(self.document, self.requests),
            commit=lambda prepared: _commit(
                self.document,
                prepared,
                apply_face_colors=self.apply_face_colors,
                group_face_colors=self.group_face_colors,
            ),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified tessellated Mesh",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling shape tessellation")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh from shape: {snapshot.progress_message}"
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
            App.Console.PrintMessage("Mesh from shape completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh from shape completed", 10000)
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh from shape cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh from shape cancelled", 10000)
        else:
            clean = str(message or "Mesh from shape failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Mesh from shape failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage("Mesh from shape failed", 10000)
        self.dialog.deleteLater()


class _ShapeTessellationStatusUi:
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
            f"Mesh from shape: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            self._finish(str(snapshot.phase))

    def _finish(self, phase: str) -> None:
        self.timer.stop()
        _STATUS.pop(self.job_id, None)
        message = {
            "completed": "Mesh from shape completed",
            "cancelled": "Mesh from shape cancelled",
        }.get(phase, "Mesh from shape failed")
        Gui.getMainWindow().statusBar().showMessage(message, 10000)
        self.timer.deleteLater()


def watch_shape_tessellation_job(manager: Any, job_id: str) -> None:
    clean = str(job_id or "")
    if clean and clean not in _STATUS:
        _ShapeTessellationStatusUi(manager, clean).start()


def start_shape_tessellations(
    entries: Sequence[Any],
    settings: Mapping[str, Any],
    apply_face_colors: bool = False,
    group_face_colors: bool = False,
) -> str:
    values = tuple(entries)
    if not values:
        raise NativeMeshError("Select at least one shape to tessellate.")
    if type(apply_face_colors) is not bool or type(group_face_colors) is not bool:
        raise NativeMeshError("Face-color options must be true or false.")
    first = values[0]
    if not isinstance(first, (tuple, list)) or len(first) != 3:
        raise NativeMeshError("Each shape tessellation entry must contain source, faces, and label.")
    document = getattr(first[0], "Document", None)
    if document is None:
        raise NativeMeshError("The selected shape document is unavailable.")
    requests = []
    for entry in values:
        if not isinstance(entry, (tuple, list)) or len(entry) != 3:
            raise NativeMeshError("Each shape tessellation entry must contain source, faces, and label.")
        source, subelements, label = entry
        if getattr(source, "Document", None) is not document:
            raise NativeMeshError("Every selected shape must belong to one active document.")
        requests.append(
            capture_shape_tessellation(
                document,
                str(document.Uid),
                source={"object_name": str(source.Name)},
                subelements=list(subelements),
                label=str(label),
                settings=settings,
            )
        )
    VibeCADGui._ensure_document_thread_invoker()
    runner = _ShapeTessellationUi(
        document,
        requests,
        get_service().native_background_manager(),
        apply_face_colors=apply_face_colors,
        group_face_colors=group_face_colors,
    )
    try:
        return runner.start()
    except NativeBackgroundError:
        raise
