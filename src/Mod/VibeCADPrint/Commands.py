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


def _whole_object_for_selection(entry: Any) -> Any | None:
    """Resolve a picked body subelement to the complete printable body."""

    obj = getattr(entry, "Object", None)
    if obj is None or not tuple(getattr(entry, "SubElementNames", ()) or ()):
        return obj
    parent_getter = getattr(obj, "getParentGeoFeatureGroup", None)
    if not callable(parent_getter):
        return obj
    try:
        parent = parent_getter()
    except Exception:
        return obj
    if parent is None:
        return obj
    type_id = str(getattr(parent, "TypeId", ""))
    is_derived_from = getattr(parent, "isDerivedFrom", None)
    try:
        is_body = type_id == "PartDesign::Body" or (
            callable(is_derived_from) and is_derived_from("PartDesign::Body")
        )
    except Exception:
        is_body = type_id == "PartDesign::Body"
    return parent if is_body else obj


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
    selected = tuple(
        obj
        for obj in (
            _whole_object_for_selection(entry)
            for entry in (FreeCADGui.Selection.getSelectionEx() or ())
        )
        if obj is not None
    )
    objects = VibeCADPrint.collect_printable_objects(
        selected,
        active_document=document,
    )
    return document, objects


def _resolved_selection(
    selection: tuple[Any, tuple[Any, ...]] | None,
) -> tuple[Any, tuple[Any, ...]]:
    if selection is None:
        return _active_selection()
    document, selected = selection
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
    override = PrintPreferences.executable_override()
    installations = backend.discover(override)
    installation = VibeCADPrint.preferred_installation(installations)
    if installation is None:
        _warning(
            "Print Setup Required",
            "PrusaSlicer is not configured. Click Setup before printing.",
        )
        return None
    if installation is not None and not installation.tested:
        if _confirm_old_installation(installation, parent):
            return installation, None
        return None

    setup, reason = _validated_saved_setup(installation, backend, parent)
    if setup is not None:
        return installation, setup
    _warning(
        "Print Setup Required",
        reason + "\n\nClick Setup to review the exact installed profiles.",
    )
    return None


def _managed_cache_directory() -> Path:
    import FreeCAD

    return Path(str(FreeCAD.getUserCachePath())) / "VibeCAD" / "3DPrint" / "handoff"


def _handoff_destination(
    document: Any,
    objects: tuple[Any, ...],
) -> tuple[Path, bool]:
    storage = PrintPreferences.load_handoff_storage()
    label = str(getattr(document, "Label", "") or "Untitled")
    names = tuple(str(getattr(obj, "Name", "")) for obj in objects)
    if storage.mode == "folder":
        return (
            VibeCADPrint.persistent_handoff_path(
                storage.directory,
                document_label=label,
                object_names=names,
            ),
            False,
        )
    return (
        VibeCADPrint.managed_handoff_path(
            _managed_cache_directory(),
            document_label=label,
            object_names=names,
        ),
        True,
    )


def open_selected_in_prusaslicer(
    *,
    installation: VibeCADPrint.SlicerInstallation | None = None,
    setup: VibeCADPrint.PrintSetup | None = None,
    selection: tuple[Any, tuple[Any, ...]] | None = None,
) -> bool:
    """Export and open the selection, optionally using panel-validated choices."""

    try:
        document, objects = _resolved_selection(selection)
    except VibeCADPrint.PrintSelectionError as exc:
        _warning("Open in PrusaSlicer", str(exc))
        return False
    backend = VibeCADPrint.PrusaSlicerBackend()
    if installation is None:
        resolved = _resolve_handoff_configuration(backend, _main_window())
        if resolved is None:
            return False
        installation, setup = resolved
    return _open_resolved_in_slicer(
        backend=backend,
        installation=installation,
        setup=setup,
        document=document,
        objects=objects,
    )


def open_selected_in_slicer(
    *,
    backend: Any,
    installation: VibeCADPrint.SlicerInstallation,
    setup: VibeCADPrint.PrintSetup | None,
    selection: tuple[Any, tuple[Any, ...]] | None = None,
) -> bool:
    """Export, prepare, and launch using an already validated slicer backend."""

    slicer_name = str(
        getattr(backend, "display_name", "")
        or getattr(installation, "display_name", "")
        or "slicer"
    )
    try:
        document, objects = _resolved_selection(selection)
    except VibeCADPrint.PrintSelectionError as exc:
        _warning(f"Open in {slicer_name}", str(exc))
        return False
    return _open_resolved_in_slicer(
        backend=backend,
        installation=installation,
        setup=setup,
        document=document,
        objects=objects,
    )


def _open_resolved_in_slicer(
    *,
    backend: Any,
    installation: VibeCADPrint.SlicerInstallation,
    setup: VibeCADPrint.PrintSetup | None,
    document: Any,
    objects: tuple[Any, ...],
) -> bool:
    slicer_name = str(
        getattr(backend, "display_name", "")
        or getattr(installation, "display_name", "")
        or "slicer"
    )
    try:
        destination, managed = _handoff_destination(document, objects)
        VibeCADPrint.export_selection_3mf(objects, destination)
        if installation.tested and setup is not None:
            import PrintSetupDialog

            PrintSetupDialog.run_with_progress(
                _main_window(),
                f"Arranging objects and preparing the {slicer_name} project…",
                lambda: backend.prepare_project(
                    installation,
                    destination,
                    destination,
                    setup,
                ),
            )
        result = backend.launch(installation, destination, setup)
        if managed:
            VibeCADPrint.prune_managed_handoffs(
                destination.parent,
                keep=VibeCADPrint.DEFAULT_HANDOFF_LIMIT,
            )
    except (OSError, VibeCADPrint.SlicerError) as exc:
        _warning(f"Open in {slicer_name}", str(exc))
        return False
    profile = (
        setup.printer_profile
        if setup is not None
        else f"profiles selected in {slicer_name}"
    )
    _status(
        f"Opened {len(objects)} selected object(s) in {installation.display_name} "
        f"using {profile}. 3MF: {destination}. Process {result.process_id or 'started'}."
    )
    return True


def _open_selected_in_prusaslicer() -> None:
    open_selected_in_prusaslicer()


def _save_selected_3mf(
    *,
    selection: tuple[Any, tuple[Any, ...]] | None = None,
) -> None:
    try:
        document, objects = _resolved_selection(selection)
    except VibeCADPrint.PrintSelectionError as exc:
        _warning("Save 3MF", str(exc))
        return
    from PySide import QtWidgets

    label = str(getattr(document, "Label", "") or "Selection")
    storage = PrintPreferences.load_handoff_storage()
    initial_root = Path(storage.directory) if storage.mode == "folder" else Path.home()
    initial = str(initial_root / f"{label}.3mf")
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
    except (OSError, VibeCADPrint.SlicerError) as exc:
        _warning("Save 3MF", str(exc))
        return
    _status(f"Saved {len(objects)} selected object(s) to {destination}.")


class _OpenInPrusaSlicerCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": PrintIcons.icon_path("open"),
            "MenuText": "Print",
            "ToolTip": (
                "Export the explicitly selected printable objects as 3MF and open "
                "them with the confirmed PrusaSlicer setup"
            ),
        }

    def IsActive(self) -> bool:
        return _selection_available()

    def Activated(self) -> None:
        import PrintPanel

        panel = PrintPanel.show_panel(refresh=False)
        if panel is not None:
            panel.print_selected()


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
        import PrintPanel

        PrintPanel.open_setup_dialog(parent=_main_window())


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
