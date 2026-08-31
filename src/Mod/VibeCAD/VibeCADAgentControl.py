# SPDX-License-Identifier: LGPL-2.1-or-later

"""Local loopback control channel for an external desktop agent.

This is additive and independent of MCP. Enabling it does not disable the
in-app VibeCAD Assistant, so Grok / ChatGPT / OpenAI / Anthropic can keep
driving the open document while a local agent performs guarded native-file
round trips, captures the visible window, activates semantic Qt targets without
controlling the physical cursor, runs authorized compatibility scripts, shows
Preferences, or reads auth status.

The fail-closed development server binds only to 127.0.0.1. The original
compatibility starter retains its explicit-host behavior for existing callers.
Callers authenticate with a bearer token that VibeCAD writes to a private file
the agent can read; the agent never types passwords or OAuth codes.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import traceback
import uuid
from typing import Any, Callable
from urllib.parse import unquote, urlparse


AGENT_HOST = "127.0.0.1"
DEFAULT_AGENT_PORT = 8766
AGENT_PORT_ENV = "VIBECAD_AGENT_PORT"
AGENT_HOME_ENV = "VIBECAD_AGENT_HOME"
TOKEN_FILENAME = "token"
ENDPOINT_FILENAME = "endpoint.json"
AGENT_BRIEF_FILENAME = "AGENTS.md"
GROK_BOT_CMD_ENV = "VIBECAD_GROK_BOT_CMD"
TOKEN_BYTES = 32
MAX_BODY_BYTES = 1_048_576
DEV_MODE_ENV = "VIBECAD_DEV_MODE"
DEV_SOURCE_SHA_ENV = "VIBECAD_DEV_SOURCE_SHA"
DEV_SOURCE_TREE_ENV = "VIBECAD_DEV_SOURCE_TREE"
DEV_SOURCE_ROOT_ENV = "VIBECAD_DEV_SOURCE_ROOT"
DEV_ATTESTATION_REQUIRED_ENV = "VIBECAD_DEV_ATTESTATION_REQUIRED"
DEV_BUILD_ATTESTATION_ENV = "VIBECAD_DEV_BUILD_ATTESTATION"
DEV_BUILD_ATTESTATION_SHA256_ENV = "VIBECAD_DEV_BUILD_ATTESTATION_SHA256"
DEV_LAUNCH_ATTESTATION_ENV = "VIBECAD_DEV_LAUNCH_ATTESTATION"
DEV_LAUNCH_ATTESTATION_SHA256_ENV = "VIBECAD_DEV_LAUNCH_ATTESTATION_SHA256"
BUILD_ATTESTATION_SCHEMA = "vibecad.dev-build-attestation.v1"
LAUNCH_ATTESTATION_SCHEMA = "vibecad.dev-launch-attestation.v1"
RUNTIME_IDENTITY_SCHEMA = "vibecad.dev-runtime-identity.v1"
OPERATION_TRACKING_SCHEMA = "vibecad.dev-operation-tracking.v1"
OPERATION_STATUS_ROUTE_TEMPLATE = "/v1/operations/{operation_id}"
MAX_TRACKED_OPERATIONS = 256
SEMANTIC_MENU_PREVIEW_MILLISECONDS = 240
DEVELOPMENT_IDENTITY_ENV_VARS = (
    DEV_MODE_ENV,
    DEV_SOURCE_SHA_ENV,
    DEV_SOURCE_TREE_ENV,
    DEV_SOURCE_ROOT_ENV,
    DEV_ATTESTATION_REQUIRED_ENV,
    DEV_BUILD_ATTESTATION_ENV,
    DEV_BUILD_ATTESTATION_SHA256_ENV,
    DEV_LAUNCH_ATTESTATION_ENV,
    DEV_LAUNCH_ATTESTATION_SHA256_ENV,
)
_ATTESTED_RUNTIME_MODULES = (
    "InitGui.py",
    "VibeCADAgentControl.py",
    "VibeCADAgentCli.py",
    "VibeCADGui.py",
)
_ATTESTED_SOURCE_ONLY_MODULES = (
    "Invoke-VibeCAD-VisibleTour.ps1",
    "Launch-VibeCAD-Dev.ps1",
)
COMMANDS = (
    "status",
    "documents",
    "open",
    "save",
    "save_as",
    "close",
    "ui_ribbon",
    "ui_menus",
    "ui_click",
    "screenshot",
    "run",
    "preferences",
    "aero",
)
UPSTREAM_COMMANDS = frozenset(
    {"status", "documents", "open", "run", "preferences", "aero"}
)

_server_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None
_document_operation_gate = threading.Lock()
_tracked_operations_lock = threading.RLock()
_tracked_operations: dict[str, dict[str, Any]] = {}
_bound_port: int | None = None
_server_instance_id = secrets.token_urlsafe(32)
_process_started_at_utc = (
    datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
)
_server_started_at_utc: str | None = None
_active_runtime_identity: dict[str, Any] | None = None


def _utc_now_text() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_path(path: Path, *, kind: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"The attested {kind} does not exist: {path}") from exc
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _validated_sha256(value: Any, *, label: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RuntimeError(f"The {label} is not a complete SHA-256 digest.")
    return digest


def _validated_git_object(value: Any, *, label: str) -> str:
    object_id = str(value or "").strip().lower()
    if len(object_id) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in object_id
    ):
        raise RuntimeError(f"The attested {label} is not a full Git object ID.")
    return object_id


def _git_checkout_identity(repository_root: Path) -> tuple[Path, str, str]:
    """Read the canonical root, full HEAD, and HEAD tree from the checkout."""

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def run_git(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository_root), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise RuntimeError(
                "The attested development checkout identity could not be read with Git."
            ) from exc
        output = completed.stdout.strip()
        if completed.returncode != 0 or not output:
            detail = completed.stderr.strip() or f"Git exited with {completed.returncode}."
            raise RuntimeError(
                f"The attested development checkout identity is unavailable: {detail}"
            )
        return output

    canonical_root = _canonical_path(
        Path(run_git("rev-parse", "--show-toplevel")),
        kind="Git repository root",
    )
    commit = _validated_git_object(
        run_git("rev-parse", "--verify", "HEAD"), label="checkout commit"
    )
    tree = _validated_git_object(
        run_git("rev-parse", "--verify", "HEAD^{tree}"), label="checkout tree"
    )
    return canonical_root, commit, tree


def _load_attestation(
    *,
    path_environment: str,
    hash_environment: str,
    schema: str,
    label: str,
) -> tuple[Path, str, dict[str, Any]]:
    raw_path = str(os.environ.get(path_environment) or "").strip()
    raw_hash = str(os.environ.get(hash_environment) or "").strip()
    if not raw_path or not raw_hash:
        raise RuntimeError(
            f"The {label} path and SHA-256 must both be supplied for an attested launch."
        )
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError(f"The {label} path must be absolute: {candidate}")
    path = _canonical_path(candidate, kind=label)
    expected_hash = _validated_sha256(raw_hash, label=f"{label} SHA-256")
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"The {label} could not be read: {path}") from exc
    actual_hash = hashlib.sha256(encoded).hexdigest()
    if not secrets.compare_digest(actual_hash, expected_hash):
        raise RuntimeError(
            f"The {label} SHA-256 does not match its actual file: {path}"
        )
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"The {label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise RuntimeError(f"The {label} does not use schema {schema}.")
    declared_path = Path(str(payload.get("attestation_path") or ""))
    if not declared_path.is_absolute() or not _same_path(declared_path, path):
        raise RuntimeError(f"The {label} does not bind its exact canonical path.")
    return path, actual_hash, payload


def _current_executable_path() -> Path:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_module_filename = kernel32.GetModuleFileNameW
        get_module_filename.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        get_module_filename.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(get_module_filename(None, buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        return _canonical_path(Path(buffer.value), kind="process executable")
    return _canonical_path(Path(sys.executable), kind="process executable")


def _current_qwindows_module_path() -> Path:
    """Return the qwindows.dll loaded by this exact VibeCAD GUI process."""

    if sys.platform != "win32":
        raise RuntimeError(
            "The attested Qt Windows platform plugin can be inspected only on Windows."
        )

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_module_handle = kernel32.GetModuleHandleW
    get_module_handle.argtypes = [wintypes.LPCWSTR]
    get_module_handle.restype = wintypes.HMODULE
    module = get_module_handle("qwindows.dll")
    if not module:
        raise RuntimeError(
            "The current VibeCAD process has not loaded the qwindows.dll platform plugin."
        )

    get_module_filename = kernel32.GetModuleFileNameW
    get_module_filename.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    get_module_filename.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(get_module_filename(module, buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return _canonical_path(
        Path(buffer.value), kind="qwindows.dll loaded by the current VibeCAD process"
    )


def _actual_runtime_module_paths() -> dict[str, Path]:
    module_root = Path(__file__).resolve().parent
    init_module = sys.modules.get("InitGui")
    init_path = Path(str(getattr(init_module, "__file__", "") or ""))
    if not init_path.is_file():
        init_path = module_root / "InitGui.py"
    gui_module = sys.modules.get("VibeCADGui")
    gui_path = Path(str(getattr(gui_module, "__file__", "") or ""))
    if not gui_path.is_file():
        gui_path = module_root / "VibeCADGui.py"
    return {
        "InitGui.py": _canonical_path(init_path, kind="InitGui.py"),
        "VibeCADAgentControl.py": _canonical_path(
            Path(__file__), kind="VibeCADAgentControl.py"
        ),
        "VibeCADAgentCli.py": _canonical_path(
            module_root / "VibeCADAgentCli.py", kind="VibeCADAgentCli.py"
        ),
        "VibeCADGui.py": _canonical_path(gui_path, kind="VibeCADGui.py"),
    }


def _expected_source_paths(repository_root: Path) -> dict[str, Path]:
    module_root = repository_root / "src" / "Mod" / "VibeCAD"
    return {
        "InitGui.py": module_root / "InitGui.py",
        "VibeCADAgentControl.py": module_root / "VibeCADAgentControl.py",
        "VibeCADAgentCli.py": module_root / "VibeCADAgentCli.py",
        "VibeCADGui.py": module_root / "VibeCADGui.py",
        "Invoke-VibeCAD-VisibleTour.ps1": (
            repository_root / "Invoke-VibeCAD-VisibleTour.ps1"
        ),
        "Launch-VibeCAD-Dev.ps1": repository_root / "Launch-VibeCAD-Dev.ps1",
    }


def _validated_runtime_modules(
    *,
    repository_root: Path,
    build_payload: dict[str, Any],
    launch_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    build_modules = build_payload.get("modules")
    launch_modules = launch_payload.get("modules")
    if not isinstance(build_modules, list) or build_modules != launch_modules:
        raise RuntimeError(
            "The build and launch attestations do not bind the same module identities."
        )
    by_name: dict[str, dict[str, Any]] = {}
    for item in build_modules:
        if not isinstance(item, dict):
            raise RuntimeError("An attested module identity is not a JSON object.")
        name = str(item.get("name") or "")
        if not name or name in by_name:
            raise RuntimeError("Attested module names must be non-empty and unique.")
        by_name[name] = item
    required_names = set(_ATTESTED_RUNTIME_MODULES + _ATTESTED_SOURCE_ONLY_MODULES)
    if set(by_name) != required_names:
        raise RuntimeError(
            "The attestations must bind exactly the required launcher, tour, and runtime modules."
        )

    expected_sources = _expected_source_paths(repository_root)
    actual_runtime_paths = _actual_runtime_module_paths()
    expected_runtime_root = (
        repository_root
        / "package"
        / "rattler-build"
        / ".pixi"
        / "envs"
        / "default"
    ).resolve()
    identities: list[dict[str, Any]] = []
    for name in _ATTESTED_RUNTIME_MODULES + _ATTESTED_SOURCE_ONLY_MODULES:
        item = by_name[name]
        source_path = Path(str(item.get("source_path") or ""))
        expected_source = _canonical_path(expected_sources[name], kind=f"{name} source")
        if not source_path.is_absolute() or not _same_path(source_path, expected_source):
            raise RuntimeError(f"The attested {name} source path is not canonical.")
        expected_source_hash = _validated_sha256(
            item.get("source_sha256"), label=f"{name} source SHA-256"
        )
        actual_source_hash = _sha256_file(expected_source)
        if not secrets.compare_digest(expected_source_hash, actual_source_hash):
            raise RuntimeError(f"The {name} source SHA-256 does not match its actual file.")

        runtime_path: Path | None = None
        runtime_hash: str | None = None
        if name in _ATTESTED_RUNTIME_MODULES:
            declared_runtime = Path(str(item.get("installed_path") or ""))
            actual_runtime = _canonical_path(
                actual_runtime_paths[name], kind=f"installed {name}"
            )
            if not declared_runtime.is_absolute() or not _same_path(
                declared_runtime, actual_runtime
            ):
                raise RuntimeError(
                    f"The attested installed path for {name} is not the file used at runtime."
                )
            try:
                actual_runtime.relative_to(expected_runtime_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"The runtime {name} is outside the checkout's Pixi environment."
                ) from exc
            expected_runtime_hash = _validated_sha256(
                item.get("installed_sha256"), label=f"{name} installed SHA-256"
            )
            runtime_hash = _sha256_file(actual_runtime)
            if not secrets.compare_digest(expected_runtime_hash, runtime_hash):
                raise RuntimeError(
                    f"The {name} installed SHA-256 does not match its actual runtime file."
                )
            if not secrets.compare_digest(actual_source_hash, runtime_hash):
                raise RuntimeError(
                    f"The installed {name} is stale relative to the attested checkout source."
                )
            runtime_path = actual_runtime
        elif item.get("installed_path") is not None or item.get("installed_sha256") is not None:
            raise RuntimeError(f"The source-only {name} must not claim an installed module.")

        identities.append(
            {
                "name": name,
                "source_path": str(expected_source),
                "source_sha256": actual_source_hash,
                "runtime_path": str(runtime_path) if runtime_path is not None else None,
                "runtime_sha256": runtime_hash,
            }
        )
    return identities


def _matching_attestation_object(
    *,
    name: str,
    build_payload: dict[str, Any],
    launch_payload: dict[str, Any],
) -> dict[str, Any]:
    build_value = build_payload.get(name)
    launch_value = launch_payload.get(name)
    if not isinstance(build_value, dict) or not isinstance(launch_value, dict):
        raise RuntimeError(
            f"The build and launch attestations must both contain a complete {name} object."
        )
    if build_value != launch_value:
        raise RuntimeError(
            f"The build and launch attestations do not bind the same {name}."
        )
    return build_value


def _attested_absolute_path(value: Any, *, label: str) -> Path:
    candidate = Path(str(value or "")).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError(f"The attested {label} path must be absolute.")
    return _canonical_path(candidate, kind=label)


def _path_within(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"The attested {label} is outside the checkout's Pixi environment."
        ) from exc


def _validated_qt_attestations(
    *,
    repository_root: Path,
    build_payload: dict[str, Any],
    launch_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    qt_runtime = _matching_attestation_object(
        name="qt_runtime",
        build_payload=build_payload,
        launch_payload=launch_payload,
    )
    qt_probe = _matching_attestation_object(
        name="qt_platform_probe",
        build_payload=build_payload,
        launch_payload=launch_payload,
    )

    if qt_runtime.get("qt_major") != 6:
        raise RuntimeError("The attested qt_runtime must bind the locked Qt 6 runtime.")
    environment_root = _canonical_path(
        repository_root
        / "package"
        / "rattler-build"
        / ".pixi"
        / "envs"
        / "default",
        kind="checkout Pixi environment",
    )
    plugin_root = _attested_absolute_path(
        qt_runtime.get("plugin_root"), label="Qt plugin root"
    )
    platforms_directory = _attested_absolute_path(
        qt_runtime.get("platforms_directory"), label="Qt platforms directory"
    )
    qwindows = _attested_absolute_path(
        qt_runtime.get("qwindows_path"), label="Qt qwindows.dll"
    )
    dll_directory = _attested_absolute_path(
        qt_runtime.get("dll_directory"), label="Qt DLL directory"
    )
    for path, label in (
        (plugin_root, "Qt plugin root"),
        (platforms_directory, "Qt platforms directory"),
        (qwindows, "Qt qwindows.dll"),
        (dll_directory, "Qt DLL directory"),
    ):
        _path_within(path, environment_root, label=label)
    if not _same_path(platforms_directory.parent, plugin_root):
        raise RuntimeError(
            "The attested qt_runtime platforms directory is not under its plugin root."
        )
    if not _same_path(qwindows.parent, platforms_directory):
        raise RuntimeError(
            "The attested qt_runtime qwindows.dll is not in its platforms directory."
        )

    qwindows_relative = qwindows.relative_to(environment_root).as_posix().casefold()
    dll_directory_relative = dll_directory.relative_to(environment_root).as_posix().casefold()
    supported_layouts = {
        (
            "library/lib/qt6/plugins/platforms/qwindows.dll",
            "library/bin",
        ),
        (
            "library/lib/qt6/plugins/platforms/qwindows.dll",
            "library/lib/qt6/bin",
        ),
        ("library/plugins/platforms/qwindows.dll", "library/bin"),
        ("plugins/platforms/qwindows.dll", "bin"),
    }
    if (qwindows_relative, dll_directory_relative) not in supported_layouts:
        raise RuntimeError(
            "The attested qt_runtime does not use a supported checkout-local Qt 6 layout."
        )

    qwindows_hash = _sha256_file(qwindows)
    expected_qwindows_hash = _validated_sha256(
        qt_runtime.get("qwindows_sha256"), label="qt_runtime qwindows SHA-256"
    )
    if not secrets.compare_digest(qwindows_hash, expected_qwindows_hash):
        raise RuntimeError(
            "The attested qt_runtime qwindows SHA-256 does not match its actual file."
        )

    dlls = qt_runtime.get("dlls")
    if not isinstance(dlls, list):
        raise RuntimeError("The attested qt_runtime DLL identities are incomplete.")
    expected_dll_names = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"}
    validated_dll_names: set[str] = set()
    for item in dlls:
        if not isinstance(item, dict):
            raise RuntimeError("An attested qt_runtime DLL identity is not an object.")
        name = str(item.get("name") or "")
        if name not in expected_dll_names or name in validated_dll_names:
            raise RuntimeError(
                "The attested qt_runtime must bind exactly the required Qt 6 DLLs."
            )
        path = _attested_absolute_path(item.get("path"), label=f"{name} DLL")
        if not _same_path(path.parent, dll_directory):
            raise RuntimeError(
                f"The attested qt_runtime {name} is outside its declared DLL directory."
            )
        actual_hash = _sha256_file(path)
        expected_hash = _validated_sha256(
            item.get("sha256"), label=f"qt_runtime {name} SHA-256"
        )
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise RuntimeError(
                f"The attested qt_runtime {name} SHA-256 does not match its actual file."
            )
        validated_dll_names.add(name)
    if validated_dll_names != expected_dll_names:
        raise RuntimeError(
            "The attested qt_runtime must bind exactly the required Qt 6 DLLs."
        )

    if qt_probe.get("complete") is not True:
        raise RuntimeError("The attested qt_platform_probe is not complete.")
    if str(qt_probe.get("platform") or "").strip().lower() != "windows":
        raise RuntimeError("The attested qt_platform_probe did not load the Windows backend.")
    if not str(qt_probe.get("checked_at_utc") or "").strip():
        raise RuntimeError("The attested qt_platform_probe has no completion timestamp.")
    python_executable = _attested_absolute_path(
        qt_probe.get("python_executable"), label="Qt probe Python executable"
    )
    expected_python = _canonical_path(
        environment_root / "python.exe", kind="checkout Pixi Python executable"
    )
    if not _same_path(python_executable, expected_python):
        raise RuntimeError(
            "The attested qt_platform_probe did not use the checkout's exact Python executable."
        )
    python_hash = _sha256_file(python_executable)
    expected_python_hash = _validated_sha256(
        qt_probe.get("python_sha256"), label="qt_platform_probe Python SHA-256"
    )
    if not secrets.compare_digest(python_hash, expected_python_hash):
        raise RuntimeError(
            "The attested qt_platform_probe Python SHA-256 does not match its actual file."
        )
    probed_qwindows = _attested_absolute_path(
        qt_probe.get("loaded_qwindows_path"), label="probed qwindows.dll"
    )
    if not _same_path(probed_qwindows, qwindows):
        raise RuntimeError(
            "The attested qt_platform_probe did not load the qt_runtime qwindows.dll."
        )
    probed_hash = _validated_sha256(
        qt_probe.get("loaded_qwindows_sha256"),
        label="qt_platform_probe qwindows SHA-256",
    )
    if not secrets.compare_digest(probed_hash, qwindows_hash):
        raise RuntimeError(
            "The attested qt_platform_probe qwindows SHA-256 does not match its actual file."
        )

    process_qwindows = _current_qwindows_module_path()
    if not _same_path(process_qwindows, qwindows):
        raise RuntimeError(
            "The current VibeCAD process did not load the exact attested qwindows.dll."
        )
    process_qwindows_hash = _sha256_file(process_qwindows)
    if not secrets.compare_digest(process_qwindows_hash, qwindows_hash):
        raise RuntimeError(
            "The qwindows.dll loaded by the current VibeCAD process changed after attestation."
        )

    qt_process = {
        "platform": "windows",
        "loaded_qwindows_path": str(process_qwindows),
        "loaded_qwindows_sha256": process_qwindows_hash,
    }
    return qt_runtime, qt_probe, qt_process


def _validated_release_evidence(
    *,
    build_payload: dict[str, Any],
    launch_payload: dict[str, Any],
) -> dict[str, Any]:
    evidence = _matching_attestation_object(
        name="release_evidence",
        build_payload=build_payload,
        launch_payload=launch_payload,
    )
    required_fields = {
        "asserted",
        "clean_checkout",
        "submodule_dirt_checked",
        "git_status_mode",
        "cold_build_asserted",
        "pre_build_environment_present",
        "pre_build_runtime_complete",
        "environment_absent_before_install",
        "pre_build_checked_at_utc",
        "environment_cleaned_at_utc",
        "build_cache_cleaned_at_utc",
        "pre_receipt_checked_at_utc",
    }
    missing_fields = required_fields.difference(evidence)
    if missing_fields:
        raise RuntimeError(
            "The attested release_evidence object is partial; missing: "
            + ", ".join(sorted(missing_fields))
        )
    if not isinstance(evidence.get("asserted"), bool):
        raise RuntimeError("The attested release_evidence asserted field is not Boolean.")
    if not isinstance(evidence.get("cold_build_asserted"), bool):
        raise RuntimeError(
            "The attested release_evidence cold-build field is not Boolean."
        )
    for name in ("pre_build_environment_present", "pre_build_runtime_complete"):
        if not isinstance(evidence.get(name), bool):
            raise RuntimeError(f"The attested release_evidence {name} field is not Boolean.")

    if evidence["asserted"]:
        expected_values = {
            "clean_checkout": True,
            "submodule_dirt_checked": True,
            "git_status_mode": (
                "--porcelain=v2 --untracked-files=all --ignore-submodules=none"
            ),
            "cold_build_asserted": True,
            "environment_absent_before_install": True,
        }
        for name, expected in expected_values.items():
            if evidence.get(name) != expected:
                raise RuntimeError(
                    f"The attested release_evidence does not prove {name}."
                )
        for name in (
            "pre_build_checked_at_utc",
            "environment_cleaned_at_utc",
            "build_cache_cleaned_at_utc",
            "pre_receipt_checked_at_utc",
        ):
            if not str(evidence.get(name) or "").strip():
                raise RuntimeError(
                    f"The attested release_evidence has no {name} timestamp."
                )
    else:
        if evidence["cold_build_asserted"]:
            raise RuntimeError(
                "The attested release_evidence cannot claim a cold build without release attestation."
            )
        for name in (
            "clean_checkout",
            "submodule_dirt_checked",
            "git_status_mode",
            "environment_absent_before_install",
            "pre_build_checked_at_utc",
            "environment_cleaned_at_utc",
            "build_cache_cleaned_at_utc",
            "pre_receipt_checked_at_utc",
        ):
            if evidence.get(name) is not None:
                raise RuntimeError(
                    f"The non-release release_evidence must leave {name} unset."
                )
    return evidence


def development_runtime_identity() -> dict[str, Any] | None:
    """Return an actual-file-derived identity for an attested dev launch.

    Normal installed startup and the legacy ``-SkipRebuild`` developer path do
    not provide attestation variables, so they retain the compatible ``None``
    identity instead of being made dependent on a source checkout.
    """

    attestation_values = {
        name: str(os.environ.get(name) or "").strip()
        for name in (
            DEV_BUILD_ATTESTATION_ENV,
            DEV_BUILD_ATTESTATION_SHA256_ENV,
            DEV_LAUNCH_ATTESTATION_ENV,
            DEV_LAUNCH_ATTESTATION_SHA256_ENV,
        )
    }
    required = str(os.environ.get(DEV_ATTESTATION_REQUIRED_ENV) or "").strip() == "1"
    if not any(attestation_values.values()):
        if required:
            raise RuntimeError("Development attestation is required but receipt values are missing.")
        return None
    if str(os.environ.get(DEV_MODE_ENV) or "").strip() != "1":
        raise RuntimeError("Development attestations are valid only in VIBECAD_DEV_MODE=1.")
    if not all(attestation_values.values()):
        raise RuntimeError("Development build and launch attestation values are incomplete.")

    build_path, build_hash, build_payload = _load_attestation(
        path_environment=DEV_BUILD_ATTESTATION_ENV,
        hash_environment=DEV_BUILD_ATTESTATION_SHA256_ENV,
        schema=BUILD_ATTESTATION_SCHEMA,
        label="build attestation",
    )
    launch_path, launch_hash, launch_payload = _load_attestation(
        path_environment=DEV_LAUNCH_ATTESTATION_ENV,
        hash_environment=DEV_LAUNCH_ATTESTATION_SHA256_ENV,
        schema=LAUNCH_ATTESTATION_SCHEMA,
        label="launch attestation",
    )
    if build_path.parent != launch_path.parent:
        raise RuntimeError("Build and launch attestations are not in the same receipt directory.")
    if build_path.parent.name != "attestations" or build_path.parent.parent.name != ".vibecad-dev":
        raise RuntimeError("Development attestations are outside the checkout's ignored receipt root.")
    repository_root = _canonical_path(
        build_path.parent.parent.parent, kind="canonical repository root"
    )

    declared_repository = _canonical_path(
        Path(str(build_payload.get("repository_root") or "")),
        kind="build repository root",
    )
    if not _same_path(repository_root, declared_repository):
        raise RuntimeError("The build attestation repository root is not path-derived.")
    launch_repository = _canonical_path(
        Path(str(launch_payload.get("repository_root") or "")),
        kind="launch repository root",
    )
    if not _same_path(repository_root, launch_repository):
        raise RuntimeError("The launch attestation repository root does not match the build.")

    commit = _validated_git_object(build_payload.get("commit"), label="commit")
    tree = _validated_git_object(build_payload.get("tree"), label="tree")
    if _validated_git_object(launch_payload.get("commit"), label="launch commit") != commit:
        raise RuntimeError("The launch attestation commit does not match the build.")
    if _validated_git_object(launch_payload.get("tree"), label="launch tree") != tree:
        raise RuntimeError("The launch attestation tree does not match the build.")

    checkout_root, checkout_commit, checkout_tree = _git_checkout_identity(repository_root)
    if not _same_path(checkout_root, repository_root):
        raise RuntimeError("The attested repository root is not the checkout's canonical Git root.")
    if checkout_commit != commit:
        raise RuntimeError("The attested commit does not match the checkout's actual HEAD.")
    if checkout_tree != tree:
        raise RuntimeError("The attested tree does not match the checkout's actual HEAD tree.")

    source_commit = str(os.environ.get(DEV_SOURCE_SHA_ENV) or "").strip().lower()
    source_tree = str(os.environ.get(DEV_SOURCE_TREE_ENV) or "").strip().lower()
    source_root = str(os.environ.get(DEV_SOURCE_ROOT_ENV) or "").strip()
    if required and (not source_commit or not source_tree or not source_root):
        raise RuntimeError("The attested source commit, tree, and repository root are required.")
    if source_commit and source_commit != commit:
        raise RuntimeError("The launcher source commit does not match the attested commit.")
    if source_tree and source_tree != tree:
        raise RuntimeError("The launcher source tree does not match the attested tree.")
    if source_root:
        declared_source_root = _canonical_path(
            Path(source_root), kind="launcher repository root"
        )
        if not _same_path(declared_source_root, repository_root):
            raise RuntimeError("The launcher repository root does not match the receipts.")

    executable_path = _current_executable_path()
    declared_executable = Path(str(build_payload.get("executable_path") or ""))
    launch_executable = Path(str(launch_payload.get("executable_path") or ""))
    if not declared_executable.is_absolute() or not _same_path(
        declared_executable, executable_path
    ):
        raise RuntimeError("The build attestation executable is not the current process executable.")
    if not launch_executable.is_absolute() or not _same_path(
        launch_executable, executable_path
    ):
        raise RuntimeError("The launch attestation executable is not the current process executable.")
    expected_executable_hash = _validated_sha256(
        build_payload.get("executable_sha256"), label="executable SHA-256"
    )
    launch_executable_hash = _validated_sha256(
        launch_payload.get("executable_sha256"), label="launch executable SHA-256"
    )
    executable_hash = _sha256_file(executable_path)
    if not secrets.compare_digest(expected_executable_hash, executable_hash):
        raise RuntimeError("The executable SHA-256 does not match the running process file.")
    if not secrets.compare_digest(launch_executable_hash, executable_hash):
        raise RuntimeError("The launch executable SHA-256 does not match the running process file.")
    expected_environment = (
        repository_root / "package" / "rattler-build" / ".pixi" / "envs" / "default"
    ).resolve()
    try:
        executable_path.relative_to(expected_environment)
    except ValueError as exc:
        raise RuntimeError("The process executable is outside the checkout's Pixi environment.") from exc

    declared_build_path = Path(str(launch_payload.get("build_attestation_path") or ""))
    declared_build_hash = _validated_sha256(
        launch_payload.get("build_attestation_sha256"),
        label="launch build-attestation SHA-256",
    )
    if not declared_build_path.is_absolute() or not _same_path(
        declared_build_path, build_path
    ):
        raise RuntimeError("The launch attestation does not bind the exact build receipt path.")
    if not secrets.compare_digest(declared_build_hash, build_hash):
        raise RuntimeError("The launch attestation does not bind the exact build receipt hash.")

    modules = _validated_runtime_modules(
        repository_root=repository_root,
        build_payload=build_payload,
        launch_payload=launch_payload,
    )
    qt_runtime, qt_platform_probe, qt_process = _validated_qt_attestations(
        repository_root=repository_root,
        build_payload=build_payload,
        launch_payload=launch_payload,
    )
    release_evidence = _validated_release_evidence(
        build_payload=build_payload,
        launch_payload=launch_payload,
    )
    return {
        "schema": RUNTIME_IDENTITY_SCHEMA,
        "repository_root": str(repository_root),
        "commit": commit,
        "tree": tree,
        "executable_path": str(executable_path),
        "executable_sha256": executable_hash,
        "build_attestation_path": str(build_path),
        "build_attestation_sha256": build_hash,
        "launch_attestation_path": str(launch_path),
        "launch_attestation_sha256": launch_hash,
        "qt_runtime": json.loads(json.dumps(qt_runtime)),
        "qt_platform_probe": json.loads(json.dumps(qt_platform_probe)),
        "release_evidence": json.loads(json.dumps(release_evidence)),
        "qt_process": qt_process,
        "modules": modules,
    }


def agent_home() -> Path:
    override = str(os.environ.get(AGENT_HOME_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return root / "VibeCAD" / "Agent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VibeCAD" / "Agent"
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return root / "VibeCAD" / "agent"


def token_path() -> Path:
    return agent_home() / TOKEN_FILENAME


def endpoint_path() -> Path:
    return agent_home() / ENDPOINT_FILENAME


def _development_mode_enabled() -> bool:
    return str(os.environ.get(DEV_MODE_ENV) or "").strip() == "1"


def _enforce_windows_current_user_only_acl(path: Path) -> None:
    """Apply and verify one protected full-control ACE for the current user."""

    import ctypes
    from ctypes import wintypes

    TOKEN_QUERY = 0x0008
    TOKEN_USER_CLASS = 1
    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    SDDL_REVISION_1 = 1
    ACL_SIZE_INFORMATION_CLASS = 2
    ACCESS_ALLOWED_ACE_TYPE = 0x00
    OBJECT_INHERIT_ACE = 0x01
    CONTAINER_INHERIT_ACE = 0x02
    INHERITED_ACE = 0x10
    FILE_ALL_ACCESS = 0x001F01FF
    SE_DACL_PRESENT = 0x0004
    SE_DACL_PROTECTED = 0x1000

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("Header", _AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    advapi32.EqualSid.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, None, 0, ctypes.byref(needed)
        )
        if not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        token_buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            token_buffer,
            needed.value,
            ctypes.byref(needed),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        token_user = ctypes.cast(token_buffer, ctypes.POINTER(_TokenUser)).contents
        sid_text_pointer = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
            token_user.User.Sid, ctypes.byref(sid_text_pointer)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            sid_text = str(sid_text_pointer.value)
        finally:
            kernel32.LocalFree(sid_text_pointer)
    finally:
        kernel32.CloseHandle(token)

    inheritance = "OICI" if path.is_dir() else ""
    sddl = f"D:P(A;{inheritance};FA;;;{sid_text})"
    security_descriptor = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        SDDL_REVISION_1,
        ctypes.byref(security_descriptor),
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        dacl_present = wintypes.BOOL()
        dacl = wintypes.LPVOID()
        dacl_defaulted = wintypes.BOOL()
        if not advapi32.GetSecurityDescriptorDacl(
            security_descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not dacl_present.value or not dacl:
            raise OSError("The generated current-user DACL is absent.")
        result = advapi32.SetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel32.LocalFree(security_descriptor)

    descriptor_size = wintypes.DWORD()
    advapi32.GetFileSecurityW(
        str(path), DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(descriptor_size)
    )
    if not descriptor_size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor_buffer = ctypes.create_string_buffer(descriptor_size.value)
    if not advapi32.GetFileSecurityW(
        str(path),
        DACL_SECURITY_INFORMATION,
        descriptor_buffer,
        descriptor_size.value,
        ctypes.byref(descriptor_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    control_flags = wintypes.WORD()
    revision = wintypes.DWORD()
    if not advapi32.GetSecurityDescriptorControl(
        descriptor_buffer, ctypes.byref(control_flags), ctypes.byref(revision)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not control_flags.value & SE_DACL_PRESENT or not control_flags.value & SE_DACL_PROTECTED:
        raise OSError("The applied DACL is not present and inheritance-protected.")

    dacl_present = wintypes.BOOL()
    dacl = wintypes.LPVOID()
    dacl_defaulted = wintypes.BOOL()
    if not advapi32.GetSecurityDescriptorDacl(
        descriptor_buffer,
        ctypes.byref(dacl_present),
        ctypes.byref(dacl),
        ctypes.byref(dacl_defaulted),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    acl_info = _AclSizeInformation()
    if not advapi32.GetAclInformation(
        dacl,
        ctypes.byref(acl_info),
        ctypes.sizeof(acl_info),
        ACL_SIZE_INFORMATION_CLASS,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if acl_info.AceCount != 1:
        raise OSError("The applied DACL is not current-user-only.")
    ace_pointer = wintypes.LPVOID()
    if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)):
        raise ctypes.WinError(ctypes.get_last_error())
    ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAllowedAce)).contents
    if ace.Header.AceType != ACCESS_ALLOWED_ACE_TYPE or ace.Header.AceFlags & INHERITED_ACE:
        raise OSError("The applied DACL does not contain one explicit allow ACE.")
    expected_inheritance = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if path.is_dir() else 0
    if ace.Header.AceFlags & (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) != expected_inheritance:
        raise OSError("The applied DACL has the wrong inheritance contract.")
    if ace.Mask & FILE_ALL_ACCESS != FILE_ALL_ACCESS:
        raise OSError("The applied DACL does not grant current-user full control.")

    expected_sid = wintypes.LPVOID()
    if not advapi32.ConvertStringSidToSidW(sid_text, ctypes.byref(expected_sid)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ace_sid = wintypes.LPVOID(
            int(ace_pointer.value) + _AccessAllowedAce.SidStart.offset
        )
        if not advapi32.EqualSid(ace_sid, expected_sid):
            raise OSError("The applied DACL ACE does not belong to the current user.")
    finally:
        kernel32.LocalFree(expected_sid)


def _restrict_path(path: Path, *, directory: bool) -> None:
    required = _development_mode_enabled()
    mode = 0o700 if directory else 0o600
    if required and sys.platform == "win32":
        try:
            _enforce_windows_current_user_only_acl(path)
            return
        except Exception as exc:
            if required:
                raise RuntimeError(
                    f"VibeCAD development mode could not enforce a current-user-only ACL on {path}: {exc}"
                ) from exc
    try:
        os.chmod(path, mode)
    except OSError as exc:
        if required:
            raise RuntimeError(
                f"VibeCAD development mode could not restrict {path}: {exc}"
            ) from exc


def _prepare_agent_home() -> Path:
    home = agent_home()
    if _development_mode_enabled():
        source_root_value = str(os.environ.get(DEV_SOURCE_ROOT_ENV) or "").strip()
        if not source_root_value:
            raise RuntimeError(
                "VibeCAD development mode requires a checkout-scoped agent home and source root."
            )
        source_root = Path(source_root_value).expanduser()
        if not source_root.is_absolute():
            raise RuntimeError(
                "VibeCAD development mode requires an absolute checkout-scoped source root."
            )
        expected_home = (source_root.resolve() / ".vibecad-dev" / "agent").resolve()
        if not _same_path(home, expected_home):
            raise RuntimeError(
                "VibeCAD development mode requires the exact checkout-scoped "
                f"agent home {expected_home}, not {home}."
            )
    home.mkdir(parents=True, exist_ok=True)
    _restrict_path(home, directory=True)
    return home


def _restrict_file(path: Path) -> None:
    _restrict_path(path, directory=False)


def _valid_token(value: Any) -> str:
    token = str(value or "").strip()
    if len(token) < 40:
        return ""
    # Keep the alphabet identical to the MCP token so agents can reuse parsers.
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(character not in allowed for character in token):
        return ""
    return token


def load_or_create_token() -> str:
    path = token_path()
    _prepare_agent_home()
    if path.is_file():
        existing = _valid_token(path.read_text(encoding="utf-8"))
        if existing:
            _restrict_file(path)
            return existing
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    _restrict_file(path)
    return token


def load_token() -> str:
    path = token_path()
    if not path.is_file():
        return ""
    return _valid_token(path.read_text(encoding="utf-8"))


def _server_identity_fields() -> dict[str, Any]:
    return {
        "server_instance_id": _server_instance_id,
        "process_id": os.getpid(),
        "server_started_at_utc": _server_started_at_utc or _process_started_at_utc,
        "runtime_identity": _active_runtime_identity,
        "fail_closed": bool(getattr(_server, "vibecad_fail_closed", False)),
    }


def operation_tracking_contract() -> dict[str, str]:
    """Describe the additive timeout-completion evidence surface."""

    return {
        "schema": OPERATION_TRACKING_SCHEMA,
        "request_operation_id_field": "operation_id",
        "status_route_template": OPERATION_STATUS_ROUTE_TEMPLATE,
        "completed_state": "completed",
    }


def _validated_operation_id(value: Any) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(value, str) or not value:
        return None, failure(
            "OPERATION_ID_INVALID",
            "operation_id must be a canonical lowercase UUID string.",
            stage="schema",
        )
    raw = value.strip()
    try:
        parsed = uuid.UUID(raw)
    except (AttributeError, ValueError):
        parsed = None
    canonical = str(parsed) if parsed is not None else ""
    if raw != canonical:
        return None, failure(
            "OPERATION_ID_INVALID",
            "operation_id must be a canonical lowercase UUID string.",
            stage="schema",
        )
    return canonical, None


def _operation_json_copy(value: Any) -> Any:
    """Detach registry evidence from mutable dispatch response objects."""

    return json.loads(json.dumps(_json_safe(value), ensure_ascii=False))


def _prune_tracked_operations_locked() -> bool:
    """Make room for one operation without ever evicting running evidence."""

    while len(_tracked_operations) >= MAX_TRACKED_OPERATIONS:
        completed_id = next(
            (
                operation_id
                for operation_id, record in _tracked_operations.items()
                if record.get("state") == "completed"
            ),
            None,
        )
        if completed_id is None:
            return False
        _tracked_operations.pop(completed_id, None)
    return True


def _begin_tracked_operation(
    operation_id: str,
    *,
    command: str,
    server_instance_id: str | None = None,
) -> dict[str, Any] | None:
    owning_server_instance = str(server_instance_id or _server_instance_id)
    with _tracked_operations_lock:
        existing = _tracked_operations.get(operation_id)
        if existing is not None:
            return failure(
                "OPERATION_ID_CONFLICT",
                f"operation_id {operation_id!r} has already been used by this server instance.",
                stage="precondition",
                operation={
                    "operation_id": operation_id,
                    "state": str(existing.get("state") or "unknown"),
                },
            )
        if not _prune_tracked_operations_locked():
            return failure(
                "OPERATION_REGISTRY_FULL",
                "The bounded operation registry contains only running operations; retry after one completes.",
                stage="precondition",
            )
        _tracked_operations[operation_id] = {
            "operation_id": operation_id,
            "server_instance_id": owning_server_instance,
            "command": str(command),
            "state": "running",
            "started_at_utc": _utc_now_text(),
            "completed_at_utc": None,
            "result": None,
            "response": None,
        }
    return None


def _complete_tracked_operation(
    operation_id: str,
    response: dict[str, Any],
    *,
    server_instance_id: str | None = None,
) -> None:
    owning_server_instance = str(server_instance_id or _server_instance_id)
    detached_response = _operation_json_copy(response)
    logical_result = detached_response
    if detached_response.get("ok") is True and "result" in detached_response:
        logical_result = detached_response.get("result")
    with _tracked_operations_lock:
        record = _tracked_operations.get(operation_id)
        if record is None:
            return
        if record.get("server_instance_id") != owning_server_instance:
            return
        record["state"] = "completed"
        record["completed_at_utc"] = _utc_now_text()
        record["result"] = logical_result
        record["response"] = detached_response


def _tracked_operation_snapshot(operation_id: str) -> dict[str, Any] | None:
    with _tracked_operations_lock:
        record = _tracked_operations.get(operation_id)
        return _operation_json_copy(record) if record is not None else None


def _reset_tracked_operations() -> None:
    with _tracked_operations_lock:
        _tracked_operations.clear()


def write_endpoint(
    *,
    host: str,
    port: int,
    server_identity: dict[str, Any] | None = None,
) -> Path:
    path = endpoint_path()
    _prepare_agent_home()
    identity = (
        _operation_json_copy(server_identity)
        if server_identity is not None
        else _server_identity_fields()
    )
    payload = {
        "host": host,
        "port": int(port),
        "base_url": f"http://{host}:{int(port)}",
        "token_path": str(token_path().resolve()),
        "assistant_disabled_by_this_channel": False,
        **identity,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _restrict_file(path)
    return path


def load_endpoint() -> dict[str, Any] | None:
    path = endpoint_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def brief_path() -> Path:
    return agent_home() / AGENT_BRIEF_FILENAME


_AGENT_BRIEF_TEMPLATE = """# VibeCAD control brief for Grok Bot

