# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human command for non-blocking retained-stock setup creation."""

from __future__ import annotations

from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeManufactureFollowUp import (
    create_follow_up_setup,
    preflight_follow_up_setup,
    prepare_follow_up_stock,
    validate_follow_up_setup,
    verify_follow_up_setup,
)
from VibeCADNativeManufactureFollowUpState import is_simulation_result
from VibeCADNativeManufactureJobState import capture_job_creation_environment
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


COMMAND_NAME = "CAM_FollowUpSetup"
_ACTIVE: dict[str, "_FollowUpUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


class _FollowUpUi:
    def __init__(self, document: Any, frozen: Any) -> None:
        self.document = document
        self.frozen = frozen
        self.manager = get_service().native_background_manager()
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing retained stock",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Create Follow-up Setup")
        self.dialog.setWindowModality(QtCore.Qt.NonModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.canceled.connect(self.cancel)
        self.timer = QtCore.QTimer(self.dialog)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def start(self) -> None:
        snapshot = self.manager.submit(
            document_uid=str(self.document.Uid),
            capability_name="manufacture.follow_up_setup.human",
            prepare=lambda cancelled, progress: prepare_follow_up_stock(
                self.frozen,
                cancelled=cancelled,
                progress=progress,
            ),
            validate_before_commit=lambda: validate_follow_up_setup(
                self.document,
                self.frozen,
            ),
            commit=lambda prepared: run_human_mutation(
                document=self.document,
                transaction_name="Create CAM Follow-up Setup",
                mutate=lambda document: create_follow_up_setup(
                    document,
                    self.frozen,
                    prepared,
                ),
                verify=verify_follow_up_setup,
            ),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Creating follow-up CAM setup",
            changes_document=True,
            resource_scope=f"manufacture:{self.frozen.source_job.Name}",
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling retained-stock preparation")

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
            setup = dict(result.get("follow_up_setup") or {}).get("setup")
            name = str(dict(setup or {}).get("object_name") or "")
            obj = self.document.getObject(name) if _document_is_live(self.document) else None
            if obj is not None:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(obj)
            Gui.getMainWindow().statusBar().showMessage(
                "Follow-up CAM setup created",
                10000,
            )
        elif phase == "cancelled":
            Gui.getMainWindow().statusBar().showMessage(
                "Follow-up setup cancelled",
                10000,
            )
        else:
            clean = str(message or "Follow-up setup failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Follow-up setup failed",
                clean,
            )
        self.dialog.deleteLater()


class FollowUpSetupCommand:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Job",
            "MenuText": "New Setup from Remaining Stock",
            "ToolTip": "Create a later setup from one retained material result",
        }

    def IsActive(self):
        document = App.ActiveDocument
        selection = tuple(Gui.Selection.getSelection())
        return bool(
            document is not None
            and not Gui.Control.activeDialog()
            and len(selection) == 1
            and is_simulation_result(selection[0])
        )

    def Activated(self):
        document = App.ActiveDocument
        selection = tuple(Gui.Selection.getSelection())
        if document is None or len(selection) != 1 or not is_simulation_result(selection[0]):
            return
        source_job = getattr(selection[0], "SimulationJob", None)
        default = f"{str(getattr(source_job, 'Label', '') or 'CAM')} follow-up setup"
        label, accepted = QtWidgets.QInputDialog.getText(
            Gui.getMainWindow(),
            "Create Follow-up Setup",
            "Setup name:",
            text=default,
        )
        if not accepted or not str(label).strip():
            return
        try:
            from VibeCADNativeManufactureFollowUpState import simulation_result_state

            state = simulation_result_state(selection[0])
            frozen = preflight_follow_up_setup(
                document,
                str(document.Uid),
                remaining_stock={
                    "object_name": str(selection[0].Name),
                    "expected_state_sha256": str(state["state_sha256"]),
                },
                label=str(label).strip(),
                expected_creation_state_sha256=(
                    capture_job_creation_environment().state_sha256
                ),
            )
            VibeCADGui._ensure_document_thread_invoker()
            _FollowUpUi(document, frozen).start()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "Follow-up setup failed",
                str(exc),
            )


def ensure_command_registered() -> None:
    if Gui.Command.get(COMMAND_NAME) is None:
        Gui.addCommand(COMMAND_NAME, FollowUpSetupCommand())
