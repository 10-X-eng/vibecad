# SPDX-License-Identifier: LGPL-2.1-or-later

"""OrcaSlicer discovery and exact-profile project handoff."""

from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping

import BambuStudio
import VibeCADPrint


ORCA_APP_ID = "com.orcaslicer.OrcaSlicer"
TESTED_ORCA_VERSION = (2, 4, 2)
ORCA_CAPABILITIES = BambuStudio.BAMBU_CAPABILITIES
_ORCA_VERSION_RE = re.compile(
    r"OrcaSlicer[- ]0*(\d+)\.0*(\d+)\.0*(\d+)(?:\.0*(\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Candidate:
    gui_command: tuple[str, ...]
    cli_command: tuple[str, ...]
    source: str
    display_name: str = "OrcaSlicer"
    config_dir: str = ""
    resource_dir: str = ""


def _default_config_dir(
    platform: str,
    environ: Mapping[str, str],
    *,
    flatpak: bool = False,
) -> str:
    home = environ.get("HOME", "")
    if flatpak:
        return (
            str(Path(home) / ".var/app" / ORCA_APP_ID / "config/OrcaSlicer")
            if home
            else ""
        )
    if platform == "win32":
        base = environ.get("APPDATA", "")
        return ntpath.join(base, "OrcaSlicer") if base else ""
    if platform == "darwin":
        return (
            str(Path(home) / "Library/Application Support/OrcaSlicer")
            if home
            else ""
        )
    base = environ.get("XDG_CONFIG_HOME", "")
    return (
        str(Path(base or (Path(home) / ".config")) / "OrcaSlicer")
        if base or home
        else ""
    )


def _native_resource_dir(executable: str, platform: str) -> str:
    path = Path(executable)
    if platform == "darwin":
        candidates = [path.parent.parent / "Resources/profiles"]
    elif platform == "win32":
        candidates = [path.parent / "resources/profiles", path.parent / "profiles"]
    else:
        candidates = [
            path.parent.parent / "share/OrcaSlicer/profiles",
            path.parent / "resources/profiles",
        ]
    return str(next((candidate for candidate in candidates if candidate.is_dir()), ""))


def _candidate_specs(
    explicit_executable: str,
    *,
    platform: str,
    environ: Mapping[str, str],
) -> tuple[_Candidate, ...]:
    values: list[_Candidate] = []
    explicit = str(explicit_executable or "").strip()
    if explicit:
        if platform == "darwin" and explicit.endswith(".app"):
            explicit = str(Path(explicit) / "Contents/MacOS/OrcaSlicer")
        values.append(
            _Candidate(
                (explicit,),
                (explicit,),
                "explicit",
                config_dir=_default_config_dir(platform, environ),
            )
        )
    if platform == "win32":
        standard: list[str] = []
        program_files = environ.get("ProgramFiles", r"C:\Program Files")
        if program_files:
            standard.extend(
                ntpath.join(program_files, "OrcaSlicer", name)
                for name in ("OrcaSlicer.exe", "orca-slicer.exe")
            )
        local_app_data = environ.get("LOCALAPPDATA", "")
        if local_app_data:
            standard.extend(
                ntpath.join(local_app_data, "Programs", "OrcaSlicer", name)
                for name in ("OrcaSlicer.exe", "orca-slicer.exe")
            )
        for executable in standard:
            values.append(
                _Candidate(
                    (executable,),
                    (executable,),
                    "standard",
                    config_dir=_default_config_dir(platform, environ),
                )
            )
        for executable in ("OrcaSlicer.exe", "orca-slicer.exe"):
            values.append(
                _Candidate(
                    (executable,),
                    (executable,),
                    "path",
                    config_dir=_default_config_dir(platform, environ),
                )
            )
    elif platform == "darwin":
        executable = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"
        values.extend(
            (
                _Candidate(
                    (executable,),
                    (executable,),
                    "standard",
                    config_dir=_default_config_dir(platform, environ),
                ),
                _Candidate(
                    ("orca-slicer",),
                    ("orca-slicer",),
                    "path",
                    config_dir=_default_config_dir(platform, environ),
                ),
            )
        )
    else:
        values.extend(
            (
                _Candidate(
                    ("orca-slicer",),
                    ("orca-slicer",),
                    "path",
                    config_dir=_default_config_dir(platform, environ),
                ),
                _Candidate(
                    ("flatpak", "--user", "run", ORCA_APP_ID),
                    (
                        "flatpak",
                        "--user",
                        "run",
                        "--command=/app/bin/orca-slicer",
                        ORCA_APP_ID,
                    ),
                    "flatpak-user",
                    "OrcaSlicer (Flatpak)",
                    _default_config_dir(platform, environ, flatpak=True),
                ),
                _Candidate(
                    ("flatpak", "--system", "run", ORCA_APP_ID),
                    (
                        "flatpak",
                        "--system",
                        "run",
                        "--command=/app/bin/orca-slicer",
                        ORCA_APP_ID,
                    ),
                    "flatpak-system",
                    "OrcaSlicer (Flatpak)",
                    _default_config_dir(platform, environ, flatpak=True),
                ),
            )
        )
    return tuple(values)


def _flatpak_resource_dir(
    source: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    if not source.startswith("flatpak-"):
        return ""
    scope = source.removeprefix("flatpak-")
    try:
        completed = runner(
            ["flatpak", "info", f"--{scope}", "--show-location", ORCA_APP_ID],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    location = Path(str(completed.stdout or "").strip())
    root = location / "files/share/OrcaSlicer/profiles"
    return str(root) if root.is_dir() else ""


def discover_orca_installations(
    explicit_executable: str = "",
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    windows_version_reader: Callable[
        [str, str], str
    ] = BambuStudio.windows_installed_version,
) -> tuple[VibeCADPrint.SlicerInstallation, ...]:
    """Discover current native and Flatpak OrcaSlicer installations."""

    platform = platform or sys.platform
    env = dict(os.environ if environ is None else environ)
    installations: list[VibeCADPrint.SlicerInstallation] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in _candidate_specs(
        explicit_executable,
        platform=platform,
        environ=env,
    ):
        gui = BambuStudio._resolve_command(candidate.gui_command, which)
        cli = BambuStudio._resolve_command(candidate.cli_command, which)
        if gui is None or cli is None or gui in seen:
            continue
        try:
            with tempfile.TemporaryDirectory(
                prefix="vibecad-slicer-probe-"
            ) as working_directory:
                completed = runner(
                    [*cli, "--help"],
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    check=False,
                    cwd=working_directory,
                )
        except (OSError, subprocess.SubprocessError):
            continue
        output = "\n".join(
            str(value or "") for value in (completed.stdout, completed.stderr)
        )
        match = _ORCA_VERSION_RE.search(output)
        if match is not None:
            pieces = [str(int(value or 0)) for value in match.groups()]
            version = ".".join(
                pieces[:3] + ([pieces[3]] if match.group(4) else [])
            )
        elif platform == "win32" and completed.returncode == 0:
            version = BambuStudio._normalized_version(
                windows_version_reader(gui[0], candidate.display_name)
            )
        else:
            version = ""
        if not version:
            continue
        resource_dir = candidate.resource_dir or _flatpak_resource_dir(
            candidate.source,
            runner=runner,
        )
        if not resource_dir:
            resource_dir = _native_resource_dir(gui[0], platform)
        seen.add(gui)
        installations.append(
            VibeCADPrint.SlicerInstallation(
                backend_id="orcaslicer",
                version=version,
                gui_command=tuple(gui),
                cli_command=tuple(cli),
                source=candidate.source,
                display_name=f"{candidate.display_name} {version}",
                config_dir=candidate.config_dir,
                capabilities=ORCA_CAPABILITIES,
                resource_dir=resource_dir,
                tested_version=TESTED_ORCA_VERSION,
            )
        )
    return tuple(installations)


def _orca_error(exc: VibeCADPrint.SlicerError) -> VibeCADPrint.SlicerError:
    message = str(exc).replace("Bambu Studio", "OrcaSlicer")
    return type(exc)(message)


def query_printer_profiles(
    installation: VibeCADPrint.SlicerInstallation,
) -> tuple[VibeCADPrint.PrinterProfile, ...]:
    try:
        return BambuStudio.query_printer_profiles(installation)
    except VibeCADPrint.SlicerError as exc:
        raise _orca_error(exc) from exc


def query_compatible_profiles(
    installation: VibeCADPrint.SlicerInstallation,
    printer_name: str,
) -> VibeCADPrint.ProfileCatalog:
    try:
        return BambuStudio.query_compatible_profiles(installation, printer_name)
    except VibeCADPrint.SlicerError as exc:
        raise _orca_error(exc) from exc


def build_prepare_project_command(
    installation: VibeCADPrint.SlicerInstallation,
    source_file: str | os.PathLike[str],
    output_file: str | os.PathLike[str],
    setup: VibeCADPrint.PrintSetup,
    machine_profile: str | os.PathLike[str],
    process_profile: str | os.PathLike[str],
    material_profiles: Iterable[str | os.PathLike[str]],
) -> tuple[str, ...]:
    return BambuStudio.build_prepare_project_command(
        installation,
        source_file,
        output_file,
        setup,
        machine_profile,
        process_profile,
        material_profiles,
    )


def prepare_orca_project(
    installation: VibeCADPrint.SlicerInstallation,
    source_file: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    setup: VibeCADPrint.PrintSetup,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 120.0,
) -> Path:
    try:
        return BambuStudio.prepare_bambu_project(
            installation,
            source_file,
            destination,
            setup,
            runner=runner,
            timeout=timeout,
        )
    except VibeCADPrint.SlicerError as exc:
        raise _orca_error(exc) from exc


def launch_orca_slicer(
    installation: VibeCADPrint.SlicerInstallation,
    handoff_file: str | os.PathLike[str],
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    platform: str | None = None,
) -> VibeCADPrint.LaunchResult:
    return VibeCADPrint.launch_slicer_gui(
        installation,
        handoff_file,
        slicer_name="OrcaSlicer",
        popen=popen,
        platform=platform,
    )


class OrcaSlicerBackend(BambuStudio.BambuStudioBackend):
    """Backend adapter consumed by the shared VibeCAD print workflow."""

    backend_id = "orcaslicer"
    display_name = "OrcaSlicer"
    capabilities = ORCA_CAPABILITIES

    def discover(
        self, explicit_executable: str = ""
    ) -> tuple[VibeCADPrint.SlicerInstallation, ...]:
        key = str(explicit_executable or "").strip()
        value = self._installation_cache.get(key)
        if value is None:
            value = discover_orca_installations(key)
            self._installation_cache[key] = value
        return value

    def query_printers(
        self, installation: VibeCADPrint.SlicerInstallation
    ) -> tuple[VibeCADPrint.PrinterProfile, ...]:
        try:
            return super().query_printers(installation)
        except VibeCADPrint.SlicerError as exc:
            raise _orca_error(exc) from exc

    def query_profiles(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        printer_profile: str,
    ) -> VibeCADPrint.ProfileCatalog:
        try:
            return super().query_profiles(installation, printer_profile)
        except VibeCADPrint.SlicerError as exc:
            raise _orca_error(exc) from exc

    def prepare_project(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        source_file: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        setup: VibeCADPrint.PrintSetup,
    ) -> Path:
        return prepare_orca_project(
            installation,
            source_file,
            destination,
            setup,
        )

    def launch(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        handoff_file: str | os.PathLike[str],
        _setup: VibeCADPrint.PrintSetup | None,
    ) -> VibeCADPrint.LaunchResult:
        return launch_orca_slicer(installation, handoff_file)