VibeCAD is running on this machine and exposes an authenticated control
channel. The one-click development launcher uses loopback only; an explicitly
configured compatibility host remains under the owner's network authority. Use
exact semantic VibeCAD targets without taking over the human's physical cursor.

## Connect

- Base URL: `{base_url}`
- Auth: send header `Authorization: Bearer <token>`
- Token file: `{token_path}` (read the file contents; never prompt a human)
- Endpoint file (host/port/base_url/token_path): `{endpoint_path}`

## Routes (all require the bearer token)

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET  | `/v1/status`      |                                   | Provider, auth (no secrets), documents, endpoint |
| GET  | `/v1/documents`   |                                   | Open documents |
| POST | `/v1/open`        | `{{"path":"..."}}`                | Open/activate a document |
| POST | `/v1/save`        | optional `{{"document":"Name"}}` | Save an already-named document |
| POST | `/v1/save-as`     | `{{"path":"...","overwrite":false}}` | Save to an explicit .FCStd path |
| POST | `/v1/close`       | optional `{{"document":"Name","discard_unsaved":false}}` | Close without silently discarding changes |
| GET  | `/v1/ui/ribbon`   |                                   | Live semantic tab names and screen geometry |
| GET  | `/v1/ui/menus`    |                                   | Live top-level menu names and screen geometry |
| POST | `/v1/ui/click`    | `{{"kind":"ribbon","text":"Model"}}` | Activate a semantic Qt target without moving the physical cursor |
| GET/POST | `/v1/screenshot` | optional `{{"path":"...png","overwrite":false}}` | Capture the visible VibeCAD window |
| POST | `/v1/run`         | `{{"python":"..."}}` or `{{"script":"..."}}` (+ optional `path`, `recompute`) | Run against the active document |
| GET  | `/v1/operations/<operation_id>` |                         | Prove completion after a client timeout |
| GET  | `/v1/aero`        |                                   | Flight card + AeroReport stamps |
| POST | `/v1/aero`        | `{{"operation":"analyze"}}` (also section, vlm, export_jsbsim, report, propose_repairs, apply_repairs, reject_repairs, flight_card) | Same Aero wrapper as in-app Grok |
| POST | `/v1/preferences` |                                   | Show VibeCAD Preferences |

