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


def _remove_list_token(group, key: str, token: str) -> bool:
    current = group.GetString(key, "")
    values = [item.strip() for item in current.split(",") if item.strip()]
    filtered = [item for item in values if item != token]
    if filtered == values:
        return False
    group.SetString(key, ",".join(filtered))
    return True


def _migrate_removed_architecture_workbench(
    remove_list_token=_remove_list_token,
) -> bool:
    """Remove persisted references to the workbench before startup selection."""

    migration = App.ParamGet("User parameter:BaseApp/Preferences/Migration")
    migration_key = "VibeCADRemovedArchitectureWorkbench2026"
    if migration.GetBool(migration_key, False):
        return False

    removed = "BIMWorkbench"
    fallback = "PartDesignWorkbench"
    changed = False
    workbenches = App.ParamGet("User parameter:BaseApp/Preferences/Workbenches")
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    for key in ("Ordered", "Disabled"):
        changed = remove_list_token(workbenches, key, removed) or changed
    changed = remove_list_token(general, "BackgroundAutoloadModules", removed) or changed
    for key in ("AutoloadModule", "LastModule"):
        if general.GetString(key, "") == removed:
            general.SetString(key, fallback)
            changed = True
    migration.SetBool(migration_key, True)
    return changed


try:
    _restore_vibecad_disabled_workbenches()
    if _migrate_removed_architecture_workbench():
        _warn("Removed saved references to the retired architecture workbench")
except Exception as exc:
    _warn(f"VibeCAD workbench preference migration failed: {exc}")


try:
    from PySide import QtCore

    import VibeCADGui

    VibeCADGui.ensure_commands_registered()

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

    QtCore.QTimer.singleShot(0, _setup_always_on_grid)
except Exception as exc:
    _warn(f"VibeCAD GUI bootstrap failed: {exc}")
