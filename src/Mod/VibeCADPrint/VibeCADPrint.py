# SPDX-License-Identifier: LGPL-2.1-or-later

"""PrusaSlicer discovery, profiles, 3MF handoff, and launch services.

The module intentionally has no FreeCAD or Qt dependency at import time.  The
GUI commands adapt these services to the running application, while tests and
future slicing backends can use the same contracts without starting a GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
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


TESTED_PRUSASLICER_VERSION = (2, 9, 6)
MANAGED_HANDOFF_PREFIX = "vibecad-print-"
DEFAULT_HANDOFF_LIMIT = 10
BACKEND_CAPABILITIES = (
    "gui_handoff",
    "profile_query",
    # Reserved additive capabilities keep the UI independent of one engine.
    "future_plate_metadata",
    "future_headless_slice",
    "future_toolpath_preview",
)

_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?")
_PRERELEASE_RE = re.compile(r"(?:alpha|beta|\brc\d*|dev|nightly)", re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SlicerError(RuntimeError):
    """Base error for an external slicer operation."""


class SlicerQueryError(SlicerError):
    """An installed slicer's profile query failed or returned invalid JSON."""


class PrintSelectionError(SlicerError):
    """The explicit document selection cannot be exported for printing."""


class PrintExportError(SlicerError):
    """The selected geometry could not be exported as a complete 3MF."""


@dataclass(frozen=True)
class BedInfo:
    """Build-volume summary returned by PrusaSlicer's profile query."""

    kind: str = ""
    width: float = 0.0
    height: float = 0.0
    origin: tuple[float, float] = (0.0, 0.0)
    max_print_height: float = 0.0


@dataclass(frozen=True)
class PrinterProfile:
    """One exact, installed printer preset."""

    name: str
    model_id: str = ""
    model_name: str = ""
    variant_name: str = ""
    vendor_id: str = ""
    vendor_name: str = ""
    technology: str = "FFF"
    extruders: int = 1
    bed: BedInfo = field(default_factory=BedInfo)
    is_user: bool = False


@dataclass(frozen=True)
class MaterialProfile:
    """One exact material preset compatible with a print preset."""

    name: str
    is_user: bool = False


@dataclass(frozen=True)
class PrintProfile:
    """One print preset and the material presets compatible with it."""

    name: str
    materials: tuple[MaterialProfile, ...] = ()
    is_user: bool = False


@dataclass(frozen=True)
class ProfileCatalog:
    """Compatible print and material profiles for one printer preset."""

    printer_profile: str
    print_profiles: tuple[PrintProfile, ...] = ()

    def find_print_profile(self, name: str) -> PrintProfile | None:
        return next(
            (profile for profile in self.print_profiles if profile.name == name), None
        )


@dataclass(frozen=True)
class PrintSetup:
    """The user's explicitly confirmed handoff configuration."""

    printer_profile: str
    print_profile: str
    material_profiles: tuple[str, ...]
    auto_arrange: bool = True
    ensure_on_bed: bool = True


@dataclass(frozen=True)
class CandidateSpec:
    """An unprobed platform-specific PrusaSlicer command pair."""

    gui_command: tuple[str, ...]
    cli_command: tuple[str, ...]
    source: str
    display_name: str = "PrusaSlicer"
    config_dir: str = ""
    explicit: bool = False


@dataclass(frozen=True)
class SlicerInstallation:
    """A probed external slicing installation."""

    backend_id: str
    version: str
    gui_command: tuple[str, ...]
    cli_command: tuple[str, ...]
    source: str
    display_name: str
    config_dir: str = ""
    capabilities: tuple[str, ...] = BACKEND_CAPABILITIES

    @property
    def tested(self) -> bool:
        return version_key(self.version) >= TESTED_PRUSASLICER_VERSION


@dataclass(frozen=True)
class LaunchResult:
    """Result of starting an external slicer process."""

    command: tuple[str, ...]
    process_id: int | None


def version_key(value: str) -> tuple[int, int, int]:
    """Return a sortable three-part version from PrusaSlicer output."""

    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _is_stable_version(value: str) -> bool:
    return not bool(_PRERELEASE_RE.search(str(value or "")))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_origin(value: Any) -> tuple[float, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            values = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value)
            value = values[:2]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
        if len(values) >= 2:
            return (_as_float(values[0]), _as_float(values[1]))
    return (0.0, 0.0)