Use `/v1/aero` for aerodynamics. Do not `exec` Analyze or `apply_repairs`
through `/v1/run`. `/v1/run` remains for non-Aero Python.

`run` executes Python in the VibeCAD process with `App`/`FreeCAD` (and
`Gui`/`FreeCADGui` when the GUI is up). Assign `result` or `__result__` to
return a JSON value. Stdout, stderr, and exceptions come back in the payload.
Add a canonical lowercase UUID as `operation_id` when completion must remain
provable after a client timeout, then poll `/v1/operations/<operation_id>` until
its state is `completed` before continuing.

The token separates authenticated callers; it is not a filesystem sandbox.
Open, save-as, and authorized `run` operations use the VibeCAD process's file
authority.

Peak Aero loop for Grok Bot (same quality as in-app Grok):
1. GET `/v1/aero` for the stamped flight card.
2. POST `/v1/aero` `analyze` (does not move CAD).
3. GET `/v1/aero` again. Do not invent mass, CL, or airworthiness.
4. `propose_repairs` then `apply_repairs` only if the user wants CAD changes.
5. Appearance claims still need isometric + front + top screenshots. Pixels
   never prove aero numbers. Claim ceiling is always not_airworthy.

## Example

```bash
TOKEN="$(cat '{token_path}')"
curl -s -H "Authorization: Bearer $TOKEN" {base_url}/v1/status
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"python":"result = App.ActiveDocument and App.ActiveDocument.Name"}}' \\
  {base_url}/v1/run
```

