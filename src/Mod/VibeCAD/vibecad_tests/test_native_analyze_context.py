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
from VibeCADNativeAnalyzeSnapshot import (
    begin_analyze_snapshot_capture,
    finish_analyze_snapshot_capture,
)


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


def test_analyze_capture_schedules_only_effectively_available_objects(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot

    next_id = 0

    def obj(
        name: str,
        *,
        type_id: str = "Part::Feature",
        visible: bool = True,
        suppressed: bool = False,
    ):
        nonlocal next_id
        next_id += 1
        return SimpleNamespace(
            ID=next_id,
            Name=name,
            TypeId=type_id,
            PropertiesList=("Shape",),
            ViewObject=SimpleNamespace(Visibility=visible),
            Suppressed=suppressed,
            getParentGroup=lambda: None,
            getParentGeoFeatureGroup=lambda: None,
        )

    visible = obj("VisibleBody")
    hidden = obj("HiddenBody", visible=False)
    suppressed = obj("SuppressedBody", suppressed=True)
    history_inactive = obj("FutureBody")
    analysis = obj(
        "Analysis",
        type_id="Fem::FemAnalysis",
        visible=False,
    )
    document = SimpleNamespace(
        Uid="document-a",
        Objects=(visible, hidden, suppressed, history_inactive, analysis),
        isObjectUsableAtCurrentTimelinePosition=(
            lambda candidate: candidate is not history_inactive
        ),
    )
    monkeypatch.setattr(analyze_snapshot, "_active_analysis", lambda _document: None)
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )

    request = begin_analyze_snapshot_capture(document, validate_brep=False)

    assert request["object_names"] == ["VisibleBody", "Analysis"]
    assert request["analysis_names"] == ["Analysis"]


def test_analyze_snapshot_preserves_independent_background_job_scopes() -> None:
    snapshot = finish_analyze_snapshot_capture(
        {
            "analysis_names": [],
            "background_job": [
                {
                    "job_id": "job-a",
                    "resource_scope": "analyze:StudyA",
                    "phase": "running",
                    "terminal": False,
                },
                {
                    "job_id": "job-b",
                    "resource_scope": "analyze:StudyB",
                    "phase": "completed",
                    "terminal": True,
                },
            ],
        },
        [{"analyses": [], "solver_states": {}}],
        {},
    )

    assert snapshot["run_status"]["phase"] == "running"
    assert snapshot["run_status"]["terminal"] is False
    assert [
        job["resource_scope"] for job in snapshot["run_status"]["background_jobs"]
    ] == ["analyze:StudyA", "analyze:StudyB"]


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


def test_pre_detached_analyze_context_never_dispatches_to_the_document_thread() -> None:
    request = _request(count=1)
    request["detached_parts"] = [{"captured": ["VisibleBody"]}]
    request["detached_clipping"] = {"available": False}

    result = capture_responsive_analyze_snapshot(
        request,
        dispatch_to_document_thread=lambda _operation: pytest.fail(
            "detached Analyze preparation must not return to the document thread"
        ),
        capture_batch=lambda _request, _names: pytest.fail(
            "detached Analyze preparation must not recapture live objects"
        ),
        capture_clipping=lambda _request: pytest.fail(
            "detached Analyze preparation must not recapture clipping state"
        ),
        finalize=lambda _request, parts, clipping: {
            "parts": list(parts),
            "clipping": dict(clipping),
        },
    )

    assert result == {
        "parts": [{"captured": ["VisibleBody"]}],
        "clipping": {"available": False},
    }


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


