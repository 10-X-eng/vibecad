# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native VibeCAD update notification and install-on-restart bridge."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADUpdate import (
    InstallPlan,
    PolicyLoadResult,
    UpdateCheckResult,
    UpdatePolicy,
    UpdateService,
    complete_pending_install_health,
    create_install_plan,
    current_release_identity,
    default_update_directory,
    load_update_policy,
    macos_install_helper_command,
    macos_install_helper_started_path,
    record_pending_install,
    spawn_detached_install_helper,
    wait_for_install_helper_start,
    write_macos_install_helper,
)


_PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/VibeCAD/Updates"
_COMMAND_NAME = "VibeCAD_CheckForUpdates"
_LEGACY_COMMAND_NAME = "VibeCAD_UpdateCenter"
_ICON = "view-refresh.svg"
_registered = False
_controller: "UpdateController | None" = None
_update_center: "UpdateCenterDialog | None" = None
_notification: Any | None = None


def _preferences():
    return App.ParamGet(_PREFERENCE_PATH)


def _user_policy_values() -> dict[str, object]:
    pref = _preferences()
    return {
        "enabled": pref.GetBool("Enabled", True),
        "automatic_checks": pref.GetBool("AutomaticChecks", True),
        "channel": pref.GetString("Channel", "auto") or "auto",
        "check_interval_hours": pref.GetInt("CheckIntervalHours", 24),
        "automatic_download": pref.GetBool("AutomaticDownload", False),
        "install_on_exit": pref.GetBool("InstallOnExit", False),
    }


def _policy_result() -> PolicyLoadResult:
    return load_update_policy(_user_policy_values())


def _write_user_policy(policy: UpdatePolicy) -> None:
    pref = _preferences()
    pref.SetBool("Enabled", policy.enabled)
    pref.SetBool("AutomaticChecks", policy.automatic_checks)
    pref.SetString("Channel", policy.channel)
    pref.SetInt("CheckIntervalHours", policy.check_interval_hours)
    pref.SetBool("AutomaticDownload", policy.automatic_download)
    pref.SetBool("InstallOnExit", policy.install_on_exit)


Signal = getattr(QtCore, "Signal", None) or getattr(QtCore, "pyqtSignal")


