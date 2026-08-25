# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from VibeCADAnalysisArtifacts import (
    AnalysisArtifactError,
    FEM_COMPAT_DIGEST_ALGORITHM,
    seal_directory,
)


def _expected_fem_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    size = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        count += 1
        size += path.stat().st_size
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest(), count, size


def test_fem_compat_seal_preserves_exact_digest_order_and_path_encoding(
    tmp_path: Path,
) -> None:
    (tmp_path / "z.txt").write_bytes(b"last")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "a.inp").write_bytes(b"first\nsecond\n")
    (tmp_path / "a.txt").write_bytes(b"middle")

    expected_sha, expected_count, expected_bytes = _expected_fem_digest(tmp_path)
    sealed = seal_directory(tmp_path)

    assert sealed.sha256 == expected_sha
    assert sealed.file_count == expected_count == 3
    assert sealed.total_bytes == expected_bytes
    assert sealed.digest_algorithm == FEM_COMPAT_DIGEST_ALGORITHM
    assert sealed.root == str(tmp_path)


def test_seal_directory_streams_files_larger_than_one_block(tmp_path: Path) -> None:
    payload = (b"0123456789abcdef" * 131_073) + b"tail"
    (tmp_path / "large.bin").write_bytes(payload)

    expected_sha, _, expected_bytes = _expected_fem_digest(tmp_path)
    sealed = seal_directory(tmp_path)

    assert sealed.sha256 == expected_sha
    assert sealed.total_bytes == expected_bytes == len(payload)


def test_seal_directory_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(AnalysisArtifactError) as caught:
        seal_directory(tmp_path)

    assert caught.value.reason == "empty"


def test_seal_directory_enforces_file_and_byte_bounds(tmp_path: Path) -> None:
    (tmp_path / "one").write_bytes(b"1")
    (tmp_path / "two").write_bytes(b"22")

    with pytest.raises(AnalysisArtifactError) as files:
        seal_directory(tmp_path, maximum_files=1)
    assert files.value.reason == "bounds"

    with pytest.raises(AnalysisArtifactError) as bytes_error:
        seal_directory(tmp_path, maximum_bytes=2)
    assert bytes_error.value.reason == "bounds"


def test_seal_directory_rejects_symbolic_links(tmp_path: Path) -> None:
    target = tmp_path / "target.dat"
    target.write_bytes(b"payload")
    link = tmp_path / "linked.dat"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this test platform")

    with pytest.raises(AnalysisArtifactError) as caught:
        seal_directory(tmp_path)

    assert caught.value.reason == "unsafe_symlink"
    assert caught.value.relative_path == "linked.dat"
