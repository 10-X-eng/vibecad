# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchNURBSConversion import (
    create_nurbs_conversion,
    preflight_nurbs_conversion,
    prepare_nurbs_conversion,
    verify_nurbs_conversion,
)
from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
    FakeConstraint,
    FakeExternalLine,
    FakePoint,
    fake_facade,
    install_fake_sketch_host,
)


def _values(sketch, indices=None, **updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": sketch.GeometryCount,
        "expected_constraint_count": sketch.ConstraintCount,
        "expected_external_reference_count": len(sketch.ExternalGeometry),
        "expected_external_geometry_count": len(sketch.ExternalGeo) - 2,
        "geometry_indices": [0] if indices is None else indices,
    }
    result.update(updates)
    return result


def _metadata(facade) -> dict[str, object]:
    return {
        "Id": int(facade.Id),
        "Construction": bool(facade.Construction),
        "Blocked": bool(facade.Blocked),
        "InternalType": str(facade.InternalType),
        "GeometryLayerId": int(facade.GeometryLayerId),
    }


def _external_metadata(geometry) -> dict[str, object]:
    extension = geometry.extension
    return {
        "reference": str(extension.Ref),
        "defining": bool(extension.testFlag("Defining")),
        "frozen": bool(extension.testFlag("Frozen")),
        "detached": bool(extension.testFlag("Detached")),
        "missing": bool(extension.testFlag("Missing")),
        "synchronized": bool(extension.testFlag("Sync")),
    }


def _external_references(sketch) -> list[dict[str, object]]:
    result = []
    offset = 0
    for obj, names in sketch.ExternalGeometry:
        for name in names:
            result.append(
                {
                    "object_name": obj.Name,
                    "subelement": name,
                    "type": int(sketch.ExternalTypes[offset]),
                }
            )
            offset += 1
    return result


def _collection_receipt(before, after) -> dict[str, object]:
    matched = set()
    old_to_new = {}
    deleted = []
    for old_index, tag in enumerate(before):
        new_index = next(
            (
                index
                for index, candidate in enumerate(after)
                if index not in matched and candidate == tag
            ),
            None,
        )
        if new_index is None:
            deleted.append({"index": old_index, "tag": tag})
        else:
            matched.add(new_index)
            old_to_new[str(old_index)] = new_index
    created = [
        {"index": index, "tag": tag}
        for index, tag in enumerate(after)
        if index not in matched
    ]
    return {
        "identity": "native_tag",
        "old_to_new": old_to_new,
        "deleted": deleted,
        "created": created,
    }


def _references(constraint) -> tuple[tuple[int, int], ...]:
    return tuple(
        (geometry, position)
        for geometry, position in (
            (constraint.First, constraint.FirstPos),
            (constraint.Second, constraint.SecondPos),
            (constraint.Third, constraint.ThirdPos),
        )
        if geometry > -2000
    )


def _simulate(sketch, geometry_indices):
    simulated = copy.deepcopy(sketch)
    before_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    before_constraints = [value.Tag for value in simulated.Constraints]
    internal = {index for index in geometry_indices if index >= 0}
    converted = []

    for index in geometry_indices:
        spline = FakeBSpline()
        if index >= 0:
            old = simulated.GeometryFacadeList[index]
            simulated.Geometry[index] = spline
            facade = fake_facade(spline, index)
            facade.Id = old.Id
            facade.Tag = f"converted-root-{index}"
            simulated.GeometryFacadeList[index] = facade
            converted.append(index)
        else:
            created_index = len(simulated.Geometry)
            simulated.Geometry.append(spline)
            facade = fake_facade(spline, created_index)
            facade.Tag = f"external-root-{created_index}"
            simulated.GeometryFacadeList.append(facade)
            converted.append(created_index)
    simulated.GeometryCount = len(simulated.Geometry)

    kept_constraints = []
    for constraint in simulated.Constraints:
        involved = [
            (geometry, position)
            for geometry, position in _references(constraint)
            if geometry in internal
        ]
        remove = bool(involved) and (
            constraint.Type != "Coincident"
            or any(position == 3 for _geometry, position in involved)
        )
        if not remove:
            kept_constraints.append(constraint)
    simulated.Constraints = kept_constraints
    simulated.ConstraintCount = len(kept_constraints)

    exposed = 0
    for index in geometry_indices:
        if index >= 0:
            exposed += simulated.exposeInternalGeometry(index)["created_count"]
    for index, facade in enumerate(simulated.GeometryFacadeList):
        if not facade.Tag:
            facade.Tag = f"created-helper-{index}"
    for index, constraint in enumerate(simulated.Constraints):
        if not getattr(constraint, "Tag", ""):
            constraint.Tag = f"created-constraint-{index}"
    simulated.GeometryCount = len(simulated.Geometry)
    simulated.ConstraintCount = len(simulated.Constraints)
    simulated.DoF = 7

    after_geometry = [value.Tag for value in simulated.GeometryFacadeList]
    after_constraints = [value.Tag for value in simulated.Constraints]
    receipt = {
        "geometry": _collection_receipt(before_geometry, after_geometry),
        "constraints": _collection_receipt(before_constraints, after_constraints),
    }
    return simulated, converted, exposed, receipt


