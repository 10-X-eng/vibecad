# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state boundaries for documents containing multiple CAM setups."""

from __future__ import annotations

import hashlib
import sys
import types
from types import SimpleNamespace

import VibeCADNativeManufactureState as manufacture_state
from VibeCADNativeManufactureJob import _job_count
from VibeCADNativeManufactureOperationSupport import (
    _job_resources_are_unchanged,
    _public_shape_is_unchanged,
    shape_sha256,
)


def _document(*objects):
    by_name = {item.Name: item for item in objects}
    document = SimpleNamespace(Objects=list(objects))
    document.getObject = by_name.get
    for item in objects:
        item.Document = document
    return document


def test_other_setup_snapshot_excludes_owned_jobs_and_detects_semantic_change(
    monkeypatch,
):
    first = SimpleNamespace(Name="SetupOne", state_sha256="a" * 64)
    second = SimpleNamespace(Name="SetupTwo", state_sha256="b" * 64)
    model = SimpleNamespace(Name="Model", state_sha256="c" * 64)
    document = _document(first, model, second)

    monkeypatch.setattr(
        manufacture_state,
        "is_job",
        lambda item: item is first or item is second,
    )
    monkeypatch.setattr(
        manufacture_state,
        "job_state",
        lambda item: {"state_sha256": item.state_sha256},
    )

    frozen = manufacture_state.capture_other_job_states(document, (first,))

    assert frozen == ((second, "b" * 64),)
    assert manufacture_state.other_job_states_are_current(document, frozen)

    second.state_sha256 = "d" * 64
    assert not manufacture_state.other_job_states_are_current(document, frozen)


def test_other_setup_snapshot_detects_replaced_job_identity(monkeypatch):
    first = SimpleNamespace(Name="SetupOne", state_sha256="a" * 64)
    document = _document(first)
    monkeypatch.setattr(manufacture_state, "is_job", lambda _item: True)
    monkeypatch.setattr(
        manufacture_state,
        "job_state",
        lambda item: {"state_sha256": item.state_sha256},
    )
    frozen = manufacture_state.capture_other_job_states(document, ())

    replacement = SimpleNamespace(
        Name="SetupOne",
        state_sha256="a" * 64,
        Document=document,
    )
    document.Objects[:] = [replacement]
    document.getObject = lambda name: replacement if name == "SetupOne" else None

    assert not manufacture_state.other_job_states_are_current(document, frozen)


def test_job_inventory_count_has_no_product_setup_cap() -> None:
    assert _job_count(10_000) == 10_000


def test_persistent_value_uses_uuid_instead_of_wrapper_address() -> None:
    class MaterialValue:
        UUID = "11111111-2222-3333-4444-555555555555"

        def __init__(self, address: str) -> None:
            self._address = address

        def __str__(self) -> str:
            return f"<Material at {self._address}>"

    first = manufacture_state._stable_property_value(MaterialValue("0000000000000001"))
    second = manufacture_state._stable_property_value(MaterialValue("0000000000000002"))

    assert first == {"uuid": MaterialValue.UUID}
    assert second == first


def test_job_model_state_survives_history_replacement(monkeypatch) -> None:
    shape = SimpleNamespace(
        ShapeType="Solid",
        isNull=lambda: False,
        isValid=lambda: True,
    )
    model = SimpleNamespace(Name="Body", Shape=shape)
    document = _document(model)
    path = types.ModuleType("Path")
    base = types.ModuleType("Path.Base")
    util = types.ModuleType("Path.Base.Util")
    util.isValidBaseObject = lambda value: value is model
    path.Base = base
    base.Util = util
    monkeypatch.setitem(sys.modules, "Path", path)
    monkeypatch.setitem(sys.modules, "Path.Base", base)
    monkeypatch.setitem(sys.modules, "Path.Base.Util", util)
    monkeypatch.setattr(
        manufacture_state,
        "mesh_object_state",
        lambda value: {
            "object_name": value.Name,
            "type_id": "PartDesign::Body",
            "state_sha256": "0" * 64,
            "state": ["Up-to-date"],
            "topology": {"solids": 1},
        },
    )
    monkeypatch.setattr(
        manufacture_state,
        "_is_usable",
        lambda _value, _document: (_ for _ in ()).throw(
            AssertionError("Job-owned source state must not depend on History usability")
        ),
    )

    state = manufacture_state._job_model_state(model)

    assert state["object_name"] == "Body"
    assert state["shape_type"] == "Solid"
    assert state["state_sha256"] != "0" * 64


