# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchIntersection import (
    create_sketch_intersection,
    preflight_sketch_intersection,
    prepare_sketch_intersection,
    verify_sketch_intersection,
)
from vibecad_tests.native_sketch_test_support import (
    FakeExternalLine,
    FakeLine,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y), z=0.0)


class _Shape:
    def __init__(self) -> None:
        self.token = "intersection-source-v1"

    def getElement(self, name: str):
        if name != "Edge1":
            raise KeyError(name)
        return SimpleNamespace(ShapeType="Edge")

    def exportBrepToString(self) -> str:
        return self.token


class _Source:
    def __init__(self, document) -> None:
        self.Document = document
        self.Name = "Source"
        self.Label = "Intersection source"
        self.TypeId = "Part::Feature"
        self.Shape = _Shape()
        self.NativeStateToken = "intersection-state-v1"
        self.InList = []
        document.Objects.append(self)

    def isDerivedFrom(self, type_id: str) -> bool:
        return self.TypeId == type_id


def _values(
    *,
    role: str = "defining",
    reference_count: int = 0,
    external_count: int = 0,
) -> dict[str, object]:
    return {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_reference_count": reference_count,
        "expected_external_geometry_count": external_count,
        "source": {"object_name": "Source", "subelement": "Edge1"},
        "role": role,
    }


def _metadata(reference: str, defining: bool) -> dict[str, object]:
    return {
        "reference": reference,
        "defining": defining,
        "frozen": False,
        "detached": False,
        "missing": False,
        "synchronized": False,
    }


def _intersection_line(offset: float = 0.0) -> FakeLine:
    return FakeLine(_point(offset, 3.0), _point(offset + 4.0, 3.0))


def _diagnostic(
    source: _Source,
    *,
    defining: bool,
    added: bool = True,
    count: int = 1,
) -> dict[str, object]:
    reference = f"{source.Name}.Edge1"
    geometry = [_intersection_line(float(index)) for index in range(count)]
    return {
        "source_object_name": source.Name,
        "source_subelement": "Edge1",
        "requested_defining": defining,
        "requested_intersection": True,
        "reference": reference,
        "type": 1 if added else 2,
        "reference_index": 0,
        "added_reference": added,
        "defining": defining,
        "external_geometry_count": count,
        "external_geometry": geometry,
        "external_geometry_metadata": [
            _metadata(reference, defining) for _item in geometry
        ],
    }


def _set_existing_projection(sketch, source: _Source, *, defining: bool) -> None:
    reference = f"{source.Name}.Edge1"
    sketch.ExternalGeometry = [(source, ("Edge1",))]
    sketch.ExternalTypes = [0]
    sketch.ExternalGeo = [
        FakeLine(),
        FakeLine(),
        FakeExternalLine(reference, defining=defining),
    ]


def _install_intersection_host(
    sketch,
    source: _Source,
    *,
    defining: bool,
    added: bool,
    count: int = 1,
):
    diagnose_calls = []
    add_calls = []

    def diagnose(self, object_name, subelement, defining_value, intersection):
        diagnose_calls.append((object_name, subelement, defining_value, intersection))
        return _diagnostic(
            source,
            defining=defining,
            added=added,
            count=count,
        )

    def add(self, object_name, subelement, defining_value, intersection):
        add_calls.append((object_name, subelement, defining_value, intersection))
        reference = f"{source.Name}.Edge1"
        if added:
            self.ExternalGeometry.append((source, (subelement,)))
            self.ExternalTypes.append(1)
        else:
            self.ExternalTypes[0] = 2
        self.ExternalGeo = [FakeLine(), FakeLine()]
        self.ExternalGeo.extend(
            FakeExternalLine(reference, defining=defining_value)
            for _index in range(count)
        )
        for index, geometry in enumerate(self.ExternalGeo[2:]):
            geometry.StartPoint = _point(float(index), 3.0)
            geometry.EndPoint = _point(float(index) + 4.0, 3.0)
        return 0

    sketch.diagnoseExternal = MethodType(diagnose, sketch)
    sketch.addExternal = MethodType(add, sketch)
    return diagnose_calls, add_calls


