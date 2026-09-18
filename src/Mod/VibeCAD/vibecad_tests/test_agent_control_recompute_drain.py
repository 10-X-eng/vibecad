# SPDX-License-Identifier: LGPL-2.1-or-later

"""Unit tests for /v1/run recompute drain after host recompute:true.

These exercise the drain helper control flow with stub documents. They do
not claim live fem_bracket GUI proof.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import VibeCADAgentControl as control


class _Clock:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if not self._values:
            return 0.0
        if len(self._values) == 1:
            return self._values[0]
        return self._values.pop(0)


class _GatedDocument:
    def __init__(
        self,
        *,
        cooperative: bool = False,
        pending: bool = False,
        presentation: bool = False,
        objects: list[Any] | None = None,
    ) -> None:
        self.CooperativeMutationActive = cooperative
        self.RecomputePending = pending
        self.PresentationUpdateActive = presentation
        self.Objects = list(objects or [])
        self.recompute_calls = 0

    def recompute(self) -> None:
        self.recompute_calls += 1


def test_recompute_gates_use_exposed_document_proxies() -> None:
    document = _GatedDocument()
    assert control._recompute_gates_block(document) is False
    document.CooperativeMutationActive = True
    assert control._recompute_gates_block(document) is True
    document.CooperativeMutationActive = False
    document.RecomputePending = True
    assert control._recompute_gates_block(document) is True
    document.RecomputePending = False
    document.PresentationUpdateActive = True
    assert control._recompute_gates_block(document) is True


def test_touched_objects_read_state_list() -> None:
    clean = SimpleNamespace(State=("Up-to-date",))
    dirty = SimpleNamespace(State=["Touched"])
    assert control._document_has_touched_objects(SimpleNamespace(Objects=[clean])) is False
    assert control._document_has_touched_objects(SimpleNamespace(Objects=[dirty])) is True
    assert control._document_has_touched_objects(SimpleNamespace(Objects=[])) is False


def test_drain_recompute_succeeds_when_already_up_to_date() -> None:
    document = _GatedDocument()
    pumps = {"count": 0}

    result = control._drain_recompute(
        document,
        timeout_s=0.0,
        pump=lambda: pumps.__setitem__("count", pumps["count"] + 1),
        clock=_Clock([0.0, 0.0]),
    )

    assert result["ok"] is True
    assert result["error"] is None
    assert result["recompute_calls"] == 1
    assert document.recompute_calls == 1
    assert pumps["count"] == 1
    assert result["ms"] >= 0.0


def test_drain_recompute_pumps_while_cooperative_and_pending_block() -> None:
    document = _GatedDocument(cooperative=True, pending=True)
    pumps = {"count": 0}

    def pump() -> None:
        pumps["count"] += 1
        if pumps["count"] >= 3:
            document.CooperativeMutationActive = False
            document.RecomputePending = False

    result = control._drain_recompute(document, timeout_s=5.0, pump=pump, clock=_Clock([0.0, 0.1, 0.2, 0.3]))

    assert result["ok"] is True
    assert document.recompute_calls == 1
    assert result["recompute_calls"] == 1
    assert pumps["count"] == 3


def test_drain_recompute_reissues_when_touched_after_gates_clear() -> None:
    touched = SimpleNamespace(State=["Touched"])
    document = _GatedDocument(objects=[touched])

    def recompute() -> None:
        document.recompute_calls += 1
        if document.recompute_calls >= 2:
            touched.State = ["Up-to-date"]

    document.recompute = recompute  # type: ignore[method-assign]
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_Clock([0.0, 0.1, 0.2, 0.3]),
    )

    assert result["ok"] is True
    assert result["recompute_calls"] == 2
    assert document.recompute_calls == 2
    assert touched.State == ["Up-to-date"]


def test_drain_recompute_times_out_with_structured_error() -> None:
    document = _GatedDocument(cooperative=True)
    result = control._drain_recompute(
        document,
        timeout_s=0.0,
        pump=lambda: None,
        clock=_Clock([10.0]),
    )

    assert result["ok"] is False
    assert result["error"] == "RECOMPUTE_DRAIN_TIMEOUT"
    assert result["recompute_calls"] == 1
    assert document.recompute_calls == 1


def test_run_script_default_recompute_is_true() -> None:
    assert inspect.signature(control.run_script).parameters["recompute"].default is True


def test_dispatch_run_defaults_recompute_true(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(control, "run_script", fake_run)
    monkeypatch.setattr(control, "_on_document_thread", lambda operation: operation())
    payload = control.dispatch("run", {"python": "pass"})
    assert payload["ok"] is True
    assert seen["recompute"] is True


def test_run_script_measure_only_recompute_true_drains(monkeypatch) -> None:
    document = _GatedDocument()
    pumps = {"count": 0}

    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(ActiveDocument=document))
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(updateGui=lambda: pumps.__setitem__("count", pumps["count"] + 1)),
    )
    monkeypatch.setattr(
        control,
        "_document_summary",
        lambda _document: {"name": "stub"},
    )

    payload = control.run_script(python="result = 7")

    assert payload["ok"] is True
    assert payload["result"] == 7
    assert document.recompute_calls == 1
    assert payload["recompute_calls"] == 1
    assert "recompute_drain_ms" in payload
    assert pumps["count"] >= 1


def test_run_script_recompute_false_does_not_drain(monkeypatch) -> None:
    document = _GatedDocument()
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(ActiveDocument=document))
    monkeypatch.setattr(control, "_gui", lambda: SimpleNamespace(updateGui=lambda: None))
    monkeypatch.setattr(control, "_document_summary", lambda _document: {"name": "stub"})

    payload = control.run_script(python="result = 7", recompute=False)

    assert payload["ok"] is True
    assert document.recompute_calls == 0
    assert "recompute_calls" not in payload
    assert "recompute_drain_ms" not in payload


def test_run_script_drain_timeout_is_hard_failure(monkeypatch) -> None:
    document = _GatedDocument(cooperative=True)
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(ActiveDocument=document))
    monkeypatch.setattr(control, "_gui", lambda: SimpleNamespace(updateGui=lambda: None))
    monkeypatch.setattr(control, "RECOMPUTE_DRAIN_TIMEOUT_S", 0.0)

    payload = control.run_script(python="result = 7")

    assert payload["ok"] is False
    assert payload["failure_code"] == "RECOMPUTE_DRAIN_TIMEOUT"
    assert payload["failure_stage"] == "recompute"
    assert document.recompute_calls == 1
    assert payload["recompute_calls"] == 1
