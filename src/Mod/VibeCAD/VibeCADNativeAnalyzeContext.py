# SPDX-License-Identifier: LGPL-2.1-or-later

"""Responsive, revision-safe preparation of Native Analyze provider context."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import threading
from typing import Any


DEFAULT_ANALYZE_CONTEXT_BATCH_SIZE = 8
MAX_ANALYZE_CONTEXT_CACHE_ENTRIES = 4
_WAIT_POLL_SECONDS = 0.05

CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[int, str], None]
ContextBuilder = Callable[[CancellationCheck, ProgressCallback], dict[str, Any]]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]
PartsPostprocessor = Callable[
    [
        Mapping[str, Any],
        Sequence[Mapping[str, Any]],
        CancellationCheck | None,
        ProgressCallback | None,
    ],
    Sequence[Mapping[str, Any]],
]


class AnalyzeContextError(RuntimeError):
    """Analyze provider context could not be prepared safely."""


class AnalyzeContextCancelled(AnalyzeContextError):
    """The caller stopped waiting for Analyze provider context."""


class AnalyzeContextStale(AnalyzeContextError):
    """Analyze provider context changed while it was being prepared."""


@dataclass
class _PendingContext:
    document_uid: str
    structural_revision: int
    variant: str
    invalidated: threading.Event = field(default_factory=threading.Event)
    completed: bool = False
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    progress_percent: int = 0
    progress_message: str = ""


def _context_key(
    document_uid: str,
    structural_revision: int,
    variant: str = "",
) -> tuple[str, int, str]:
    uid = str(document_uid or "").strip()
    if not uid:
        raise AnalyzeContextError("Analyze context requires an exact document UID.")
    if isinstance(structural_revision, bool):
        raise AnalyzeContextError("Analyze context requires an integer revision.")
    try:
        revision = int(structural_revision)
    except (TypeError, ValueError) as exc:
        raise AnalyzeContextError(
            "Analyze context requires an integer revision."
        ) from exc
    if revision < 0:
        raise AnalyzeContextError(
            "Analyze context requires a non-negative revision."
        )
    return uid, revision, str(variant or "")[:160]


class AnalyzeContextCoordinator:
    """Coalesce and cache detached Analyze snapshots without owning CAD work.

    The caller that wins a cache miss performs the capture. Other callers wait
    for that same document/revision result. This coordinator never shares the
    mutation/mesh/solver background lane and never receives live FreeCAD
    objects.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[tuple[str, int, str], _PendingContext] = {}
        self._cache: OrderedDict[
            tuple[str, int, str],
            dict[str, Any],
        ] = OrderedDict()

    def get_or_build(
        self,
        document_uid: str,
        structural_revision: int,
        build: ContextBuilder,
        *,
        variant: str = "",
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not callable(build):
            raise TypeError("build must be callable")
        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable")
        if progress_callback is not None and not callable(progress_callback):
            raise TypeError("progress_callback must be callable")
        key = _context_key(document_uid, structural_revision, variant)
        with self._condition:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
            pending = self._pending.get(key)
            owner = pending is None
            if pending is None:
                pending = _PendingContext(*key)
                self._pending[key] = pending

        if not owner:
            return self._wait_for_pending(
                pending,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )

        def cancelled() -> bool:
            return pending.invalidated.is_set() or bool(
                cancellation_check is not None and cancellation_check()
            )

        def progress(percent: int, message: str) -> None:
            if isinstance(percent, bool) or type(percent) is not int:
                raise AnalyzeContextError(
                    "Analyze context progress must be an integer."
                )
            clean_percent = max(0, min(100, percent))
            clean_message = str(message or "").strip()[:160]
            with self._condition:
                pending.progress_percent = max(
                    pending.progress_percent,
                    clean_percent,
                )
                pending.progress_message = clean_message
                self._condition.notify_all()
            if progress_callback is not None:
                progress_callback(clean_percent, clean_message)

        try:
            result = build(cancelled, progress)
            if not isinstance(result, dict):
                raise AnalyzeContextError(
                    "Analyze context preparation must return an object."
                )
            with self._condition:
                if pending.invalidated.is_set() or self._pending.get(key) is not pending:
                    raise AnalyzeContextStale(
                        "The Analyze document changed while context was prepared."
                    )
                pending.result = deepcopy(result)
                self._cache[key] = deepcopy(result)
                self._cache.move_to_end(key)
                self._discard_other_document_revisions_locked(key)
                while len(self._cache) > MAX_ANALYZE_CONTEXT_CACHE_ENTRIES:
                    self._cache.popitem(last=False)
                return deepcopy(result)
        except BaseException as exc:
            reported = (
                AnalyzeContextStale(
                    "The Analyze document changed while context was prepared."
                )
                if pending.invalidated.is_set()
                and isinstance(exc, AnalyzeContextCancelled)
                else exc
            )
            with self._condition:
                pending.error = reported
            if reported is not exc:
                raise reported from exc
            raise
        finally:
            with self._condition:
                pending.completed = True
                if self._pending.get(key) is pending:
                    self._pending.pop(key, None)
                self._condition.notify_all()

    def _wait_for_pending(
        self,
        pending: _PendingContext,
        *,
        cancellation_check: CancellationCheck | None,
        progress_callback: ProgressCallback | None,
    ) -> dict[str, Any]:
        reported_progress = (-1, "")
        while True:
            with self._condition:
                if pending.completed:
                    if pending.error is not None:
                        raise pending.error
                    if pending.result is None:
                        raise AnalyzeContextError(
                            "Analyze context preparation completed without a result."
                        )
                    return deepcopy(pending.result)
                current_progress = (
                    pending.progress_percent,
                    pending.progress_message,
                )
                self._condition.wait(_WAIT_POLL_SECONDS)
            if cancellation_check is not None and cancellation_check():
                raise AnalyzeContextCancelled(
                    "Analyze context preparation was cancelled."
                )
            if progress_callback is not None and current_progress != reported_progress:
                progress_callback(*current_progress)
                reported_progress = current_progress

    def _discard_other_document_revisions_locked(
        self,
        keep: tuple[str, int, str],
    ) -> None:
        for key in list(self._cache):
            if key[0] == keep[0] and key != keep:
                self._cache.pop(key, None)

    def invalidate_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        if not uid:
            return False
        changed = False
        with self._condition:
            for key in list(self._cache):
                if key[0] == uid:
                    self._cache.pop(key, None)
                    changed = True
            for key, pending in list(self._pending.items()):
                if key[0] == uid:
                    pending.invalidated.set()
                    changed = True
            if changed:
                self._condition.notify_all()
        return changed

    def has_cached(
        self,
        document_uid: str,
        structural_revision: int,
        *,
        variant: str = "",
    ) -> bool:
        key = _context_key(document_uid, structural_revision, variant)
        with self._condition:
            return key in self._cache

    def close_document(self, document_uid: str) -> bool:
        return self.invalidate_document(document_uid)


def capture_responsive_analyze_snapshot(
    request: Mapping[str, Any],
    *,
    dispatch_to_document_thread: DocumentThreadDispatcher,
    capture_batch: Callable[[Mapping[str, Any], Sequence[str]], Mapping[str, Any]],
    capture_clipping: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    finalize: Callable[
        [Mapping[str, Any], Sequence[Mapping[str, Any]], Mapping[str, Any]],
        dict[str, Any],
    ],
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    postprocess_parts: PartsPostprocessor | None = None,
    batch_size: int = DEFAULT_ANALYZE_CONTEXT_BATCH_SIZE,
) -> dict[str, Any]:
    """Capture live facts in bounded dispatches and finalize detached data."""

    if not isinstance(request, Mapping):
        raise TypeError("request must be a mapping")
    for callback in (
        dispatch_to_document_thread,
        capture_batch,
        capture_clipping,
        finalize,
    ):
        if not callable(callback):
            raise TypeError("Analyze context callbacks must be callable")
    if postprocess_parts is not None and not callable(postprocess_parts):
        raise TypeError("postprocess_parts must be callable or None")
    if isinstance(batch_size, bool) or type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    raw_names = request.get("object_names")
    if not isinstance(raw_names, list) or any(
        not isinstance(name, str) or not name for name in raw_names
    ):
        raise AnalyzeContextError(
            "Analyze context request requires exact object names."
        )
    object_names = list(raw_names)

    def check_cancelled() -> None:
        if cancellation_check is not None and cancellation_check():
            raise AnalyzeContextCancelled(
                "Analyze context preparation was cancelled."
            )

    detached_parts = request.get("detached_parts")
    if detached_parts is not None:
        if not isinstance(detached_parts, list) or any(
            not isinstance(part, Mapping) for part in detached_parts
        ):
            raise AnalyzeContextError("Detached Analyze context is malformed.")
        check_cancelled()
        parts = [deepcopy(dict(part)) for part in detached_parts]
        if progress_callback is not None:
            progress_callback(
                85,
                f"Analyzing objects {len(object_names)} of {len(object_names)}",
            )
    else:
        parts = []
        batch_count = max(1, (len(object_names) + batch_size - 1) // batch_size)
        for index, offset in enumerate(range(0, len(object_names), batch_size)):
            check_cancelled()
            names = object_names[offset : offset + batch_size]
            part = dispatch_to_document_thread(
                lambda names=names: capture_batch(request, names)
            )
            if not isinstance(part, Mapping):
                raise AnalyzeContextError(
                    "Analyze context batch capture returned no object."
                )
            parts.append(dict(part))
            if progress_callback is not None:
                progress_callback(
                    5 + int(80 * (index + 1) / batch_count),
                    f"Analyzing objects "
                    f"{min(offset + len(names), len(object_names))}"
                    f" of {len(object_names)}",
                )

    if postprocess_parts is not None:
        check_cancelled()
        processed = postprocess_parts(
            request,
            parts,
            cancellation_check,
            progress_callback,
        )
        if not isinstance(processed, Sequence) or any(
            not isinstance(part, Mapping) for part in processed
        ):
            raise AnalyzeContextError(
                "Analyze context postprocessing returned invalid batch data."
            )
        parts = [dict(part) for part in processed]

    check_cancelled()
    detached_clipping = request.get("detached_clipping")
    clipping = (
        deepcopy(dict(detached_clipping))
        if isinstance(detached_clipping, Mapping)
        else dispatch_to_document_thread(lambda: capture_clipping(request))
    )
    if not isinstance(clipping, Mapping):
        raise AnalyzeContextError(
            "Analyze clipping capture returned no object."
        )
    check_cancelled()
    if progress_callback is not None:
        progress_callback(90, "Finalizing detached Analyze context")
    result = finalize(request, parts, clipping)
    if not isinstance(result, dict):
        raise AnalyzeContextError(
            "Analyze context finalization returned no object."
        )
    return result
