# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeSketchConstraintSelection as selection_module
from VibeCADNativeSketchConstraintSelection import (
    prepare_constraint_selection,
    read_associated_constraints,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInspectRuntime import NativeSketchInspectRuntime
from vibecad_tests.native_sketch_inspect_test_support import install_inspect_host


def _indices(result: dict) -> list[int]:
    return [item["constraint_index"] for item in result["associated_constraints"]]


@pytest.mark.parametrize(
    ("selection", "expected"),
    (
        (
            [{"geometry_index": 0, "position": "whole"}],
            [
                0,
                1,
                4,
                5,
                7,
            ],
        ),
        (
            [{"geometry_index": 0, "position": "end"}],
            [1],
        ),
        (
            [{"geometry_index": 2, "position": "whole"}],
            [3, 5],
        ),
        (
            [{"geometry_index": 3, "position": "start"}],
            [6],
        ),
        (
            [{"geometry_index": -3, "position": "whole"}],
            [4],
        ),
        (
            [{"geometry_index": -1, "position": "start"}],
            [7],
        ),
    ),
)
def test_constraint_lookup_matches_whole_point_group_internal_external_and_axis_semantics(
    monkeypatch,
    selection,
    expected,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    values["selection"] = selection
    spec = prepare_constraint_selection(document.Uid, values)
    before = selection_module._freeze_state(sketch, spec)
    result = read_associated_constraints(
        context,
        spec,
    )
    assert _indices(result) == expected
    assert result["operation"] == "select_constraints"
    assert result["selection"] == selection
    assert result["selection_count"] == len(selection)
    assert result["associated_constraint_count"] == len(expected)
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 8
    assert result["external_geometry_count"] == 1
    assert len(result["geometry_state_sha256"]) == 64
    assert len(result["constraint_state_sha256"]) == 64
    assert selection_module._freeze_state(sketch, spec) == before


def test_multiple_elements_return_each_constraint_once_with_match_indices(
    monkeypatch,
) -> None:
    document, _sketch, context, values = install_inspect_host(monkeypatch)
    values["selection"] = [
        {"geometry_index": 0, "position": "end"},
        {"geometry_index": 2, "position": "whole"},
    ]
    result = read_associated_constraints(
        context,
        prepare_constraint_selection(document.Uid, values),
    )
    assert _indices(result) == [1, 3, 5]
    assert [
        item["matched_selection_indices"] for item in result["associated_constraints"]
    ] == [[0], [1], [1]]


def test_constraint_lookup_ignores_exact_host_undefined_element_padding(
    monkeypatch,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    sketch.Constraints[0].Elements += ((-2000, 0), (-2000, 0))
    result = read_associated_constraints(
        context,
        prepare_constraint_selection(document.Uid, values),
    )
    assert _indices(result)[0] == 0


@pytest.mark.parametrize(
    "change",
    (
        lambda values: values.update({"unexpected": True}),
        lambda values: values.update({"expected_geometry_count": 5}),
        lambda values: values.update({"expected_constraint_count": 9}),
        lambda values: values.update({"expected_external_geometry_count": 2}),
        lambda values: values.update({"selection": []}),
        lambda values: values.update(
            {
                "selection": [
                    {"geometry_index": 0, "position": "whole"},
                    {"geometry_index": 0, "position": "whole"},
                ]
            }
        ),
        lambda values: values.update(
            {"selection": [{"geometry_index": 99, "position": "whole"}]}
        ),
        lambda values: values.update(
            {"selection": [{"geometry_index": -2, "position": "start"}]}
        ),
    ),
)
def test_constraint_lookup_rejects_closed_stale_duplicate_and_invalid_targets(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, values = install_inspect_host(monkeypatch)
    change(values)
    with pytest.raises(NativeSketchError):
        read_associated_constraints(
            context,
            prepare_constraint_selection(document.Uid, values),
        )


def test_constraint_lookup_requires_unique_live_constraint_tags(
    monkeypatch,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    sketch.Constraints[1].Tag = sketch.Constraints[0].Tag
    with pytest.raises(NativeSketchError, match="live identities"):
        read_associated_constraints(
            context,
            prepare_constraint_selection(document.Uid, values),
        )


@pytest.mark.parametrize("flag", ("Missing", "Detached", "Sync"))
def test_constraint_lookup_rejects_unhealthy_external_geometry(
    monkeypatch,
    flag,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    values["selection"] = [{"geometry_index": -3, "position": "whole"}]
    sketch.ExternalGeo[2].extension.setFlag(flag, True)
    with pytest.raises(NativeSketchError, match="stable exact selection"):
        read_associated_constraints(
            context,
            prepare_constraint_selection(document.Uid, values),
        )


def test_constraint_lookup_rejects_state_change_during_read(monkeypatch) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    original = selection_module._result

    def drift(prepared):
        result = original(prepared)
        sketch.Constraints[0].Name = "ChangedDuringRead"
        return result

    monkeypatch.setattr(selection_module, "_result", drift)
    with pytest.raises(NativeSketchError, match="changed during"):
        read_associated_constraints(
            context,
            prepare_constraint_selection(document.Uid, values),
        )


def test_sketch_inspect_runtime_is_strict_and_read_only(monkeypatch) -> None:
    document, _sketch, context, values = install_inspect_host(monkeypatch)
    runtime = NativeSketchInspectRuntime(context)
    result = runtime.inspect({"operation": "select_constraints", **values})
    assert result["associated_constraint_count"] == 5
    with pytest.raises(Exception, match="arguments"):
        runtime.inspect(
            {"operation": "select_constraints", **values, "unexpected": True}
        )
    with pytest.raises(Exception, match="unavailable"):
        runtime.inspect({"operation": "not_an_operation", **values})
