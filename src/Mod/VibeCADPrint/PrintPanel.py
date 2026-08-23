# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent daily-use 3D Print panel backed by exact PrusaSlicer profiles."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide import QtCore, QtWidgets

import PrintPreferences
import PrintSetupDialog
import VibeCADPrint


DOCK_NAME = "VibeCADPrintPanel"
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vibecad-print-panel")


def _wrapped(label: Any) -> Any:
    label.setWordWrap(True)
    policy = label.sizePolicy()
    policy.setVerticalPolicy(QtWidgets.QSizePolicy.Minimum)
    label.setSizePolicy(policy)
    return label


def _compact_combo(combo: Any) -> Any:
    combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(12)
    combo.setMinimumWidth(0)
    combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    return combo


def _active_print_selection() -> tuple[Any, tuple[Any, ...]]:
    import PrintCommandLoader

    return PrintCommandLoader.command_module()._active_selection()


class _Bridge(QtCore.QObject):
    finished = QtCore.Signal(int, object, object)


class _SelectionObserver:
    def __init__(self, panel: "PrintPanelWidget") -> None:
        self.panel = panel

    def _changed(self) -> None:
        QtCore.QTimer.singleShot(0, self.panel._update_selection_summary)

    def addSelection(self, *_args) -> None:
        self._changed()

    def removeSelection(self, *_args) -> None:
        self._changed()

    def setSelection(self, *_args) -> None:
        self._changed()

    def clearSelection(self, *_args) -> None:
        self._changed()


