# SPDX-License-Identifier: LGPL-2.1-or-later

"""Toolbar / menu commands for the Aero workbench."""

from __future__ import annotations

from typing import Any

import AeroIcons
import VibeCADAero

_ICON = AeroIcons.aero_icon_path()


def _console(message: str, kind: str = "message") -> None:
    try:
        import FreeCAD

        printer = {
            "error": FreeCAD.Console.PrintError,
            "warning": FreeCAD.Console.PrintWarning,
        }.get(kind, FreeCAD.Console.PrintMessage)
        printer(message + "\n")
    except Exception:
        print(message)


def _dialog(title: str, message: str, kind: str = "warning") -> None:
    _console(message, "error" if kind == "warning" else "message")
    try:
        from PySide import QtGui

        if kind == "warning":
            QtGui.QMessageBox.warning(None, title, message)
        else:
            QtGui.QMessageBox.information(None, title, message)
    except Exception:
        pass


def _active_doc() -> Any:
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        doc = FreeCAD.newDocument("Aero")
    return doc


def _refresh_workspace() -> None:
    try:
        import AeroWorkspace

        AeroWorkspace.refresh_workspace()
    except Exception:
        pass


def format_analyze_report(result: dict[str, Any], title: str = "Aero Analyze") -> str:
    """Human-readable Analyze text for the dialog and signed-in Grok chat."""

    try:
        import AeroResults

        return AeroResults.format_human_report(result, title)
    except Exception:
        unstable = (
            "PITCH UNSTABLE (Cmα > 0)"
            if result.get("PitchUnstable")
            else "pitch stable"
        )
        return (
            f"{title} ({result.get('source')})\n"
            f"CL={result.get('CL')}  CD={result.get('CD')}  CM={result.get('CM')}\n"
            f"CLα={result.get('CLalpha')}  Cmα={result.get('Cmalpha')}  {unstable}"
        )


def _append_in_app_conversation(
    role: str,
    text: str,
    *,
    persist: bool = False,
    metadata: dict[str, Any] | None = None,
) -> bool:
    try:
        import VibeCADGui

        VibeCADGui._append_conversation(
            role, text, persist=persist, metadata=metadata
        )
        return True
    except Exception:
        return False


def _queue_in_app_steering(text: str, source: str = "aero") -> dict[str, Any]:
    try:
        from VibeCADCore import get_service

        return get_service().queue_steering_message(text, source=source)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _push_analyze_to_in_app_grok(result: dict[str, Any], title: str) -> str:
    """Persist Analyze as a VibeCAD/assistant turn and steer an in-flight Grok run."""

    text = format_analyze_report(result, title)
    _append_in_app_conversation(
        "VibeCAD",
        text,
        persist=True,
        metadata={"source": "aero"},
    )
    _queue_in_app_steering(text, "aero")
    return text


def _report_result(result: dict[str, Any], title: str) -> None:
    _refresh_workspace()
    if not result.get("ok"):
        _dialog(title, result.get("error") or "Aero solve failed.")
        return
    text = _push_analyze_to_in_app_grok(result, title)
    _dialog(title, text, kind="info")


class _AeroCommand:
    def IsActive(self) -> bool:
        return True


class VibeCADAero_Analyze(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Analyze",
            "ToolTip": "Solve section+3D+hover and write AeroReport. Does not move CAD.",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_analyze(_active_doc(), repair=False), "Aero Analyze")


class VibeCADAero_Section(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Section / NeuralFoil",
            "ToolTip": "2D viscous section at low Re (NeuralFoil large)",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_section(_active_doc()), "Aero Section")


class VibeCADAero_VLM(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "3D / AeroSandbox",
            "ToolTip": "VortexLatticeMethod + AeroBuildup (NeuralFoil-backed)",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_vlm(_active_doc()), "Aero 3D")


class VibeCADAero_ExportJSBSim(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Export JSBSim plant",
            "ToolTip": "Write a 6DOF JSBSim XML plant from the last AeroReport",
        }

    def IsActive(self) -> bool:
        return _has_report()

    def Activated(self) -> None:
        result = VibeCADAero.export_jsbsim(_active_doc())
        _refresh_workspace()
        if not result.get("ok"):
            _dialog("JSBSim", result.get("error") or "Export failed.")
            return
        message = f"Wrote {result.get('fdm_path')}"
        if result.get("boot_error"):
            message += f"\n\nJSBSim boot: {result['boot_error']}"
        _dialog("JSBSim", message, kind="info")


class VibeCADAero_Report(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Write report",
            "ToolTip": "Write markdown and spreadsheet from the last solve. Does not re-solve.",
        }

    def IsActive(self) -> bool:
        return _has_report()

    def Activated(self) -> None:
        _report_result(VibeCADAero.write_last_report(_active_doc()), "Aero Report")


class VibeCADAero_ProposeRepairs(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Propose repairs",
            "ToolTip": "Preview bounded pitch-stability CAD changes. Does not apply them.",
        }

    def IsActive(self) -> bool:
        return _has_report()

    def Activated(self) -> None:
        _report_result(VibeCADAero.propose_repairs(_active_doc()), "Aero Propose repairs")


class VibeCADAero_ApplyRepairs(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Apply repairs",
            "ToolTip": "Apply the current repair preview if the document has not changed.",
        }

    def IsActive(self) -> bool:
        return _has_report()

    def Activated(self) -> None:
        _report_result(VibeCADAero.apply_repairs(_active_doc()), "Aero Apply repairs")


class VibeCADAero_FlightCard(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Flight card",
            "ToolTip": "Mass, loading, hover margin, tail volume, endurance estimate. Not airworthy.",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.flight_card(_active_doc()), "Aero Flight card")


def _has_report() -> bool:
    try:
        import FreeCAD

        doc = FreeCAD.ActiveDocument
        return bool(doc is not None and doc.getObject("AeroReport") is not None)
    except Exception:
        return False


def register_commands(gui: Any | None = None) -> None:
    if gui is None:
        try:
            import FreeCADGui as gui  # type: ignore[no-redef]
        except Exception:
            return
    add_command = getattr(gui, "addCommand", None)
    if not callable(add_command):
        return
    add_command("VibeCADAero_Analyze", VibeCADAero_Analyze())
    add_command("VibeCADAero_Section", VibeCADAero_Section())
    add_command("VibeCADAero_VLM", VibeCADAero_VLM())
    add_command("VibeCADAero_ExportJSBSim", VibeCADAero_ExportJSBSim())
    add_command("VibeCADAero_Report", VibeCADAero_Report())
    add_command("VibeCADAero_ProposeRepairs", VibeCADAero_ProposeRepairs())
    add_command("VibeCADAero_ApplyRepairs", VibeCADAero_ApplyRepairs())
    add_command("VibeCADAero_FlightCard", VibeCADAero_FlightCard())


register_commands()
