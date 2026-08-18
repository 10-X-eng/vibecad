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
            "ToolTip": "Run NeuralFoil + AeroSandbox + momentum hover, repair pitch-unstable geometry, and write AeroReport",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_analyze(_active_doc()), "Aero Analyze")


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
            "ToolTip": "Write a 6DOF JSBSim XML plant and store the path on the document",
        }

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
            "ToolTip": "Write markdown and spreadsheet report objects from the last solve",
        }

    def Activated(self) -> None:
        result = VibeCADAero.run_analyze(
            _active_doc(),
            spreadsheet=True,
            markdown=True,
        )
        _report_result(result, "Aero Report")


def register_commands(gui: Any | None = None) -> None:
    if gui is None:
        try:
            import FreeCADGui as gui  # type: ignore[no-redef]
        except Exception:
            return
    gui.addCommand("VibeCADAero_Analyze", VibeCADAero_Analyze())
    gui.addCommand("VibeCADAero_Section", VibeCADAero_Section())
    gui.addCommand("VibeCADAero_VLM", VibeCADAero_VLM())
    gui.addCommand("VibeCADAero_ExportJSBSim", VibeCADAero_ExportJSBSim())
    gui.addCommand("VibeCADAero_Report", VibeCADAero_Report())


register_commands()
