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

    def recompute(self, *args: Any, **kwargs: Any) -> None:
        self.recompute_calls += 1
        for obj in self.Objects:
            if getattr(obj, "State", None) is not None:
                obj.State = ["Up-to-date"]


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


class _TickClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


class _Shape:
    def __init__(self, volume: float, hash_code: int = 1) -> None:
        self.Volume = volume
        self._hash_code = hash_code

    def hashCode(self) -> int:
        return self._hash_code


class _MovingShape:
    def __init__(self, volumes: list[float]) -> None:
        self._volumes = list(volumes)

    @property
    def Volume(self) -> float:
        if len(self._volumes) > 1:
            return self._volumes.pop(0)
        return self._volumes[0]

    def hashCode(self) -> int:
        return int(self.Volume)


class _Feature:
    def __init__(
        self,
        name: str,
        *,
        type_id: str,
        volume: float | None = None,
        shape: Any = None,
        rejected: int = 0,
        occurrences: int | None = None,
        generated: int | None = None,
        tip: Any = None,
    ) -> None:
        self.Name = name
        self.TypeId = type_id
        self.State = ["Up-to-date"]
        self.Shape = _Shape(volume) if shape is None and volume is not None else shape
        self.RejectedSolidCount = rejected
        if occurrences is not None:
            self.Occurrences = occurrences
        if generated is not None:
            self.GeneratedOccurrenceCount = generated
        if tip is not None:
            self.Tip = tip
        self.Originals: list[Any] = []
        self.touch_calls = 0

    def touch(self) -> None:
        self.touch_calls += 1
        self.State = ["Touched"]


def test_shape_signature_uses_volume_and_hash() -> None:
    shape = _Shape(3925.23456789, hash_code=44)
    assert control._shape_signature(shape)[0] == 3925.234568
    assert control._shape_signature(shape)[1] == 44


def test_solid_readiness_ignores_plain_pads() -> None:
    pad = _Feature("Pad001", type_id="PartDesign::Pad", volume=10.0)
    readiness = control._solid_readiness(SimpleNamespace(Objects=[pad]))
    assert readiness["items"] == ()
    assert readiness["needs_dirty"] is False


def test_solid_readiness_flags_tip_volume_mismatch() -> None:
    tip = _Feature("PolarPattern", type_id="PartDesign::PolarPattern", volume=3925.23)
    body = _Feature("Body", type_id="PartDesign::Body", volume=3793.0, tip=tip)
    readiness = control._solid_readiness(SimpleNamespace(Objects=[body, tip]))
    assert readiness["tip_mismatch"] is True
    assert readiness["needs_dirty"] is True


def test_pattern_rebuild_incomplete_uses_diagnostics() -> None:
    rejected = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3793.0,
        rejected=2,
    )
    short = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3793.0,
        occurrences=6,
        generated=4,
    )
    ok = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3925.23,
        occurrences=6,
        generated=6,
    )
    assert control._pattern_rebuild_incomplete(rejected) is True
    assert control._pattern_rebuild_incomplete(short) is True
    assert control._pattern_rebuild_incomplete(ok) is False


def test_drain_recompute_waits_for_moving_tip_volume() -> None:
    polar = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        shape=_MovingShape([3793.0, 3881.0, 3925.23, 3925.23, 3925.23, 3925.23, 3925.23]),
        occurrences=6,
        generated=6,
    )
    document = _GatedDocument(objects=[polar])
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert polar.Shape.Volume == 3925.23
    assert result["recompute_calls"] >= 2


def test_drain_recompute_forces_when_body_tip_volumes_differ() -> None:
    tip = _Feature("PolarPattern", type_id="PartDesign::PolarPattern", volume=3925.23)
    body = _Feature("Body", type_id="PartDesign::Body", volume=3793.0, tip=tip)
    document = _GatedDocument(objects=[body, tip])

    def recompute(*_args: Any, **_kwargs: Any) -> None:
        document.recompute_calls += 1
        body.State = ["Up-to-date"]
        tip.State = ["Up-to-date"]
        if document.recompute_calls >= 2:
            body.Shape.Volume = 3925.23

    document.recompute = recompute  # type: ignore[method-assign]
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert body.Shape.Volume == 3925.23
    assert result["recompute_calls"] >= 2


def test_drain_recompute_forces_when_rejected_solids_remain() -> None:
    polar = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3793.0,
        rejected=1,
        occurrences=6,
        generated=6,
    )
    document = _GatedDocument(objects=[polar])

    def recompute(*_args: Any, **_kwargs: Any) -> None:
        document.recompute_calls += 1
        polar.State = ["Up-to-date"]
        if document.recompute_calls >= 2:
            polar.RejectedSolidCount = 0
            polar.Shape.Volume = 3925.23

    document.recompute = recompute  # type: ignore[method-assign]
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert polar.RejectedSolidCount == 0
    assert result["recompute_calls"] >= 2


def test_drain_recompute_dirties_pattern_tip_once_when_already_stable() -> None:
    polar = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3925.23,
        occurrences=6,
        generated=6,
    )
    document = _GatedDocument(objects=[polar])
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert document.recompute_calls == 2
    assert polar.touch_calls >= 1
    assert result["recompute_calls"] == 2


def test_drain_recompute_dirties_until_wrong_pattern_volume_stabilizes() -> None:
    polar = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3837.27,
        occurrences=6,
        generated=6,
    )
    document = _GatedDocument(objects=[polar])

    def recompute(*_args: Any, **_kwargs: Any) -> None:
        document.recompute_calls += 1
        polar.State = ["Up-to-date"]
        if polar.touch_calls:
            polar.Shape.Volume = 3925.23

    document.recompute = recompute  # type: ignore[method-assign]
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert polar.Shape.Volume == 3925.23
    assert polar.touch_calls >= 1
    assert result["recompute_calls"] >= 3


def test_drain_recompute_touches_tip_and_pad_originals() -> None:
    pad = _Feature("Pad001", type_id="PartDesign::Pad", volume=8.0)
    tip = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3925.23,
        occurrences=6,
        generated=6,
    )
    tip.Originals = [pad]
    body = _Feature("Body", type_id="PartDesign::Body", volume=3925.23, tip=tip)
    document = _GatedDocument(objects=[body, tip, pad])
    result = control._drain_recompute(
        document,
        timeout_s=5.0,
        pump=lambda: None,
        clock=_TickClock(),
    )
    assert result["ok"] is True
    assert tip.touch_calls >= 1
    assert pad.touch_calls >= 1
    assert body.touch_calls >= 1


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


def test_run_script_patterned_tip_dirties_idle_rebuild(monkeypatch) -> None:
    polar = _Feature(
        "PolarPattern",
        type_id="PartDesign::PolarPattern",
        volume=3925.23,
        occurrences=6,
        generated=6,
    )
    document = _GatedDocument(objects=[polar])
    monkeypatch.setattr(control, "_app", lambda: SimpleNamespace(ActiveDocument=document))
    monkeypatch.setattr(control, "_gui", lambda: SimpleNamespace(updateGui=lambda: None))
    monkeypatch.setattr(control, "_document_summary", lambda _document: {"name": "stub"})

    payload = control.run_script(python="result = 7")

    assert payload["ok"] is True
    assert document.recompute_calls == 2
    assert polar.touch_calls >= 1
    assert payload["recompute_calls"] == 2
