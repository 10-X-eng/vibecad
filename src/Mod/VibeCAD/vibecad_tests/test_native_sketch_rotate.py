# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
from types import MethodType

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchRotate import (
    create_rotate,
    preflight_rotate,
    prepare_rotate,
    verify_rotate,
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
        "center_mm": {"x": 0.0, "y": 0.0},
        "total_angle": {"value": 90.0, "unit": "deg"},
        "copy_count": 1,
        "constraint_mode": "copy",
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


def _rotate_line(geometry, center, angle: float):
    result = copy.deepcopy(geometry)
    if not isinstance(result, (FakeLine, FakeExternalLine)):
        raise AssertionError("The focused Rotate fake only supports lines.")
    cosine = math.cos(angle)
    sine = math.sin(angle)
    for point in (result.StartPoint, result.EndPoint):
        x = point.x - center.x
        y = point.y - center.y
        point.x = center.x + x * cosine - y * sine
        point.y = center.y + x * sine + y * cosine
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


def _rotated_state(
    sketch,
    geometry_indices,
    center,
    total_angle,
    copy_count,
    equalize,
    *,
    tag_prefix: str,
):
    before_geometry_tags = [facade.Tag for facade in sketch.GeometryFacadeList]
    before_constraint_tags = [constraint.Tag for constraint in sketch.Constraints]
    source_geometry = []
    source_facades = []
    for geometry_index in geometry_indices:
        source_geometry.append(copy.deepcopy(sketch.getGeometry(geometry_index)))
        source_facades.append(
            copy.deepcopy(sketch.GeometryFacadeList[geometry_index])
            if geometry_index >= 0
            else fake_facade(source_geometry[-1], 0)
        )

    copies_to_make = 1 if copy_count == 0 else copy_count
    created_geometry = []
    created_source_indices = []
    for copy_index in range(1, copies_to_make + 1):
        angle = total_angle * copy_index / copies_to_make
        for source_offset, geometry in enumerate(source_geometry):
            created_geometry.append(_rotate_line(geometry, center, angle))
            created_source_indices.append(source_offset)

    moving = copy_count == 0
    selected_internal = {index for index in geometry_indices if index >= 0}
    final_geometry = [
        copy.deepcopy(geometry)
        for index, geometry in enumerate(sketch.Geometry)
        if not moving or index not in selected_internal
    ]
    final_facades = [
        copy.deepcopy(facade)
        for index, facade in enumerate(sketch.GeometryFacadeList)
        if not moving or index not in selected_internal
    ]
    created_geometry_indices = []
    for created_offset, geometry in enumerate(created_geometry):
        final_index = len(final_geometry)
        source_facade = source_facades[created_source_indices[created_offset]]
        tag = f"{tag_prefix}-geometry-{created_offset}"
        geometry.Tag = tag
        facade = fake_facade(
            geometry,
            200 + final_index,
            construction=bool(source_facade.Construction),
            internal_type=str(source_facade.InternalType),
        )
        facade.Tag = tag
        final_geometry.append(geometry)
        final_facades.append(facade)
        created_geometry_indices.append(final_index)

    final_constraints = [] if moving else copy.deepcopy(sketch.Constraints)
    final_expressions = [] if moving else list(sketch.ExpressionEngine)
    created_constraint_indices = []
    for constraint in sketch.Constraints:
        references = {constraint.First, constraint.Second, constraint.Third}
        if not references.intersection(geometry_indices):
            if moving:
                final_constraints.append(copy.deepcopy(constraint))
            continue
        for created_offset, final_geometry_index in enumerate(created_geometry_indices):
            source_offset = created_source_indices[created_offset]
            source_index = geometry_indices[source_offset]
            rotated = copy.deepcopy(constraint)
            if (
                equalize
                and not moving
                and constraint.Type
                in {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter"}
            ):
                rotated.Type = "Equal"
                rotated.First = source_index
                rotated.FirstPos = 0
                rotated.Second = final_geometry_index
                rotated.SecondPos = 0
                rotated.Third = -2000
                rotated.ThirdPos = 0
                rotated.Elements = ((source_index, 0), (final_geometry_index, 0))
            else:
                for field in ("First", "Second", "Third"):
                    if getattr(rotated, field) == source_index:
                        setattr(rotated, field, final_geometry_index)
                rotated.Elements = tuple(
                    (geometry, position)
                    for geometry, position in (
                        (rotated.First, rotated.FirstPos),
                        (rotated.Second, rotated.SecondPos),
                        (rotated.Third, rotated.ThirdPos),
                    )
                    if geometry > -2000
                )
            rotated.Tag = f"{tag_prefix}-constraint-{len(created_constraint_indices)}"
            new_constraint_index = len(final_constraints)
            final_constraints.append(rotated)
            created_constraint_indices.append(new_constraint_index)
            if not equalize and sketch.ExpressionEngine:
                final_expressions.append(
                    (
                        f"Constraints[{new_constraint_index}]",
                        sketch.ExpressionEngine[0][1],
                    )
                )

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
        created_constraint_indices,
    )


def _diagnostic(sketch, args, *, tag_prefix: str) -> dict[str, object]:
    geometry_indices, center, total_angle, copy_count, equalize = args
    geometry, facades, constraints, expressions, receipt, created_constraints = (
        _rotated_state(
            sketch,
            geometry_indices,
            center,
            total_angle,
            copy_count,
            equalize,
            tag_prefix=tag_prefix,
        )
    )
    references = _external_references(sketch)
    external_geometry = copy.deepcopy(sketch.ExternalGeo[2:])
    original_constraint_count = int(sketch.ConstraintCount)
    created_expressions = [
        {
            "constraint_index": index,
            "path": f"Constraints[{index}]",
            "expression": expression,
        }
        for path, expression in expressions
        for index in [int(path.removeprefix("Constraints[").removesuffix("]"))]
        if index in created_constraints or copy_count == 0
        if index >= (0 if copy_count == 0 else original_constraint_count)
    ]
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
        "total_angle_radians": float(total_angle),
        "copy_count": copy_count,
        "equalize_dimensional_constraints": equalize,
        "deleted_originals": copy_count == 0,
        "geometry_tags": [facade.Tag for facade in facades],
        "constraint_tags": [constraint.Tag for constraint in constraints],
        "expressions": created_expressions,
        "mutation_receipt": receipt,
    }


