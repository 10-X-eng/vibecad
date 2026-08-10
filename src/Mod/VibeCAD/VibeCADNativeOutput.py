# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-granted, one-shot filesystem output for Native capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Callable


MAX_NATIVE_OUTPUT_PATH_CHARACTERS = 4096
MAX_NATIVE_OUTPUT_FILE_NAME_CHARACTERS = 255
MAX_NATIVE_OUTPUT_REQUEST_TEXT_CHARACTERS = 256
NATIVE_OUTPUT_AUTHORIZATION_FAILED = "NATIVE_OUTPUT_AUTHORIZATION_FAILED"
NATIVE_OUTPUT_FAILED = "NATIVE_OUTPUT_FAILED"


class NativeOutputError(RuntimeError):
    """A Native output was not explicitly authorized or safely published."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(str(message).strip())
        self.code = str(code)

    def failure(self) -> dict[str, str]:
        return {"error_code": self.code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class NativeOutputRequest:
    """Trusted host description of one file the human may choose to create."""

    purpose: str
    title: str
    suggested_file_name: str
    allowed_suffixes: tuple[str, ...]
    name_filter: str
    maximum_bytes: int

    def __post_init__(self) -> None:
        text_fields = {
            "purpose": self.purpose,
            "title": self.title,
            "suggested file name": self.suggested_file_name,
            "name filter": self.name_filter,
        }
        for label, value in text_fields.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MAX_NATIVE_OUTPUT_REQUEST_TEXT_CHARACTERS
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"Native output {label} is invalid.")
        suggested = Path(self.suggested_file_name)
        if (
            suggested.name != self.suggested_file_name
            or self.suggested_file_name in {".", ".."}
            or len(self.suggested_file_name) > MAX_NATIVE_OUTPUT_FILE_NAME_CHARACTERS
        ):
            raise ValueError("Native output suggested file name must be one basename.")
        suffixes = tuple(self.allowed_suffixes)
        if (
            not suffixes
            or len(suffixes) > 8
            or len(set(suffixes)) != len(suffixes)
            or any(
                not isinstance(value, str)
                or not value.startswith(".")
                or value != value.casefold()
                or len(value) > 16
                or not value[1:].isalnum()
                for value in suffixes
            )
        ):
            raise ValueError("Native output suffixes are invalid.")
        if (
            type(self.maximum_bytes) is not int
            or not 1 <= self.maximum_bytes <= 16 * 1024 * 1024 * 1024
        ):
            raise ValueError("Native output byte bound is invalid.")


@dataclass(frozen=True, slots=True)
class _PathState:
    exists: bool
    device: int = 0
    inode: int = 0
    mode: int = 0
    size: int = 0
    modified_ns: int = 0


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    device: int
    inode: int
    mode: int


def _path_state(path: Path) -> _PathState:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return _PathState(False)
    except OSError as exc:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output destination could not be inspected.",
        ) from exc
    return _PathState(
        True,
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def _directory_identity(path: Path) -> _DirectoryIdentity:
    value = _path_state(path)
    if not value.exists or not stat.S_ISDIR(value.mode):
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output directory is no longer available.",
        )
    return _DirectoryIdentity(value.device, value.inode, value.mode)


@dataclass(slots=True)
class NativeOutputAuthorization:
    """One exact destination grant created only after a human file choice."""

    request: NativeOutputRequest
    _destination: Path = field(repr=False)
    _parent_identity: _DirectoryIdentity = field(repr=False)
    _destination_state: _PathState = field(repr=False)
    _claimed: bool = field(default=False, init=False, repr=False)

    def claim(self, request: NativeOutputRequest) -> Path:
        if request is not self.request:
            raise NativeOutputError(
                NATIVE_OUTPUT_AUTHORIZATION_FAILED,
                "The output authorization belongs to a different request.",
            )
        if self._claimed:
            raise NativeOutputError(
                NATIVE_OUTPUT_AUTHORIZATION_FAILED,
                "The human output authorization has already been used.",
            )
        self._claimed = True
        self.verify_destination_unchanged()
        return self._destination

    def verify_destination_unchanged(self) -> None:
        if _directory_identity(self._destination.parent) != self._parent_identity:
            raise NativeOutputError(
                NATIVE_OUTPUT_AUTHORIZATION_FAILED,
                "The selected output directory changed after authorization.",
            )
        if _path_state(self._destination) != self._destination_state:
            raise NativeOutputError(
                NATIVE_OUTPUT_AUTHORIZATION_FAILED,
                "The selected output file changed after authorization.",
            )


NativeOutputAuthorizer = Callable[
    [NativeOutputRequest], NativeOutputAuthorization | None
]


@dataclass(frozen=True, slots=True)
class NativeOutputArtifact:
    file_name: str
    size_bytes: int
    sha256: str
    replaced_existing: bool

    def summary(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "replaced_existing": self.replaced_existing,
        }


def authorize_native_output_path(
    request: NativeOutputRequest,
    path: str | os.PathLike[str],
) -> NativeOutputAuthorization:
    """Turn one exact human-selected path into a one-shot output grant."""

    if not isinstance(request, NativeOutputRequest):
        raise TypeError("request must be a NativeOutputRequest")
    raw = os.fspath(path) if isinstance(path, os.PathLike) else path
    if not isinstance(raw, str) or not raw.strip():
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "No output destination was selected.",
        )
    if "\x00" in raw or len(raw) > MAX_NATIVE_OUTPUT_PATH_CHARACTERS:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output path is invalid.",
        )
    selected = Path(raw).expanduser()
    if selected.name in {"", ".", ".."}:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output path has no file name.",
        )
    if len(selected.name) > MAX_NATIVE_OUTPUT_FILE_NAME_CHARACTERS:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output file name is too long.",
        )
    if selected.suffix.casefold() not in request.allowed_suffixes:
        allowed = ", ".join(request.allowed_suffixes)
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            f"The selected output file must use one of: {allowed}.",
        )
    try:
        parent = selected.parent.resolve(strict=True)
    except OSError as exc:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output directory does not exist.",
        ) from exc
    if not parent.is_dir():
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output parent is not a directory.",
        )
    destination = parent / selected.name
    if len(str(destination)) > MAX_NATIVE_OUTPUT_PATH_CHARACTERS:
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output path is too long.",
        )
    destination_state = _path_state(destination)
    if destination_state.exists and not stat.S_ISREG(destination_state.mode):
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "The selected output destination must be a regular file.",
        )
    return NativeOutputAuthorization(
        request=request,
        _destination=destination,
        _parent_identity=_directory_identity(parent),
        _destination_state=destination_state,
    )


def _read_output(path: Path, maximum_bytes: int) -> tuple[int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NativeOutputError(
            NATIVE_OUTPUT_FAILED,
            "The generated output could not be opened safely.",
        ) from exc
    digest = hashlib.sha256()
    size = 0
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise NativeOutputError(
                NATIVE_OUTPUT_FAILED,
                "The generated output is not a regular file.",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum_bytes:
                raise NativeOutputError(
                    NATIVE_OUTPUT_FAILED,
                    "The generated output exceeds its bounded size.",
                )
            digest.update(chunk)
        if size <= 0:
            raise NativeOutputError(
                NATIVE_OUTPUT_FAILED,
                "The generated output is empty.",
            )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return size, digest.hexdigest()


def publish_authorized_output(
    request: NativeOutputRequest,
    authorization: NativeOutputAuthorization,
    *,
    writer: Callable[[str], Any],
    guard: Callable[[], None],
    validator: Callable[[Path], None] | None = None,
) -> NativeOutputArtifact:
    """Write privately, verify, then atomically publish one authorized file."""

    if not isinstance(request, NativeOutputRequest):
        raise TypeError("request must be a NativeOutputRequest")
    if not isinstance(authorization, NativeOutputAuthorization):
        raise NativeOutputError(
            NATIVE_OUTPUT_AUTHORIZATION_FAILED,
            "VibeCAD did not receive a valid human output authorization.",
        )
    if not callable(writer) or not callable(guard):
        raise TypeError("Native output writer and guard must be callable")
    if validator is not None and not callable(validator):
        raise TypeError("Native output validator must be callable")

    destination = authorization.claim(request)
    guard()
    authorization.verify_destination_unchanged()
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.vibecad-",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temporary = Path(temporary_name)
        os.close(descriptor)
        descriptor = -1
        writer(str(temporary))
        if validator is not None:
            validator(temporary)
        size, digest = _read_output(temporary, request.maximum_bytes)
        guard()
        authorization.verify_destination_unchanged()
        replaced = authorization._destination_state.exists
        os.replace(temporary, destination)
        temporary = None
        return NativeOutputArtifact(destination.name, size, digest, replaced)
    except NativeOutputError:
        raise
    except Exception as exc:
        raise NativeOutputError(
            NATIVE_OUTPUT_FAILED,
            "The authorized output could not be generated and published.",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
