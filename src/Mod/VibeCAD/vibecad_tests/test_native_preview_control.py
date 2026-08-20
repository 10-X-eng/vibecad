# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativePreviewCommands import (
    ApplyNativePreviewCommand,
    RejectNativePreviewCommand,
    register_preview_commands,
)
from VibeCADNativePreviewControl import (
    maybe_auto_apply_pending_preview,
    pending_document_previews,
    reject_document_preview,
)
from VibeCADNativeState import NativeDocumentStateStore, NativeStateError


class _Document:
    Uid = "document-preview"


def _service(store: NativeDocumentStateStore, document=_Document()) -> SimpleNamespace:
    return SimpleNamespace(
        _active_document=lambda: document,
        native_document_state_store=lambda: store,
    )


def test_reject_document_preview_consumes_without_apply() -> None:
    store = NativeDocumentStateStore()
    store.ensure_document("document-preview")
    preview = store.propose_mutation_preview(
        "document-preview",
        capability_name="model.extrude",
        arguments={"operation": "extrude", "label": "Pad"},
    )
    rejected = reject_document_preview(_service(store))
    assert rejected["preview_id"] == preview["preview_id"]
    assert rejected["applied"] is False
    assert rejected["rejected"] is True
    assert pending_document_previews(_service(store)) == []


def test_reject_document_preview_fails_when_empty() -> None:
    store = NativeDocumentStateStore()
    store.ensure_document("document-preview")
    with pytest.raises(NativeStateError, match="NATIVE_PREVIEW_MISSING"):
        reject_document_preview(_service(store))


def test_apply_command_is_active_only_for_fresh_previews(monkeypatch) -> None:
    store = NativeDocumentStateStore()
    store.ensure_document("document-preview")
    monkey_service = _service(store)
    monkeypatch.setattr(
        "VibeCADNativePreviewCommands.get_service",
        lambda: monkey_service,
        raising=False,
    )
    monkeypatch.setattr(
        "VibeCADCore.get_service",
        lambda: monkey_service,
        raising=False,
    )
    command = ApplyNativePreviewCommand()
    reject = RejectNativePreviewCommand()
    assert command.IsActive() is False
    assert reject.IsActive() is False
    store.propose_mutation_preview(
        "document-preview",
        capability_name="model.extrude",
        arguments={"label": "Pad"},
    )
    assert command.IsActive() is True
    assert reject.IsActive() is True
    store.note_structural_change("document-preview")
    assert command.IsActive() is False
    assert reject.IsActive() is True


def test_maybe_auto_apply_is_off_by_default() -> None:
    dispatcher = SimpleNamespace(
        auto_apply_previews=False,
        pending_previews=lambda: [{"preview_id": "x", "stale": False}],
        apply_pending_preview=lambda *_args, **_kwargs: pytest.fail("disabled"),
    )
    result = maybe_auto_apply_pending_preview(dispatcher)
    assert result == {"auto_applied": False, "reason": "disabled"}


def test_maybe_auto_apply_refuses_stale() -> None:
    dispatcher = SimpleNamespace(
        auto_apply_previews=True,
        pending_previews=lambda: [{"preview_id": "x", "stale": True}],
        apply_pending_preview=lambda *_args, **_kwargs: pytest.fail("stale"),
    )
    result = maybe_auto_apply_pending_preview(dispatcher)
    assert result["auto_applied"] is False
    assert result["stale"] is True


def test_register_preview_commands_adds_apply_and_reject() -> None:
    registered = {}
    gui = SimpleNamespace(addCommand=lambda name, command: registered.__setitem__(name, command))
    register_preview_commands(gui)
    assert set(registered) == {
        "VibeCAD_ApplyNativePreview",
        "VibeCAD_RejectNativePreview",
    }
    assert registered["VibeCAD_ApplyNativePreview"].GetResources()["MenuText"] == (
        "Apply Native preview"
    )
