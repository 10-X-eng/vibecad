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
from VibeCADNativeSketchExtend import (
    create_sketch_extend,
    preflight_sketch_extend,
    prepare_sketch_extend,
    verify_sketch_extend,
)
from VibeCADNativeSketchExtendDiagnostic import parse_sketch_extend_diagnostic
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
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


def _values(
    *,
    endpoint: str = "start",
    target: tuple[float, float] = (-5.0, 3.0),
    **updates,
) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_constraint_count": 1,
            "expected_external_geometry_count": 0,
            "target": {
                "geometry_index": 0,
                "endpoint": endpoint,
                "target_point_mm": {"x": target[0], "y": target[1]},
            },
            **updates,
        }
    )


def _host(monkeypatch, *, arc: bool = False, construction: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    target = (
        FakeArc(
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 10.0),
            0.0,
            1.0,
        )
        if arc
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


def _metadata(facade) -> dict[str, object]:
    return {
        "Id": int(facade.Id),
        "Construction": bool(facade.Construction),
        "Blocked": bool(facade.Blocked),
        "InternalType": str(facade.InternalType or "None"),
        "GeometryLayerId": int(facade.GeometryLayerId),
    }


def _identity_receipt(sketch) -> dict[str, object]:
    return {
        "geometry": {
            "identity": "native_tag",
            "old_to_new": {str(index): index for index in range(sketch.GeometryCount)},
            "deleted": [],
            "created": [],
        },
        "constraints": {
            "identity": "native_tag",
            "old_to_new": {
                str(index): index for index in range(sketch.ConstraintCount)
            },
            "deleted": [],
            "created": [],
        },
    }


def _projected_line_point(source, target: tuple[float, float]):
    start = source.StartPoint
    end = source.EndPoint
    dx = float(end.x) - float(start.x)
    dy = float(end.y) - float(start.y)
    scale = ((target[0] - float(start.x)) * dx + (target[1] - float(start.y)) * dy) / (
        dx * dx + dy * dy
    )
    return _point(float(start.x) + scale * dx, float(start.y) + scale * dy)


def _projected_arc_parameter(source, target: tuple[float, float]) -> float:
    return math.atan2(
        target[1] - float(source.Center.y),
        target[0] - float(source.Center.x),
    )


def _span(geometry) -> float:
    if geometry.TypeId == "Part::GeomLineSegment":
        return math.hypot(
            float(geometry.EndPoint.x) - float(geometry.StartPoint.x),
            float(geometry.EndPoint.y) - float(geometry.StartPoint.y),
        )
    return float(geometry.LastParameter) - float(geometry.FirstParameter)


def _final_state(
    sketch,
    *,
    endpoint: str,
    target: tuple[float, float],
):
    source = sketch.Geometry[0]
    target_tag = str(sketch.GeometryFacadeList[0].Tag)
    before_span = _span(source)
    if source.TypeId == "Part::GeomLineSegment":
        replacement = _tagged_clone(source, target_tag)
        moved = _projected_line_point(source, target)
        if endpoint == "start":
            replacement.StartPoint = moved
        else:
            replacement.EndPoint = moved
    else:
        parameter = _projected_arc_parameter(source, target)
        circle = FakeCircle(
            copy.deepcopy(source.Center),
            copy.deepcopy(source.Axis),
            float(source.Radius),
        )
        first = parameter if endpoint == "start" else float(source.FirstParameter)
        last = parameter if endpoint == "end" else float(source.LastParameter)
        replacement = FakeArc(circle, first, last)
        replacement.Tag = target_tag

    retained = _tagged_clone(sketch.Geometry[1], sketch.GeometryFacadeList[1].Tag)
    geometry = [replacement, retained]
    metadata = [_metadata(item) for item in sketch.GeometryFacadeList]
    constraints = copy.deepcopy(sketch.Constraints)
    increment = _span(replacement) - before_span
    return geometry, metadata, constraints, _identity_receipt(sketch), increment


def _diagnostic(
    sketch,
    *,
    endpoint: str,
    target: tuple[float, float],
) -> dict[str, object]:
    geometry, metadata, constraints, receipt, increment = _final_state(
        sketch,
        endpoint=endpoint,
        target=target,
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
        "reference_point_mm": [target[0], target[1]],
        "external_geometry_count": 0,
        "mutation_receipt": receipt,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
        "input_endpoint": endpoint,
        "extension_increment": increment,
    }


def _install_diagnostic(
    sketch,
    *,
    endpoint: str,
    target: tuple[float, float],
):
    calls = []

    def diagnose(self, geometry_index, target_point, endpoint_value):
        calls.append((geometry_index, target_point, endpoint_value))
        return _diagnostic(self, endpoint=endpoint, target=target)

    sketch.diagnoseExtend = MethodType(diagnose, sketch)
    return calls


def _apply_fake_extend(
    sketch,
    *,
    endpoint: str,
    target: tuple[float, float],
):
    calls = []

    def extend(self, geometry_index, increment, endpoint_value):
        calls.append((geometry_index, increment, endpoint_value))
        geometry, metadata, constraints, receipt, expected_increment = _final_state(
            self,
            endpoint=endpoint,
            target=target,
        )
        assert math.isclose(
            float(increment),
            expected_increment,
            rel_tol=0.0,
            abs_tol=1.0e-10,
        )
        self.Geometry = geometry
        self.GeometryFacadeList = []
        for index, (item, details) in enumerate(zip(geometry, metadata, strict=True)):
            facade = fake_facade(
                item,
                index,
                construction=bool(details["Construction"]),
            )
            facade.Id = int(details["Id"])
            facade.Blocked = bool(details["Blocked"])
            facade.InternalType = str(details["InternalType"])
            facade.GeometryLayerId = int(details["GeometryLayerId"])
            facade.Tag = str(item.Tag)
            self.GeometryFacadeList.append(facade)
        self.Constraints = constraints
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.ExpressionEngine = [("Constraints[0]", "5 mm")]
        return receipt

    sketch.extend = MethodType(extend, sketch)
    return calls


def _prepared(
    document,
    sketch,
    context,
    *,
    endpoint: str,
    target: tuple[float, float],
):
    _install_diagnostic(sketch, endpoint=endpoint, target=target)
    return preflight_sketch_extend(
        context,
        prepare_sketch_extend(
            document.Uid,
            _values(endpoint=endpoint, target=target),
        ),
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"geometry_index": 0}},
        {
            "target": {
                "geometry_index": True,
                "endpoint": "start",
                "target_point_mm": {"x": -5.0, "y": 3.0},
            }
        },
        {
            "target": {
                "geometry_index": 0,
                "endpoint": "middle",
                "target_point_mm": {"x": -5.0, "y": 3.0},
            }
        },
        {
            "target": {
                "geometry_index": 1_000_000,
                "endpoint": "start",
                "target_point_mm": {"x": -5.0, "y": 3.0},
            }
        },
        {
            "target": {
                "geometry_index": 0,
                "endpoint": "start",
                "target_point_mm": {"x": math.inf, "y": 3.0},
            }
        },
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_extend_target_rejects_open_or_unbounded_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_extend("document", values)


