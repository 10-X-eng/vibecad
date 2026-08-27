# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared human-authorized Mesh import path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeBackground import (
    NativeBackgroundCancelled,
    NativeBackgroundError,
)
from VibeCADNativeInput import NativeInputError, authorize_native_input_path
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshImport import (
    commit_mesh_imports,
    mesh_import_input_request,
    prepare_mesh_import,
    verify_mesh_imports,
)
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_MeshImportUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any) -> None:
    if not _document_is_live(document):
        raise NativeMeshError(
            "The Mesh document closed while import was running.",
            error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeMeshError(
            "Finish the active document operation before publishing imported Meshes.",
            error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
        )


def _prepare_batch(
    authorizations: Sequence[Any],
    request: Any,
    *,
    cancelled: Any,
    progress: Any,
) -> tuple[Any, ...]:
    count = len(authorizations)
    prepared = []
    for index, authorization in enumerate(authorizations):
        if cancelled():
            raise NativeBackgroundCancelled()

        def report(percent: int, message: str) -> None:
            mapped = 1 + int((index + min(90, max(1, percent)) / 90.0) * 88 / count)
            progress(min(89, mapped), f"{message} ({index + 1}/{count})")

        prepared.append(
            prepare_mesh_import(
                authorization,
                request,
                cancelled=cancelled,
                progress=report,
            )
        )
    return tuple(prepared)


def _commit(document: Any, prepared: Sequence[Any]) -> Mapping[str, Any]:
    _validate(document)
    return run_human_mutation(
        document=document,
        transaction_name="Import Mesh",
        mutate=lambda exact_document: commit_mesh_imports(exact_document, prepared),
        verify=verify_mesh_imports,
    )


class _MeshImportUi:
    def __init__(
        self,
        document: Any,
        authorizations: Sequence[Any],
        request: Any,
        manager: Any,
    ) -> None:
        self.document = document
        self.authorizations = tuple(authorizations)
        self.request = request
        self.manager = manager
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing Mesh import",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Import Mesh")
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
            capability_name="mesh.io.import_mesh.human",
            prepare=lambda cancelled, progress: _prepare_batch(
                self.authorizations,
                self.request,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: _validate(self.document),
            commit=lambda prepared: _commit(self.document, prepared),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified imported Meshes",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh import")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh import: {snapshot.progress_message}"
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
            count = int(result.get("imported_count") or 0)
            App.Console.PrintMessage(f"Imported {count} Mesh file(s) in the background.\n")
            Gui.getMainWindow().statusBar().showMessage(
                f"Imported {count} Mesh file(s)",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh import cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh import cancelled", 10000)
        else:
            clean = str(message or "Mesh import failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Mesh import failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage("Mesh import failed", 10000)
        self.dialog.deleteLater()


def start_mesh_imports(document: Any, paths: Sequence[str]) -> str:
    _validate(document)
    values = tuple(paths)
    if not 1 <= len(values) <= 64 or any(
        not isinstance(path, str) or not path for path in values
    ):
        raise NativeMeshError("Choose 1 to 64 Mesh files to import.")
    request = mesh_import_input_request()
    try:
        authorizations = tuple(
            authorize_native_input_path(request, path) for path in values
        )
    except NativeInputError as exc:
        raise NativeMeshError(str(exc), error_code=exc.code) from exc
    VibeCADGui._ensure_document_thread_invoker()
    runner = _MeshImportUi(
        document,
        authorizations,
        request,
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc),
            error_code="NATIVE_MESH_IMPORT_QUEUE_FAILED",
        ) from exc
