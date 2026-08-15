# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchSplit import (
    create_sketch_split,
    preflight_sketch_split,
    prepare_sketch_split,
    verify_sketch_split,
)
from VibeCADNativeSketchSplitDiagnostic import parse_sketch_split_diagnostic
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_geometry_records,
)
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeCircle,
    FakeConstraint,
    FakeLine,
    FakePoint,
    fake_facade,
    geometry_target_values,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y), z=0.0)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_constraint_count": 1,
            "expected_external_geometry_count": 0,
            "target": {
                "geometry_index": 0,
                "reference_point_mm": {"x": 8.0, "y": 0.0},
            },
            **updates,
        }
    )


def _host(monkeypatch, *, closed: bool = False, construction: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    target = (
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 10.0)
        if closed
        else FakeLine(_point(0.0, 0.0), _point(20.0, 0.0))
    )
    sketch.Geometry[0] = target
    sketch.GeometryFacadeList[0].Geometry = target
    sketch.GeometryFacadeList[0].Construction = construction
    sketch.enablePersistentGeometryTags()
    assert (
        sketch.addGeometry(FakeLine(_point(30.0, 0.0), _point(30.0, 5.0)), False) == 1
    )
    assert sketch.addConstraint(FakeConstraint("Vertical", 1)) == 0
    sketch.ExpressionEngine = [("Constraints[0]", "5 mm")]
    return document, sketch, context


def _tagged_clone(geometry, tag: str):
    result = copy.deepcopy(geometry)
    result.Tag = tag
    return result


def _metadata(
    geometry_id: int,
    *,
    construction: bool = False,
) -> dict[str, object]:
    return {
        "Id": geometry_id,
        "Construction": construction,
        "Blocked": False,
        "InternalType": "None",
        "GeometryLayerId": 0,
    }


def _final_state(sketch, *, prefix: str):
    source = sketch.Geometry[0]
    construction = bool(sketch.GeometryFacadeList[0].Construction)
    retained = _tagged_clone(sketch.Geometry[1], sketch.GeometryFacadeList[1].Tag)
    if source.TypeId == "Part::GeomCircle":
        replacement = FakeArc(copy.deepcopy(source), 0.0, 2.0 * math.pi)
        replacement.Tag = f"{prefix}-geometry-0"
        geometry = [replacement, retained]
        metadata = [_metadata(102, construction=construction), _metadata(101)]
        constraints = [FakeConstraint("Vertical", 1)]
        created_indices = (0,)
    else:
        first = FakeLine(_point(0.0, 0.0), _point(8.0, 0.0))
        second = FakeLine(_point(8.0, 0.0), _point(20.0, 0.0))
        first.Tag = f"{prefix}-geometry-0"
        second.Tag = f"{prefix}-geometry-2"
        geometry = [first, retained, second]
        metadata = [
            _metadata(102, construction=construction),
            _metadata(101),
            _metadata(103, construction=construction),
        ]
        constraints = [
            FakeConstraint("Vertical", 1),
            FakeConstraint("Coincident", 0, 2, 2, 1),
        ]
        created_indices = (0, 2)
    target_tag = str(sketch.GeometryFacadeList[0].Tag)
    receipt = {
        "geometry": {
            "identity": "native_tag",
            "old_to_new": {"1": 1},
            "deleted": [{"index": 0, "tag": target_tag}],
            "created": [
                {"index": index, "tag": f"{prefix}-geometry-{index}"}
                for index in created_indices
            ],
        },
        "constraints": {
            "identity": "native_tag",
            "old_to_new": {"0": 0},
            "deleted": [],
            "created": (
                []
                if len(created_indices) == 1
                else [{"index": 1, "tag": f"{prefix}-constraint-1"}]
            ),
        },
    }
    return geometry, metadata, constraints, receipt


