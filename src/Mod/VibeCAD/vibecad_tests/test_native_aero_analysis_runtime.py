# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass

import pytest

import VibeCADAeroAnalysisRuntime as aero_analysis
import VibeCADNativeAeroRuntime as native_aero
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


@dataclass
class _Snapshot:
    job_id: str = "aero-job-1"
    capability_name: str = "aero.solve"
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


class _State:
    def __init__(self, revision: int = 0) -> None:
        self.revision = revision

    def current_revision(self, _document_uid: str) -> int:
        return self.revision


class _Document:
    pass


def _runtime_context(manager: _Manager) -> NativeRuntimeContext:
    context = object.__new__(NativeRuntimeContext)
    document = _Document()
    object.__setattr__(context, "document", document)
    object.__setattr__(context, "document_uid", "doc-aero")
    object.__setattr__(context, "background_manager", manager)
    object.__setattr__(context, "document_thread_dispatch", lambda callback: callback())
    object.__setattr__(context, "state", _State())
    return context


def test_native_aero_background_solve_uses_shared_job_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    context = _runtime_context(manager)
    runtime = native_aero.NativeAeroRuntime(context)
    prepared = object()

    monkeypatch.setattr(
        native_aero,
        "prepare_document_input",
        lambda document, operation: (prepared, "geometry-r1"),
    )
    monkeypatch.setattr(native_aero, "run_detached", lambda *args, **kwargs: "completed")
    monkeypatch.setattr(native_aero, "validate_document_input", lambda *args, **kwargs: None)

    ticket = NativeCallTicket(
        document_uid="doc-aero",
        capability_name="aero.solve",
        expected_revision=0,
        idempotency_token="token-aero",
    )
    result = runtime._solve_background("vlm", ticket)

    assert result["job"]["job_id"] == "aero-job-1"
    assert result["job"]["capability"] == "aero.solve"
    assert result["next"] == {
        "tool": "native.job",
        "operation": "status",
        "job_id": "aero-job-1",
    }
    assert manager.submission is not None
    assert manager.submission["document_uid"] == "doc-aero"
    assert manager.submission["capability_name"] == "aero.solve"
    assert manager.submission["dispatch_to_document_thread"] is context.document_thread_dispatch


def test_aero_geometry_revision_is_rechecked_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _Document()
    monkeypatch.setattr(aero_analysis.AeroConfig, "resolve_geometry", lambda _doc: {"airfoil": "NACA0012"})
    monkeypatch.setattr(aero_analysis.AeroPreview, "geometry_revision", lambda _doc, _cfg: "geometry-r2")

    with pytest.raises(aero_analysis.AeroAnalysisRuntimeError) as caught:
        aero_analysis.validate_document_input(document, "geometry-r1")

    assert caught.value.error_code == "AERO_ANALYSIS_STALE"
    assert caught.value.current_revision == "geometry-r2"


def test_aero_publication_rejects_wrong_detached_result_type() -> None:
    with pytest.raises(TypeError, match="CompletedAeroAnalysis"):
        aero_analysis.publish_document_result(_Document(), object())
