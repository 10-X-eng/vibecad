# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path

from VibeCADMeshCacheAtomic import atomic_cache_temporary_path


VIBECAD_ROOT = Path(__file__).resolve().parents[1]
MESH_CACHE_JOBS = (
    "VibeCADMeshBooleanJob.py",
    "VibeCADMeshConversionJob.py",
    "VibeCADMeshCurvatureJob.py",
    "VibeCADMeshCutJob.py",
    "VibeCADMeshModificationJob.py",
    "VibeCADMeshSegmentationJob.py",
    "VibeCADMeshTessellationJob.py",
)


def test_atomic_cache_temporary_name_has_a_bounded_windows_path_component() -> None:
    directory = Path("C:/") / ("nested-application-cache-" + "x" * 180)
    temporary = atomic_cache_temporary_path(
        directory,
        role="mesh-artifact",
        token="0123456789abcdef",
    )

    assert temporary.parent == directory
    assert temporary.name == ".mesh-artifact-0123456789abcdef.tmp"
    assert len(temporary.name) <= 48


def test_every_mesh_cache_job_uses_the_bounded_atomic_path_helper() -> None:
    for filename in MESH_CACHE_JOBS:
        source = (VIBECAD_ROOT / filename).read_text(encoding="utf-8")
        assert "atomic_cache_temporary_path" in source, filename
        assert 'f".{artifact.name}.' not in source, filename
        assert 'f".{metadata_path.name}.' not in source, filename
