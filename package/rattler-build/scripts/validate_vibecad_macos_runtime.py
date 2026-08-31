#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib
import importlib.util
from pathlib import Path
import sys
import traceback
from typing import Callable


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one named VibeCAD macOS runtime validation."
    )
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--check", required=True)
    return parser.parse_args()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_bundle_module(module_name: str, prefix: Path):
    module = importlib.import_module(module_name)
    origin_value = getattr(module, "__file__", None)
    if not origin_value:
        raise RuntimeError(f"{module_name} has no import origin.")
    origin = Path(origin_value).resolve()
    if not _is_relative_to(origin, prefix):
        raise RuntimeError(
            f"{module_name} loaded from outside the app bundle: {origin}; "
            f"expected a path below {prefix}."
        )
    print(f"{module_name}: {origin}", flush=True)
    return module


def _check_python(prefix: Path) -> None:
    actual_prefix = Path(sys.prefix).resolve()
    if actual_prefix != prefix:
        raise RuntimeError(
            f"Python prefix is {actual_prefix}; expected bundled prefix {prefix}."
        )
    executable = Path(sys.executable).resolve()
    if not _is_relative_to(executable, prefix):
        raise RuntimeError(
            f"Python host is outside the app bundle: {executable}; expected below {prefix}."
        )
    print(
        f"python: version={sys.version.split()[0]} prefix={actual_prefix} "
        f"executable={executable}",
        flush=True,
    )


def _check_module(module_name: str) -> Callable[[Path], None]:
    def check(prefix: Path) -> None:
        _require_bundle_module(module_name, prefix)

    return check


def _check_macos_keyring(prefix: Path) -> None:
    _require_bundle_module("keyring", prefix)
    backend = _require_bundle_module("keyring.backends.macOS", prefix)
    priority = backend.Keyring.priority
    if priority <= 0:
        raise RuntimeError(f"macOS Keychain backend priority is not usable: {priority!r}")
    print(f"macOS Keychain backend priority: {priority}", flush=True)


def _check_gemini_sdk(prefix: Path) -> None:
    _require_bundle_module("openai", prefix)
    module_name = "agents"
    if importlib.util.find_spec(module_name) is not None:
        raise RuntimeError(
            f"The retired OpenAI Agents module {module_name!r} is present."
        )
    print("Gemini OpenAI-compatible SDK: available", flush=True)


def _check_pivy(prefix: Path) -> None:
    _require_bundle_module("pivy", prefix)
    _require_bundle_module("pivy.coin", prefix)


def _check_provider_subprocess(prefix: Path) -> None:
    module = _require_bundle_module("VibeCADProvider", prefix)
    context = module._provider_multiprocessing_context()
    if context.get_start_method() != "spawn":
        raise RuntimeError(
            f"macOS provider multiprocessing method is {context.get_start_method()!r}; "
            "expected 'spawn'."
        )
    module._provider_subprocess_smoke()
    print("provider subprocess: spawn smoke passed", flush=True)


def _check_codex(prefix: Path) -> None:
    module = _require_bundle_module("VibeCADCodex", prefix)
    result = module.runtime_execution_smoke()
    print(f"Codex app-server: {result['version']}", flush=True)


CHECKS: dict[str, Callable[[Path], None]] = {
    "python": _check_python,
    "anthropic": _check_module("anthropic"),
    "keyring": _check_module("keyring"),
    "jsonschema": _check_module("jsonschema"),
    "mcp": _check_module("mcp"),
    "mcp-types": _check_module("mcp_types"),
    "openai": _check_module("openai"),
    "tuf": _check_module("tuf"),
    "macos-keyring": _check_macos_keyring,
    "gemini-sdk": _check_gemini_sdk,
    "removed-openai-sdk": _check_gemini_sdk,
    "pivy": _check_pivy,
    "provider-subprocess": _check_provider_subprocess,
    "codex": _check_codex,
}


def main() -> int:
    arguments = _parse_arguments()
    prefix = arguments.prefix.resolve()
    check = CHECKS.get(arguments.check)
    if check is None:
        raise SystemExit(
            f"Unknown runtime check {arguments.check!r}; choose from {sorted(CHECKS)}"
        )
    if not prefix.is_dir():
        raise SystemExit(f"Bundled runtime prefix does not exist: {prefix}")

    print(f"Running macOS runtime check: {arguments.check}", flush=True)
    try:
        check(prefix)
    except BaseException:
        print(f"macOS runtime check failed: {arguments.check}", flush=True)
        traceback.print_exc()
        return 1
    print(f"macOS runtime check passed: {arguments.check}", flush=True)
    return 0


if __name__ == "__main__":
    result = main()
    if result:
        raise SystemExit(result)
