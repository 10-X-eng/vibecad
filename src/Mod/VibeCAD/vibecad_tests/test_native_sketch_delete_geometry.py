# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeSketchDeleteGeometry import (
    create_sketch_delete_geometry,
    preflight_sketch_delete_geometry,
    prepare_sketch_delete_geometry,
    verify_sketch_delete_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeLine,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y), z=0.0)


def _values(**updates) -> dict[str, object]:
    values: dict[str, object] = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 3,
        "expected_constraint_count": 0,
        "geometry_ids": [101],
    }
    values.update(updates)
    return values


def _host(monkeypatch):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo = []
    sketch.enablePersistentGeometryTags()
    sketch.GeometryFacadeList[0].Tag = "delete-geometry-0"
    sketch._next_geometry_tag = 1
    sketch.addGeometry(FakeLine(_point(0, 2), _point(5, 2)), False)
    sketch.addGeometry(FakeLine(_point(0, 4), _point(5, 4)), False)
    return document, sketch, context


def _install_delete(sketch):
    calls = []

    def delete(self, indices, no_solve):
        calls.append((tuple(indices), no_solve))
        assert indices == [1]
        assert no_solve is True
        deleted_tag = self.GeometryFacadeList[1].Tag
        del self.Geometry[1]
        del self.GeometryFacadeList[1]
        self.GeometryCount = len(self.Geometry)
        deleted_constraints = []
        if self.Constraints:
            assert len(self.Constraints) == 1
            constraint = self.Constraints.pop()
            deleted_constraints.append({"index": 0, "tag": constraint.Tag})
            self.ConstraintCount = 0
        return {
            "geometry": {
                "identity": "native_tag",
                "old_to_new": {"0": 0, "2": 1},
                "deleted": [{"index": 1, "tag": deleted_tag}],
                "created": [],
            },
            "constraints": {
                "identity": "native_tag",
                "old_to_new": {},
                "deleted": deleted_constraints,
                "created": [],
            },
        }

    sketch.delGeometries = MethodType(delete, sketch)
    return calls


def test_delete_geometry_rejects_open_unbounded_or_stale_targets(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    for values in (
        {**_values(), "unexpected": True},
        {**_values(), "geometry_ids": []},
        {**_values(), "geometry_ids": [101, 101]},
        {**_values(), "geometry_ids": [-1]},
        {**_values(), "geometry_ids": list(range(65))},
    ):
        with pytest.raises(NativeSketchError):
            prepare_sketch_delete_geometry(document.Uid, values)

    with pytest.raises(
        NativeSketchError,
        match=r"geometry_ids \[999\].*available geometry_ids are \[100, 101, 102\]",
    ):
        preflight_sketch_delete_geometry(
            context,
            prepare_sketch_delete_geometry(
                document.Uid,
                _values(geometry_ids=[999]),
            ),
        )
def test_delete_geometry_requires_group_handle_and_owning_curve(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Group", [2, 0, 0, 0, 1, 0]))
    with pytest.raises(NativeSketchError, match="group handle geometry_id 102"):
        preflight_sketch_delete_geometry(
            context,
            prepare_sketch_delete_geometry(
                document.Uid,
                _values(expected_constraint_count=1, geometry_ids=[101]),
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[1].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal helper"):
        preflight_sketch_delete_geometry(
            context,
            prepare_sketch_delete_geometry(document.Uid, _values()),
        )


def test_delete_geometry_executes_and_verifies_exact_identity(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    calls = _install_delete(sketch)
    prepared = preflight_sketch_delete_geometry(
        context,
        prepare_sketch_delete_geometry(document.Uid, _values()),
    )

    result = verify_sketch_delete_geometry(
        document,
        create_sketch_delete_geometry(document, prepared),
    )

    assert calls == [((1,), True)]
    assert result["operation"] == "delete_geometry"
    assert result["requested_geometry_ids"] == [101]
    assert result["deleted_geometry_count"] == 1
    assert result["deleted_constraint_count"] == 0
    assert result["geometry_count"] == 2


def test_delete_geometry_verifies_deleted_constraint_identity(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    constraint = FakeConstraint("Horizontal", 1)
    constraint.Tag = "delete-constraint-0"
    sketch.addConstraint(constraint)
    calls = _install_delete(sketch)
    prepared = preflight_sketch_delete_geometry(
        context,
        prepare_sketch_delete_geometry(
            document.Uid,
            _values(expected_constraint_count=1),
        ),
    )

    result = verify_sketch_delete_geometry(
        document,
        create_sketch_delete_geometry(document, prepared),
    )

    assert calls == [((1,), True)]
    assert result["deleted_constraint_count"] == 1
    assert result["constraint_count"] == 0


def test_delete_geometry_rejects_post_preflight_drift(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = preflight_sketch_delete_geometry(
        context,
        prepare_sketch_delete_geometry(document.Uid, _values()),
    )
    sketch.Geometry[0].EndPoint.x = 8.0
    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_delete_geometry(document, prepared)


def test_geometry_runtime_routes_delete_through_one_exact_transaction(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    calls = _install_delete(sketch)
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "delete_geometry", **_values()},
        ticket=None,
    )

    assert calls == [((1,), True)]
    assert captured["transaction_name"] == "Delete Native Sketch Geometry"
    assert result["operation"] == "delete_geometry"
