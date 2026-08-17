# SPDX-License-Identifier: LGPL-2.1-or-later

"""Toolbar / menu commands for the Aero workbench."""

from __future__ import annotations

from typing import Any

import VibeCADAero


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


def _report_result(result: dict[str, Any], title: str) -> None:
    if not result.get("ok"):
        _dialog(title, result.get("error") or "Aero solve failed.")
        return
    unstable = "PITCH UNSTABLE (Cmα > 0)" if result.get("PitchUnstable") else "pitch stable"
    text = (
        f"{title} ({result.get('source')})\n"
        f"CL={result.get('CL')}  CD={result.get('CD')}  CM={result.get('CM')}\n"
        f"CLα={result.get('CLalpha')}  Cmα={result.get('Cmalpha')}  {unstable}\n"
        f"Re={result.get('Re')}  V_loaf={result.get('V_loaf')} m/s\n"
        f"P_hover={result.get('P_hover')} W (momentum-theory)\n"
        f"P_cruise={result.get('P_cruise')} W (η=0.65)\n"
        f"Airfoil={result.get('airfoil')} from {result.get('airfoil_source')}"
    )
    if result.get("jsbsim_path"):
        text += f"\nJSBSim: {result['jsbsim_path']}"
        if result.get("jsbsim_boot_error"):
            text += f"\nJSBSim boot: {result['jsbsim_boot_error']}"
    _dialog(title, text, kind="info")


class _AeroCommand:
    def IsActive(self) -> bool:
        return True


class VibeCADAero_Analyze(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": "utilities-system-monitor",
            "MenuText": "Analyze",
            "ToolTip": "Run NeuralFoil + AeroSandbox + momentum hover and write AeroReport",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_analyze(_active_doc()), "Aero Analyze")


class VibeCADAero_Section(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": "Sketcher_CreateArc",
            "MenuText": "Section / NeuralFoil",
            "ToolTip": "2D viscous section at low Re (NeuralFoil large)",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_section(_active_doc()), "Aero Section")


class VibeCADAero_VLM(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": "Part_Box",
            "MenuText": "3D / AeroSandbox",
            "ToolTip": "VortexLatticeMethod + AeroBuildup (NeuralFoil-backed)",
        }

    def Activated(self) -> None:
        _report_result(VibeCADAero.run_vlm(_active_doc()), "Aero 3D")


class VibeCADAero_ExportJSBSim(_AeroCommand):
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": "Document-export",
            "MenuText": "Export JSBSim plant",
            "ToolTip": "Write a 6DOF JSBSim XML plant and store the path on the document",
        }

    def Activated(self) -> None:
        result = VibeCADAero.export_jsbsim(_active_doc())
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
            "Pixmap": "Spreadsheet",
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


def register_commands() -> None:
    try:
        import FreeCADGui
    except Exception:
        return
    FreeCADGui.addCommand("VibeCADAero_Analyze", VibeCADAero_Analyze())
    FreeCADGui.addCommand("VibeCADAero_Section", VibeCADAero_Section())
    FreeCADGui.addCommand("VibeCADAero_VLM", VibeCADAero_VLM())
    FreeCADGui.addCommand("VibeCADAero_ExportJSBSim", VibeCADAero_ExportJSBSim())
    FreeCADGui.addCommand("VibeCADAero_Report", VibeCADAero_Report())


register_commands()
