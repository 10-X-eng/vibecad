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
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTrim import (
    create_sketch_trim,
    preflight_sketch_trim,
    prepare_sketch_trim,
    verify_sketch_trim,
)
from VibeCADNativeSketchTrimDiagnostic import parse_sketch_trim_diagnostic
from vibecad_tests.native_sketch_test_support import (
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
            "expected_geometry_count": 3,
            "expected_constraint_count": 2,
            "expected_external_geometry_count": 0,
            "target": {
                "geometry_index": 0,
                "reference_point_mm": {"x": 10.0, "y": 0.0},
            },
            **updates,
        }
    )


def _host(monkeypatch, *, construction: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    target = FakeLine(_point(0.0, 0.0), _point(20.0, 0.0))
    sketch.Geometry[0] = target
    sketch.GeometryFacadeList[0].Geometry = target
    sketch.GeometryFacadeList[0].Construction = construction
    sketch.enablePersistentGeometryTags()
    assert sketch.addGeometry(FakeLine(_point(5.0, -5.0), _point(5.0, 5.0)), False) == 1
    assert (
        sketch.addGeometry(FakeLine(_point(15.0, -5.0), _point(15.0, 5.0)), False) == 2
    )
    assert sketch.addConstraint(FakeConstraint("Horizontal", 0)) == 0
    assert sketch.addConstraint(FakeConstraint("Vertical", 1)) == 1
    sketch.ExpressionEngine = [("Constraints[1]", "10 mm")]
    return document, sketch, context


def _tagged_clone(geometry, tag: str):
    result = copy.deepcopy(geometry)
    result.Tag = tag
    return result


def _metadata(
    geometry_id: int,
    *,
    construction: bool = False,
    internal_type: str = "None",
) -> dict[str, object]:
    return {
        "Id": geometry_id,
        "Construction": construction,
        "Blocked": False,
        "InternalType": internal_type,
        "GeometryLayerId": 0,
    }


def _receipt(
    *,
    outcome: str,
    prefix: str,
    target_tag: str,
) -> dict[str, object]:
    replacement_indices = {
        "deleted": (),
        "shortened": (0,),
        "split": (0, 3),
    }[outcome]
    geometry_mapping = {"1": 0, "2": 1} if outcome == "deleted" else {"1": 1, "2": 2}
    return {
        "geometry": {
            "identity": "native_tag",
            "old_to_new": geometry_mapping,
            "deleted": [{"index": 0, "tag": target_tag}],
            "created": [
                {"index": index, "tag": f"{prefix}-geometry-{index}"}
                for index in replacement_indices
            ],
        },
        "constraints": {
            "identity": "native_tag",
            "old_to_new": {"1": 0},
            "deleted": [{"index": 0, "tag": "old-constraint-0"}],
            "created": [
                {"index": index + 1, "tag": f"{prefix}-constraint-{index + 1}"}
                for index in range(len(replacement_indices))
            ],
        },
    }


def _final_state(sketch, *, outcome: str, prefix: str):
    if outcome not in {"deleted", "shortened", "split"}:
        raise ValueError("Unsupported fake Trim outcome")
    construction = bool(sketch.GeometryFacadeList[0].Construction)
    retained_first = _tagged_clone(sketch.Geometry[1], sketch.GeometryFacadeList[1].Tag)
    retained_second = _tagged_clone(
        sketch.Geometry[2], sketch.GeometryFacadeList[2].Tag
    )
    replacements = []
    if outcome != "deleted":
        first = FakeLine(_point(0.0, 0.0), _point(5.0, 0.0))
        first.Tag = f"{prefix}-geometry-0"
        replacements.append(first)
    if outcome == "split":
        second = FakeLine(_point(15.0, 0.0), _point(20.0, 0.0))
        second.Tag = f"{prefix}-geometry-3"
        replacements.append(second)

    if outcome == "deleted":
        geometry = [retained_first, retained_second]
        metadata = [_metadata(101), _metadata(102)]
        retained_geometry_index = 0
    else:
        geometry = [replacements[0], retained_first, retained_second]
        metadata = [
            _metadata(103, construction=construction),
            _metadata(101),
            _metadata(102),
        ]
        retained_geometry_index = 1
        if outcome == "split":
            geometry.append(replacements[1])
            metadata.append(_metadata(104, construction=construction))

    retained_constraint = FakeConstraint("Vertical", retained_geometry_index)
    constraints = [retained_constraint]
    if outcome != "deleted":
        constraints.append(FakeConstraint("Horizontal", 0))
    if outcome == "split":
        constraints.append(FakeConstraint("Horizontal", 3))
    return (
        geometry,
        metadata,
        constraints,
        _receipt(
            outcome=outcome,
            prefix=prefix,
            target_tag=str(sketch.GeometryFacadeList[0].Tag),
        ),
    )


def _diagnostic(sketch, *, outcome: str = "split") -> dict[str, object]:
    geometry, metadata, constraints, receipt = _final_state(
        sketch,
        outcome=outcome,
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
        "reference_point_mm": [10.0, 0.0],
        "external_geometry_count": 0,
        "mutation_receipt": receipt,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
    }


def _install_diagnostic(sketch, *, outcome: str = "split"):
    calls = []

    def diagnose(self, geometry_index, reference_point):
        calls.append((geometry_index, reference_point))
        return _diagnostic(self, outcome=outcome)

    sketch.diagnoseTrim = MethodType(diagnose, sketch)
    return calls


def _apply_fake_trim(sketch, *, outcome: str = "split"):
    calls = []

    def trim(self, geometry_index, reference_point):
        calls.append((geometry_index, reference_point))
        geometry, metadata, constraints, receipt = _final_state(
            self,
            outcome=outcome,
            prefix="actual",
        )
        self.Geometry = geometry
        self.GeometryFacadeList = []
        for index, (item, details) in enumerate(zip(geometry, metadata, strict=True)):
            facade = fake_facade(
                item,
                index,
                construction=bool(details["Construction"]),
                internal_type=(
                    ""
                    if details["InternalType"] == "None"
                    else str(details["InternalType"])
                ),
            )
            facade.Id = int(details["Id"])
            facade.Blocked = bool(details["Blocked"])
            facade.GeometryLayerId = int(details["GeometryLayerId"])
            facade.Tag = str(item.Tag)
            self.GeometryFacadeList.append(facade)
        self.Constraints = constraints
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.ExpressionEngine = [("Constraints[0]", "10 mm")]
        return receipt

    sketch.trim = MethodType(trim, sketch)
    return calls


def _prepared(document, sketch, context, *, outcome: str = "split"):
    _install_diagnostic(sketch, outcome=outcome)
    return preflight_sketch_trim(
        context,
        prepare_sketch_trim(document.Uid, _values()),
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"geometry_index": 0}},
        {
            "target": {
                "geometry_index": True,
                "reference_point_mm": {"x": 10.0, "y": 0.0},
            }
        },
        {
            "target": {
                "geometry_index": 1_000_000,
                "reference_point_mm": {"x": 10.0, "y": 0.0},
            }
        },
        {
            "target": {
                "geometry_index": 0,
                "reference_point_mm": {"x": 10.0, "y": 0.0, "z": 0.0},
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
def test_trim_target_rejects_open_or_unbounded_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_trim("document", values)


@pytest.mark.parametrize(
    ("outcome", "created_indices"),
    (("deleted", ()), ("shortened", (0,)), ("split", (0, 3))),
)
def test_trim_diagnostic_accepts_exact_human_outcomes(
    monkeypatch,
    outcome: str,
    created_indices: tuple[int, ...],
) -> None:
    document, sketch, context = _host(monkeypatch)
    calls = _install_diagnostic(sketch, outcome=outcome)

    prepared = preflight_sketch_trim(
        context,
        prepare_sketch_trim(document.Uid, _values()),
    )

    assert len(calls) == 1
    assert calls[0][0] == 0
    assert (calls[0][1].x, calls[0][1].y, calls[0][1].z) == (10.0, 0.0, 0.0)
    assert prepared.plan.outcome == outcome
    assert prepared.plan.identity.geometry.deleted_indices == (0,)
    assert prepared.plan.identity.geometry.created_indices == created_indices
    assert prepared.plan.identity.constraints.old_to_new == ((1, 0),)


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
        (
            lambda value: value.update({"external_geometry_count": 1}),
            "external geometry",
        ),
        (lambda value: value.update({"geometry_count": 99}), "counts"),
        (lambda value: value.update({"mutation_receipt": {}}), "mutation receipt"),
        (
            lambda value: value["mutation_receipt"]["geometry"]["deleted"].append(
                {"index": 1, "tag": "fake-geometry-1"}
            ),
            "account for every prior",
        ),
        (
            lambda value: setattr(value["geometry"][0], "TypeId", "Part::GeomCircle"),
            "replacement curve",
        ),
    ),
)
def test_trim_diagnostic_rejects_untrusted_results(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, sketch, _context = _host(monkeypatch)
    spec = prepare_sketch_trim(document.Uid, _values())
    before_geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    before_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch)
    )
    value = _diagnostic(sketch)
    mutate(value)

    with pytest.raises(NativeSketchError, match=message):
        parse_sketch_trim_diagnostic(
            value,
            spec,
            before_geometry,
            before_constraints,
        )


