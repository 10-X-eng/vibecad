# SPDX-License-Identifier: LGPL-2.1-or-later

"""Responsive, revision-safe preparation of Native Drawing source context."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import threading
from typing import Any


DEFAULT_DRAWING_SOURCE_BATCH_SIZE = 8
_WAIT_POLL_SECONDS = 0.05

CancellationCheck = Callable[[], bool]
ProgressCallback = Callable[[int, str], None]
CatalogBuilder = Callable[
    [CancellationCheck, ProgressCallback],
    dict[str, Any],
]
DocumentThreadDispatcher = Callable[[Callable[[], Any]], Any]


class DrawingContextError(RuntimeError):
    """Drawing provider context could not be prepared safely."""


class DrawingContextCancelled(DrawingContextError):
    """The caller stopped waiting for Drawing provider context."""


class DrawingContextStale(DrawingContextError):
    """The Drawing document changed while source context was captured."""


def _context_key(document_uid: str, structural_revision: int) -> tuple[str, int]:
    uid = str(document_uid or "").strip()
    if not uid:
        raise DrawingContextError("Drawing context requires an exact document UID.")
    if isinstance(structural_revision, bool):
        raise DrawingContextError("Drawing context requires an integer revision.")
    try:
        revision = int(structural_revision)
    except (TypeError, ValueError) as exc:
        raise DrawingContextError(
            "Drawing context requires an integer revision."
        ) from exc
    if revision < 0:
        raise DrawingContextError(
            "Drawing context requires a non-negative revision."
        )
    return uid, revision


@dataclass
class _PendingCatalog:
    document_uid: str
    structural_revision: int
    invalidated: threading.Event = field(default_factory=threading.Event)
    completed: bool = False
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    progress_percent: int = 0
    progress_message: str = ""


class DrawingSourceCatalogCoordinator:
    """Coalesce concurrent catalog capture without owning live CAD objects.

    Completed data lives in ``VibeCADNativeDrawingSourceCatalog``. This class
    only coordinates an in-flight build, so there is one source scan per exact
    document revision even when tab prewarming and Send overlap.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[tuple[str, int], _PendingCatalog] = {}

    def get_or_build(
        self,
        document_uid: str,
        structural_revision: int,
        build: CatalogBuilder,
        *,
        cancellation_check: CancellationCheck | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not callable(build):
            raise TypeError("build must be callable")
        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable")
        if progress_callback is not None and not callable(progress_callback):
            raise TypeError("progress_callback must be callable")
        key = _context_key(document_uid, structural_revision)
        with self._condition:
            pending = self._pending.get(key)
            owner = pending is None
            if pending is None:
                pending = _PendingCatalog(*key)
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
                raise DrawingContextError(
                    "Drawing context progress must be an integer."
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
                raise DrawingContextError(
                    "Drawing source preparation must return an object."
                )
            with self._condition:
                if pending.invalidated.is_set():
                    raise DrawingContextStale(
                        "The Drawing document changed while sources were prepared."
                    )
                pending.result = deepcopy(result)
            return deepcopy(result)
        except BaseException as exc:
            reported = (
                DrawingContextStale(
                    "The Drawing document changed while sources were prepared."
                )
                if pending.invalidated.is_set()
                and isinstance(exc, DrawingContextCancelled)
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
        pending: _PendingCatalog,
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
                        raise DrawingContextError(
                            "Drawing source preparation completed without a result."
                        )
                    return deepcopy(pending.result)
                current_progress = (
                    pending.progress_percent,
                    pending.progress_message,
                )
                self._condition.wait(_WAIT_POLL_SECONDS)
            if cancellation_check is not None and cancellation_check():
                raise DrawingContextCancelled(
                    "Drawing source preparation was cancelled."
                )
            if progress_callback is not None and current_progress != reported_progress:
                progress_callback(*current_progress)
                reported_progress = current_progress

    def invalidate_document(self, document_uid: str) -> bool:
        uid = str(document_uid or "").strip()
        if not uid:
            return False
        changed = False
        with self._condition:
            for key, pending in tuple(self._pending.items()):
                if key[0] == uid:
                    pending.invalidated.set()
                    changed = True
            if changed:
                self._condition.notify_all()
        return changed

    def close_document(self, document_uid: str) -> bool:
        return self.invalidate_document(document_uid)


def capture_responsive_drawing_source_catalog(
    request: Mapping[str, Any],
    *,
    dispatch_to_document_thread: DocumentThreadDispatcher,
    capture_batch: Callable[
        [Mapping[str, Any], Sequence[str]],
        Mapping[str, Any],
    ],
    finalize: Callable[
        [Mapping[str, Any], Sequence[Mapping[str, Any]]],
        dict[str, Any],
    ],
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
    batch_size: int = DEFAULT_DRAWING_SOURCE_BATCH_SIZE,
) -> dict[str, Any]:
    """Capture live source facts in bounded Qt dispatches, then cache detached."""

    if not isinstance(request, Mapping):
        raise TypeError("request must be a mapping")
    for callback in (dispatch_to_document_thread, capture_batch, finalize):
        if not callable(callback):
            raise TypeError("Drawing context callbacks must be callable")
    if isinstance(batch_size, bool) or type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    raw_names = request.get("object_names")
    if not isinstance(raw_names, list) or any(
        not isinstance(name, str) or not name for name in raw_names
    ):
        raise DrawingContextError(
            "Drawing context request requires exact object names."
        )
    object_names = list(raw_names)

    def check_cancelled() -> None:
        if cancellation_check is not None and cancellation_check():
            raise DrawingContextCancelled(
                "Drawing source preparation was cancelled."
            )

    sources: list[dict[str, Any]] = []
    batch_count = max(1, (len(object_names) + batch_size - 1) // batch_size)
    for index, offset in enumerate(range(0, len(object_names), batch_size)):
        check_cancelled()
        names = object_names[offset : offset + batch_size]
        part = dispatch_to_document_thread(
            lambda names=names: capture_batch(request, names)
        )
        if not isinstance(part, Mapping):
            raise DrawingContextError(
                "Drawing context batch capture returned no object."
            )
        batch_sources = part.get("sources")
        if not isinstance(batch_sources, list) or any(
            not isinstance(source, Mapping) for source in batch_sources
        ):
            raise DrawingContextError(
                "Drawing context batch returned invalid source data."
            )
        sources.extend(dict(source) for source in batch_sources)
        if progress_callback is not None:
            progress_callback(
                5 + int(80 * (index + 1) / batch_count),
                f"Reading Drawing sources "
                f"{min(offset + len(names), len(object_names))}"
                f" of {len(object_names)}",
            )
    if not object_names and progress_callback is not None:
        progress_callback(85, "Reading Drawing sources 0 of 0")

    check_cancelled()
    result = finalize(request, sources)
    if not isinstance(result, dict):
        raise DrawingContextError(
            "Drawing context finalization returned no object."
        )
    return result


__all__ = [
    "DEFAULT_DRAWING_SOURCE_BATCH_SIZE",
    "DrawingContextCancelled",
    "DrawingContextError",
    "DrawingContextStale",
    "DrawingSourceCatalogCoordinator",
    "capture_responsive_drawing_source_catalog",
]
