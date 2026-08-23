# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI commands for explicit 3MF export and PrusaSlicer handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import PrintIcons
import PrintPreferences
import VibeCADPrint


_WARNED_OLD_INSTALLATIONS: set[tuple[tuple[str, ...], str]] = set()


def _console(message: str, kind: str = "message") -> None:
    try:
        import FreeCAD

        writer = {
            "error": FreeCAD.Console.PrintError,
            "warning": FreeCAD.Console.PrintWarning,
        }.get(kind, FreeCAD.Console.PrintMessage)
        writer(message.rstrip() + "\n")
    except Exception:
        print(message)


def _main_window() -> Any:
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _warning(title: str, message: str) -> None:
    _console(message, "warning")
    from PySide import QtWidgets

    QtWidgets.QMessageBox.warning(_main_window(), title, message)


def _status(message: str) -> None:
    _console(message)
    try:
        window = _main_window()
        bar = window.statusBar() if window is not None else None
        if bar is not None:
            bar.showMessage(message, 8000)
    except Exception:
        pass


def _active_selection() -> tuple[Any, tuple[Any, ...]]:
    import FreeCAD
    import FreeCADGui

    document = FreeCAD.ActiveDocument
    selected = tuple(FreeCADGui.Selection.getSelection() or ())
    objects = VibeCADPrint.collect_printable_objects(
        selected,
        active_document=document,
    )
    return document, objects


def _selection_available() -> bool:
    try:
        import FreeCAD
        import FreeCADGui

        return FreeCAD.ActiveDocument is not None and bool(
            FreeCADGui.Selection.getSelection()
        )
    except Exception:
        return False


def _validated_saved_setup(
    installation: VibeCADPrint.SlicerInstallation,
    backend: VibeCADPrint.PrusaSlicerBackend,
    parent: Any,
) -> tuple[VibeCADPrint.PrintSetup | None, str]:
    setup = PrintPreferences.load_confirmed_setup()
    if setup is None:
        return (
            None,
            "Choose and confirm the exact printer, print, and material profiles.",
        )
    import PrintSetupDialog

    try:
        printers = PrintSetupDialog.run_with_progress(
            parent,
            "Checking installed printer profiles...",
            lambda: backend.query_printers(installation),
        )
        printer = next(
            (value for value in printers if value.name == setup.printer_profile), None
        )
        if printer is None:
            return (
                None,
                f"Printer profile '{setup.printer_profile}' is no longer installed.",
            )
        catalog = PrintSetupDialog.run_with_progress(
            parent,
            "Checking compatible print and material profiles...",
            lambda: backend.query_profiles(installation, printer.name),
        )
    except Exception as exc:
        return None, f"Could not refresh installed PrusaSlicer profiles: {exc}"
    errors = VibeCADPrint.validate_setup(setup, printer, catalog)
    return (setup, "") if not errors else (None, "\n".join(errors))


def _confirm_old_installation(
    installation: VibeCADPrint.SlicerInstallation, parent: Any
) -> bool:
    key = (installation.gui_command, installation.version)
    if key in _WARNED_OLD_INSTALLATIONS:
        return True
    from PySide import QtWidgets

    result = QtWidgets.QMessageBox.question(
        parent,
        "Older PrusaSlicer",
        f"PrusaSlicer {installation.version} is older than the tested 2.9.6 "
        "integration baseline.\n\nVibeCAD will open the selected 3MF without "
        "passing printer, print, or material profiles. Continue?",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
    )
    if result == QtWidgets.QMessageBox.Yes:
        _WARNED_OLD_INSTALLATIONS.add(key)
        return True
    return False


def _resolve_handoff_configuration(
    backend: VibeCADPrint.PrusaSlicerBackend,
    parent: Any,
) -> tuple[VibeCADPrint.SlicerInstallation, VibeCADPrint.PrintSetup | None] | None:
    import PrintSetupDialog

    override = PrintPreferences.executable_override()
    installations = backend.discover(override)
    installation = VibeCADPrint.preferred_installation(installations)
    if installation is not None and not installation.tested:
        if _confirm_old_installation(installation, parent):
            return installation, None
        return None

    reason = ""
    if installation is not None:
        setup, reason = _validated_saved_setup(installation, backend, parent)
        if setup is not None:
            return installation, setup

    choice = PrintSetupDialog.choose_print_setup(
        parent=parent,
        backend=backend,
        open_after_save=True,
        initial_installation=installation,
        initial_message=reason,
    )
    if not choice.accepted or choice.installation is None:
        return None
    return choice.installation, choice.setup


