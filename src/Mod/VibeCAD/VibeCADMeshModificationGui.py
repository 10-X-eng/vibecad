# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI for the shared retained Mesh modification path."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADMeshModificationJob import make_request, run_mesh_modification
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshModify import (
    PreparedMeshModification,
    accept_mesh_modification_results,
    create_mesh_modification,
    prepare_mesh_modification,
    prepare_selected_mesh_facets,
    verify_mesh_modification,
    verify_mesh_modification_noop,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_MeshModificationUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _commit(document: Any, base: PreparedMeshModification, result: Any) -> Mapping[str, Any]:
    accepted = accept_mesh_modification_results(
        result.request.prepared,
        result,
    )
    if not accepted.targets:
        response = verify_mesh_modification_noop(document, accepted)
    else:
        transaction_name = {
            "harmonize_normals": "Harmonize Mesh Normals",
            "flip_normals": "Flip Mesh Normals",
            "fill_holes": "Fill Mesh Holes",
            "fill_boundary": "Fill Mesh Boundary",
            "add_triangle": "Add Mesh Triangle",
            "remove_components": "Remove Mesh Facets",
            "smooth": "Smooth Mesh",
            "decimate": "Decimate Mesh",
            "scale": "Scale Mesh",
        }[accepted.operation]
        response = run_human_mutation(
            document=document,
            transaction_name=transaction_name,
            mutate=lambda current: create_mesh_modification(current, accepted),
            verify=verify_mesh_modification,
        )
    response["background_prepared"] = True
    response["cache_hit"] = bool(result.cache_hit)
    return response


class _MeshModificationUi:
    def __init__(self, document: Any, prepared: PreparedMeshModification, manager: Any) -> None:
        self.document = document
        self.prepared = prepared
        self.manager = manager
        self.job_id = ""
        self.launch_selection = tuple(
            str(obj.Name) for obj in Gui.Selection.getSelection(str(document.Name))
        )
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing Mesh modification",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Mesh")
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
            capability_name=f"mesh.modify.{self.prepared.operation}.human",
            prepare=lambda cancelled, progress: run_mesh_modification(
                request,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: self._validate(),
            commit=lambda result: _commit(self.document, self.prepared, result),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Mesh modification",
            changes_document=True,
            document_change_resolver=lambda result: bool(result["changed"]),
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def _validate(self) -> None:
        if not _document_is_live(self.document):
            raise NativeMeshError(
                "The document closed while the Mesh modification was running.",
                error_code="NATIVE_MESH_DOCUMENT_UNAVAILABLE",
            )
        if bool(getattr(self.document, "HasPendingTransaction", False)):
            raise NativeMeshError(
                "Finish the active document operation before publishing the Mesh modification.",
                error_code="NATIVE_MESH_TRANSACTION_ACTIVE",
            )

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Mesh modification")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Mesh: {snapshot.progress_message}"
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
            outputs = list(result.get("outputs") or [])
            document_live = _document_is_live(self.document)
            current_selection = (
                tuple(
                    str(obj.Name)
                    for obj in Gui.Selection.getSelection(str(self.document.Name))
                )
                if document_live
                else ()
            )
            replace_selection = (
                document_live
                and bool(outputs)
                and current_selection == self.launch_selection
            )
            if replace_selection:
                Gui.Selection.clearSelection()
            for output in (outputs if replace_selection else ()):
                reference = dict(output.get("result") or {})
                obj = self.document.getObject(str(reference.get("object_name") or ""))
                if obj is not None:
                    Gui.Selection.addSelection(obj)
            App.Console.PrintMessage("Mesh modification completed in the background.\n")
            Gui.getMainWindow().statusBar().showMessage(
                "Mesh modification completed",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage("Mesh modification cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage("Mesh modification cancelled", 10000)
        else:
            clean = str(message or "Mesh modification failed.")
            App.Console.PrintError(clean + "\n")
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Critical,
                "Mesh modification failed",
                clean,
                QtWidgets.QMessageBox.Ok,
                Gui.getMainWindow(),
            )
            box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            box.open()
        self.dialog.deleteLater()


def start_mesh_modifications(
    entries: Sequence[Sequence[Any]],
    operation: str,
    arguments_json: str,
) -> str:
    if not entries:
        raise NativeMeshError("Select at least one non-empty Mesh.")
    documents = {getattr(entry[0], "Document", None) for entry in entries}
    if len(documents) != 1 or None in documents:
        raise NativeMeshError("Every selected Mesh must belong to one open document.")
    document = next(iter(documents))
    if not _document_is_live(document):
        raise NativeMeshError("The selected Mesh document is no longer open.")
    try:
        extras = json.loads(str(arguments_json or "{}"))
    except ValueError as exc:
        raise NativeMeshError("The Mesh modification settings are invalid.") from exc
    if not isinstance(extras, dict):
        raise NativeMeshError("The Mesh modification settings must be an object.")

    targets = []
    selected_triangle_points: list[int] = []
    for entry in entries:
        if len(entry) != 4:
            raise NativeMeshError("A selected Mesh modification target is incomplete.")
        source, label, point_indices, facet_indices = entry
        state = mesh_object_state(source)
        target = {
            "object_name": str(source.Name),
            "expected_state_sha256": str(state["state_sha256"]),
            "label": str(label),
        }
        if operation == "smooth":
            points = [int(value) for value in point_indices]
            target["selection"] = (
                {"kind": "point_indices", "point_indices": points}
                if points
                else {"kind": "all"}
            )
        if operation == "add_triangle" and point_indices:
            selected_triangle_points = [int(value) for value in point_indices]
        if operation == "remove_components" and facet_indices:
            target["facet_indices"] = [int(value) for value in facet_indices]
        targets.append(target)
    values = {"targets": targets, **extras}
    if operation == "add_triangle" and "point_indices" not in values:
        values["point_indices"] = selected_triangle_points
    prepared = (
        prepare_selected_mesh_facets(document, str(document.Uid), targets)
        if operation == "remove_components"
        and targets
        and all("facet_indices" in target for target in targets)
        else prepare_mesh_modification(
            document,
            str(document.Uid),
            str(operation),
            values,
        )
    )
    try:
        return _MeshModificationUi(
            document,
            prepared,
            get_service().native_background_manager(),
        ).start()
    except NativeBackgroundError as exc:
        raise NativeMeshError(
            str(exc),
            error_code="NATIVE_MESH_MODIFICATION_QUEUE_FAILED",
        ) from exc
