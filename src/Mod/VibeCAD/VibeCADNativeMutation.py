# SPDX-License-Identifier: LGPL-2.1-or-later

"""One fail-closed transaction runner for immediate Native mutations."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Mapping

from VibeCADNativeState import (
    NativeCallTicket,
    NativeDocumentStateStore,
    NativeObjectIdentity,
    NativeOperationReceipt,
)


NATIVE_DOCUMENT_UNAVAILABLE = "NATIVE_DOCUMENT_UNAVAILABLE"
NATIVE_TRANSACTION_ACTIVE = "NATIVE_TRANSACTION_ACTIVE"
NATIVE_TRANSACTION_FAILED = "NATIVE_TRANSACTION_FAILED"
NATIVE_EXECUTION_FAILED = "NATIVE_EXECUTION_FAILED"
NATIVE_RECOMPUTE_FAILED = "NATIVE_RECOMPUTE_FAILED"
NATIVE_POSTCONDITION_FAILED = "NATIVE_POSTCONDITION_FAILED"
_NATIVE_REAUTHORIZATION_FAILED = "_NATIVE_REAUTHORIZATION_FAILED"


class NativeMutationError(RuntimeError):
    """Concise provider-safe failure with detailed cause retained internally."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code).strip()

    def failure(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class NativeMutationDraft:
    """Domain mutation output awaiting recompute and postcondition proof."""

    value: Any = field(repr=False, compare=False)
    recompute_targets: tuple[Any, ...] = field(default=(), repr=False, compare=False)
    created: tuple[NativeObjectIdentity, ...] = ()
    changed: tuple[NativeObjectIdentity, ...] = ()
    deleted: tuple[NativeObjectIdentity, ...] = ()
    replaced: tuple[NativeObjectIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeMutationExecution:
    result: dict[str, Any]
    receipt: NativeOperationReceipt | None
    duplicate: bool


MutationHandler = Callable[[Any], NativeMutationDraft]
PostconditionHandler = Callable[[Any, NativeMutationDraft], Mapping[str, Any]]
TransactionFactory = Callable[[Any, str], Any]
TurnReauthorizer = Callable[[], Any]
DocumentLiveness = Callable[[Any], bool]


def _default_transaction_factory(document: Any, name: str) -> Any:
    from VibeCADNativeTransaction import _OwnedDocumentTransaction

    return _OwnedDocumentTransaction(document, name)


def _default_document_is_live(document: Any) -> bool:
    try:
        import FreeCAD as App

        return App.getDocument(str(document.Name)) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def _transaction_is_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _exact_recompute_targets(document: Any, targets: tuple[Any, ...]) -> list[Any]:
    exact: list[Any] = []
    names: set[str] = set()
    get_object = getattr(document, "getObject", None)
    for target in tuple(targets):
        name = str(getattr(target, "Name", "") or "").strip()
        if (
            not name
            or getattr(target, "Document", None) is not document
            or (callable(get_object) and get_object(name) is not target)
        ):
            raise NativeMutationError(
                NATIVE_RECOMPUTE_FAILED,
                "A Native recompute target is no longer in the exact document.",
            )
        if name not in names:
            exact.append(target)
            names.add(name)
    return exact


def _recompute_exact(document: Any, targets: tuple[Any, ...]) -> None:
    exact = _exact_recompute_targets(document, targets)
    if not exact:
        return
    recompute = getattr(document, "recompute", None)
    if not callable(recompute):
        raise NativeMutationError(
            NATIVE_RECOMPUTE_FAILED,
            "The exact document cannot recompute the affected objects.",
        )
    try:
        result = recompute(exact, True, True)
    except Exception as exc:
        raise NativeMutationError(
            NATIVE_RECOMPUTE_FAILED,
            "The affected Native document graph failed to recompute.",
        ) from exc
    if result is False:
        raise NativeMutationError(
            NATIVE_RECOMPUTE_FAILED,
            "The affected Native document graph failed to recompute.",
        )


def _abort_owned_transaction(
    transaction: Any | None,
    state: NativeDocumentStateStore,
    ticket: NativeCallTicket,
) -> BaseException | None:
    abort_error: BaseException | None = None
    if transaction is not None:
        try:
            transaction.abort()
        except BaseException as exc:
            abort_error = exc
    state.cancel_mutation(ticket)
    return abort_error


class NativeMutationRunner:
    """Execute, recompute, verify, and commit one semantic Native mutation."""

    def __init__(
        self,
        state: NativeDocumentStateStore,
        *,
        transaction_factory: TransactionFactory = _default_transaction_factory,
        document_is_live: DocumentLiveness = _default_document_is_live,
    ) -> None:
        if not isinstance(state, NativeDocumentStateStore):
            raise TypeError("state must be a NativeDocumentStateStore")
        if not callable(transaction_factory) or not callable(document_is_live):
            raise TypeError("Native mutation host callbacks must be callable")
        self._state = state
        self._transaction_factory = transaction_factory
        self._document_is_live = document_is_live

    def run(
        self,
        *,
        ticket: NativeCallTicket,
        document: Any,
        transaction_name: str,
        reauthorize_turn: TurnReauthorizer,
        mutate: MutationHandler,
        verify: PostconditionHandler,
    ) -> NativeMutationExecution:
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        if not all(callable(item) for item in (reauthorize_turn, mutate, verify)):
            raise TypeError("Native mutation callbacks must be callable")
        name = str(transaction_name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("transaction_name must contain 1 to 80 characters")
        if str(getattr(document, "Uid", "") or "") != ticket.document_uid:
            raise NativeMutationError(
                NATIVE_DOCUMENT_UNAVAILABLE,
                "The exact Native target document is no longer active.",
            )

        reauthorize_turn()
        if not self._document_is_live(document):
            raise NativeMutationError(
                NATIVE_DOCUMENT_UNAVAILABLE,
                "The exact Native target document is no longer open.",
            )
        if _transaction_is_open(document):
            raise NativeMutationError(
                NATIVE_TRANSACTION_ACTIVE,
                "Finish or cancel the active document transaction before retrying.",
            )

        authorization = self._state.authorize_mutation(ticket)
        if authorization.duplicate:
            return NativeMutationExecution(
                result=dict(authorization.prior_verified_result or {}),
                receipt=None,
                duplicate=True,
            )

        transaction = None
        stage = NATIVE_TRANSACTION_FAILED
        try:
            transaction = self._transaction_factory(document, name)
            self._state.begin_mutation_observation(ticket)
            stage = NATIVE_EXECUTION_FAILED
            draft = mutate(document)
            if not isinstance(draft, NativeMutationDraft):
                raise TypeError("Native mutation handler returned an invalid draft.")
            if not self._document_is_live(document):
                raise NativeMutationError(
                    NATIVE_DOCUMENT_UNAVAILABLE,
                    "The exact Native target document closed during the operation.",
                )

            stage = NATIVE_RECOMPUTE_FAILED
            _recompute_exact(document, draft.recompute_targets)
            stage = NATIVE_POSTCONDITION_FAILED
            verified = verify(document, draft)
            if not isinstance(verified, Mapping):
                raise TypeError("Native postcondition must return a result object.")
            stage = _NATIVE_REAUTHORIZATION_FAILED
            reauthorize_turn()
            stage = NATIVE_TRANSACTION_FAILED
            if not self._document_is_live(document):
                raise NativeMutationError(
                    NATIVE_DOCUMENT_UNAVAILABLE,
                    "The exact Native target document closed before commit.",
                )
            prepared = self._state.prepare_mutation_completion(
                ticket,
                verified,
                created=draft.created,
                changed=draft.changed,
                deleted=draft.deleted,
                replaced=draft.replaced,
            )

            stage = NATIVE_TRANSACTION_FAILED
            transaction.commit()
            transaction = None
            self._state.commit_mutation_observation(ticket)
            receipt = self._state.complete_prepared_mutation(prepared)
            return NativeMutationExecution(
                result=json.loads(prepared.verified_result_json),
                receipt=receipt,
                duplicate=False,
            )
        except NativeMutationError:
            abort_error = _abort_owned_transaction(transaction, self._state, ticket)
            if abort_error is not None:
                raise NativeMutationError(
                    NATIVE_TRANSACTION_FAILED,
                    "The Native transaction failed and could not be cleanly aborted.",
                ) from abort_error
            raise
        except Exception as exc:
            abort_error = _abort_owned_transaction(transaction, self._state, ticket)
            if abort_error is not None:
                raise NativeMutationError(
                    NATIVE_TRANSACTION_FAILED,
                    "The Native transaction failed and could not be cleanly aborted.",
                ) from abort_error
            if stage == _NATIVE_REAUTHORIZATION_FAILED:
                raise
            messages = {
                NATIVE_EXECUTION_FAILED: "The Native operation failed before commit.",
                NATIVE_RECOMPUTE_FAILED: (
                    "The affected Native document graph failed to recompute."
                ),
                NATIVE_POSTCONDITION_FAILED: (
                    "The Native operation did not satisfy its postcondition."
                ),
                NATIVE_TRANSACTION_FAILED: "The Native transaction could not commit.",
            }
            raise NativeMutationError(stage, messages[stage]) from exc
