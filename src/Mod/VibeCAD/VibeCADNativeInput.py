# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-granted, one-shot filesystem input for Native capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Callable


MAX_NATIVE_INPUT_PATH_CHARACTERS = 4096
MAX_NATIVE_INPUT_FILE_NAME_CHARACTERS = 255
MAX_NATIVE_INPUT_REQUEST_TEXT_CHARACTERS = 256
NATIVE_INPUT_AUTHORIZATION_FAILED = "NATIVE_INPUT_AUTHORIZATION_FAILED"
NATIVE_INPUT_FAILED = "NATIVE_INPUT_FAILED"


class NativeInputError(RuntimeError):
    """A Native input was not explicitly authorized or changed after selection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(str(message).strip())
        self.code = str(code)

    def failure(self) -> dict[str, str]:
        return {"error_code": self.code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class NativeInputRequest:
    """Trusted host description of one existing file the human may select."""

    purpose: str
    title: str
    allowed_suffixes: tuple[str, ...]
    name_filter: str
    maximum_bytes: int

    def __post_init__(self) -> None:
        for label, value in {
            "purpose": self.purpose,
            "title": self.title,
            "name filter": self.name_filter,
        }.items():
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MAX_NATIVE_INPUT_REQUEST_TEXT_CHARACTERS
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"Native input {label} is invalid.")
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
            raise ValueError("Native input suffixes are invalid.")
        if (
            type(self.maximum_bytes) is not int
            or not 1 <= self.maximum_bytes <= 16 * 1024 * 1024 * 1024
        ):
            raise ValueError("Native input byte bound is invalid.")


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


def _file_identity_from_stat(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _path_identity(path: Path, *, code: str) -> _FileIdentity:
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeInputError(
            code, "The selected input file is no longer available."
        ) from exc
    identity = _file_identity_from_stat(value)
    if not stat.S_ISREG(identity.mode):
        raise NativeInputError(code, "The selected input must be a regular file.")
    return identity


def _read_open_file(
    path: Path,
    *,
    expected: _FileIdentity,
    maximum_bytes: int,
    code: str,
    capture_bytes: bool = False,
) -> tuple[str, bytes | None]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NativeInputError(
            code, "The selected input file could not be opened safely."
        ) from exc
    digest = hashlib.sha256()
    chunks: list[bytes] | None = [] if capture_bytes else None
    try:
        before = _file_identity_from_stat(os.fstat(descriptor))
        if before != expected or before.size > maximum_bytes:
            raise NativeInputError(
                code,
                "The selected input file changed or exceeds its byte bound.",
            )
        bytes_read = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes - bytes_read + 1),
            )
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > maximum_bytes:
                raise NativeInputError(
                    code,
                    "The selected input file changed or exceeds its byte bound.",
                )
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = _file_identity_from_stat(os.fstat(descriptor))
        if after != before or bytes_read != before.size:
            raise NativeInputError(
                code, "The selected input file changed while being read."
            )
    except OSError as exc:
        raise NativeInputError(
            code, "The selected input file could not be read safely."
        ) from exc
    finally:
        os.close(descriptor)
    return digest.hexdigest(), None if chunks is None else b"".join(chunks)


def _hash_open_file(
    path: Path,
    *,
    expected: _FileIdentity,
    maximum_bytes: int,
    code: str,
) -> str:
    digest, _value = _read_open_file(
        path,
        expected=expected,
        maximum_bytes=maximum_bytes,
        code=code,
    )
    return digest


@dataclass(frozen=True, slots=True)
class NativeInputArtifact:
    """Exact selected file identity and content, never serialized to the provider."""

    file_name: str
    size_bytes: int
    sha256: str
    _path: Path = field(repr=False, compare=False)
    _identity: _FileIdentity = field(repr=False, compare=False)
    _maximum_bytes: int = field(repr=False, compare=False)

    def verify_unchanged(self) -> None:
        identity = _path_identity(self._path, code=NATIVE_INPUT_FAILED)
        if identity != self._identity:
            raise NativeInputError(
                NATIVE_INPUT_FAILED,
                "The selected input file changed after human authorization.",
            )
        digest = _hash_open_file(
            self._path,
            expected=identity,
            maximum_bytes=self._maximum_bytes,
            code=NATIVE_INPUT_FAILED,
        )
        if digest != self.sha256:
            raise NativeInputError(
                NATIVE_INPUT_FAILED,
                "The selected input content changed after human authorization.",
            )

    def host_path(self) -> Path:
        self.verify_unchanged()
        return self._path

    def read_bytes(self, *, maximum_bytes: int | None = None) -> bytes:
        limit = self._maximum_bytes if maximum_bytes is None else int(maximum_bytes)
        if not 0 <= self.size_bytes <= min(limit, self._maximum_bytes):
            raise NativeInputError(
                NATIVE_INPUT_FAILED,
                "The selected input is too large for this operation.",
            )
        identity = _path_identity(self._path, code=NATIVE_INPUT_FAILED)
        if identity != self._identity:
            raise NativeInputError(
                NATIVE_INPUT_FAILED,
                "The selected input file changed after human authorization.",
            )
        digest, value = _read_open_file(
            self._path,
            expected=identity,
            maximum_bytes=min(limit, self._maximum_bytes),
            code=NATIVE_INPUT_FAILED,
            capture_bytes=True,
        )
        if value is None or len(value) != self.size_bytes or digest != self.sha256:
            raise NativeInputError(
                NATIVE_INPUT_FAILED,
                "The selected input content changed while being read.",
            )
        return value

    def summary(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(slots=True)
class NativeInputAuthorization:
    """One exact input grant created only after a human file choice."""

    request: NativeInputRequest
    _path: Path = field(repr=False)
    _identity: _FileIdentity = field(repr=False)
    _claimed: bool = field(default=False, init=False, repr=False)

    def claim(self, request: NativeInputRequest) -> NativeInputArtifact:
        if request is not self.request:
            raise NativeInputError(
                NATIVE_INPUT_AUTHORIZATION_FAILED,
                "The input authorization belongs to a different request.",
            )
        if self._claimed:
            raise NativeInputError(
                NATIVE_INPUT_AUTHORIZATION_FAILED,
                "The human input authorization has already been used.",
            )
        self._claimed = True
        identity = _path_identity(
            self._path,
            code=NATIVE_INPUT_AUTHORIZATION_FAILED,
        )
        if identity != self._identity:
            raise NativeInputError(
                NATIVE_INPUT_AUTHORIZATION_FAILED,
                "The selected input file changed after authorization.",
            )
        digest = _hash_open_file(
            self._path,
            expected=identity,
            maximum_bytes=self.request.maximum_bytes,
            code=NATIVE_INPUT_AUTHORIZATION_FAILED,
        )
        return NativeInputArtifact(
            file_name=self._path.name,
            size_bytes=identity.size,
            sha256=digest,
            _path=self._path,
            _identity=identity,
            _maximum_bytes=self.request.maximum_bytes,
        )


NativeInputAuthorizer = Callable[[NativeInputRequest], NativeInputAuthorization | None]


def authorize_native_input_path(
    request: NativeInputRequest,
    path: str | os.PathLike[str],
) -> NativeInputAuthorization:
    """Turn one exact human-selected existing path into a one-shot input grant."""

    if not isinstance(request, NativeInputRequest):
        raise TypeError("request must be a NativeInputRequest")
    raw = os.fspath(path) if isinstance(path, os.PathLike) else path
    if not isinstance(raw, str) or not raw.strip():
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "No input file was selected.",
        )
    if "\x00" in raw or len(raw) > MAX_NATIVE_INPUT_PATH_CHARACTERS:
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "The selected input path is invalid.",
        )
    selected = Path(raw).expanduser()
    if (
        selected.name in {"", ".", ".."}
        or len(selected.name) > MAX_NATIVE_INPUT_FILE_NAME_CHARACTERS
        or any(
            ord(character) < 32 or ord(character) == 127 for character in selected.name
        )
    ):
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "The selected input path has no valid file name.",
        )
    if selected.suffix.casefold() not in request.allowed_suffixes:
        allowed = ", ".join(request.allowed_suffixes)
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            f"The selected input file must use one of: {allowed}.",
        )
    try:
        exact = selected.resolve(strict=True)
    except OSError as exc:
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "The selected input file does not exist.",
        ) from exc
    if len(str(exact)) > MAX_NATIVE_INPUT_PATH_CHARACTERS:
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "The selected input path is too long.",
        )
    identity = _path_identity(exact, code=NATIVE_INPUT_AUTHORIZATION_FAILED)
    if identity.size > request.maximum_bytes:
        raise NativeInputError(
            NATIVE_INPUT_AUTHORIZATION_FAILED,
            "The selected input file exceeds its byte bound.",
        )
    return NativeInputAuthorization(request, exact, identity)


def inspect_native_input_file(
    path: str | os.PathLike[str],
    *,
    maximum_bytes: int,
) -> dict[str, Any]:
    """Return a path-free content record for one trusted document file property."""

    raw = os.fspath(path) if isinstance(path, os.PathLike) else path
    if not isinstance(raw, str) or not raw:
        return {"configured": False}
    if "\x00" in raw or len(raw) > MAX_NATIVE_INPUT_PATH_CHARACTERS:
        raise NativeInputError(NATIVE_INPUT_FAILED, "A document input path is invalid.")
    try:
        exact = Path(raw).resolve(strict=True)
    except OSError as exc:
        raise NativeInputError(
            NATIVE_INPUT_FAILED,
            "A configured document input file is unavailable.",
        ) from exc
    identity = _path_identity(exact, code=NATIVE_INPUT_FAILED)
    if identity.size > int(maximum_bytes):
        raise NativeInputError(
            NATIVE_INPUT_FAILED,
            "A configured document input file exceeds its byte bound.",
        )
    digest = _hash_open_file(
        exact,
        expected=identity,
        maximum_bytes=int(maximum_bytes),
        code=NATIVE_INPUT_FAILED,
    )
    return {
        "configured": True,
        "size_bytes": identity.size,
        "sha256": digest,
    }
