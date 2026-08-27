# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-blocking human UI for the shared Visual Inspection operation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADCore import get_service
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeInspectionCompare import (
    capture_inspection_comparison,
    commit_inspection_comparisons,
    comparison_still_exact,
    run_inspection_comparison,
    verify_inspection_comparisons,
)
from VibeCADNativeMutation import run_human_mutation
import VibeCADGui


_ACTIVE: dict[str, "_VisualInspectionUi"] = {}


def _document_is_live(document: Any) -> bool:
    try:
        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _validate(document: Any, requests: Sequence[Any]) -> None:
    if not _document_is_live(document):
        raise RuntimeError("The Inspection document closed while comparison was running.")
    if bool(getattr(document, "HasPendingTransaction", False)):
        raise RuntimeError("Finish the active document operation before publishing Inspection results.")
    if not all(comparison_still_exact(document, request) for request in requests):
        raise RuntimeError(
            "Inspection geometry changed while deviations were computed; no stale result was applied."
        )


def _prepare(requests: Sequence[Any], cancelled: Any, progress: Any) -> tuple[Any, ...]:
    prepared = []
    count = len(requests)
    for index, request in enumerate(requests):
        if cancelled():
            raise NativeBackgroundCancelled()

        def report(percent: int, message: str) -> None:
            fraction = (index + min(90, max(1, percent)) / 90.0) / count
            progress(min(90, 1 + int(89 * fraction)), f"{message} ({index + 1}/{count})")

        prepared.append(
            run_inspection_comparison(
                request,
                cancelled=cancelled,
                progress=report,
            )
        )
    return tuple(prepared)


def _commit(document: Any, prepared: tuple[Any, ...]) -> Mapping[str, Any]:
    result = run_human_mutation(
        document=document,
        transaction_name="Visual Inspection",
        mutate=lambda exact_document: commit_inspection_comparisons(
            exact_document,
            prepared,
        ),
        verify=verify_inspection_comparisons,
    )
    first = prepared[0].request
    document_expression = f"App.getDocument({str(document.Name)!r})"
    actuals = ",".join(
        f"{document_expression}.getObject({value.request.actual.name!r})"
        for value in prepared
    )
    nominals = ",".join(
        f"{document_expression}.getObject({source.name!r})"
        for source in first.nominals
    )
    Gui.addModule("VibeCADInspectionComparisonGui")
    Gui.doCommandSkip(
        "VibeCADInspectionComparisonGui.start_visual_inspection("
        f"[{actuals}],[{nominals}],{first.search_radius_mm!r},{first.thickness_mm!r})"
    )
    return result


class _VisualInspectionUi:
    def __init__(self, document: Any, requests: Sequence[Any]) -> None:
        self.document = document
        self.requests = tuple(requests)
        self.manager = get_service().native_background_manager()
        self.job_id = ""
        self.dialog = QtWidgets.QProgressDialog(
            "Computing signed deviations",
            "Cancel",
            0,
            100,
            Gui.getMainWindow(),
        )
        self.dialog.setWindowTitle("Visual Inspection")
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
            capability_name="inspect.compare.human",
            prepare=lambda cancelled, progress: _prepare(
                self.requests,
                cancelled,
                progress,
            ),
            validate_before_commit=lambda: _validate(self.document, self.requests),
            commit=lambda prepared: _commit(self.document, prepared),
            dispatch_to_document_thread=VibeCADGui._dispatch_to_document_thread,
            finalize_message="Publishing verified Inspection results",
            changes_document=True,
        )
        self.job_id = str(snapshot.job_id)
        _ACTIVE[self.job_id] = self
        self.dialog.show()
        self.timer.start()
        return self.job_id

    def cancel(self) -> None:
        if self.job_id and self.manager.cancel(self.job_id):
            self.dialog.setLabelText("Cancelling Visual Inspection")

    def poll(self) -> None:
        try:
            snapshot = self.manager.snapshot(self.job_id)
        except Exception as exc:
            self.finish("failed", str(exc))
            return
        self.dialog.setValue(int(snapshot.progress_percent))
        self.dialog.setLabelText(str(snapshot.progress_message))
        Gui.getMainWindow().statusBar().showMessage(
            f"Visual Inspection: {snapshot.progress_message}"
        )
        if snapshot.terminal:
            error = dict(snapshot.error or {})
            self.finish(
                str(snapshot.phase),
                str(error.get("message") or snapshot.progress_message),
            )

    def finish(self, phase: str, message: str) -> None:
        self.timer.stop()
        self.dialog.close()
        _ACTIVE.pop(self.job_id, None)
        if phase == "failed":
            QtWidgets.QMessageBox.warning(
                Gui.getMainWindow(),
                "Visual Inspection",
                message,
            )
        status = {
            "completed": "Visual Inspection completed",
            "cancelled": "Visual Inspection cancelled",
        }.get(phase, "Visual Inspection failed")
        Gui.getMainWindow().statusBar().showMessage(status, 10000)
        self.timer.deleteLater()


def start_visual_inspection(
    actuals: Sequence[Any],
    nominals: Sequence[Any],
    search_radius_mm: float,
    thickness_mm: float,
) -> str:
    actual_objects = tuple(actuals)
    nominal_objects = tuple(nominals)
    if not actual_objects or not nominal_objects:
        raise ValueError("Choose at least one actual and one nominal object.")
    document = getattr(actual_objects[0], "Document", None)
    objects = (*actual_objects, *nominal_objects)
    if document is None or any(getattr(obj, "Document", None) is not document for obj in objects):
        raise ValueError("Every Inspection object must belong to one active document.")
    VibeCADGui._ensure_document_thread_invoker()
    requests = tuple(
        capture_inspection_comparison(
            document,
            str(document.Uid),
            {
                "actual": {"object_name": str(actual.Name)},
                "nominals": [
                    {"object_name": str(nominal.Name)} for nominal in nominal_objects
                ],
                "search_radius_mm": float(search_radius_mm),
                "tolerance_mm": float(search_radius_mm),
                "thickness_mm": float(thickness_mm),
                "require_complete": False,
                "result_label": f"{actual.Label} Inspection",
            },
        )
        for actual in actual_objects
    )
    runner = _VisualInspectionUi(document, requests)
    return runner.start()