class UpdateController(QtCore.QObject):
    check_started = Signal()
    check_finished = Signal(object)
    download_started = Signal(object)
    download_progress = Signal(int, int)
    download_finished = Signal(object, object)
    operation_failed = Signal(str)
    install_staged = Signal(object)

    def __init__(self) -> None:
        super().__init__(Gui.getMainWindow())
        self._lock = threading.RLock()
        self._busy = False
        self._cancel_download = threading.Event()
        self.last_result: UpdateCheckResult | None = None
        self.downloaded_package: Path | None = None
        self.pending_plan: InstallPlan | None = None
        self._exit_connected = False
        self._helper_launched = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def _begin(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def _finish(self) -> None:
        with self._lock:
            self._busy = False

    def _service(self) -> tuple[UpdateService, PolicyLoadResult]:
        policy_result = _policy_result()
        return (
            UpdateService(current_release_identity(), policy_result.policy),
            policy_result,
        )

    def automatic_check(self) -> None:
        try:
            service, policy_result = self._service()
        except Exception as exc:
            App.Console.PrintWarning(f"VibeCAD update setup failed: {exc}\n")
            return
        if (
            policy_result.error
            or not service.policy.enabled
            or not service.policy.automatic_checks
            or not service.check_due()
        ):
            if policy_result.error:
                App.Console.PrintWarning(
                    f"VibeCAD managed update policy is invalid: {policy_result.error}\n"
                )
            return
        self.check(force=False, notify=True)

    def check(self, *, force: bool = True, notify: bool = False) -> None:
        if not self._begin():
            return
        self.check_started.emit()

        def run() -> None:
            try:
                service, policy_result = self._service()
                if policy_result.error:
                    result = UpdateCheckResult(
                        "error",
                        service.current,
                        message=f"Managed update policy error: {policy_result.error}",
                    )
                else:
                    result = service.check_for_updates(force=force)
                self.last_result = result
                self.downloaded_package = None
            except Exception as exc:
                self._finish()
                self.operation_failed.emit(str(exc))
                return
            self._finish()
            self.check_finished.emit(result)
            if notify and result.status == "available":
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "show_available_notification",
                    QtCore.Qt.QueuedConnection,
                )
            if result.status == "available" and service.policy.automatic_download:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "download",
                    QtCore.Qt.QueuedConnection,
                )

        threading.Thread(target=run, name="VibeCADUpdateCheck", daemon=True).start()

    @QtCore.Slot()
    def show_available_notification(self) -> None:
        result = self.last_result
        if result is not None and result.status == "available":
            _show_update_notification(result)

    @QtCore.Slot()
    def download(self) -> None:
        result = self.last_result
        if result is None or result.status != "available" or result.asset is None:
            self.operation_failed.emit("Check for an available update before downloading.")
            return
        if not self._begin():
            return
        self._cancel_download.clear()
        self.download_started.emit(result.asset)

        def run() -> None:
            try:
                service, _policy = self._service()
                package = service.download_asset(
                    result.asset,
                    progress=self.download_progress.emit,
                    cancelled=self._cancel_download.is_set,
                )
                self.downloaded_package = package
            except Exception as exc:
                self._finish()
                self.operation_failed.emit(str(exc))
                return
            self._finish()
            self.download_finished.emit(package, result.asset)
            if service.policy.install_on_exit:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "stage_install",
                    QtCore.Qt.QueuedConnection,
                )

        threading.Thread(target=run, name="VibeCADUpdateDownload", daemon=True).start()

    def cancel_download(self) -> None:
        self._cancel_download.set()

    @QtCore.Slot()
    def stage_install(self) -> None:
        result = self.last_result
        package = self.downloaded_package
        if (
            result is None
            or result.asset is None
            or package is None
            or not package.is_file()
        ):
            self.operation_failed.emit("Download and verify the update before installing.")
            return
        try:
            plan = create_install_plan(package, result.asset)
            if result.release is None:
                raise RuntimeError("The downloaded update has no release identity.")
            record_pending_install(
                plan,
                current_release_identity(),
                result.release.identity,
            )
            self.pending_plan = plan
            if not self._exit_connected:
                QtWidgets.QApplication.instance().aboutToQuit.connect(
                    self._launch_pending_install
                )
                self._exit_connected = True
            self.install_staged.emit(plan)
        except Exception as exc:
            self.operation_failed.emit(str(exc))

    def launch_pending_install_now(self) -> None:
        """Start the detached installer before the GUI begins tearing down."""

        self._launch_pending_install()

    @QtCore.Slot()
    def _launch_pending_install(self) -> None:
        if self._helper_launched:
            return
        plan = self.pending_plan
        if plan is None:
            return
        self._helper_launched = True
        try:
            if plan.kind == "windows-installer":
                _launch_windows_install_helper(plan)
            elif plan.kind == "appimage":
                _launch_appimage_install_helper(plan)
            elif plan.kind == "macos-dmg":
                _launch_macos_install_helper(plan)
            else:
                raise RuntimeError(f"Unsupported staged update plan: {plan.kind}")
        except Exception as exc:
            self._helper_launched = False
            App.Console.PrintError(f"VibeCAD could not launch the staged update: {exc}\n")


def _launch_windows_install_helper(plan: InstallPlan) -> None:
    helper_dir = default_update_directory() / "install-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / "install-windows-update.ps1"
    started = helper_dir / "install-helper.started"
    started.unlink(missing_ok=True)
    helper.write_text(
        """param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][int]$VibeCADProcessId,
    [Parameter(Mandatory=$true)][string]$Started
)
$ErrorActionPreference = 'Stop'
[IO.File]::WriteAllText($Started, "$PID")
$vibecad = Get-Process -Id $VibeCADProcessId -ErrorAction SilentlyContinue
if ($null -ne $vibecad) {
    $vibecad | Wait-Process
}
Start-Process -FilePath $Installer
""",
        encoding="utf-8",
        newline="\n",
    )
    spawn_detached_install_helper(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(plan.package),
            str(os.getpid()),
            str(started),
        ],
        log_path=helper_dir / "install-helper.log",
    )
    if not wait_for_install_helper_start(started):
        raise RuntimeError("The Windows install helper did not start.")


