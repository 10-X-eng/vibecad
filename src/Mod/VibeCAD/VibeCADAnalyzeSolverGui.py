# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking GUI presentation for the shared detached solver pipeline."""

from __future__ import annotations

from typing import Any, Mapping

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeSolverExecution import (
    capture_solver_execution_request,
    commit_solver_execution,
    solver_resource_scope,
    validate_captured_solver_execution,
    verify_solver_execution,
)
from VibeCADNativeAnalyzeSolverExecutionInput import (
    create_solver_execution_workspace,
    freeze_solver_execution_snapshot,
    materialize_solver_execution_snapshot,
)
from VibeCADNativeAnalyzeSolverExecutionWorker import (
    execute_frozen_solver_execution,
)
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeMutation import NativeMutationDraft
import VibeCADGui

_ACTIVE_RUNS: dict[str, "_SolverRunUi"] = {}
_STATUS_RUNS: dict[str, "_SolverJobStatusUi"] = {}
_BACKEND_LABELS = {
    "calculix": "CalculiX",
    "elmer": "Elmer",
    "mystran": "Mystran",
    "openfoam": "OpenFOAM",
    "z88": "Z88",
}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _commit_human_result(document: Any, prepared: Any) -> Mapping[str, Any]:
    if not _document_is_live(document):
        raise NativeAnalyzeError(
            "The FEM document closed before result import.",
            error_code="NATIVE_ANALYZE_DOCUMENT_UNAVAILABLE",
        )
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise NativeAnalyzeError(
            "Finish the active document operation before importing FEM results.",
            error_code="NATIVE_ANALYZE_TRANSACTION_ACTIVE",
        )
    document.openTransaction("Import FEM Results")
    try:
        draft = commit_solver_execution(document, prepared)
        if not isinstance(draft, NativeMutationDraft):
            raise RuntimeError("FEM result import returned no document change.")
        targets = tuple(dict.fromkeys(draft.recompute_targets))
        if targets and document.recompute(list(targets), True, True) is False:
            raise RuntimeError("The FEM result graph failed to recompute.")
        if draft.after_recompute is not None:
            draft.after_recompute(document)
        result = verify_solver_execution(document, draft)
        document.commitTransaction()
        return result
    except Exception:
        document.abortTransaction()
        raise


class _SolverRunUi:
    def __init__(
        self,
        document: Any,
        captured: Any,
        workspace: Any,
        manager: Any,
    ) -> None:
        self.document = document
        self.captured = captured
        self.workspace = workspace
        self.manager = manager
        self.backend = _BACKEND_LABELS[captured.target.kind]
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            f"Preparing {self.backend} case",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle(self.backend)
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
        captured = self.captured
        workspace = self.workspace

        def prepare(cancelled: Any, progress: Any) -> Any:
            progress(3, "Capturing exact FEM document")
            materialized = VibeCADGui._dispatch_to_document_thread(
                lambda: materialize_solver_execution_snapshot(
                    document,
                    captured,
                    workspace,
                )
            )
            progress(5, "Authenticating exact FEM document snapshot")
            frozen = freeze_solver_execution_snapshot(materialized)
            return execute_frozen_solver_execution(
                frozen,
                cancelled=cancelled,
                progress=progress,
            )

        def validate() -> None:
            if not _document_is_live(document):
                raise NativeAnalyzeError(
                    "The FEM document closed while the solver was running.",
                    error_code="NATIVE_ANALYZE_DOCUMENT_UNAVAILABLE",
                )
            validate_captured_solver_execution(document, captured)

        snapshot = self.manager.submit(
            document_uid=str(document.Uid),
            capability_name="analyze.solver_execution.run",
            prepare=prepare,
            validate_before_commit=validate,
            commit=lambda prepared: _commit_human_result(document, prepared),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message=f"Importing verified {self.backend} results",
            cleanup=lambda _prepared: workspace.cleanup(),
            resource_scope=solver_resource_scope(captured),
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
            self.dialog.setLabelText(f"Cancelling {self.backend}")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self._finish("failed", str(exc), None)
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"{self.backend}: {snapshot.progress_message}"
        )
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
            App.Console.PrintMessage(f"{self.backend} analysis completed.\n")
            Gui.getMainWindow().statusBar().showMessage(
                f"{self.backend} analysis completed",
                10000,
            )
        elif phase == "cancelled":
            App.Console.PrintMessage(f"{self.backend} analysis cancelled.\n")
            Gui.getMainWindow().statusBar().showMessage(
                f"{self.backend} analysis cancelled",
                10000,
            )
        else:
            clean = str(message or f"{self.backend} analysis failed.")
            App.Console.PrintError(clean + "\n")
            QtWidgets.QMessageBox.critical(
                Gui.getMainWindow(),
                f"{self.backend} failed",
                clean,
            )
            Gui.getMainWindow().statusBar().showMessage(
                f"{self.backend} analysis failed",
                10000,
            )
        self.dialog.deleteLater()


