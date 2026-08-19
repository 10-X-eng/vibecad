# SPDX-License-Identifier: LGPL-2.1-or-later

"""Analyze pushes the human-readable report into the in-app Grok chat."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import Commands


def test_command_module_is_safe_in_headless_freecad_python() -> None:
    Commands.register_commands(SimpleNamespace())


def _result() -> dict:
    return {
        "ok": True,
        "source": "AeroBuildup",
        "CL": 1.516,
        "CD": 0.242,
        "CM": 0.733,
        "CLalpha": 7.3,
        "Cmalpha": 4.68,
        "PitchUnstable": True,
        "Re": 25000.0,
        "V_loaf": 4.19,
        "P_hover": 24.2,
        "P_cruise": 1.51,
        "airfoil": "e63",
        "airfoil_source": "bundled:e63",
        "corrections": [
            "PitchUnstable: Cmα > 0. Increase decalage, add tail volume, "
            "or move CG forward until Cmα < 0."
        ],
    }


def test_format_analyze_report_includes_coefficients_and_corrections() -> None:
    text = Commands.format_analyze_report(_result(), "Aero Analyze")
    assert "Aero Analyze (AeroBuildup)" in text
    assert "CL=1.516" in text
    assert "CD=0.242" in text
    assert "Cmα=4.68" in text
    assert "PITCH UNSTABLE" in text
    assert "Increase decalage" in text


def test_report_result_appends_vibecad_turn_and_queues_aero_steering(
    monkeypatch,
) -> None:
    appended: list[dict] = []
    steered: list[dict] = []

    monkeypatch.setattr(Commands, "_refresh_workspace", lambda: None)
    monkeypatch.setattr(Commands, "_dialog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Commands, "_is_assistant_run_active", lambda: True)
    monkeypatch.setattr(
        Commands,
        "_append_in_app_conversation",
        lambda role, text, **kwargs: appended.append(
            {"role": role, "text": text, **kwargs}
        ),
    )
    monkeypatch.setattr(
        Commands,
        "_queue_in_app_steering",
        lambda text, source: steered.append({"text": text, "source": source})
        or {"ok": True},
    )

    Commands._report_result(_result(), "Aero Analyze")

    assert len(appended) == 1
    assert appended[0]["role"] == "VibeCAD"
    assert appended[0]["persist"] is True
    assert appended[0]["metadata"] == {"source": "aero"}
    assert "CL=1.516" in appended[0]["text"]
    assert "PITCH UNSTABLE" in appended[0]["text"]
    assert len(steered) == 1
    assert steered[0]["source"] == "aero"
    assert "aero.solve" in steered[0]["text"]
    assert "not_airworthy" in steered[0]["text"]


def test_report_result_does_not_queue_steering_when_assistant_run_inactive(
    monkeypatch,
) -> None:
    appended: list[dict] = []
    steered: list[dict] = []
    started: list[str] = []

    monkeypatch.setattr(Commands, "_refresh_workspace", lambda: None)
    monkeypatch.setattr(Commands, "_dialog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Commands, "_is_assistant_run_active", lambda: False)
    monkeypatch.setattr(
        Commands, "_start_in_app_clanker", lambda prompt: started.append(prompt) or True
    )
    monkeypatch.setattr(
        Commands,
        "_append_in_app_conversation",
        lambda role, text, **kwargs: appended.append(
            {"role": role, "text": text, **kwargs}
        ),
    )
    monkeypatch.setattr(
        Commands,
        "_queue_in_app_steering",
        lambda text, source: steered.append({"text": text, "source": source})
        or {"ok": True},
    )

    Commands._report_result(_result(), "Aero Analyze")

    assert len(appended) == 1
    assert appended[0]["persist"] is True
    assert "CL=1.516" in appended[0]["text"]
    assert steered == []
    assert len(started) == 1
    assert "aero.solve" in started[0]


def test_failed_analyze_does_not_push_into_grok_chat(monkeypatch) -> None:
    appended: list[dict] = []
    monkeypatch.setattr(Commands, "_refresh_workspace", lambda: None)
    monkeypatch.setattr(Commands, "_dialog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        Commands,
        "_append_in_app_conversation",
        lambda *_args, **_kwargs: appended.append({}),
    )
    monkeypatch.setattr(
        Commands,
        "_queue_in_app_steering",
        lambda *_args, **_kwargs: appended.append({}),
    )

    Commands._report_result({"ok": False, "error": "missing neuralfoil"}, "Aero Analyze")

    assert appended == []


def test_append_and_steering_helpers_call_gui_and_service(monkeypatch) -> None:
    calls: list[tuple] = []
    gui = ModuleType("VibeCADGui")
    gui._append_conversation = (
        lambda role, text, persist=False, metadata=None: calls.append(
            ("append", role, text, persist, metadata)
        )
    )
    core = ModuleType("VibeCADCore")
    core.get_service = lambda: SimpleNamespace(
        queue_steering_message=lambda text, source="user": (
            calls.append(("steer", text, source)) or {"ok": True}
        )
    )
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)
    monkeypatch.setitem(sys.modules, "VibeCADCore", core)

    Commands._append_in_app_conversation(
        "VibeCAD",
        "CL=1.516",
        persist=True,
        metadata={"source": "aero"},
    )
    Commands._queue_in_app_steering("CL=1.516", "aero")

    assert ("append", "VibeCAD", "CL=1.516", True, {"source": "aero"}) in calls
    assert ("steer", "CL=1.516", "aero") in calls


def test_is_assistant_run_active_probes_vibecadgui(monkeypatch) -> None:
    gui = ModuleType("VibeCADGui")
    gui._is_assistant_run_active = lambda: True
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)
    assert Commands._is_assistant_run_active() is True

    gui._is_assistant_run_active = lambda: False
    assert Commands._is_assistant_run_active() is False

    monkeypatch.setitem(sys.modules, "VibeCADGui", ModuleType("VibeCADGui"))
    assert Commands._is_assistant_run_active() is False