def test_trim_preflight_is_side_effect_free(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    calls = _install_diagnostic(sketch)

    prepared = preflight_sketch_trim(
        context,
        prepare_sketch_trim(document.Uid, _values()),
    )

    assert len(calls) == 1
    assert prepared.plan.geometry_records
    assert vars(sketch.Geometry[0].StartPoint) == vars(before[0].StartPoint)
    assert vars(sketch.Geometry[0].EndPoint) == vars(before[0].EndPoint)
    assert (sketch.GeometryCount, sketch.ConstraintCount) == (3, 2)
    assert sketch.ExpressionEngine == [("Constraints[1]", "10 mm")]


def test_trim_rejects_diagnostic_side_effect_and_ineligible_targets(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)

    def mutating_diagnosis(self, *_arguments):
        result = _diagnostic(self)
        self.Geometry[0].EndPoint.x = 99.0
        return result

    sketch.diagnoseTrim = MethodType(mutating_diagnosis, sketch)
    with pytest.raises(NativeSketchError, match="feasibility changed"):
        preflight_sketch_trim(
            context,
            prepare_sketch_trim(document.Uid, _values()),
        )

    document, sketch, context = _host(monkeypatch)
    sketch.Geometry[0] = FakePoint(_point(0.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="human Trim"):
        preflight_sketch_trim(
            context,
            prepare_sketch_trim(document.Uid, _values()),
        )

    document, sketch, context = _host(monkeypatch)
    sketch.GeometryFacadeList[0].InternalType = "BSplineControlPoint"
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="human Trim"):
        preflight_sketch_trim(
            context,
            prepare_sketch_trim(document.Uid, _values()),
        )

    document, sketch, context = _host(monkeypatch)
    sketch.Constraints.append(
        SimpleNamespace(Type="Group", Elements=((9, 0), (0, 0), (1, 0)))
    )
    sketch.ConstraintCount = 3
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="grouped member"):
        preflight_sketch_trim(
            context,
            prepare_sketch_trim(
                document.Uid,
                _values(expected_constraint_count=3),
            ),
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
def test_trim_mutation_rejects_any_post_preflight_drift(monkeypatch, change) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_trim(sketch)
    change(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_trim(document, prepared)
    assert calls == []


@pytest.mark.parametrize(
    ("outcome", "counts", "replacement_indices"),
    (
        ("deleted", (2, 1), []),
        ("shortened", (3, 2), [0]),
        ("split", (4, 3), [0, 3]),
    ),
)
def test_trim_executes_and_verifies_exact_human_outcome(
    monkeypatch,
    outcome: str,
    counts: tuple[int, int],
    replacement_indices: list[int],
) -> None:
    document, sketch, context = _host(monkeypatch, construction=True)
    prepared = _prepared(document, sketch, context, outcome=outcome)
    calls = _apply_fake_trim(sketch, outcome=outcome)

    draft = create_sketch_trim(document, prepared)
    result = verify_sketch_trim(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 1
    assert calls[0][0] == 0
    assert (calls[0][1].x, calls[0][1].y, calls[0][1].z) == (10.0, 0.0, 0.0)
    assert result["operation"] == "trim"
    assert result["input_geometry_index"] == 0
    assert result["reference_point_mm"] == {"x": 10.0, "y": 0.0}
    assert result["outcome"] == outcome
    assert result["deleted_geometry_indices"] == [0]
    assert result["replacement_geometry_indices"] == replacement_indices
    assert len(result["replacement_geometry"]) == len(replacement_indices)
    assert all(item["construction"] is True for item in result["replacement_geometry"])
    assert (result["geometry_count"], result["constraint_count"]) == counts
    assert sketch.ExpressionEngine == [("Constraints[0]", "10 mm")]


@pytest.mark.parametrize(
    ("corrupt", "message"),
    (
        (
            lambda sketch, draft: setattr(sketch.Geometry[0].EndPoint, "x", 4.0),
            "geometry state",
        ),
        (
            lambda sketch, draft: setattr(
                sketch.GeometryFacadeList[1], "Tag", "wrong-retained-tag"
            ),
            "retained geometry identity",
        ),
        (
            lambda sketch, draft: setattr(
                sketch.GeometryFacadeList[0], "Tag", "fake-geometry-0"
            ),
            "created geometry",
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
def test_trim_verifier_rejects_geometry_identity_expression_or_receipt_drift(
    monkeypatch,
    corrupt,
    message: str,
) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    _apply_fake_trim(sketch)
    draft = create_sketch_trim(document, prepared)
    corrupt(sketch, draft)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_trim(document, draft)


def test_geometry_runtime_routes_trim_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)
    diagnose_calls = _install_diagnostic(sketch, outcome="shortened")
    trim_calls = _apply_fake_trim(sketch, outcome="shortened")
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "trim", **_values()},
        ticket=None,
    )

    assert len(diagnose_calls) == 1
    assert len(trim_calls) == 1
    assert captured["transaction_name"] == "Trim Native Sketch Geometry"
    assert result["operation"] == "trim"
    assert result["outcome"] == "shortened"
