# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchScale import (
    create_scale,
    preflight_scale,
    prepare_scale,
    verify_scale,
)
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    FakeLine,
    fake_facade,
    install_fake_sketch_host,
)


def _values(**changes) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 1,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_indices": [0],
        "center_mm": {"x": 1.0, "y": -1.0},
        "scale_factor": 2.0,
        "keep_originals": False,
    }
    result.update(changes)
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


def _receipt(before_geometry, before_constraints, after_geometry, after_constraints):
    def collection(before, after):
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

    return {
        "geometry": collection(before_geometry, after_geometry),
        "constraints": collection(before_constraints, after_constraints),
    }


def _scale_line(geometry, center, factor: float):
    result = copy.deepcopy(geometry)
    if not isinstance(result, (FakeLine, FakeExternalLine)):
        raise AssertionError("The focused Scale fake only supports lines.")
    for point in (result.StartPoint, result.EndPoint):
        point.x = center.x + (point.x - center.x) * factor
        point.y = center.y + (point.y - center.y) * factor
    return result


def _external_references(sketch) -> list[dict[str, object]]:
    result = []
    index = 0
    for obj, names in sketch.ExternalGeometry:
        for name in names:
            result.append(
                {
                    "object_name": obj.Name,
                    "subelement": name,
                    "type": int(sketch.ExternalTypes[index]),
                }
            )
            index += 1
    return result


def _constraint_references(constraint) -> set[int]:
    return {
        index
        for index in (constraint.First, constraint.Second, constraint.Third)
        if index > -2000
    }