def _diagnostic(sketch) -> dict[str, object]:
    geometry, metadata, constraints, receipt = _final_state(
        sketch,
        prefix="diagnostic",
    )
    return {
        "accepted": True,
        "degrees_of_freedom": 5,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "input_geometry_index": 0,
        "reference_point_mm": [8.0, 0.0],
        "external_geometry_count": 0,
        "mutation_receipt": receipt,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
    }


def _install_diagnostic(sketch):
    calls = []

    def diagnose(self, geometry_index, reference_point):
        calls.append((geometry_index, reference_point))
        return _diagnostic(self)

    sketch.diagnoseSplit = MethodType(diagnose, sketch)
    return calls


def _apply_fake_split(sketch):
    calls = []

    def split(self, geometry_index, reference_point):
        calls.append((geometry_index, reference_point))
        geometry, metadata, constraints, receipt = _final_state(self, prefix="actual")
        self.Geometry = geometry
        self.GeometryFacadeList = []
        for index, (item, details) in enumerate(zip(geometry, metadata, strict=True)):
            facade = fake_facade(
                item,
                index,
                construction=bool(details["Construction"]),
            )
            facade.Id = int(details["Id"])
            facade.Tag = str(item.Tag)
            self.GeometryFacadeList.append(facade)
        self.Constraints = constraints
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.ExpressionEngine = [("Constraints[0]", "5 mm")]
        return receipt

    sketch.split = MethodType(split, sketch)
    return calls


def _prepared(document, sketch, context):
    _install_diagnostic(sketch)
    return preflight_sketch_split(
        context,
        prepare_sketch_split(document.Uid, _values()),
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"geometry_index": 0}},
        {
            "target": {
                "geometry_index": True,
                "reference_point_mm": {"x": 8.0, "y": 0.0},
            }
        },
        {
            "target": {
                "geometry_index": 1_000_000,
                "reference_point_mm": {"x": 8.0, "y": 0.0},
            }
        },
        {
            "target": {
                "geometry_index": 0,
                "reference_point_mm": {"x": math.inf, "y": 0.0},
            }
        },
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_split_target_rejects_open_or_unbounded_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_split("document", values)


@pytest.mark.parametrize(
    ("closed", "outcome", "created_indices"),
    ((False, "split", (0, 2)), (True, "opened", (0,))),
)
def test_split_diagnostic_accepts_exact_open_and_closed_outcomes(
    monkeypatch,
    closed: bool,
    outcome: str,
    created_indices: tuple[int, ...],
) -> None:
    document, sketch, context = _host(monkeypatch, closed=closed)
    calls = _install_diagnostic(sketch)

    prepared = preflight_sketch_split(
        context,
        prepare_sketch_split(document.Uid, _values()),
    )

    assert len(calls) == 1
    assert prepared.plan.outcome == outcome
    assert prepared.plan.identity.geometry.deleted_indices == (0,)
    assert prepared.plan.identity.geometry.created_indices == created_indices


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"extra": True}), "incomplete"),
        (lambda value: value.update({"accepted": False}), "nothing changed"),
        (lambda value: value.update({"solver_status": 1}), "inconsistent"),
        (lambda value: value.update({"input_geometry_index": 1}), "different curve"),
        (
            lambda value: value.update({"reference_point_mm": [9.0, 0.0]}),
            "different reference point",
        ),
        (lambda value: value.update({"external_geometry_count": 1}), "external"),
        (lambda value: value.update({"geometry_count": 99}), "counts"),
        (lambda value: value.update({"mutation_receipt": {}}), "mutation receipt"),
        (
            lambda value: value["mutation_receipt"]["geometry"]["created"].pop(),
            "account for every",
        ),
        (
            lambda value: setattr(value["geometry"][0], "TypeId", "Part::GeomCircle"),
            "replacement curve",
        ),
        (
            lambda value: setattr(value["geometry"][0].EndPoint, "x", 7.0),
            "disconnected",
        ),
    ),
)
def test_split_diagnostic_rejects_untrusted_results(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, sketch, _context = _host(monkeypatch)
    spec = prepare_sketch_split(document.Uid, _values())
    before_geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    before_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch)
    )
    value = _diagnostic(sketch)
    mutate(value)

    with pytest.raises(NativeSketchError, match=message):
        parse_sketch_split_diagnostic(
            value,
            spec,
            before_geometry,
            before_constraints,
        )


