# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from VibeCADNativeDrawingContext import (
    DrawingContextCancelled,
    DrawingSourceCatalogCoordinator,
    capture_responsive_drawing_source_catalog,
)


def _request(*, revision: int = 7, count: int = 18) -> dict:
    return {
        "document_uid": "document-a",
        "structural_revision": revision,
        "object_names": [f"Object{index}" for index in range(count)],
    }


def test_responsive_drawing_catalog_dispatches_bounded_document_batches() -> None:
    request = _request(count=18)
    batches = []
    in_dispatch = False
    finalized = []
    progress = []

    def dispatch(operation):
        nonlocal in_dispatch
        assert in_dispatch is False
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    def capture_batch(current, names):
        assert in_dispatch is True
        assert current is request
        batches.append(list(names))
        return {
            "sources": [
                {"object_name": name, "type_id": "PartDesign::Body"}
                for name in names
            ]
        }

    def finalize(current, sources):
        assert in_dispatch is False
        finalized.append((current, list(sources)))
        return {"source_count": len(sources)}

    result = capture_responsive_drawing_source_catalog(
        request,
        dispatch_to_document_thread=dispatch,
        capture_batch=capture_batch,
        finalize=finalize,
        progress_callback=lambda percent, message: progress.append(
            (percent, message)
        ),
    )

    assert batches == [
        request["object_names"][:8],
        request["object_names"][8:16],
        request["object_names"][16:],
    ]
    assert result == {"source_count": 18}
    assert len(finalized) == 1
    assert [item["object_name"] for item in finalized[0][1]] == request[
        "object_names"
    ]
    assert progress[-1] == (85, "Reading Drawing sources 18 of 18")


def test_responsive_drawing_catalog_checks_cancellation_between_batches() -> None:
    request = _request(count=9)
    completed_batches = 0

    def capture_batch(_current, names):
        nonlocal completed_batches
        completed_batches += 1
        return {"sources": [{"object_name": name} for name in names]}

    with pytest.raises(DrawingContextCancelled):
        capture_responsive_drawing_source_catalog(
            request,
            dispatch_to_document_thread=lambda operation: operation(),
            capture_batch=capture_batch,
            finalize=lambda _current, _sources: {},
            cancellation_check=lambda: completed_batches == 1,
        )

    assert completed_batches == 1


def test_detached_drawing_catalog_never_dispatches_to_the_document_thread() -> None:
    request = _request(count=1)
    request["detached_sources"] = [
        {"object_name": "VisibleBody", "type_id": "PartDesign::Body"}
    ]
    progress = []

    result = capture_responsive_drawing_source_catalog(
        request,
        dispatch_to_document_thread=lambda _operation: pytest.fail(
            "detached Drawing preparation must not return to the document thread"
        ),
        capture_batch=lambda _request, _names: pytest.fail(
            "detached Drawing preparation must not recapture live objects"
        ),
        finalize=lambda _request, sources: {
            "source_names": [source["object_name"] for source in sources]
        },
        progress_callback=lambda percent, message: progress.append(
            (percent, message)
        ),
    )

    assert result == {"source_names": ["VisibleBody"]}
    assert progress == [(85, "Reading Drawing sources 1 of 1")]


def test_visibility_change_invalidates_drawing_cache_without_structural_revision() -> None:
    from VibeCADCore import VibeCADService

    invalidated = []
    service = VibeCADService.__new__(VibeCADService)
    service._object_document_uid = lambda _obj: "document-a"
    service._native_document_states = SimpleNamespace(
        current_revision=lambda _uid: 7,
        note_object_property_change=lambda _uid, _property: 7,
    )
    service._invalidate_native_read_contexts = invalidated.append
    service._sync_native_authority_metadata_if_active = lambda _uid: None

    revision = service.note_native_object_property_change(object(), "Visibility")

    assert revision == 7
    assert invalidated == ["document-a"]


