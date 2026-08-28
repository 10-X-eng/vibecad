# SPDX-License-Identifier: LGPL-2.1-or-later

"""Domain-neutral immutable input/artifact sealing primitives for Analysis jobs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
from typing import Iterable, Iterator
import zipfile


DEFAULT_MAXIMUM_FILES = 4096
DEFAULT_MAXIMUM_BYTES = 4 * 1024 * 1024 * 1024
STREAM_BLOCK_BYTES = 1024 * 1024
FEM_COMPAT_DIGEST_ALGORITHM = "vibecad-fem-directory-sha256-v1"
ARTIFACT_MANIFEST_VERSION = "vibecad-analysis-artifacts-v1"


class AnalysisArtifactError(RuntimeError):
    """A sealed Analysis artifact set failed a generic integrity invariant."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        relative_path: str = "",
    ) -> None:
        clean_reason = str(reason or "").strip()
        if clean_reason not in {
            "empty", "bounds", "unsafe_symlink", "unsafe_path", "unsafe_archive",
            "hash_mismatch", "read_failed", "invalid_manifest",
        }:
            raise ValueError("Unsupported Analysis artifact failure reason.")
        self.reason = clean_reason
        self.relative_path = str(relative_path or "").strip()
        super().__init__(str(message or "").strip())


@dataclass(frozen=True, slots=True)
class SealedDirectory:
    """Immutable identity of a bounded input directory.

    ``fem-compat-v1`` deliberately preserves the exact digest semantics used by
    detached FEM before this host extraction: sorted Path traversal, POSIX
    relative-name bytes prefixed by a four-byte big-endian length, followed by
    file content streamed in 1 MiB blocks.
    """

    root: str
    sha256: str
    file_count: int
    total_bytes: int
    digest_algorithm: str = FEM_COMPAT_DIGEST_ALGORITHM


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """Compact, immutable identity for one external Analysis artifact."""

    role: str
    logical_name: str
    media_type: str
    relative_path: str
    byte_count: int
    sha256: str
    producer_id: str
    job_id: str
    provider_id: str
    solver_id: str
    source_correlation: str
    exactness_class: str
    created_at: str

    def __post_init__(self) -> None:
        for field in (
            "role", "logical_name", "media_type", "relative_path", "producer_id",
            "job_id", "provider_id", "solver_id", "source_correlation",
            "exactness_class", "created_at",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"{field} must be non-empty")
        clean_path = _safe_relative_path(self.relative_path)
        object.__setattr__(self, "relative_path", clean_path)
        if type(self.byte_count) is not int or self.byte_count < 0:
            raise ValueError("byte_count must be a non-negative integer")
        digest = str(self.sha256 or "").lower()
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    version: str
    artifacts: tuple[ArtifactDescriptor, ...]

    def __post_init__(self) -> None:
        if self.version != ARTIFACT_MANIFEST_VERSION:
            raise ValueError("Unsupported artifact manifest version")
        artifacts = tuple(self.artifacts)
        if not artifacts:
            raise ValueError("Artifact manifest must not be empty")
        paths = tuple(item.relative_path for item in artifacts)
        if len(paths) != len(set(paths)):
            raise ValueError("Artifact manifest paths must be unique")
        object.__setattr__(self, "artifacts", artifacts)

    def canonical_json(self) -> str:
        return json.dumps(
            {"version": self.version, "artifacts": [
                {field: getattr(item, field) for field in item.__dataclass_fields__}
                for item in self.artifacts
            ]}, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _safe_relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    candidate = Path(raw)
    if (
        not raw or raw.startswith("/") or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in raw.split("/"))
        or (len(raw) > 1 and raw[1] == ":")
    ):
        raise AnalysisArtifactError("unsafe_path", "Artifact path is not safely relative.", relative_path=raw)
    return raw


def _file_sha256(
    path: Path,
    *,
    maximum_bytes: int | None = None,
    relative_path: str = "",
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    error_path = str(relative_path or path.name)
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(STREAM_BLOCK_BYTES), b""):
                total += len(block)
                if maximum_bytes is not None and total > maximum_bytes:
                    raise AnalysisArtifactError(
                        "bounds",
                        "Analysis artifact exceeds its declared byte bound.",
                        relative_path=error_path,
                    )
                digest.update(block)
    except AnalysisArtifactError:
        raise
    except Exception as exc:
        raise AnalysisArtifactError(
            "read_failed",
            "An Analysis artifact could not be read.",
            relative_path=error_path,
        ) from exc
    return digest.hexdigest(), total