def _diagnostic(sketch, geometry_indices) -> dict[str, object]:
    simulated, converted, exposed, receipt = _simulate(sketch, geometry_indices)
    references = _external_references(simulated)
    external = copy.deepcopy(simulated.ExternalGeo[2:])
    return {
        "accepted": True,
        "degrees_of_freedom": simulated.DoF,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "geometry_count": simulated.GeometryCount,
        "constraint_count": simulated.ConstraintCount,
        "geometry": simulated.Geometry,
        "geometry_metadata": [
            _metadata(value) for value in simulated.GeometryFacadeList
        ],
        "constraints": simulated.Constraints,
        "external_reference_count": len(references),
        "external_references": references,
        "external_geometry_count": len(external),
        "external_geometry": external,
        "external_geometry_metadata": [_external_metadata(value) for value in external],
        "input_geometry_indices": list(geometry_indices),
        "converted_geometry_indices": converted,
        "exposed_internal_geometry_count": exposed,
        "geometry_tags": [value.Tag for value in simulated.GeometryFacadeList],
        "constraint_tags": [value.Tag for value in simulated.Constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_conversion_host(sketch):
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, geometry_indices):
        calls.append(tuple(geometry_indices))
        result = _diagnostic(self, geometry_indices)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, geometry_indices):
        commits.append(tuple(geometry_indices))
        simulated, _converted, _exposed, receipt = _simulate(self, geometry_indices)
        old_to_new = receipt["constraints"]["old_to_new"]
        expressions = []
        for path, expression in self.ExpressionEngine:
            if path.startswith("Constraints[") and path.endswith("]"):
                old = path[12:-1]
                if old in old_to_new:
                    expressions.append((f"Constraints[{old_to_new[old]}]", expression))
            else:
                expressions.append((path, expression))
        for field in (
            "Geometry",
            "GeometryFacadeList",
            "Constraints",
            "GeometryCount",
            "ConstraintCount",
            "DoF",
        ):
            setattr(self, field, getattr(simulated, field))
        self.ExpressionEngine = expressions
        return receipt

    sketch.diagnoseConvertToNURBS = MethodType(diagnose, sketch)
    sketch.convertToNURBSExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, external=False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch._persistent_geometry_tags = True
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    sketch._next_geometry_tag = 1
    constraint = FakeConstraint("Distance", 0, 5.0)
    constraint.Tag = "distance-0"
    sketch.Constraints = [constraint]
    sketch.ConstraintCount = 1
    sketch.ExpressionEngine = [("Constraints[0]", "Spreadsheet.Length")]
    indices = [0]
    if external:
        source = SimpleNamespace(
            Name="Source", TypeId="Part::Feature", Document=sketch.Document
        )
        sketch.Document.Objects.append(source)
        sketch.ExternalGeometry = [(source, ["Edge1"])]
        sketch.ExternalTypes = [0]
        sketch.ExternalGeo.append(FakeExternalLine("Source.Edge1"))
        indices = [-3, 0]
    calls, commits, state = _install_conversion_host(sketch)
    return document, sketch, context, _values(sketch, indices), calls, commits, state


def test_conversion_target_is_closed_signed_bounded_and_exact(monkeypatch) -> None:
    document, sketch, _context, values, *_rest = _host(monkeypatch, external=True)
    spec = prepare_nurbs_conversion(document.Uid, values)
    assert spec.geometry_indices == (-3, 0)
    for arguments in (
        {**values, "unexpected": True},
        {**values, "geometry_indices": []},
        {**values, "geometry_indices": [0, 0]},
        {**values, "geometry_indices": [True]},
        {**values, "geometry_indices": list(range(257))},
        {**values, "expected_external_geometry_count": -1},
    ):
        with pytest.raises(NativeSketchError):
            prepare_nurbs_conversion(document.Uid, arguments)
    assert sketch.GeometryCount == 1


