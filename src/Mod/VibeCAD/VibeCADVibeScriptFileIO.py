# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic VibeScript project-file I/O that cooperates with Windows readers."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import math
import os
from pathlib import Path
import time
from typing import BinaryIO, Iterator
import uuid

CRITICAL_IO_TIMEOUT_SECONDS = 10.0
TELEMETRY_IO_TIMEOUT_SECONDS = 0.25
_INITIAL_RETRY_SECONDS = 0.005
_MAX_RETRY_SECONDS = 0.1
_WINDOWS_SHARING_ERRORS = frozenset({5, 32, 33})

if os.name == "nt":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _windows_replace_file = _kernel32.ReplaceFileW
    _windows_replace_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    _windows_replace_file.restype = wintypes.BOOL
    _windows_create_file = _kernel32.CreateFileW
    _windows_create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _windows_create_file.restype = wintypes.HANDLE
    _windows_close_handle = _kernel32.CloseHandle
    _windows_close_handle.argtypes = (wintypes.HANDLE,)
    _windows_close_handle.restype = wintypes.BOOL


def _replace_is_temporarily_blocked(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in _WINDOWS_SHARING_ERRORS or exc.errno in {
        errno.EACCES,
        errno.EPERM,
    }


def _read_is_temporarily_blocked(exc: OSError) -> bool:
    return isinstance(exc, FileNotFoundError) or _replace_is_temporarily_blocked(exc)


def _replace_path(source: Path, destination: Path) -> None:
    """Replace one file without rejecting VibeCAD's own Windows readers."""

    if os.name != "nt" or not destination.exists():
        os.replace(source, destination)
        return

    if _windows_replace_file(str(destination), str(source), None, 0, None, None):
        return
    error = ctypes.get_last_error()
    if error in {2, 3} and source.exists():
        # The destination may have disappeared between the existence check and
        # ReplaceFileW. MoveFileExW (used by os.replace) can publish it anew.
        os.replace(source, destination)
        return
    raise ctypes.WinError(error)


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    replace_timeout_seconds: float = CRITICAL_IO_TIMEOUT_SECONDS,
    best_effort: bool = False,
) -> bool:
    """Publish complete text through a unique sibling and atomic replacement.

    Windows scanners and ordinary readers can briefly deny replacement of an
    open destination. Retry those sharing violations until the caller's
    deadline. Best-effort telemetry returns ``False`` instead of invalidating
    otherwise valid CAD work; durable state continues to fail closed.
    """

    destination = Path(path)
    timeout = float(replace_timeout_seconds)
    if not math.isfinite(timeout) or timeout < 0.0:
        raise ValueError("replace_timeout_seconds must be finite and non-negative")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        try:
            temporary.write_text(str(content), encoding="utf-8")
        except OSError:
            if best_effort:
                return False
            raise

        deadline = time.monotonic() + timeout
        delay = _INITIAL_RETRY_SECONDS
        while True:
            try:
                _replace_path(temporary, destination)
                return True
            except OSError as exc:
                remaining = deadline - time.monotonic()
                if not _replace_is_temporarily_blocked(exc) or remaining <= 0.0:
                    if best_effort:
                        return False
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2.0, _MAX_RETRY_SECONDS)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A scanner may retain the unpublished unique sibling briefly.
            # It is never treated as current state and can be cleaned later.
            pass


@contextmanager
def open_shared_binary(path: str | Path) -> Iterator[BinaryIO]:
    """Open a reader that permits concurrent replacement on Windows."""

    source = Path(path)
    if os.name != "nt":
        with source.open("rb") as stream:
            yield stream
        return

    import ctypes
    import msvcrt

    generic_read = 0x80000000
    share_read = 0x00000001
    share_write = 0x00000002
    share_delete = 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_sequential_scan = 0x08000000
    invalid_handle_value = ctypes.c_void_p(-1).value

    handle = _windows_create_file(
        str(source),
        generic_read,
        share_read | share_write | share_delete,
        None,
        open_existing,
        file_attribute_normal | file_flag_sequential_scan,
        None,
    )
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(
            int(handle),
            os.O_RDONLY | os.O_BINARY,
        )
    except BaseException:
        _windows_close_handle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        yield stream


def read_text_shared(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    retry_timeout_seconds: float = CRITICAL_IO_TIMEOUT_SECONDS,
) -> str:
    """Read one complete snapshot while cooperating with atomic replacement."""

    timeout = float(retry_timeout_seconds)
    if not math.isfinite(timeout) or timeout < 0.0:
        raise ValueError("retry_timeout_seconds must be finite and non-negative")
    deadline = time.monotonic() + timeout
    delay = _INITIAL_RETRY_SECONDS
    while True:
        try:
            with open_shared_binary(path) as stream:
                return stream.read().decode(encoding)
        except OSError as exc:
            remaining = deadline - time.monotonic()
            if not _read_is_temporarily_blocked(exc) or remaining <= 0.0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2.0, _MAX_RETRY_SECONDS)
