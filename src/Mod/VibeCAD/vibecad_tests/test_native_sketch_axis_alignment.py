# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchAxisAlignment import (
    create_axis_alignment,
    preflight_axis_alignment,
    prepare_axis_alignment,
    verify_axis_alignment,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeLine,
    install_fake_sketch_host,
)


def _values(constraint_count: int = 0, **changes) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": constraint_count,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_indices": [0],
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


def _involves(constraint, selected: set[int]) -> bool:
    return any(
        geometry in selected
        for geometry in (constraint.First, constraint.Second, constraint.Third)
    )


def _axis_state(sketch, geometry_indices):
    selected = set(geometry_indices)
    before_geometry_tags = [facade.Tag for facade in sketch.GeometryFacadeList]
    before_constraint_tags = [constraint.Tag for constraint in sketch.Constraints]
    constraints = []
    anchors = {}
    counts = {
        "removed_horizontal_constraints": 0,
        "removed_vertical_constraints": 0,
        "created_parallel_constraints": 0,
        "removed_axis_symmetry_constraints": 0,
        "removed_point_on_axis_constraints": 0,
        "converted_distance_constraints": 0,
    }
    for constraint in sketch.Constraints:
        if not _involves(constraint, selected):
            constraints.append(copy.deepcopy(constraint))
            continue
        if (
            constraint.Type in {"Horizontal", "Vertical"}
            and constraint.FirstPos == 0
            and constraint.SecondPos == 0
        ):
            orientation = constraint.Type.lower()
            counts[f"removed_{orientation}_constraints"] += 1
            if orientation not in anchors:
                anchors[orientation] = constraint.First
            else:
                parallel = FakeConstraint(
                    "Parallel", anchors[orientation], constraint.First
                )
                parallel.Tag = f"created-parallel-{orientation}-{constraint.First}"
                constraints.append(parallel)
                counts["created_parallel_constraints"] += 1
            continue
        if (
            constraint.Type == "Symmetric"
            and constraint.Third in {-1, -2}
            and constraint.ThirdPos == 0
        ):
            counts["removed_axis_symmetry_constraints"] += 1
            continue
        if (
            constraint.Type == "PointOnObject"
            and constraint.Second in {-1, -2}
            and constraint.SecondPos == 0
        ):
            counts["removed_point_on_axis_constraints"] += 1
            continue
        current = copy.deepcopy(constraint)
        if constraint.Type in {"DistanceX", "DistanceY"}:
            current.Type = "Distance"
            counts["converted_distance_constraints"] += 1
        constraints.append(current)
    after_constraint_tags = [constraint.Tag for constraint in constraints]
    receipt = {
        "geometry": _collection_receipt(
            before_geometry_tags,
            before_geometry_tags,
        ),
        "constraints": _collection_receipt(
            before_constraint_tags,
            after_constraint_tags,
        ),
    }
    expressions = []
    index_map = receipt["constraints"]["old_to_new"]
    for path, expression in sketch.ExpressionEngine:
        if path.startswith("Constraints[") and path.endswith("]"):
            old_index = path[12:-1]
            if old_index in index_map:
                expressions.append((f"Constraints[{index_map[old_index]}]", expression))
        else:
            expressions.append((path, expression))
    return constraints, expressions, counts, receipt


def _diagnostic(sketch, geometry_indices) -> dict[str, object]:
    constraints, expressions, counts, receipt = _axis_state(sketch, geometry_indices)
    if not any(counts.values()):
        raise ValueError("No axes alignment is removable.")
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
        "geometry_count": len(sketch.Geometry),
        "constraint_count": len(constraints),
        "geometry": copy.deepcopy(sketch.Geometry),
        "geometry_metadata": [_metadata(value) for value in sketch.GeometryFacadeList],
        "constraints": constraints,
        "external_reference_count": len(references),
        "external_references": references,
        "external_geometry_count": len(external_geometry),
        "external_geometry": external_geometry,
        "external_geometry_metadata": [
            _external_metadata(value) for value in external_geometry
        ],
        "input_geometry_indices": list(geometry_indices),
        **counts,
        "geometry_tags": [value.Tag for value in sketch.GeometryFacadeList],
        "constraint_tags": [value.Tag for value in constraints],
        "expressions": [],
        "mutation_receipt": receipt,
    }


def _install_axis_host(sketch):
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
        constraints, expressions, _counts, receipt = _axis_state(self, geometry_indices)
        self.Constraints = constraints
        self.ConstraintCount = len(constraints)
        self.ExpressionEngine = expressions
        self.DoF = 4
        return receipt

    sketch.diagnoseRemoveAxesAlignment = MethodType(diagnose, sketch)
    sketch.removeAxesAlignmentExact = MethodType(commit, sketch)
    return calls, commits, state


def _constraint(constraint_type: str, *references, tag: str):
    result = FakeConstraint(constraint_type, *references)
    result.Tag = tag
    return result


def _host(monkeypatch, *, full: bool = True):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    if full:
        first = sketch.addGeometry(FakeLine(), False)
        second = sketch.addGeometry(FakeLine(), False)
        sketch.GeometryFacadeList[first].Tag = "geometry-1"
        sketch.GeometryFacadeList[second].Tag = "geometry-2"
        sketch.Constraints = [
            _constraint("Horizontal", 0, tag="horizontal-0"),
            _constraint("Horizontal", first, tag="horizontal-1"),
            _constraint("Vertical", second, tag="vertical-2"),
            _constraint("Symmetric", 0, 1, first, 2, -1, tag="axis-symmetry"),
            _constraint("PointOnObject", 0, 1, -2, tag="point-on-axis"),
            _constraint("DistanceX", 0, 1, first, 2, 5.0, tag="distance-x"),
            _constraint("Horizontal", 0, 1, first, 1, tag="point-horizontal"),
            _constraint("PointOnObject", 0, 2, first, tag="curve-relation"),
        ]
        sketch.ConstraintCount = len(sketch.Constraints)
        sketch.ExpressionEngine = [("Constraints[5]", "Spreadsheet.Length")]
    calls, commits, state = _install_axis_host(sketch)
    values = _values(
        sketch.ConstraintCount,
        expected_geometry_count=sketch.GeometryCount,
        geometry_indices=list(range(sketch.GeometryCount)) if full else [0],
    )
    return document, sketch, context, values, calls, commits, state