@pytest.mark.parametrize(
    ("arc", "endpoint", "target", "outcome", "increment"),
    (
        (False, "start", (-5.0, 3.0), "extended", 5.0),
        (False, "end", (15.0, -2.0), "shortened", -5.0),
        (
            True,
            "start",
            (10.0 * math.cos(-0.5), 10.0 * math.sin(-0.5)),
            "extended",
            0.5,
        ),
        (
            True,
            "end",
            (10.0 * math.cos(0.75), 10.0 * math.sin(0.75)),
            "shortened",
            -0.25,
        ),
    ),
)
def test_extend_diagnostic_accepts_exact_human_outcomes(
    monkeypatch,
    arc: bool,
    endpoint: str,
    target: tuple[float, float],
    outcome: str,
    increment: float,
) -> None:
    document, sketch, context = _host(monkeypatch, arc=arc)
    calls = _install_diagnostic(sketch, endpoint=endpoint, target=target)

    prepared = preflight_sketch_extend(
        context,
        prepare_sketch_extend(
            document.Uid,
            _values(endpoint=endpoint, target=target),
        ),
    )

    assert len(calls) == 1
    assert prepared.plan.endpoint == endpoint
    assert prepared.plan.outcome == outcome
    assert math.isclose(prepared.plan.extension_increment, increment, abs_tol=1.0e-10)
    assert prepared.plan.changed_geometry_indices == (0,)
    assert prepared.plan.identity.geometry.old_to_new == ((0, 0), (1, 1))
    assert prepared.plan.identity.constraints.old_to_new == ((0, 0),)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"extra": True}), "incomplete"),
        (lambda value: value.update({"accepted": False}), "nothing changed"),
        (lambda value: value.update({"solver_status": 1}), "inconsistent"),
        (lambda value: value.update({"input_geometry_index": 1}), "different curve"),
        (lambda value: value.update({"input_endpoint": "end"}), "different endpoint"),
        (
            lambda value: value.update({"extension_increment": True}),
            "invalid increment",
        ),
        (lambda value: value.update({"extension_increment": 0.0}), "invalid increment"),
        (
            lambda value: value.update({"extension_increment": math.inf}),
            "invalid increment",
        ),
        (
            lambda value: value.update({"reference_point_mm": [-4.0, 3.0]}),
            "different reference point",
        ),
        (lambda value: value.update({"external_geometry_count": 1}), "external"),
        (lambda value: value.update({"geometry_count": 99}), "counts"),
        (lambda value: value.update({"mutation_receipt": {}}), "mutation receipt"),
        (
            lambda value: setattr(value["constraints"][0], "Type", "Horizontal"),
            "changed constraints",
        ),
        (
            lambda value: setattr(value["geometry"][0], "TypeId", "Part::GeomCircle"),
            "curve kind",
        ),
        (
            lambda value: setattr(value["geometry"][0].EndPoint, "x", 19.0),
            "opposite endpoint",
        ),
        (
            lambda value: setattr(value["geometry"][0].StartPoint, "x", -4.0),
            "project the exact target",
        ),
        (
            lambda value: value.update({"extension_increment": 4.0}),
            "wrong curve extent",
        ),
        (
            lambda value: setattr(value["geometry"][1].EndPoint, "y", 6.0),
            "outside the target constraint component",
        ),
    ),
)
def test_extend_diagnostic_rejects_untrusted_results(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, sketch, _context = _host(monkeypatch)
    spec = prepare_sketch_extend(document.Uid, _values())
    before_geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    before_constraints = canonical_sketch_records(
        iter_sketch_constraint_records(sketch)
    )
    value = _diagnostic(sketch, endpoint="start", target=(-5.0, 3.0))
    mutate(value)

    with pytest.raises(NativeSketchError, match=message):
        parse_sketch_extend_diagnostic(
            value,
            spec,
            before_geometry,
            before_constraints,
        )


def test_extend_preflight_is_pure_and_rejects_geometry_outside_human_gate(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    _install_diagnostic(sketch, endpoint="start", target=(-5.0, 3.0))
    prepared = preflight_sketch_extend(
        context,
        prepare_sketch_extend(document.Uid, _values()),
    )
    assert prepared.plan.geometry_records
    assert vars(sketch.Geometry[0].StartPoint) == vars(before[0].StartPoint)
    assert (sketch.GeometryCount, sketch.ConstraintCount) == (2, 1)

    document, sketch, context = _host(monkeypatch)
    sketch.Geometry[0] = FakePoint(_point(0.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    _install_diagnostic(sketch, endpoint="start", target=(-5.0, 3.0))
    with pytest.raises(NativeSketchError, match="human Extend"):
        preflight_sketch_extend(
            context,
            prepare_sketch_extend(document.Uid, _values()),
        )


def test_extend_preflight_rejects_a_diagnostic_that_mutates_the_live_sketch(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)

    def diagnose(self, _geometry_index, _target_point, _endpoint_value):
        result = _diagnostic(self, endpoint="start", target=(-5.0, 3.0))
        self.Geometry[0].StartPoint.x = -1.0
        return result

    sketch.diagnoseExtend = MethodType(diagnose, sketch)
    with pytest.raises(NativeSketchError, match="changed the active Sketch"):
        preflight_sketch_extend(
            context,
            prepare_sketch_extend(document.Uid, _values()),
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
def test_extend_mutation_rejects_any_post_preflight_drift(monkeypatch, change) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(
        document,
        sketch,
        context,
        endpoint="start",
        target=(-5.0, 3.0),
    )
    calls = _apply_fake_extend(sketch, endpoint="start", target=(-5.0, 3.0))
    change(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_extend(document, prepared)
    assert calls == []


@pytest.mark.parametrize(
    ("arc", "endpoint", "target", "outcome"),
    (
        (False, "start", (-5.0, 3.0), "extended"),
        (False, "end", (15.0, -2.0), "shortened"),
        (True, "start", (10.0 * math.cos(-0.5), 10.0 * math.sin(-0.5)), "extended"),
        (True, "end", (10.0 * math.cos(0.75), 10.0 * math.sin(0.75)), "shortened"),
    ),
)
def test_extend_executes_and_verifies_exact_human_outcome(
    monkeypatch,
    arc: bool,
    endpoint: str,
    target: tuple[float, float],
    outcome: str,
) -> None:
    document, sketch, context = _host(
        monkeypatch,
        arc=arc,
        construction=True,
    )
    prepared = _prepared(
        document,
        sketch,
        context,
        endpoint=endpoint,
        target=target,
    )
    calls = _apply_fake_extend(sketch, endpoint=endpoint, target=target)

    draft = create_sketch_extend(document, prepared)
    result = verify_sketch_extend(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 1
    assert result["operation"] == "extend"
    assert result["outcome"] == outcome
    assert result["geometry_index"] == 0
    assert result["endpoint"] == endpoint
    assert result["target_point_mm"] == {"x": target[0], "y": target[1]}
    assert result["changed_geometry_indices"] == [0]
    assert (result["geometry_count"], result["constraint_count"]) == (2, 1)
    assert sketch.GeometryFacadeList[0].Construction is True
    assert sketch.ExpressionEngine == [("Constraints[0]", "5 mm")]


@pytest.mark.parametrize(
    ("corrupt", "message"),
    (
        (
            lambda sketch, draft: setattr(sketch.Geometry[0].StartPoint, "x", -4.0),
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
            lambda sketch, draft: setattr(sketch.Constraints[0], "Type", "Horizontal"),
            "constraint topology",
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
def test_extend_verifier_rejects_state_identity_expression_or_receipt_drift(
    monkeypatch,
    corrupt,
    message: str,
) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(
        document,
        sketch,
        context,
        endpoint="start",
        target=(-5.0, 3.0),
    )
    _apply_fake_extend(sketch, endpoint="start", target=(-5.0, 3.0))
    draft = create_sketch_extend(document, prepared)
    corrupt(sketch, draft)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_extend(document, draft)


def test_geometry_runtime_routes_extend_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = _host(monkeypatch)
    diagnose_calls = _install_diagnostic(
        sketch,
        endpoint="start",
        target=(-5.0, 3.0),
    )
    extend_calls = _apply_fake_extend(
        sketch,
        endpoint="start",
        target=(-5.0, 3.0),
    )
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "extend", **_values()},
        ticket=None,
    )

    assert len(diagnose_calls) == 1
    assert len(extend_calls) == 1
    assert captured["transaction_name"] == "Extend Native Sketch Geometry"
    assert result["operation"] == "extend"
    assert result["outcome"] == "extended"
