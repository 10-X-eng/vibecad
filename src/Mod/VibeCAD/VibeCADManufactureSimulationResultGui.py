# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human command for the shared non-blocking retained CAM simulation path."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import PathScripts.PathUtils as PathUtils
from VibeCADCore import get_service
from VibeCADNativeManufactureSimulationResult import (
    create_native_simulation_result,
    verify_native_simulation_result,
)
from VibeCADNativeManufactureSimulationResultInput import (
    preflight_native_simulation,
    validate_native_simulation,
)
from VibeCADNativeManufactureSimulationResultWorker import execute_native_simulation
from VibeCADNativeManufactureState import (
    is_job,
    job_state,
    operation_active_state,
    operation_reference_state,
)
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


COMMAND_NAME = "CAM_RetainSimulationResult"
_ACTIVE: dict[str, "_RetainedSimulationUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _target(state: Mapping[str, Any]) -> dict[str, str]:
    return {
        "object_name": str(state["object_name"]),
        "expected_state_sha256": str(state["state_sha256"]),
    }


def _selected_job(document: Any) -> Any | None:
    selected = tuple(Gui.Selection.getSelection())
    if selected:
        jobs = {
            id(job): job
            for obj in selected
            for job in (PathUtils.findParentJob(obj),)
            if job is not None and getattr(job, "Document", None) is document
        }
        return next(iter(jobs.values())) if len(jobs) == 1 else None
    jobs = tuple(obj for obj in document.Objects if is_job(obj))
    return jobs[0] if len(jobs) == 1 else None


def _active_operations(job: Any) -> tuple[Any, ...]:
    return tuple(
        operation
        for operation in tuple(getattr(job.Operations, "Group", ()) or ())
        if operation_active_state(operation)
    )


class _RetainedSimulationUi:
    def __init__(self, document: Any, frozen: Any) -> None:
        self.document = document
        self.frozen = frozen
        self.manager = get_service().native_background_manager()
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing retained material",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Retain Simulation Result")
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
            capability_name="manufacture.simulation_result.human",
            prepare=lambda cancelled, progress: execute_native_simulation(
                self.frozen,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: validate_native_simulation(
                self.document,
                self.frozen,
            ),
            commit=lambda prepared: run_human_mutation(
                document=self.document,
                transaction_name="Create CAM Simulation Result",
                mutate=lambda document: create_native_simulation_result(
                    document,
                    prepared,
                ),
                verify=verify_native_simulation_result,
            ),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Committing retained CAM material result",
            changes_document=True,
            resource_scope=f"manufacture:{self.frozen.job.Name}",
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling retained simulation")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self.finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        if snapshot.terminal:
            error = dict(snapshot.error or {})
            self.finish(
                str(snapshot.phase),
                str(error.get("message") or snapshot.progress_message),
                snapshot.result,
            )

    def finish(
        self,
        phase: str,
        message: str,
        result: Mapping[str, Any] | None,
    ) -> None:
        self.timer.stop()
        self.dialog.close()
        _ACTIVE.pop(self.job_id, None)
        if phase == "completed" and isinstance(result, Mapping):
            receipt = dict(result.get("simulation_result") or {})
            result_state = dict(receipt.get("result") or {})
            name = str(result_state.get("object_name") or "")
            obj = self.document.getObject(name) if _document_is_live(self.document) else None
            if obj is not None:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(obj)
            verification = dict(receipt.get("verification") or {})
            protected = dict(verification.get("protected_model") or {})
            collisions = int(protected.get("collision_command_count") or 0)
            machine_travel = dict(verification.get("machine_travel") or {})
            travel_violations = len(machine_travel.get("violations") or ())
            status = "Retained CAM result created"
            if collisions:
                status += f"; {collisions} protected-model collisions found"
            if travel_violations:
                status += f"; {travel_violations} machine travel span violations found"
            if collisions or travel_violations:
                App.Console.PrintWarning(status + "\n")
            Gui.getMainWindow().statusBar().showMessage(status, 15000)
        elif phase == "cancelled":
            Gui.getMainWindow().statusBar().showMessage(
                "Retained CAM simulation cancelled",
                10000,
            )
        else:
            clean = str(message or "Retained CAM simulation failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Retained CAM simulation failed",
                clean,
            )
        self.dialog.deleteLater()


def start_retained_simulation(
    job: Any,
    *,
    operations: Sequence[Any],
    quality: int,
) -> str:
    document = getattr(job, "Document", None)
    if document is None or not is_job(job):
        raise ValueError("Retained simulation requires one live CAM setup.")
    selected = tuple(operations)
    if not selected:
        raise ValueError("Retained simulation requires at least one active operation.")
    frozen = preflight_native_simulation(
        document,
        job=_target(job_state(job)),
        operations=[_target(operation_reference_state(value)) for value in selected],
        quality=quality,
    )
    VibeCADGui._ensure_document_thread_invoker()
    return _RetainedSimulationUi(document, frozen).start()


class RetainSimulationResultCommand:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Simulator",
            "MenuText": "Retain Simulation Result",
            "ToolTip": "Simulate one setup and retain its remaining stock and verification",
        }

    def IsActive(self):
        document = App.ActiveDocument
        if document is None or Gui.Control.activeDialog():
            return False
        job = _selected_job(document)
        return bool(job is not None and _active_operations(job))

    def Activated(self):
        document = App.ActiveDocument
        if document is None:
            return
        job = _selected_job(document)
        operations = _active_operations(job) if job is not None else ()
        if job is None or not operations:
            return
        quality, accepted = QtWidgets.QInputDialog.getInt(
            Gui.getMainWindow(),
            "Retain Simulation Result",
            "Simulation quality (1–10):",
            7,
            1,
            10,
            1,
        )
        if not accepted:
            return
        try:
            start_retained_simulation(job, operations=operations, quality=quality)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Retained CAM simulation failed",
                str(exc),
            )


def ensure_command_registered() -> None:
    if Gui.Command.get(COMMAND_NAME) is None:
        Gui.addCommand(COMMAND_NAME, RetainSimulationResultCommand())
