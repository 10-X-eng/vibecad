# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
from pathlib import Path
import tarfile
import stat
import zipfile

import pytest

from VibeCADAnalysisArtifacts import (
    AnalysisArtifactError,
    ARTIFACT_MANIFEST_VERSION,
    ArtifactManifest,
    ContentAddressedArtifactStore,
    FEM_COMPAT_DIGEST_ALGORITHM,
    seal_artifact,
    seal_directory,
    validate_archive,
    verify_artifact,
)


def _seal(path: Path, root: Path):
    return seal_artifact(
        path, root=root, role="solver_output", logical_name="forces",
        media_type="application/json", producer_id="adapter/1", job_id="job-1",
        provider_id="local", solver_id="calculix", source_correlation="body:Pad",
        exactness_class="derived", created_at="2026-08-27T00:00:00Z",
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


def test_artifact_manifest_is_complete_canonical_and_detects_mutation(tmp_path: Path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_bytes(b'{"force": 12.5}')
    descriptor = _seal(artifact, tmp_path)
    manifest = ArtifactManifest(ARTIFACT_MANIFEST_VERSION, (descriptor,))

    assert descriptor.relative_path == "result.json"
    assert descriptor.byte_count == artifact.stat().st_size
    assert len(manifest.sha256) == 64
    assert manifest.canonical_json() == manifest.canonical_json()
    verify_artifact(artifact, descriptor)

    artifact.write_bytes(b'{"force": 99}')
    with pytest.raises(AnalysisArtifactError) as caught:
        verify_artifact(artifact, descriptor)
    assert caught.value.reason == "hash_mismatch"


def test_seal_artifact_rejects_escape_and_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-analysis-artifact.bin"
    outside.write_bytes(b"outside")
    try:
        with pytest.raises(AnalysisArtifactError) as escaped:
            _seal(outside, tmp_path)
        assert escaped.value.reason == "unsafe_path"
    finally:
        outside.unlink(missing_ok=True)

    target = tmp_path / "target.bin"
    target.write_bytes(b"target")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this test platform")
    with pytest.raises(AnalysisArtifactError) as linked:
        _seal(link, tmp_path)
    assert linked.value.reason == "unsafe_symlink"


def test_archive_validation_rejects_traversal_links_and_expansion_bounds(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape.txt", b"escape")
    with pytest.raises(AnalysisArtifactError) as unsafe:
        validate_archive(traversal)
    assert unsafe.value.reason == "unsafe_path"

    bounded = tmp_path / "bounded.zip"
    with zipfile.ZipFile(bounded, "w") as archive:
        archive.writestr("one", b"1")
        archive.writestr("two", b"2")
    with pytest.raises(AnalysisArtifactError) as bounds:
        validate_archive(bounded, maximum_files=1)
    assert bounds.value.reason == "bounds"

    zip_link = tmp_path / "linked.zip"
    with zipfile.ZipFile(zip_link, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    with pytest.raises(AnalysisArtifactError) as unsafe_zip_link:
        validate_archive(zip_link)
    assert unsafe_zip_link.value.reason == "unsafe_archive"

    linked = tmp_path / "linked.tar"
    with tarfile.open(linked, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    with pytest.raises(AnalysisArtifactError) as unsafe_link:
        validate_archive(linked)
    assert unsafe_link.value.reason == "unsafe_archive"


def test_content_store_admission_is_atomic_idempotent_and_evidence_aware(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "result.bin"
    artifact.write_bytes(b"verified result")
    descriptor = _seal(artifact, workspace)
    store = ContentAddressedArtifactStore(tmp_path / "store")

    admitted = store.admit(artifact, descriptor)
    assert admitted == store.path_for(descriptor.sha256)
    assert admitted.read_bytes() == b"verified result"
    assert store.admit(artifact, descriptor) == admitted
    assert not store.cleanup(descriptor.sha256, protected_sha256=(descriptor.sha256,))
    assert admitted.exists()
    assert store.cleanup(descriptor.sha256)
    assert not store.cleanup(descriptor.sha256)


def test_content_store_reverifies_only_regular_admitted_objects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "result.bin"
    artifact.write_bytes(b"verified result")
    descriptor = _seal(artifact, workspace)
    store = ContentAddressedArtifactStore(tmp_path / "store")

    admitted = store.admit(artifact, descriptor)
    assert store.verify_admitted(descriptor) == admitted

    admitted.unlink()
    admitted.mkdir()
    with pytest.raises(AnalysisArtifactError) as non_file:
        store.verify_admitted(descriptor)
    assert non_file.value.reason == "invalid_manifest"

    admitted.rmdir()
    with pytest.raises(AnalysisArtifactError) as missing:
        store.verify_admitted(descriptor)
    assert missing.value.reason == "read_failed"


def test_content_store_reverification_rejects_symlinked_object(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "result.bin"
    artifact.write_bytes(b"verified result")
    descriptor = _seal(artifact, workspace)
    store = ContentAddressedArtifactStore(tmp_path / "store")
    admitted = store.admit(artifact, descriptor)

    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(artifact.read_bytes())
    admitted.unlink()
    try:
        admitted.symlink_to(replacement)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this test platform")

    with pytest.raises(AnalysisArtifactError) as linked:
        store.verify_admitted(descriptor)
    assert linked.value.reason == "unsafe_symlink"


def test_content_store_enforces_unique_object_count_and_byte_quotas(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first.bin"
    first.write_bytes(b"first")
    second = workspace / "second.bin"
    second.write_bytes(b"second")
    first_descriptor = _seal(first, workspace)
    second_descriptor = _seal(second, workspace)
    store = ContentAddressedArtifactStore(
        tmp_path / "store",
        maximum_artifacts=1,
        maximum_bytes=first_descriptor.byte_count,
    )

    admitted = store.admit(first, first_descriptor)
    assert store.usage() == {"artifact_count": 1, "total_bytes": 5}
    assert store.admit(first, first_descriptor) == admitted

    with pytest.raises(AnalysisArtifactError) as count_bound:
        store.admit(second, second_descriptor)
    assert count_bound.value.reason == "bounds"
    assert store.usage() == {"artifact_count": 1, "total_bytes": 5}

    assert store.cleanup(first_descriptor.sha256)
    with pytest.raises(AnalysisArtifactError) as byte_bound:
        store.admit(second, second_descriptor)
    assert byte_bound.value.reason == "bounds"
    assert store.usage() == {"artifact_count": 0, "total_bytes": 0}
