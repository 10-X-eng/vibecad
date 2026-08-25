# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from VibeCADNativeAnalyzeContext import (
    AnalyzeContextCancelled,
    AnalyzeContextCoordinator,
    AnalyzeContextStale,
    capture_responsive_analyze_snapshot,
)
from VibeCADNativeAnalyzeGeometrySources import active_analyze_geometry_sources


def _request(revision: int = 7, count: int = 10) -> dict:
    return {
        "document_uid": "document-a",
        "structural_revision": revision,
        "object_names": [f"Object{index}" for index in range(count)],
        "base_snapshot": {
            "surface_id": "analyze",
            "document": {
                "document_uid": "document-a",
                "document_name": "DocumentA",
            },
            "structural_revision": revision,
            "working_set": [],
        },
        "background_job": None,
    }


def test_context_coordinator_coalesces_callers_and_reuses_the_revision() -> None:
    coordinator = AnalyzeContextCoordinator()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    results = []

    def build(cancelled, progress):
        calls.append("build")
        entered.set()
        progress(25, "Capturing Analyze state")
        assert release.wait(1.0)
        assert cancelled() is False
        return {"revision": 7, "items": ["captured"]}

    def read() -> None:
        results.append(
            coordinator.get_or_build(
                "document-a",
                7,
                build,
            )
        )

    first = threading.Thread(target=read)
    second = threading.Thread(target=read)
    first.start()
    assert entered.wait(1.0)
    second.start()
    time.sleep(0.02)
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert calls == ["build"]
    assert results == [
        {"revision": 7, "items": ["captured"]},
        {"revision": 7, "items": ["captured"]},
    ]
    assert results[0] is not results[1]

    cached = coordinator.get_or_build(
        "document-a",
        7,
        lambda *_args: pytest.fail("the current revision must be cached"),
    )
    assert cached == results[0]
    assert cached is not results[0]


def test_context_coordinator_uses_revision_keys_and_discards_stale_completion() -> None:
    coordinator = AnalyzeContextCoordinator()
    entered = threading.Event()
    release = threading.Event()
    outcome = []

    def build(cancelled, _progress):
        entered.set()
        assert release.wait(1.0)
        assert cancelled() is True
        return {"revision": 7}

    def read() -> None:
        try:
            coordinator.get_or_build("document-a", 7, build)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(1.0)
    coordinator.invalidate_document("document-a")
    release.set()
    worker.join(1.0)

    assert len(outcome) == 1
    assert isinstance(outcome[0], AnalyzeContextStale)

    rebuilt = coordinator.get_or_build(
        "document-a",
        8,
        lambda _cancelled, _progress: {"revision": 8},
    )
    assert rebuilt == {"revision": 8}


def test_context_waiter_can_cancel_without_cancelling_shared_capture() -> None:
    coordinator = AnalyzeContextCoordinator()
    entered = threading.Event()
    release = threading.Event()
    owner_result = []

    def build(_cancelled, _progress):
        entered.set()
        assert release.wait(1.0)
        return {"revision": 7}

    owner = threading.Thread(
        target=lambda: owner_result.append(
            coordinator.get_or_build("document-a", 7, build)
        )
    )
    owner.start()
    assert entered.wait(1.0)

    with pytest.raises(AnalyzeContextCancelled):
        coordinator.get_or_build(
            "document-a",
            7,
            build,
            cancellation_check=lambda: True,
        )

    release.set()
    owner.join(1.0)
    assert owner_result == [{"revision": 7}]


