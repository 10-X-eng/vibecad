# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancelable external-process sequence for detached FEM solver execution."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping, NoReturn

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import (
    ExternalProcessCancelled,
    ExternalProcessError,
    run_process_sequence,
)


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


def _failure_diagnostics(
    root: Path,
    log_path: Path,
    backend: str,
) -> list[dict[str, str]]:
    candidates: list[Path] = []
    if str(backend).casefold() == "calculix":
        for suffix in _DIAGNOSTIC_SUFFIXES:
            candidates.extend(
                sorted(root.glob(f"*{suffix}"), key=lambda path: path.name)
            )
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


def raise_solver_process_cancelled(
    error: ExternalProcessCancelled,
) -> NoReturn:
    """Translate shared-process cancellation through the preserved FEM surface."""

    if not isinstance(error, ExternalProcessCancelled):
        raise TypeError("error must be ExternalProcessCancelled")
    raise NativeBackgroundCancelled() from error


def raise_solver_process_error(
    error: ExternalProcessError,
    *,
    working_directory: str | Path,
    backend: str,
) -> NoReturn:
    """Translate a shared-process failure with legacy-exact FEM diagnostics."""

    if not isinstance(error, ExternalProcessError):
        raise TypeError("error must be ExternalProcessError")
    root = Path(working_directory)
    backend = str(backend)
    if error.reason == "timeout":
        raise NativeAnalyzeError(
            f"{backend} exceeded timeout_seconds before producing results.",
            error_code="NATIVE_ANALYZE_SOLVER_TIMEOUT",
        ) from error
    if error.reason == "output_limit":
        raise NativeAnalyzeError(
            f"{backend} exceeded the 16 MiB diagnostic-output bound.",
            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
        ) from error
    if error.reason == "start_failed":
        raise NativeAnalyzeError(
            f"{backend} stage {error.stage} could not be started.",
            error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
        ) from error

    log_path = root / f"solver-{error.stage}.log"
    diagnostics = _failure_diagnostics(root, log_path, backend)
    detail = diagnostics[0]["excerpt"] if diagnostics else error.detail
    if backend.casefold() == "openfoam":
        detail = _failure_detail(log_path, backend) or detail
    suffix = f": {detail}" if detail else ""
    raise NativeAnalyzeError(
        f"{backend} stage {error.stage} exited with code {error.exit_code}{suffix}",
        error_code="NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
        repair={
            "backend": backend,
            "stage": error.stage,
            "exit_code": error.exit_code,
            "diagnostics": diagnostics,
        },
    ) from error


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
    if not root.is_dir():
        raise NativeAnalyzeError(
            f"{backend} stage 1 could not be started.",
            error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
        )

    def stage_started(stage: int, total: int) -> None:
        base_progress = 12 + int(65 * (stage - 1) / total)
        progress(base_progress, f"Running {backend} stage {stage}/{total}")

    def stage_heartbeat(stage: int, total: int, elapsed: int) -> None:
        base_progress = 12 + int(65 * (stage - 1) / total)
        progress(
            base_progress,
            f"Running {backend} stage {stage}/{total} ({elapsed}s elapsed)",
        )

    try:
        stages = run_process_sequence(
            commands,
            working_directory=root,
            environment=environment,
            timeout_seconds=timeout_seconds,
            cancellation_check=cancelled,
            log_name=lambda stage: f"solver-{stage}.log",
            stage_started=stage_started,
            stage_heartbeat=stage_heartbeat,
            maximum_log_bytes=MAX_SOLVER_LOG_BYTES,
        )
    except ExternalProcessCancelled as exc:
        raise_solver_process_cancelled(exc)
    except ExternalProcessError as exc:
        raise_solver_process_error(
            exc,
            working_directory=root,
            backend=backend,
        )

    progress(84, f"{backend} result artifacts ready")
    return tuple(
        {
            "stage": stage.stage,
            "program": Path(stage.program).name,
            "exit_code": stage.exit_code,
        }
        for stage in stages
    )
