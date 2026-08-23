# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guided, non-blocking PrusaSlicer profile and placement setup."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from PySide import QtCore, QtGui, QtWidgets

import PrintPreferences
import VibeCADPrint


DOWNLOAD_URL = "https://www.prusa3d.com/page/prusaslicer_424/"
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vibecad-prusa")


def _qt_exec(value: Any) -> int:
    execute = getattr(value, "exec", None) or getattr(value, "exec_", None)
    return int(execute())


def _configure_wrapped_label(label: Any) -> None:
    """Keep wrapped text from being vertically collapsed by parent layouts."""

    label.setWordWrap(True)
    policy = label.sizePolicy()
    policy.setVerticalPolicy(QtWidgets.QSizePolicy.Minimum)
    label.setSizePolicy(policy)


def run_with_progress(
    parent: Any,
    label: str,
    operation: Callable[[], Any],
) -> Any:
    """Run a bounded backend call while keeping the Qt event loop responsive."""

    future = _EXECUTOR.submit(operation)
    progress = QtWidgets.QProgressDialog(label, "Cancel", 0, 0, parent)
    progress.setWindowTitle("3D Print")
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(150)
    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer(progress)

    def poll() -> None:
        if future.done() or progress.wasCanceled():
            loop.quit()

    timer.timeout.connect(poll)
    timer.start(40)
    progress.show()
    _qt_exec(loop)
    timer.stop()
    progress.close()
    if progress.wasCanceled():
        future.cancel()
        raise VibeCADPrint.SlicerQueryError("PrusaSlicer profile check was canceled.")
    return future.result()


@dataclass(frozen=True)
class SetupChoice:
    accepted: bool
    installation: VibeCADPrint.SlicerInstallation | None = None
    setup: VibeCADPrint.PrintSetup | None = None


class VibeCADPrintPreferencesPage:
    """Persistent executable override; profile choices stay in Print Setup."""

    def __init__(self, parent=None) -> None:
        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADPrintPreferencesPage")
        self.form.setWindowTitle("3D Printing")
        layout = QtWidgets.QFormLayout(self.form)
        row = QtWidgets.QWidget(self.form)
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        self.executable = QtWidgets.QLineEdit(row)
        self.executable.setObjectName("VibeCADPrintExecutablePreference")
        self.executable.setPlaceholderText("Auto-detect PrusaSlicer")
        browse = QtWidgets.QPushButton("Locate", row)
        browse.clicked.connect(self._browse)
        row_layout.addWidget(self.executable, 1)
        row_layout.addWidget(browse)
        layout.addRow("PrusaSlicer executable", row)
        note = QtWidgets.QLabel(
            "Leave this empty to auto-detect native installations and the official "
            "Linux Flatpak. Printer, print, and material profiles are confirmed in "
            "the 3D Print ribbon's Print Setup dialog.",
            self.form,
        )
        note.setWordWrap(True)
        layout.addRow("Detection", note)
        self.loadSettings()

    def _browse(self) -> None:
        selected = _browse_for_prusaslicer(self.form, self.executable.text())
        if selected:
            self.executable.setText(selected)

    def saveSettings(self) -> None:
        PrintPreferences.set_executable_override(self.executable.text())

    def loadSettings(self) -> None:
        self.executable.setText(PrintPreferences.executable_override())


def _browse_for_prusaslicer(parent: Any, initial: str = "") -> str:
    start = initial or str(Path.home())
    if sys.platform == "darwin":
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            parent,
            "Locate PrusaSlicer.app",
            start,
        )
        if selected:
            return str(selected)
    selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        "Locate the PrusaSlicer application",
        start,
        "Executables (*);;All files (*)",
    )
    return str(selected or "")


