# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADNativeDocument import (
    MAX_DOCUMENT_PATH_CHARACTERS,
    NativeDocumentError,
    guarded_save,
)


class _Document:
    Uid = "document-a"
    Name = "DocumentA"
    HasPendingTransaction = False
    Recomputing = False
    RecomputePending = False

    def __init__(self, path: Path | None) -> None:
        self.FileName = str(path) if path is not None else ""
        self.booked_transaction = 0
        self.save_result = None
        self.save_error: Exception | None = None

    def getBookedTransactionID(self) -> int:
        return self.booked_transaction

    def save(self):
        if self.save_error is not None:
            raise self.save_error
        if self.FileName:
            Path(self.FileName).write_bytes(b"FCStd")
        return self.save_result


def _save(document: _Document, *, active=None, editing=False):
    return guarded_save(
        document,
        active_document=lambda: document if active is None else active,
        edit_or_task_active=lambda: editing,
    )


def test_guarded_save_writes_and_verifies_the_exact_existing_path(tmp_path) -> None:
    document = _Document(tmp_path / "exact.FCStd")

    result = _save(document)

    assert result == {
        "saved": True,
        "document_uid": "document-a",
        "file_path": str(tmp_path / "exact.FCStd"),
        "size_bytes": 5,
    }


@pytest.mark.parametrize(
    ("configure", "message"),
    (
        (lambda document: setattr(document, "booked_transaction", 4), "transaction"),
        (lambda document: setattr(document, "Recomputing", True), "recompute"),
    ),
)
def test_guarded_save_refuses_unstable_document_state(
    tmp_path,
    configure,
    message,
) -> None:
    document = _Document(tmp_path / "blocked.FCStd")
    configure(document)

    with pytest.raises(NativeDocumentError, match=message):
        _save(document)

    assert not Path(document.FileName).exists()


def test_guarded_save_refuses_other_active_document_and_edit_task(tmp_path) -> None:
    document = _Document(tmp_path / "blocked.FCStd")

    with pytest.raises(NativeDocumentError, match="no longer active"):
        _save(document, active=object())
    with pytest.raises(NativeDocumentError, match="active edit task"):
        _save(document, editing=True)


def test_guarded_save_requires_human_save_as_for_unsaved_document() -> None:
    document = _Document(None)

    with pytest.raises(NativeDocumentError) as caught:
        _save(document)

    assert caught.value.failure()["repair"] == {"human_action": "save_as"}


def test_guarded_save_bounds_paths_and_normalizes_native_failures(tmp_path) -> None:
    document = _Document(tmp_path / "failed.FCStd")
    document.save_error = RuntimeError("private native details")
    with pytest.raises(NativeDocumentError, match="could not be saved") as caught:
        _save(document)
    assert "private native details" not in str(caught.value)

    document.FileName = "x" * (MAX_DOCUMENT_PATH_CHARACTERS + 1)
    with pytest.raises(NativeDocumentError, match="exceeds"):
        _save(document)


def test_guarded_save_verifies_output_and_document_liveness(tmp_path) -> None:
    class _NoOutput(_Document):
        def save(self):
            return True

    no_output = _NoOutput(tmp_path / "missing.FCStd")
    with pytest.raises(NativeDocumentError, match="did not produce"):
        _save(no_output)

    exact = _Document(tmp_path / "closed.FCStd")
    calls = 0

    def active_document():
        nonlocal calls
        calls += 1
        return exact if calls == 1 else None

    with pytest.raises(NativeDocumentError, match="closed"):
        guarded_save(
            exact,
            active_document=active_document,
            edit_or_task_active=lambda: False,
        )
