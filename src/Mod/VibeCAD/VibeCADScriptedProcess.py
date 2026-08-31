# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable, windowless process runner for scripted CAD engines."""

from __future__ import annotations

from collections.abc import Callable
import ntpath
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


def process_memory_bytes(pid: int) -> int | None:
    if sys.platform == "win32":
        return _windows_process_memory_bytes(pid)
    if sys.platform == "darwin":
        return _darwin_process_memory_bytes(pid)
    status = Path(f"/proc/{int(pid)}/status")
    try:
        text = status.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    resident: int | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if line.startswith("VmHWM:"):
            return int(parts[1]) * 1024
        if line.startswith("VmRSS:"):
            resident = int(parts[1]) * 1024
    return resident


def _darwin_process_memory_bytes(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(int(pid))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="ascii",
            errors="replace",
            timeout=1.0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return int(completed.stdout.strip()) * 1024


def _windows_process_memory_bytes(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
    except AttributeError:
        return None
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate a worker and every subprocess it owns."""

    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            taskkill = ntpath.join(
                os.environ.get("SystemRoot", r"C:\Windows"),
                "System32",
                "taskkill.exe",
            )
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                timeout=3.0,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3.0)
    except Exception:
        process.kill()
        process.wait(timeout=3.0)


def _terminate(process: subprocess.Popen[Any]) -> None:
    """Compatibility wrapper for existing internal callers and tests."""

    terminate_process_tree(process)


def _read_output_tail(stream: Any, *, max_bytes: int = 64_000) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = int(stream.tell())
    stream.seek(max(0, size - max_bytes), os.SEEK_SET)
    return stream.read().decode("utf-8", errors="replace")[-16_000:]


def run_process(
    command: list[str],
    *,
    cwd: str | Path,
    environment: dict[str, str],
    cancellation_check: Callable[[], bool] | None,
    timeout_seconds: float,
    memory_limit_bytes: int,
    poll_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Run one child process without a console window and enforce hard bounds."""
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform == "win32"
        else 0
    )
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+b") as stdout_stream, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=sys.platform != "win32",
                creationflags=creation_flags,
            )
        except Exception as exc:
            return {
                "started": False,
                "error": str(exc),
                "exception_type": type(exc).__name__,
            }

        cancelled = False
        timed_out = False
        memory_exceeded = False
        observed_memory: int | None = None
        next_memory_check = 0.0
        while process.poll() is None:
            if cancellation_check is not None and cancellation_check():
                cancelled = True
                break
            if poll_callback is not None:
                try:
                    poll_callback()
                except Exception:
                    terminate_process_tree(process)
                    raise
            now = time.monotonic()
            if now - started > timeout_seconds:
                timed_out = True
                break
            if memory_limit_bytes > 0 and now >= next_memory_check:
                next_memory_check = now + 0.5
                observed_memory = process_memory_bytes(process.pid)
                if observed_memory is not None and observed_memory > memory_limit_bytes:
                    memory_exceeded = True
                    break
            time.sleep(0.05)
        if cancelled or timed_out or memory_exceeded:
            terminate_process_tree(process)
        process.wait()
        cpu_exceeded = bool(
            sys.platform != "win32"
            and hasattr(signal, "SIGXCPU")
            and process.returncode == -int(signal.SIGXCPU)
        )
        termination_reason = (
            "host_cancellation_request"
            if cancelled
            else (
                "wall_time_limit"
                if timed_out
                else (
                    "memory_limit"
                    if memory_exceeded
                    else "cpu_time_limit" if cpu_exceeded else "process_exit"
                )
            )
        )
        return {
            "started": True,
            "returncode": process.returncode,
            "stdout": _read_output_tail(stdout_stream),
            "stderr": _read_output_tail(stderr_stream),
            "cancelled": cancelled,
            "timed_out": timed_out,
            "memory_exceeded": memory_exceeded,
            "cpu_exceeded": cpu_exceeded,
            "cancelled_by": "host" if cancelled else None,
            "limit_reached": (
                "wall_time_seconds"
                if timed_out
                else (
                    "memory_bytes"
                    if memory_exceeded
                    else "cpu_seconds" if cpu_exceeded else None
                )
            ),
            "termination_reason": termination_reason,
            "timeout_seconds": float(timeout_seconds),
            "memory_limit_bytes": int(memory_limit_bytes),
            "observed_memory_bytes": observed_memory,
            "elapsed_seconds": time.monotonic() - started,
        }