def _launch_appimage_install_helper(plan: InstallPlan) -> None:
    current = plan.current_appimage
    if current is None:
        raise RuntimeError("The staged AppImage plan has no current AppImage.")
    update_dir = default_update_directory()
    helper_dir = update_dir / "install-helper"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / "install-appimage-update.sh"
    receipt = update_dir / "install-receipt.json"
    current_identity = current_release_identity()
    backup = current.with_name(
        f"{current.name}.rollback-{current_identity.version}-build{current_identity.build}"
    )
    helper.write_text(
        """#!/bin/sh
set -eu
pid="$1"
current="$2"
package="$3"
backup="$4"
receipt="$5"
pending="$6"
new_file="${current}.vibecad-new"

while kill -0 "$pid" 2>/dev/null; do sleep 1; done
if [ -e "$backup" ] || [ -e "$new_file" ]; then
    exit 20
fi
cp "$package" "$new_file"
chmod +x "$new_file"
mv "$current" "$backup"
if ! mv "$new_file" "$current"; then
    mv "$backup" "$current"
    exit 21
fi
if ! "$current" freecadcmd --safe-mode -c "import VibeCADUpdate; print('VibeCAD update health check passed')"; then
    rm -f "$current"
    mv "$backup" "$current"
    exit 22
fi
printf '{"status":"installed","platform":"appimage"}\n' > "$receipt"
"$current" >/dev/null 2>&1 &
new_pid=$!
healthy=0
attempt=0
while [ "$attempt" -lt 120 ]; do
    if [ ! -f "$pending" ]; then
        healthy=1
        break
    fi
    if ! kill -0 "$new_pid" 2>/dev/null; then
        break
    fi
    sleep 1
    attempt=$((attempt + 1))
done
if [ "$healthy" -eq 1 ]; then
    exit 0
fi
kill "$new_pid" 2>/dev/null || true
wait "$new_pid" 2>/dev/null || true
rm -f "$current"
mv "$backup" "$current"
printf '{"status":"rolled-back","platform":"appimage"}\n' > "$receipt"
"$current" >/dev/null 2>&1 &
""",
        encoding="utf-8",
        newline="\n",
    )
    helper.chmod(0o700)
    spawn_detached_install_helper(
        [
            "/bin/sh",
            str(helper),
            str(os.getpid()),
            str(current),
            str(plan.package),
            str(backup),
            str(receipt),
            str(update_dir / "pending-install.json"),
        ],
        log_path=update_dir / "install-helper.log",
    )


def _launch_macos_install_helper(plan: InstallPlan) -> None:
    application = plan.current_install_root
    if application is None:
        raise RuntimeError("The staged macOS plan has no application bundle.")
    update_dir = default_update_directory()
    helper_dir = update_dir / "install-helper"
    helper = write_macos_install_helper(helper_dir / "install-macos-update.sh")
    started = macos_install_helper_started_path(update_dir)
    started.unlink(missing_ok=True)
    spawn_detached_install_helper(
        macos_install_helper_command(
            helper,
            plan,
            process_id=os.getpid(),
            update_directory=update_dir,
        ),
        log_path=update_dir / "install-helper.log",
    )
    if not wait_for_install_helper_start(started):
        raise RuntimeError("The macOS install helper did not start.")


class CheckForUpdatesCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": _ICON,
            "MenuText": "Check for Updates",
            "ToolTip": "Check for a VibeCAD update",
        }

    def IsActive(self) -> bool:
        return True

    def Activated(self) -> None:
        show_check_for_updates()


class UpdateCenterCommand(CheckForUpdatesCommand):
    """Compatibility alias for callers using the original command class."""