def test_responsive_capture_dispatches_bounded_document_thread_batches() -> None:
    request = _request(count=10)
    dispatches = []
    batches = []

    def dispatch(operation):
        dispatches.append(threading.get_ident())
        return operation()

    def capture_batch(current, names):
        assert current == request
        batches.append(list(names))
        return {"captured": list(names)}

    result = capture_responsive_analyze_snapshot(
        request,
        dispatch_to_document_thread=dispatch,
        capture_batch=capture_batch,
        capture_clipping=lambda current: {
            "available": current["document_uid"] == "document-a"
        },
        finalize=lambda current, parts, clipping: {
            "request": deepcopy(current),
            "parts": list(parts),
            "clipping": dict(clipping),
        },
        batch_size=3,
    )

    assert batches == [
        ["Object0", "Object1", "Object2"],
        ["Object3", "Object4", "Object5"],
        ["Object6", "Object7", "Object8"],
        ["Object9"],
    ]
    assert len(dispatches) == 5
    assert result["clipping"] == {"available": True}
    assert [part["captured"] for part in result["parts"]] == batches


def test_responsive_capture_postprocesses_detached_parts_outside_dispatch() -> None:
    request = _request(count=4)
    in_dispatch = False
    postprocessed = []

    def dispatch(operation):
        nonlocal in_dispatch
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    def postprocess(_request, parts, _cancelled, _progress):
        assert in_dispatch is False
        postprocessed.append(True)
        return [{**part, "validated": True} for part in parts]

    result = capture_responsive_analyze_snapshot(
        request,
        dispatch_to_document_thread=dispatch,
        capture_batch=lambda _current, names: {"captured": list(names)},
        capture_clipping=lambda _current: {},
        finalize=lambda _current, parts, _clipping: {"parts": list(parts)},
        postprocess_parts=postprocess,
        batch_size=2,
    )

    assert postprocessed == [True]
    assert all(part["validated"] is True for part in result["parts"])


def test_responsive_capture_checks_cancellation_between_batches() -> None:
    request = _request(count=5)
    completed_batches = 0

    def capture_batch(_current, names):
        nonlocal completed_batches
        completed_batches += 1
        return {"captured": list(names)}

    with pytest.raises(AnalyzeContextCancelled):
        capture_responsive_analyze_snapshot(
            request,
            dispatch_to_document_thread=lambda operation: operation(),
            capture_batch=capture_batch,
            capture_clipping=lambda _current: {},
            finalize=lambda _current, _parts, _clipping: {},
            cancellation_check=lambda: completed_batches == 1,
            batch_size=2,
        )

    assert completed_batches == 1