def _host(
    monkeypatch,
    *,
    defining: bool = True,
    projected: bool = False,
    count: int = 1,
):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    source = _Source(document)
    if projected:
        _set_existing_projection(sketch, source, defining=defining)
    calls, add_calls = _install_intersection_host(
        sketch,
        source,
        defining=defining,
        added=not projected,
        count=count,
    )
    values = _values(
        role="defining" if defining else "reference",
        reference_count=1 if projected else 0,
        external_count=1 if projected else 0,
    )
    return document, sketch, source, context, values, calls, add_calls


def test_intersection_preflight_is_pure_for_both_explicit_roles(monkeypatch) -> None:
    for defining in (True, False):
        document, sketch, source, context, values, calls, _add_calls = _host(
            monkeypatch,
            defining=defining,
        )
        before = (
            copy.deepcopy(sketch.ExternalGeometry),
            copy.deepcopy(sketch.ExternalTypes),
            source.Shape.token,
        )
        prepared = preflight_sketch_intersection(
            context,
            prepare_sketch_intersection(document.Uid, values),
        )

        assert calls == [(source.Name, "Edge1", defining, True)]
        assert prepared.plan.added_reference is True
        assert prepared.plan.defining is defining
        assert prepared.plan.final_kind == "intersection"
        assert prepared.plan.outcome == "added_intersection"
        assert sketch.ExternalGeometry == before[0]
        assert sketch.ExternalTypes == before[1]
        assert source.Shape.token == before[2]


def test_intersection_upgrades_only_an_exact_role_preserving_projection(
    monkeypatch,
) -> None:
    document, sketch, _source, context, values, _calls, _add_calls = _host(
        monkeypatch,
        defining=False,
        projected=True,
    )
    prepared = preflight_sketch_intersection(
        context,
        prepare_sketch_intersection(document.Uid, values),
    )
    assert prepared.plan.outcome == "upgraded_projection"
    assert prepared.plan.final_kind == "projection_and_intersection"
    assert prepared.plan.added_reference is False

    with pytest.raises(NativeSketchError, match="different target"):
        preflight_sketch_intersection(
            context,
            prepare_sketch_intersection(document.Uid, {**values, "role": "defining"}),
        )
    sketch.ExternalTypes = [1]
    with pytest.raises(NativeSketchError, match="already intersected"):
        preflight_sketch_intersection(
            context,
            prepare_sketch_intersection(document.Uid, values),
        )


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda sketch, source: setattr(sketch, "GeometryCount", 2), "count"),
        (lambda sketch, source: setattr(source.Shape, "token", "changed"), "changed"),
        (
            lambda sketch, source: sketch.ExpressionEngine.append(
                ("Constraints[0]", "1 mm")
            ),
            "changed",
        ),
    ),
)
def test_intersection_refuses_stale_exact_state(
    monkeypatch,
    change,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, add_calls = _host(monkeypatch)
    spec = prepare_sketch_intersection(document.Uid, values)
    if message == "count":
        change(sketch, source)
        with pytest.raises(NativeSketchError, match=message):
            preflight_sketch_intersection(context, spec)
    else:
        prepared = preflight_sketch_intersection(context, spec)
        change(sketch, source)
        with pytest.raises(NativeSketchError, match=message):
            create_sketch_intersection(document, prepared)
        assert add_calls == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"extra": True}), "incomplete diagnostics"),
        (
            lambda value: value.update({"requested_intersection": False}),
            "different target",
        ),
        (lambda value: value.update({"type": 0}), "link outcome"),
        (lambda value: value.update({"reference_index": 2}), "link outcome"),
        (lambda value: value.update({"external_geometry": []}), "geometry count"),
        (
            lambda value: value["external_geometry_metadata"][0].update(
                {"missing": True}
            ),
            "external metadata",
        ),
    ),
)
def test_intersection_rejects_untrusted_host_diagnostic(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)
    diagnostic = _diagnostic(source, defining=True)
    mutate(diagnostic)
    sketch.diagnoseExternal = MethodType(lambda self, *_args: diagnostic, sketch)
    with pytest.raises(NativeSketchError, match=message):
        preflight_sketch_intersection(
            context,
            prepare_sketch_intersection(document.Uid, values),
        )