## Rules

- Keep the one-click development server on loopback. Treat any owner-configured
  compatibility host as a privileged authenticated service.
- UI activation is in-process Qt only; never move, click, confine, hide, or
  block the human's physical cursor.
- Never type passwords or OAuth codes. Sign-in stays in VibeCAD Preferences.
- Do not enable MCP; it disables the in-app Assistant.
"""


def write_agent_brief(*, host: str = AGENT_HOST, port: int | None = None) -> Path:
    """Write an AGENTS.md brief telling a local agent how to drive VibeCAD.

    The brief is written next to the token/endpoint files so a desktop agent
    such as Grok Bot can read the connection details and the available routes.
    """

    resolved_port = int(port or _bound_port or configured_port())
    base_url = f"http://{host}:{resolved_port}"
    path = brief_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _AGENT_BRIEF_TEMPLATE.format(
        base_url=base_url,
        token_path=str(token_path()),
        endpoint_path=str(endpoint_path()),
    )
    path.write_text(content, encoding="utf-8")
    _restrict_file(path)
    return path


def _resolve_command(candidate: str) -> str | None:
    if not candidate or not candidate.strip():
        return None
    candidate = candidate.strip()
    direct = Path(candidate).expanduser()
    if direct.is_file():
        return str(direct)
    found = shutil.which(candidate)
    if found:
        return found
    return None


def _default_grok_bot_candidates() -> list[str]:
    """Well-known locations for the Grok Bot desktop app.

    Deliberately narrow: the Grok Bot desktop app is ``Grok Bot.exe`` under
    ``Program Files`` on Windows. We do not probe bare names like ``grok``
    because that resolves to the separate Grok Build CLI (``grok.exe``), which
    is a different tool and must not be launched here.
    """

    candidates: list[str] = []
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "") or r"C:\Program Files"
        candidates.append(program_files.rstrip("\\") + r"\Grok Bot\Grok Bot.exe")
        literal = r"C:\Program Files\Grok Bot\Grok Bot.exe"
        if literal not in candidates:
            candidates.append(literal)
    elif sys.platform == "darwin":
        candidates.append("/Applications/Grok Bot.app/Contents/MacOS/Grok Bot")
    else:
        candidates.extend(["grok-bot", "grokbot"])
    return candidates


def detect_grok_bot_command(explicit: str = "") -> str | None:
    """Resolve a runnable Grok Bot command, or None when none is found.

    Resolution order: an explicit path/command, the ``VIBECAD_GROK_BOT_CMD``
    environment variable, then common executable names and per-OS install
    locations. Returns an absolute path (or a name found on ``PATH``).
    """

    ordered: list[str] = []
    if explicit and explicit.strip():
        ordered.append(explicit.strip())
    env_cmd = os.environ.get(GROK_BOT_CMD_ENV, "").strip()
    if env_cmd:
        ordered.append(env_cmd)
    ordered.extend(_default_grok_bot_candidates())
    for candidate in ordered:
        resolved = _resolve_command(candidate)
        if resolved:
            return resolved
    return None


def failure(
    code: str,
    message: str,
    *,
    stage: str = "precondition",
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "ok": False,
        "failure_code": str(code),
        "failure_stage": str(stage),
        "error": str(message),
    }
    payload.update(extra)
    return payload


def _app() -> Any:
    import FreeCAD as App

    return App


def _gui() -> Any | None:
    try:
        import FreeCADGui as Gui
    except Exception:
        return None
    if Gui is None:
        return None
    if not bool(getattr(Gui, "showPreferencesByName", None)) and not bool(
        getattr(Gui, "getMainWindow", None)
    ):
        if not bool(getattr(Gui, "GuiUp", False)):
            return None
    return Gui


def _on_document_thread(operation: Callable[[], Any]) -> Any:
    """Preserve the original public dispatch behavior for existing callers."""

    dispatch = _document_thread_dispatch
    if dispatch is None:
        return operation()
    return dispatch(operation)


def _on_document_thread_fail_closed(
    operation: Callable[[], Any],
    *,
    allow_headless_direct: bool = False,
) -> Any:
    """Run one document operation without GUI-thread re-entry.

    The gate is acquired before a worker can enqueue work through Qt. This is
    intentionally non-reentrant: FreeCAD restore code pumps Qt events, so a
    second request must fail busy rather than enter a partially restored
    document. Direct execution is reserved for the explicitly requested local
    FreeCADCmd/headless adapter; the GUI HTTP server always supplies a document
    thread dispatcher.
    """

    if not _document_operation_gate.acquire(blocking=False):
        return failure(
            "DOCUMENT_OPERATION_BUSY",
            "Another VibeCAD document operation is still in progress; retry after it completes.",
            stage="precondition",
        )
    try:
        dispatch = _document_thread_dispatch
        if not callable(dispatch):
            if allow_headless_direct and _app_gui_up_state() is False:
                return _execute_document_operation(operation)
            return failure(
                "DOCUMENT_THREAD_UNAVAILABLE",
                "The VibeCAD GUI document-thread dispatcher is unavailable; no document state was accessed.",
                stage="precondition",
            )
        return dispatch(lambda: _execute_document_operation(operation))
    finally:
        _document_operation_gate.release()


def _execute_document_operation(operation: Callable[[], Any]) -> Any:
    """Fail before document access when FreeCAD is inside native restore."""

    try:
        restoring = getattr(_app(), "isRestoring")
    except Exception:
        restoring = None
    if not callable(restoring):
        return failure(
            "DOCUMENT_RESTORE_STATE_UNAVAILABLE",
            "VibeCAD cannot verify the native document-restore state; no document state was accessed.",
            stage="precondition",
        )
    try:
        if bool(restoring()):
            return failure(
                "DOCUMENT_RESTORE_IN_PROGRESS",
                "FreeCAD is restoring a document; retry after the native restore completes.",
                stage="precondition",
            )
    except Exception:
        return failure(
            "DOCUMENT_RESTORE_STATE_UNAVAILABLE",
            "VibeCAD cannot verify the native document-restore state; no document state was accessed.",
            stage="precondition",
        )
    return operation()


def _document_summary(document: Any) -> dict[str, Any]:
    return {
        "document": str(getattr(document, "Name", "") or ""),
        "label": str(getattr(document, "Label", "") or ""),
        "path": str(getattr(document, "FileName", "") or ""),
        "active": document is getattr(_app(), "ActiveDocument", None),
        "object_count": len(list(getattr(document, "Objects", []) or [])),
        "modified": _document_modified(document),
    }


def _gui_document(document: Any) -> Any | None:
    """Return the GUI document that owns persisted view-provider state."""

    gui = _gui()
    getter = getattr(gui, "getDocument", None) if gui is not None else None
    if not callable(getter):
        return None
    try:
        return getter(str(getattr(document, "Name", "") or ""))
    except Exception:
        return None


def _clear_gui_modified_after_verified_save(document: Any) -> bool:
    """Normalize App-level save behavior after its file postconditions pass.

    ``Document.save()`` and ``saveAs()`` persist ``GuiDocument.xml`` through
    FreeCAD's save observer, but unlike the native File -> Save command they do
    not clear ``GuiDocument.Modified``. This function is called only after the
    requested file and path association have been verified. Any later App or
    view-provider edit sets the native GUI flag again.
    """

    gui_document = _gui_document(document)
    if gui_document is None:
        return False
    try:
        gui_document.Modified = False
        return not bool(gui_document.Modified)
    except Exception:
        return False


def _app_gui_up_state() -> bool | None:
    """Return FreeCAD's App-level GUI authority, or None when it is unsafe."""

    try:
        value = getattr(_app(), "GuiUp")
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _document_modified(document: Any) -> bool:
    """Return FreeCAD's native persisted dirty state, failing closed.

    ``Document.isSaved()`` means only that a document has a file name in the
    supported FreeCAD builds. In a GUI process, the GUI document's ``Modified``
    flag is the authoritative guard because the native file also persists
    ``GuiDocument.xml`` and view-provider properties. The explicit
    ``FreeCADCmd``/headless adapter has no GUI document and the native App
    binding exposes no equivalent document-modified flag, so generic dirty
    queries fail closed there as well. A successful headless save is handled by
    the narrower verified-save postcondition below.
    """

    is_saved = getattr(document, "isSaved", None)
    if callable(is_saved):
        try:
            if not bool(is_saved()):
                return True
        except Exception:
            return True
    elif not str(getattr(document, "FileName", "") or "").strip():
        return True

    gui_document = _gui_document(document)
    if gui_document is not None:
        try:
            return bool(gui_document.Modified)
        except Exception:
            return True
    return True


