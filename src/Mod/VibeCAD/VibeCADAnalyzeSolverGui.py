# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI presentation for the shared OpenFOAM solver pipeline."""

from __future__ import annotations

from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    commit_solver_execution,
    discard_solver_execution_request,
    prepare_solver_execution_request,
    run_solver_execution,
    verify_solver_execution,
)
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMutation import NativeMutationDraft
import VibeCADGui


_ACTIVE_RUNS: dict[str, "_OpenFOAMRunUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _commit_human_result(document: Any, prepared: Any) -> Mapping[str, Any]:
    if not _document_is_live(document):
        raise NativeAnalyzeError(
            "The OpenFOAM document closed before result import.",
            error_code="NATIVE_ANALYZE_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeAnalyzeError(
            "Finish the active document operation before importing OpenFOAM results.",
            error_code="NATIVE_ANALYZE_TRANSACTION_ACTIVE",
        )
    document.openTransaction("Import OpenFOAM FEM Results")
    try:
        draft = commit_solver_execution(document, prepared)
        if not isinstance(draft, NativeMutationDraft):
            raise RuntimeError("OpenFOAM result import returned no document change.")
        targets = tuple(dict.fromkeys(draft.recompute_targets))
        if targets and document.recompute(list(targets), True, True) is False:
            raise RuntimeError("The OpenFOAM result graph failed to recompute.")
        if draft.after_recompute is not None:
            draft.after_recompute(document)
        result = verify_solver_execution(document, draft)
        document.commitTransaction()
        return result
    except Exception:
        document.abortTransaction()
        raise


class _OpenFOAMRunUi:
    def __init__(self, document: Any, request: Any, manager: Any) -> None:
        self.document = document
        self.request = request
        self.manager = manager
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Preparing OpenFOAM case",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("OpenFOAM")
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
        document = self.document
        request = self.request

        def prepare(cancelled: Any, progress: Any) -> Any:
            return run_solver_execution(request, cancelled=cancelled, progress=progress)

        def validate() -> None:
            if not _document_is_live(document):
                raise NativeAnalyzeError(
                    "The OpenFOAM document closed while the solver was running.",
                    error_code="NATIVE_ANALYZE_DOCUMENT_UNAVAILABLE",
                )

        snapshot = self.manager.submit(
            document_uid=str(document.Uid),
            capability_name="analyze.solver_execution.run",
            prepare=prepare,
            validate_before_commit=validate,
            commit=lambda prepared: _commit_human_result(document, prepared),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Importing verified OpenFOAM results",
            cleanup=lambda _prepared: discard_solver_execution_request(request),
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE_RUNS[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if not self.job_id:
            return
        if self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling OpenFOAM")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        if not snapshot.terminal:
            return
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
        _ACTIVE_RUNS.pop(self.job_id, None)
        if phase == "completed" and result is not None:
            result_name = str(dict(result.get("result") or {}).get("object_name") or "")
            result_object = (
                self.document.getObject(result_name)
                if _document_is_live(self.document)
                else None
            )
            if result_object is not None:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(result_object)
            App.Console.PrintMessage("OpenFOAM analysis completed.\n")
        elif phase == "cancelled":
            App.Console.PrintMessage("OpenFOAM analysis cancelled.\n")
        else:
            clean = str(message or "OpenFOAM analysis failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                "OpenFOAM failed",
                clean,
            )
        self.dialog.deleteLater()


def run_openfoam_solver(solver: Any) -> str:
    """Start the exact OpenFOAM solver through the shared detached pipeline."""

    state = solver_state(solver)
    if state["solver_kind"] != "openfoam":
        raise TypeError("run_openfoam_solver requires an OpenFOAM solver")
    document = solver.Document
    VibeCADGui._ensure_document_thread_invoker()
    request = prepare_solver_execution_request(
        document,
        str(document.Uid),
        target={
            "object_name": str(solver.Name),
            "expected_state_sha256": str(state["state_sha256"]),
        },
        timeout_seconds=86400,
    )
    runner = _OpenFOAMRunUi(
        document,
        request,
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError:
        discard_solver_execution_request(request)
        raise
    except Exception:
        discard_solver_execution_request(request)
        raise