def test_public_shape_snapshot_accepts_equal_brep_with_new_kernel_identity() -> None:
    before = SimpleNamespace(
        isSame=lambda _other: False,
        exportBrepToString=lambda: "exact-brep",
    )
    after = SimpleNamespace(
        isSame=lambda _other: False,
        exportBrepToString=lambda: "exact-brep",
    )

    unchanged, actual_sha256 = _public_shape_is_unchanged(
        after,
        before,
        hashlib.sha256(b"exact-brep").hexdigest(),
        "CAM model Body",
    )

    assert unchanged is True
    assert actual_sha256 == hashlib.sha256(b"exact-brep").hexdigest()


def test_public_shape_snapshot_rejects_changed_brep() -> None:
    before = SimpleNamespace(
        isSame=lambda _other: False,
        exportBrepToString=lambda: "before",
    )
    after = SimpleNamespace(
        isSame=lambda _other: False,
        exportBrepToString=lambda: "after",
    )

    unchanged, actual_sha256 = _public_shape_is_unchanged(
        after,
        before,
        hashlib.sha256(b"before").hexdigest(),
        "CAM model Body",
    )

    assert unchanged is False
    assert actual_sha256 == hashlib.sha256(b"after").hexdigest()


def test_shape_fingerprint_ignores_only_kernel_tolerance_drift() -> None:
    class Shape:
        def __init__(self, geometry: str, tolerance: float):
            self.geometry = geometry
            self.tolerance = tolerance

        def copy(self):
            return Shape(self.geometry, self.tolerance)

        def fixTolerance(self, value):
            self.tolerance = value

        def exportBrepToString(self):
            return f"{self.geometry}:{self.tolerance:.12g}"

    before = Shape("same-geometry", 1.00000011e-7)
    after = Shape("same-geometry", 1.00000001e-7)
    changed = Shape("changed-geometry", 1.00000001e-7)

    before_sha256 = shape_sha256(before, "CAM model Body")
    after_sha256 = shape_sha256(after, "CAM model Body")
    assert before_sha256 != after_sha256
    assert shape_sha256(before, "CAM model Body") != shape_sha256(
        changed, "CAM model Body"
    )
    unchanged, actual_sha256 = _public_shape_is_unchanged(
        after,
        before,
        before_sha256,
        "CAM model Body",
    )
    assert unchanged is True
    assert actual_sha256 == after_sha256
    changed_geometry, changed_sha256 = _public_shape_is_unchanged(
        changed,
        before,
        before_sha256,
        "CAM model Body",
    )
    assert changed_geometry is False
    assert changed_sha256 == shape_sha256(changed, "CAM model Body")
    assert before.tolerance == 1.00000011e-7
    assert after.tolerance == 1.00000001e-7


def test_operation_resource_proof_ignores_only_transient_recompute_state() -> None:
    before = {
        "models": [
            {
                "object_name": "Body",
                "resource_name": "Clone",
                "resource_state_sha256": "a" * 64,
                "state": ["Touched"],
            }
        ],
        "tools": [
            {
                "object_name": "ToolController",
                "state_sha256": "b" * 64,
                "state": ["Touched"],
            }
        ],
    }
    after = {
        "models": [{**before["models"][0], "state": ["Up-to-date"]}],
        "tools": [{**before["tools"][0], "state": ["Up-to-date"]}],
    }

    assert _job_resources_are_unchanged(before, after)
    after["tools"][0]["state_sha256"] = "c" * 64
    assert not _job_resources_are_unchanged(before, after)
