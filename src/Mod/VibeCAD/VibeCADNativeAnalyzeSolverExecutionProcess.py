# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancelable external-process sequence for detached FEM solver execution."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import terminate_process_tree

MAX_SOLVER_LOG_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_ARTIFACT_BYTES = 128 * 1024
MAX_DIAGNOSTIC_EXCERPT_CHARS = 480
MAX_DIAGNOSTIC_ARTIFACTS = 3
_DIAGNOSTIC_SUFFIXES = (".sta", ".cvg", ".dat")
_DIAGNOSTIC_MARKER = re.compile(
    r"(?:\*+\s*error|fatal|singular|zero pivot|not connected|failed)",
    re.IGNORECASE,
)


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 2400))
            return stream.read(2400).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _failure_detail(path: Path, backend: str) -> str:
    detail = _tail(path)
    if str(backend).casefold() != "openfoam":
        return detail
    marker = "FOAM FATAL ERROR:"
    if marker not in detail:
        return detail
    reason = detail.split(marker, 1)[1]
    if "From function" in reason:
        reason = reason.split("From function", 1)[0]
    return reason.strip()


def _bounded_artifact_text(path: Path) -> str:
    """Read bounded head/tail text without retaining a failed private workspace."""

    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size <= MAX_DIAGNOSTIC_ARTIFACT_BYTES:
                data = stream.read(MAX_DIAGNOSTIC_ARTIFACT_BYTES)
            else:
                half = MAX_DIAGNOSTIC_ARTIFACT_BYTES // 2
                head = stream.read(half)
                stream.seek(max(0, size - half))
                data = head + b"\n...\n" + stream.read(half)
    except Exception:
        return ""
    return data.decode("utf-8", errors="replace")


def _diagnostic_excerpt(path: Path) -> str:
    lines = [" ".join(line.split()) for line in _bounded_artifact_text(path).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    marked = [line for line in lines if _DIAGNOSTIC_MARKER.search(line)]
    selected = marked[-4:] if marked else lines[-6:]
    return " | ".join(selected)[-MAX_DIAGNOSTIC_EXCERPT_CHARS:]


def _failure_diagnostics(root: Path, log_path: Path, backend: str) -> list[dict[str, str]]:
    candidates: list[Path] = []
    if str(backend).casefold() == "calculix":
        for suffix in _DIAGNOSTIC_SUFFIXES:
            candidates.extend(sorted(root.glob(f"*{suffix}"), key=lambda path: path.name))
    candidates.append(log_path)
    diagnostics = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        excerpt = _diagnostic_excerpt(path)
        if not excerpt:
            continue
        diagnostics.append({"artifact": path.name, "excerpt": excerpt})
        if len(diagnostics) >= MAX_DIAGNOSTIC_ARTIFACTS:
            break
    return diagnostics


def run_solver_processes(
    commands: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    working_directory: str,
    environment: Mapping[str, str],
    timeout_seconds: int,
    cancelled: Any,
    progress: Any,
    backend: str,
) -> tuple[dict[str, Any], ...]:
    if not commands:
        raise NativeAnalyzeError("The detached FEM solver has no executable command.")
    root = Path(working_directory)
    started = time.monotonic()
    summaries = []
    for index, (program, arguments) in enumerate(commands):
        if cancelled():
            raise NativeBackgroundCancelled()
        log_path = root / f"solver-{index + 1}.log"
        base_progress = 12 + int(65 * index / len(commands))
        progress(base_progress, f"Running {backend} stage {index + 1}/{len(commands)}")
        stage_started = time.monotonic()
        next_status = stage_started + 5.0
        try:
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [program, *arguments],
                    cwd=root,
                    env=dict(environment),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=sys.platform != "win32",
                    creationflags=(
                        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if sys.platform == "win32"
                        else 0
                    ),
                )
                while process.poll() is None:
                    if cancelled():
                        terminate_process_tree(process)
                        raise NativeBackgroundCancelled()
                    if time.monotonic() - started > timeout_seconds:
                        terminate_process_tree(process)
                        raise NativeAnalyzeError(
                            f"{backend} exceeded timeout_seconds before producing results.",
                            error_code="NATIVE_ANALYZE_SOLVER_TIMEOUT",
                        )
                    if log_path.stat().st_size > MAX_SOLVER_LOG_BYTES:
                        terminate_process_tree(process)
                        raise NativeAnalyzeError(
                            f"{backend} exceeded the 16 MiB diagnostic-output bound.",
                            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
                        )
                    now = time.monotonic()
                    if now >= next_status:
                        elapsed = int(now - stage_started)
                        progress(
                            base_progress,
                            (
                                f"Running {backend} stage {index + 1}/{len(commands)} "
                                f"({elapsed}s elapsed)"
                            ),
                        )
                        next_status = now + 5.0
                    time.sleep(0.1)
                exit_code = int(process.returncode or 0)
        except (NativeBackgroundCancelled, NativeAnalyzeError):
            raise
        except Exception as exc:
            raise NativeAnalyzeError(
                f"{backend} stage {index + 1} could not be started.",
                error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
            ) from exc
        if exit_code != 0:
            diagnostics = _failure_diagnostics(root, log_path, backend)
            detail = (
                diagnostics[0]["excerpt"]
                if diagnostics
                else _failure_detail(log_path, backend)
            )
            if str(backend).casefold() == "openfoam":
                detail = _failure_detail(log_path, backend) or detail
            suffix = f": {detail}" if detail else ""
            raise NativeAnalyzeError(
                f"{backend} stage {index + 1} exited with code {exit_code}{suffix}",
                error_code="NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
                repair={
                    "backend": str(backend),
                    "stage": index + 1,
                    "exit_code": exit_code,
                    "diagnostics": diagnostics,
                },
            )
        summaries.append(
            {
                "stage": index + 1,
                "program": Path(program).name,
                "exit_code": exit_code,
            }
        )
    progress(84, f"{backend} result artifacts ready")
    return tuple(summaries)