def test_intersection_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)

    def impure(self, *_args):
        result = _diagnostic(source, defining=True)
        self.Geometry[0].EndPoint.x = 7.0
        return result

    sketch.diagnoseExternal = MethodType(impure, sketch)
    with pytest.raises(NativeSketchError, match="changed after preflight"):
        preflight_sketch_intersection(
            context,
            prepare_sketch_intersection(document.Uid, values),
        )

    document, sketch, source, context, values, _calls, add_calls = _host(monkeypatch)
    count = 0

    def drifting(self, *_args):
        nonlocal count
        count += 1
        return _diagnostic(source, defining=True, count=count)

    sketch.diagnoseExternal = MethodType(drifting, sketch)
    prepared = preflight_sketch_intersection(
        context,
        prepare_sketch_intersection(document.Uid, values),
    )
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_sketch_intersection(document, prepared)
    assert add_calls == []


@pytest.mark.parametrize(
    ("defining", "projected", "outcome", "kind"),
    (
        (True, False, "added_intersection", "intersection"),
        (False, False, "added_intersection", "intersection"),
        (False, True, "upgraded_projection", "projection_and_intersection"),
    ),
)
def test_intersection_executes_and_verifies_exact_human_outcomes(
    monkeypatch,
    defining: bool,
    projected: bool,
    outcome: str,
    kind: str,
) -> None:
    document, _sketch, source, context, values, calls, add_calls = _host(
        monkeypatch,
        defining=defining,
        projected=projected,
    )
    prepared = preflight_sketch_intersection(
        context,
        prepare_sketch_intersection(document.Uid, values),
    )
    draft = create_sketch_intersection(document, prepared)
    result = verify_sketch_intersection(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert add_calls == [(source.Name, "Edge1", defining, True)]
    assert result["operation"] == "intersect_external_geometry"
    assert result["role"] == ("defining" if defining else "reference")
    assert result["outcome"] == outcome
    assert result["reference_kind"] == kind
    assert result["affected_geometry_indices"] == [-3]
    assert result["external_reference_count"] == 1
    assert result["external_geometry_count"] == 1


@pytest.mark.parametrize(
    ("corrupt", "message"),
    (
        (
            lambda sketch, source: setattr(sketch.Geometry[0].EndPoint, "x", 8.0),
            "internal geometry",
        ),
        (
            lambda sketch, source: sketch.ExternalGeometry.append((source, ("Edge1",))),
            "durable external link",
        ),
        (
            lambda sketch, source: setattr(sketch.ExternalGeo[2].EndPoint, "x", 8.0),
            "projected geometry",
        ),
        (
            lambda sketch, source: setattr(source.Shape, "token", "changed"),
            "source object",
        ),
    ),
)
def test_intersection_verifier_rejects_postcondition_drift(
    monkeypatch,
    corrupt,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)
    prepared = preflight_sketch_intersection(
        context,
        prepare_sketch_intersection(document.Uid, values),
    )
    draft = create_sketch_intersection(document, prepared)
    corrupt(sketch, source)
    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_intersection(document, draft)


def test_geometry_runtime_routes_intersection_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, _source, context, values, calls, add_calls = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "intersect_external_geometry", **values},
        ticket=None,
    )

    assert len(calls) == 2
    assert len(add_calls) == 1
    assert captured["transaction_name"] == "Intersect Native Sketch External Geometry"
    assert result["operation"] == "intersect_external_geometry"
