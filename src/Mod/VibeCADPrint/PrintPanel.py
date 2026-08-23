# SPDX-License-Identifier: LGPL-2.1-or-later

"""Persistent 3D Print workspace for exact PrusaSlicer handoff choices."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
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


class _Bridge(QtCore.QObject):
    finished = QtCore.Signal(int, object, object)


class PrintPanelWidget(QtWidgets.QWidget):
    """Non-modal profile, placement, and 3MF storage controls."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADPrintPanelContents")
        self.setWindowTitle("3D Print")
        self.setMinimumWidth(320)
        self.backend = VibeCADPrint.PrusaSlicerBackend()
        self.installation: VibeCADPrint.SlicerInstallation | None = None
        self.printers: tuple[VibeCADPrint.PrinterProfile, ...] = ()
        self.catalog: VibeCADPrint.ProfileCatalog | None = None
        self.material_combos: list[Any] = []
        self._remembered = PrintPreferences.load_confirmed_setup()
        self._job = 0
        self._callback: Callable[[Any], None] | None = None
        self._futures: set[Future[Any]] = set()
        self._bridge = _Bridge(self)
        self._bridge.finished.connect(self._job_finished)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        intro = _wrapped(
            QtWidgets.QLabel(
                "Choose exact installed profiles. VibeCAD never substitutes a "
                "printer, print profile, or material.",
                self,
            )
        )
        outer.addWidget(intro)

        scroll = QtWidgets.QScrollArea(self)
        scroll.setObjectName("VibeCADPrintPanelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget(scroll)
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        slicer_group = QtWidgets.QGroupBox("PrusaSlicer", content)
        slicer_layout = QtWidgets.QVBoxLayout(slicer_group)
        slicer_layout.addWidget(QtWidgets.QLabel("Detected installation", slicer_group))
        self.installation_combo = QtWidgets.QComboBox(slicer_group)
        self.installation_combo.setObjectName("VibeCADPrintInstallation")
        self.installation_combo.currentIndexChanged.connect(
            self._installation_changed
        )
        slicer_layout.addWidget(self.installation_combo)
        slicer_buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh profiles", slicer_group)
        self.refresh_button.clicked.connect(self.refresh)
        initial_setup = QtWidgets.QPushButton("Initial setup…", slicer_group)
        initial_setup.clicked.connect(self._initial_setup)
        slicer_buttons.addWidget(self.refresh_button)
        slicer_buttons.addWidget(initial_setup)
        slicer_layout.addLayout(slicer_buttons)
        content_layout.addWidget(slicer_group)

        profiles_group = QtWidgets.QGroupBox("Installed profiles", content)
        profiles_layout = QtWidgets.QVBoxLayout(profiles_group)
        profiles_layout.addWidget(QtWidgets.QLabel("Printer profile", profiles_group))
        self.printer_combo = QtWidgets.QComboBox(profiles_group)
        self.printer_combo.setObjectName("VibeCADPrintPrinterProfile")
        self.printer_combo.currentIndexChanged.connect(self._printer_changed)
        profiles_layout.addWidget(self.printer_combo)
        self.bed_details = _wrapped(QtWidgets.QLabel(profiles_group))
        self.bed_details.setObjectName("VibeCADPrintBuildVolume")
        profiles_layout.addWidget(self.bed_details)
        profiles_layout.addWidget(QtWidgets.QLabel("Print profile", profiles_group))
        self.print_combo = QtWidgets.QComboBox(profiles_group)
        self.print_combo.setObjectName("VibeCADPrintPrintProfile")
        self.print_combo.currentIndexChanged.connect(self._print_changed)
        profiles_layout.addWidget(self.print_combo)
        profiles_layout.addWidget(QtWidgets.QLabel("Materials", profiles_group))
        self.material_widget = QtWidgets.QWidget(profiles_group)
        self.material_layout = QtWidgets.QFormLayout(self.material_widget)
        self.material_layout.setContentsMargins(0, 0, 0, 0)
        self.material_layout.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        profiles_layout.addWidget(self.material_widget)
        content_layout.addWidget(profiles_group)

        placement_group = QtWidgets.QGroupBox("Placement", content)
        placement_layout = QtWidgets.QVBoxLayout(placement_group)
        self.auto_arrange = QtWidgets.QCheckBox("Auto-arrange", placement_group)
        self.ensure_on_bed = QtWidgets.QCheckBox("Ensure on bed", placement_group)
        remembered = self._remembered
        self.auto_arrange.setChecked(
            remembered.auto_arrange if remembered is not None else True
        )
        self.ensure_on_bed.setChecked(
            remembered.ensure_on_bed if remembered is not None else True
        )
        self.auto_arrange.toggled.connect(self._update_actions)
        self.ensure_on_bed.toggled.connect(self._update_actions)
        placement_layout.addWidget(self.auto_arrange)
        placement_layout.addWidget(self.ensure_on_bed)
        content_layout.addWidget(placement_group)

        storage_group = QtWidgets.QGroupBox("3MF handoff location", content)
        storage_layout = QtWidgets.QVBoxLayout(storage_group)
        self.managed_storage = QtWidgets.QRadioButton(
            "Managed VibeCAD cache", storage_group
        )
        self.folder_storage = QtWidgets.QRadioButton(
            "Choose a folder", storage_group
        )
        storage_layout.addWidget(self.managed_storage)
        storage_layout.addWidget(self.folder_storage)
        folder_row = QtWidgets.QHBoxLayout()
        self.folder_edit = QtWidgets.QLineEdit(storage_group)
        self.folder_edit.setObjectName("VibeCADPrintHandoffDirectory")
        self.folder_edit.setPlaceholderText("Folder for persistent 3MF files")
        browse = QtWidgets.QPushButton("Browse…", storage_group)
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        storage_layout.addLayout(folder_row)
        storage_note = _wrapped(
            QtWidgets.QLabel(
                "Managed files are automatically limited to recent handoffs. "
                "Files in your chosen folder are never pruned by VibeCAD.",
                storage_group,
            )
        )
        storage_layout.addWidget(storage_note)
        storage = PrintPreferences.load_handoff_storage()
        self.folder_edit.setText(storage.directory)
        self.folder_edit.textChanged.connect(self._update_actions)
        self.managed_storage.setChecked(storage.mode == "managed")
        self.folder_storage.setChecked(storage.mode == "folder")
        self.managed_storage.toggled.connect(self._storage_changed)
        self.folder_storage.toggled.connect(self._storage_changed)
        content_layout.addWidget(storage_group)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.status = _wrapped(QtWidgets.QLabel(self))
        self.status.setObjectName("VibeCADPrintPanelStatus")
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        outer.addWidget(self.status)

        setup_actions = QtWidgets.QHBoxLayout()
        self.apply_button = QtWidgets.QPushButton("Apply setup", self)
        self.apply_button.clicked.connect(self._save)
        self.open_button = QtWidgets.QPushButton("Open selected", self)
        self.open_button.clicked.connect(self._open_selected)
        setup_actions.addWidget(self.apply_button)
        setup_actions.addWidget(self.open_button)
        outer.addLayout(setup_actions)
        save_3mf = QtWidgets.QPushButton("Save selected 3MF…", self)
        save_3mf.clicked.connect(self._save_selected)
        outer.addWidget(save_3mf)

        self._clear_profiles()
        self._storage_changed()
        self._update_actions()

    def sizeHint(self):
        return QtCore.QSize(390, 720)

    def refresh(self) -> None:
        self._remembered = PrintPreferences.load_confirmed_setup()
        override = PrintPreferences.executable_override()
        self._clear_profiles()
        self._start_job(
            "Detecting PrusaSlicer and reading installed profiles…",
            lambda: self.backend.discover(override),
            self._installations_loaded,
        )

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
        self.refresh_button.setEnabled(not busy)
        self.installation_combo.setEnabled(not busy)
        self.printer_combo.setEnabled(not busy)
        self.print_combo.setEnabled(not busy)
        if busy:
            self.apply_button.setEnabled(False)

    def _installations_loaded(self, values: Any) -> None:
        installations = tuple(values or ())
        preferred = VibeCADPrint.preferred_installation(installations)
        self.installation_combo.blockSignals(True)
        self.installation_combo.clear()
        for installation in installations:
            self.installation_combo.addItem(installation.display_name, installation)
        if preferred is not None:
            for index, installation in enumerate(installations):
                if installation.gui_command == preferred.gui_command:
                    self.installation_combo.setCurrentIndex(index)
                    break
        self.installation_combo.blockSignals(False)
        if preferred is None:
            self.installation = None
            self.status.setText(
                "PrusaSlicer was not found. Use Initial setup to locate or install it."
            )
            self._update_actions()
            return
        self._installation_changed(self.installation_combo.currentIndex())

    def _selected_installation(self) -> VibeCADPrint.SlicerInstallation | None:
        value = self.installation_combo.currentData()
        return value if isinstance(value, VibeCADPrint.SlicerInstallation) else None

    def _installation_changed(self, _index: int) -> None:
        self.installation = self._selected_installation()
        self._clear_profiles()
        if self.installation is None:
            self.status.setText("Choose a PrusaSlicer installation.")
            return
        if not self.installation.tested:
            self.status.setText(
                f"PrusaSlicer {self.installation.version} is below the tested 2.9.6 "
                "profile-integration baseline."
            )
            return
        self._start_job(
            "Reading exact installed printer profiles…",
            lambda: self.backend.query_printers(self.installation),
            self._printers_loaded,
        )

    def _clear_profiles(self) -> None:
        self.printers = ()
        self.catalog = None
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose a printer profile…", None)
        self.printer_combo.blockSignals(False)
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile…", None)
        self.print_combo.blockSignals(False)
        self.bed_details.setText("No printer selected.")
        self._clear_materials()

    def _printers_loaded(self, values: Any) -> None:
        self.printers = tuple(values or ())
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose a printer profile…", None)
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
            self.status.setText("Choose the exact installed printer profile.")
            self._update_actions()

    def _selected_printer(self) -> VibeCADPrint.PrinterProfile | None:
        value = self.printer_combo.currentData()
        return value if isinstance(value, VibeCADPrint.PrinterProfile) else None

    def _printer_changed(self, _index: int) -> None:
        printer = self._selected_printer()
        self.catalog = None
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile…", None)
        self.print_combo.blockSignals(False)
        self._clear_materials()
        if printer is None:
            self.bed_details.setText("No printer selected.")
            self.status.setText("Choose the exact installed printer profile.")
            self._update_actions()
            return
        bed = printer.bed
        self.bed_details.setText(
            f"{bed.width:g} × {bed.height:g} × {bed.max_print_height:g} mm; "
            f"{printer.extruders} extruder(s)"
        )
        if self.installation is None:
            return
        self._start_job(
            f"Reading profiles compatible with {printer.name}…",
            lambda: self.backend.query_profiles(self.installation, printer.name),
            self._profiles_loaded,
        )

    def _profiles_loaded(self, value: Any) -> None:
        if not isinstance(value, VibeCADPrint.ProfileCatalog):
            self.status.setText("PrusaSlicer returned an invalid profile catalog.")
            return
        self.catalog = value
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile…", None)
        selected = 0
        printer = self._selected_printer()
        for index, profile in enumerate(value.print_profiles, start=1):
            self.print_combo.addItem(
                profile.name + (" — user" if profile.is_user else ""), profile
            )
            if (
                self._remembered
                and printer is not None
                and self._remembered.printer_profile == printer.name
                and profile.name == self._remembered.print_profile
            ):
                selected = index
        self.print_combo.setCurrentIndex(selected)
        self.print_combo.blockSignals(False)
        if selected:
            self._print_changed(selected)
        else:
            self.status.setText("Choose the exact compatible print profile.")
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
            combo = QtWidgets.QComboBox(self.material_widget)
            combo.addItem("Choose a material profile…", None)
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
            self.material_layout.addRow(f"Extruder {extruder + 1}", combo)
            self.material_combos.append(combo)
        self.status.setText("Review the exact setup, then click Apply setup.")
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

    def _current_storage(self) -> PrintPreferences.HandoffStorage | None:
        directory = self.folder_edit.text().strip()
        if self.folder_storage.isChecked():
            if not directory:
                return None
            return PrintPreferences.HandoffStorage("folder", directory)
        return PrintPreferences.HandoffStorage("managed", directory)

    def _storage_changed(self, *_args) -> None:
        folder = self.folder_storage.isChecked()
        self.folder_edit.setEnabled(folder)
        self._update_actions()

    def _update_actions(self, *_args) -> None:
        ready = self._current_setup() is not None and self._current_storage() is not None
        self.apply_button.setEnabled(ready)
        self.open_button.setEnabled(ready)

    def _save(self) -> bool:
        setup = self._current_setup()
        storage = self._current_storage()
        if setup is None:
            self.status.setText(
                "Choose a printer, print profile, and one material per extruder."
            )
            return False
        if storage is None:
            self.status.setText("Choose a folder or select Managed VibeCAD cache.")
            return False
        PrintPreferences.save_confirmed_setup(setup)
        PrintPreferences.save_handoff_storage(storage)
        self._remembered = setup
        destination = (
            storage.directory if storage.mode == "folder" else "the managed VibeCAD cache"
        )
        self.status.setText(f"Setup applied. New handoffs will be written to {destination}.")
        return True

    def _browse_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose a folder for 3MF handoffs",
            start,
        )
        if selected:
            self.folder_edit.setText(str(selected))
            self.folder_storage.setChecked(True)

    def _open_selected(self) -> None:
        if not self._save():
            return
        import FreeCADGui

        FreeCADGui.runCommand("VibeCADPrint_OpenInPrusaSlicer")

    def _save_selected(self) -> None:
        import FreeCADGui

        FreeCADGui.runCommand("VibeCADPrint_Save3MF")

    def _initial_setup(self) -> None:
        choice = PrintSetupDialog.choose_print_setup(
            parent=self,
            backend=self.backend,
            open_after_save=False,
            initial_installation=self.installation,
        )
        if choice.accepted:
            self.refresh()


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
    dock = QtWidgets.QDockWidget("3D Print", main)
    dock.setObjectName(DOCK_NAME)
    dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
    dock.setWidget(PrintPanelWidget(dock))
    main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.toggleViewAction().setVisible(True)
    dock.hide()
    return dock


def show_panel() -> None:
    dock = ensure_panel_registered()
    dock.show()
    dock.raise_()
    widget = dock.widget()
    if isinstance(widget, PrintPanelWidget):
        widget.refresh()


def hide_panel() -> None:
    dock = _find_dock()
    if dock is not None:
        dock.hide()