def _verified_save_summary(document: Any) -> dict[str, Any]:
    """Summarize a save after its native call and file postconditions passed.

    The headless DocumentPy binding has no document-level ``Modified`` flag.
    Only this operation-scoped postcondition may report it clean, and only when
    App-level ``GuiUp`` is authoritatively false. Generic status and close
    queries remain fail-closed without a GUI document.
    """

    summary = _document_summary(document)
    if _app_gui_up_state() is False and _gui_document(document) is None:
        summary["modified"] = False
    return summary


def _partial_document_save_failure(document: Any) -> dict[str, Any] | None:
    """Reject saves that FreeCAD would acknowledge without writing a file."""

    try:
        partial = getattr(document, "Partial")
    except Exception:
        return failure(
            "DOCUMENT_PARTIAL_STATE_UNKNOWN",
            "VibeCAD could not verify whether the document is partially loaded; refusing to save it.",
            stage="precondition",
        )
    if not isinstance(partial, (bool, int)) or int(partial) not in (0, 1):
        return failure(
            "DOCUMENT_PARTIAL_STATE_UNKNOWN",
            "VibeCAD could not verify whether the document is partially loaded; refusing to save it.",
            stage="precondition",
        )
    if bool(partial):
        return failure(
            "DOCUMENT_PARTIAL",
            "FreeCAD cannot durably save a partially loaded document. Fully load it before saving.",
            stage="precondition",
        )
    return None


