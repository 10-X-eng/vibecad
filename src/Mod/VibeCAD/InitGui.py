# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the shared VibeCAD assistant."""

from __future__ import annotations

import FreeCAD as App


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


def _restore_vibecad_disabled_workbenches() -> bool:
    """Undo only the exact disabled lists previously written by VibeCAD."""

    preferences = App.ParamGet(
        "User parameter:BaseApp/Preferences/Workbenches"
    )
    disabled = frozenset(
        item.strip()
        for item in preferences.GetString("Disabled", "").split(",")
        if item.strip()
    )
    disabled_sets_to_repair = (
        frozenset(
            {
                "InspectionWorkbench",
                "MaterialWorkbench",
                "OpenSCADWorkbench",
                "PointsWorkbench",
                "ReverseEngineeringWorkbench",
                "RobotWorkbench",
                "TestWorkbench",
                "NoneWorkbench",
            }
        ),
        frozenset(
            {
                "InspectionWorkbench",
                "MaterialWorkbench",
                "PointsWorkbench",
                "ReverseEngineeringWorkbench",
                "RobotWorkbench",
                "TestWorkbench",
                "NoneWorkbench",
            }
        ),
    )
    if disabled not in disabled_sets_to_repair:
        return False
    preferences.SetString("Disabled", "TestWorkbench,NoneWorkbench")
    return True


try:
    _restore_vibecad_disabled_workbenches()
except Exception as exc:
    _warn(f"VibeCAD workbench preference migration failed: {exc}")


try:
    from PySide import QtCore

    import VibeCADGui

    VibeCADGui.ensure_commands_registered()

    def _open_startup_assistant() -> None:
        try:
            import VibeCADGui as _VibeCADGui

            _VibeCADGui.ensure_commands_registered()
            _VibeCADGui.show_assistant_for_active_workbench()
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(
                    f"VibeCAD assistant startup open failed: {exc}\n"
                )
            except Exception:
                pass

    def _setup_always_on_grid() -> None:
        try:
            import VibeCADGrid

            VibeCADGrid.setup()
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(f"VibeCAD grid startup setup failed: {exc}\n")
            except Exception:
                pass

    QtCore.QTimer.singleShot(0, _open_startup_assistant)
    QtCore.QTimer.singleShot(0, _setup_always_on_grid)
except Exception as exc:
    _warn(f"VibeCAD GUI bootstrap failed: {exc}")