class VibeCADUpdatePreferencesPage:
    def __init__(self, parent=None) -> None:
        self.form = QtWidgets.QWidget(parent)
        self.form.setObjectName("VibeCADUpdatePreferencesPage")
        self.form.setWindowTitle("Updates")
        layout = QtWidgets.QFormLayout(self.form)

        self.enabled = QtWidgets.QCheckBox("Allow VibeCAD updates", self.form)
        layout.addRow("Update service", self.enabled)
        self.automatic_checks = QtWidgets.QCheckBox(
            "Check automatically in the background", self.form
        )
        layout.addRow("Checks", self.automatic_checks)
        self.channel = QtWidgets.QComboBox(self.form)
        self.channel.addItem("Match installed release", "auto")
        self.channel.addItem("Stable", "stable")
        self.channel.addItem("Preview", "preview")
        layout.addRow("Channel", self.channel)
        self.interval = QtWidgets.QSpinBox(self.form)
        self.interval.setRange(1, 24 * 30)
        self.interval.setSuffix(" hours")
        layout.addRow("Check interval", self.interval)
        self.automatic_download = QtWidgets.QCheckBox(
            "Download trusted packages automatically", self.form
        )
        layout.addRow("Downloads", self.automatic_download)
        self.install_on_exit = QtWidgets.QCheckBox(
            "Stage verified packages for installation on exit", self.form
        )
        layout.addRow("Installation", self.install_on_exit)
        self.policy_source = QtWidgets.QLabel(self.form)
        self.policy_source.setWordWrap(True)
        layout.addRow("Policy", self.policy_source)
        self.loadSettings()

    def saveSettings(self) -> None:
        result = _policy_result()
        if result.policy.managed:
            return
        _write_user_policy(
            UpdatePolicy(
                enabled=self.enabled.isChecked(),
                automatic_checks=self.automatic_checks.isChecked(),
                channel=str(self.channel.currentData()),
                check_interval_hours=self.interval.value(),
                automatic_download=self.automatic_download.isChecked(),
                install_on_exit=self.install_on_exit.isChecked(),
            )
        )

    def loadSettings(self) -> None:
        result = _policy_result()
        policy = result.policy
        self.enabled.setChecked(policy.enabled)
        self.automatic_checks.setChecked(policy.automatic_checks)
        index = self.channel.findData(policy.channel)
        self.channel.setCurrentIndex(max(0, index))
        self.interval.setValue(policy.check_interval_hours)
        self.automatic_download.setChecked(policy.automatic_download)
        self.install_on_exit.setChecked(policy.install_on_exit)
        controls = (
            self.enabled,
            self.automatic_checks,
            self.channel,
            self.interval,
            self.automatic_download,
            self.install_on_exit,
        )
        for control in controls:
            control.setEnabled(not policy.managed)
        if result.error:
            self.policy_source.setText(f"Invalid managed policy ({result.source}): {result.error}")
        elif policy.managed:
            self.policy_source.setText(f"Managed by {result.source}")
        else:
            self.policy_source.setText("User preference")


