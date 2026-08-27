# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import analysis_fem_execution_route as execution_route
import VibeCADNativeAnalyzeSolverExecutionRuntime as solver_runtime
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeRuntimeContext import NativeRuntimeContext, NativeRuntimeContextError
from VibeCADNativeState import NativeCallTicket, NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


@dataclass
class _Snapshot:
    job_id: str = "fem-job-1"
    capability_name: str = "analyze.solver_execution.run"
    phase: str = "queued"
    progress_percent: int = 0
    progress_message: str = "Queued"
    terminal: bool = False


class _Manager:
    def __init__(self) -> None:
        self.submission: dict[str, object] | None = None

    def submit(self, **kwargs):
        self.submission = dict(kwargs)
        return _Snapshot()


class _State(NativeDocumentStateStore):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled: list[NativeCallTicket] = []

    def cancel_mutation(self, ticket: NativeCallTicket) -> None:
        self.cancelled.append(ticket)
        super().cancel_mutation(ticket)


class _Document:
    def __init__(self, uid: str) -> None:
        self.Uid = uid


def _context(manager: _Manager):
    document = _Document("doc-fem-route")
    active = [document]
    state = _State()
    context = NativeRuntimeContext(
        service=object(),
        document=document,
        state=state,
        undo_ledger=NativeAssistantUndoLedger(),
        reauthorize_turn=lambda: None,
        active_document=lambda: active[0],
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
        background_manager=manager,
        document_thread_dispatch=lambda operation: operation(),
    )
    return context, active, state


def _ticket() -> NativeCallTicket:
    return NativeCallTicket(
        document_uid="doc-fem-route",
        capability_name="analyze.solver_execution.run",
        expected_revision=0,
        idempotency_token="route-ticket",
    )


def _arguments() -> dict[str, object]:
    return {
        "operation": "run",
        "target": {"object_name": "Solver"},
        "timeout_seconds": 30,
    }


def test_internal_route_defaults_to_extracted_analysis_runtime() -> None:
    assert (
        execution_route.current_fem_execution_route()
        == execution_route.ANALYSIS_RUNTIME_FEM
    )


def test_internal_route_is_installed_but_not_a_public_or_durable_setting() -> None:
    root = Path(__file__).resolve().parents[1]
    cmake = (root / "CMakeLists.txt").read_text(encoding="utf-8")
    route_source = (root / "analysis_fem_execution_route.py").read_text(
        encoding="utf-8"
    )
    public_source = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in (
            "VibeCADNativeAnalyzeSolverExecutionBindings.py",
            "VibeCADNativeAnalyzeSolverExecutionSchema.py",
        )
    )

    assert "analysis_fem_execution_route.py" in cmake
    assert "vibecad_tests/test_analysis_fem_execution_route.py" in cmake
    assert "ParamGet" not in route_source
    assert "getenv" not in route_source
    assert execution_route.ANALYSIS_RUNTIME_FEM not in public_source
    assert execution_route.LEGACY_FEM_EXECUTION not in public_source


def test_internal_route_is_temporary_nested_and_rejects_unknown_values() -> None:
    with execution_route.temporary_fem_execution_route(
        execution_route.LEGACY_FEM_EXECUTION
    ):
        assert (
            execution_route.current_fem_execution_route()
            == execution_route.LEGACY_FEM_EXECUTION
        )
        with execution_route.temporary_fem_execution_route(
            execution_route.ANALYSIS_RUNTIME_FEM
        ):
            assert (
                execution_route.current_fem_execution_route()
                == execution_route.ANALYSIS_RUNTIME_FEM
            )
        assert (
            execution_route.current_fem_execution_route()
            == execution_route.LEGACY_FEM_EXECUTION
        )
    assert (
        execution_route.current_fem_execution_route()
        == execution_route.ANALYSIS_RUNTIME_FEM
    )
    with pytest.raises(ValueError, match="FEM execution route"):
        with execution_route.temporary_fem_execution_route("unknown"):
            pass