def _scaled_state(
    sketch,
    geometry_indices,
    center,
    factor,
    keep_originals,
    allow_origin_constraints,
    *,
    tag_prefix: str,
):
    assert allow_origin_constraints is False
    before_geometry_tags = [facade.Tag for facade in sketch.GeometryFacadeList]
    before_constraint_tags = [constraint.Tag for constraint in sketch.Constraints]
    source_geometry = [
        copy.deepcopy(sketch.getGeometry(index)) for index in geometry_indices
    ]
    source_facades = [
        copy.deepcopy(sketch.GeometryFacadeList[index])
        if index >= 0
        else fake_facade(source_geometry[offset], 0)
        for offset, index in enumerate(geometry_indices)
    ]

    selected_internal = {index for index in geometry_indices if index >= 0}
    final_geometry = [
        copy.deepcopy(geometry)
        for index, geometry in enumerate(sketch.Geometry)
        if keep_originals or index not in selected_internal
    ]
    final_facades = [
        copy.deepcopy(facade)
        for index, facade in enumerate(sketch.GeometryFacadeList)
        if keep_originals or index not in selected_internal
    ]
    created_geometry_indices = []
    source_to_created = {}
    for source_offset, geometry in enumerate(source_geometry):
        source_index = geometry_indices[source_offset]
        scaled = _scale_line(geometry, center, factor)
        final_index = len(final_geometry)
        tag = f"{tag_prefix}-geometry-{source_offset}"
        scaled.Tag = tag
        source_facade = source_facades[source_offset]
        facade = fake_facade(
            scaled,
            200 + final_index,
            construction=bool(source_facade.Construction),
            internal_type=str(source_facade.InternalType),
        )
        if not keep_originals and source_index >= 0:
            facade.Id = source_facade.Id
        facade.Tag = tag
        final_geometry.append(scaled)
        final_facades.append(facade)
        created_geometry_indices.append(final_index)
        source_to_created[source_index] = final_index

    final_constraints = copy.deepcopy(sketch.Constraints) if keep_originals else []
    final_expressions = list(sketch.ExpressionEngine) if keep_originals else []
    for constraint in sketch.Constraints:
        references = _constraint_references(constraint)
        selected_references = references.intersection(geometry_indices)
        if not selected_references:
            if not keep_originals:
                final_constraints.append(copy.deepcopy(constraint))
            continue
        if not references.issubset(source_to_created):
            continue
        scaled = copy.deepcopy(constraint)
        for field in ("First", "Second", "Third"):
            value = getattr(scaled, field)
            if value in source_to_created:
                setattr(scaled, field, source_to_created[value])
        scaled.Elements = tuple(
            (geometry, position)
            for geometry, position in (
                (scaled.First, scaled.FirstPos),
                (scaled.Second, scaled.SecondPos),
                (scaled.Third, scaled.ThirdPos),
            )
            if geometry > -2000
        )
        if scaled.Type in {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter"}:
            scaled.Value *= factor
        scaled.LabelDistance *= factor
        if scaled.Type not in {"Radius", "Diameter"}:
            scaled.LabelPosition *= factor
        scaled.Tag = f"{tag_prefix}-constraint-{len(final_constraints)}"
        final_constraints.append(scaled)

    receipt = _receipt(
        before_geometry_tags,
        before_constraint_tags,
        [facade.Tag for facade in final_facades],
        [constraint.Tag for constraint in final_constraints],
    )
    return (
        final_geometry,
        final_facades,
        final_constraints,
        final_expressions,
        receipt,
    )


def _diagnostic(sketch, args, *, tag_prefix: str) -> dict[str, object]:
    geometry_indices, center, factor, keep_originals, allow_origin_constraints = args
    geometry, facades, constraints, _expressions, receipt = _scaled_state(
        sketch,
        geometry_indices,
        center,
        factor,
        keep_originals,
        allow_origin_constraints,
        tag_prefix=tag_prefix,
    )
    references = _external_references(sketch)
    external_geometry = copy.deepcopy(sketch.ExternalGeo[2:])
    return {
        "accepted": True,
        "degrees_of_freedom": 4,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": [_metadata(facade) for facade in facades],
        "constraints": constraints,
        "external_reference_count": len(references),
        "external_references": references,
        "external_geometry_count": len(external_geometry),
        "external_geometry": external_geometry,
        "external_geometry_metadata": [
            _external_metadata(value) for value in external_geometry
        ],
        "input_geometry_indices": list(geometry_indices),
        "center_mm": {"x": float(center.x), "y": float(center.y)},
        "scale_factor": float(factor),
        "keep_originals": keep_originals,
        "allow_origin_constraints": allow_origin_constraints,
        "deleted_originals": not keep_originals,
        "geometry_tags": [facade.Tag for facade in facades],
        "constraint_tags": [constraint.Tag for constraint in constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_scale_host(sketch):
    calls = []
    commits = []
    state = {"diagnostic_count": 0, "mutate_diagnostic": None}

    def diagnose(self, *args):
        calls.append(args)
        state["diagnostic_count"] += 1
        result = _diagnostic(
            self,
            args,
            tag_prefix=f"diagnostic-{state['diagnostic_count']}",
        )
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, state["diagnostic_count"])
        return result

    def commit(self, *args):
        commits.append(args)
        geometry, facades, constraints, expressions, receipt = _scaled_state(
            self,
            *args,
            tag_prefix="live",
        )
        self.Geometry = geometry
        self.GeometryFacadeList = facades
        self.Constraints = constraints
        self.ExpressionEngine = expressions
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.DoF = 4
        return receipt

    sketch.diagnoseScale = MethodType(diagnose, sketch)
    sketch.scaleExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, constraints: bool = True):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0].StartPoint.x = 2.0
    sketch.Geometry[0].StartPoint.y = 1.0
    sketch.Geometry[0].EndPoint.x = 5.0
    sketch.Geometry[0].EndPoint.y = 1.0
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    sketch.GeometryFacadeList[0].Id = 313
    if constraints:
        constraint = FakeConstraint("Distance", 0, 1, 0, 2, 3.0)
        constraint.Tag = "constraint-0"
        constraint.LabelDistance = 4.0
        constraint.LabelPosition = 5.0
        sketch.Constraints = [constraint]
        sketch.ConstraintCount = 1
        sketch.ExpressionEngine = [("Constraints[0]", "Spreadsheet.Width")]
    calls, commits, state = _install_scale_host(sketch)
    values = _values(expected_constraint_count=1 if constraints else 0)
    return document, sketch, context, values, calls, commits, state


