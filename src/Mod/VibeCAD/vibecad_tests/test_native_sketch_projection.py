# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchProjection import (
    create_sketch_projection,
    preflight_sketch_projection,
    prepare_sketch_projection,
    verify_sketch_projection,
)
from VibeCADNativeTargets import NativeTargetError
from vibecad_tests.native_sketch_test_support import (
    FakeExternalLine,
    FakeLine,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=float(x), y=float(y), z=0.0)


class _Shape:
    def __init__(self, shape_type: str = "Edge") -> None:
        self.shape_type = shape_type
        self.token = "source-shape-v1"

    def getElement(self, name: str):
        if name != f"{self.shape_type}1":
            raise KeyError(name)
        return SimpleNamespace(ShapeType=self.shape_type)

    def exportBrepToString(self) -> str:
        return self.token


class _Source:
    def __init__(
        self,
        document,
        *,
        name: str = "Source",
        type_id: str = "Part::Feature",
        shape_type: str = "Edge",
    ) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.Shape = _Shape(shape_type)
        self.NativeStateToken = "source-state-v1"
        self.InList = []
        document.Objects.append(self)

    def dumpContent(self) -> str:
        return f"{self.Name}:{self.TypeId}:{self.NativeStateToken}:{self.Shape.token}"

    def isDerivedFrom(self, type_id: str) -> bool:
        return self.TypeId == type_id


def _values(
    *,
    source_name: str = "Source",
    subelement: str | None = "Edge1",
    role: str = "defining",
    external_reference_count: int = 0,
    external_geometry_count: int = 0,
    **updates,
) -> dict[str, object]:
    source = {"object_name": source_name}
    if subelement is not None:
        source["subelement"] = subelement
    result: dict[str, object] = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_reference_count": external_reference_count,
        "expected_external_geometry_count": external_geometry_count,
        "source": source,
        "role": role,
    }
    result.update(updates)
    return result


def _metadata(reference: str, defining: bool) -> dict[str, object]:
    return {
        "reference": reference,
        "defining": defining,
        "frozen": False,
        "detached": False,
        "missing": False,
        "synchronized": False,
    }


def _projected_line(offset: float = 0.0) -> FakeLine:
    return FakeLine(_point(offset, 2.0), _point(offset + 4.0, 2.0))


def _diagnostic(
    source: _Source,
    *,
    defining: bool,
    added: bool = True,
    count: int = 1,
    reference_index: int = 0,
) -> dict[str, object]:
    reference = f"{source.Name}.Edge1"
    geometry = [_projected_line(float(index)) for index in range(count)]
    return {
        "source_object_name": source.Name,
        "source_subelement": "Edge1",
        "requested_defining": defining,
        "requested_intersection": False,
        "reference": reference,
        "type": 0 if added else 2,
        "reference_index": reference_index,
        "added_reference": added,
        "defining": defining,
        "external_geometry_count": count,
        "external_geometry": geometry,
        "external_geometry_metadata": [
            _metadata(reference, defining) for _item in geometry
        ],
    }


def _set_existing_intersection(sketch, source: _Source, *, defining: bool) -> None:
    reference = f"{source.Name}.Edge1"
    sketch.ExternalGeometry = [(source, ("Edge1",))]
    sketch.ExternalTypes = [1]
    sketch.ExternalGeo = [
        FakeLine(),
        FakeLine(),
        FakeExternalLine(reference, defining=defining),
    ]