def test_split_preflight_is_side_effect_free_and_rejects_ineligible_target(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    _install_diagnostic(sketch)
    prepared = preflight_sketch_split(
        context,
        prepare_sketch_split(document.Uid, _values()),
    )
    assert prepared.plan.geometry_records
    assert vars(sketch.Geometry[0].EndPoint) == vars(before[0].EndPoint)
    assert (sketch.GeometryCount, sketch.ConstraintCount) == (2, 1)

    document, sketch, context = _host(monkeypatch)
    sketch.Geometry[0] = FakePoint(_point(0.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="human Split"):
        preflight_sketch_split(
            context,
            prepare_sketch_split(document.Uid, _values()),
        )


@pytest.mark.parametrize(
    "change",
    (
        lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 19.0),
        lambda sketch: setattr(sketch.GeometryFacadeList[0], "Tag", "replaced"),
        lambda sketch: sketch.ExpressionEngine.append(("Other", "1 mm")),
        lambda sketch: sketch.RedundantConstraints.append(0),
    ),
)
def test_split_mutation_rejects_any_post_preflight_drift(monkeypatch, change) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_split(sketch)
    change(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_split(document, prepared)
    assert calls == []


@pytest.mark.parametrize(
    ("closed", "outcome", "counts", "replacement_indices"),
    (
        (False, "split", (3, 2), [0, 2]),
        (True, "opened", (2, 1), [0]),
    ),
)
def test_split_executes_and_verifies_exact_human_outcome(
    monkeypatch,
    closed: bool,
    outcome: str,
    counts: tuple[int, int],
    replacement_indices: list[int],
) -> None:
    document, sketch, context = _host(
        monkeypatch,
        closed=closed,
        construction=True,
    )
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_split(sketch)

    draft = create_sketch_split(document, prepared)
    result = verify_sketch_split(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 1
    assert result["operation"] == "split"
    assert result["outcome"] == outcome
    assert result["input_geometry_index"] == 0
    assert result["reference_point_mm"] == {"x": 8.0, "y": 0.0}
    assert result["deleted_geometry_indices"] == [0]
    assert result["replacement_geometry_indices"] == replacement_indices
    assert all(item["construction"] is True for item in result["replacement_geometry"])
    assert (result["geometry_count"], result["constraint_count"]) == counts
    assert sketch.ExpressionEngine == [("Constraints[0]", "5 mm")]


@pytest.mark.parametrize(
    ("corrupt", "message"),
    (
        (
            lambda sketch, draft: setattr(sketch.Geometry[0].EndPoint, "x", 7.0),
            "geometry state",
        ),
        (
            lambda sketch, draft: setattr(
                sketch.GeometryFacadeList[1],
                "Tag",
                "wrong-retained-tag",
            ),
            "retained geometry identity",
        ),
        (
            lambda sketch, draft: sketch.ExpressionEngine.append(("Other", "1 mm")),
            "expressions",
        ),
        (
            lambda sketch, draft: draft.value.update({"receipt": {}}),
            "mutation receipt",
        ),
    ),
)
def test_split_verifier_rejects_state_identity_expression_or_receipt_drift(
    monkeypatch,
    corrupt,
    message: str,
) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    _apply_fake_split(sketch)
    draft = create_sketch_split(document, prepared)
    corrupt(sketch, draft)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_split(document, draft)


def test_geometry_runtime_routes_split_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)
    diagnose_calls = _install_diagnostic(sketch)
    split_calls = _apply_fake_split(sketch)
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "split", **_values()},
        ticket=None,
    )

    assert len(diagnose_calls) == 1
    assert len(split_calls) == 1
    assert captured["transaction_name"] == "Split Native Sketch Geometry"
    assert result["operation"] == "split"
    assert result["outcome"] == "split"