def test_scale_target_is_closed_bounded_and_explicit(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_scale(document.Uid, values)
    assert spec.geometry_indices == (0,)
    assert spec.center_mm == (1.0, -1.0)
    assert spec.scale_factor == 2.0
    assert spec.keep_originals is False
    assert spec.allow_origin_constraints is False

    invalid = (
        ({**values, "unexpected": True}, "incorrect fields"),
        ({**values, "geometry_indices": [0, 0]}, "unique"),
        ({**values, "center_mm": {"x": float("inf"), "y": 0.0}}, "finite"),
        ({**values, "scale_factor": 0.0}, "greater"),
        ({**values, "scale_factor": 1.0e-7}, "greater"),
        ({**values, "scale_factor": 1_000_001.0}, "no greater"),
        ({**values, "keep_originals": 1}, "true or false"),
    )
    for arguments, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            prepare_scale(document.Uid, arguments)


def test_scale_preflight_rejects_axes_group_members_and_incomplete_internal_geometry(
    monkeypatch,
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch, constraints=False)
    for geometry_index in (-1, -2, -3):
        with pytest.raises(NativeSketchError, match="axes|external geometry index"):
            preflight_scale(
                context,
                prepare_scale(
                    document.Uid,
                    {**values, "geometry_indices": [geometry_index]},
                ),
            )

    second = sketch.addGeometry(FakeLine(), False)
    sketch.GeometryFacadeList[second].Tag = "geometry-1"
    group = FakeConstraint("Group", [1, 0, 0, 0, 1, 0])
    group.Tag = "constraint-group"
    sketch.Constraints = [group]
    sketch.ConstraintCount = 1
    with pytest.raises(NativeSketchError, match="grouped"):
        preflight_scale(
            context,
            prepare_scale(
                document.Uid,
                {
                    **values,
                    "expected_geometry_count": 2,
                    "expected_constraint_count": 1,
                    "geometry_indices": [0],
                },
            ),
        )

    alignment = FakeConstraint(
        "InternalAlignment:Sketcher::BSplineControlPoint", 1, 1, 0, 0
    )
    alignment.Tag = "constraint-alignment"
    sketch.Constraints = [alignment]
    with pytest.raises(NativeSketchError, match="owner together"):
        preflight_scale(
            context,
            prepare_scale(
                document.Uid,
                {
                    **values,
                    "expected_geometry_count": 2,
                    "expected_constraint_count": 1,
                    "geometry_indices": [1],
                },
            ),
        )


def test_scale_diagnosis_is_pure_and_replace_executes_once(monkeypatch) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    prepared = preflight_scale(context, prepare_scale(document.Uid, values))
    assert sketch.Geometry[0].StartPoint.x == before[0].StartPoint.x
    assert len(calls) == 1

    draft = create_scale(document, prepared)
    result = verify_scale(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert len(commits) == 1
    assert (sketch.Geometry[0].StartPoint.x, sketch.Geometry[0].StartPoint.y) == (
        3.0,
        3.0,
    )
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        9.0,
        3.0,
    )
    assert sketch.GeometryFacadeList[0].Id == 313
    assert sketch.Constraints[0].Value == 6.0
    assert sketch.Constraints[0].LabelDistance == 8.0
    assert sketch.Constraints[0].LabelPosition == 10.0
    assert sketch.ExpressionEngine == []
    assert result["mode"] == "replace"
    assert result["created_geometry_indices"] == [0]
    assert result["deleted_geometry_indices"] == [0]
    assert result["created_constraint_indices"] == [0]
    assert result["deleted_constraint_indices"] == [0]


def test_scale_supports_keep_originals_and_external_geometry(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, _state = _host(monkeypatch)
    keep_values = {**values, "keep_originals": True, "scale_factor": 0.5}
    result = verify_scale(
        document,
        create_scale(
            document,
            preflight_scale(context, prepare_scale(document.Uid, keep_values)),
        ),
    )
    assert result["mode"] == "copy"
    assert result["created_geometry_indices"] == [1]
    assert result["deleted_geometry_indices"] == []
    assert [geometry.StartPoint.x for geometry in sketch.Geometry] == [2.0, 1.5]
    assert [constraint.Value for constraint in sketch.Constraints] == [3.0, 1.5]
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Width")]

    document, sketch, context, values, _calls, _commits, _state = _host(
        monkeypatch, constraints=False
    )
    source = type(
        "Source",
        (),
        {
            "Name": "Source",
            "Label": "Source",
            "TypeId": "Part::Feature",
            "Document": document,
        },
    )()
    document.Objects.append(source)
    sketch.ExternalGeometry = [(source, ("Edge1",))]
    sketch.ExternalTypes = [0]
    sketch.ExternalGeo.append(FakeExternalLine("Source.Edge1"))
    external_values = {
        **values,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 1,
        "geometry_indices": [-3],
        "keep_originals": True,
    }
    result = verify_scale(
        document,
        create_scale(
            document,
            preflight_scale(context, prepare_scale(document.Uid, external_values)),
        ),
    )
    assert result["created_geometry_indices"] == [1]
    assert len(sketch.ExternalGeo) == 3


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda sketch: setattr(sketch, "GeometryCount", 2), "count"),
        (lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 9.0), "changed"),
        (
            lambda sketch: sketch.ExpressionEngine.append(
                ("Constraints[0]", "Spreadsheet.Other")
            ),
            "changed",
        ),
        (lambda sketch: setattr(sketch.Constraints[0], "Tag", "different"), "changed"),
    ),
)
def test_scale_refuses_stale_state(monkeypatch, change, message: str) -> None:
    document, sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    spec = prepare_scale(document.Uid, values)
    if message == "count":
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            preflight_scale(context, spec)
    else:
        prepared = preflight_scale(context, spec)
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            create_scale(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"input_geometry_indices": [4]}),
            "different operation",
        ),
        (
            lambda result, _count: result.update({"allow_origin_constraints": True}),
            "different operation",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
        (
            lambda result, _count: result["geometry_tags"].__setitem__(0, "wrong"),
            "wrong created geometry",
        ),
        (
            lambda result, _count: result["mutation_receipt"]["constraints"].update(
                {"created": []}
            ),
            "account for every",
        ),
    ),
)
def test_scale_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message: str
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_scale(context, prepare_scale(document.Uid, values))