def _install_projection_host(
    sketch,
    source: _Source,
    *,
    defining: bool,
    added: bool = True,
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
            self.ExternalTypes.append(0)
        else:
            self.ExternalTypes[0] = 2
        self.ExternalGeo = [FakeLine(), FakeLine()]
        self.ExternalGeo.extend(
            FakeExternalLine(reference, defining=defining_value)
            for _index in range(count)
        )
        for index, geometry in enumerate(self.ExternalGeo[2:]):
            geometry.StartPoint = _point(float(index), 2.0)
            geometry.EndPoint = _point(float(index) + 4.0, 2.0)
        return 0

    sketch.diagnoseExternal = MethodType(diagnose, sketch)
    sketch.addExternal = MethodType(add, sketch)
    return diagnose_calls, add_calls


def _host(
    monkeypatch,
    *,
    defining: bool = True,
    intersection: bool = False,
    count: int = 1,
):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    source = _Source(document)
    if intersection:
        _set_existing_intersection(sketch, source, defining=defining)
    diagnose_calls, add_calls = _install_projection_host(
        sketch,
        source,
        defining=defining,
        added=not intersection,
        count=count,
    )
    values = _values(
        role="defining" if defining else "reference",
        external_reference_count=1 if intersection else 0,
        external_geometry_count=1 if intersection else 0,
    )
    return document, sketch, source, context, values, diagnose_calls, add_calls


@pytest.mark.parametrize(
    "updates",
    (
        {"source": {"object_name": "Source", "subelement": "Wire1"}},
        {"source": {"object_name": "Source", "subelement": "Edge0"}},
        {"source": {"object_name": "Source", "extra": True}},
        {"role": "construction"},
        {"expected_external_reference_count": -1},
        {"expected_external_geometry_count": 1_000_001},
        {"expected_geometry_count": True},
        {"unexpected": True},
    ),
)
def test_projection_target_rejects_open_or_unbounded_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises((NativeSketchError, ValueError)):
        prepare_sketch_projection("document", values)


def test_projection_preflight_is_pure_and_preserves_explicit_role(monkeypatch) -> None:
    for defining in (True, False):
        document, sketch, source, context, values, calls, _add_calls = _host(
            monkeypatch,
            defining=defining,
        )
        before = (
            copy.deepcopy(sketch.ExternalGeometry),
            copy.deepcopy(sketch.ExternalTypes),
            source.dumpContent(),
        )
        prepared = preflight_sketch_projection(
            context,
            prepare_sketch_projection(document.Uid, values),
        )

        assert calls == [(source.Name, "Edge1", defining, False)]
        assert prepared.plan.added_reference is True
        assert prepared.plan.defining is defining
        assert prepared.plan.final_kind == "projection"
        assert prepared.plan.outcome == "added_projection"
        assert sketch.ExternalGeometry == before[0]
        assert sketch.ExternalTypes == before[1]
        assert source.dumpContent() == before[2]


def test_projection_ignores_only_unlinked_host_external_type_padding(
    monkeypatch,
) -> None:
    document, sketch, _source, context, values, _calls, _add_calls = _host(monkeypatch)
    sketch.ExternalTypes = [0]
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    assert prepared.plan.reference_index == 0
    assert prepared.plan.added_reference is True


def test_projection_allows_expected_source_backlink_bookkeeping(monkeypatch) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    source.InList.append(sketch)
    draft = create_sketch_projection(document, prepared)
    assert verify_sketch_projection(document, draft)["outcome"] == "added_projection"


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda document, sketch, source: setattr(sketch, "GeometryCount", 2), "count"),
        (
            lambda document, sketch, source: setattr(sketch, "ConstraintCount", 1),
            "count",
        ),
        (
            lambda document, sketch, source: sketch.ExternalGeometry.append(
                (source, ("Edge1",))
            ),
            "count",
        ),
        (
            lambda document, sketch, source: sketch.ExternalGeo.append(
                FakeExternalLine("Source.Edge1")
            ),
            "count",
        ),
        (
            lambda document, sketch, source: setattr(source.Shape, "token", "changed"),
            "changed after preflight",
        ),
        (
            lambda document, sketch, source: sketch.ExpressionEngine.append(
                ("Constraints[0]", "1 mm")
            ),
            "changed after preflight",
        ),
        (
            lambda document, sketch, source: sketch.RedundantConstraints.append(0),
            "changed after preflight",
        ),
    ),
)
def test_projection_refuses_stale_or_invalid_exact_state(
    monkeypatch,
    change,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, add_calls = _host(monkeypatch)
    spec = prepare_sketch_projection(document.Uid, values)
    if "count" in message:
        change(document, sketch, source)
        with pytest.raises(NativeSketchError, match=message):
            preflight_sketch_projection(context, spec)
    else:
        prepared = preflight_sketch_projection(context, spec)
        change(document, sketch, source)
        with pytest.raises(NativeSketchError, match=message):
            create_sketch_projection(document, prepared)
        assert add_calls == []


def test_projection_rejects_missing_self_wrong_shape_and_wrong_whole_source(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _Source(document)
    for values in (
        _values(source_name="Missing"),
        _values(source_name="Sketch"),
        _values(subelement="Face1"),
    ):
        with pytest.raises((NativeSketchError, NativeTargetError, ValueError)):
            preflight_sketch_projection(
                context,
                prepare_sketch_projection(document.Uid, values),
            )
    _Source(document, name="WholeFeature")
    with pytest.raises(NativeSketchError, match="whole-object sources"):
        preflight_sketch_projection(
            context,
            prepare_sketch_projection(
                document.Uid,
                _values(source_name="WholeFeature", subelement=None),
            ),
        )
    datum = _Source(document, name="DatumLine", type_id="Part::DatumLine")
    _install_projection_host(sketch, datum, defining=False)
    values = _values(
        source_name="DatumLine",
        subelement=None,
        role="reference",
    )
    diagnostic = _diagnostic(datum, defining=False)
    diagnostic["source_subelement"] = ""
    diagnostic["reference"] = f"{datum.Name}."
    diagnostic["external_geometry_metadata"][0]["reference"] = diagnostic["reference"]

    def diagnose(self, *_args):
        return diagnostic

    sketch.diagnoseExternal = MethodType(diagnose, sketch)
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    assert prepared.plan.defining is False


def test_projection_rejects_duplicate_projection_and_role_mismatched_upgrade(
    monkeypatch,
) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(
        monkeypatch,
        defining=False,
        intersection=True,
    )
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    assert prepared.plan.outcome == "upgraded_intersection"
    assert prepared.plan.final_kind == "projection_and_intersection"
    assert prepared.plan.added_reference is False

    mismatched = {**values, "role": "defining"}
    with pytest.raises(NativeSketchError, match="different target"):
        preflight_sketch_projection(
            context,
            prepare_sketch_projection(document.Uid, mismatched),
        )

    sketch.ExternalTypes = [0]
    with pytest.raises(NativeSketchError, match="already projected"):
        preflight_sketch_projection(
            context,
            prepare_sketch_projection(document.Uid, values),
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"extra": True}), "incomplete diagnostics"),
        (
            lambda value: value.update({"source_object_name": "Other"}),
            "different target",
        ),
        (lambda value: value.update({"requested_defining": False}), "different target"),
        (
            lambda value: value.update({"requested_intersection": True}),
            "different target",
        ),
        (lambda value: value.update({"reference": ""}), "reference key"),
        (lambda value: value.update({"reference_index": 1}), "link outcome"),
        (lambda value: value.update({"type": 2}), "link outcome"),
        (lambda value: value.update({"added_reference": False}), "link outcome"),
        (
            lambda value: value.update({"external_geometry_count": True}),
            "geometry count",
        ),
        (lambda value: value.update({"external_geometry": []}), "geometry count"),
        (
            lambda value: value["external_geometry_metadata"][0].update(
                {"frozen": True}
            ),
            "external metadata",
        ),
        (
            lambda value: setattr(value["external_geometry"][0], "TypeId", "Unknown"),
            "invalid projected geometry",
        ),
        (
            lambda value: setattr(value["external_geometry"][0], "EndPoint", None),
            "invalid projected geometry",
        ),
    ),
)
def test_projection_rejects_untrusted_host_diagnostic(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)
    diagnostic = _diagnostic(source, defining=True)
    mutate(diagnostic)

    def diagnose(self, *_args):
        return diagnostic

    sketch.diagnoseExternal = MethodType(diagnose, sketch)
    with pytest.raises(NativeSketchError, match=message):
        preflight_sketch_projection(
            context,
            prepare_sketch_projection(document.Uid, values),
        )