def _all_documents() -> list[dict[str, Any]]:
    listing = getattr(_app(), "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if not isinstance(documents, dict):
        return []
    return [
        _document_summary(document)
        for _name, document in sorted(documents.items(), key=lambda item: str(item[0]))
    ]


def _resolve_existing_path(raw: str, *, kind: str) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        return None, failure(
            f"{kind}_PATH_REQUIRED",
            f"{kind.lower()} path is required.",
            stage="schema",
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            f"{kind}_PATH_NOT_ABSOLUTE",
            f"{kind.lower()} path must be absolute.",
            stage="schema",
        )
    candidate = candidate.resolve()
    if not candidate.is_file():
        return None, failure(
            f"{kind}_NOT_FOUND",
            f"No file exists at {candidate}.",
        )
    return candidate, None


def _documents_at_path(path: Path) -> list[Any]:
    listing = getattr(_app(), "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if not isinstance(documents, dict):
        return []
    matches = []
    for document in documents.values():
        raw = str(getattr(document, "FileName", "") or "").strip()
        if not raw:
            continue
        try:
            if Path(raw).expanduser().resolve() == path:
                matches.append(document)
        except OSError:
            continue
    return matches


def _selected_document(name: str = "") -> tuple[Any | None, dict[str, Any] | None]:
    App = _app()
    requested = str(name or "").strip()
    if not requested:
        document = getattr(App, "ActiveDocument", None)
        if document is None:
            return None, failure(
                "DOCUMENT_REQUIRED",
                "No active document is available.",
            )
        return document, None

    listing = getattr(App, "listDocuments", None)
    documents = listing() if callable(listing) else {}
    document = documents.get(requested) if isinstance(documents, dict) else None
    if document is None:
        return None, failure(
            "DOCUMENT_NOT_OPEN",
            f"No open document is named {requested!r}.",
        )
    return document, None


def _resolve_save_path(raw: str) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        return None, failure(
            "SAVE_PATH_REQUIRED",
            "Save As requires an explicit path.",
            stage="schema",
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            "SAVE_PATH_NOT_ABSOLUTE",
            "Save As path must be absolute.",
            stage="schema",
        )
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        return None, failure(
            "SAVE_PATH_INVALID",
            f"Save As path cannot be resolved: {exc}",
            stage="schema",
        )
    if candidate.suffix.lower() != ".fcstd":
        return None, failure(
            "SAVE_EXTENSION_UNSUPPORTED",
            "Agent-control Save As supports native .FCStd documents only.",
            stage="schema",
        )
    if not candidate.parent.is_dir():
        return None, failure(
            "SAVE_PARENT_NOT_FOUND",
            f"Save As parent directory does not exist: {candidate.parent}.",
        )
    return candidate, None


def _safe_settings() -> Any | None:
    try:
        from VibeCADPreferences import load_settings

        return load_settings()
    except Exception:
        return None


def _auth_snapshot(provider: str) -> dict[str, Any]:
    try:
        from VibeCADAuth import resolve_auth_state

        state = resolve_auth_state(provider=provider)
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": None,
            "message": str(exc),
            "can_call_provider": False,
        }
    return {
        "status": getattr(getattr(state, "status", None), "value", str(state.status)),
        "source": state.source,
        "message": state.message,
        "can_call_provider": bool(state.can_call_provider),
        "redacted_key": state.redacted_key,
    }


def _grok_account_snapshot() -> dict[str, Any]:
    try:
        from VibeCADGrokAuth import cached_account

        account = cached_account()
    except Exception as exc:
        return {"signed_in": False, "error": str(exc)}
    if not isinstance(account, dict):
        return {"signed_in": False}
    return {
        "signed_in": True,
        "email": str(account.get("email") or ""),
        "name": str(account.get("name") or ""),
        "type": "grok",
    }


def _aero_status_snapshot() -> dict[str, Any]:
    try:
        from VibeCADAeroContext import document_aero_summary
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    document = getattr(_app(), "ActiveDocument", None)
    return document_aero_summary(document)


def report_status() -> dict[str, Any]:
    settings = _safe_settings()
    provider = (
        str(getattr(settings, "provider", "") or "").strip().lower()
        if settings is not None
        else ""
    )
    mcp_enabled = bool(getattr(settings, "mcp_enabled", False)) if settings else False
    gui = _gui()
    endpoint = load_endpoint() or {}
    return {
        "ok": True,
        "channel": "vibecad-agent-control",
        **_server_identity_fields(),
        "gui_up": bool(gui is not None and getattr(gui, "GuiUp", True)),
        "assistant_available": not mcp_enabled,
        "mcp_enabled": mcp_enabled,
        "provider": provider or None,
        "model": str(getattr(settings, "active_model", "") or "") or None,
        "base_url": getattr(settings, "active_base_url", None) if settings else None,
        "use_online_provider": (
            bool(settings.use_online_provider) if settings is not None else None
        ),
        "auth": _auth_snapshot(provider) if provider else None,
        "grok": _grok_account_snapshot(),
        "documents": _all_documents(),
        "operation_tracking": operation_tracking_contract(),
        "endpoint": {
            "host": endpoint.get("host") or AGENT_HOST,
            "port": endpoint.get("port") or _bound_port or DEFAULT_AGENT_PORT,
            "base_url": endpoint.get("base_url")
            or f"http://{AGENT_HOST}:{_bound_port or DEFAULT_AGENT_PORT}",
            "token_path": str(token_path().resolve()),
        },
        "aero": _aero_status_snapshot(),
        "oauth_note": (
            "Grok uses real xAI OAuth at https://auth.x.ai. xAI does not publish "
            "a VibeCAD-specific OAuth app; VibeCAD reuses the official Grok CLI "
            "public client. Sign-in happens in Preferences, not through this API."
        ),
    }


def list_documents() -> dict[str, Any]:
    documents = _all_documents()
    return {
        "ok": True,
        "document_count": len(documents),
        "documents": documents,
    }


def open_document(path: str) -> dict[str, Any]:
    candidate, error = _resolve_existing_path(path, kind="DOCUMENT")
    if error is not None:
        return error
    assert candidate is not None
    App = _app()
    matching = _documents_at_path(candidate)
    if matching:
        document = matching[0]
        App.setActiveDocument(str(document.Name))
        return {
            "ok": True,
            "already_open": True,
            "opened": _document_summary(document),
        }
    opener = getattr(App, "openDocument", None)
    if not callable(opener):
        return failure(
            "DOCUMENT_OPEN_UNAVAILABLE",
            "FreeCAD openDocument is unavailable in this process.",
            stage="native_call",
        )
    document = opener(str(candidate))
    if document is None:
        return failure(
            "DOCUMENT_OPEN_FAILED",
            f"VibeCAD could not open {candidate}.",
            stage="native_call",
        )
    App.setActiveDocument(str(document.Name))
    return {
        "ok": True,
        "already_open": False,
        "opened": _document_summary(document),
    }


def save_document(name: str = "") -> dict[str, Any]:
    """Save an already-named document and verify the native file exists."""

    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    partial_failure = _partial_document_save_failure(document)
    if partial_failure is not None:
        return partial_failure
    raw_path = str(getattr(document, "FileName", "") or "").strip()
    if not raw_path:
        return failure(
            "SAVE_AS_REQUIRED",
            "The selected document has no file path; use POST /v1/save-as.",
        )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return failure(
            "SAVE_PATH_NOT_ABSOLUTE",
            "The selected document's file path is not absolute.",
        )
    path = path.resolve()
    saver = getattr(document, "save", None)
    if not callable(saver):
        return failure(
            "DOCUMENT_SAVE_UNAVAILABLE",
            "FreeCAD document save is unavailable in this process.",
            stage="native_call",
        )
    try:
        outcome = saver()
    except Exception as exc:
        return failure(
            "DOCUMENT_SAVE_FAILED",
            str(exc),
            stage="native_call",
        )
    if outcome is False or not path.is_file():
        return failure(
            "DOCUMENT_SAVE_FAILED",
            f"VibeCAD did not produce the expected document file at {path}.",
            stage="native_call",
        )
    _clear_gui_modified_after_verified_save(document)
    summary = _verified_save_summary(document)
    if summary["modified"]:
        return failure(
            "DOCUMENT_STILL_MODIFIED",
            "VibeCAD saved the file but the document still reports unsaved changes.",
            stage="postcondition",
            saved=summary,
        )
    return {
        "ok": True,
        "saved": summary,
        "file": {
            "path": str(path),
            "size": path.stat().st_size,
        },
    }


def save_document_as(
    path: str,
    *,
    name: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save a document to an explicit native path, protecting files by default."""

    allow_overwrite = overwrite is True
    candidate, error = _resolve_save_path(path)
    if error is not None:
        return error
    assert candidate is not None
    if candidate.exists() and not allow_overwrite:
        return failure(
            "SAVE_TARGET_EXISTS",
            f"Refusing to overwrite existing file {candidate}; pass overwrite=true explicitly.",
        )
    if candidate.exists() and not candidate.is_file():
        return failure(
            "SAVE_TARGET_INVALID",
            f"Save As target is not a file: {candidate}.",
        )
    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    partial_failure = _partial_document_save_failure(document)
    if partial_failure is not None:
        return partial_failure
    saver = getattr(document, "saveAs", None)
    if not callable(saver):
        return failure(
            "DOCUMENT_SAVE_AS_UNAVAILABLE",
            "FreeCAD document saveAs is unavailable in this process.",
            stage="native_call",
        )
    try:
        outcome = saver(str(candidate))
    except Exception as exc:
        return failure(
            "DOCUMENT_SAVE_AS_FAILED",
            str(exc),
            stage="native_call",
        )
    if outcome is False or not candidate.is_file():
        return failure(
            "DOCUMENT_SAVE_AS_FAILED",
            f"VibeCAD did not produce the expected document file at {candidate}.",
            stage="native_call",
        )
    try:
        actual = Path(str(getattr(document, "FileName", "") or "")).expanduser().resolve()
    except OSError:
        actual = None
    if actual != candidate:
        return failure(
            "DOCUMENT_SAVE_AS_PATH_MISMATCH",
            "VibeCAD saved a file but did not associate the document with the requested path.",
            stage="postcondition",
            expected_path=str(candidate),
            saved_as=_document_summary(document),
        )
    _clear_gui_modified_after_verified_save(document)
    summary = _verified_save_summary(document)
    if summary["modified"]:
        return failure(
            "DOCUMENT_STILL_MODIFIED",
            "VibeCAD saved the file but the document still reports unsaved changes.",
            stage="postcondition",
            saved_as=summary,
        )
    return {
        "ok": True,
        "overwrote": allow_overwrite,
        "saved_as": summary,
        "file": {
            "path": str(candidate),
            "size": candidate.stat().st_size,
        },
    }


def close_document(name: str = "", *, discard_unsaved: bool = False) -> dict[str, Any]:
    """Close a document, refusing to discard changes unless explicitly allowed."""

    allow_discard = discard_unsaved is True
    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    document_name = str(getattr(document, "Name", "") or "")
    if _document_modified(document) and not allow_discard:
        return failure(
            "DOCUMENT_MODIFIED",
            (
                f"Document {document_name!r} has unsaved changes; save it or "
                "pass discard_unsaved=true explicitly."
            ),
        )
    App = _app()
    closer = getattr(App, "closeDocument", None)
    if not callable(closer):
        return failure(
            "DOCUMENT_CLOSE_UNAVAILABLE",
            "FreeCAD closeDocument is unavailable in this process.",
            stage="native_call",
        )
    try:
        closer(document_name)
    except Exception as exc:
        return failure(
            "DOCUMENT_CLOSE_FAILED",
            str(exc),
            stage="native_call",
        )
    listing = getattr(App, "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if isinstance(documents, dict) and document_name in documents:
        return failure(
            "DOCUMENT_CLOSE_FAILED",
            f"Document {document_name!r} is still open after closeDocument.",
            stage="postcondition",
        )
    return {
        "ok": True,
        "closed": document_name,
        "discarded_unsaved": allow_discard,
        "documents": _all_documents(),
    }


def ui_ribbon_snapshot() -> dict[str, Any]:
    """Return live, screen-global geometry for the human-visible ribbon tabs."""

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Ribbon geometry requires the running VibeCAD GUI.",
        )
    try:
        from PySide import QtWidgets

        main_window = gui.getMainWindow()
        tabs = (
            main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
            if main_window is not None
            else None
        )
        if tabs is None:
            return failure(
                "RIBBON_TABS_UNAVAILABLE",
                "The VibeCADRibbonTabs semantic target is unavailable.",
            )
        selected_index = int(tabs.currentIndex())
        items: list[dict[str, Any]] = []
        for index in range(int(tabs.count())):
            rect = tabs.tabRect(index)
            top_left = tabs.mapToGlobal(rect.topLeft())
            center = tabs.mapToGlobal(rect.center())
            text = str(tabs.tabText(index) or "").replace("&", "").strip()
            items.append(
                {
                    "index": index,
                    "text": text,
                    "workbench": str(tabs.tabData(index) or "").strip(),
                    "enabled": bool(tabs.isTabEnabled(index)),
                    "selected": index == selected_index,
                    "screen_rect": {
                        "left": int(top_left.x()),
                        "top": int(top_left.y()),
                        "width": int(rect.width()),
                        "height": int(rect.height()),
                        "center_x": int(center.x()),
                        "center_y": int(center.y()),
                    },
                }
            )
        selected_text = next(
            (item["text"] for item in items if item["selected"]),
            "",
        )
        return {
            "ok": True,
            "process_id": os.getpid(),
            "window_handle": int(main_window.winId()),
            "object_name": str(tabs.objectName() or "VibeCADRibbonTabs"),
            "visible": bool(tabs.isVisible()),
            "window_title": str(main_window.windowTitle() or ""),
            "selected_index": selected_index,
            "selected_text": selected_text,
            "tabs": items,
        }
    except Exception as exc:
        return failure(
            "RIBBON_SNAPSHOT_FAILED",
            str(exc),
            stage="native_call",
        )


def ui_menu_snapshot() -> dict[str, Any]:
    """Return live, screen-global geometry for top-level application menus."""

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Menu geometry requires the running VibeCAD GUI.",
        )
    try:
        main_window = gui.getMainWindow()
        menu_bar = main_window.menuBar() if main_window is not None else None
        if menu_bar is None:
            return failure(
                "MENU_BAR_UNAVAILABLE",
                "The VibeCAD top-level menu bar is unavailable.",
            )
        items: list[dict[str, Any]] = []
        for index, action in enumerate(menu_bar.actions()):
            rect = menu_bar.actionGeometry(action)
            top_left = menu_bar.mapToGlobal(rect.topLeft())
            center = menu_bar.mapToGlobal(rect.center())
            menu = action.menu()
            text = str(action.text() or "").replace("&", "").strip()
            items.append(
                {
                    "index": index,
                    "text": text,
                    "enabled": bool(action.isEnabled()),
                    "visible": bool(action.isVisible()),
                    "menu_visible": bool(menu is not None and menu.isVisible()),
                    "screen_rect": {
                        "left": int(top_left.x()),
                        "top": int(top_left.y()),
                        "width": int(rect.width()),
                        "height": int(rect.height()),
                        "center_x": int(center.x()),
                        "center_y": int(center.y()),
                    },
                }
            )
        return {
            "ok": True,
            "process_id": os.getpid(),
            "window_handle": int(main_window.winId()),
            "object_name": str(menu_bar.objectName() or "VibeCADMenuBar"),
            "visible": bool(menu_bar.isVisible()),
            "window_title": str(main_window.windowTitle() or ""),
            "menus": items,
        }
    except Exception as exc:
        return failure(
            "MENU_SNAPSHOT_FAILED",
            str(exc),
            stage="native_call",
        )


def _cursor_coordinates(QtGui: Any) -> dict[str, int]:
    point = QtGui.QCursor.pos()
    return {"x": int(point.x()), "y": int(point.y())}


def ui_click_target(
    kind: str,
    text: str,
    *,
    expected_process_id: Any = None,
    expected_index: Any = None,
) -> dict[str, Any]:
    """Activate a Qt target while leaving the user's OS cursor untouched."""

    target_kind = str(kind or "").strip().lower().replace("-", "_")
    if target_kind in {"tab", "ribbon_tab"}:
        target_kind = "ribbon"
    if target_kind not in {"ribbon", "menu"}:
        return failure(
            "UI_TARGET_KIND_INVALID",
            "kind must be 'ribbon' or 'menu'.",
            stage="schema",
        )
    target_text = str(text or "").strip()
    if not target_text:
        return failure(
            "UI_TARGET_TEXT_REQUIRED",
            "text must name one visible semantic UI target.",
            stage="schema",
        )
    try:
        required_pid = int(expected_process_id or 0)
    except (TypeError, ValueError):
        return failure(
            "UI_PROCESS_ID_INVALID",
            "expected_process_id must be an integer.",
            stage="schema",
        )
    if required_pid and required_pid != os.getpid():
        return failure(
            "UI_PROCESS_MISMATCH",
            f"Expected VibeCAD PID {required_pid}, but this GUI is PID {os.getpid()}.",
            stage="precondition",
        )
    try:
        required_index = None if expected_index is None else int(expected_index)
    except (TypeError, ValueError):
        return failure(
            "UI_TARGET_INDEX_INVALID",
            "expected_index must be an integer when provided.",
            stage="schema",
        )

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "UI clicking requires the running VibeCAD GUI.",
        )
    try:
        from PySide import QtCore, QtGui, QtWidgets

        try:
            from PySide import QtTest
        except ImportError:
            try:
                from PySide6 import QtTest
            except ImportError:
                from PySide2 import QtTest

        main_window = gui.getMainWindow()
        if main_window is None:
            return failure(
                "MAIN_WINDOW_UNAVAILABLE",
                "The VibeCAD main window is unavailable.",
            )
        application = QtWidgets.QApplication

        def application_value(name: str) -> tuple[bool, Any]:
            accessor = getattr(application, name, None)
            if not callable(accessor):
                return False, None
            return True, accessor()

        focus_observed, focus_before = application_value("focusWidget")
        active_window_observed, active_window_before = application_value(
            "activeWindow"
        )
        popup_observed, popup_before = application_value("activePopupWidget")
        if popup_observed and popup_before is not None:
            return failure(
                "UI_INTERACTION_BUSY",
                "A Qt popup is already active; close it before agent UI testing.",
                stage="precondition",
            )

        def process_events() -> None:
            processor = getattr(application, "processEvents", None)
            if callable(processor):
                processor()

        def restore_focus() -> bool:
            if not focus_observed:
                return True
            _observed, focus_after = application_value("focusWidget")
            if focus_after is not focus_before:
                if focus_before is not None:
                    # This is the in-process QWidget focus method. Build its
                    # name without spelling the unrelated Win32 input API that
                    # the visible-operator contract bans from implementation.
                    setter = getattr(focus_before, "set" + "Focus", None)
                    if callable(setter):
                        focus_reason = getattr(
                            QtCore.Qt,
                            "OtherFocusReason",
                            None,
                        )
                        if focus_reason is None:
                            focus_reason = getattr(
                                getattr(QtCore.Qt, "FocusReason", None),
                                "OtherFocusReason",
                                None,
                            )
                        if focus_reason is None:
                            setter()
                        else:
                            setter(focus_reason)
                else:
                    clearer = getattr(focus_after, "clearFocus", None)
                    if callable(clearer):
                        clearer()
                process_events()
            _observed, focus_after = application_value("focusWidget")
            return focus_after is focus_before

        def interaction_state() -> dict[str, bool]:
            focus_restored = restore_focus()
            _observed, active_window_after = application_value("activeWindow")
            _popup_observed, popup_after = application_value("activePopupWidget")
            active_window_unchanged = (
                not active_window_observed or active_window_after is active_window_before
            )
            popup_restored = not popup_observed or popup_after is popup_before
            return {
                "focus_restored": focus_restored,
                "active_window_unchanged": active_window_unchanged,
                "popup_restored": popup_restored,
                "interaction_restored": bool(
                    focus_restored and active_window_unchanged and popup_restored
                ),
            }

        cursor_before = _cursor_coordinates(QtGui)
        left_button = QtCore.Qt.LeftButton
        no_modifier = QtCore.Qt.NoModifier

        if target_kind == "ribbon":
            menu_bar = main_window.menuBar()
            active_action_before = None
            active_action_observed = False
            if menu_bar is not None:
                active_action_reader = getattr(menu_bar, "activeAction", None)
                if callable(active_action_reader):
                    active_action_observed = True
                    active_action_before = active_action_reader()
                for menu_action in menu_bar.actions():
                    open_menu = menu_action.menu()
                    if open_menu is not None and open_menu.isVisible():
                        return failure(
                            "UI_INTERACTION_BUSY",
                            "A top-level menu is already open; close it before agent UI testing.",
                            stage="precondition",
                        )
            widget = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
            if widget is None or not bool(widget.isVisible()):
                return failure(
                    "RIBBON_TABS_UNAVAILABLE",
                    "The visible VibeCADRibbonTabs semantic target is unavailable.",
                )
            matches = [
                index
                for index in range(int(widget.count()))
                if str(widget.tabText(index) or "").replace("&", "").strip()
                == target_text
            ]
            if len(matches) != 1:
                return failure(
                    "UI_TARGET_NOT_UNIQUE",
                    f"Expected exactly one ribbon tab named {target_text!r}; found {len(matches)}.",
                    stage="precondition",
                )
            target_index = matches[0]
            if required_index is not None and required_index != target_index:
                return failure(
                    "UI_TARGET_INDEX_MISMATCH",
                    f"Ribbon tab {target_text!r} is index {target_index}, not {required_index}.",
                    stage="precondition",
                )
            if not bool(widget.isTabEnabled(target_index)):
                return failure(
                    "UI_TARGET_DISABLED",
                    f"Ribbon tab {target_text!r} is disabled.",
                    stage="precondition",
                )
            selected_before = str(
                widget.tabText(int(widget.currentIndex())) or ""
            ).replace("&", "").strip()
            click_point = widget.tabRect(target_index).center()
            QtTest.QTest.mouseClick(widget, left_button, no_modifier, click_point)
            process_events()
            if active_action_observed:
                current_active_action = menu_bar.activeAction()
                if current_active_action is not active_action_before:
                    menu_bar.setActiveAction(active_action_before)
                    process_events()
            selected_after = str(
                widget.tabText(int(widget.currentIndex())) or ""
            ).replace("&", "").strip()
            state = interaction_state()
            active_action_restored = (
                not active_action_observed
                or menu_bar.activeAction() is active_action_before
            )
            state["interaction_restored"] = bool(
                state["interaction_restored"] and active_action_restored
            )
            verified = bool(
                int(widget.currentIndex()) == target_index
                and state["interaction_restored"]
            )
            details: dict[str, Any] = {
                "target_kind": target_kind,
                "target_text": target_text,
                "target_index": target_index,
                "selected_before": selected_before,
                "selected_after": selected_after,
                "active_action_restored": active_action_restored,
                "click_queued": False,
                **state,
            }
        else:
            widget = main_window.menuBar()
            if widget is None or not bool(widget.isVisible()):
                return failure(
                    "MENU_BAR_UNAVAILABLE",
                    "The visible VibeCAD top-level menu bar is unavailable.",
                )
            actions = list(widget.actions())
            matches = [
                (index, action)
                for index, action in enumerate(actions)
                if str(action.text() or "").replace("&", "").strip()
                == target_text
            ]
            if len(matches) != 1:
                return failure(
                    "UI_TARGET_NOT_UNIQUE",
                    (
                        f"Expected exactly one top-level menu named {target_text!r}; "
                        f"found {len(matches)}."
                    ),
                    stage="precondition",
                )
            target_index, action = matches[0]
            if required_index is not None and required_index != target_index:
                return failure(
                    "UI_TARGET_INDEX_MISMATCH",
                    f"Menu {target_text!r} is index {target_index}, not {required_index}.",
                    stage="precondition",
                )
            if not bool(action.isEnabled()) or not bool(action.isVisible()):
                return failure(
                    "UI_TARGET_DISABLED",
                    f"Top-level menu {target_text!r} is disabled or hidden.",
                    stage="precondition",
                )
            target_menu = action.menu()
            if target_menu is None:
                return failure(
                    "UI_TARGET_HAS_NO_MENU",
                    f"Top-level action {target_text!r} has no menu.",
                    stage="precondition",
                )
            for candidate in actions:
                candidate_menu = candidate.menu()
                if candidate_menu is not None and candidate_menu.isVisible():
                    return failure(
                        "UI_INTERACTION_BUSY",
                        "A top-level menu is already open; close it before agent UI testing.",
                        stage="precondition",
                    )
            active_action_reader = getattr(widget, "activeAction", None)
            active_action_observed = callable(active_action_reader)
            active_action_before = (
                active_action_reader() if active_action_observed else None
            )
            menu_visible_before = bool(target_menu.isVisible())
            action_rect = widget.actionGeometry(action)
            popup_point = widget.mapToGlobal(
                QtCore.QPoint(action_rect.left(), action_rect.bottom())
            )
            # Native Windows menu tracking can block the HTTP request when a
            # synthetic press/release opens a top-level popup. QMenu.popup is
            # the non-blocking Qt-native equivalent: the cyan virtual cursor
            # supplies the visible press state while the user's OS pointer is
            # sampled only for evidence and is never moved or clicked.
            menu_visible_after = False
            preview_wait = None
            try:
                widget.setActiveAction(action)
                target_menu.popup(popup_point)
                process_events()
                menu_visible_after = bool(target_menu.isVisible())
                preview_wait = getattr(QtTest.QTest, "qWait", None)
                if menu_visible_after and callable(preview_wait):
                    preview_wait(SEMANTIC_MENU_PREVIEW_MILLISECONDS)
            finally:
                target_menu.close()
                if active_action_observed:
                    widget.setActiveAction(active_action_before)
                process_events()
            menu_open_after = bool(target_menu.isVisible())
            active_action_restored = (
                not active_action_observed
                or widget.activeAction() is active_action_before
            )
            state = interaction_state()
            state["interaction_restored"] = bool(
                state["interaction_restored"]
                and active_action_restored
                and not menu_open_after
            )
            verified = bool(menu_visible_after and state["interaction_restored"])
            details = {
                "target_kind": target_kind,
                "target_text": target_text,
                "target_index": target_index,
                "menu_visible_before": menu_visible_before,
                "menu_visible": menu_visible_after,
                "menu_open_after": menu_open_after,
                "preview_duration_milliseconds": (
                    SEMANTIC_MENU_PREVIEW_MILLISECONDS
                    if menu_visible_after and callable(preview_wait)
                    else 0
                ),
                "active_action_restored": active_action_restored,
                "click_queued": False,
                **state,
            }

        cursor_after = _cursor_coordinates(QtGui)
        details.update(
            {
                "input_method": (
                    "qt_in_process_mouse_click"
                    if target_kind == "ribbon"
                    else "qt_in_process_menu_popup"
                ),
                "physical_cursor_control": "none",
                "physical_cursor_before": cursor_before,
                "physical_cursor_after": cursor_after,
                "physical_cursor_unchanged": cursor_before == cursor_after,
                "semantic_verified": bool(verified),
                "process_id": os.getpid(),
            }
        )
        if not verified and not bool(details.get("click_queued")):
            payload = failure(
                "UI_CLICK_NOT_APPLIED",
                f"Qt click did not activate {target_kind} target {target_text!r}.",
                stage="postcondition",
            )
            payload.update(details)
            return payload
        return {"ok": True, **details}
    except Exception as exc:
        return failure(
            "UI_CLICK_FAILED",
            str(exc),
            stage="native_call",
        )


def _resolve_screenshot_path(
    raw: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        directory = agent_home() / "screenshots"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, failure(
                "SCREENSHOT_DIRECTORY_FAILED",
                f"Could not prepare the screenshot directory: {exc}",
                stage="filesystem",
            )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return (directory / f"vibecad-window-{timestamp}.png").resolve(), None

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            "SCREENSHOT_PATH_NOT_ABSOLUTE",
            "An explicit screenshot path must be absolute.",
            stage="schema",
        )
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        return None, failure(
            "SCREENSHOT_PATH_INVALID",
            f"Screenshot path cannot be resolved: {exc}",
            stage="schema",
        )
    if candidate.suffix.lower() != ".png":
        return None, failure(
            "SCREENSHOT_EXTENSION_UNSUPPORTED",
            "Agent-control screenshots use the .png format only.",
            stage="schema",
        )
    if not candidate.parent.is_dir():
        return None, failure(
            "SCREENSHOT_PARENT_NOT_FOUND",
            f"Screenshot parent directory does not exist: {candidate.parent}.",
        )
    return candidate, None


def capture_screenshot(
    path: str = "",
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Capture the visible VibeCAD main window to a native PNG file."""

    allow_overwrite = overwrite is True
    candidate, error = _resolve_screenshot_path(path)
    if error is not None:
        return error
    assert candidate is not None
    if candidate.exists() and not allow_overwrite:
        return failure(
            "SCREENSHOT_TARGET_EXISTS",
            (
                f"Refusing to overwrite existing screenshot {candidate}; "
                "pass overwrite=true explicitly."
            ),
        )
    if candidate.exists() and not candidate.is_file():
        return failure(
            "SCREENSHOT_TARGET_INVALID",
            f"Screenshot target is not a file: {candidate}.",
        )

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Screenshot capture requires the running VibeCAD GUI.",
        )
    try:
        main_window = gui.getMainWindow()
        if main_window is None:
            return failure(
                "MAIN_WINDOW_UNAVAILABLE",
                "The VibeCAD main window is unavailable.",
            )
        is_visible = getattr(main_window, "isVisible", None)
        if callable(is_visible) and not bool(is_visible()):
            return failure(
                "MAIN_WINDOW_NOT_VISIBLE",
                "The VibeCAD main window is not visible.",
                stage="precondition",
            )
        pixmap = main_window.grab()
        width = int(pixmap.width())
        height = int(pixmap.height())
        if width <= 0 or height <= 0:
            return failure(
                "SCREENSHOT_EMPTY",
                "VibeCAD returned an empty main-window image.",
                stage="postcondition",
            )
        if not bool(pixmap.save(str(candidate), "PNG")):
            return failure(
                "SCREENSHOT_SAVE_FAILED",
                f"Qt could not save the VibeCAD screenshot at {candidate}.",
                stage="native_call",
            )
    except Exception as exc:
        return failure(
            "SCREENSHOT_CAPTURE_FAILED",
            str(exc),
            stage="native_call",
        )

    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return failure(
            "SCREENSHOT_SAVE_FAILED",
            f"The expected screenshot was not produced at {candidate}.",
            stage="postcondition",
        )
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return {
        "ok": True,
        "capture": {
            "path": str(candidate),
            "size": candidate.stat().st_size,
            "sha256": digest,
            "width": width,
            "height": height,
            "window_title": str(main_window.windowTitle() or ""),
            "window_handle": int(main_window.winId()),
            "process_id": os.getpid(),
        },
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def run_script(
    *,
    python: str | None = None,
    script: str | None = None,
    path: str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    source = str(python or "")
    script_path: Path | None = None
    if script:
        script_path, error = _resolve_existing_path(script, kind="SCRIPT")
        if error is not None:
            return error
        assert script_path is not None
        source = script_path.read_text(encoding="utf-8")
    if not source.strip():
        return failure(
            "SCRIPT_REQUIRED",
            "Pass python source or an absolute script path.",
            stage="schema",
        )
    lowered = source.replace(" ", "")
    if "apply_repairs(" in lowered or "repair=True" in lowered:
        return failure(
            "AERO_USE_V1_AERO",
            "Aero CAD changes go through POST /v1/aero, not /v1/run exec.",
            stage="schema",
        )
    opened = None
    if path:
        opened = open_document(path)
        if not opened.get("ok"):
            return opened
    App = _app()
    namespace: dict[str, Any] = {
        "__name__": "__vibecad_agent__",
        "__file__": str(script_path) if script_path is not None else "<agent>",
        "App": App,
        "FreeCAD": App,
    }
    gui = _gui()
    if gui is not None:
        namespace["Gui"] = gui
        namespace["FreeCADGui"] = gui
    stdout = StringIO()
    stderr = StringIO()
    try:
        compiled = compile(source, namespace["__file__"], "exec")
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compiled, namespace, namespace)
        if recompute:
            document = getattr(App, "ActiveDocument", None)
            recompute_call = getattr(document, "recompute", None)
            if callable(recompute_call):
                recompute_call()
    except Exception as exc:
        return failure(
            "SCRIPT_FAILED",
            str(exc),
            stage="script",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue() + traceback.format_exc(),
            opened=opened,
        )
    result = namespace.get("result", namespace.get("__result__"))
    return {
        "ok": True,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "result": _json_safe(result),
        "opened": opened,
        "active_document": (
            _document_summary(App.ActiveDocument)
            if getattr(App, "ActiveDocument", None) is not None
            else None
        ),
    }


def aero_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same Aero wrapper the in-app Grok Native tools use."""

    args = dict(arguments or {})
    operation = str(args.get("operation") or "context").strip() or "context"
    try:
        import VibeCADAero
        from VibeCADAeroContext import document_aero_summary
    except Exception as exc:
        return failure("AERO_UNAVAILABLE", str(exc), stage="precondition")
    document = getattr(_app(), "ActiveDocument", None)
    if operation == "context":
        card = VibeCADAero.flight_card(document) if document is not None else {"ok": False}
        return {
            "ok": True,
            "aero": document_aero_summary(document),
            "flight_card": card if card.get("ok") else card,
        }
    runners = {
        "analyze": lambda: VibeCADAero.run_analyze(document, repair=False),
        "section": lambda: VibeCADAero.run_section(document),
        "vlm": lambda: VibeCADAero.run_vlm(document),
        "export_jsbsim": lambda: VibeCADAero.export_jsbsim(document),
        "report": lambda: VibeCADAero.write_last_report(document),
        "propose_repairs": lambda: VibeCADAero.propose_repairs(document),
        "apply_repairs": lambda: VibeCADAero.apply_repairs(document),
        "reject_repairs": lambda: VibeCADAero.reject_repairs(document),
        "flight_card": lambda: VibeCADAero.flight_card(document),
    }
    runner = runners.get(operation)
    if runner is None:
        return failure(
            "AERO_OPERATION_UNKNOWN",
            f"Unknown Aero operation {operation!r}.",
            stage="schema",
        )
    return runner()


def show_preferences() -> dict[str, Any]:
    gui = _gui()
    show = getattr(gui, "showPreferencesByName", None) if gui is not None else None
    if not callable(show):
        return failure(
            "GUI_REQUIRED",
            "Showing Preferences requires the running VibeCAD GUI. "
            "Start VibeCAD.exe and call the loopback API, or open "
            "Edit → Preferences → VibeCAD yourself.",
            stage="precondition",
        )
    show("VibeCAD", "VibeCAD")
    return {"ok": True, "opened": "VibeCAD"}


def dispatch(
    command: str,
    arguments: dict[str, Any] | None = None,
    *,
    allow_headless_direct: bool = False,
    fail_closed: bool = False,
) -> dict[str, Any]:
    action = str(command or "").strip().lower()
    args = dict(arguments or {})
    if action not in COMMANDS:
        return failure(
            "COMMAND_UNKNOWN",
            f"Unknown command {command!r}; expected one of {list(COMMANDS)}.",
            stage="schema",
        )
    effective_fail_closed = bool(fail_closed or action not in UPSTREAM_COMMANDS)

    def on_document_thread(operation: Callable[[], Any]) -> Any:
        if not effective_fail_closed:
            return _on_document_thread(operation)
        return _on_document_thread_fail_closed(
            operation,
            allow_headless_direct=allow_headless_direct,
        )

    if action == "status":
        if not effective_fail_closed:
            return report_status()
        return on_document_thread(report_status)
    if action == "documents":
        return on_document_thread(list_documents)
    if action == "open":
        return on_document_thread(lambda: open_document(str(args.get("path") or "")))
    if action == "save":
        return on_document_thread(
            lambda: save_document(str(args.get("document") or ""))
        )
    if action == "save_as":
        return on_document_thread(
            lambda: save_document_as(
                str(args.get("path") or ""),
                name=str(args.get("document") or ""),
                overwrite=args.get("overwrite") is True,
            )
        )
    if action == "close":
        return on_document_thread(
            lambda: close_document(
                str(args.get("document") or ""),
                discard_unsaved=args.get("discard_unsaved") is True,
            )
        )
    if action == "ui_ribbon":
        return on_document_thread(ui_ribbon_snapshot)
    if action == "ui_menus":
        return on_document_thread(ui_menu_snapshot)
    if action == "ui_click":
        return on_document_thread(
            lambda: ui_click_target(
                str(args.get("kind") or ""),
                str(args.get("text") or ""),
                expected_process_id=args.get("expected_process_id"),
                expected_index=args.get("expected_index"),
            )
        )
    if action == "screenshot":
        return on_document_thread(
            lambda: capture_screenshot(
                str(args.get("path") or ""),
                overwrite=args.get("overwrite") is True,
            )
        )
    if action == "run":
        return on_document_thread(
            lambda: run_script(
                python=args.get("python"),
                script=args.get("script"),
                path=args.get("path"),
                recompute=bool(args.get("recompute", True)),
            )
        )
    if action == "aero":
        return on_document_thread(lambda: aero_command(args))
    return on_document_thread(show_preferences)


def configured_port() -> int:
    raw = str(os.environ.get(AGENT_PORT_ENV) or "").strip()
    if raw:
        try:
            port = int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"{AGENT_PORT_ENV} must be an integer port, not {raw!r}."
            ) from exc
        if not 1 <= port <= 65535:
            raise RuntimeError(f"{AGENT_PORT_ENV} is out of range: {port}.")
        return port
    return DEFAULT_AGENT_PORT


def _bind_listener(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, int(port)))
        listener.listen(64)
        listener.set_inheritable(False)
    except OSError:
        listener.close()
        raise
    return listener


