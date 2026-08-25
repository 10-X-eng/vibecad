# SPDX-License-Identifier: LGPL-2.1-or-later

"""Domain-neutral immutable input/artifact sealing primitives for Analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


DEFAULT_MAXIMUM_FILES = 4096
DEFAULT_MAXIMUM_BYTES = 4 * 1024 * 1024 * 1024
STREAM_BLOCK_BYTES = 1024 * 1024
FEM_COMPAT_DIGEST_ALGORITHM = "vibecad-fem-directory-sha256-v1"


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
        if clean_reason not in {"empty", "bounds", "unsafe_symlink", "read_failed"}:
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