class PrintSetupDialog(QtWidgets.QDialog):
    """Explicit profile selection backed by PrusaSlicer's installed presets."""

    def __init__(
        self,
        *,
        parent: Any,
        backend: VibeCADPrint.PrusaSlicerBackend,
        open_after_save: bool,
        initial_installation: VibeCADPrint.SlicerInstallation | None = None,
        initial_message: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VibeCADPrintSetupDialog")
        self.setWindowTitle("Print Setup")
        self.backend = backend
        self.open_after_save = open_after_save
        self.initial_installation = initial_installation
        self.result_installation: VibeCADPrint.SlicerInstallation | None = None
        self.result_setup: VibeCADPrint.PrintSetup | None = None
        self._remembered = PrintPreferences.load_confirmed_setup()
        self._printers: tuple[VibeCADPrint.PrinterProfile, ...] = ()
        self._catalog: VibeCADPrint.ProfileCatalog | None = None
        self._material_combos: list[Any] = []
        self._job_id = 0
        self._callbacks: dict[int, Callable[[Any], None]] = {}
        self._futures: set[Future[Any]] = set()

        class _AsyncBridge(QtCore.QObject):
            finished = QtCore.Signal(int, object, object)

        self._bridge = _AsyncBridge(self)
        self._bridge.finished.connect(self._async_finished)

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "VibeCAD reads profiles installed by PrusaSlicer. Nothing is selected "
            "or substituted on your behalf.",
            self,
        )
        _configure_wrapped_label(intro)
        layout.addWidget(intro)

        executable_group = QtWidgets.QGroupBox("PrusaSlicer", self)
        executable_layout = QtWidgets.QFormLayout(executable_group)
        executable_row = QtWidgets.QWidget(executable_group)
        executable_row_layout = QtWidgets.QHBoxLayout(executable_row)
        executable_row_layout.setContentsMargins(0, 0, 0, 0)
        self.executable = QtWidgets.QLineEdit(executable_row)
        self.executable.setText(PrintPreferences.executable_override())
        self.executable.setPlaceholderText("Auto-detect")
        locate = QtWidgets.QPushButton("Locate", executable_row)
        locate.clicked.connect(self._locate)
        auto_detect = QtWidgets.QPushButton("Auto-detect", executable_row)
        auto_detect.clicked.connect(self._auto_detect)
        executable_row_layout.addWidget(self.executable, 1)
        executable_row_layout.addWidget(locate)
        executable_row_layout.addWidget(auto_detect)
        executable_layout.addRow("Executable", executable_row)

        self.installation_combo = QtWidgets.QComboBox(executable_group)
        self.installation_combo.currentIndexChanged.connect(self._installation_changed)
        executable_layout.addRow("Detected installation", self.installation_combo)
        action_row = QtWidgets.QWidget(executable_group)
        action_layout = QtWidgets.QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.open_slicer_button = QtWidgets.QPushButton("Open PrusaSlicer", action_row)
        self.open_slicer_button.clicked.connect(self._open_prusaslicer)
        download = QtWidgets.QPushButton("Download", action_row)
        download.clicked.connect(self._download)
        retry = QtWidgets.QPushButton("Retry", action_row)
        retry.clicked.connect(self._detect)
        action_layout.addWidget(self.open_slicer_button)
        action_layout.addWidget(download)
        action_layout.addWidget(retry)
        action_layout.addStretch(1)
        executable_layout.addRow("Actions", action_row)
        layout.addWidget(executable_group)

        profiles_group = QtWidgets.QGroupBox("Installed profiles", self)
        profiles_layout = QtWidgets.QFormLayout(profiles_group)
        profiles_layout.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)
        self.printer_combo = QtWidgets.QComboBox(profiles_group)
        self.printer_combo.currentIndexChanged.connect(self._printer_changed)
        profiles_layout.addRow("Printer profile", self.printer_combo)
        self.bed_details = QtWidgets.QLabel("Choose a printer profile.", profiles_group)
        _configure_wrapped_label(self.bed_details)
        profiles_layout.addRow("Build volume", self.bed_details)
        self.print_combo = QtWidgets.QComboBox(profiles_group)
        self.print_combo.currentIndexChanged.connect(self._print_changed)
        profiles_layout.addRow("Print profile", self.print_combo)
        self.material_widget = QtWidgets.QWidget(profiles_group)
        self.material_layout = QtWidgets.QFormLayout(self.material_widget)
        self.material_layout.setContentsMargins(0, 0, 0, 0)
        profiles_layout.addRow("Materials", self.material_widget)
        material_note = QtWidgets.QLabel(
            "Choose one material profile for every extruder. Object-to-extruder "
            "assignment remains explicit in PrusaSlicer.",
            profiles_group,
        )
        _configure_wrapped_label(material_note)
        profiles_layout.addRow("Multi-extruder", material_note)
        layout.addWidget(profiles_group)

        placement_group = QtWidgets.QGroupBox("Placement", self)
        placement_layout = QtWidgets.QVBoxLayout(placement_group)
        self.auto_arrange = QtWidgets.QCheckBox("Auto-arrange", placement_group)
        self.auto_arrange.setToolTip(
            "Allow PrusaSlicer to arrange imported objects in XY. Turn this off "
            "to retain their exact CAD XY positions."
        )
        self.ensure_on_bed = QtWidgets.QCheckBox("Ensure on bed", placement_group)
        self.ensure_on_bed.setToolTip(
            "Allow PrusaSlicer to lift objects that extend below the build plate."
        )
        remembered = self._remembered
        self.auto_arrange.setChecked(
            remembered.auto_arrange if remembered is not None else True
        )
        self.ensure_on_bed.setChecked(
            remembered.ensure_on_bed if remembered is not None else True
        )
        self.auto_arrange.toggled.connect(self._update_summary)
        self.ensure_on_bed.toggled.connect(self._update_summary)
        placement_layout.addWidget(self.auto_arrange)
        placement_layout.addWidget(self.ensure_on_bed)
        layout.addWidget(placement_group)

        storage_group = QtWidgets.QGroupBox("3MF handoff location", self)
        storage_layout = QtWidgets.QVBoxLayout(storage_group)
        self.managed_storage = QtWidgets.QRadioButton(
            "Managed VibeCAD cache", storage_group
        )
        self.folder_storage = QtWidgets.QRadioButton("Choose a folder", storage_group)
        storage_layout.addWidget(self.managed_storage)
        storage_layout.addWidget(self.folder_storage)
        folder_row = QtWidgets.QWidget(storage_group)
        folder_layout = QtWidgets.QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QtWidgets.QLineEdit(folder_row)
        self.folder_edit.setPlaceholderText("Folder for persistent 3MF files")
        browse_folder = QtWidgets.QPushButton("Browse…", folder_row)
        browse_folder.clicked.connect(self._browse_handoff_folder)
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(browse_folder)
        storage_layout.addWidget(folder_row)
        storage_note = QtWidgets.QLabel(
            "Managed files are automatically limited to recent handoffs. Files "
            "in your chosen folder are never pruned by VibeCAD.",
            storage_group,
        )
        _configure_wrapped_label(storage_note)
        storage_layout.addWidget(storage_note)
        storage = PrintPreferences.load_handoff_storage()
        self.folder_edit.setText(storage.directory)
        self.folder_edit.textChanged.connect(self._update_actions)
        self.managed_storage.setChecked(storage.mode == "managed")
        self.folder_storage.setChecked(storage.mode == "folder")
        self.managed_storage.toggled.connect(self._storage_changed)
        self.folder_storage.toggled.connect(self._storage_changed)
        layout.addWidget(storage_group)

        self.summary = QtWidgets.QLabel(self)
        _configure_wrapped_label(self.summary)
        self.summary.setObjectName("VibeCADPrintSetupSummary")
        layout.addWidget(self.summary)
        self.status = QtWidgets.QLabel(initial_message, self)
        _configure_wrapped_label(self.status)
        self.status.setObjectName("VibeCADPrintSetupStatus")
        layout.addWidget(self.status)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel,
            self,
        )
        self.save_button = self.buttons.button(QtWidgets.QDialogButtonBox.Save)
        self.save_button.setText("Save & Open" if open_after_save else "Save Setup")
        self.save_button.clicked.connect(self._accept_setup)
        self.buttons.rejected.connect(self.reject)
        self.basic_button = self.buttons.addButton(
            "Open without profiles",
            QtWidgets.QDialogButtonBox.ActionRole,
        )
        self.basic_button.setVisible(open_after_save)
        self.basic_button.clicked.connect(self._accept_basic)
        layout.addWidget(self.buttons)

        self._clear_profiles()
        self._storage_changed()
        self._update_summary()
        self.resize(700, 590)
        self._ensure_layout_room()
        QtCore.QTimer.singleShot(0, self._detect)

    def _ensure_layout_room(self) -> None:
        """Grow to the current styled size hint; never compress wrapped rows."""

        current_layout = self.layout()
        if current_layout is not None:
            current_layout.activate()
        hint = self.sizeHint()
        self.resize(max(self.width(), hint.width()), max(self.height(), hint.height()))

    def _set_status(self, message: str) -> None:
        self.status.setText(message)
        QtCore.QTimer.singleShot(0, self._ensure_layout_room)

    def _start_async(
        self,
        label: str,
        operation: Callable[[], Any],
        callback: Callable[[Any], None],
    ) -> None:
        self._job_id += 1
        job = self._job_id
        self._callbacks = {job: callback}
        self._set_status(label)
        self._set_busy(True)
        future = _EXECUTOR.submit(operation)
        self._futures.add(future)

        def completed(value: Future[Any]) -> None:
            try:
                result, error = value.result(), None
            except Exception as exc:
                result, error = None, exc
            try:
                self._bridge.finished.emit(job, result, error)
            except RuntimeError:
                pass

        future.add_done_callback(completed)

    def _async_finished(self, job: int, result: Any, error: Any) -> None:
        if job != self._job_id:
            return
        callback = self._callbacks.pop(job, None)
        self._set_busy(False)
        if error is not None:
            self._set_status(str(error))
            self._update_actions()
            return
        if callback is not None:
            callback(result)

    def _set_busy(self, busy: bool) -> None:
        self.installation_combo.setEnabled(not busy)
        self.printer_combo.setEnabled(not busy)
        self.print_combo.setEnabled(not busy)
        self.save_button.setEnabled(
            False
            if busy
            else self._selected_setup() is not None
            and self._selected_storage() is not None
        )

    def _selected_storage(self) -> PrintPreferences.HandoffStorage | None:
        directory = self.folder_edit.text().strip()
        if self.folder_storage.isChecked():
            if not directory:
                return None
            return PrintPreferences.HandoffStorage("folder", directory)
        return PrintPreferences.HandoffStorage("managed", directory)

    def _storage_changed(self, *_args) -> None:
        self.folder_edit.setEnabled(self.folder_storage.isChecked())
        self._update_actions()

    def _browse_handoff_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Choose a folder for 3MF handoffs",
            start,
        )
        if selected:
            self.folder_edit.setText(str(selected))
            self.folder_storage.setChecked(True)

    def _locate(self) -> None:
        selected = _browse_for_prusaslicer(self, self.executable.text())
        if selected:
            self.executable.setText(selected)
            self._detect()

    def _auto_detect(self) -> None:
        self.executable.clear()
        self._detect()

    def _detect(self) -> None:
        override = self.executable.text().strip()
        self._start_async(
            "Detecting PrusaSlicer installations...",
            lambda: self.backend.discover(override),
            self._installations_loaded,
        )

    def _installations_loaded(self, values: Any) -> None:
        installations = list(values or ())
        if self.initial_installation is not None and all(
            value.gui_command != self.initial_installation.gui_command
            for value in installations
        ):
            installations.append(self.initial_installation)
        preferred = VibeCADPrint.preferred_installation(installations)
        self.installation_combo.blockSignals(True)
        self.installation_combo.clear()
        for installation in installations:
            suffix = "" if installation.tested else " — update recommended"
            self.installation_combo.addItem(
                installation.display_name + suffix, installation
            )
        if preferred is not None:
            index = next(
                (
                    position
                    for position, installation in enumerate(installations)
                    if installation.gui_command == preferred.gui_command
                ),
                0,
            )
            self.installation_combo.setCurrentIndex(index)
        self.installation_combo.blockSignals(False)
        if preferred is None:
            self._set_status(
                "PrusaSlicer was not found. Use Locate or Download, then Retry."
            )
            self._clear_profiles()
            self._update_actions()
            return
        self._installation_changed(self.installation_combo.currentIndex())

    def _selected_installation(self) -> VibeCADPrint.SlicerInstallation | None:
        value = self.installation_combo.currentData()
        return value if isinstance(value, VibeCADPrint.SlicerInstallation) else None

    def _installation_changed(self, _index: int) -> None:
        installation = self._selected_installation()
        self._clear_profiles()
        if installation is None:
            self._set_status("Choose a detected PrusaSlicer installation.")
        elif not installation.tested:
            self._set_status(
                f"PrusaSlicer {installation.version} is older than the tested "
                "2.9.6 baseline. Basic 3MF handoff remains available, but VibeCAD "
                "will not pass profiles."
            )
        else:
            self._start_async(
                "Reading installed printer profiles...",
                lambda: self.backend.query_printers(installation),
                self._printers_loaded,
            )
        self._update_actions()

    def _clear_profiles(self) -> None:
        self._printers = ()
        self._catalog = None
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose a printer profile...", None)
        self.printer_combo.blockSignals(False)
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile...", None)
        self.print_combo.blockSignals(False)
        self._clear_materials()
        self.bed_details.setText("Choose a printer profile.")
        self._update_summary()

    def _printers_loaded(self, values: Any) -> None:
        self._printers = tuple(values or ())
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem("Choose a printer profile...", None)
        for printer in self._printers:
            label = printer.name + (" — user" if printer.is_user else "")
            self.printer_combo.addItem(label, printer)
        remembered = self._remembered
        selected_index = 0
        if remembered is not None:
            for index in range(1, self.printer_combo.count()):
                printer = self.printer_combo.itemData(index)
                if printer is not None and printer.name == remembered.printer_profile:
                    selected_index = index
                    break
        self.printer_combo.setCurrentIndex(selected_index)
        self.printer_combo.blockSignals(False)
        self._set_status(
            "Select the exact installed printer profile."
            if selected_index == 0
            else "Refreshing compatible print and material profiles..."
        )
        if selected_index:
            self._printer_changed(selected_index)
        self._update_actions()

    def _selected_printer(self) -> VibeCADPrint.PrinterProfile | None:
        value = self.printer_combo.currentData()
        return value if isinstance(value, VibeCADPrint.PrinterProfile) else None

    def _printer_changed(self, _index: int) -> None:
        printer = self._selected_printer()
        self._catalog = None
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile...", None)
        self.print_combo.blockSignals(False)
        self._clear_materials()
        if printer is None:
            self.bed_details.setText("Choose a printer profile.")
            self._set_status("Select the exact installed printer profile.")
            self._update_actions()
            return
        bed = printer.bed
        self.bed_details.setText(
            f"{bed.kind or 'Build plate'} — {bed.width:g} × {bed.height:g} × "
            f"{bed.max_print_height:g} mm; origin {bed.origin[0]:g}, "
            f"{bed.origin[1]:g}; {printer.extruders} extruder(s)"
        )
        installation = self._selected_installation()
        if installation is None:
            return
        self._start_async(
            f"Reading profiles compatible with {printer.name}...",
            lambda: self.backend.query_profiles(installation, printer.name),
            self._profiles_loaded,
        )

    def _profiles_loaded(self, catalog: Any) -> None:
        if not isinstance(catalog, VibeCADPrint.ProfileCatalog):
            self._set_status("PrusaSlicer returned an invalid profile catalog.")
            return
        printer = self._selected_printer()
        if printer is None or catalog.printer_profile != printer.name:
            self._set_status(
                "PrusaSlicer returned profiles for a different printer."
            )
            return
        self._catalog = catalog
        self.print_combo.blockSignals(True)
        self.print_combo.clear()
        self.print_combo.addItem("Choose a print profile...", None)
        for profile in catalog.print_profiles:
            label = profile.name + (" — user" if profile.is_user else "")
            self.print_combo.addItem(label, profile)
        selected_index = 0
        remembered = self._remembered
        if remembered is not None and remembered.printer_profile == printer.name:
            for index in range(1, self.print_combo.count()):
                profile = self.print_combo.itemData(index)
                if profile is not None and profile.name == remembered.print_profile:
                    selected_index = index
                    break
        self.print_combo.setCurrentIndex(selected_index)
        self.print_combo.blockSignals(False)
        self._set_status(
            "Select the exact print profile."
            if selected_index == 0
            else "Confirmed profile names were found; review the setup before saving."
        )
        if selected_index:
            self._print_changed(selected_index)
        self._update_actions()

    def _selected_print_profile(self) -> VibeCADPrint.PrintProfile | None:
        value = self.print_combo.currentData()
        return value if isinstance(value, VibeCADPrint.PrintProfile) else None

    def _clear_materials(self) -> None:
        self._material_combos.clear()
        while self.material_layout.count():
            item = self.material_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _print_changed(self, _index: int) -> None:
        self._clear_materials()
        printer = self._selected_printer()
        profile = self._selected_print_profile()
        if printer is None or profile is None:
            self._update_actions()
            return
        for extruder in range(printer.extruders):
            combo = QtWidgets.QComboBox(self.material_widget)
            combo.addItem("Choose a material profile...", None)
            for material in profile.materials:
                label = material.name + (" — user" if material.is_user else "")
                combo.addItem(label, material.name)
            combo.currentIndexChanged.connect(self._update_summary)
            remembered = self._remembered
            if (
                remembered is not None
                and remembered.printer_profile == printer.name
                and remembered.print_profile == profile.name
                and extruder < len(remembered.material_profiles)
            ):
                exact = remembered.material_profiles[extruder]
                index = combo.findData(exact)
                if index >= 0:
                    combo.setCurrentIndex(index)
            self.material_layout.addRow(f"Extruder {extruder + 1}", combo)
            self._material_combos.append(combo)
        self._update_actions()

    def _selected_setup(self) -> VibeCADPrint.PrintSetup | None:
        printer = self._selected_printer()
        print_profile = self._selected_print_profile()
        if printer is None or print_profile is None:
            return None
        materials = tuple(
            str(combo.currentData() or "") for combo in self._material_combos
        )
        if len(materials) != printer.extruders or any(not name for name in materials):
            return None
        setup = VibeCADPrint.PrintSetup(
            printer_profile=printer.name,
            print_profile=print_profile.name,
            material_profiles=materials,
            auto_arrange=self.auto_arrange.isChecked(),
            ensure_on_bed=self.ensure_on_bed.isChecked(),
        )
        if self._catalog is None or VibeCADPrint.validate_setup(
            setup, printer, self._catalog
        ):
            return None
        return setup

    def _update_actions(self) -> None:
        installation = self._selected_installation()
        self.open_slicer_button.setEnabled(installation is not None)
        self.basic_button.setEnabled(self.open_after_save and installation is not None)
        self.save_button.setEnabled(
            self._selected_setup() is not None
            and self._selected_storage() is not None
        )
        self._update_summary()

    def _update_summary(self, *_args) -> None:
        installation = self._selected_installation()
        printer = self._selected_printer()
        print_profile = self._selected_print_profile()
        storage = self._selected_storage()
        materials = [
            str(combo.currentData() or "Not selected")
            for combo in self._material_combos
        ]
        self.summary.setText(
            "Active setup: "
            f"{installation.display_name if installation else 'No slicer'} | "
            f"{printer.name if printer else 'No printer'} | "
            f"{print_profile.name if print_profile else 'No print profile'} | "
            f"materials: {', '.join(materials) if materials else 'not selected'} | "
            f"Auto-arrange: {'on' if self.auto_arrange.isChecked() else 'off'} | "
            f"Ensure on bed: {'on' if self.ensure_on_bed.isChecked() else 'off'} | "
            "3MF: "
            + (
                storage.directory
                if storage is not None and storage.mode == "folder"
                else "managed cache"
                if storage is not None
                else "choose a folder"
            )
        )
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(
                self._selected_setup() is not None
                and self._selected_storage() is not None
            )
            QtCore.QTimer.singleShot(0, self._ensure_layout_room)

    def _accept_setup(self) -> None:
        setup = self._selected_setup()
        storage = self._selected_storage()
        installation = self._selected_installation()
        if setup is None or storage is None or installation is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Incomplete Print Setup",
                "Explicitly select a printer, print profile, and one compatible "
                "material profile for every extruder, plus a 3MF handoff location.",
            )
            return
        PrintPreferences.set_executable_override(self.executable.text())
        PrintPreferences.save_confirmed_setup(setup)
        PrintPreferences.save_handoff_storage(storage)
        self.result_installation = installation
        self.result_setup = setup
        self.accept()

    def _accept_basic(self) -> None:
        installation = self._selected_installation()
        storage = self._selected_storage()
        if installation is None or storage is None:
            return
        PrintPreferences.set_executable_override(self.executable.text())
        PrintPreferences.save_handoff_storage(storage)
        self.result_installation = installation
        self.result_setup = None
        self.accept()

    def _open_prusaslicer(self) -> None:
        installation = self._selected_installation()
        if installation is None:
            return
        creationflags = 0
        if sys.platform == "win32":
            creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        try:
            subprocess.Popen(
                list(installation.gui_command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=(sys.platform != "win32"),
                creationflags=creationflags,
            )
            self._set_status(
                "PrusaSlicer opened. Complete its Configuration Wizard if needed, "
                "then return here and click Retry."
            )
        except OSError as exc:
            self._set_status(f"Could not open PrusaSlicer: {exc}")

    def _download(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(DOWNLOAD_URL))


def choose_print_setup(
    *,
    parent: Any,
    backend: VibeCADPrint.PrusaSlicerBackend,
    open_after_save: bool,
    initial_installation: VibeCADPrint.SlicerInstallation | None = None,
    initial_message: str = "",
) -> SetupChoice:
    dialog = PrintSetupDialog(
        parent=parent,
        backend=backend,
        open_after_save=open_after_save,
        initial_installation=initial_installation,
        initial_message=initial_message,
    )
    accepted = _qt_exec(dialog) == QtWidgets.QDialog.Accepted
    return SetupChoice(
        accepted=accepted,
        installation=dialog.result_installation if accepted else None,
        setup=dialog.result_setup if accepted else None,
    )
