# SPDX-License-Identifier: LGPL-2.1-or-later
"""Solver-neutral detached execution invariants distilled from live VibeCAD FEM.

Pass 03 Correction 01 treats this module as a **transitional reference only**.
The canonical target is to extract these physics-neutral invariants into one
host-owned VibeCAD Analysis Runtime, prove that runtime first with existing FEM,
and then make Aero a client without changing Aero case/result contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FrozenInput:
    root: str
    sha256: str
    file_count: int
    total_bytes: int


def freeze_directory(root: str | Path, *, max_files: int = 4096, max_bytes: int = 4 * 1024**3) -> FrozenInput:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValueError("detached input contains symlink")
        if not path.is_file():
            continue
        count += 1
        total += path.stat().st_size
        if count > max_files or total > max_bytes:
            raise ValueError("detached input exceeds configured bounds")
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    if count == 0:
        raise ValueError("detached input contains no files")
    return FrozenInput(str(base), digest.hexdigest(), count, total)


@dataclass(frozen=True)
class AttachmentGuard:
    input_sha256: str
    native_revision: int
    geometry_revision: str
    case_sha256: str


def can_attach(guard: AttachmentGuard, *, input_sha256: str, native_revision: int, geometry_revision: str, case_sha256: str) -> bool:
    return (
        str(input_sha256) == guard.input_sha256
        and int(native_revision) == guard.native_revision
        and str(geometry_revision) == guard.geometry_revision
        and str(case_sha256) == guard.case_sha256
    )
