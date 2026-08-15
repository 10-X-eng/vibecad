# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared receipt and assistant-local undo handling for immediate mutations."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from VibeCADNativeMutation import (
    NativeMutationDraft,
    NativeMutationRunner,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


def run_immediate_mutation(
    context: NativeRuntimeContext,
    *,
    ticket: NativeCallTicket,
    transaction_name: str,
    mutate: Callable[[Any], NativeMutationDraft],
    verify: Callable[[Any, NativeMutationDraft], Mapping[str, Any]],
    transaction_factory: Callable[[Any, str], Any] | None = None,
    active_transaction_factory: Callable[[Any, str], Any] | None = None,
    after_abort: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Run one guarded mutation and append its bounded host-owned receipt."""

    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    context.guard()
    checkpoint = context.undo_ledger.checkpoint(context.document)
    runner_options = {"active_transaction_factory": active_transaction_factory}
    if transaction_factory is not None:
        if not callable(transaction_factory):
            raise TypeError("transaction_factory must be callable or None")
        runner_options["transaction_factory"] = transaction_factory
    execution = NativeMutationRunner(context.state, **runner_options).run(
        ticket=ticket,
        document=context.document,
        transaction_name=transaction_name,
        reauthorize_turn=context.guard,
        mutate=mutate,
        verify=verify,
        after_abort=after_abort,
    )
    result = dict(execution.result)
    if execution.receipt is not None:
        result["receipt"] = execution.receipt.summary()
        result["assistant_undo_available"] = bool(
            execution.committed_undo_entry
            and context.undo_ledger.record_commit(
                context.document,
                transaction_name,
                checkpoint,
                execution.receipt,
            )
        )
    return result
