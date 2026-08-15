# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchConstruction import (
    create_sketch_construction,
    preflight_sketch_construction,
    prepare_sketch_construction,
    verify_sketch_construction,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    FakeLine,
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "targets": [{"geometry_index": 0, "expected_state": False}],
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_construction(
        context,
        prepare_sketch_construction(document.Uid, values),
    )


def test_construction_toggles_internal_and_external_human_states(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    external = FakeExternalLine("Support.Edge1", defining=True)
    sketch.ExternalGeo.append(external)
    values = _values(
        expected_external_geometry_count=1,
        targets=[
            {"geometry_index": 0, "expected_state": False},
            {"geometry_index": -3, "expected_state": True},
        ],
    )
    prepared = _prepared(document, context, values)

    draft = create_sketch_construction(document, prepared)
    result = verify_sketch_construction(document, draft)

    assert sketch.getConstruction(0) is True
    assert external.extension.testFlag("Defining") is False
    assert draft.recompute_targets == (sketch,)
    assert [identity.object_name for identity in draft.changed] == [sketch.Name]
    assert result["operation"] == "toggle_construction"
    assert result["external_geometry_count"] == 1
    assert result["changed_geometry"] == [
        {
            "geometry_index": 0,
            "geometry_kind": "line",
            "state_kind": "construction",
            "previous_state": False,
            "current_state": True,
        },
        {
            "geometry_index": -3,
            "geometry_kind": "line",
            "state_kind": "defining",
            "previous_state": True,
            "current_state": False,
        },
    ]


@pytest.mark.parametrize(
    "updates",
    (
        {"targets": []},
        {"targets": [{"geometry_index": -2, "expected_state": False}]},
        {"targets": [{"geometry_index": 0, "expected_state": 0}]},
        {
            "targets": [
                {"geometry_index": 0, "expected_state": False},
                {"geometry_index": 0, "expected_state": False},
            ]
        },
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_construction_rejects_invalid_exact_targets(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)

    with pytest.raises(NativeSketchError):
        prepare_sketch_construction(document.Uid, _values(**updates))


def test_construction_rejects_stale_expected_state_before_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)

    with pytest.raises(NativeSketchError, match="state changed"):
        _prepared(
            document,
            context,
            _values(targets=[{"geometry_index": 0, "expected_state": True}]),
        )

    assert sketch.getConstruction(0) is False


def test_construction_rejects_group_member_and_names_handle(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member_index = sketch.addGeometry(FakeLine(), False)
    sketch.addConstraint(
        FakeConstraint("Text", [0, 0, member_index, 0], "A", "Font.ttf", True)
    )

    with pytest.raises(NativeSketchError, match="group handle 0"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                expected_constraint_count=1,
                targets=[{"geometry_index": member_index, "expected_state": False}],
            ),
        )


def test_construction_rejects_internal_alignment_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.GeometryFacadeList[0].InternalType = "BSplineControlPoint"

    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(document, context, _values())


def test_construction_rejects_external_count_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))

    with pytest.raises(NativeSketchError, match="external geometry count changed"):
        _prepared(document, context, _values())


def test_construction_rejects_external_geometry_without_defining_state(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeLine())

    with pytest.raises(NativeSketchError, match="does not expose a defining state"):
        _prepared(
            document,
            context,
            _values(
                expected_external_geometry_count=1,
                targets=[{"geometry_index": -3, "expected_state": False}],
            ),
        )


def test_construction_rejects_preflight_drift_before_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True

    with pytest.raises(NativeSketchError, match="after Construction preflight"):
        create_sketch_construction(document, prepared)

    assert sketch.getConstruction(0) is False


def test_construction_verifier_rejects_unrequested_geometry_change(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    other_index = sketch.addGeometry(FakeLine(), False)
    prepared = _prepared(
        document,
        context,
        _values(expected_geometry_count=2),
    )
    draft = create_sketch_construction(document, prepared)
    sketch.toggleConstruction(other_index)

    with pytest.raises(NativeSketchError, match="beyond the exact requested states"):
        verify_sketch_construction(document, draft)