def test_projection_rejects_impure_or_drifting_diagnostic(monkeypatch) -> None:
    document, sketch, source, context, values, _calls, add_calls = _host(monkeypatch)

    def impure(self, *_args):
        result = _diagnostic(source, defining=True)
        self.Geometry[0].EndPoint.x = 7.0
        return result

    sketch.diagnoseExternal = MethodType(impure, sketch)
    with pytest.raises(NativeSketchError, match="changed after preflight"):
        preflight_sketch_projection(
            context,
            prepare_sketch_projection(document.Uid, values),
        )

    document, sketch, source, context, values, _calls, add_calls = _host(monkeypatch)
    call_count = 0

    def drifting(self, *_args):
        nonlocal call_count
        call_count += 1
        return _diagnostic(source, defining=True, count=call_count)

    sketch.diagnoseExternal = MethodType(drifting, sketch)
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_sketch_projection(document, prepared)
    assert add_calls == []


@pytest.mark.parametrize(
    ("defining", "intersection", "outcome", "kind", "affected"),
    (
        (True, False, "added_projection", "projection", [-3]),
        (False, False, "added_projection", "projection", [-3]),
        (
            False,
            True,
            "upgraded_intersection",
            "projection_and_intersection",
            [-3],
        ),
    ),
)
def test_projection_executes_and_verifies_exact_human_outcomes(
    monkeypatch,
    defining: bool,
    intersection: bool,
    outcome: str,
    kind: str,
    affected: list[int],
) -> None:
    document, sketch, source, context, values, calls, add_calls = _host(
        monkeypatch,
        defining=defining,
        intersection=intersection,
    )
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    draft = create_sketch_projection(document, prepared)
    result = verify_sketch_projection(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert add_calls == [(source.Name, "Edge1", defining, False)]
    assert result["operation"] == "project_external_geometry"
    assert result["source"]["object_name"] == source.Name
    assert result["source"]["subelement"] == "Edge1"
    assert result["role"] == ("defining" if defining else "reference")
    assert result["outcome"] == outcome
    assert result["reference_kind"] == kind
    assert result["affected_geometry_indices"] == affected
    assert result["external_reference_count"] == 1
    assert result["external_geometry_count"] == 1
    assert (result["geometry_count"], result["constraint_count"]) == (1, 0)


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
            lambda sketch, source: setattr(
                sketch.ExternalGeo[2].EndPoint,
                "x",
                8.0,
            ),
            "projected geometry",
        ),
        (
            lambda sketch, source: sketch.ExpressionEngine.append(
                ("Constraints[0]", "1 mm")
            ),
            "expressions",
        ),
        (
            lambda sketch, source: setattr(source.Shape, "token", "changed"),
            "source object",
        ),
        (
            lambda sketch, source: sketch.RedundantConstraints.append(0),
            "solver issues",
        ),
    ),
)
def test_projection_verifier_rejects_every_postcondition_drift(
    monkeypatch,
    corrupt,
    message: str,
) -> None:
    document, sketch, source, context, values, _calls, _add_calls = _host(monkeypatch)
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    draft = create_sketch_projection(document, prepared)
    corrupt(sketch, source)
    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_projection(document, draft)


def test_projection_result_bounds_affected_indices(monkeypatch) -> None:
    document, _sketch, _source, context, values, _calls, _add_calls = _host(
        monkeypatch,
        count=40,
    )
    prepared = preflight_sketch_projection(
        context,
        prepare_sketch_projection(document.Uid, values),
    )
    result = verify_sketch_projection(
        document,
        create_sketch_projection(document, prepared),
    )
    assert result["affected_geometry_count"] == 40
    assert len(result["affected_geometry_indices"]) == 32
    assert result["affected_geometry_indices_truncated"] is True


def test_geometry_runtime_routes_projection_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, _source, context, values, calls, add_calls = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        draft = kwargs["mutate"](document)
        return kwargs["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "project_external_geometry", **values},
        ticket=None,
    )

    assert len(calls) == 2
    assert len(add_calls) == 1
    assert captured["transaction_name"] == "Project Native Sketch External Geometry"
    assert result["operation"] == "project_external_geometry"