def test_internal_route_does_not_leak_to_another_thread() -> None:
    observed: list[str] = []
    with execution_route.temporary_fem_execution_route(
        execution_route.LEGACY_FEM_EXECUTION
    ):
        worker = threading.Thread(
            target=lambda: observed.append(
                execution_route.current_fem_execution_route()
            )
        )
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert (
            execution_route.current_fem_execution_route()
            == execution_route.LEGACY_FEM_EXECUTION
        )
    assert observed == [execution_route.ANALYSIS_RUNTIME_FEM]


def test_default_route_keeps_the_extracted_snapshot_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    context, _active, _state = _context(manager)
    captured = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    workspace = SimpleNamespace(cleanup=lambda: None)
    monkeypatch.setattr(
        solver_runtime,
        "capture_solver_execution_request",
        lambda *_args, **_kwargs: captured,
    )
    monkeypatch.setattr(
        solver_runtime,
        "create_solver_execution_workspace",
        lambda: workspace,
    )
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "prepare_solver_execution_request",
        lambda *_args, **_kwargs: pytest.fail("legacy route was selected"),
    )

    result = solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
        _arguments(), ticket=_ticket()
    )

    assert result["job"]["job_id"] == "fem-job-1"
    assert manager.submission is not None


def test_legacy_route_is_captured_at_submission_and_preserves_public_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    context, _active, state = _context(manager)
    request = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    prepared = object()
    calls: list[str] = []
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "prepare_solver_execution_request",
        lambda *_args, **_kwargs: calls.append("prepare-request") or request,
    )
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "run_solver_execution",
        lambda *_args, **_kwargs: calls.append("run") or prepared,
    )
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "discard_solver_execution_request",
        lambda value: calls.append("discard") if value is request else None,
    )
    monkeypatch.setattr(
        solver_runtime,
        "capture_solver_execution_request",
        lambda *_args, **_kwargs: pytest.fail("analysis route was selected"),
    )

    with execution_route.temporary_fem_execution_route(
        execution_route.LEGACY_FEM_EXECUTION
    ):
        result = solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
            _arguments(), ticket=_ticket()
        )

    assert manager.submission is not None
    assert manager.submission["prepare"](lambda: False, lambda *_args: None) is prepared
    manager.submission["cleanup"](prepared)
    assert calls == ["prepare-request", "run", "discard"]
    assert state.cancelled == [_ticket()]
    assert result == {
        "job": {
            "job_id": "fem-job-1",
            "capability": "analyze.solver_execution.run",
            "phase": "queued",
            "progress_percent": 0,
            "progress_message": "Queued",
            "terminal": False,
        },
        "next": {
            "tool": "native.job",
            "operation": "status",
            "job_id": "fem-job-1",
            "poll_after_seconds": 30,
            "guidance": (
                "Continue polling until terminal. Do not cancel solely because "
                "progress is slow or unchanged."
            ),
        },
    }


@pytest.mark.parametrize(
    "failure",
    [NativeBackgroundCancelled(), RuntimeError("representative failure")],
    ids=["cancel", "failure"],
)
def test_legacy_route_preserves_cancel_failure_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    manager = _Manager()
    context, _active, state = _context(manager)
    request = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    discarded: list[object] = []
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "prepare_solver_execution_request",
        lambda *_args, **_kwargs: request,
    )

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "run_solver_execution",
        fail,
    )
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "discard_solver_execution_request",
        discarded.append,
    )
    with execution_route.temporary_fem_execution_route(
        execution_route.LEGACY_FEM_EXECUTION
    ):
        solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
            _arguments(), ticket=_ticket()
        )

    assert manager.submission is not None
    with pytest.raises(type(failure), match=str(failure) or None):
        manager.submission["prepare"](lambda: False, lambda *_args: None)
    manager.submission["cleanup"](None)
    assert discarded == [request]
    assert state.cancelled == [_ticket()]