def test_detached_analyze_session_uses_exactly_one_document_thread_dispatch(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module
    import VibeCADSession as session_module

    request = _request(count=1)
    request["cacheable"] = False
    request["detached_parts"] = [{"captured": ["VisibleBody"]}]
    request["detached_clipping"] = {"available": False}
    request["base_snapshot"]["_selection"] = {
        "document_uid": "document-a",
        "items": [],
    }
    in_dispatch = False
    dispatch_count = 0

    class _Service:
        @staticmethod
        def begin_native_analyze_context_request():
            assert in_dispatch is True
            return deepcopy(request)

        @staticmethod
        def native_analyze_context_coordinator():
            return AnalyzeContextCoordinator()

        @staticmethod
        def capture_native_analyze_context_batch(_request, _names):
            pytest.fail("detached Analyze context must not recapture live objects")

        @staticmethod
        def capture_native_analyze_context_clipping(_request):
            pytest.fail("detached Analyze context must not recapture clipping state")

    def dispatch(operation):
        nonlocal in_dispatch, dispatch_count
        assert in_dispatch is False
        dispatch_count += 1
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    monkeypatch.setattr(
        analyze_snapshot_module,
        "finish_analyze_snapshot_capture",
        lambda _request, parts, clipping: {
            "kind": "analyze",
            "parts": list(parts),
            "clipping": dict(clipping),
        },
    )

    result = session_module._build_responsive_analyze_native_state(
        _Service(),
        dispatch,
    )

    assert result["domain"]["parts"] == [{"captured": ["VisibleBody"]}]
    assert dispatch_count == 1


def test_cached_analyze_session_uses_initial_detached_clipping() -> None:
    import VibeCADSession as session_module

    coordinator = AnalyzeContextCoordinator()
    coordinator.get_or_build(
        "document-a",
        7,
        lambda _cancelled, _progress: {
            "kind": "analyze",
            "clipping": {"available": False},
        },
    )
    request = _request(count=1)
    request["cacheable"] = True
    request["detached_clipping"] = {"available": True, "mode": "plane"}
    request["base_snapshot"]["_selection"] = {
        "document_uid": "document-a",
        "items": [],
    }
    in_dispatch = False
    dispatch_count = 0

    class _Service:
        @staticmethod
        def begin_native_analyze_context_request():
            assert in_dispatch is True
            return deepcopy(request)

        @staticmethod
        def native_analyze_context_coordinator():
            return coordinator

        @staticmethod
        def capture_native_analyze_context_batch(_request, _names):
            pytest.fail("a cached Analyze context must not recapture live objects")

        @staticmethod
        def capture_native_analyze_context_clipping(_request):
            pytest.fail("initial detached clipping must satisfy the cache refresh")

    def dispatch(operation):
        nonlocal in_dispatch, dispatch_count
        assert in_dispatch is False
        dispatch_count += 1
        in_dispatch = True
        try:
            return operation()
        finally:
            in_dispatch = False

    result = session_module._build_responsive_analyze_native_state(
        _Service(),
        dispatch,
    )

    assert result["domain"]["clipping"] == {"available": True, "mode": "plane"}
    assert dispatch_count == 1


class _Shape:
    def __init__(self) -> None:
        self.validity_checks = 0
        self.export_calls = 0
        self.Solids = [object()]
        self.Faces = []
        self.Edges = []

    def isNull(self) -> bool:
        return False

    def isValid(self) -> bool:
        self.validity_checks += 1
        return True

    def exportBrep(self, _path: str) -> None:
        self.export_calls += 1
        raise AssertionError("turn-start Analyze context must not export BREP")


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


def test_turn_start_context_captures_geometry_without_brep_validation(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module

    document = SimpleNamespace(Uid="document-a", Objects=[])
    obj = _GeometryObject(document)
    document.Objects = [obj]
    document.getObject = lambda name: obj if name == obj.Name else None
    monkeypatch.setitem(
        sys.modules,
        "PartGui",
        SimpleNamespace(isModelingObjectActive=lambda _obj: True),
    )
    monkeypatch.setattr(
        analyze_snapshot_module,
        "mesh_object_state",
        lambda current: {
            "object_name": current.Name,
            "topology": {"solids": 1, "faces": 0, "edges": 0},
            "state_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        analyze_snapshot_module,
        "clipping_face_source_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "turn-start Analyze context must not validate clipping faces"
            )
        ),
    )

    request = analyze_snapshot_module.begin_analyze_snapshot_capture(
        document,
        validate_brep=False,
    )
    part = analyze_snapshot_module.capture_analyze_snapshot_batch(
        document,
        request,
        [obj.Name],
    )

    assert request["validate_brep"] is False
    assert request["defer_brep_validation"] is False
    assert request["geometry_validation_artifact_root"] == ""
    assert part["geometry_source_count"] == 1
    assert part["geometry_sources"][0]["object_name"] == obj.Name
    assert part["geometry_validation_artifacts"] == []
    assert obj.Shape.validity_checks == 0
    assert obj.Shape.export_calls == 0


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


def test_analyze_snapshot_uses_context_mesh_state_and_skips_definitions_as_outputs(
    monkeypatch,
) -> None:
    import VibeCADNativeAnalyzeSnapshot as snapshot

    definition = object()
    document = type("Document", (), {"Objects": [definition]})()
    definition_calls = []
    output_calls = []
    monkeypatch.setattr(
        snapshot,
        "is_fem_mesh_definition",
        lambda obj: obj is definition,
        raising=False,
    )
    monkeypatch.setattr(
        snapshot,
        "fem_mesh_definition_context_state",
        lambda obj: definition_calls.append(obj)
        or {"object_name": "Mesh", "state_sha256": "a" * 64},
        raising=False,
    )
    monkeypatch.setattr(
        snapshot,
        "fem_mesh_object_context_state",
        lambda obj: output_calls.append(obj)
        or {"object_name": "Output", "state_sha256": "b" * 64},
        raising=False,
    )

    count, definitions, _states = snapshot._mesh_definitions(document)
    output_count, outputs = snapshot._fem_mesh_outputs(document)

    assert count == 1
    assert definitions[0]["object_name"] == "Mesh"
    assert output_count == 0
    assert outputs == []
    assert definition_calls == [definition]
    assert output_calls == []


def test_analyze_study_record_uses_bounded_mesh_context_state(monkeypatch) -> None:
    import VibeCADNativeAnalyzeSnapshot as snapshot

    analysis = SimpleNamespace(Name="Analysis", Group=[])
    observed = []
    monkeypatch.setattr(
        snapshot,
        "analysis_state",
        lambda _analysis: {"object_name": "Analysis", "state_sha256": "a" * 64},
    )
    monkeypatch.setattr(
        snapshot,
        "result_purge_state",
        lambda _analysis: {
            "object_count": 0,
            "solver_result_root_count": 0,
            "ordinary_operation_count": 0,
            "purge_ready": True,
            "blockers": [],
            "graph_sha256": "b" * 64,
        },
    )

    def bounded_study(_analysis, *, mesh_state_reader=None):
        observed.append(mesh_state_reader)
        return {"intent": {}, "inventory": {}}

    monkeypatch.setattr(snapshot, "study_state", bounded_study)

    snapshot._analysis_capture_record(
        analysis,
        active_analysis_name="Analysis",
        include_summary=True,
    )

    assert observed == [snapshot.fem_mesh_definition_context_state]


def test_analyze_mesh_state_cache_reuses_shape_topology_within_one_capture(
    monkeypatch,
) -> None:
    import VibeCADNativeMeshState as mesh_state

    class Shape:
        def __init__(self) -> None:
            self.face_reads = 0

        @property
        def Faces(self):
            self.face_reads += 1
            return [object(), object()]

        Vertexes = ()
        Edges = ()
        Wires = ()
        Shells = ()
        Solids = (object(),)
        BoundBox = None

    shape = Shape()
    obj = SimpleNamespace(
        Name="Body",
        Label="Body",
        TypeId="PartDesign::Body",
        Shape=shape,
        PropertiesList=(),
    )
    monkeypatch.setattr(
        mesh_state,
        "concise_object",
        lambda _obj: {"object_name": "Body", "label": "Body"},
    )

    with mesh_state.mesh_object_state_cache():
        first = mesh_state.mesh_object_state(obj)
        second = mesh_state.mesh_object_state(obj)

    assert first == second
    assert first is not second
    assert shape.face_reads == 1