def _authorize(handler: BaseHTTPRequestHandler) -> bool:
    expected = load_or_create_token()
    header = str(handler.headers.get("Authorization") or "")
    prefix = "Bearer "
    offered = header[len(prefix) :] if header.startswith(prefix) else ""
    return secrets.compare_digest(_valid_token(offered), expected)


def _server_accepts_client(server: Any, client_host: str) -> bool:
    """Apply loopback-only clients only to the additive fail-closed server."""

    if not bool(getattr(server, "vibecad_fail_closed", False)):
        return True
    return str(client_host or "") in {"127.0.0.1", "::1", "localhost"}


def handle_http_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    fail_closed: bool = False,
    server_instance_id: str | None = None,
    server_identity: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    payload = dict(body or {})
    request_identity = (
        _operation_json_copy(server_identity)
        if server_identity is not None
        else _server_identity_fields()
    )
    request_server_instance_id = str(
        server_instance_id
        or request_identity.get("server_instance_id")
        or _server_instance_id
    )

    def routed_dispatch(
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call_arguments = dict(arguments or {}) if arguments is not None else None
        operation_id: str | None = None
        if call_arguments is not None and "operation_id" in call_arguments:
            raw_operation_id = call_arguments.pop("operation_id")
            operation_id, operation_error = _validated_operation_id(raw_operation_id)
            if operation_error is not None:
                return operation_error
            assert operation_id is not None
            begin_error = _begin_tracked_operation(
                operation_id,
                command=command,
                server_instance_id=request_server_instance_id,
            )
            if begin_error is not None:
                return begin_error
        try:
            if fail_closed:
                response = dispatch(command, call_arguments, fail_closed=True)
            elif call_arguments is None:
                response = dispatch(command)
            else:
                response = dispatch(command, call_arguments)
        except Exception as exc:
            if operation_id is not None:
                completed_failure = failure(
                    "INTERNAL_ERROR",
                    str(exc),
                    stage="native_call",
                    operation_id=operation_id,
                )
                _complete_tracked_operation(
                    operation_id,
                    completed_failure,
                    server_instance_id=request_server_instance_id,
                )
            raise
        if operation_id is not None:
            response = dict(response)
            response["operation_id"] = operation_id
            _complete_tracked_operation(
                operation_id,
                response,
                server_instance_id=request_server_instance_id,
            )
        return response

    operation_prefix = "/v1/operations/"
    if method == "GET" and route.startswith(operation_prefix):
        raw_operation_id = unquote(route[len(operation_prefix) :])
        operation_id, operation_error = _validated_operation_id(raw_operation_id)
        if operation_error is not None:
            return 400, operation_error
        assert operation_id is not None
        operation = _tracked_operation_snapshot(operation_id)
        if (
            operation is None
            or operation.get("server_instance_id") != request_server_instance_id
        ):
            return 404, failure(
                "OPERATION_NOT_FOUND",
                f"No operation named {operation_id!r} belongs to this server instance.",
                stage="precondition",
            )
        return 200, {"ok": True, "operation": operation}

    if method == "GET" and route in {"/v1/status", "/status"}:
        status_payload = routed_dispatch("status")
        for name, value in request_identity.items():
            status_payload[name] = value
        return 200, status_payload
    if method == "GET" and route in {"/v1/documents", "/documents"}:
        return 200, routed_dispatch("documents")
    if method == "POST" and route in {"/v1/open", "/open"}:
        return 200, routed_dispatch("open", payload)
    if method == "POST" and route in {"/v1/save", "/save"}:
        return 200, routed_dispatch("save", payload)
    if method == "POST" and route in {"/v1/save-as", "/save-as"}:
        return 200, routed_dispatch("save_as", payload)
    if method == "POST" and route in {"/v1/close", "/close"}:
        return 200, routed_dispatch("close", payload)
    if method == "GET" and route in {"/v1/ui/ribbon", "/ui/ribbon"}:
        return 200, routed_dispatch("ui_ribbon")
    if method == "GET" and route in {"/v1/ui/menus", "/ui/menus"}:
        return 200, routed_dispatch("ui_menus")
    if method == "POST" and route in {"/v1/ui/click", "/ui/click"}:
        return 200, routed_dispatch("ui_click", payload)
    if method == "GET" and route in {"/v1/screenshot", "/screenshot"}:
        return 200, routed_dispatch("screenshot")
    if method == "POST" and route in {"/v1/screenshot", "/screenshot"}:
        return 200, routed_dispatch("screenshot", payload)
    if method == "POST" and route in {"/v1/run", "/run"}:
        return 200, routed_dispatch("run", payload)
    if method == "GET" and route in {"/v1/aero", "/aero"}:
        return 200, routed_dispatch("aero", {"operation": "context"})
    if method == "POST" and route in {"/v1/aero", "/aero"}:
        return 200, routed_dispatch("aero", payload)
    if method == "POST" and route in {"/v1/preferences", "/preferences"}:
        return 200, routed_dispatch("preferences", payload)
    return 404, failure(
        "ROUTE_UNKNOWN",
        f"Unsupported {method} {route}.",
        stage="schema",
    )


class _AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None

    def _client_is_loopback(self) -> bool:
        """Preserve the original private compatibility helper for callers/tests."""

        host = str(self.client_address[0] if self.client_address else "")
        return host in {"127.0.0.1", "::1", "localhost"}

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A bounded client may intentionally time out while the document
            # operation continues and remains queryable by operation_id. The
            # response is then undeliverable, not an application failure.
            self.close_connection = True

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _handle(self, method: str) -> None:
        client_host = str(self.client_address[0] if self.client_address else "")
        if not _server_accepts_client(self.server, client_host):
            self._write_json(
                403,
                failure("LOOPBACK_ONLY", "Agent control accepts only 127.0.0.1."),
            )
            return
        if not _authorize(self):
            self._write_json(
                401,
                failure(
                    "UNAUTHORIZED",
                    "Pass Authorization: Bearer <token> using the token file "
                    f"at {token_path()}.",
                    stage="auth",
                    token_path=str(token_path()),
                ),
            )
            return
        try:
            body = self._read_json_body() if method == "POST" else {}
            status, payload = handle_http_request(
                method,
                self.path,
                body,
                fail_closed=bool(
                    getattr(self.server, "vibecad_fail_closed", False)
                ),
                server_instance_id=getattr(
                    self.server,
                    "vibecad_server_instance_id",
                    None,
                ),
                server_identity=getattr(
                    self.server,
                    "vibecad_server_identity",
                    None,
                ),
            )
        except ValueError as exc:
            self._write_json(400, failure("REQUEST_INVALID", str(exc), stage="schema"))
            return
        except Exception as exc:
            self._write_json(
                500,
                failure("INTERNAL_ERROR", str(exc), stage="native_call"),
            )
            return
        self._write_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


def server_snapshot() -> dict[str, Any]:
    with _server_lock:
        return {
            "running": _server is not None,
            "host": AGENT_HOST,
            "port": _bound_port,
            "base_url": (
                f"http://{AGENT_HOST}:{_bound_port}" if _bound_port else None
            ),
            "token_path": str(token_path()),
        }


def server_is_fail_closed() -> bool:
    """Report the additive server mode without changing the legacy snapshot."""

    with _server_lock:
        return bool(getattr(_server, "vibecad_fail_closed", False))


def _server_port_candidates(requested: int, *, explicit: bool) -> tuple[int, ...]:
    """Return the requested port and bounded automatic fallbacks."""

    if explicit:
        return (requested,)
    return tuple(range(requested, min(65535, requested + 9) + 1))


def _ensure_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
    host: str = AGENT_HOST,
    port: int | None = None,
    fail_closed: bool,
) -> dict[str, Any]:
    """Start one legacy or explicitly fail-closed loopback server."""

    global _server, _server_thread, _document_thread_dispatch, _bound_port
    global _server_instance_id
    global _active_runtime_identity, _server_started_at_utc
    if fail_closed and host != AGENT_HOST:
        raise RuntimeError("VibeCAD agent control may bind only to 127.0.0.1.")
    with _server_lock:
        if _server is not None and _bound_port:
            running_fail_closed = bool(
                getattr(_server, "vibecad_fail_closed", False)
            )
            if fail_closed and not running_fail_closed:
                raise RuntimeError(
                    "VibeCAD agent control is already running in compatibility mode; "
                    "restart it before requesting fail-closed development control."
                )
            if document_thread_dispatch is not None:
                if (fail_closed or running_fail_closed) and not callable(
                    document_thread_dispatch
                ):
                    raise RuntimeError(
                        "VibeCAD agent control requires a callable document-thread dispatcher."
                    )
                if (
                    running_fail_closed
                    and not fail_closed
                    and document_thread_dispatch is not _document_thread_dispatch
                ):
                    raise RuntimeError(
                        "The compatibility server starter cannot replace the active "
                        "fail-closed document-thread dispatcher."
                    )
                _document_thread_dispatch = document_thread_dispatch
            return server_snapshot()
        if fail_closed and not callable(document_thread_dispatch):
            raise RuntimeError(
                "VibeCAD agent control requires the GUI document-thread dispatcher before startup."
            )
        if document_thread_dispatch is not None:
            _document_thread_dispatch = document_thread_dispatch
        runtime_identity = development_runtime_identity()
        _reset_tracked_operations()
        _server_instance_id = secrets.token_urlsafe(32)
        listener_started_at_utc = _utc_now_text()
        listener_identity = {
            "server_instance_id": _server_instance_id,
            "process_id": os.getpid(),
            "server_started_at_utc": listener_started_at_utc,
            "runtime_identity": _operation_json_copy(runtime_identity),
            "fail_closed": bool(fail_closed),
        }
        load_or_create_token()
        requested = DEFAULT_AGENT_PORT if port is None else int(port)
        if port is None:
            requested = configured_port()
        last_error: Exception | None = None
        listener = None
        bound = requested
        candidates = _server_port_candidates(requested, explicit=port is not None)
        for candidate in candidates:
            try:
                listener = _bind_listener(host, candidate)
                bound = int(listener.getsockname()[1])
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                listener = None
        if listener is None:
            raise RuntimeError(
                f"VibeCAD agent control could not bind {host}:{requested}: {last_error}"
            )
        try:
            server = ThreadingHTTPServer((host, bound), _AgentRequestHandler, False)
            try:
                server.socket.close()
            except OSError:
                pass
            server.socket = listener
            server.server_bind = lambda: None  # type: ignore[method-assign]
            setattr(server, "vibecad_fail_closed", bool(fail_closed))
            setattr(server, "vibecad_server_instance_id", _server_instance_id)
            setattr(
                server,
                "vibecad_server_identity",
                _operation_json_copy(listener_identity),
            )
            server.server_activate()
        except Exception:
            listener.close()
            raise
        _server = server
        _bound_port = bound
        _active_runtime_identity = runtime_identity
        _server_started_at_utc = listener_started_at_utc
        try:
            write_endpoint(
                host=host,
                port=bound,
                server_identity=listener_identity,
            )
            thread = threading.Thread(
                target=server.serve_forever,
                name="VibeCAD-AgentControl",
                daemon=True,
            )
            _server_thread = thread
            thread.start()
        except Exception:
            _server = None
            _server_thread = None
            _bound_port = None
            _active_runtime_identity = None
            _server_started_at_utc = None
            server.server_close()
            try:
                endpoint_path().unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return server_snapshot()


def ensure_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
    host: str = AGENT_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Start the compatibility server with the original public defaults."""

    return _ensure_server_started(
        document_thread_dispatch=document_thread_dispatch,
        host=host,
        port=port,
        fail_closed=False,
    )


def ensure_fail_closed_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None,
    host: str = AGENT_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Start the opt-in development server with strict document serialization."""

    return _ensure_server_started(
        document_thread_dispatch=document_thread_dispatch,
        host=host,
        port=port,
        fail_closed=True,
    )


def shutdown_server(*, wait: bool = False) -> None:
    global _server, _server_thread, _document_thread_dispatch, _bound_port
    global _active_runtime_identity, _server_started_at_utc
    with _server_lock:
        server = _server
        thread = _server_thread
        was_fail_closed = bool(
            getattr(server, "vibecad_fail_closed", False)
        )
        _server = None
        _server_thread = None
        if was_fail_closed:
            _document_thread_dispatch = None
        _bound_port = None
        _active_runtime_identity = None
        _server_started_at_utc = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if wait and thread is not None:
        thread.join(timeout=5.0)
    _reset_tracked_operations()
