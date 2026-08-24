# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancelable external-process sequence for detached FEM solver execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADScriptedProcess import (
    ExternalProcessCancelled,
    ExternalProcessError,
    run_process_sequence,
)


MAX_SOLVER_LOG_BYTES = 16 * 1024 * 1024


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
    if not Path(working_directory).is_dir():
        raise NativeAnalyzeError(
            f"{backend} stage 1 could not be started.",
            error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
        )

    def stage_started(stage: int, total: int) -> None:
        base_progress = 12 + int(65 * (stage - 1) / total)
        progress(base_progress, f"Running {backend} stage {stage}/{total}")

    try:
        stages = run_process_sequence(
            commands,
            working_directory=working_directory,
            environment=environment,
            timeout_seconds=timeout_seconds,
            cancellation_check=cancelled,
            log_name=lambda stage: f"solver-{stage}.log",
            stage_started=stage_started,
            maximum_log_bytes=MAX_SOLVER_LOG_BYTES,
        )
    except ExternalProcessCancelled as exc:
        raise NativeBackgroundCancelled() from exc
    except ExternalProcessError as exc:
        if exc.reason == "timeout":
            raise NativeAnalyzeError(
                f"{backend} exceeded timeout_seconds before producing results.",
                error_code="NATIVE_ANALYZE_SOLVER_TIMEOUT",
            ) from exc
        if exc.reason == "output_limit":
            raise NativeAnalyzeError(
                f"{backend} exceeded the 16 MiB diagnostic-output bound.",
                error_code="NATIVE_ANALYZE_SOLVER_OUTPUT_LIMIT",
            ) from exc
        if exc.reason == "start_failed":
            raise NativeAnalyzeError(
                f"{backend} stage {exc.stage} could not be started.",
                error_code="NATIVE_ANALYZE_SOLVER_START_FAILED",
            ) from exc
        suffix = f": {exc.detail}" if exc.detail else ""
        raise NativeAnalyzeError(
            f"{backend} stage {exc.stage} exited with code {exc.exit_code}{suffix}",
            error_code="NATIVE_ANALYZE_SOLVER_BACKEND_FAILED",
        ) from exc

    progress(84, f"{backend} result artifacts ready")
    return tuple(
        {
            "stage": stage.stage,
            "program": Path(stage.program).name,
            "exit_code": stage.exit_code,
        }
        for stage in stages
    )