def seal_artifact(
    path: str | Path, *, root: str | Path, role: str, logical_name: str,
    media_type: str, producer_id: str, job_id: str, provider_id: str,
    solver_id: str, source_correlation: str, exactness_class: str, created_at: str,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
) -> ArtifactDescriptor:
    """Seal one regular file without trusting its name, reported size, or hash."""
    file_path = Path(path)
    base = Path(root)
    try:
        relative = _safe_relative_path(file_path.relative_to(base).as_posix())
    except ValueError as exc:
        raise AnalysisArtifactError("unsafe_path", "Artifact escaped its declared root.") from exc
    if file_path.is_symlink():
        raise AnalysisArtifactError("unsafe_symlink", "Artifact is a symbolic link.", relative_path=relative)
    if not file_path.is_file():
        raise AnalysisArtifactError("read_failed", "Artifact is not a regular file.", relative_path=relative)
    digest, byte_count = _file_sha256(
        file_path,
        maximum_bytes=_positive_integer(maximum_bytes, "maximum_bytes"),
        relative_path=relative,
    )
    return ArtifactDescriptor(
        role, logical_name, media_type, relative, byte_count, digest, producer_id,
        job_id, provider_id, solver_id, source_correlation, exactness_class, created_at,
    )


def verify_artifact(path: str | Path, descriptor: ArtifactDescriptor) -> None:
    digest, byte_count = _file_sha256(
        Path(path),
        maximum_bytes=descriptor.byte_count,
        relative_path=descriptor.relative_path,
    )
    if digest != descriptor.sha256 or byte_count != descriptor.byte_count:
        raise AnalysisArtifactError("hash_mismatch", "Artifact content does not match its immutable descriptor.", relative_path=descriptor.relative_path)


def validate_archive(path: str | Path, *, maximum_files: int = DEFAULT_MAXIMUM_FILES, maximum_bytes: int = DEFAULT_MAXIMUM_BYTES) -> None:
    """Validate ZIP/TAR members before any caller extracts an untrusted bundle."""
    max_files = _positive_integer(maximum_files, "maximum_files")
    max_bytes = _positive_integer(maximum_bytes, "maximum_bytes")
    archive = Path(path)
    count = total = 0
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                members = [
                    (
                        item.filename,
                        item.file_size,
                        stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF),
                    )
                    for item in bundle.infolist()
                    if not item.is_dir()
                ]
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as bundle:
                members = [
                    (item.name, item.size, item.issym() or item.islnk())
                    for item in bundle.getmembers()
                    if not item.isdir()
                ]
        else:
            raise AnalysisArtifactError("unsafe_archive", "Unsupported or invalid Analysis archive.")
        for name, size, is_link in members:
            relative = _safe_relative_path(name)
            if is_link:
                raise AnalysisArtifactError("unsafe_archive", "Archive contains a link member.", relative_path=relative)
            count += 1
            total += size
            if count > max_files or total > max_bytes:
                raise AnalysisArtifactError("bounds", "Archive exceeds configured expansion bounds.", relative_path=relative)
    except AnalysisArtifactError:
        raise
    except Exception as exc:
        raise AnalysisArtifactError("unsafe_archive", "Analysis archive could not be safely inspected.") from exc