class UpdateCenterDialog(QtWidgets.QDialog):
    """Compatibility-named dialog implementing the simple update flow."""

    def __init__(self, controller: UpdateController, parent=None) -> None:
        super().__init__(parent or Gui.getMainWindow())
        self.controller = controller
        self.setWindowTitle("VibeCAD Updates")
        self.setMinimumWidth(560)
        layout = QtWidgets.QVBoxLayout(self)
        current = current_release_identity()
        self.current_label = QtWidgets.QLabel(f"Installed: VibeCAD {current.display}", self)
        layout.addWidget(self.current_label)
        policy_result = _policy_result()
        channel = policy_result.policy.resolved_channel(current)
        policy_text = f"Channel: {channel}"
        if policy_result.policy.managed:
            policy_text += f" · managed by {policy_result.source}"
        self.policy_label = QtWidgets.QLabel(policy_text, self)
        self.policy_label.setWordWrap(True)
        layout.addWidget(self.policy_label)
        self.status = QtWidgets.QLabel("Ready to check for a trusted update.", self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar(self)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("Check now", self)
        self.download_button = QtWidgets.QPushButton("Download", self)
        self.install_button = QtWidgets.QPushButton("Install and restart", self)
        self.close_button = QtWidgets.QPushButton("Close", self)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.install_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.check_button.clicked.connect(lambda: controller.check(force=True))
        self.download_button.clicked.connect(controller.download)
        self.install_button.clicked.connect(self._install_and_restart)
        self.close_button.clicked.connect(self.close)
        controller.check_started.connect(self._check_started)
        controller.check_finished.connect(self._check_finished)
        controller.download_started.connect(self._download_started)
        controller.download_progress.connect(self._download_progress)
        controller.download_finished.connect(self._download_finished)
        controller.operation_failed.connect(self._operation_failed)
        controller.install_staged.connect(self._install_staged)
        self._refresh_buttons()
        if controller.last_result is not None:
            self._check_finished(controller.last_result)

    def _refresh_buttons(self) -> None:
        result = self.controller.last_result
        available = bool(result is not None and result.status == "available")
        downloaded = bool(
            self.controller.downloaded_package is not None
            and self.controller.downloaded_package.is_file()
        )
        self.check_button.setEnabled(not self.controller.busy)
        self.download_button.setEnabled(available and not downloaded and not self.controller.busy)
        self.install_button.setEnabled(downloaded and not self.controller.busy)

    @QtCore.Slot()
    def _check_started(self) -> None:
        self.status.setText("Checking for updates…")
        self.progress.setVisible(False)
        self._refresh_buttons()

    @QtCore.Slot(object)
    def _check_finished(self, result: UpdateCheckResult) -> None:
        self.status.setText(result.message)
        self._refresh_buttons()

    @QtCore.Slot(object)
    def _download_started(self, asset: Any) -> None:
        self.status.setText(f"Downloading and verifying {asset.name}…")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._refresh_buttons()

    @QtCore.Slot(int, int)
    def _download_progress(self, downloaded: int, total: int) -> None:
        self.progress.setValue(int(downloaded * 1000 / max(1, total)))

    @QtCore.Slot(object, object)
    def _download_finished(self, package: Path, _asset: Any) -> None:
        self.progress.setValue(1000)
        self.status.setText(f"Verified update ready: {package.name}")
        self._refresh_buttons()

    @QtCore.Slot(str)
    def _operation_failed(self, message: str) -> None:
        self.status.setText(message)
        self.progress.setVisible(False)
        self._refresh_buttons()

    @QtCore.Slot(object)
    def _install_staged(self, _plan: InstallPlan) -> None:
        self.status.setText(
            "VibeCAD will close, then apply the verified update and reopen."
        )
        self._refresh_buttons()

    def _install_and_restart(self) -> None:
        self.controller.stage_install()
        if self.controller.pending_plan is None:
            _warn_install_failure(self, self.status.text())
            return
        try:
            self.hide()
            _prepare_to_quit_for_update()
        except Exception as exc:
            self.show()
            self.status.setText(str(exc))
            _warn_install_failure(self, str(exc))
            return
        self.controller.launch_pending_install_now()
        if not self.controller._helper_launched:
            self.show()
            message = "VibeCAD could not start the install helper. See the Report view."
            self.status.setText(message)
            _warn_install_failure(self, message)
            return
        _exit_process_for_update()


def _show_update_notification(result: UpdateCheckResult) -> None:
    global _notification
    if _notification is not None:
        try:
            _notification.raise_()
            return
        except RuntimeError:
            _notification = None
    dialog = QtWidgets.QDialog(Gui.getMainWindow())
    dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    dialog.setWindowTitle("VibeCAD update available")
    layout = QtWidgets.QVBoxLayout(dialog)
    label = QtWidgets.QLabel(
        f"VibeCAD {result.release.display if result.release else ''} is available.", dialog
    )
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QtWidgets.QDialogButtonBox(dialog)
    open_button = buttons.addButton("Download update", QtWidgets.QDialogButtonBox.AcceptRole)
    later_button = buttons.addButton("Later", QtWidgets.QDialogButtonBox.RejectRole)
    open_button.clicked.connect(lambda: _download_available_update(dialog))
    later_button.clicked.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.destroyed.connect(lambda: _clear_notification(dialog))
    _notification = dialog
    dialog.show()


def _clear_notification(dialog: Any) -> None:
    global _notification
    if _notification is dialog:
        _notification = None


def _download_available_update(notification: Any) -> None:
    notification.accept()
    show_check_for_updates(check_now=False)
    get_update_controller().download()


def show_check_for_updates(*, check_now: bool = True) -> None:
    global _update_center
    controller = get_update_controller()
    if _update_center is None:
        _update_center = UpdateCenterDialog(controller)
        _update_center.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        _update_center.destroyed.connect(_clear_update_center)
    _update_center.show()
    _update_center.raise_()
    _update_center.activateWindow()
    if check_now and not controller.busy:
        controller.check(force=True)


def show_update_center() -> None:
    """Open the update dialog for compatibility with existing callers."""

    show_check_for_updates()


def _clear_update_center(*_args: Any) -> None:
    global _update_center
    _update_center = None


def _warn_install_failure(parent: Any, message: str) -> None:
    text = (message or "").strip() or "The update could not be installed."
    QtWidgets.QMessageBox.warning(parent, "Install update", text)


def _prepare_to_quit_for_update() -> None:
    """Save or discard open documents before the process is replaced."""

    main_window = Gui.getMainWindow()
    if main_window is None:
        return
    closer = getattr(main_window, "closeAllDocuments", None)
    if callable(closer) and not closer(False):
        raise RuntimeError(
            "Save or discard open documents, then choose Install and restart."
        )


def _exit_process_for_update() -> None:
    """Hard-exit so the detached helper can replace this application bundle."""

    os._exit(0)


def _quit_application_for_update() -> None:
    """Compatibility wrapper used by tests and older callers."""

    _prepare_to_quit_for_update()
    _exit_process_for_update()


def get_update_controller() -> UpdateController:
    global _controller
    if _controller is None:
        _controller = UpdateController()
    return _controller


def _find_help_menu(main_window: Any) -> Any | None:
    menu_bar = main_window.menuBar()
    candidates = list(menu_bar.findChildren(QtWidgets.QMenu))
    candidates.extend(main_window.findChildren(QtWidgets.QMenu))
    for menu_action in menu_bar.actions():
        try:
            menu = menu_action.menu()
        except RuntimeError:
            continue
        if menu is not None:
            candidates.append(menu)
    for menu in candidates:
        try:
            title = menu.title()
            menu.actions()
        except RuntimeError:
            continue
        if title.replace("&", "").strip().casefold() == "help":
            return menu
    return None


def _add_help_menu_action() -> None:
    main_window = Gui.getMainWindow()
    command = Gui.Command.get(_COMMAND_NAME)
    actions = command.ensureAction() if command is not None else []
    if not actions:
        return
    action = actions[0]
    action.setObjectName("VibeCADCheckForUpdatesAction")
    action.setProperty("VibeCADCheckForUpdates", True)
    action.setProperty("VibeCADUpdateCenter", True)
    menu = _find_help_menu(main_window)
    if menu is None:
        return
    try:
        if action not in menu.actions():
            menu.addSeparator()
            menu.addAction(action)
        if not menu.property("VibeCADCheckForUpdatesHooked"):
            menu.aboutToShow.connect(_add_help_menu_action)
            menu.setProperty("VibeCADCheckForUpdatesHooked", True)
    except RuntimeError:
        # Workbench activation can replace FreeCAD's menus between discovery
        # and insertion. A later scheduled/workbench callback will retry.
        return


def _schedule_help_menu_action(*_args: Any) -> None:
    QtCore.QTimer.singleShot(0, _add_help_menu_action)


def ensure_registered() -> None:
    global _registered
    if _registered:
        return
    Gui.addIconPath(str(Path(__file__).resolve().parent))
    Gui.addCommand(_COMMAND_NAME, CheckForUpdatesCommand())
    Gui.addCommand(_LEGACY_COMMAND_NAME, UpdateCenterCommand())
    Gui.addPreferencePage(VibeCADUpdatePreferencesPage, "VibeCAD")
    main_window = Gui.getMainWindow()
    if not main_window.property("VibeCADUpdateMenuHooked"):
        main_window.workbenchActivated.connect(_schedule_help_menu_action)
        main_window.setProperty("VibeCADUpdateMenuHooked", True)
    for delay in (0, 250, 1000, 5000):
        QtCore.QTimer.singleShot(delay, _add_help_menu_action)
    QtCore.QTimer.singleShot(15000, get_update_controller().automatic_check)
    QtCore.QTimer.singleShot(0, _complete_startup_health_check)
    QtCore.QTimer.singleShot(2000, _complete_startup_health_check)
    _registered = True


def _complete_startup_health_check() -> None:
    try:
        status = complete_pending_install_health(current_release_identity())
        if status in {"healthy", "rolled-back"}:
            App.Console.PrintMessage(f"VibeCAD update health status: {status}\n")
    except Exception as exc:
        App.Console.PrintWarning(f"VibeCAD update health receipt failed: {exc}\n")
