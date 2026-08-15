# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchCarbonCopy import (
    create_carbon_copy,
    preflight_carbon_copy,
    prepare_carbon_copy,
    verify_carbon_copy,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    FakeSketch,
    fake_facade,
    install_fake_sketch_host,
)


def _values(
    *,
    geometry_mode: str = "construction",
    permission: str = "same_body_aligned",
    source_reference_count: int = 0,
    source_external_count: int = 0,
) -> dict[str, object]:
    return {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "source_sketch": {"object_name": "Source"},
        "expected_source_geometry_count": 1,
        "expected_source_constraint_count": 1,
        "expected_source_external_reference_count": source_reference_count,
        "expected_source_external_geometry_count": source_external_count,
        "geometry_mode": geometry_mode,
        "reference_permission": permission,
    }


def _receipt() -> dict[str, object]:
    return {
        "geometry": {
            "identity": "native_tag",
            "old_to_new": {"0": 0},
            "deleted": [],
            "created": [{"index": 1, "tag": "carbon-geometry-1"}],
        },
        "constraints": {
            "identity": "native_tag",
            "old_to_new": {},
            "deleted": [],
            "created": [{"index": 0, "tag": "carbon-constraint-0"}],
        },
    }


def _metadata(index: int, construction: bool) -> dict[str, object]:
    return {
        "Id": 100 + index,
        "Construction": construction,
        "Blocked": False,
        "InternalType": "",
        "GeometryLayerId": 0,
    }


def _external_metadata(reference: str) -> dict[str, object]:
    return {
        "reference": reference,
        "defining": False,
        "frozen": False,
        "detached": False,
        "missing": False,
        "synchronized": False,
    }


class _LinkedObject:
    Name = "Box"
    Label = "Carbon source support"
    TypeId = "Part::Feature"

    def __init__(self, document) -> None:
        self.Document = document
        self.InList = []
        document.Objects.append(self)

    def isDerivedFrom(self, type_id: str) -> bool:
        return self.TypeId == type_id


def _diagnostic(
    target,
    source,
    *,
    construction: bool,
    allow_other_body: bool,
    allow_unaligned: bool,
    x_inverted: bool = False,
    y_inverted: bool = False,
) -> dict[str, object]:
    copied_construction = construction or bool(
        source.GeometryFacadeList[0].Construction
    )
    copied_constraint = copy.deepcopy(source.Constraints[0])
    for field in ("First", "Second", "Third"):
        value = int(getattr(copied_constraint, field))
        if value >= 0:
            setattr(copied_constraint, field, value + target.GeometryCount)
    external_references = []
    for obj, names in source.ExternalGeometry:
        for name in names:
            external_references.append(
                {"object_name": obj.Name, "subelement": name, "type": 0}
            )
    external_geometry = [copy.deepcopy(value) for value in source.ExternalGeo[2:]]
    external_metadata = [
        _external_metadata(value.extension.Ref) for value in external_geometry
    ]
    return {
        "accepted": True,
        "degrees_of_freedom": 3,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "source_object_name": source.Name,
        "requested_construction": construction,
        "requested_allow_other_body": allow_other_body,
        "requested_allow_unaligned": allow_unaligned,
        "x_inverted": x_inverted,
        "y_inverted": y_inverted,
        "copied_geometry_count": 1,
        "copied_constraint_count": 1,
        "copied_external_reference_count": len(external_references),
        "geometry_count": 2,
        "constraint_count": 1,
        "geometry": [
            copy.deepcopy(target.Geometry[0]),
            copy.deepcopy(source.Geometry[0]),
        ],
        "geometry_metadata": [
            _metadata(0, bool(target.GeometryFacadeList[0].Construction)),
            _metadata(1, copied_construction),
        ],
        "constraints": [copied_constraint],
        "external_reference_count": len(external_references),
        "external_references": external_references,
        "external_geometry_count": len(external_geometry),
        "external_geometry": external_geometry,
        "external_geometry_metadata": external_metadata,
        "expressions": [
            {
                "constraint_index": 0,
                "path": "Constraints[0]",
                "expression": "Source.Constraints[0]",
            }
        ],
        "mutation_receipt": _receipt(),
    }