def _managed_cache_directory() -> Path:
    import FreeCAD

    return Path(str(FreeCAD.getUserCachePath())) / "VibeCAD" / "3DPrint" / "handoff"


def _open_selected_in_prusaslicer() -> None:
    try:
        document, objects = _active_selection()
    except VibeCADPrint.PrintSelectionError as exc:
        _warning("Open in PrusaSlicer", str(exc))
        return
    parent = _main_window()
    backend = VibeCADPrint.PrusaSlicerBackend()
    resolved = _resolve_handoff_configuration(backend, parent)
    if resolved is None:
        return
    installation, setup = resolved
    destination = VibeCADPrint.managed_handoff_path(
        _managed_cache_directory(),
        document_label=str(getattr(document, "Label", "") or "Untitled"),
        object_names=tuple(str(getattr(obj, "Name", "")) for obj in objects),
    )
    try:
        VibeCADPrint.export_selection_3mf(objects, destination)
        result = backend.launch(installation, destination, setup)
        VibeCADPrint.prune_managed_handoffs(
            destination.parent,
            keep=VibeCADPrint.DEFAULT_HANDOFF_LIMIT,
        )
    except VibeCADPrint.SlicerError as exc:
        _warning("Open in PrusaSlicer", str(exc))
        return
    profile = (
        setup.printer_profile
        if setup is not None
        else "profiles selected in PrusaSlicer"
    )
    _status(
        f"Opened {len(objects)} selected object(s) in {installation.display_name} "
        f"using {profile}. Process {result.process_id or 'started'}."
    )


def _save_selected_3mf() -> None:
    try:
        document, objects = _active_selection()
    except VibeCADPrint.PrintSelectionError as exc:
        _warning("Save 3MF", str(exc))
        return
    from PySide import QtWidgets

    label = str(getattr(document, "Label", "") or "Selection")
    initial = str(Path.home() / f"{label}.3mf")
    selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
        _main_window(),
        "Save selected objects as 3MF",
        initial,
        "3MF files (*.3mf);;All files (*)",
    )
    if not selected:
        return
    destination = Path(selected)
    if destination.suffix.lower() != ".3mf":
        destination = destination.with_suffix(".3mf")
    try:
        VibeCADPrint.export_selection_3mf(objects, destination)
    except VibeCADPrint.SlicerError as exc:
        _warning("Save 3MF", str(exc))
        return
    _status(f"Saved {len(objects)} selected object(s) to {destination}.")


class _OpenInPrusaSlicerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": PrintIcons.icon_path("open"),
            "MenuText": "Open in PrusaSlicer",
            "ToolTip": (
                "Export the explicitly selected printable objects as 3MF and open "
                "them with the confirmed PrusaSlicer setup"
            ),
        }

    def IsActive(self) -> bool:
        return _selection_available()

    def Activated(self) -> None:
        _open_selected_in_prusaslicer()


class _Save3MFCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": PrintIcons.icon_path("save"),
            "MenuText": "Save 3MF",
            "ToolTip": "Save the explicitly selected printable objects as one 3MF",
        }

    def IsActive(self) -> bool:
        return _selection_available()

    def Activated(self) -> None:
        _save_selected_3mf()


class _PrintSetupCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": PrintIcons.icon_path("setup"),
            "MenuText": "Print Setup",
            "ToolTip": (
                "Locate PrusaSlicer and explicitly confirm printer, print, material, "
                "auto-arrange, and ensure-on-bed choices"
            ),
        }

    def IsActive(self) -> bool:
        return True

    def Activated(self) -> None:
        import PrintSetupDialog

        PrintSetupDialog.choose_print_setup(
            parent=_main_window(),
            backend=VibeCADPrint.PrusaSlicerBackend(),
            open_after_save=False,
        )


def register_commands(gui: Any | None = None) -> None:
    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    commands = {
        "VibeCADPrint_OpenInPrusaSlicer": _OpenInPrusaSlicerCommand(),
        "VibeCADPrint_Save3MF": _Save3MFCommand(),
        "VibeCADPrint_Setup": _PrintSetupCommand(),
    }
    existing = {str(command) for command in getattr(gui, "listCommands", lambda: [])()}
    for name, command in commands.items():
        if name not in existing:
            gui.addCommand(name, command)
