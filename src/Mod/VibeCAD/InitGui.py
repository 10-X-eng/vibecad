# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the shared VibeCAD assistant."""

from __future__ import annotations

import FreeCAD as App


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


def _check_bundled_fasteners() -> bool:
    try:
        from VibeCADFasteners import require_available

        require_available()
        return True
    except Exception as exc:
        _warn(
            "VibeCAD bundled Fasteners catalog failed to load; standard-component "
            f"commands are disabled: {exc}"
        )
        return False


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


def _replace_list_token(group, key: str, token: str, replacement: str) -> bool:
    current = group.GetString(key, "")
    values = [item.strip() for item in current.split(",") if item.strip()]
    if token not in values:
        return False
    migrated = []
    for item in values:
        candidate = replacement if item == token else item
        if candidate not in migrated:
            migrated.append(candidate)
    group.SetString(key, ",".join(migrated))
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


def _migrate_consolidated_part_workbench(
    replace_list_token=_replace_list_token,
) -> bool:
    """Point saved standalone Part selections at the consolidated workbench."""

    migration = App.ParamGet("User parameter:BaseApp/Preferences/Migration")
    migration_key = "VibeCADConsolidatedPartWorkbench2026"
    if migration.GetBool(migration_key, False):
        return False

    retired = "PartWorkbench"
    consolidated = "PartDesignWorkbench"
    changed = False
    workbenches = App.ParamGet("User parameter:BaseApp/Preferences/Workbenches")
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    for key in ("Ordered", "Disabled"):
        changed = replace_list_token(workbenches, key, retired, consolidated) or changed
    changed = (
        replace_list_token(general, "BackgroundAutoloadModules", retired, consolidated)
        or changed
    )
    for key in ("AutoloadModule", "LastModule"):
        if general.GetString(key, "") == retired:
            general.SetString(key, consolidated)
            changed = True
    migration.SetBool(migration_key, True)
    return changed


try:
    _restore_vibecad_disabled_workbenches()
    if _migrate_removed_architecture_workbench():
        _warn("Removed saved references to the retired architecture workbench")
    if _migrate_consolidated_part_workbench():
        _warn("Migrated saved Part workbench references to Part Design")
except Exception as exc:
    _warn(f"VibeCAD workbench preference migration failed: {exc}")


try:
    fasteners_available = _check_bundled_fasteners()
    from PySide import QtCore

    import VibeCADGui

    VibeCADGui.ensure_commands_registered()
    if fasteners_available:
        try:
            import VibeCADFastenersGui

            VibeCADFastenersGui.ensure_commands_registered()
        except Exception as exc:
            _warn(f"VibeCAD standard-component commands failed to register: {exc}")

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
