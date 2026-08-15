# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInternalAlignment import _validate_exposure_receipt
from VibeCADNativeSketchInternalAlignmentState import (
    InternalHelperBinding,
    _complete_keys,
    _retained_bspline_keys,
    _retained_conic_keys,
    identity_mapping,
)
from VibeCADNativeSketchInternalAlignmentTarget import (
    prepare_sketch_internal_alignment,
)


def _arguments() -> dict:
    return {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 10,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "targets": [
            {"geometry_index": 1, "expected_internal_geometry_count": 0},
            {"geometry_index": 5, "expected_internal_geometry_count": 4},
        ],
    }


def _helper(role: str, index: int, geometry_index: int) -> InternalHelperBinding:
    return InternalHelperBinding(
        role,
        index,
        geometry_index,
        f"geometry-{geometry_index}",
        geometry_index,
        f"constraint-{geometry_index}",
    )


def _constraint(kind: str, *indices: int, name: str = "") -> dict:
    result = {
        "type": kind,
        "references": [
            {"slot": slot, "geometry_index": index}
            for slot, index in enumerate(indices, start=1)
        ],
    }
    if name:
        result["name"] = name
    return result


def test_internal_alignment_target_parser_is_closed_and_distinct() -> None:
    spec = prepare_sketch_internal_alignment("document-uid", _arguments())
    assert spec.target.reference.object_name == "Sketch"
    assert spec.expected_external_reference_count == 1
    assert spec.expected_external_geometry_count == 2
    assert tuple(item.geometry_index for item in spec.targets) == (1, 5)
    assert tuple(item.expected_internal_geometry_count for item in spec.targets) == (
        0,
        4,
    )
    for invalid in (
        {**_arguments(), "unexpected": True},
        {**_arguments(), "targets": []},
        {**_arguments(), "targets": _arguments()["targets"] * 33},
        {
            **_arguments(),
            "targets": [
                {"geometry_index": 1, "expected_internal_geometry_count": 0},
                {"geometry_index": 1, "expected_internal_geometry_count": 1},
            ],
        },
        {
            **_arguments(),
            "targets": [{"geometry_index": -1, "expected_internal_geometry_count": 0}],
        },
        {**_arguments(), "expected_external_geometry_count": True},
    ):
        with pytest.raises(NativeSketchError):
            prepare_sketch_internal_alignment("document-uid", invalid)


def test_complete_roles_cover_every_supported_human_curve() -> None:
    assert len(_complete_keys({"type_id": "Part::GeomEllipse"})) == 4
    assert len(_complete_keys({"type_id": "Part::GeomArcOfEllipse"})) == 4
    assert len(_complete_keys({"type_id": "Part::GeomArcOfHyperbola"})) == 3
    assert len(_complete_keys({"type_id": "Part::GeomArcOfParabola"})) == 2
    assert _complete_keys(
        {
            "type_id": "Part::GeomBSplineCurve",
            "pole_count": 3,
            "knot_count": 2,
        }
    ) == (
        ("BSplineControlPoint", 0),
        ("BSplineControlPoint", 1),
        ("BSplineControlPoint", 2),
        ("BSplineKnotPoint", 0),
        ("BSplineKnotPoint", 1),
    )
    with pytest.raises(NativeSketchError, match="ellipse, conic arc, or B-spline"):
        _complete_keys({"type_id": "Part::GeomLineSegment"})


def test_conic_cleanup_retains_only_helpers_with_custom_constraints() -> None:
    helpers = (
        _helper("EllipseMajorDiameter", -1, 5),
        _helper("EllipseMinorDiameter", -1, 6),
        _helper("EllipseFocus1", -1, 7),
        _helper("EllipseFocus2", -1, 8),
    )
    constraints = tuple(
        [_constraint("InternalAlignment", item.geometry_index, 0) for item in helpers]
        + [_constraint("Distance", 5)]
    )
    assert _retained_conic_keys("Part::GeomEllipse", helpers, constraints) == (
        ("EllipseMajorDiameter", -1),
    )


def test_bspline_cleanup_matches_weight_equal_and_custom_rules() -> None:
    helpers = (
        _helper("BSplineControlPoint", 0, 5),
        _helper("BSplineControlPoint", 1, 6),
        _helper("BSplineKnotPoint", 0, 7),
    )
    constraints = (
        _constraint("InternalAlignment", 5, 0),
        _constraint("InternalAlignment", 6, 0),
        _constraint("InternalAlignment", 7, 0),
        _constraint("Weight", 5),
        _constraint("Equal", 5, 6),
        _constraint("DistanceX", 6),
        _constraint("Coincident", 7, 9),
    )
    assert _retained_bspline_keys(helpers, constraints) == (
        ("BSplineControlPoint", 1),
        ("BSplineKnotPoint", 0),
    )


def test_exposure_receipt_accepts_host_count_quirk_but_not_wrong_roles() -> None:
    receipt = {
        "source_geometry_index": 0,
        "geometry_count_before": 1,
        "geometry_count_after": 5,
        "created_count": 3,
        "created": [
            {"geometry_index": 1, "geometry_id": 11, "role": role}
            for role in (
                "EllipseMajorDiameter",
                "EllipseMinorDiameter",
                "EllipseFocus1",
                "EllipseFocus2",
            )
        ],
    }
    for offset, item in enumerate(receipt["created"], start=1):
        item["geometry_index"] = offset
    missing = {
        ("EllipseMajorDiameter", -1),
        ("EllipseMinorDiameter", -1),
        ("EllipseFocus1", -1),
        ("EllipseFocus2", -1),
    }
    _validate_exposure_receipt(
        receipt,
        source_index=0,
        before_count=1,
        after_count=5,
        missing_keys=missing,
    )
    receipt["created"][0]["role"] = "WrongRole"
    with pytest.raises(NativeSketchError, match="unexpected helper roles"):
        _validate_exposure_receipt(
            receipt,
            source_index=0,
            before_count=1,
            after_count=5,
            missing_keys=missing,
        )


def test_identity_mapping_is_stable_across_index_compaction() -> None:
    mapping, deleted, created = identity_mapping(
        ("root-a", "helper", "root-b"),
        ("root-a", "root-b", "new-helper"),
    )
    assert mapping == {0: 0, 2: 1}
    assert deleted == {1}
    assert created == {2}
