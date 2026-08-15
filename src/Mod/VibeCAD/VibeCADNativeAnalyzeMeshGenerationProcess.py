# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancelable subprocess execution for detached FEM mesh generation."""

from __future__ import annotations

from pathlib import Path
import subprocess
import time
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeBackground import NativeBackgroundCancelled


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def run_mesh_process(
    command: tuple[str, ...],
    *,
    log_path: Path,
    timeout_seconds: int,
    cancelled: Any,
    progress: Any,
    backend: str,
) -> None:
    started = time.monotonic()
    last_progress = 12
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
            )
            while process.poll() is None:
                if cancelled():
                    stop_process(process)
                    raise NativeBackgroundCancelled()
                elapsed = time.monotonic() - started
                if elapsed > timeout_seconds:
                    stop_process(process)
                    raise NativeAnalyzeError(
                        f"{backend} exceeded timeout_seconds before producing a mesh.",
                        error_code="NATIVE_ANALYZE_MESH_TIMEOUT",
                    )
                percent = min(78, 12 + int(66 * elapsed / timeout_seconds))
                if percent > last_progress:
                    progress(percent, f"Running {backend}")
                    last_progress = percent
                time.sleep(0.1)
            exit_code = int(process.returncode or 0)
    except (NativeBackgroundCancelled, NativeAnalyzeError):
        raise
    except Exception as exc:
        raise NativeAnalyzeError(
            f"{backend} could not be started.",
            error_code="NATIVE_ANALYZE_MESH_START_FAILED",
        ) from exc
    if exit_code == 0:
        return
    try:
        detail = log_path.read_text(encoding="utf-8", errors="replace").strip()[-1200:]
    except Exception:
        detail = ""
    suffix = f": {detail}" if detail else ""
    raise NativeAnalyzeError(
        f"{backend} exited with code {exit_code}{suffix}",
        error_code="NATIVE_ANALYZE_MESH_BACKEND_FAILED",
    )