def _install_rotate_host(sketch):
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
        geometry, facades, constraints, expressions, receipt, _created = _rotated_state(
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

    sketch.diagnoseRotate = MethodType(diagnose, sketch)
    sketch.rotateExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch, *, constraints: bool = True):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0].StartPoint.x = 2.0
    sketch.Geometry[0].EndPoint.x = 5.0
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    if constraints:
        constraint = FakeConstraint("Distance", 0, 1, 0, 2, 3.0)
        constraint.Tag = "constraint-0"
        sketch.Constraints = [constraint]
        sketch.ConstraintCount = 1
        sketch.ExpressionEngine = [("Constraints[0]", "Spreadsheet.Width")]
    calls, commits, state = _install_rotate_host(sketch)
    values = _values(expected_constraint_count=1 if constraints else 0)
    return document, sketch, context, values, calls, commits, state


def test_rotate_target_is_closed_bounded_and_uses_explicit_degrees(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_rotate(document.Uid, values)
    assert spec.geometry_indices == (0,)
    assert spec.total_angle_degrees == 90.0
    assert spec.total_angle_radians == math.pi / 2.0

    invalid = (
        ({**values, "unexpected": True}, "incorrect fields"),
        ({**values, "geometry_indices": [0, 0]}, "unique"),
        ({**values, "center_mm": {"x": float("inf"), "y": 0.0}}, "finite"),
        ({**values, "total_angle": {"value": 0.0, "unit": "deg"}}, "nonzero"),
        ({**values, "total_angle": {"value": 90.0, "unit": "rad"}}, "unit deg"),
        (
            {**values, "copy_count": 0, "constraint_mode": "equalize_dimensions"},
            "move mode",
        ),
        (
            {**values, "geometry_indices": list(range(65)), "copy_count": 64},
            "too many geometry",
        ),
    )
    for arguments, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            prepare_rotate(document.Uid, arguments)


def test_rotate_preflight_rejects_axes_group_members_and_incomplete_internal_geometry(
    monkeypatch,
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch, constraints=False)
    for geometry_index in (-1, -2, -3):
        with pytest.raises(NativeSketchError, match="axes|external geometry index"):
            preflight_rotate(
                context,
                prepare_rotate(
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
        preflight_rotate(
            context,
            prepare_rotate(
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
        preflight_rotate(
            context,
            prepare_rotate(
                document.Uid,
                {
                    **values,
                    "expected_geometry_count": 2,
                    "expected_constraint_count": 1,
                    "geometry_indices": [1],
                },
            ),
        )


def test_rotate_diagnosis_is_pure_and_move_executes_once_with_expression(
    monkeypatch,
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    values = {**values, "copy_count": 0, "total_angle": {"value": 90.0, "unit": "deg"}}
    before = copy.deepcopy(sketch.Geometry)
    prepared = preflight_rotate(context, prepare_rotate(document.Uid, values))
    assert sketch.Geometry[0].StartPoint.x == before[0].StartPoint.x
    assert len(calls) == 1

    draft = create_rotate(document, prepared)
    result = verify_rotate(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2
    assert len(commits) == 1
    assert abs(sketch.Geometry[0].StartPoint.x) < 1.0e-9
    assert sketch.Geometry[0].StartPoint.y == 2.0
    assert sketch.ExpressionEngine == [("Constraints[0]", "Spreadsheet.Width")]
    assert result["mode"] == "move"
    assert result["total_angle"] == {"value": 90.0, "unit": "deg"}
    assert result["created_geometry_indices"] == [0]
    assert result["deleted_geometry_indices"] == [0]
    assert result["created_constraint_indices"] == [0]
    assert result["deleted_constraint_indices"] == [0]


def test_rotate_supports_polar_array_equalize_and_external_geometry(
    monkeypatch,
) -> None:
    document, sketch, context, values, _calls, _commits, _state = _host(monkeypatch)
    array_values = {
        **values,
        "total_angle": {"value": 180.0, "unit": "deg"},
        "copy_count": 2,
        "constraint_mode": "equalize_dimensions",
    }
    result = verify_rotate(
        document,
        create_rotate(
            document,
            preflight_rotate(context, prepare_rotate(document.Uid, array_values)),
        ),
    )
    assert result["mode"] == "polar_array"
    assert result["created_geometry_count"] == 2
    assert [
        (round(geometry.StartPoint.x, 9), round(geometry.StartPoint.y, 9))
        for geometry in sketch.Geometry
    ] == [(2.0, 0.0), (0.0, 2.0), (-2.0, 0.0)]
    assert [constraint.Type for constraint in sketch.Constraints] == [
        "Distance",
        "Equal",
        "Equal",
    ]
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
    }
    result = verify_rotate(
        document,
        create_rotate(
            document,
            preflight_rotate(context, prepare_rotate(document.Uid, external_values)),
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
def test_rotate_refuses_stale_state(monkeypatch, change, message: str) -> None:
    document, sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    spec = prepare_rotate(document.Uid, values)
    if message == "count":
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            preflight_rotate(context, spec)
    else:
        prepared = preflight_rotate(context, spec)
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            create_rotate(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"input_geometry_indices": [4]}),
            "different operation",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
        (
            lambda result, _count: result["geometry_tags"].__setitem__(1, "wrong"),
            "wrong created geometry",
        ),
        (
            lambda result, _count: result["mutation_receipt"]["constraints"].update(
                {"created": []}
            ),
            "account for every",
        ),
        (
            lambda result, _count: result["constraint_tags"].__setitem__(1, 5),
            "invalid constraint_tags",
        ),
    ),
)
def test_rotate_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message: str
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_rotate(context, prepare_rotate(document.Uid, values))


def test_rotate_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def impure(result, count):
        if count == 1:
            sketch.Geometry[0].EndPoint.x = 8.0

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_rotate(context, prepare_rotate(document.Uid, values))

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drifting(result, count):
        if count == 2:
            result["geometry"][1].EndPoint.x = 99.0

    state["mutate_diagnostic"] = drifting
    prepared = preflight_rotate(context, prepare_rotate(document.Uid, values))
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_rotate(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (
            lambda sketch, draft: setattr(sketch.Geometry[1].EndPoint, "x", 99.0),
            "geometry",
        ),
        (
            lambda sketch, draft: setattr(sketch.Constraints[1], "Type", "Vertical"),
            "constraints",
        ),
        (
            lambda sketch, draft: sketch.ExpressionEngine.__setitem__(
                1, ("Constraints[1]", "Spreadsheet.Wrong")
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
def test_rotate_verifier_rejects_every_final_state_drift(
    monkeypatch, change, message: str
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    prepared = preflight_rotate(context, prepare_rotate(document.Uid, values))
    draft = create_rotate(document, prepared)
    change(sketch, draft)
    with pytest.raises(NativeSketchError, match=message):
        verify_rotate(document, draft)


def test_geometry_runtime_routes_rotate_through_one_exact_transaction(
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
        {"operation": "rotate", **values},
        ticket=None,
    )

    assert len(calls) == 2
    assert len(commits) == 1
    assert captured["transaction_name"] == "Rotate Native Sketch Geometry"
    assert result["operation"] == "rotate"