class _SolverJobStatusUi:
    """Mirror provider-started solver progress into VibeCAD's status bar."""

    def __init__(self, manager: Any, job_id: str, solver_kind: str) -> None:
        self.manager = manager
        self.job_id = str(job_id)
        self.backend = _BACKEND_LABELS.get(str(solver_kind), str(solver_kind).title())
        self.timer = QtCore.QTimer(Gui.getMainWindow())
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)

    def start(self) -> None:
        _STATUS_RUNS[self.job_id] = self
        self.poll()
        if self.job_id in _STATUS_RUNS:
            self.timer.start()

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception:
            self._finish("failed")
            return
        Gui.getMainWindow().statusBar().showMessage(
            f"{self.backend}: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            self._finish(str(snapshot.phase))

    def _finish(self, phase: str) -> None:
        self.timer.stop()
        _STATUS_RUNS.pop(self.job_id, None)
        message = {
            "completed": f"{self.backend} analysis completed",
            "cancelled": f"{self.backend} analysis cancelled",
        }.get(phase, f"{self.backend} analysis failed")
        Gui.getMainWindow().statusBar().showMessage(message, 10000)
        self.timer.deleteLater()


def watch_solver_job(manager: Any, job_id: str, solver_kind: str) -> None:
    """Start one non-modal status watcher for an AI-started solver job."""

    clean = str(job_id or "")
    if not clean or clean in _STATUS_RUNS:
        return
    _SolverJobStatusUi(manager, clean, solver_kind).start()


def run_solver_detached(solver: Any) -> str:
    """Start a supported solver through the shared exact detached pipeline."""

    state = solver_state(solver)
    if state["solver_kind"] not in _BACKEND_LABELS:
        raise TypeError("run_solver_detached requires a supported FEM solver")
    document = solver.Document
    VibeCADGui._ensure_document_thread_invoker()
    captured = capture_solver_execution_request(
        document,
        str(document.Uid),
        target={
            "object_name": str(solver.Name),
            "expected_state_sha256": str(state["state_sha256"]),
        },
        timeout_seconds=86400,
    )
    workspace = create_solver_execution_workspace()
    runner = _SolverRunUi(
        document,
        captured,
        workspace,
        get_service().native_background_manager(),
    )
    try:
        return runner.start()
    except NativeBackgroundError:
        workspace.cleanup()
        raise
    except Exception:
        workspace.cleanup()
        raise


def run_openfoam_solver(solver: Any) -> str:
    """Start OpenFOAM through the shared exact detached pipeline."""

    state = solver_state(solver)
    if state["solver_kind"] != "openfoam":
        raise TypeError("run_openfoam_solver requires an OpenFOAM solver")
    return run_solver_detached(solver)


def run_elmer_solver(solver: Any) -> str:
    """Start Elmer through the shared exact detached pipeline."""

    state = solver_state(solver)
    if state["solver_kind"] != "elmer":
        raise TypeError("run_elmer_solver requires an Elmer solver")
    return run_solver_detached(solver)