def test_axis_alignment_target_is_closed_internal_bounded_and_exact(
    monkeypatch,
) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_axis_alignment(document.Uid, values)
    assert spec.geometry_indices == (0, 1, 2)

    invalid = (
        ({**values, "unexpected": True}, "incorrect fields"),
        ({**values, "geometry_indices": []}, "unique current internal"),
        ({**values, "geometry_indices": [0, 0]}, "unique current internal"),
        ({**values, "geometry_indices": [-1]}, "unique current internal"),
        ({**values, "geometry_indices": [True]}, "unique current internal"),
        ({**values, "geometry_indices": [3]}, "unique current internal"),
        (
            {
                **values,
                "expected_geometry_count": 300,
                "geometry_indices": list(range(257)),
            },
            "unique current internal",
        ),
    )
    for arguments, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            prepare_axis_alignment(document.Uid, arguments)


def test_axis_alignment_rejects_noop_and_stale_external_state(monkeypatch) -> None:
    document, _sketch, context, values, *_rest = _host(monkeypatch, full=False)
    with pytest.raises(NativeSketchError, match="rejected"):
        preflight_axis_alignment(
            context,
            prepare_axis_alignment(document.Uid, values),
        )

    document, _sketch, context, values, *_rest = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="external state changed"):
        preflight_axis_alignment(
            context,
            prepare_axis_alignment(
                document.Uid,
                {**values, "expected_external_reference_count": 1},
            ),
        )


def test_axis_alignment_diagnosis_is_pure_and_exact_commit_is_verified(
    monkeypatch,
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    before_constraints = copy.deepcopy(sketch.Constraints)
    prepared = preflight_axis_alignment(
        context,
        prepare_axis_alignment(document.Uid, values),
    )
    assert [value.Type for value in sketch.Constraints] == [
        value.Type for value in before_constraints
    ]
    draft = create_axis_alignment(document, prepared)
    result = verify_axis_alignment(document, draft)

    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and len(commits) == 1
    assert [value.Type for value in sketch.Constraints] == [
        "Parallel",
        "Distance",
        "Horizontal",
        "PointOnObject",
    ]
    assert sketch.ExpressionEngine == [("Constraints[1]", "Spreadsheet.Length")]
    assert result == {
        **result,
        "operation": "remove_axis_alignment",
        "input_geometry_count": 3,
        "removed_horizontal_constraints": 2,
        "removed_vertical_constraints": 1,
        "created_parallel_constraints": 1,
        "removed_axis_symmetry_constraints": 1,
        "removed_point_on_axis_constraints": 1,
        "converted_distance_constraints": 1,
        "created_constraint_count": 1,
        "removed_constraint_count": 5,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda sketch: setattr(sketch, "GeometryCount", 4), "count"),
        (lambda sketch: setattr(sketch.Constraints[0], "Type", "Vertical"), "changed"),
        (lambda sketch: sketch.ExpressionEngine.append(("A", "B")), "changed"),
    ),
)
def test_axis_alignment_refuses_stale_state(monkeypatch, change, message: str) -> None:
    document, sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    spec = prepare_axis_alignment(document.Uid, values)
    if message == "count":
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            preflight_axis_alignment(context, spec)
    else:
        prepared = preflight_axis_alignment(context, spec)
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            create_axis_alignment(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"input_geometry_indices": [0]}),
            "different operation",
        ),
        (
            lambda result, _count: result.update({"removed_horizontal_constraints": 0}),
            "wrong constraint rewrite",
        ),
        (
            lambda result, _count: setattr(result["constraints"][0], "Type", "Equal"),
            "wrong constraint rewrite",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
    ),
)
def test_axis_alignment_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message: str
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_axis_alignment(
            context,
            prepare_axis_alignment(document.Uid, values),
        )


def test_axis_alignment_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.Constraints[0].Type = "Vertical"

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_axis_alignment(
            context,
            prepare_axis_alignment(document.Uid, values),
        )

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drifting(result, count):
        if count == 2:
            result["constraints"][0].Second = 2

    state["mutate_diagnostic"] = drifting
    prepared = preflight_axis_alignment(
        context,
        prepare_axis_alignment(document.Uid, values),
    )
    with pytest.raises(NativeSketchError, match="wrong constraint rewrite"):
        create_axis_alignment(document, prepared)
    assert commits == []


def test_axis_alignment_verifier_rejects_semantic_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_axis_alignment(
        context,
        prepare_axis_alignment(document.Uid, values),
    )
    draft = create_axis_alignment(document, prepared)
    sketch.Constraints[0].Second = 2
    with pytest.raises(NativeSketchError, match="final constraints"):
        verify_axis_alignment(document, draft)


def test_geometry_runtime_routes_axis_alignment_through_one_exact_transaction(
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
        {"operation": "remove_axis_alignment", **values},
        ticket=None,
    )

    assert len(calls) == 2 and len(commits) == 1
    assert captured["transaction_name"] == "Remove Sketch Axes Alignment"
    assert result["operation"] == "remove_axis_alignment"