class PrintPanelWidget(QtWidgets.QWidget):
    """Remembered, non-modal controls for the normal print workflow."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADPrintPanelContents")
        self.setWindowTitle("3D Print")
        self.setMinimumWidth(280)
        self.backend = VibeCADPrint.PrusaSlicerBackend()
        self.installation: VibeCADPrint.SlicerInstallation | None = None
        self.printers: tuple[VibeCADPrint.PrinterProfile, ...] = ()
        self.catalog: VibeCADPrint.ProfileCatalog | None = None
        self.material_combos: list[Any] = []
        self._remembered = PrintPreferences.load_confirmed_setup()
        self._job = 0
        self._callback: Callable[[Any], None] | None = None
        self._futures: set[Future[Any]] = set()
        self._busy = False
        self._print_selection_ready = False
        self._selection_observer: _SelectionObserver | None = None
        self._bridge = _Bridge(self)
        self._bridge.finished.connect(self._job_finished)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>Print with PrusaSlicer</b>", self)
        header.addWidget(title, 1)
        self.refresh_button = QtWidgets.QPushButton("Refresh", self)
        self.refresh_button.setToolTip("Reload profiles installed by PrusaSlicer")
        self.refresh_button.clicked.connect(self.refresh)
        self.setup_button = QtWidgets.QPushButton("Setup…", self)
        self.setup_button.setToolTip(
            "Locate PrusaSlicer and configure profiles and 3MF storage"
        )
        self.setup_button.clicked.connect(self._initial_setup)
        header.addWidget(self.refresh_button)
        header.addWidget(self.setup_button)
        outer.addLayout(header)

        self.installation_label = _wrapped(QtWidgets.QLabel(self))
        self.installation_label.setObjectName("VibeCADPrintInstallationSummary")
        outer.addWidget(self.installation_label)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setObjectName("VibeCADPrintPanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget(scroll)
        content.setMinimumWidth(0)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        content_layout.addWidget(QtWidgets.QLabel("<b>SELECTED OBJECTS</b>", content))
        self.selection_summary = _wrapped(QtWidgets.QLabel(content))
        self.selection_summary.setObjectName("VibeCADPrintSelectionSummary")
        self.selection_summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        content_layout.addWidget(self.selection_summary)

        content_layout.addWidget(QtWidgets.QLabel("<b>PRINT SETTINGS</b>", content))
        profiles = QtWidgets.QFormLayout()
        profiles.setContentsMargins(0, 0, 0, 0)
        profiles.setSpacing(6)
        profiles.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        profiles.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)

        self.printer_combo = _compact_combo(QtWidgets.QComboBox(content))
        self.printer_combo.setObjectName("VibeCADPrintPrinterProfile")
        self.printer_combo.currentIndexChanged.connect(self._printer_changed)
        profiles.addRow("Printer", self.printer_combo)

        self.bed_details = _wrapped(QtWidgets.QLabel(content))
        self.bed_details.setObjectName("VibeCADPrintBuildVolume")
        profiles.addRow("", self.bed_details)

        self.print_combo = _compact_combo(QtWidgets.QComboBox(content))
        self.print_combo.setObjectName("VibeCADPrintPrintProfile")
        self.print_combo.currentIndexChanged.connect(self._print_changed)
        profiles.addRow("Quality", self.print_combo)

        self.material_widget = QtWidgets.QWidget(content)
        self.material_layout = QtWidgets.QFormLayout(self.material_widget)
        self.material_layout.setContentsMargins(0, 0, 0, 0)
        self.material_layout.setSpacing(6)
        self.material_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )
        self.material_layout.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        profiles.addRow("Material", self.material_widget)
        content_layout.addLayout(profiles)

        content_layout.addWidget(QtWidgets.QLabel("<b>PLACEMENT</b>", content))
        placement = QtWidgets.QHBoxLayout()
        placement.setContentsMargins(0, 0, 0, 0)
        self.auto_arrange = QtWidgets.QCheckBox("Auto-arrange", content)
        self.ensure_on_bed = QtWidgets.QCheckBox("Ensure on bed", content)
        remembered = self._remembered
        self.auto_arrange.setChecked(
            remembered.auto_arrange if remembered is not None else True
        )
        self.ensure_on_bed.setChecked(
            remembered.ensure_on_bed if remembered is not None else True
        )
        self.auto_arrange.toggled.connect(self._update_actions)
        self.ensure_on_bed.toggled.connect(self._update_actions)
        placement.addWidget(self.auto_arrange)
        placement.addWidget(self.ensure_on_bed)
        placement.addStretch(1)
        content_layout.addLayout(placement)

        content_layout.addWidget(QtWidgets.QLabel("<b>3MF OUTPUT</b>", content))
        self.output_location = _wrapped(QtWidgets.QLabel(content))
        self.output_location.setObjectName("VibeCADPrintOutputLocation")
        self.output_location.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        content_layout.addWidget(self.output_location)
        automatic = _wrapped(
            QtWidgets.QLabel(
                "Selections are saved automatically. Change the slicer or output "
                "location in Setup.",
                content,
            )
        )
        content_layout.addWidget(automatic)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.status = _wrapped(QtWidgets.QLabel(self))
        self.status.setObjectName("VibeCADPrintPanelStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        outer.addWidget(self.status)

        self.print_button = QtWidgets.QPushButton("Print", self)
        self.print_button.setObjectName("VibeCADPrintPrimaryAction")
        self.print_button.setMinimumHeight(38)
        self.print_button.setToolTip(
            "Export the selection and open it with these exact profiles"
        )
        self.print_button.clicked.connect(self._open_selected)
        outer.addWidget(self.print_button)

        self.export_button = QtWidgets.QPushButton("Export 3MF…", self)
        self.export_button.clicked.connect(self._save_selected)
        outer.addWidget(self.export_button)

        # Compatibility aliases for callers of the prototype widget.
        self.open_button = self.print_button
        self.apply_button = QtWidgets.QPushButton(self)
        self.apply_button.hide()
        self.installation_combo = QtWidgets.QComboBox(self)
        self.installation_combo.hide()

        self._update_output_location()
        self._clear_profiles()
        self._attach_selection_observer()
        self._update_selection_summary()
        self._update_actions()

    def sizeHint(self):
        return QtCore.QSize(360, 640)

    def refresh(self) -> None:
        self._remembered = PrintPreferences.load_confirmed_setup()
        if self._remembered is not None:
            self.auto_arrange.setChecked(self._remembered.auto_arrange)
            self.ensure_on_bed.setChecked(self._remembered.ensure_on_bed)
        self._update_output_location()
        override = PrintPreferences.executable_override()
        self._clear_profiles()
        self._start_job(
            "Loading installed PrusaSlicer profiles…",
            lambda: self.backend.discover(override),
            self._installations_loaded,
        )

    def _update_output_location(self) -> None:
        storage = PrintPreferences.load_handoff_storage()
        text = storage.directory if storage.mode == "folder" else "Managed VibeCAD cache"
        self.output_location.setText(text)
        self.output_location.setToolTip(text)

    def _attach_selection_observer(self) -> None:
        try:
            import FreeCADGui

            observer = _SelectionObserver(self)
            FreeCADGui.Selection.addObserver(observer)
            self._selection_observer = observer
            self.destroyed.connect(self._detach_selection_observer)
        except Exception:
            self._selection_observer = None

    def _detach_selection_observer(self, *_args) -> None:
        observer = self._selection_observer
        self._selection_observer = None
        if observer is None:
            return
        try:
            import FreeCADGui

            FreeCADGui.Selection.removeObserver(observer)
        except Exception:
            pass

    def _update_selection_summary(self) -> None:
        try:
            _document, objects = _active_print_selection()
        except (VibeCADPrint.PrintSelectionError, RuntimeError) as exc:
            self._print_selection_ready = False
            self.selection_summary.setText(str(exc))
            self.selection_summary.setToolTip(str(exc))
        except Exception:
            self._print_selection_ready = False
            message = "Select one or more printable objects."
            self.selection_summary.setText(message)
            self.selection_summary.setToolTip(message)
        else:
            labels = tuple(
                str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or "Object")
                for obj in objects
            )
            count = len(labels)
            heading = f"{count} object{'s' if count != 1 else ''} will be sent"
            self.selection_summary.setText(
                heading + "\n" + "\n".join(f"• {label}" for label in labels)
            )
            self.selection_summary.setToolTip("\n".join(labels))
            self._print_selection_ready = bool(objects)
        self._update_actions()

    def _start_job(
        self,
        message: str,
        operation: Callable[[], Any],
        callback: Callable[[Any], None],
    ) -> None:
        self._job += 1
        job = self._job
        self._callback = callback
        self._set_busy(True)
        self.status.setText(message)
        future = _EXECUTOR.submit(operation)
        self._futures.add(future)

        def complete(value: Future[Any]) -> None:
            self._futures.discard(value)
            try:
                result, error = value.result(), None
            except Exception as exc:
                result, error = None, exc
            try:
                self._bridge.finished.emit(job, result, error)
            except RuntimeError:
                pass

        future.add_done_callback(complete)

    def _job_finished(self, job: int, result: Any, error: Any) -> None:
        if job != self._job:
            return
        callback = self._callback
        self._callback = None
        self._set_busy(False)
        if error is not None:
            self.status.setText(str(error))
            self._update_actions()
            return
        if callback is not None:
            callback(result)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        self.printer_combo.setEnabled(not busy)
        self.print_combo.setEnabled(not busy)
        if busy:
            self.print_button.setEnabled(False)

    def _installations_loaded(self, values: Any) -> None:
        installations = tuple(values or ())
        preferred = VibeCADPrint.preferred_installation(installations)
        self.installation_combo.blockSignals(True)
        self.installation_combo.clear()
        for installation in installations:
            self.installation_combo.addItem(installation.display_name, installation)
        self.installation_combo.blockSignals(False)
        self.installation = preferred
        if preferred is None:
            self.installation_label.setText("PrusaSlicer is not configured")
            self.status.setText("Click Setup to locate PrusaSlicer and choose profiles.")
            self._update_actions()
            return
        self.installation_label.setText(preferred.display_name)
        if not preferred.tested:
            self.status.setText(
                f"PrusaSlicer {preferred.version} is below the tested 2.9.6 baseline."
            )
            self._update_actions()
            return
        self._start_job(
            "Loading printer profiles…",
            lambda: self.backend.query_printers(preferred),
            self._printers_loaded,
        )

    def _selected_installation(self) -> VibeCADPrint.SlicerInstallation | None:
        return self.installation

    def _installation_changed(self, _index: int) -> None:
        value = self.installation_combo.currentData()
        self.installation = (
            value if isinstance(value, VibeCADPrint.SlicerInstallation) else None
        )
        self._clear_profiles()
        self._update_actions()

    def _clear_profiles(self) -> None:
        self.printers = ()
        self.catalog = None
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose printer…", None)
        self.printer_combo.blockSignals(False)
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose quality…", None)
        self.print_combo.blockSignals(False)
        self.bed_details.setText("")
        self._clear_materials()

    def _printers_loaded(self, values: Any) -> None:
        self.printers = tuple(values or ())
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose printer…", None)
        selected = 0
        for index, printer in enumerate(self.printers, start=1):
            self.printer_combo.addItem(
                printer.name + (" — user" if printer.is_user else ""), printer
            )
            if self._remembered and printer.name == self._remembered.printer_profile:
                selected = index
        self.printer_combo.setCurrentIndex(selected)
        self.printer_combo.blockSignals(False)
        if selected:
            self._printer_changed(selected)
        else:
            self.status.setText("Choose the exact printer profile.")
            self._update_actions()

    def _selected_printer(self) -> VibeCADPrint.PrinterProfile | None:
        value = self.printer_combo.currentData()
        return value if isinstance(value, VibeCADPrint.PrinterProfile) else None

    def _printer_changed(self, _index: int) -> None:
        printer = self._selected_printer()
        self.catalog = None
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose quality…", None)
        self.print_combo.blockSignals(False)
        self._clear_materials()
        if printer is None:
            self.bed_details.setText("")
            self.status.setText("Choose the exact printer profile.")
            self._update_actions()
            return
        bed = printer.bed
        self.bed_details.setText(
            f"{bed.width:g} × {bed.height:g} × {bed.max_print_height:g} mm · "
            f"{printer.extruders} extruder(s)"
        )
        if self.installation is None:
            return
        self._start_job(
            f"Loading profiles for {printer.name}…",
            lambda: self.backend.query_profiles(self.installation, printer.name),
            self._profiles_loaded,
        )

    def _profiles_loaded(self, value: Any) -> None:
        if not isinstance(value, VibeCADPrint.ProfileCatalog):
            self.status.setText("PrusaSlicer returned an invalid profile catalog.")
            return
        printer = self._selected_printer()
        if printer is None or value.printer_profile != printer.name:
            self.status.setText("PrusaSlicer returned profiles for another printer.")
            return
        self.catalog = value
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose quality…", None)
        selected = 0
        for index, profile in enumerate(value.print_profiles, start=1):
            self.print_combo.addItem(
                profile.name + (" — user" if profile.is_user else ""), profile
            )
            if (
                self._remembered
                and self._remembered.printer_profile == printer.name
                and profile.name == self._remembered.print_profile
            ):
                selected = index
        self.print_combo.setCurrentIndex(selected)
        self.print_combo.blockSignals(False)
        if selected:
            self._print_changed(selected)
        else:
            self.status.setText("Choose the exact compatible quality profile.")
            self._update_actions()

    def _selected_print(self) -> VibeCADPrint.PrintProfile | None:
        value = self.print_combo.currentData()
        return value if isinstance(value, VibeCADPrint.PrintProfile) else None

    def _clear_materials(self) -> None:
        self.material_combos.clear()
        while self.material_layout.count():
            item = self.material_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _print_changed(self, _index: int) -> None:
        self._clear_materials()
        printer = self._selected_printer()
        profile = self._selected_print()
        if printer is None or profile is None:
            self._update_actions()
            return
        for extruder in range(printer.extruders):
            combo = _compact_combo(QtWidgets.QComboBox(self.material_widget))
            combo.addItem("Choose material…", None)
            for material in profile.materials:
                combo.addItem(
                    material.name + (" — user" if material.is_user else ""),
                    material.name,
                )
            if (
                self._remembered
                and self._remembered.printer_profile == printer.name
                and self._remembered.print_profile == profile.name
                and extruder < len(self._remembered.material_profiles)
            ):
                index = combo.findData(self._remembered.material_profiles[extruder])
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._update_actions)
            label = "" if printer.extruders == 1 else f"Extruder {extruder + 1}"
            self.material_layout.addRow(label, combo)
            self.material_combos.append(combo)
        self._update_actions()

    def _current_setup(self) -> VibeCADPrint.PrintSetup | None:
        printer = self._selected_printer()
        profile = self._selected_print()
        if printer is None or profile is None or self.catalog is None:
            return None
        materials = tuple(str(combo.currentData() or "") for combo in self.material_combos)
        if len(materials) != printer.extruders or any(not value for value in materials):
            return None
        setup = VibeCADPrint.PrintSetup(
            printer_profile=printer.name,
            print_profile=profile.name,
            material_profiles=materials,
            auto_arrange=self.auto_arrange.isChecked(),
            ensure_on_bed=self.ensure_on_bed.isChecked(),
        )
        return None if VibeCADPrint.validate_setup(setup, printer, self.catalog) else setup

    def _current_storage(self) -> PrintPreferences.HandoffStorage:
        return PrintPreferences.load_handoff_storage()

    def _storage_changed(self, *_args) -> None:
        self._update_output_location()
        self._update_actions()

    def _update_actions(self, *_args) -> None:
        setup = self._current_setup()
        ready = (
            setup is not None
            and self.installation is not None
            and self._print_selection_ready
            and not self._busy
        )
        self.print_button.setEnabled(ready)
        self.export_button.setEnabled(self._print_selection_ready)
        if setup is not None:
            PrintPreferences.save_confirmed_setup(setup)
            self._remembered = setup
            self.status.setText("Ready · selections saved automatically")
        elif not self._busy and self.status.text().startswith("Ready"):
            self.status.setText("Choose a printer, quality, and material.")

    def _save(self) -> bool:
        setup = self._current_setup()
        if setup is None:
            self.status.setText("Choose a printer, quality, and material.")
            return False
        PrintPreferences.save_confirmed_setup(setup)
        self._remembered = setup
        self.status.setText("Ready · selections saved automatically")
        return True

    def _browse_folder(self) -> None:
        self._initial_setup()

    def print_selected(self) -> None:
        self._update_selection_summary()
        if not self._print_selection_ready:
            return
        if not self._save() or self.installation is None:
            return
        import PrintCommandLoader

        commands = PrintCommandLoader.command_module()
        commands.open_selected_in_prusaslicer(
            installation=self.installation,
            setup=self._current_setup(),
        )

    def _open_selected(self) -> None:
        self.print_selected()

    def _save_selected(self) -> None:
        import FreeCADGui

        FreeCADGui.runCommand("VibeCADPrint_Save3MF")

    def _initial_setup(self) -> None:
        open_setup_dialog(parent=self, backend=self.backend)


def _main_window() -> Any:
    import FreeCADGui

    return FreeCADGui.getMainWindow()


def _find_dock() -> Any | None:
    main = _main_window()
    if main is None:
        return None
    return main.findChild(QtWidgets.QDockWidget, DOCK_NAME)


def ensure_panel_registered() -> Any:
    """Create the native dock once so View > Panels can also control it."""

    dock = _find_dock()
    if dock is not None:
        return dock
    main = _main_window()
    if main is None:
        raise RuntimeError("FreeCAD main window is unavailable.")
    contents = PrintPanelWidget()
    dock = main.addDockWindow(contents, DOCK_NAME, area="right")
    dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    dock.toggleViewAction().setVisible(True)
    dock.hide()
    return dock


def show_panel(*, refresh: bool = True) -> PrintPanelWidget | None:
    dock = ensure_panel_registered()
    dock.show()
    dock.raise_()
    widget = dock.widget()
    if isinstance(widget, PrintPanelWidget):
        widget._update_selection_summary()
        if refresh:
            widget.refresh()
        return widget
    return None


def hide_panel() -> None:
    dock = _find_dock()
    if dock is not None:
        dock.hide()


def open_setup_dialog(
    *,
    parent: Any | None = None,
    backend: VibeCADPrint.PrusaSlicerBackend | None = None,
) -> Any:
    """Open explicit slicer configuration and refresh the daily-use panel."""

    panel_dock = _find_dock()
    panel = panel_dock.widget() if panel_dock is not None else None
    initial = panel.installation if isinstance(panel, PrintPanelWidget) else None
    choice = PrintSetupDialog.choose_print_setup(
        parent=parent or _main_window(),
        backend=backend or VibeCADPrint.PrusaSlicerBackend(),
        open_after_save=False,
        initial_installation=initial,
    )
    if choice.accepted and isinstance(panel, PrintPanelWidget):
        panel.refresh()
    return choice