def _install_carbon_host(target, source):
    calls = []
    commits = []
    state = {"x_inverted": False, "y_inverted": False, "diagnostic_count": 0}

    def diagnose(self, source_name, construction, allow_other_body, allow_unaligned):
        calls.append((source_name, construction, allow_other_body, allow_unaligned))
        state["diagnostic_count"] += 1
        result = _diagnostic(
            target,
            source,
            construction=construction,
            allow_other_body=allow_other_body,
            allow_unaligned=allow_unaligned,
            x_inverted=state["x_inverted"],
            y_inverted=state["y_inverted"],
        )
        result["geometry"][1].Tag = f"detached-diagnostic-{state['diagnostic_count']}"
        return result

    def commit(self, source_name, construction, allow_other_body, allow_unaligned):
        commits.append((source_name, construction, allow_other_body, allow_unaligned))
        copied_geometry = copy.deepcopy(source.Geometry[0])
        self.Geometry.append(copied_geometry)
        facade = fake_facade(
            copied_geometry,
            1,
            construction=construction
            or bool(source.GeometryFacadeList[0].Construction),
        )
        facade.Tag = "carbon-geometry-1"
        self.GeometryFacadeList.append(facade)
        copied_constraint = copy.deepcopy(source.Constraints[0])
        for field in ("First", "Second", "Third"):
            value = int(getattr(copied_constraint, field))
            if value >= 0:
                setattr(copied_constraint, field, value + 1)
        self.Constraints.append(copied_constraint)
        self.ExpressionEngine.append(("Constraints[0]", "Source.Constraints[0]"))
        for obj, names in source.ExternalGeometry:
            self.ExternalGeometry.append((obj, tuple(names)))
            self.ExternalTypes.append(0)
        self.ExternalGeo.extend(copy.deepcopy(source.ExternalGeo[2:]))
        self.GeometryCount = len(self.Geometry)
        self.ConstraintCount = len(self.Constraints)
        self.DoF = 3
        return _receipt()

    target.diagnoseCarbonCopy = MethodType(diagnose, target)
    target.carbonCopyExact = MethodType(commit, target)
    return calls, commits, state


def _host(monkeypatch, *, with_external: bool = False):
    document, target, context = install_fake_sketch_host(monkeypatch)
    source = FakeSketch(document)
    source.Name = "Source"
    source.Label = "Carbon source"
    source.Constraints = [FakeConstraint("Distance", 0, 5.0)]
    source.ConstraintCount = 1
    source.DoF = 3
    source.Geometry[0].StartPoint.x = 2.0
    source.Geometry[0].EndPoint.x = 7.0
    linked = None
    if with_external:
        linked = _LinkedObject(document)
        source.ExternalGeometry = [(linked, ("Edge1",))]
        source.ExternalTypes = [0]
        source.ExternalGeo.append(FakeExternalLine("Box.Edge1"))
    calls, commits, state = _install_carbon_host(target, source)
    values = _values(
        source_reference_count=1 if with_external else 0,
        source_external_count=1 if with_external else 0,
    )
    return document, target, source, linked, context, values, calls, commits, state


def test_carbon_copy_target_is_closed_and_maps_exact_human_permission_modes(
    monkeypatch,
) -> None:
    document, _target, _source, _linked, _context, values, *_rest = _host(monkeypatch)
    expected = {
        "same_body_aligned": (False, False),
        "cross_body_aligned": (True, False),
        "unaligned": (True, True),
    }
    for permission, options in expected.items():
        spec = prepare_carbon_copy(
            document.Uid,
            {**values, "reference_permission": permission},
        )
        assert (spec.allow_other_body, spec.allow_unaligned) == options

    with pytest.raises(NativeSketchError, match="incorrect fields"):
        prepare_carbon_copy(document.Uid, {**values, "unexpected": True})
    with pytest.raises(NativeSketchError, match="permission"):
        prepare_carbon_copy(
            document.Uid,
            {**values, "reference_permission": "automatic"},
        )


def test_carbon_copy_preflight_is_pure_and_reads_exact_source_and_target(
    monkeypatch,
) -> None:
    document, target, source, _linked, context, values, calls, _commits, state = _host(
        monkeypatch
    )
    before = (
        copy.deepcopy(target.Geometry),
        copy.deepcopy(source.Geometry),
        list(target.ExpressionEngine),
        list(source.ExpressionEngine),
    )
    prepared = preflight_carbon_copy(
        context,
        prepare_carbon_copy(document.Uid, values),
    )

    assert calls == [(source.Name, True, False, False)]
    assert state["diagnostic_count"] == 1
    assert prepared.plan.degrees_of_freedom == 3
    assert prepared.plan.x_inverted is False
    assert target.Geometry[0].EndPoint.x == before[0][0].EndPoint.x
    assert source.Geometry[0].EndPoint.x == before[1][0].EndPoint.x
    assert target.ExpressionEngine == before[2]
    assert source.ExpressionEngine == before[3]


