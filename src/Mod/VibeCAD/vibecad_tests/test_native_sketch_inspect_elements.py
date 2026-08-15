# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

import VibeCADNativeSketchElementSelection as element_module
from VibeCADNativeSketchElementSelection import (
    prepare_element_selection,
    read_associated_elements,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInspectRuntime import NativeSketchInspectRuntime
from vibecad_tests.native_sketch_inspect_test_support import install_inspect_host


def _constraint(index: int, constraint_type: str, name: str = "") -> dict:
    return {
        "constraint_index": index,
        "expected_type": constraint_type,
        "expected_name": name,
    }


def _values(values: dict, *constraints: dict) -> dict:
    result = {key: value for key, value in values.items() if key != "selection"}
    result["constraints"] = list(constraints)
    return result


def _elements(result: dict) -> list[tuple[int, str]]:
    return [
        (item["geometry_index"], item["position"])
        for item in result["associated_elements"]
    ]


@pytest.mark.parametrize(
    ("constraint", "expected"),
    (
        (_constraint(0, "Horizontal"), [(0, "whole")]),
        (_constraint(1, "Coincident", "JoinedEndpoint"), [(0, "end"), (1, "start")]),
        (
            _constraint(5, "Group"),
            [(3, "whole"), (0, "whole"), (1, "whole"), (2, "whole")],
        ),
        (
            _constraint(4, "PointOnObject"),
            [(0, "start"), (-3, "whole")],
        ),
        (
            _constraint(6, "InternalAlignment"),
            [(3, "start"), (1, "whole")],
        ),
        (_constraint(7, "DistanceX"), [(0, "start"), (-1, "start")]),
    ),
)
def test_element_lookup_matches_full_constraint_relationships(
    monkeypatch,
    constraint,
    expected,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(values, constraint)
    spec = prepare_element_selection(document.Uid, request)
    before = element_module._freeze_state(sketch, spec)
    result = read_associated_elements(context, spec)
    assert result["operation"] == "select_elements"
    assert _elements(result) == expected
    assert result["selected_constraint_count"] == 1
    assert result["associated_element_count"] == len(expected)
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 8
    assert result["external_geometry_count"] == 1
    assert len(result["geometry_state_sha256"]) == 64
    assert len(result["constraint_state_sha256"]) == 64
    assert element_module._freeze_state(sketch, spec) == before


def test_element_lookup_deduplicates_targets_and_reports_match_indices(
    monkeypatch,
) -> None:
    document, _sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(
        values,
        _constraint(0, "Horizontal"),
        _constraint(5, "Group"),
    )
    result = read_associated_elements(
        context,
        prepare_element_selection(document.Uid, request),
    )
    assert _elements(result) == [
        (0, "whole"),
        (3, "whole"),
        (1, "whole"),
        (2, "whole"),
    ]
    assert [
        item["matched_constraint_selection_indices"]
        for item in result["associated_elements"]
    ] == [[0, 1], [1], [1], [1]]


@pytest.mark.parametrize(
    "change",
    (
        lambda request: request.update({"unexpected": True}),
        lambda request: request.update({"expected_geometry_count": 5}),
        lambda request: request.update({"expected_constraint_count": 9}),
        lambda request: request.update({"expected_external_geometry_count": 2}),
        lambda request: request.update({"constraints": []}),
        lambda request: request.update(
            {
                "constraints": [
                    _constraint(0, "Horizontal"),
                    _constraint(0, "Horizontal"),
                ]
            }
        ),
        lambda request: request.update(
            {"constraints": [_constraint(99, "Horizontal")]}
        ),
        lambda request: request.update({"constraints": [_constraint(0, "Vertical")]}),
        lambda request: request.update(
            {"constraints": [_constraint(0, "Horizontal", "StaleName")]}
        ),
    ),
)
def test_element_lookup_rejects_closed_stale_duplicate_and_invalid_targets(
    monkeypatch,
    change,
) -> None:
    document, _sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(values, _constraint(0, "Horizontal"))
    change(request)
    with pytest.raises(NativeSketchError):
        read_associated_elements(
            context,
            prepare_element_selection(document.Uid, request),
        )


@pytest.mark.parametrize("flag", ("Missing", "Detached", "Sync"))
def test_element_lookup_rejects_unhealthy_related_external_geometry(
    monkeypatch,
    flag,
) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(values, _constraint(4, "PointOnObject"))
    sketch.ExternalGeo[2].extension.setFlag(flag, True)
    with pytest.raises(NativeSketchError, match="stable exact selection"):
        read_associated_elements(
            context,
            prepare_element_selection(document.Uid, request),
        )


def test_element_lookup_rejects_state_change_during_read(monkeypatch) -> None:
    document, sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(values, _constraint(0, "Horizontal"))
    original = element_module._result

    def drift(prepared):
        result = original(prepared)
        sketch.Constraints[0].Name = "ChangedDuringRead"
        return result

    monkeypatch.setattr(element_module, "_result", drift)
    with pytest.raises(NativeSketchError, match="changed during"):
        read_associated_elements(
            context,
            prepare_element_selection(document.Uid, request),
        )


def test_sketch_inspect_runtime_dispatches_element_lookup_strictly(monkeypatch) -> None:
    _document, _sketch, context, values = install_inspect_host(monkeypatch)
    request = _values(values, _constraint(1, "Coincident", "JoinedEndpoint"))
    result = NativeSketchInspectRuntime(context).inspect(
        {"operation": "select_elements", **request}
    )
    assert _elements(result) == [(0, "end"), (1, "start")]
    with pytest.raises(Exception, match="arguments"):
        NativeSketchInspectRuntime(context).inspect(
            {"operation": "select_elements", **request, "unexpected": True}
        )
