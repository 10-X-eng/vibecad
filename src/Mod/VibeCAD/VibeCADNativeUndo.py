# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host-owned undo provenance for verified assistant operations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Any, Callable

from VibeCADNativeState import (
    NativeCallTicket,
    NativeDocumentStateStore,
    NativeObjectIdentity,
    NativeOperationReceipt,
)
from VibeCADNativeTargets import document_uid


NATIVE_UNDO_UNAVAILABLE = "NATIVE_UNDO_UNAVAILABLE"
NATIVE_UNDO_FAILED = "NATIVE_UNDO_FAILED"
NATIVE_UNDO_RECOVERY_FAILED = "NATIVE_UNDO_RECOVERY_FAILED"
MAX_LOCAL_UNDO_ENTRIES = 64


class NativeUndoError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code).strip()

    def failure(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class NativeUndoCheckpoint:
    document_uid: str
    undo_count: int
    undo_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeAssistantUndoEntry:
    run_id: str
    document_uid: str
    idempotency_token: str
    capability_name: str
    transaction_name: str
    revision_after: int
    undo_count_after: int
    receipt: NativeOperationReceipt

    def summary(self) -> dict[str, Any]:
        return {
            "capability": self.capability_name,
            "revision_after": self.revision_after,
            "undo_count_after": self.undo_count_after,
        }


@dataclass(frozen=True, slots=True)
class NativeUndoExecution:
    result: dict[str, Any]
    receipt: NativeOperationReceipt | None
    duplicate: bool


@dataclass(slots=True)
class _GuardedUndoEntry:
    entry: NativeAssistantUndoEntry
    guard_revision: int
    undo_names: tuple[str, ...]
    prior_undo_names: tuple[str, ...]


def _required_text(value: Any, label: str, limit: int = 160) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > limit:
        raise ValueError(f"{label} must contain 1 to {limit} characters")
    return clean


def _history(document: Any) -> tuple[int, tuple[str, ...]]:
    try:
        count = int(document.UndoCount)
        names = tuple(str(value) for value in list(document.UndoNames))
    except Exception as exc:
        raise NativeUndoError(
            NATIVE_UNDO_UNAVAILABLE,
            "The exact document does not expose stable undo history.",
        ) from exc
    if count < 0 or len(names) != count:
        raise NativeUndoError(
            NATIVE_UNDO_UNAVAILABLE,
            "The exact document undo history is inconsistent.",
        )
    return count, names


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _object_matches(document: Any, identity: NativeObjectIdentity) -> bool:
    obj = document.getObject(identity.object_name)
    return bool(obj is not None and str(getattr(obj, "TypeId", "")) == identity.type_id)


def _undo_postcondition(document: Any, entry: NativeAssistantUndoEntry) -> bool:
    if any(document.getObject(item.object_name) is not None for item in entry.receipt.created):
        return False
    restored = (*entry.receipt.deleted, *entry.receipt.changed, *entry.receipt.replaced)
    return all(_object_matches(document, item) for item in restored)


class NativeAssistantUndoLedger:
    """Track exact assistant history while the owning document remains open.

    A provider run is only a transport lifetime. Ending a turn or branching a
    conversation must not erase otherwise-safe undo provenance from the CAD
    document's still-current history stack.
    """

    def __init__(self) -> None:
        self._run_id: str | None = None
        self._documents: dict[str, list[_GuardedUndoEntry]] = {}
        self._run_lock = threading.RLock()

    def begin_run(self, run_id: str) -> None:
        clean = _required_text(run_id, "run_id")
        with self._run_lock:
            self._run_id = clean

    def end_run(self, run_id: str) -> None:
        clean = _required_text(run_id, "run_id")
        with self._run_lock:
            if clean == self._run_id:
                self._run_id = None

    @contextmanager
    def run_scope(self, run_id: str):
        """Temporarily restore one transport run for its background commit."""

        clean = _required_text(run_id, "run_id")
        with self._run_lock:
            previous = self._run_id
            self._run_id = clean
            try:
                yield
            finally:
                self._run_id = previous

    def close_document(self, document_uid: str) -> None:
        uid = _required_text(document_uid, "document UID")
        self._documents.pop(uid, None)

    def checkpoint(self, document: Any) -> NativeUndoCheckpoint:
        if self._run_id is None:
            raise NativeUndoError(
                NATIVE_UNDO_UNAVAILABLE,
                "No assistant run owns document history.",
            )
        uid = document_uid(document)
        count, names = _history(document)
        return NativeUndoCheckpoint(uid, count, names)

    def record_commit(
        self,
        document: Any,
        transaction_name: str,
        checkpoint: NativeUndoCheckpoint,
        receipt: NativeOperationReceipt,
    ) -> bool:
        if self._run_id is None:
            return False
        uid = document_uid(document)
        name = _required_text(transaction_name, "transaction_name", 80)
        if (
            not isinstance(checkpoint, NativeUndoCheckpoint)
            or not isinstance(receipt, NativeOperationReceipt)
            or checkpoint.document_uid != uid
            or receipt.created + receipt.changed + receipt.deleted + receipt.replaced == ()
        ):
            self._documents.pop(uid, None)
            return False
        count, names = _history(document)
        normal_growth = bool(
            count == checkpoint.undo_count + 1
            and names == (name, *checkpoint.undo_names)
        )
        bounded_growth = bool(
            checkpoint.undo_count > 0
            and count == checkpoint.undo_count
            and names == (name, *checkpoint.undo_names[:-1])
        )
        if (
            receipt.revision_after < receipt.revision_before
            or not (normal_growth or bounded_growth)
        ):
            self._documents.pop(uid, None)
            return False
        entry = NativeAssistantUndoEntry(
            run_id=self._run_id,
            document_uid=uid,
            idempotency_token=receipt.idempotency_token,
            capability_name=receipt.capability_name,
            transaction_name=name,
            revision_after=receipt.revision_after,
            undo_count_after=count,
            receipt=receipt,
        )
        entries = self._documents.setdefault(uid, [])
        if entries:
            previous = entries[-1]
            if (
                previous.guard_revision != receipt.revision_before
                or previous.undo_names != checkpoint.undo_names
            ):
                entries.clear()
        prior_undo_names = (
            checkpoint.undo_names
            if normal_growth
            else checkpoint.undo_names[:-1]
        )
        entries.append(
            _GuardedUndoEntry(
                entry,
                receipt.revision_after,
                names,
                prior_undo_names,
            )
        )
        del entries[:-MAX_LOCAL_UNDO_ENTRIES]
        return True

    def available(self, document: Any, state: NativeDocumentStateStore) -> dict[str, Any]:
        uid = document_uid(document)
        entries = self._documents.get(uid, []) if self._run_id is not None else []
        if not entries:
            return {"available": False}
        guarded = entries[-1]
        count, names = _history(document)
        safe = bool(
            state.current_revision(uid) == guarded.guard_revision
            and count == len(guarded.undo_names)
            and names == guarded.undo_names
        )
        result: dict[str, Any] = {"available": safe}
        if safe:
            result["operation"] = guarded.entry.summary()
        return result

    def undo_latest(
        self,
        *,
        ticket: NativeCallTicket,
        document: Any,
        state: NativeDocumentStateStore,
        reauthorize_turn: Callable[[], Any],
        active_document: Callable[[], Any],
    ) -> NativeUndoExecution:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        if not isinstance(state, NativeDocumentStateStore):
            raise TypeError("state must be a NativeDocumentStateStore")
        if not callable(reauthorize_turn) or not callable(active_document):
            raise TypeError("Native undo guards must be callable")
        uid = document_uid(document)
        if uid != ticket.document_uid or active_document() is not document:
            raise NativeUndoError(
                NATIVE_UNDO_UNAVAILABLE,
                "The exact Native undo document is no longer active.",
            )
        reauthorize_turn()
        if _transaction_open(document):
            raise NativeUndoError(
                NATIVE_UNDO_UNAVAILABLE,
                "Finish or cancel the active transaction before undoing.",
            )
        authorization = state.authorize_mutation(ticket)
        if authorization.duplicate:
            return NativeUndoExecution(
                result=dict(authorization.prior_verified_result or {}),
                receipt=None,
                duplicate=True,
            )
        entries = self._documents.get(uid, []) if self._run_id is not None else []
        if not entries:
            state.cancel_mutation(ticket)
            raise NativeUndoError(
                NATIVE_UNDO_UNAVAILABLE,
                "There is no safe assistant-owned operation to undo.",
            )
        guarded = entries[-1]
        count, names = _history(document)
        if (
            state.current_revision(uid) != guarded.guard_revision
            or count != len(guarded.undo_names)
            or names != guarded.undo_names
        ):
            state.cancel_mutation(ticket)
            raise NativeUndoError(
                NATIVE_UNDO_UNAVAILABLE,
                "Document history changed after the assistant operation; undo it manually.",
            )

        state.begin_mutation_observation(ticket)
        try:
            document.undo()
            state.note_structural_change(uid)
            after_count, after_names = _history(document)
            if (
                after_count != count - 1
                or after_names != guarded.prior_undo_names
                or not _undo_postcondition(document, guarded.entry)
            ):
                raise NativeUndoError(
                    NATIVE_UNDO_FAILED,
                    "The assistant operation did not undo to its verified prior state.",
                )
            prior_available = bool(
                len(entries) > 1
                and after_names
                and after_names
                == entries[-2].undo_names[: len(after_names)]
            )
            result = {
                "undone": guarded.entry.summary(),
                "undo_available": prior_available,
            }
            prepared = state.prepare_mutation_completion(
                ticket,
                result,
                created=guarded.entry.receipt.deleted,
                changed=guarded.entry.receipt.changed,
                deleted=guarded.entry.receipt.created,
                replaced=guarded.entry.receipt.replaced,
            )
        except Exception as exc:
            try:
                document.redo()
            except Exception as recovery_exc:
                state.cancel_mutation(ticket)
                self._documents.pop(uid, None)
                raise NativeUndoError(
                    NATIVE_UNDO_RECOVERY_FAILED,
                    "Native undo failed and the exact operation could not be restored.",
                ) from recovery_exc
            state.cancel_mutation(ticket)
            if isinstance(exc, NativeUndoError):
                raise
            raise NativeUndoError(
                NATIVE_UNDO_FAILED,
                "The assistant operation could not be undone.",
            ) from exc

        state.commit_mutation_observation(ticket)
        receipt = state.complete_prepared_mutation(prepared)
        entries.pop()
        if entries and prior_available:
            previous = entries[-1]
            previous.guard_revision = receipt.revision_after
            previous.undo_names = after_names
            previous.prior_undo_names = previous.prior_undo_names[
                : max(0, after_count - 1)
            ]
        else:
            self._documents.pop(uid, None)
        return NativeUndoExecution(result=result, receipt=receipt, duplicate=False)
