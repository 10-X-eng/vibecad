# SPDX-License-Identifier: LGPL-2.1-or-later

"""In-app Apply / Reject for Native mutation previews."""

from __future__ import annotations

from typing import Any

from VibeCADNativeState import NATIVE_PREVIEW_MISSING, NativeStateError
from VibeCADNativeTargets import document_uid


def pending_document_previews(service: Any) -> list[dict[str, Any]]:
    document = service._active_document()
    if document is None:
        return []
    return service.native_document_state_store().list_mutation_previews(
        document_uid(document)
    )


def reject_document_preview(service: Any, preview_id: str | None = None) -> dict[str, Any]:
    document = service._active_document()
    if document is None:
        raise NativeStateError(NATIVE_PREVIEW_MISSING)
    uid = document_uid(document)
    state = service.native_document_state_store()
    pending = state.list_mutation_previews(uid)
    if not pending:
        raise NativeStateError(NATIVE_PREVIEW_MISSING)
    token = str(preview_id or "").strip() or str(pending[-1]["preview_id"])
    return state.reject_mutation_preview(uid, token)


def apply_document_preview(dispatcher: Any, preview_id: str | None = None) -> dict[str, Any]:
    return dispatcher.apply_pending_preview(preview_id)