def _bed_info(value: Any) -> BedInfo:
    raw = value if isinstance(value, Mapping) else {}
    return BedInfo(
        kind=str(raw.get("type", "") or ""),
        width=_as_float(raw.get("width")),
        height=_as_float(raw.get("height")),
        origin=_parse_origin(raw.get("origin")),
        max_print_height=_as_float(raw.get("max_print_height")),
    )


def _printer_profile(
    value: Any,
    *,
    model: Mapping[str, Any],
    variant_name: str,
    is_user: bool,
) -> PrinterProfile | None:
    raw = value if isinstance(value, Mapping) else {}
    name = str(raw.get("name", "") or "").strip()
    if not name:
        return None
    technology = str(model.get("technology", "FFF") or "FFF")
    default_extruders = 0 if technology.upper() == "SLA" else 1
    return PrinterProfile(
        name=name,
        model_id=str(model.get("id", "") or ""),
        model_name=str(model.get("name", "") or name),
        variant_name=variant_name,
        vendor_id=str(model.get("vendor_id", "") or ""),
        vendor_name=str(model.get("vendor_name", "") or ""),
        technology=technology,
        extruders=_as_int(raw.get("extruders_cnt"), default_extruders),
        bed=_bed_info(raw.get("bed")),
        is_user=is_user,
    )


def parse_printer_models(payload: Mapping[str, Any]) -> tuple[PrinterProfile, ...]:
    """Flatten PrusaSlicer's model/variant JSON into exact printer presets."""

    parsed: list[PrinterProfile] = []
    models = payload.get("printer_models", [])
    if not isinstance(models, Sequence) or isinstance(models, (str, bytes)):
        raise SlicerQueryError(
            "PrusaSlicer printer query did not contain printer_models."
        )
    for value in models:
        if not isinstance(value, Mapping):
            continue
        variants = value.get("variants")
        containers: list[tuple[Mapping[str, Any], str]] = []
        if isinstance(variants, Sequence) and not isinstance(variants, (str, bytes)):
            containers.extend(
                (variant, str(variant.get("name", "") or ""))
                for variant in variants
                if isinstance(variant, Mapping)
            )
        else:
            containers.append((value, ""))
        for container, variant_name in containers:
            for key, is_user in (
                ("printer_profiles", False),
                ("user_printer_profiles", True),
            ):
                values = container.get(key, [])
                if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                    continue
                for raw_profile in values:
                    profile = _printer_profile(
                        raw_profile,
                        model=value,
                        variant_name=variant_name,
                        is_user=is_user,
                    )
                    if profile is not None:
                        parsed.append(profile)
    return tuple(parsed)