def test_conversion_diagnosis_is_pure_and_exact_commit_is_verified(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    before_type = sketch.Geometry[0].TypeId
    prepared = preflight_nurbs_conversion(
        context, prepare_nurbs_conversion(document.Uid, values)
    )
    assert sketch.Geometry[0].TypeId == before_type
    draft = create_nurbs_conversion(document, prepared)
    result = verify_nurbs_conversion(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and len(commits) == 1
    assert sketch.Geometry[0].TypeId == "Part::GeomBSplineCurve"
    assert sketch.ExpressionEngine == []
    assert result == {
        **result,
        "operation": "convert_to_nurbs",
        "input_geometry_count": 1,
        "converted_geometry_indices": [0],
        "internal_conversion_count": 1,
        "external_copy_count": 0,
        "exposed_internal_geometry_count": 4,
        "created_geometry_count": 5,
        "removed_geometry_count": 1,
        "created_constraint_count": 6,
        "removed_constraint_count": 1,
    }


def test_conversion_supports_mixed_external_and_internal_edges(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, _state = _host(
        monkeypatch, external=True
    )
    prepared = preflight_nurbs_conversion(
        context, prepare_nurbs_conversion(document.Uid, values)
    )
    result = verify_nurbs_conversion(
        document, create_nurbs_conversion(document, prepared)
    )
    assert result["converted_geometry_indices"] == [1, 0]
    assert result["internal_conversion_count"] == 1
    assert result["external_copy_count"] == 1
    assert result["created_geometry_count"] == 6
    assert sketch.GeometryCount == 6
    assert len(sketch.ExternalGeo) == 3


@pytest.mark.parametrize("index", (-1, -2, -3, 1))
def test_conversion_rejects_stale_axes_and_external_targets(monkeypatch, index) -> None:
    document, _sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    with pytest.raises(NativeSketchError):
        preflight_nurbs_conversion(
            context,
            prepare_nurbs_conversion(
                document.Uid, {**values, "geometry_indices": [index]}
            ),
        )
    assert commits == []


def test_conversion_rejects_points_groups_and_internal_geometry(monkeypatch) -> None:
    document, sketch, context, _values_unused, _calls, commits, _state = _host(
        monkeypatch
    )
    point = sketch.addGeometry(FakePoint(SimpleNamespace(x=1, y=2, z=0)), False)
    with pytest.raises(NativeSketchError, match="points"):
        preflight_nurbs_conversion(
            context,
            prepare_nurbs_conversion(document.Uid, _values(sketch, [point])),
        )
    sketch.GeometryFacadeList[0].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        preflight_nurbs_conversion(
            context,
            prepare_nurbs_conversion(document.Uid, _values(sketch, [0])),
        )
    assert commits == []


def test_conversion_rejects_excessive_existing_spline_state_before_diagnosis(
    monkeypatch,
) -> None:
    document, sketch, context, _unused, calls, commits, _state = _host(monkeypatch)
    spline = FakeBSpline()
    spline.NbPoles = 4_096
    sketch.Geometry[0] = spline
    sketch.GeometryFacadeList[0].Geometry = spline
    with pytest.raises(NativeSketchError, match="too much spline state"):
        preflight_nurbs_conversion(
            context,
            prepare_nurbs_conversion(document.Uid, _values(sketch, [0])),
        )
    assert calls == []
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"converted_geometry_indices": [9]}),
            "different operation",
        ),
        (
            lambda result, _count: setattr(
                result["geometry"][0], "TypeId", "Part::GeomLineSegment"
            ),
            "B-spline",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_conversion_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_nurbs_conversion(
            context, prepare_nurbs_conversion(document.Uid, values)
        )


def test_conversion_refuses_stale_impure_and_drifting_state(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.GeometryFacadeList[0].Blocked = True

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_nurbs_conversion(
            context, prepare_nurbs_conversion(document.Uid, values)
        )
    assert commits == []

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drift(result, count):
        if count == 2:
            result["exposed_internal_geometry_count"] = 3

    state["mutate_diagnostic"] = drift
    prepared = preflight_nurbs_conversion(
        context, prepare_nurbs_conversion(document.Uid, values)
    )
    with pytest.raises(NativeSketchError, match="invalid amount"):
        create_nurbs_conversion(document, prepared)
    assert commits == []


def test_conversion_verifier_rejects_semantic_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_nurbs_conversion(
        context, prepare_nurbs_conversion(document.Uid, values)
    )
    draft = create_nurbs_conversion(document, prepared)
    sketch.GeometryFacadeList[-1].Construction = False
    with pytest.raises(NativeSketchError, match="final geometry"):
        verify_nurbs_conversion(document, draft)


def test_geometry_runtime_routes_conversion_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, context, values, calls, commits, _state = _host(monkeypatch)
    captured = {}

    def run_immediate(runtime_context, **kwargs):
        assert runtime_context is context
        captured.update(kwargs)
        return kwargs["verify"](document, kwargs["mutate"](document))

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchGeometryRuntime(context).mutate_geometry(
        {"operation": "convert_to_nurbs", **values}, ticket=None
    )
    assert len(calls) == 2 and len(commits) == 1
    assert captured["transaction_name"] == "Convert Sketch Geometry to B-Splines"
    assert result["operation"] == "convert_to_nurbs"
