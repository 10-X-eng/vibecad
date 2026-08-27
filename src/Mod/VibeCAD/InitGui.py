# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the shared VibeCAD assistant."""

from __future__ import annotations

import FreeCAD as App


def _warn(message: str) -> None:
    App.Console.PrintWarning(f"{message}\n")


def _check_bundled_fasteners(warn=_warn) -> bool:
    try:
        from VibeCADFasteners import require_available

        require_available()
        return True
    except Exception as exc:
        warn(
            "VibeCAD bundled Fasteners catalog failed to load; standard-component "
            f"commands are disabled: {exc}"
        )
        return False


def _load_ribbon_extension_commands(warn=_warn) -> None:
    """Register native commands used outside their legacy workbench page."""

    for module_name in ("InspectionGui", "MeshPartGui", "PartGui"):
        try:
            __import__(module_name)
        except Exception as exc:
            warn(f"VibeCAD ribbon extension {module_name} failed to load: {exc}")


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


def _migrate_removed_openscad_workbench(
    replace_list_token=_replace_list_token,
    remove_list_token=_remove_list_token,
) -> bool:
    """Point saved OpenSCAD workbench selections at the native Mesh tools."""

    migration = App.ParamGet("User parameter:BaseApp/Preferences/Migration")
    migration_key = "VibeCADRemovedOpenSCADWorkbench2026"
    if migration.GetBool(migration_key, False):
        return False

    retired = "OpenSCADWorkbench"
    fallback = "MeshWorkbench"
    changed = False
    workbenches = App.ParamGet("User parameter:BaseApp/Preferences/Workbenches")
    general = App.ParamGet("User parameter:BaseApp/Preferences/General")
    changed = (
        replace_list_token(workbenches, "Ordered", retired, fallback) or changed
    )
    changed = remove_list_token(workbenches, "Disabled", retired) or changed
    changed = (
        replace_list_token(general, "BackgroundAutoloadModules", retired, fallback)
        or changed
    )
    for key in ("AutoloadModule", "LastModule"):
        if general.GetString(key, "") == retired:
            general.SetString(key, fallback)
            changed = True
    migration.SetBool(migration_key, True)
    return changed


try:
    _restore_vibecad_disabled_workbenches()
    if _migrate_removed_architecture_workbench():
        _warn("Removed saved references to the retired architecture workbench")
    if _migrate_consolidated_part_workbench():
        _warn("Migrated saved Part workbench references to Part Design")
    if _migrate_removed_openscad_workbench():
        _warn("Migrated saved OpenSCAD workbench references to Mesh")
except Exception as exc:
    _warn(f"VibeCAD workbench preference migration failed: {exc}")


try:
    fasteners_available = _check_bundled_fasteners()
    from PySide import QtCore

    import VibeCADGui

    VibeCADGui.ensure_commands_registered()
    import VibeCADAnalyzeStudyGui

    VibeCADAnalyzeStudyGui.ensure_command_registered()
    import VibeCADManufactureFollowUpGui

    VibeCADManufactureFollowUpGui.ensure_command_registered()
    import VibeCADManufactureSimulationResultGui

    VibeCADManufactureSimulationResultGui.ensure_command_registered()
    _load_ribbon_extension_commands()
    if fasteners_available:
        try:
            import VibeCADFastenersGui

            VibeCADFastenersGui.ensure_commands_registered()
        except Exception as exc:
            _warn(f"VibeCAD standard-component commands failed to register: {exc}")

    def _setup_development_identity() -> None:
        """Mark only repo-launcher sessions with their exact source revision."""

        try:
            import os

            if str(os.environ.get("VIBECAD_DEV_MODE") or "").strip() != "1":
                return
            from PySide import QtWidgets
            import FreeCADGui as Gui

            source_sha = str(
                os.environ.get("VIBECAD_DEV_SOURCE_SHA") or "unknown"
            ).strip()
            if (
                str(os.environ.get("VIBECAD_DEV_ATTESTATION_REQUIRED") or "").strip()
                == "1"
            ):
                import VibeCADAgentControl

                runtime_identity = VibeCADAgentControl.development_runtime_identity()
                if runtime_identity is None:
                    raise RuntimeError(
                        "The attested development runtime identity is unavailable."
                    )
                source_sha = str(runtime_identity["commit"])
            source_sha = source_sha[:12]
            marker = f"VibeCAD DEV • {source_sha}"
            main_window = Gui.getMainWindow()
            if main_window is None:
                return

            title_guard_property = "VibeCADDevelopmentIdentityTitleGuard"
            if not bool(main_window.property(title_guard_property)):

                def preserve_development_title(
                    title,
                    guarded_window=main_window,
                    guarded_marker=marker,
                ):
                    current = str(title or "")
                    if guarded_marker in current:
                        return
                    guarded_window.setWindowTitle(
                        f"{current} — {guarded_marker}" if current else guarded_marker
                    )

                main_window.windowTitleChanged.connect(preserve_development_title)
                main_window.setProperty(title_guard_property, True)

            current_title = str(main_window.windowTitle() or "")
            if marker not in current_title:
                main_window.setWindowTitle(
                    f"{current_title} — {marker}" if current_title else marker
                )

            status_bar = main_window.statusBar()
            if status_bar is None:
                return
            label = status_bar.findChild(
                QtWidgets.QLabel,
                "VibeCADDevelopmentIdentity",
            )
            if label is None:
                label = QtWidgets.QLabel(status_bar)
                label.setObjectName("VibeCADDevelopmentIdentity")
                status_bar.addPermanentWidget(label)
            label.setText(marker)
            label.setToolTip(
                "Development VibeCAD launched from the current source checkout."
            )
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(
                    f"VibeCAD development identity failed to install: {exc}\n"
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

    def _setup_agent_control() -> None:
        try:
            import os

            from PySide import QtWidgets
            import VibeCADAgentControl
            import VibeCADGui

            starter = VibeCADAgentControl.ensure_server_started
            if str(os.environ.get("VIBECAD_DEV_MODE") or "").strip() == "1":
                starter = VibeCADAgentControl.ensure_fail_closed_server_started
            starter(document_thread_dispatch=VibeCADGui._dispatch_to_document_thread)
            application = QtWidgets.QApplication.instance()
            if application is not None:
                application.aboutToQuit.connect(
                    lambda: VibeCADAgentControl.shutdown_server(wait=False)
                )
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(
                    f"VibeCAD agent control server failed to start: {exc}\n"
                )
            except Exception:
                pass

    def _setup_aero_ribbon() -> None:
        try:
            import VibeCADAeroRibbon

            VibeCADAeroRibbon.install_aero_ribbon_tab()
        except Exception as exc:
            try:
                import FreeCAD as _App

                _App.Console.PrintWarning(
                    f"VibeCAD Aero ribbon tab failed to install: {exc}\n"
                )
            except Exception:
                pass

    QtCore.QTimer.singleShot(0, _setup_development_identity)
    QtCore.QTimer.singleShot(0, _setup_always_on_grid)
    QtCore.QTimer.singleShot(0, _setup_agent_control)
    QtCore.QTimer.singleShot(0, _setup_aero_ribbon)
except Exception as exc:
    _warn(f"VibeCAD GUI bootstrap failed: {exc}")
