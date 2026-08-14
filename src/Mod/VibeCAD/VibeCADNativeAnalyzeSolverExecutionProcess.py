# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancelable external-process sequence for detached FEM solver execution."""

from __future__ import annotations

from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMeshGenerationProcess import stop_process
from VibeCADNativeBackground import NativeBackgroundCancelled


MAX_SOLVER_LOG_BYTES = 16 * 1024 * 1024


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 2400))
            return stream.read(2400).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


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
                )
                while process.poll() is None:
                    if cancelled():
                        stop_process(process)
                        raise NativeBackgroundCancelled()
                    if time.monotonic() - started > timeout_seconds:
                        stop_process(process)
                        raise NativeAnalyzeError(
                            f"{backend} exceeded timeout_seconds before producing results.",
                            error_code="NATIVE_ANALYZE_SOLVER_TIMEOUT",
                        )
                    if log_path.stat().st_size > MAX_SOLVER_LOG_BYTES:
                        stop_process(process)
                        raise NativeAnalyzeError(
                            f"{backend} exceeded the 16 MiB diagnostic-output bound.",
                            error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
                        )
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
            detail = _tail(log_path)
            suffix = f": {detail}" if detail else ""
            raise NativeAnalyzeError(
                f"{backend} stage {index + 1} exited with code {exit_code}{suffix}",
                error_code="NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
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
