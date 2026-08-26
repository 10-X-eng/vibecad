# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable, windowless process runner for scripted and Native engines."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


DEFAULT_PROCESS_POLL_SECONDS = 0.1
DEFAULT_PROCESS_TAIL_BYTES = 2400


@dataclass(frozen=True, slots=True)
class ExternalProcessStage:
    stage: int
    program: str
    exit_code: int
    log_path: str


class ExternalProcessCancelled(RuntimeError):
    """A host cancellation request stopped an external process sequence."""


class ExternalProcessError(RuntimeError):
    """Bounded process failure for translation by a domain adapter."""

    def __init__(
        self,
        reason: str,
        *,
        stage: int,
        program: str,
        exit_code: int | None = None,
        detail: str = "",
    ) -> None:
        clean_reason = str(reason or "").strip()
        if clean_reason not in {
            "start_failed",
            "timeout",
            "output_limit",
            "backend_failed",
        }:
            raise ValueError("Unsupported external-process failure reason.")
        self.reason = clean_reason
        self.stage = int(stage)
        self.program = str(program)
        self.exit_code = None if exit_code is None else int(exit_code)
        self.detail = str(detail or "")[:DEFAULT_PROCESS_TAIL_BYTES]
        super().__init__(clean_reason)


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


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    timeout_seconds: float = 3.0,
) -> None:
    """Terminate a worker and every subprocess it owns."""

    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            taskkill = system_root / "System32" / "taskkill.exe"
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                timeout=timeout_seconds,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=timeout_seconds)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=timeout_seconds)
        except Exception:
            # The caller still owns the authoritative timeout/cancellation
            # result even if the OS races final process-tree reaping.
            pass


def terminate_process(
    process: subprocess.Popen[Any],
    *,
    timeout_seconds: float = 3.0,
) -> None:
    """Compatibility wrapper for the bounded process-sequence runner."""

    terminate_process_tree(process, timeout_seconds=timeout_seconds)


def _terminate(process: subprocess.Popen[Any]) -> None:
    """Compatibility wrapper for existing internal callers and tests."""

    terminate_process_tree(process)


def _read_output_tail(stream: Any, *, max_bytes: int = 64_000) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = int(stream.tell())
    stream.seek(max(0, size - max_bytes), os.SEEK_SET)
    return stream.read().decode("utf-8", errors="replace")[-16_000:]


def bounded_log_tail(
    path: Path,
    *,
    maximum_bytes: int = DEFAULT_PROCESS_TAIL_BYTES,
) -> str:
    bound = max(0, int(maximum_bytes))
    if bound == 0:
        return ""
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - bound))
            return stream.read(bound).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _log_exceeds_limit(path: Path, maximum_log_bytes: int | None) -> bool:
    """Return whether a process log crossed its configured hard byte bound."""

    return maximum_log_bytes is not None and path.stat().st_size > maximum_log_bytes


def _process_creation_kwargs() -> dict[str, Any]:
    return {
        "start_new_session": sys.platform != "win32",
        "creationflags": (
            int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if sys.platform == "win32"
            else 0
        ),
    }


def run_process_sequence(
    commands: Sequence[tuple[str, Sequence[str]]],
    *,
    working_directory: str | Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    cancellation_check: Callable[[], bool],
    log_name: Callable[[int], str] | None = None,
    stage_started: Callable[[int, int], None] | None = None,
    stage_heartbeat: Callable[[int, int, int], None] | None = None,
    heartbeat_seconds: float = 5.0,
    maximum_log_bytes: int | None = None,
    poll_seconds: float = DEFAULT_PROCESS_POLL_SECONDS,
) -> tuple[ExternalProcessStage, ...]:
    """Run an exact shell-free command sequence under one wall-time bound.

    This is the shared process primitive for domain-specific adapters. It owns
    child lifecycle and bounded log capture, but it deliberately owns no FEM,
    mesh, Aero, document, evidence, or transaction semantics.
    """

    exact_commands = tuple(
        (str(program), tuple(str(argument) for argument in arguments))
        for program, arguments in commands
    )
    if not exact_commands:
        raise ValueError("An external process sequence needs at least one command.")
    if not callable(cancellation_check):
        raise TypeError("cancellation_check must be callable")
    if stage_started is not None and not callable(stage_started):
        raise TypeError("stage_started must be callable")
    if stage_heartbeat is not None and not callable(stage_heartbeat):
        raise TypeError("stage_heartbeat must be callable")
    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    if maximum_log_bytes is not None and (
        type(maximum_log_bytes) is not int or maximum_log_bytes < 1
    ):
        raise ValueError("maximum_log_bytes must be a positive integer")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")

    root = Path(working_directory)
    if not root.is_dir():
        raise ValueError("working_directory must be an existing directory")
    make_log_name = log_name or (lambda stage: f"process-{stage}.log")
    started = time.monotonic()
    completed: list[ExternalProcessStage] = []

    for index, (program, arguments) in enumerate(exact_commands, start=1):
        if cancellation_check():
            raise ExternalProcessCancelled()
        if stage_started is not None:
            stage_started(index, len(exact_commands))
        stage_started_at = time.monotonic()
        next_heartbeat = stage_started_at + heartbeat_seconds
        log_path = root / str(make_log_name(index))
        process: subprocess.Popen[Any] | None = None
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
                    **_process_creation_kwargs(),
                )
                while process.poll() is None:
                    if cancellation_check():
                        terminate_process(process)
                        raise ExternalProcessCancelled()
                    if time.monotonic() - started > timeout_seconds:
                        terminate_process(process)
                        raise ExternalProcessError(
                            "timeout",
                            stage=index,
                            program=program,
                        )
                    if _log_exceeds_limit(log_path, maximum_log_bytes):
                        terminate_process(process)
                        raise ExternalProcessError(
                            "output_limit",
                            stage=index,
                            program=program,
                        )
                    now = time.monotonic()
                    if stage_heartbeat is not None and now >= next_heartbeat:
                        stage_heartbeat(
                            index,
                            len(exact_commands),
                            max(0, int(now - stage_started_at)),
                        )
                        next_heartbeat = now + heartbeat_seconds
                    time.sleep(poll_seconds)
                exit_code = int(process.returncode or 0)
        except (ExternalProcessCancelled, ExternalProcessError):
            raise
        except Exception as exc:
            if process is not None:
                try:
                    terminate_process(process)
                except Exception:
                    pass
            raise ExternalProcessError(
                "start_failed",
                stage=index,
                program=program,
            ) from exc

        if _log_exceeds_limit(log_path, maximum_log_bytes):
            raise ExternalProcessError(
                "output_limit",
                stage=index,
                program=program,
            )
        if exit_code != 0:
            raise ExternalProcessError(
                "backend_failed",
                stage=index,
                program=program,
                exit_code=exit_code,
                detail=bounded_log_tail(log_path),
            )
        completed.append(
            ExternalProcessStage(
                stage=index,
                program=program,
                exit_code=exit_code,
                log_path=str(log_path),
            )
        )

    return tuple(completed)


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