class ContentAddressedArtifactStore:
    """Atomic immutable admission and evidence-aware, idempotent cleanup."""

    def __init__(
        self,
        root: str | Path,
        *,
        maximum_artifacts: int = DEFAULT_MAXIMUM_FILES,
        maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
    ) -> None:
        self.root = Path(root)
        self.maximum_artifacts = _positive_integer(
            maximum_artifacts, "maximum_artifacts"
        )
        self.maximum_bytes = _positive_integer(maximum_bytes, "maximum_bytes")
        self.lock_path = self.root / ".writer.lock"

    @contextmanager
    def _writer(self) -> Iterator[None]:
        """Serialize quota accounting and mutations across host processes."""

        self.root.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise AnalysisArtifactError(
                "read_failed",
                "Another VibeCAD process owns Analysis artifact storage writes.",
            ) from exc
        try:
            yield
        finally:
            try:
                stream.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _usage_unlocked(self) -> dict[str, int]:
        count = total = 0
        if not self.root.exists():
            return {"artifact_count": count, "total_bytes": total}
        for prefix in self.root.iterdir():
            if prefix == self.lock_path:
                continue
            if (
                prefix.is_symlink()
                or not prefix.is_dir()
                or len(prefix.name) != 2
                or any(value not in "0123456789abcdef" for value in prefix.name)
            ):
                raise AnalysisArtifactError(
                    "invalid_manifest",
                    "Analysis artifact storage contains an invalid object path.",
                    relative_path=prefix.name,
                )
            for object_path in prefix.iterdir():
                digest = prefix.name + object_path.name
                if (
                    not object_path.is_file()
                    or object_path.is_symlink()
                    or len(digest) != 64
                    or any(value not in "0123456789abcdef" for value in digest)
                ):
                    raise AnalysisArtifactError(
                        "invalid_manifest",
                        "Analysis artifact storage contains an invalid object path.",
                        relative_path=f"{prefix.name}/{object_path.name}",
                    )
                count += 1
                total += object_path.stat().st_size
        return {"artifact_count": count, "total_bytes": total}

    def usage(self) -> dict[str, int]:
        """Return exact retained-object quota usage under the storage lock."""

        with self._writer():
            return self._usage_unlocked()

    def path_for(self, sha256: str) -> Path:
        digest = str(sha256 or "").lower()
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        return self.root / digest[:2] / digest[2:]

    def verify_admitted(self, descriptor: ArtifactDescriptor) -> Path:
        """Reverify one admitted regular object under the storage lock."""

        destination = self.path_for(descriptor.sha256)
        with self._writer():
            prefix = destination.parent
            if (
                self.root.is_symlink()
                or prefix.is_symlink()
                or destination.is_symlink()
            ):
                raise AnalysisArtifactError(
                    "unsafe_symlink",
                    "Immutable artifact storage contains a symbolic link.",
                    relative_path=descriptor.relative_path,
                )
            if not destination.exists():
                raise AnalysisArtifactError(
                    "read_failed",
                    "Immutable artifact storage is not durably readable.",
                    relative_path=descriptor.relative_path,
                )
            if not destination.is_file():
                raise AnalysisArtifactError(
                    "invalid_manifest",
                    "Immutable artifact storage contains a non-file object.",
                    relative_path=descriptor.relative_path,
                )
            verify_artifact(destination, descriptor)
            return destination

    def admit(self, source: str | Path, descriptor: ArtifactDescriptor) -> Path:
        source_path = Path(source)
        verify_artifact(source_path, descriptor)
        destination = self.path_for(descriptor.sha256)
        with self._writer():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                verify_artifact(destination, descriptor)
                return destination
            usage = self._usage_unlocked()
            if usage["artifact_count"] + 1 > self.maximum_artifacts:
                raise AnalysisArtifactError(
                    "bounds", "Analysis artifact storage exceeds its object quota."
                )
            if usage["total_bytes"] + descriptor.byte_count > self.maximum_bytes:
                raise AnalysisArtifactError(
                    "bounds", "Analysis artifact storage exceeds its byte quota."
                )
            temporary = destination.with_name(
                f".{destination.name}.{os.getpid()}.tmp"
            )
            try:
                shutil.copyfile(source_path, temporary)
                verify_artifact(temporary, descriptor)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            return destination

    def cleanup(self, sha256: str, *, protected_sha256: Iterable[str] = ()) -> bool:
        digest = str(sha256 or "").lower()
        if digest in {str(item).lower() for item in protected_sha256}:
            return False
        path = self.path_for(digest)
        with self._writer():
            if not path.exists():
                return False
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
            return True


def _positive_integer(value: int, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def seal_directory(
    root: str | Path,
    *,
    maximum_files: int = DEFAULT_MAXIMUM_FILES,
    maximum_bytes: int = DEFAULT_MAXIMUM_BYTES,
) -> SealedDirectory:
    """Seal one detached directory using the current FEM-compatible digest.

    This function owns only generic filesystem integrity. It has no knowledge
    of FEM, Aero, solver choice, document state, publication, qualification, or
    scheduling. The initial digest is intentionally compatibility-preserving;
    richer manifests can be layered on later without changing this SHA.
    """

    max_files = _positive_integer(maximum_files, "maximum_files")
    max_bytes = _positive_integer(maximum_bytes, "maximum_bytes")
    base = Path(root)
    digest = hashlib.sha256()
    count = 0
    total = 0

    try:
        paths = sorted(base.rglob("*"))
    except Exception as exc:
        raise AnalysisArtifactError(
            "read_failed",
            "The detached Analysis input directory could not be enumerated.",
        ) from exc

    for path in paths:
        try:
            relative = path.relative_to(base).as_posix()
        except Exception as exc:
            raise AnalysisArtifactError(
                "read_failed",
                "An Analysis input path escaped its detached root.",
            ) from exc

        if path.is_symlink():
            raise AnalysisArtifactError(
                "unsafe_symlink",
                "A detached Analysis input contains an unsafe symbolic link.",
                relative_path=relative,
            )
        if not path.is_file():
            continue

        try:
            byte_count = path.stat().st_size
        except Exception as exc:
            raise AnalysisArtifactError(
                "read_failed",
                "A detached Analysis input could not be inspected.",
                relative_path=relative,
            ) from exc

        count += 1
        total += byte_count
        if count > max_files or total > max_bytes:
            raise AnalysisArtifactError(
                "bounds",
                "The detached Analysis input exceeds its configured bounds.",
                relative_path=relative,
            )

        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(STREAM_BLOCK_BYTES), b""):
                    digest.update(block)
        except Exception as exc:
            raise AnalysisArtifactError(
                "read_failed",
                "A detached Analysis input could not be read.",
                relative_path=relative,
            ) from exc

    if count == 0:
        raise AnalysisArtifactError(
            "empty",
            "The detached Analysis input contains no files.",
        )

    return SealedDirectory(
        root=str(base),
        sha256=digest.hexdigest(),
        file_count=count,
        total_bytes=total,
    )
