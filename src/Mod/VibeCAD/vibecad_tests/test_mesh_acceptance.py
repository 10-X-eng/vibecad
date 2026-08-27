# SPDX-License-Identifier: LGPL-2.1-or-later

import pytest

from vibecad_tests.mesh_acceptance import validate_mesh_quality


def _object(*, solid: bool, watertight: bool, issues: dict[str, int]) -> dict:
    return {
        "object_name": "MeshResult",
        "label": "Mesh Result",
        "points": 8,
        "facets": 12,
        "solid": solid,
        "watertight": watertight,
        "issue_counts": issues,
    }


def test_mesh_acceptance_rejects_open_subsets_as_a_repaired_solid() -> None:
    with pytest.raises(AssertionError, match="all_solid"):
        validate_mesh_quality(
            [
                _object(solid=False, watertight=False, issues={}),
                _object(solid=False, watertight=False, issues={}),
            ],
            {
                "object_count": 2,
                "all_solid": True,
                "all_watertight": True,
                "maximum_issue_counts": {"self_intersections": 0},
            },
        )


def test_mesh_acceptance_returns_exact_quality_evidence() -> None:
    evidence = validate_mesh_quality(
        [_object(solid=True, watertight=True, issues={"surface_fold_overs": 3})],
        {
            "object_count": 1,
            "all_solid": True,
            "all_watertight": True,
            "maximum_issue_counts": {"surface_fold_overs": 3},
        },
    )

    assert evidence == {
        "object_count": 1,
        "point_count": 8,
        "facet_count": 12,
        "solid_count": 1,
        "watertight_count": 1,
        "issue_counts": {"surface_fold_overs": 3},
        "objects": [
            _object(solid=True, watertight=True, issues={"surface_fold_overs": 3})
        ],
    }