@pytest.mark.parametrize(
    "route",
    [execution_route.ANALYSIS_RUNTIME_FEM, execution_route.LEGACY_FEM_EXECUTION],
)
def test_both_routes_refuse_publication_after_document_switch(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    manager = _Manager()
    context, active, _state = _context(manager)
    captured = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    workspace = SimpleNamespace(cleanup=lambda: None)
    request = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    monkeypatch.setattr(
        solver_runtime,
        "capture_solver_execution_request",
        lambda *_args, **_kwargs: captured,
    )
    monkeypatch.setattr(
        solver_runtime,
        "create_solver_execution_workspace",
        lambda: workspace,
    )
    monkeypatch.setattr(
        solver_runtime.legacy_solver_execution,
        "prepare_solver_execution_request",
        lambda *_args, **_kwargs: request,
    )
    with execution_route.temporary_fem_execution_route(route):
        solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
            _arguments(), ticket=_ticket()
        )

    active[0] = _Document("replacement-with-same-or-different-name")
    assert manager.submission is not None
    with pytest.raises(
        NativeRuntimeContextError,
        match="exact Native document is no longer active",
    ):
        manager.submission["validate_before_commit"]()


def test_analysis_route_rebinds_exact_reopened_source_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    context, active, _state = _context(manager)
    captured = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    rebound_captured = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    workspace = SimpleNamespace(cleanup=lambda: None)
    completed = object()
    rebound_completed = object()
    validated: list[tuple[object, object]] = []
    committed: list[tuple[object, object]] = []
    monkeypatch.setattr(
        solver_runtime,
        "capture_solver_execution_request",
        lambda *_args, **_kwargs: captured,
    )
    monkeypatch.setattr(
        solver_runtime,
        "create_solver_execution_workspace",
        lambda: workspace,
    )
    monkeypatch.setattr(
        solver_runtime,
        "rebind_captured_solver_execution",
        lambda document, uid, value: (
            rebound_captured
            if uid == "doc-fem-route" and value is captured
            else pytest.fail("unexpected captured-request rebind")
        ),
    )
    monkeypatch.setattr(
        solver_runtime,
        "validate_captured_solver_execution",
        lambda document, value: validated.append((document, value)),
    )
    monkeypatch.setattr(
        solver_runtime,
        "rebind_completed_solver_execution",
        lambda document, value: (
            rebound_completed
            if value is completed
            else pytest.fail("unexpected completed-result rebind")
        ),
    )
    monkeypatch.setattr(
        solver_runtime,
        "commit_solver_execution",
        lambda document, value: committed.append((document, value)) or object(),
    )
    monkeypatch.setattr(solver_runtime, "verify_solver_execution", lambda *_args: {})
    monkeypatch.setattr(
        solver_runtime,
        "run_immediate_mutation",
        lambda rebound_context, *, mutate, **_kwargs: mutate(
            rebound_context.document
        ),
    )

    solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
        _arguments(), ticket=_ticket()
    )
    reopened = _Document("doc-fem-route")
    active[0] = reopened

    assert manager.submission is not None
    manager.submission["validate_before_commit"]()
    manager.submission["commit"](completed)

    assert validated == [(reopened, rebound_captured)]
    assert committed == [(reopened, rebound_completed)]


def test_analysis_route_refuses_closed_or_same_name_replacement_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    context, active, _state = _context(manager)
    captured = SimpleNamespace(target=SimpleNamespace(kind="calculix"))
    monkeypatch.setattr(
        solver_runtime,
        "capture_solver_execution_request",
        lambda *_args, **_kwargs: captured,
    )
    monkeypatch.setattr(
        solver_runtime,
        "create_solver_execution_workspace",
        lambda: SimpleNamespace(cleanup=lambda: None),
    )
    monkeypatch.setattr(
        solver_runtime,
        "rebind_captured_solver_execution",
        lambda *_args: pytest.fail("a replacement source must not be rebound"),
    )
    solver_runtime.NativeAnalyzeSolverExecutionRuntime(context).execute(
        _arguments(), ticket=_ticket()
    )

    assert manager.submission is not None
    active[0] = None
    with pytest.raises(NativeRuntimeContextError, match="no longer active"):
        manager.submission["validate_before_commit"]()

    active[0] = _Document("same-name-different-uid")
    with pytest.raises(NativeRuntimeContextError, match="no longer active"):
        manager.submission["validate_before_commit"]()