def _material_profiles(raw: Mapping[str, Any]) -> tuple[MaterialProfile, ...]:
    materials: list[MaterialProfile] = []
    for key, is_user in (
        ("filament_profiles", False),
        ("sla_material_profiles", False),
        ("user_filament_profiles", True),
        ("user_sla_material_profiles", True),
    ):
        values = raw.get(key, [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            name = str(value or "").strip()
            if name and all(item.name != name for item in materials):
                materials.append(MaterialProfile(name=name, is_user=is_user))
    return tuple(materials)


def parse_compatible_profiles(payload: Mapping[str, Any]) -> ProfileCatalog:
    """Parse print profiles while retaining their material compatibility sets."""

    printer_profile = str(payload.get("printer_profile", "") or "").strip()
    if not printer_profile:
        raise SlicerQueryError("PrusaSlicer profile query omitted printer_profile.")
    profiles: list[PrintProfile] = []
    for key, is_user in (("print_profiles", False), ("user_print_profiles", True)):
        values = payload.get(key, [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            name = str(value.get("name", "") or "").strip()
            if name:
                profiles.append(
                    PrintProfile(
                        name=name,
                        materials=_material_profiles(value),
                        is_user=is_user,
                    )
                )
    return ProfileCatalog(
        printer_profile=printer_profile, print_profiles=tuple(profiles)
    )


def default_config_directory(
    *, platform: str | None = None, environ: Mapping[str, str] | None = None
) -> str:
    """Return PrusaSlicer's normal per-platform configuration directory."""

    platform = platform or sys.platform
    env = dict(os.environ if environ is None else environ)
    if platform == "win32":
        base = env.get("APPDATA", "")
        return ntpath.join(base, "PrusaSlicer") if base else ""
    home = env.get("HOME", "")
    if platform == "darwin":
        return (
            str(Path(home) / "Library/Application Support/PrusaSlicer") if home else ""
        )
    base = env.get("XDG_CONFIG_HOME", "")
    if base:
        return str(Path(base) / "PrusaSlicer")
    return str(Path(home) / ".config/PrusaSlicer") if home else ""


def default_candidate_specs(
    *, platform: str | None = None, environ: Mapping[str, str] | None = None
) -> tuple[CandidateSpec, ...]:
    """Return narrow, predictable PrusaSlicer candidates for an OS."""

    platform = platform or sys.platform
    env = dict(os.environ if environ is None else environ)
    config_dir = default_config_directory(platform=platform, environ=env)
    candidates: list[CandidateSpec] = []
    if platform == "win32":
        roots = [env.get("ProgramFiles", r"C:\Program Files")]
        if env.get("LOCALAPPDATA"):
            roots.append(env["LOCALAPPDATA"])
        for root in roots:
            gui = ntpath.join(root, "Prusa3D", "PrusaSlicer", "prusa-slicer.exe")
            console = ntpath.join(
                root, "Prusa3D", "PrusaSlicer", "prusa-slicer-console.exe"
            )
            candidates.append(
                CandidateSpec(
                    gui_command=(gui,),
                    cli_command=(console,),
                    source="standard",
                    config_dir=config_dir,
                )
            )
        candidates.append(
            CandidateSpec(
                gui_command=("prusa-slicer.exe",),
                cli_command=("prusa-slicer-console.exe",),
                source="path",
                config_dir=config_dir,
            )
        )
    elif platform == "darwin":
        executable = "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"
        candidates.extend(
            [
                CandidateSpec(
                    gui_command=(executable,),
                    cli_command=(executable,),
                    source="standard",
                    config_dir=config_dir,
                ),
                CandidateSpec(
                    gui_command=("prusa-slicer",),
                    cli_command=("prusa-slicer",),
                    source="path",
                    config_dir=config_dir,
                ),
            ]
        )
    else:
        candidates.extend(
            [
                CandidateSpec(
                    gui_command=("prusa-slicer",),
                    cli_command=("prusa-slicer",),
                    source="path",
                    config_dir=config_dir,
                ),
                CandidateSpec(
                    gui_command=("/usr/local/bin/prusa-slicer",),
                    cli_command=("/usr/local/bin/prusa-slicer",),
                    source="standard",
                    config_dir=config_dir,
                ),
                CandidateSpec(
                    gui_command=("/usr/bin/prusa-slicer",),
                    cli_command=("/usr/bin/prusa-slicer",),
                    source="standard",
                    config_dir=config_dir,
                ),
                CandidateSpec(
                    gui_command=(
                        "flatpak",
                        "--user",
                        "run",
                        "com.prusa3d.PrusaSlicer",
                    ),
                    cli_command=(
                        "flatpak",
                        "--user",
                        "run",
                        "--command=/app/bin/prusa-slicer",
                        "com.prusa3d.PrusaSlicer",
                    ),
                    source="flatpak-user",
                    display_name="PrusaSlicer (Flatpak)",
                ),
                CandidateSpec(
                    gui_command=(
                        "flatpak",
                        "--system",
                        "run",
                        "com.prusa3d.PrusaSlicer",
                    ),
                    cli_command=(
                        "flatpak",
                        "--system",
                        "run",
                        "--command=/app/bin/prusa-slicer",
                        "com.prusa3d.PrusaSlicer",
                    ),
                    source="flatpak-system",
                    display_name="PrusaSlicer (Flatpak)",
                ),
            ]
        )
    return tuple(candidates)


def explicit_candidate_spec(
    executable: str,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> CandidateSpec | None:
    """Create a GUI/CLI pair from a user-selected executable or macOS app."""

    raw = str(executable or "").strip()
    if not raw:
        return None
    platform = platform or sys.platform
    if platform == "darwin" and raw.endswith(".app"):
        raw = str(Path(raw) / "Contents/MacOS/PrusaSlicer")
    cli = raw
    if platform == "win32" and ntpath.basename(raw).lower() == "prusa-slicer.exe":
        cli = ntpath.join(ntpath.dirname(raw), "prusa-slicer-console.exe")
    return CandidateSpec(
        gui_command=(raw,),
        cli_command=(cli,),
        source="explicit",
        config_dir=default_config_directory(platform=platform, environ=environ),
        explicit=True,
    )


def _resolve_program(
    command: tuple[str, ...], which: Callable[[str], str | None]
) -> tuple[str, ...] | None:
    if not command:
        return None
    first = command[0]
    if os.path.isabs(first) or (len(first) > 2 and first[1:3] == ":\\"):
        if Path(first).is_file():
            return command
        return None
    found = which(first)
    if not found:
        return None
    return (found, *command[1:])


def probe_candidate(
    candidate: CandidateSpec,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    timeout: float = 10.0,
) -> SlicerInstallation | None:
    """Validate a candidate and read its version without starting its GUI."""

    gui_command = _resolve_program(candidate.gui_command, which)
    cli_command = _resolve_program(candidate.cli_command, which)
    if gui_command is None:
        return None
    # Windows installers occasionally omit the console binary.  The GUI
    # executable still accepts CLI actions, so it is a safe query fallback.
    if cli_command is None and candidate.source != "explicit":
        cli_command = gui_command
    elif cli_command is None:
        cli_command = gui_command
    try:
        completed = runner(
            [*cli_command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = "\n".join(
        str(value or "") for value in (completed.stdout, completed.stderr)
    )
    match = _VERSION_RE.search(output)
    if match is None:
        return None
    version = match.group(0)
    return SlicerInstallation(
        backend_id="prusaslicer",
        version=version,
        gui_command=tuple(gui_command),
        cli_command=tuple(cli_command),
        source=candidate.source,
        display_name=f"{candidate.display_name} {version}",
        config_dir=candidate.config_dir,
    )


def discover_prusaslicer_installations(
    explicit_executable: str = "",
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[SlicerInstallation, ...]:
    """Discover and probe supported native and Flatpak installations."""

    specs: list[CandidateSpec] = []
    explicit = explicit_candidate_spec(
        explicit_executable,
        platform=platform,
        environ=environ,
    )
    if explicit is not None:
        specs.append(explicit)
    specs.extend(default_candidate_specs(platform=platform, environ=environ))
    installations: list[SlicerInstallation] = []
    seen: set[tuple[str, ...]] = set()
    for spec in specs:
        installation = probe_candidate(spec, runner=runner, which=which)
        if installation is None or installation.gui_command in seen:
            continue
        seen.add(installation.gui_command)
        installations.append(installation)
    return tuple(installations)


def preferred_installation(
    installations: Iterable[SlicerInstallation],
    *,
    explicit_gui_command: tuple[str, ...] | None = None,
) -> SlicerInstallation | None:
    """Prefer an explicit installation, otherwise the newest stable build."""

    values = list(installations)
    configured = next((item for item in values if item.source == "explicit"), None)
    if configured is not None and explicit_gui_command is None:
        return configured
    if explicit_gui_command:
        explicit = next(
            (item for item in values if item.gui_command == explicit_gui_command), None
        )
        if explicit is not None:
            return explicit
    stable = [item for item in values if _is_stable_version(item.version)]
    candidates = stable or values
    return max(candidates, key=lambda item: version_key(item.version), default=None)


def _query_failure_message(
    completed: subprocess.CompletedProcess[str], detail: str = ""
) -> str:
    pieces = [f"PrusaSlicer profile query failed with status {completed.returncode}."]
    if detail:
        pieces.append(detail)
    if completed.stdout:
        pieces.append(f"stdout: {completed.stdout.strip()}")
    if completed.stderr:
        pieces.append(f"stderr: {completed.stderr.strip()}")
    return " ".join(piece for piece in pieces if piece)


def run_json_query(
    installation: SlicerInstallation,
    arguments: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    temporary_directory: str | os.PathLike[str] | None = None,
    timeout: float = 30.0,
) -> Mapping[str, Any]:
    """Run a PrusaSlicer JSON query, accepting valid output despite status 1."""

    if temporary_directory is None:
        with tempfile.TemporaryDirectory(prefix="vibecad-prusa-query-") as directory:
            return run_json_query(
                installation,
                arguments,
                runner=runner,
                temporary_directory=directory,
                timeout=timeout,
            )
    directory = Path(temporary_directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"profiles-{uuid4().hex}.json"
    command = [*installation.cli_command, *arguments]
    if installation.config_dir:
        command.extend(("--datadir", installation.config_dir))
    # Flatpak gives the slicer a private /tmp, so a path created by the host is
    # not the same path inside the sandbox.  Profile-sharing actions natively
    # emit the same JSON to stdout when --output is omitted.
    query_via_stdout = installation.source.startswith("flatpak")
    if not query_via_stdout:
        command.extend(("--output", str(output)))
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SlicerQueryError(
            f"Could not run PrusaSlicer profile query: {exc}"
        ) from exc
    if query_via_stdout and completed.stdout:
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SlicerQueryError(
                _query_failure_message(completed, f"Invalid JSON output: {exc}")
            ) from exc
        if isinstance(payload, Mapping):
            return payload
        raise SlicerQueryError(
            _query_failure_message(completed, "JSON output was not an object.")
        )
    if output.is_file():
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SlicerQueryError(
                _query_failure_message(completed, f"Invalid JSON output: {exc}")
            ) from exc
        if isinstance(payload, Mapping):
            return payload
        raise SlicerQueryError(
            _query_failure_message(completed, "JSON output was not an object.")
        )
    raise SlicerQueryError(_query_failure_message(completed))


def query_printer_profiles(
    installation: SlicerInstallation,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[PrinterProfile, ...]:
    payload = run_json_query(
        installation,
        ("--query-printer-models", "--printer-technology", "FFF"),
        runner=runner,
    )
    return parse_printer_models(payload)


def query_compatible_profiles(
    installation: SlicerInstallation,
    printer_profile: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProfileCatalog:
    payload = run_json_query(
        installation,
        (
            "--query-print-filament-profiles",
            "--printer-profile",
            printer_profile,
        ),
        runner=runner,
    )
    return parse_compatible_profiles(payload)


def validate_setup(
    setup: PrintSetup,
    printer: PrinterProfile,
    catalog: ProfileCatalog,
) -> tuple[str, ...]:
    """Validate exact names without substituting a compatible-looking preset."""

    errors: list[str] = []
    if setup.printer_profile != printer.name or catalog.printer_profile != printer.name:
        errors.append(
            f"Printer profile '{setup.printer_profile}' is no longer available."
        )
        return tuple(errors)
    print_profile = catalog.find_print_profile(setup.print_profile)
    if print_profile is None:
        errors.append(
            f"Print profile '{setup.print_profile}' is not compatible with this printer."
        )
        return tuple(errors)
    required = max(0, printer.extruders)
    if len(setup.material_profiles) != required:
        errors.append(
            f"Select one material profile for each of the printer's {required} extruders."
        )
        return tuple(errors)
    compatible = {material.name for material in print_profile.materials}
    unavailable = [name for name in setup.material_profiles if name not in compatible]
    if unavailable:
        joined = ", ".join(dict.fromkeys(unavailable))
        errors.append(
            f"Material profile(s) {joined} are not compatible with '{setup.print_profile}'."
        )
    return tuple(errors)


def build_launch_command(
    installation: SlicerInstallation,
    handoff_file: str | os.PathLike[str],
    setup: PrintSetup | None,
) -> tuple[str, ...]:
    """Build the exact shell-free GUI handoff command."""

    command = list(installation.gui_command)
    if setup is not None:
        if not setup.auto_arrange:
            command.append("--dont-arrange")
        command.append(
            "--ensure-on-bed" if setup.ensure_on_bed else "--no-ensure-on-bed"
        )
        command.extend(("--printer-profile", setup.printer_profile))
        command.extend(("--print-profile", setup.print_profile))
        command.extend(("--material-profile", ";".join(setup.material_profiles)))
    command.append(str(Path(handoff_file)))
    return tuple(command)


def launch_prusaslicer(
    installation: SlicerInstallation,
    handoff_file: str | os.PathLike[str],
    setup: PrintSetup | None,
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> LaunchResult:
    """Launch PrusaSlicer detached from VibeCAD without invoking a shell."""

    command = build_launch_command(installation, handoff_file, setup)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    try:
        process = popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
            creationflags=creationflags,
        )
    except OSError as exc:
        raise SlicerError(f"Could not launch PrusaSlicer: {exc}") from exc
    return LaunchResult(command=command, process_id=getattr(process, "pid", None))


def _is_printable_object(obj: Any) -> bool:
    shape = getattr(obj, "Shape", None)
    if shape is not None:
        is_null = getattr(shape, "isNull", None)
        try:
            if not callable(is_null) or not bool(is_null()):
                return True
        except Exception:
            pass
    mesh = getattr(obj, "Mesh", None)
    if mesh is None:
        return False
    count = getattr(mesh, "CountFacets", None)
    try:
        return int(count) > 0
    except (TypeError, ValueError):
        facets = getattr(mesh, "Facets", ())
        try:
            return len(facets) > 0
        except TypeError:
            return False


def _object_label(obj: Any) -> str:
    return str(getattr(obj, "Label", "") or getattr(obj, "Name", "") or repr(obj))


def collect_printable_objects(
    selection: Iterable[Any], *, active_document: Any
) -> tuple[Any, ...]:
    """Validate an explicit selection; never fall back to visible/all objects."""

    if active_document is None:
        raise PrintSelectionError(
            "Open an active document before exporting for printing."
        )
    unique: list[Any] = []
    seen: set[int] = set()
    for obj in selection:
        identity = id(obj)
        if identity not in seen:
            seen.add(identity)
            unique.append(obj)
    if not unique:
        raise PrintSelectionError("Select at least one printable object.")
    unsupported = [
        _object_label(obj)
        for obj in unique
        if getattr(obj, "Document", None) is not active_document
        or not _is_printable_object(obj)
    ]
    if unsupported:
        raise PrintSelectionError(
            "These selected objects cannot be exported for printing: "
            + ", ".join(unsupported)
        )
    return tuple(unique)


def export_selection_3mf(
    objects: Iterable[Any],
    destination: str | os.PathLike[str],
    *,
    mesh_exporter: Callable[[Sequence[Any], str], Any] | None = None,
) -> Path:
    """Export all objects together and atomically publish a complete 3MF."""

    selected = tuple(objects)
    if not selected:
        raise PrintExportError("No selected objects were provided for 3MF export.")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.stem}.{uuid4().hex}.partial.3mf")
    if mesh_exporter is None:
        import Mesh

        mesh_exporter = Mesh.export
    try:
        mesh_exporter(list(selected), str(partial))
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise PrintExportError("3MF export did not produce a non-empty file.")
        os.replace(partial, target)
    except PrintExportError:
        raise
    except Exception as exc:
        raise PrintExportError(
            f"Could not export the selected objects as 3MF: {exc}"
        ) from exc
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
    return target


def _safe_name(value: str, fallback: str = "Untitled") -> str:
    sanitized = _SAFE_NAME_RE.sub("-", str(value or "").strip()).strip("-._")
    return (sanitized or fallback)[:80]


def prune_managed_handoffs(
    directory: str | os.PathLike[str], *, keep: int = DEFAULT_HANDOFF_LIMIT
) -> None:
    """Remove only old 3MF files carrying this feature's private prefix."""

    root = Path(directory)
    if not root.is_dir():
        return
    owned = sorted(
        (
            path
            for path in root.glob(f"{MANAGED_HANDOFF_PREFIX}*.3mf")
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for path in owned[max(0, keep) :]:
        try:
            path.unlink()
        except OSError:
            continue


def managed_handoff_path(
    cache_directory: str | os.PathLike[str],
    *,
    document_label: str,
    object_names: Sequence[str],
    keep: int = DEFAULT_HANDOFF_LIMIT,
) -> Path:
    """Create a collision-resistant managed handoff path and prune old files."""

    root = Path(cache_directory)
    root.mkdir(parents=True, exist_ok=True)
    prune_managed_handoffs(root, keep=keep)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    identity = "\0".join(str(name) for name in object_names)
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:10]
    label = _safe_name(document_label)
    return root / f"{MANAGED_HANDOFF_PREFIX}{label}-{stamp}-{digest}.3mf"


class PrusaSlicerBackend:
    """Adapter used by today's GUI and future plate/slice coordinators."""

    backend_id = "prusaslicer"
    capabilities = BACKEND_CAPABILITIES

    def discover(self, explicit_executable: str = "") -> tuple[SlicerInstallation, ...]:
        return discover_prusaslicer_installations(explicit_executable)

    def query_printers(
        self, installation: SlicerInstallation
    ) -> tuple[PrinterProfile, ...]:
        return query_printer_profiles(installation)

    def query_profiles(
        self, installation: SlicerInstallation, printer_profile: str
    ) -> ProfileCatalog:
        return query_compatible_profiles(installation, printer_profile)

    def launch(
        self,
        installation: SlicerInstallation,
        handoff_file: str | os.PathLike[str],
        setup: PrintSetup | None,
    ) -> LaunchResult:
        return launch_prusaslicer(installation, handoff_file, setup)