def test_carbon_copy_executes_once_and_verifies_all_exact_state(monkeypatch) -> None:
    document, _target, source, _linked, context, values, calls, commits, state = _host(
        monkeypatch,
        with_external=True,
    )
    state["x_inverted"] = True
    prepared = preflight_carbon_copy(
        context,
        prepare_carbon_copy(document.Uid, values),
    )
    draft = create_carbon_copy(document, prepared)
    result = verify_carbon_copy(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert commits == [(source.Name, True, False, False)]
    assert result["operation"] == "carbon_copy"
    assert result["source_sketch"]["object_name"] == source.Name
    assert result["geometry_mode"] == "construction"
    assert result["reference_permission"] == "same_body_aligned"
    assert result["x_inverted"] is True
    assert result["y_inverted"] is False
    assert result["copied_geometry_count"] == 1
    assert result["copied_constraint_count"] == 1
    assert result["created_geometry_indices"] == [1]
    assert result["created_constraint_indices"] == [0]
    assert result["external_reference_count"] == 1
    assert result["external_geometry_count"] == 1


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda target, source: setattr(target, "GeometryCount", 2), "count"),
        (
            lambda target, source: setattr(source.Geometry[0].EndPoint, "x", 9.0),
            "changed",
        ),
        (
            lambda target, source: source.ExpressionEngine.append(
                ("Constraints[0]", "6 mm")
            ),
            "changed",
        ),
    ),
)
def test_carbon_copy_refuses_stale_target_or_source(
    monkeypatch,
    change,
    message: str,
) -> None:
    document, target, source, _linked, context, values, _calls, commits, _state = _host(
        monkeypatch
    )
    spec = prepare_carbon_copy(document.Uid, values)
    if message == "count":
        change(target, source)
        with pytest.raises(NativeSketchError, match=message):
            preflight_carbon_copy(context, spec)
    else:
        prepared = preflight_carbon_copy(context, spec)
        change(target, source)
        with pytest.raises(NativeSketchError, match=message):
            create_carbon_copy(document, prepared)
        assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"unexpected": True}), "incomplete diagnostics"),
        (
            lambda value: value.update({"requested_construction": False}),
            "different operation",
        ),
        (
            lambda value: value.update({"copied_geometry_count": 2}),
            "different operation",
        ),
        (lambda value: value.update({"accepted": False}), "solver issue"),
        (
            lambda value: value["expressions"][0].update({"path": "Constraints[4]"}),
            "expression target",
        ),
        (
            lambda value: value["mutation_receipt"]["geometry"].update({"created": []}),
            "account for every",
        ),
    ),
)
def test_carbon_copy_rejects_untrusted_host_diagnostics(
    monkeypatch,
    mutate,
    message: str,
) -> None:
    document, target, source, _linked, context, values, *_rest = _host(monkeypatch)
    diagnostic = _diagnostic(
        target,
        source,
        construction=True,
        allow_other_body=False,
        allow_unaligned=False,
    )
    mutate(diagnostic)
    target.diagnoseCarbonCopy = MethodType(
        lambda self, *_args: copy.deepcopy(diagnostic),
        target,
    )
    with pytest.raises(NativeSketchError, match=message):
        preflight_carbon_copy(
            context,
            prepare_carbon_copy(document.Uid, values),
        )


def test_carbon_copy_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, target, source, _linked, context, values, _calls, commits, _state = _host(
        monkeypatch
    )

    def impure(self, *args):
        result = _diagnostic(
            target,
            source,
            construction=args[1],
            allow_other_body=args[2],
            allow_unaligned=args[3],
        )
        self.Geometry[0].EndPoint.x = 8.0
        return result

    target.diagnoseCarbonCopy = MethodType(impure, target)
    with pytest.raises(NativeSketchError, match="changed a live Sketch"):
        preflight_carbon_copy(
            context,
            prepare_carbon_copy(document.Uid, values),
        )

    document, target, source, _linked, context, values, _calls, commits, _state = _host(
        monkeypatch
    )
    count = 0

    def drifting(self, source_name, construction, allow_other_body, allow_unaligned):
        nonlocal count
        count += 1
        result = _diagnostic(
            target,
            source,
            construction=construction,
            allow_other_body=allow_other_body,
            allow_unaligned=allow_unaligned,
        )
        if count == 2:
            result["geometry"][1].EndPoint.x = 9.0
        return result

    target.diagnoseCarbonCopy = MethodType(drifting, target)
    prepared = preflight_carbon_copy(
        context,
        prepare_carbon_copy(document.Uid, values),
    )
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_carbon_copy(document, prepared)
    assert commits == []


def test_carbon_copy_verifier_rejects_source_and_target_drift(monkeypatch) -> None:
    document, target, source, _linked, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_carbon_copy(
        context,
        prepare_carbon_copy(document.Uid, values),
    )
    draft = create_carbon_copy(document, prepared)
    source.Geometry[0].EndPoint.x = 12.0
    with pytest.raises(NativeSketchError, match="source Sketch"):
        verify_carbon_copy(document, draft)

    source.Geometry[0].EndPoint.x = 7.0
    target.ExpressionEngine[0] = ("Constraints[0]", "Wrong.Constraints[0]")
    with pytest.raises(NativeSketchError, match="expressions"):
        verify_carbon_copy(document, draft)


def test_geometry_runtime_routes_carbon_copy_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _target, _source, _linked, context, values, calls, commits, _state = (
        _host(monkeypatch)
    )
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "carbon_copy", **values},
        ticket=None,
    )

    assert len(calls) == 2
    assert len(commits) == 1
    assert captured["transaction_name"] == "Create Native Sketch Carbon Copy"
    assert result["operation"] == "carbon_copy"