def test_drawing_catalog_coordinator_coalesces_concurrent_preparation() -> None:
    coordinator = DrawingSourceCatalogCoordinator()
    entered = threading.Event()
    release = threading.Event()
    builds = []
    results = []

    def build(_cancelled, _progress):
        builds.append("build")
        entered.set()
        assert release.wait(1.0)
        return {"source_count": 18}

    def prepare() -> None:
        results.append(coordinator.get_or_build("document-a", 7, build))

    first = threading.Thread(target=prepare)
    second = threading.Thread(target=prepare)
    first.start()
    assert entered.wait(1.0)
    second.start()
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert builds == ["build"]
    assert results == [{"source_count": 18}, {"source_count": 18}]


def test_session_build_batches_once_then_reuses_the_revision_cache() -> None:
    import VibeCADSession as session_module
    from VibeCADNativeDrawingSourceCatalog import (
        invalidate_drawing_source_catalog_cache,
    )

    invalidate_drawing_source_catalog_cache()
    coordinator = DrawingSourceCatalogCoordinator()
    request = _request(count=10)
    capture_calls = []
    in_dispatch = False

    class _Service:
        @staticmethod
        def begin_native_drawing_context_request():
            assert in_dispatch is True
            return dict(request)

        @staticmethod
        def native_drawing_context_coordinator():
            return coordinator

        @staticmethod
        def capture_native_drawing_context_batch(_request, names):
            assert in_dispatch is True
            capture_calls.append(list(names))
            return {
                "sources": [
                    {"object_name": name, "type_id": "PartDesign::Body"}
                    for name in names
                ]
            }

        @staticmethod
        def finish_native_drawing_context_request(current):
            assert in_dispatch is True
            return {
                "surface_id": "drawing",
                "structural_revision": current["structural_revision"],
            }

    def dispatch(operation):
        nonlocal in_dispatch
        assert in_dispatch is False
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    events = []
    first = session_module._build_responsive_drawing_native_state(
        _Service(),
        dispatch,
        progress_callback=events.append,
    )
    second = session_module._build_responsive_drawing_native_state(
        _Service(),
        dispatch,
        progress_callback=events.append,
    )

    assert first == second == {
        "surface_id": "drawing",
        "structural_revision": 7,
    }
    assert capture_calls == [request["object_names"][:8], request["object_names"][8:]]
    assert any(event["event"] == "drawing_context_progress" for event in events)
    assert any(event["event"] == "drawing_context_ready" for event in events)
    assert any(event["event"] == "drawing_context_cache_hit" for event in events)


def test_detached_session_build_uses_exactly_one_document_thread_dispatch() -> None:
    import VibeCADSession as session_module
    from VibeCADNativeDrawingSourceCatalog import (
        invalidate_drawing_source_catalog_cache,
    )

    invalidate_drawing_source_catalog_cache()
    coordinator = DrawingSourceCatalogCoordinator()
    expected = {
        "surface_id": "drawing",
        "structural_revision": 7,
        "domain": {"kind": "drawing"},
    }
    request = _request(count=1)
    request["detached_sources"] = [
        {"object_name": "VisibleBody", "type_id": "PartDesign::Body"}
    ]
    request["completed_snapshot"] = expected
    in_dispatch = False
    dispatch_count = 0

    class _Service:
        @staticmethod
        def begin_native_drawing_context_request():
            assert in_dispatch is True
            return dict(request)

        @staticmethod
        def native_drawing_context_coordinator():
            return coordinator

        @staticmethod
        def capture_native_drawing_context_batch(_request, _names):
            pytest.fail("detached Drawing context must not recapture live objects")

        @staticmethod
        def finish_native_drawing_context_request(_request):
            pytest.fail("completed Drawing context must not return to the UI thread")

    def dispatch(operation):
        nonlocal in_dispatch, dispatch_count
        assert in_dispatch is False
        dispatch_count += 1
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    result = session_module._build_responsive_drawing_native_state(
        _Service(),
        dispatch,
    )

    assert result == expected
    assert dispatch_count == 1