def test_session_capture_uses_document_dispatches_then_reuses_cache(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module
    import VibeCADSession as session_module

    coordinator = AnalyzeContextCoordinator()
    request = _request(count=10)
    request["cacheable"] = True
    request["base_snapshot"]["_selection"] = {
        "document_uid": "document-a",
        "items": [],
    }
    capture_calls = []
    in_dispatch = False
    begin_calls = 0

    class _Service:
        def begin_native_analyze_context_request(self):
            nonlocal begin_calls
            assert in_dispatch
            begin_calls += 1
            current = deepcopy(request)
            if begin_calls > 1:
                current["base_snapshot"]["selection"] = {
                    "document_uid": "document-a",
                    "items": [{"object": {"object_name": "Object9"}}],
                }
            return current

        @staticmethod
        def native_analyze_context_coordinator():
            return coordinator

        @staticmethod
        def capture_native_analyze_context_batch(current, names):
            assert in_dispatch
            capture_calls.append(list(names))
            return {"captured": list(names)}

        @staticmethod
        def capture_native_analyze_context_clipping(current):
            assert in_dispatch
            return {"available": current["document_uid"] == "document-a"}

    def dispatch(operation):
        nonlocal in_dispatch
        assert in_dispatch is False
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    monkeypatch.setattr(
        analyze_snapshot_module,
        "finish_analyze_snapshot_capture",
        lambda current, parts, clipping: {
            "kind": "analyze",
            "captured_names": [
                name for part in parts for name in part["captured"]
            ],
            "clipping": dict(clipping),
        },
    )
    events = []

    first = session_module._build_responsive_analyze_native_state(
        _Service(),
        dispatch,
        progress_callback=events.append,
    )
    second = session_module._build_responsive_analyze_native_state(
        _Service(),
        dispatch,
        progress_callback=events.append,
    )

    assert first["domain"] == second["domain"]
    assert "selection" not in first
    assert second["selection"]["items"][0]["object"]["object_name"] == "Object9"
    assert first["domain"]["captured_names"] == request["object_names"]
    assert capture_calls == [
        request["object_names"][:8],
        request["object_names"][8:],
    ]
    assert any(event["event"] == "analyze_context_progress" for event in events)
    assert any(event["event"] == "analyze_context_ready" for event in events)
    assert any(event["event"] == "analyze_context_cache_hit" for event in events)


class _Shape:
    def __init__(self) -> None:
        self.validity_checks = 0
        self.Solids = [object()]
        self.Faces = []
        self.Edges = []

    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        self.validity_checks += 1
        return True


class _GeometryObject:
    def __init__(self, document) -> None:
        self.Document = document
        self.Name = "Body"
        self.ID = 1
        self.TypeId = "PartDesign::Body"
        self.Shape = _Shape()
        self.VibeCADTimelineRole = ""
        self.VibeCADAnalysisDomain = False

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id == "PartDesign::Body"

    def getParentGeoFeatureGroup(self):
        return None


def test_batched_geometry_discovery_keeps_shape_validation(monkeypatch) -> None:
    document = SimpleNamespace(Uid="document-a", Objects=[])
    obj = _GeometryObject(document)
    document.Objects = [obj]
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    captured = active_analyze_geometry_sources(
        document,
        filter_analysis_sources=False,
    )

    assert captured == (obj,)
    assert obj.Shape.validity_checks == 1


def test_context_geometry_discovery_defers_unbounded_brep_validation(
    monkeypatch,
) -> None:
    document = SimpleNamespace(Uid="document-a", Objects=[])
    obj = _GeometryObject(document)
    document.Objects = [obj]
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    captured = active_analyze_geometry_sources(
        document,
        filter_analysis_sources=False,
        validate_brep=False,
    )

    assert captured == (obj,)
    assert obj.Shape.validity_checks == 0


def test_responsive_context_request_marks_geometry_validation_as_deferred() -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module

    document = SimpleNamespace(
        Uid="document-a",
        Objects=[],
    )

    request = analyze_snapshot_module.begin_analyze_snapshot_capture(
        document,
        defer_brep_validation=True,
    )

    assert request["defer_brep_validation"] is True
    assert request["geometry_validation_artifact_root"]


def test_analyze_geometry_postprocess_keeps_only_isolated_valid_results(
    monkeypatch,
    tmp_path,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module

    artifacts = [
        {"shape_path": str(tmp_path / "valid.brep"), "identity": "valid"},
        {"shape_path": str(tmp_path / "invalid.brep"), "identity": "invalid"},
    ]
    for artifact in artifacts:
        Path(artifact["shape_path"]).write_bytes(b"BREP")
    monkeypatch.setattr(
        "VibeCADGeometry.validate_brep_artifacts_parallel",
        lambda _artifacts, **_kwargs: [
            {"identity": "valid", "ok": True, "valid": True},
            {"identity": "invalid", "ok": True, "valid": False},
        ],
    )
    parts = [
        {
            "geometry_source_count": 2,
            "geometry_sources": [
                {"object_name": "Valid", "_brep_validation_identity": "valid"},
                {"object_name": "Invalid", "_brep_validation_identity": "invalid"},
            ],
            "geometry_validation_artifacts": artifacts,
        }
    ]

    processed = analyze_snapshot_module.validate_analyze_snapshot_geometry(
        {"defer_brep_validation": True},
        parts,
        None,
        None,
    )

    assert processed[0]["geometry_source_count"] == 1
    assert processed[0]["geometry_sources"] == [{"object_name": "Valid"}]
    assert "geometry_validation_artifacts" not in processed[0]
