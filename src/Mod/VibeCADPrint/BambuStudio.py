# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bambu Studio discovery, exact profile resolution, project prep, and launch."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4
import xml.etree.ElementTree as ET
import zipfile

import VibeCADPrint


BAMBU_APP_ID = "com.bambulab.BambuStudio"
TESTED_BAMBU_VERSION = (2, 8, 2)
BAMBU_CAPABILITIES = (
    "gui_handoff",
    "profile_store",
    "project_export",
    "auto_arrange",
    "ensure_on_bed",
    "object_filament_assignment",
)

_BAMBU_VERSION_RE = re.compile(
    r"BambuStudio[- ]0*(\d+)\.0*(\d+)\.0*(\d+)(?:\.0*(\d+))?",
    re.IGNORECASE,
)
_POINT_RE = re.compile(r"^\s*(-?(?:\d+(?:\.\d*)?|\.\d+))x"
                       r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$")
_PROFILE_TYPES = {"machine", "process", "filament"}
_PLAIN_VERSION_RE = re.compile(
    r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?"
)


@dataclass(frozen=True)
class _Candidate:
    gui_command: tuple[str, ...]
    cli_command: tuple[str, ...]
    source: str
    display_name: str = "Bambu Studio"
    config_dir: str = ""
    resource_dir: str = ""


@dataclass(frozen=True)
class _ProfileRecord:
    profile_type: str
    name: str
    vendor: str
    path: Path
    data: Mapping[str, Any]
    is_user: bool


@dataclass(frozen=True)
class _ProfileStore:
    records: tuple[_ProfileRecord, ...]
    _by_type: Mapping[str, tuple[_ProfileRecord, ...]] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _by_type_name: Mapping[tuple[str, str], tuple[_ProfileRecord, ...]] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        by_type: dict[str, list[_ProfileRecord]] = {}
        by_type_name: dict[tuple[str, str], list[_ProfileRecord]] = {}
        for record in self.records:
            by_type.setdefault(record.profile_type, []).append(record)
            by_type_name.setdefault((record.profile_type, record.name), []).append(
                record
            )
        object.__setattr__(
            self,
            "_by_type",
            {key: tuple(values) for key, values in by_type.items()},
        )
        object.__setattr__(
            self,
            "_by_type_name",
            {key: tuple(values) for key, values in by_type_name.items()},
        )

    def records_for(self, profile_type: str, name: str = "") -> tuple[_ProfileRecord, ...]:
        if name:
            return self._by_type_name.get((profile_type, name), ())
        return self._by_type.get(profile_type, ())


def _normalized_version(value: str) -> str:
    match = _PLAIN_VERSION_RE.search(str(value or ""))
    if match is None:
        return ""
    parts = [str(int(part or 0)) for part in match.groups()]
    return ".".join(parts[:3] + ([parts[3]] if match.group(4) else []))


def _windows_file_version(executable: str) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        api = ctypes.windll.version
        ignored = wintypes.DWORD()
        size = api.GetFileVersionInfoSizeW(str(executable), ctypes.byref(ignored))
        if not size:
            return ""
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(executable), 0, size, buffer):
            return ""
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not api.VerQueryValueW(
            buffer,
            "\\",
            ctypes.byref(pointer),
            ctypes.byref(length),
        ):
            return ""
        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        if info.dwSignature != 0xFEEF04BD:
            return ""
        return ".".join(
            str(part)
            for part in (
                info.dwFileVersionMS >> 16,
                info.dwFileVersionMS & 0xFFFF,
                info.dwFileVersionLS >> 16,
                info.dwFileVersionLS & 0xFFFF,
            )
        )
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return ""


def _windows_registry_versions(product_name: str) -> tuple[str, ...]:
    try:
        import winreg
    except ImportError:
        return ()

    expected = str(product_name or "").strip().casefold()
    if not expected:
        return ()
    versions: list[str] = []
    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                key = winreg.OpenKey(root, uninstall, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, name) as product:
                            display_name = str(
                                winreg.QueryValueEx(product, "DisplayName")[0]
                            ).strip()
                            if display_name.casefold() != expected:
                                continue
                            versions.append(
                                str(
                                    winreg.QueryValueEx(product, "DisplayVersion")[0]
                                ).strip()
                            )
                    except OSError:
                        continue
    return tuple(versions)


def windows_installed_version(executable: str, product_name: str) -> str:
    """Read a Windows GUI slicer's version without relying on console output."""

    candidates = (
        _windows_file_version(executable),
        *_windows_registry_versions(product_name),
    )
    normalized = tuple(
        value
        for value in (_normalized_version(candidate) for candidate in candidates)
        if VibeCADPrint.version_key(value) != (0, 0, 0)
    )
    return max(normalized, key=VibeCADPrint.version_key, default="")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _references(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value if str(item or "").strip())
    return ()