def test_scale_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(result, count):
        if count == 1:
            sketch.Geometry[0].EndPoint.x = 8.0

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_scale(context, prepare_scale(document.Uid, values))

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drifting(result, count):
        if count == 2:
            result["geometry"][0].EndPoint.x = 99.0

    state["mutate_diagnostic"] = drifting
    prepared = preflight_scale(context, prepare_scale(document.Uid, values))
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_scale(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            lambda sketch, draft: setattr(sketch.Geometry[0].EndPoint, "x", 99.0),
            "geometry",
        ),
        (
            lambda sketch, draft: setattr(sketch.Constraints[0], "Type", "Vertical"),
            "constraints",
        ),
        (
            lambda sketch, draft: sketch.ExpressionEngine.append(
                ("Constraints[0]", "Spreadsheet.Wrong")
            ),
            "expressions",
        ),
        (lambda sketch, draft: setattr(sketch, "DoF", 2), "solver"),
        (
            lambda sketch, draft: draft.value.receipt["geometry"]["created"][0].update(
                {"tag": "wrong"}
            ),
            "wrong created geometry",
        ),
    ),
)
def test_scale_verifier_rejects_every_final_state_drift(
    monkeypatch, change, message: str
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_scale(context, prepare_scale(document.Uid, values))
    draft = create_scale(document, prepared)
    change(sketch, draft)
    with pytest.raises(NativeSketchError, match=message):
        verify_scale(document, draft)


def test_geometry_runtime_routes_scale_through_one_exact_transaction(
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
        {"operation": "scale", **values},
        ticket=None,
    )

    assert len(calls) == 2
    assert len(commits) == 1
    assert captured["transaction_name"] == "Scale Native Sketch Geometry"
    assert result["operation"] == "scale"
