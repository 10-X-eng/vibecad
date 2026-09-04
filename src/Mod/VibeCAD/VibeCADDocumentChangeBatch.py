# SPDX-License-Identifier: LGPL-2.1-or-later

"""Process-wide coordination for atomic live-document change batches.

FreeCAD reports every property assignment through its document observers. A
single logical publication can therefore produce thousands of callbacks. This
small registry lets independent service and GUI layers defer aggregate work
until the outermost atomic document change has finished.
"""

from __future__ import annotations

import threading
from typing import Callable

_lock = threading.RLock()
_depth_by_document: dict[str, int] = {}
_commit_by_document: dict[str, bool] = {}
_origins_by_document: dict[str, set[tuple[str, str]]] = {}
_completed_listeners: list[Callable[[str], None]] = []
_finished_listeners: list[Callable[[str, bool], None]] = []
_flush_listeners: list[Callable[[str], None]] = []


def begin_document_change_batch(
    document_uid: str,
    *,
    origin_program_id: str = "",
    origin_domain: str = "",
) -> None:
    """Enter a nested atomic change batch for one document."""

    uid = str(document_uid or "").strip()
    if not uid:
        raise ValueError("A document change batch requires a document UID.")
    program_id = str(origin_program_id or "").strip()
    domain = str(origin_domain or "").strip()
    if bool(program_id) != bool(domain):
        raise ValueError("A document change batch origin needs both program ID and domain.")
    with _lock:
        depth = _depth_by_document.get(uid, 0)
        if depth == 0:
            _commit_by_document[uid] = True
            _origins_by_document[uid] = set()
        _depth_by_document[uid] = depth + 1
        if program_id:
            _origins_by_document[uid].add((program_id, domain))


def end_document_change_batch(document_uid: str, *, commit: bool = True) -> bool:
    """Leave a batch and notify listeners after its outermost scope closes."""

    uid = str(document_uid or "").strip()
    if not uid:
        raise ValueError("A document change batch requires a document UID.")
    if type(commit) is not bool:
        raise TypeError("commit must be a boolean")
    with _lock:
        depth = _depth_by_document.get(uid, 0)
        if depth < 1:
            raise RuntimeError(f"Document {uid!r} has no active change batch.")
        _commit_by_document[uid] = _commit_by_document.get(uid, True) and commit
        if depth > 1:
            _depth_by_document[uid] = depth - 1
            return False
        _depth_by_document.pop(uid, None)
        committed = _commit_by_document.pop(uid, True)
        _origins_by_document.pop(uid, None)
        listeners = tuple(_completed_listeners)
        finished_listeners = tuple(_finished_listeners)
    for listener in listeners:
        try:
            listener(uid)
        except Exception:
            # An observer refresh is advisory and must never corrupt or mask a
            # successfully completed CAD transaction.
            continue
    for listener in finished_listeners:
        try:
            listener(uid, committed)
        except Exception:
            # Deferred observers are advisory. Their failure must not mask the
            # transaction outcome that has already been established.
            continue
    return True


def document_change_batch_active(document_uid: str) -> bool:
    """Return whether one document is inside an atomic change batch."""

    uid = str(document_uid or "").strip()
    if not uid:
        return False
    with _lock:
        return _depth_by_document.get(uid, 0) > 0


def document_change_batch_origins(document_uid: str) -> frozenset[tuple[str, str]]:
    """Return program identities responsible for the active nested batch."""

    uid = str(document_uid or "").strip()
    if not uid:
        return frozenset()
    with _lock:
        return frozenset(_origins_by_document.get(uid, ()))


def flush_document_change_batch(document_uid: str) -> None:
    """Flush aggregate observers while the caller's transaction is still open."""

    uid = str(document_uid or "").strip()
    if not uid:
        raise ValueError("A document change batch requires a document UID.")
    with _lock:
        if _depth_by_document.get(uid, 0) < 1:
            return
        listeners = tuple(_flush_listeners)
    for listener in listeners:
        listener(uid)


def register_document_change_batch_completed(
    listener: Callable[[str], None],
) -> None:
    """Register an idempotent callback for outermost batch completion."""

    if not callable(listener):
        raise TypeError("A document change batch listener must be callable.")
    with _lock:
        if listener not in _completed_listeners:
            _completed_listeners.append(listener)


def register_document_change_batch_finished(
    listener: Callable[[str, bool], None],
) -> None:
    """Register an idempotent callback with the outermost commit outcome."""

    if not callable(listener):
        raise TypeError("A document change batch listener must be callable.")
    with _lock:
        if listener not in _finished_listeners:
            _finished_listeners.append(listener)


def register_document_change_batch_flush(listener: Callable[[str], None]) -> None:
    """Register work which must run once before an atomic transaction commits."""

    if not callable(listener):
        raise TypeError("A document change batch listener must be callable.")
    with _lock:
        if listener not in _flush_listeners:
            _flush_listeners.append(listener)