def _profile_roots(installation: VibeCADPrint.SlicerInstallation) -> tuple[tuple[Path, bool], ...]:
    roots: list[tuple[Path, bool]] = []
    if installation.resource_dir:
        resource = Path(installation.resource_dir)
        if resource.is_dir():
            roots.append((resource, False))
    if installation.config_dir:
        config = Path(installation.config_dir)
        if config.is_dir():
            roots.append((config, True))
    return tuple(roots)


def _load_profile_store(installation: VibeCADPrint.SlicerInstallation) -> _ProfileStore:
    records: list[_ProfileRecord] = []
    for root, is_user in _profile_roots(installation):
        for path in sorted(root.rglob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(value, Mapping):
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = Path(path.name)
            profile_type = str(value.get("type", "") or "").strip()
            if profile_type not in _PROFILE_TYPES:
                profile_type = next(
                    (
                        part
                        for part in reversed(relative.parts[:-1])
                        if part in _PROFILE_TYPES
                    ),
                    "",
                )
            name = str(value.get("name", "") or "").strip()
            if profile_type not in _PROFILE_TYPES or not name:
                continue
            vendor = relative.parts[0] if len(relative.parts) > 2 else "user"
            records.append(
                _ProfileRecord(
                    profile_type=profile_type,
                    name=name,
                    vendor=vendor,
                    path=path,
                    data=value,
                    is_user=is_user,
                )
            )
    if not records:
        raise VibeCADPrint.SlicerQueryError(
            "Bambu Studio's machine, process, and filament profile store was not found."
        )
    return _ProfileStore(tuple(records))


def _select_record(
    store: _ProfileStore,
    profile_type: str,
    name: str,
    *,
    vendor: str = "",
) -> _ProfileRecord:
    candidates = store.records_for(profile_type, name)
    if vendor:
        matching_vendor = tuple(record for record in candidates if record.vendor == vendor)
        if matching_vendor:
            candidates = matching_vendor
    if not candidates:
        raise VibeCADPrint.SlicerQueryError(
            f"Bambu Studio profile '{name}' ({profile_type}) is not available."
        )
    return max(candidates, key=lambda record: (record.is_user, str(record.path)))


def _resolve_record(
    store: _ProfileStore,
    record: _ProfileRecord,
    *,
    stack: tuple[tuple[str, str, str], ...] = (),
    cache: dict[tuple[str, str, str, str, bool], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    key = (record.profile_type, record.vendor, record.name)
    if key in stack:
        chain = " -> ".join(item[2] for item in (*stack, key))
        raise VibeCADPrint.SlicerQueryError(
            f"Bambu Studio profile inheritance contains a cycle: {chain}."
        )
    if cache is None:
        cache = {}
    cache_key = (*key, str(record.path), record.is_user)
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached)
    resolved: dict[str, Any] = {}
    for relation in ("inherits", "include"):
        for name in _references(record.data.get(relation)):
            parent = _select_record(
                store,
                record.profile_type,
                name,
                vendor=record.vendor,
            )
            resolved.update(
                _resolve_record(
                    store,
                    parent,
                    stack=(*stack, key),
                    cache=cache,
                )
            )
    resolved.update(record.data)
    resolved.pop("inherits", None)
    resolved.pop("include", None)
    cache[cache_key] = dict(resolved)
    return resolved


def resolved_profile(
    installation: VibeCADPrint.SlicerInstallation,
    profile_type: str,
    name: str,
) -> dict[str, Any]:
    """Return the engine profile with inheritance/includes fully materialized."""

    if profile_type not in _PROFILE_TYPES:
        raise ValueError(f"Unsupported Bambu Studio profile type: {profile_type}")
    store = _load_profile_store(installation)
    return _resolve_record(store, _select_record(store, profile_type, name))


def _instantiated_profiles(
    store: _ProfileStore,
    profile_type: str,
) -> tuple[tuple[_ProfileRecord, dict[str, Any]], ...]:
    selected: dict[str, _ProfileRecord] = {}
    for record in store.records_for(profile_type):
        # Bambu Studio omits the system-profile ``instantiation`` marker from
        # user-owned presets saved in its filament library. Those files are
        # selectable presets; compatibility is still enforced after resolving
        # their inherited data in ``_compatible_profile_catalog``.
        user_filament = (
            profile_type == "filament"
            and record.is_user
            and str(record.data.get("from", "")).strip().casefold() == "user"
            and "instantiation" not in record.data
        )
        if user_filament or _truthy(record.data.get("instantiation")):
            current = selected.get(record.name)
            if current is None or (record.is_user and not current.is_user):
                selected[record.name] = record
    cache: dict[tuple[str, str, str, str, bool], dict[str, Any]] = {}
    return tuple(
        (record, _resolve_record(store, record, cache=cache))
        for _name, record in sorted(selected.items(), key=lambda item: item[0].casefold())
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bed_info(profile: Mapping[str, Any]) -> VibeCADPrint.BedInfo:
    points: list[tuple[float, float]] = []
    raw_points = profile.get("printable_area", ())
    if isinstance(raw_points, Sequence) and not isinstance(raw_points, (str, bytes)):
        for raw in raw_points:
            match = _POINT_RE.match(str(raw or ""))
            if match:
                points.append((_float(match.group(1)), _float(match.group(2))))
    if not points:
        return VibeCADPrint.BedInfo(
            kind="Polygon",
            max_print_height=_float(profile.get("printable_height")),
        )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return VibeCADPrint.BedInfo(
        kind="Polygon",
        width=max(xs) - min(xs),
        height=max(ys) - min(ys),
        origin=(min(xs), min(ys)),
        max_print_height=_float(profile.get("printable_height")),
    )


def _printer_profiles_from_instantiated(
    profiles: tuple[tuple[_ProfileRecord, dict[str, Any]], ...],
) -> tuple[VibeCADPrint.PrinterProfile, ...]:
    printers: list[VibeCADPrint.PrinterProfile] = []
    for record, profile in profiles:
        diameters = profile.get("nozzle_diameter", ())
        extruders = (
            len(diameters)
            if isinstance(diameters, Sequence)
            and not isinstance(diameters, (str, bytes))
            else 1
        )
        printers.append(
            VibeCADPrint.PrinterProfile(
                name=record.name,
                model_id=str(profile.get("printer_model", "") or ""),
                model_name=str(profile.get("printer_model", "") or record.name),
                variant_name=str(profile.get("printer_variant", "") or ""),
                vendor_id=record.vendor,
                vendor_name=record.vendor,
                technology="FFF",
                extruders=max(1, extruders),
                bed=_bed_info(profile),
                is_user=record.is_user,
            )
        )
    return tuple(printers)


def query_printer_profiles(
    installation: VibeCADPrint.SlicerInstallation,
) -> tuple[VibeCADPrint.PrinterProfile, ...]:
    store = _load_profile_store(installation)
    return _printer_profiles_from_instantiated(
        _instantiated_profiles(store, "machine")
    )


def _is_compatible(profile: Mapping[str, Any], printer_name: str) -> bool:
    compatible = profile.get("compatible_printers", ())
    if not isinstance(compatible, Sequence) or isinstance(compatible, (str, bytes)):
        return False
    return printer_name in {str(value) for value in compatible}


def _compatible_profile_catalog(
    store: _ProfileStore,
    printer_name: str,
    materials_data: tuple[tuple[_ProfileRecord, dict[str, Any]], ...],
    processes_data: tuple[tuple[_ProfileRecord, dict[str, Any]], ...],
) -> VibeCADPrint.ProfileCatalog:
    printer = _select_record(store, "machine", printer_name)
    if not _truthy(printer.data.get("instantiation")):
        raise VibeCADPrint.SlicerQueryError(
            f"Bambu Studio printer profile '{printer_name}' is not selectable."
        )
    materials = tuple(
        VibeCADPrint.MaterialProfile(name=record.name, is_user=record.is_user)
        for record, profile in materials_data
        if _is_compatible(profile, printer_name)
    )
    profiles = tuple(
        VibeCADPrint.PrintProfile(
            name=record.name,
            materials=materials,
            is_user=record.is_user,
        )
        for record, profile in processes_data
        if _is_compatible(profile, printer_name)
    )
    return VibeCADPrint.ProfileCatalog(
        printer_profile=printer_name,
        print_profiles=profiles,
    )


def query_compatible_profiles(
    installation: VibeCADPrint.SlicerInstallation,
    printer_name: str,
) -> VibeCADPrint.ProfileCatalog:
    store = _load_profile_store(installation)
    return _compatible_profile_catalog(
        store,
        printer_name,
        _instantiated_profiles(store, "filament"),
        _instantiated_profiles(store, "process"),
    )


def _default_config_dir(
    platform: str,
    environ: Mapping[str, str],
    *,
    flatpak: bool = False,
) -> str:
    home = environ.get("HOME", "")
    if flatpak:
        return (
            str(Path(home) / ".var/app" / BAMBU_APP_ID / "config/BambuStudio")
            if home
            else ""
        )
    if platform == "win32":
        base = environ.get("APPDATA", "")
        return _platform_path(platform, base, "BambuStudio") if base else ""
    if platform == "darwin":
        return (
            str(Path(home) / "Library/Application Support/BambuStudio")
            if home
            else ""
        )
    base = environ.get("XDG_CONFIG_HOME", "")
    return str(Path(base or (Path(home) / ".config")) / "BambuStudio") if base or home else ""


def _native_resource_dir(executable: str, platform: str) -> str:
    path = Path(executable)
    candidates: list[Path]
    if platform == "darwin":
        candidates = [path.parent.parent / "Resources/profiles"]
    elif platform == "win32":
        candidates = [path.parent / "resources/profiles", path.parent / "profiles"]
    else:
        candidates = [
            path.parent.parent / "share/BambuStudio/profiles",
            path.parent / "resources/profiles",
        ]
    return str(next((candidate for candidate in candidates if candidate.is_dir()), ""))


def _platform_path(platform: str, root: str, *parts: str) -> str:
    """Join real Windows roots while keeping injected POSIX test roots usable."""

    if platform == "win32" and (
        bool(ntpath.splitdrive(root)[0]) or str(root).startswith(("\\\\", "//"))
    ):
        return ntpath.join(root, *parts)
    return str(Path(root).joinpath(*parts))


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
            explicit = str(Path(explicit) / "Contents/MacOS/BambuStudio")
        values.append(
            _Candidate(
                (explicit,),
                (explicit,),
                "explicit",
                config_dir=_default_config_dir(platform, environ),
            )
        )
    if platform == "win32":
        roots = [
            environ.get("ProgramFiles", r"C:\Program Files"),
            environ.get("LOCALAPPDATA", ""),
        ]
        for root in (value for value in roots if value):
            executable = _platform_path(
                platform,
                root,
                "Bambu Studio",
                "bambu-studio.exe",
            )
            values.append(
                _Candidate(
                    (executable,),
                    (executable,),
                    "standard",
                    config_dir=_default_config_dir(platform, environ),
                )
            )
        values.append(
            _Candidate(
                ("bambu-studio.exe",),
                ("bambu-studio.exe",),
                "path",
                config_dir=_default_config_dir(platform, environ),
            )
        )
    elif platform == "darwin":
        executable = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
        values.extend(
            (
                _Candidate(
                    (executable,),
                    (executable,),
                    "standard",
                    config_dir=_default_config_dir(platform, environ),
                ),
                _Candidate(
                    ("bambu-studio",),
                    ("bambu-studio",),
                    "path",
                    config_dir=_default_config_dir(platform, environ),
                ),
            )
        )
    else:
        values.extend(
            (
                _Candidate(
                    ("bambu-studio",),
                    ("bambu-studio",),
                    "path",
                    config_dir=_default_config_dir(platform, environ),
                ),
                _Candidate(
                    ("flatpak", "--user", "run", BAMBU_APP_ID),
                    (
                        "flatpak",
                        "--user",
                        "run",
                        "--command=/app/bin/bambu-studio",
                        BAMBU_APP_ID,
                    ),
                    "flatpak-user",
                    "Bambu Studio (Flatpak)",
                    _default_config_dir(platform, environ, flatpak=True),
                ),
                _Candidate(
                    ("flatpak", "--system", "run", BAMBU_APP_ID),
                    (
                        "flatpak",
                        "--system",
                        "run",
                        "--command=/app/bin/bambu-studio",
                        BAMBU_APP_ID,
                    ),
                    "flatpak-system",
                    "Bambu Studio (Flatpak)",
                    _default_config_dir(platform, environ, flatpak=True),
                ),
            )
        )
    return tuple(values)


def _resolve_command(
    command: tuple[str, ...], which: Callable[[str], str | None]
) -> tuple[str, ...] | None:
    if not command:
        return None
    program = command[0]
    if os.path.isabs(program) or (len(program) > 2 and program[1:3] == ":\\"):
        return command if Path(program).is_file() else None
    resolved = which(program)
    return (resolved, *command[1:]) if resolved else None


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
            ["flatpak", "info", f"--{scope}", "--show-location", BAMBU_APP_ID],
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
    root = location / "files/share/BambuStudio/profiles"
    return str(root) if root.is_dir() else ""


def discover_bambu_installations(
    explicit_executable: str = "",
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    windows_version_reader: Callable[[str, str], str] = windows_installed_version,
) -> tuple[VibeCADPrint.SlicerInstallation, ...]:
    """Discover current native and Flatpak Bambu Studio installations."""

    platform = platform or sys.platform
    env = dict(os.environ if environ is None else environ)
    installations: list[VibeCADPrint.SlicerInstallation] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in _candidate_specs(
        explicit_executable,
        platform=platform,
        environ=env,
    ):
        gui = _resolve_command(candidate.gui_command, which)
        cli = _resolve_command(candidate.cli_command, which)
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
                    **VibeCADPrint.background_subprocess_kwargs(platform=platform),
                )
        except (OSError, subprocess.SubprocessError):
            continue
        output = "\n".join(
            str(value or "") for value in (completed.stdout, completed.stderr)
        )
        match = _BAMBU_VERSION_RE.search(output)
        if match is not None:
            pieces = [str(int(value or 0)) for value in match.groups()]
            version = ".".join(
                pieces[:3] + ([pieces[3]] if match.group(4) else [])
            )
        elif platform == "win32" and completed.returncode == 0:
            version = _normalized_version(
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
                backend_id="bambustudio",
                version=version,
                gui_command=tuple(gui),
                cli_command=tuple(cli),
                source=candidate.source,
                display_name=f"{candidate.display_name} {version}",
                config_dir=candidate.config_dir,
                capabilities=BAMBU_CAPABILITIES,
                resource_dir=resource_dir,
                tested_version=TESTED_BAMBU_VERSION,
            )
        )
    return tuple(installations)


def build_prepare_project_command(
    installation: VibeCADPrint.SlicerInstallation,
    source_file: str | os.PathLike[str],
    output_file: str | os.PathLike[str],
    setup: VibeCADPrint.PrintSetup,
    machine_profile: str | os.PathLike[str],
    process_profile: str | os.PathLike[str],
    material_profiles: Iterable[str | os.PathLike[str]],
    *,
    model_files: Iterable[str | os.PathLike[str]] = (),
) -> tuple[str, ...]:
    """Build Bambu Studio's shell-free, exact-profile project export command."""

    material_paths = tuple(material_profiles)
    model_paths = tuple(Path(path) for path in model_files)
    if model_paths and not setup.object_filament_ids:
        raise VibeCADPrint.SlicerError(
            "Choose a filament for each object before preparing a multi-filament "
            "project."
        )
    if model_paths and len(model_paths) != len(setup.object_filament_ids):
        raise VibeCADPrint.SlicerError(
            "Bambu Studio requires one separate model file and filament choice for "
            "each object."
        )
    command = [*installation.cli_command, "--debug", "2"]
    command.extend(("--arrange", "1" if setup.auto_arrange else "0"))
    if setup.ensure_on_bed:
        command.append("--ensure-on-bed")
    if model_paths:
        command.extend(
            (
                "--load-filament-ids",
                ",".join(str(value) for value in setup.object_filament_ids),
            )
        )
    command.extend(
        (
            "--load-settings",
            f"{Path(machine_profile)};{Path(process_profile)}",
            "--load-filaments",
            ";".join(str(Path(path)) for path in material_paths),
            "--export-3mf",
            str(Path(output_file)),
        )
    )
    command.extend(str(path) for path in model_paths or (Path(source_file),))
    return tuple(command)


def _project_setup(
    setup: VibeCADPrint.PrintSetup,
    *,
    object_count: int,
) -> VibeCADPrint.PrintSetup:
    """Return a dense project-filament setup with one explicit ID per object."""

    material_count = len(setup.material_profiles)
    raw_ids = tuple(setup.object_filament_ids)
    if material_count == 1 and not raw_ids:
        raw_ids = (1,) * object_count
    if len(raw_ids) != object_count:
        raise VibeCADPrint.SlicerError(
            "Choose a filament for each object before preparing the slicer project."
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > material_count
        for value in raw_ids
    ):
        raise VibeCADPrint.SlicerError(
            "Choose a valid filament for each object before preparing the slicer "
            "project."
        )
    used_ids = tuple(sorted(set(raw_ids)))
    remap = {source_id: index for index, source_id in enumerate(used_ids, start=1)}
    return replace(
        setup,
        material_profiles=tuple(
            setup.material_profiles[source_id - 1] for source_id in used_ids
        ),
        object_filament_ids=tuple(remap[value] for value in raw_ids),
    )


def _source_object_names(path: Path) -> tuple[str, ...]:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
    except (ET.ParseError, OSError, KeyError, zipfile.BadZipFile) as exc:
        raise VibeCADPrint.SlicerError(
            "Could not prepare the Bambu Studio project because the source 3MF "
            "does not contain a valid object model."
        ) from exc
    objects = root.findall("./{*}resources/{*}object")
    if not objects:
        raise VibeCADPrint.SlicerError(
            "Could not prepare the Bambu Studio project because the source 3MF "
            "does not contain printable objects."
        )
    return tuple(str(obj.attrib.get("name", "") or "") for obj in objects)


def _prepared_object_names(path: Path) -> tuple[str, ...]:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("Metadata/model_settings.config"))
            json.loads(archive.read("Metadata/project_settings.config"))
    except (
        ET.ParseError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise VibeCADPrint.SlicerError(
            "Bambu Studio produced an invalid project 3MF without project metadata."
        ) from exc
    return tuple(
        str(metadata.attrib.get("value", "") or "")
        for metadata in root.findall('./{*}object/{*}metadata[@key="name"]')
    )


def _metadata_tag(element: ET.Element) -> str:
    namespace = (
        element.tag.partition("}")[0] + "}" if element.tag.startswith("{") else ""
    )
    return f"{namespace}metadata"


def _set_metadata(element: ET.Element, key: str, value: str) -> None:
    metadata = next(
        (
            item
            for item in element.findall("./{*}metadata")
            if item.attrib.get("key") == key
        ),
        None,
    )
    if metadata is None:
        metadata = ET.SubElement(element, _metadata_tag(element), {"key": key})
    metadata.set("value", value)


def _normalize_project_metadata(
    path: Path,
    *,
    source_names: Sequence[str],
    setup: VibeCADPrint.PrintSetup,
    filament_keys: set[str],
) -> None:
    """Repair Bambu-format CLI metadata without changing selected profiles."""

    try:
        with zipfile.ZipFile(path) as archive:
            model = ET.fromstring(archive.read("Metadata/model_settings.config"))
            settings = json.loads(archive.read("Metadata/project_settings.config"))
    except (
        ET.ParseError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise VibeCADPrint.SlicerError(
            "Bambu Studio produced an invalid project 3MF without editable project "
            "metadata."
        ) from exc
    if not isinstance(settings, dict):
        raise VibeCADPrint.SlicerError(
            "Bambu Studio produced invalid project profile metadata."
        )

    objects = model.findall("./{*}object")
    object_ids = tuple(setup.object_filament_ids)
    if len(objects) != len(source_names) or (
        object_ids and len(object_ids) != len(source_names)
    ):
        raise VibeCADPrint.SlicerError(
            "Bambu Studio changed the project object count while assigning "
            "filaments."
        )
    for index, (obj, name) in enumerate(zip(objects, source_names)):
        _set_metadata(obj, "name", str(name))
        if object_ids:
            _set_metadata(obj, "extruder", str(object_ids[index]))
        for part in obj.findall("./{*}part"):
            _set_metadata(part, "name", str(name))

    material_count = len(setup.material_profiles)
    for key in filament_keys | {"filament_colour", "filament_map"}:
        values = settings.get(key)
        if material_count > 1 and isinstance(values, list) and len(values) == 1:
            settings[key] = values * material_count

    filament_settings = settings.get("filament_settings_id")
    if not isinstance(filament_settings, list) or len(filament_settings) != material_count:
        raise VibeCADPrint.SlicerError(
            "Bambu Studio produced incomplete filament profile metadata."
        )

    nozzle_diameters = settings.get("nozzle_diameter")
    nozzle_count = len(nozzle_diameters) if isinstance(nozzle_diameters, list) else 0
    nozzle_types = settings.get("nozzle_volume_type")
    if (
        nozzle_count > 1
        and isinstance(nozzle_types, list)
        and 0 < len(nozzle_types) < nozzle_count
    ):
        defaults = settings.get("default_nozzle_volume_type")
        if isinstance(defaults, list) and len(defaults) == nozzle_count:
            settings["nozzle_volume_type"] = list(defaults)
        elif len(nozzle_types) == 1:
            settings["nozzle_volume_type"] = nozzle_types * nozzle_count
        else:
            raise VibeCADPrint.SlicerError(
                "Bambu Studio produced incomplete nozzle metadata."
            )

    replacements = {
        "Metadata/model_settings.config": ET.tostring(
            model,
            encoding="utf-8",
            xml_declaration=True,
        ),
        "Metadata/project_settings.config": json.dumps(
            settings,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
    }
    rewritten = path.with_name(f".{path.name}.{uuid4().hex}.rewrite")
    try:
        with (
            zipfile.ZipFile(path) as source_archive,
            zipfile.ZipFile(rewritten, "w") as rewritten_archive,
        ):
            rewritten_archive.comment = source_archive.comment
            for item in source_archive.infolist():
                replacement = replacements.get(item.filename)
                if replacement is not None:
                    rewritten_archive.writestr(item, replacement)
                    continue
                with (
                    source_archive.open(item) as source_entry,
                    rewritten_archive.open(
                        item,
                        mode="w",
                        force_zip64=True,
                    ) as rewritten_entry,
                ):
                    shutil.copyfileobj(source_entry, rewritten_entry)
        os.replace(rewritten, path)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise VibeCADPrint.SlicerError(
            f"Could not finalize the Bambu Studio project metadata: {exc}"
        ) from exc
    finally:
        if rewritten.exists():
            try:
                rewritten.unlink()
            except OSError:
                pass


def prepare_bambu_project(
    installation: VibeCADPrint.SlicerInstallation,
    source_file: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    setup: VibeCADPrint.PrintSetup,
    *,
    model_files: Iterable[str | os.PathLike[str]] = (),
    source_names: Iterable[str] = (),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 120.0,
    backend: BambuStudioBackend | None = None,
) -> Path:
    """Atomically prepare a full-profile, named, multi-object Bambu 3MF."""

    source = Path(source_file)
    target = Path(destination)
    exact_source_names = tuple(str(name) for name in source_names)
    if not exact_source_names:
        exact_source_names = _source_object_names(source)
    model_paths = tuple(Path(path) for path in model_files)
    if model_paths and len(model_paths) != len(exact_source_names):
        raise VibeCADPrint.SlicerError(
            "Bambu Studio requires one separate model file for each source object."
        )
    project_setup = (
        _project_setup(setup, object_count=len(exact_source_names))
        if model_paths
        else setup
    )
    backend = backend or BambuStudioBackend()
    printers = backend.query_printers(installation)
    printer = next((item for item in printers if item.name == setup.printer_profile), None)
    if printer is None:
        raise VibeCADPrint.SlicerError(
            f"Bambu Studio printer profile '{setup.printer_profile}' is not available."
        )
    catalog = backend.query_profiles(installation, printer.name)
    errors = VibeCADPrint.validate_setup(
        setup,
        printer,
        catalog,
        allow_additional_materials=True,
    )
    if errors:
        raise VibeCADPrint.SlicerError("\n".join(errors))
    resolved = (
        ("machine", setup.printer_profile),
        ("process", setup.print_profile),
        *[("filament", name) for name in project_setup.material_profiles],
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    profile_paths: list[Path] = []
    resolved_profiles: list[dict[str, Any]] = []
    partial = target.with_name(f"{target.stem}.{token}.partial.3mf")
    try:
        for index, (profile_type, name) in enumerate(resolved):
            profile = next(
                (
                    value
                    for record, value in backend._resolved(installation, profile_type)
                    if record.name == name
                ),
                None,
            )
            if profile is None:
                raise VibeCADPrint.SlicerError(
                    f"Bambu Studio profile '{name}' ({profile_type}) is not available."
                )
            resolved_profiles.append(dict(profile))
            path = target.parent / f".{target.stem}.{token}.{index}.profile.json"
            path.write_text(
                json.dumps(
                    profile,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            profile_paths.append(path)
        command = build_prepare_project_command(
            installation,
            source,
            partial,
            project_setup,
            profile_paths[0],
            profile_paths[1],
            profile_paths[2:],
            model_files=model_paths,
        )
        with tempfile.TemporaryDirectory(
            prefix=".vibecad-slicer-",
            dir=target.parent,
        ) as working_directory:
            completed = runner(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=working_directory,
                **VibeCADPrint.background_subprocess_kwargs(),
            )
        if completed.returncode != 0 or not partial.is_file():
            details = " ".join(
                value.strip()
                for value in (completed.stdout or "", completed.stderr or "")
                if value.strip()
            )
            suffix = f" {details}" if details else ""
            raise VibeCADPrint.SlicerError(
                "Bambu Studio could not prepare the 3MF project "
                f"(status {completed.returncode}).{suffix}"
            )
        _normalize_project_metadata(
            partial,
            source_names=exact_source_names,
            setup=project_setup,
            filament_keys={
                key
                for profile in resolved_profiles[2:]
                for key in profile
                if key.startswith("filament_")
            },
        )
        prepared_names = _prepared_object_names(partial)
        if len(prepared_names) != len(exact_source_names):
            raise VibeCADPrint.SlicerError(
                "Bambu Studio changed the project object count from "
                f"{len(exact_source_names)} to {len(prepared_names)}; the original 3MF "
                "was preserved."
            )
        if all(exact_source_names) and Counter(prepared_names) != Counter(
            exact_source_names
        ):
            raise VibeCADPrint.SlicerError(
                "Bambu Studio changed the project object names; the original 3MF "
                "was preserved."
            )
        os.replace(partial, target)
    except VibeCADPrint.SlicerError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise VibeCADPrint.SlicerError(
            f"Could not prepare the Bambu Studio project: {exc}"
        ) from exc
    finally:
        for path in (partial, *profile_paths):
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
    return target


def launch_bambu_studio(
    installation: VibeCADPrint.SlicerInstallation,
    handoff_file: str | os.PathLike[str],
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    platform: str | None = None,
) -> VibeCADPrint.LaunchResult:
    return VibeCADPrint.launch_slicer_gui(
        installation,
        handoff_file,
        slicer_name="Bambu Studio",
        popen=popen,
        platform=platform,
    )


class BambuStudioBackend:
    """Backend adapter consumed by the shared VibeCAD print workflow."""

    backend_id = "bambustudio"
    display_name = "Bambu Studio"
    capabilities = BAMBU_CAPABILITIES

    def __init__(self) -> None:
        self._installation_cache: dict[
            str, tuple[VibeCADPrint.SlicerInstallation, ...]
        ] = {}
        self._store_cache: dict[
            VibeCADPrint.SlicerInstallation, _ProfileStore
        ] = {}
        self._resolved_cache: dict[
            tuple[VibeCADPrint.SlicerInstallation, str],
            tuple[tuple[_ProfileRecord, dict[str, Any]], ...],
        ] = {}
        self._printer_cache: dict[
            VibeCADPrint.SlicerInstallation,
            tuple[VibeCADPrint.PrinterProfile, ...],
        ] = {}
        self._catalog_cache: dict[
            tuple[VibeCADPrint.SlicerInstallation, str],
            VibeCADPrint.ProfileCatalog,
        ] = {}

    def invalidate_cache(self) -> None:
        """Forget all slicer data so an explicit Refresh rereads disk and CLI state."""

        self._installation_cache.clear()
        self._store_cache.clear()
        self._resolved_cache.clear()
        self._printer_cache.clear()
        self._catalog_cache.clear()

    def _store(
        self,
        installation: VibeCADPrint.SlicerInstallation,
    ) -> _ProfileStore:
        value = self._store_cache.get(installation)
        if value is None:
            value = _load_profile_store(installation)
            self._store_cache[installation] = value
        return value

    def _resolved(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        profile_type: str,
    ) -> tuple[tuple[_ProfileRecord, dict[str, Any]], ...]:
        key = (installation, profile_type)
        value = self._resolved_cache.get(key)
        if value is None:
            value = _instantiated_profiles(self._store(installation), profile_type)
            self._resolved_cache[key] = value
        return value

    def discover(
        self, explicit_executable: str = ""
    ) -> tuple[VibeCADPrint.SlicerInstallation, ...]:
        key = str(explicit_executable or "").strip()
        value = self._installation_cache.get(key)
        if value is None:
            value = discover_bambu_installations(key)
            self._installation_cache[key] = value
        return value

    def query_printers(
        self, installation: VibeCADPrint.SlicerInstallation
    ) -> tuple[VibeCADPrint.PrinterProfile, ...]:
        value = self._printer_cache.get(installation)
        if value is None:
            value = _printer_profiles_from_instantiated(
                self._resolved(installation, "machine")
            )
            self._printer_cache[installation] = value
        return value

    def query_profiles(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        printer_profile: str,
    ) -> VibeCADPrint.ProfileCatalog:
        key = (installation, printer_profile)
        value = self._catalog_cache.get(key)
        if value is None:
            value = _compatible_profile_catalog(
                self._store(installation),
                printer_profile,
                self._resolved(installation, "filament"),
                self._resolved(installation, "process"),
            )
            self._catalog_cache[key] = value
        return value

    def prepare_project(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        source_file: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        setup: VibeCADPrint.PrintSetup,
        *,
        model_files: Iterable[str | os.PathLike[str]] = (),
        source_names: Iterable[str] = (),
    ) -> Path:
        return prepare_bambu_project(
            installation,
            source_file,
            destination,
            setup,
            model_files=model_files,
            source_names=source_names,
            backend=self,
        )

    def launch(
        self,
        installation: VibeCADPrint.SlicerInstallation,
        handoff_file: str | os.PathLike[str],
        _setup: VibeCADPrint.PrintSetup | None,
    ) -> VibeCADPrint.LaunchResult:
        return launch_bambu_studio(installation, handoff_file)
