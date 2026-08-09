# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guarded document-level operations shared by Native surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from VibeCADNativeTargets import document_uid


MAX_DOCUMENT_PATH_CHARACTERS = 4096


class NativeDocumentError(RuntimeError):
    def __init__(self, message: str, *, repair: dict[str, Any] | None = None) -> None:
        super().__init__(str(message).strip())
        self.repair = dict(repair) if repair is not None else None

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": "NATIVE_DOCUMENT_OPERATION_FAILED",
            "message": str(self),
        }
        if self.repair is not None:
            result["repair"] = self.repair
        return result


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def guarded_save(
    document: Any,
    *,
    active_document: Callable[[], Any],
    edit_or_task_active: Callable[[], bool],
) -> dict[str, Any]:
    uid = document_uid(document)
    if not callable(active_document) or not callable(edit_or_task_active):
        raise TypeError("Native save guards must be callable")
    if active_document() is not document:
        raise NativeDocumentError("The exact save target is no longer active.")
    if _transaction_open(document):
        raise NativeDocumentError("Finish or cancel the open transaction before saving.")
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        raise NativeDocumentError("Wait for document recompute before saving.")
    if edit_or_task_active():
        raise NativeDocumentError("Finish or cancel the active edit task before saving.")
    file_path = str(getattr(document, "FileName", "") or "").strip()
    if not file_path:
        raise NativeDocumentError(
            "This document has no file path. Use the human Save As command first.",
            repair={"human_action": "save_as"},
        )
    if len(file_path) > MAX_DOCUMENT_PATH_CHARACTERS:
        raise NativeDocumentError("The document file path exceeds the save bound.")
    try:
        outcome = document.save()
    except Exception as exc:
        raise NativeDocumentError("The exact document could not be saved.") from exc
    if active_document() is not document or document_uid(document) != uid:
        raise NativeDocumentError("The exact document closed while it was being saved.")
    path = Path(file_path)
    try:
        saved = path.is_file()
        size_bytes = int(path.stat().st_size) if saved else 0
    except OSError as exc:
        raise NativeDocumentError("The saved document file could not be verified.") from exc
    if outcome is False or not saved:
        raise NativeDocumentError("The exact document did not produce its saved file.")
    return {
        "saved": True,
        "document_uid": uid,
        "file_path": str(path),
        "size_bytes": size_bytes,
    }
