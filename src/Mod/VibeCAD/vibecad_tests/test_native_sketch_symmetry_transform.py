# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
from types import MethodType, SimpleNamespace

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchSymmetry import (
    create_symmetry,
    preflight_symmetry,
    prepare_symmetry,
    verify_symmetry,
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
        "expected_constraint_count": 0,
        "expected_external_reference_count": 0,
        "expected_external_geometry_count": 0,
        "geometry_indices": [0],
        "reference": {"geometry_index": -1, "position": "whole"},
        "source_mode": "keep",
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


def _receipt(before_geometry, before_constraints, after_geometry, after_constraints):
    return {
        "geometry": _collection_receipt(before_geometry, after_geometry),
        "constraints": _collection_receipt(before_constraints, after_constraints),
    }


def _source_geometry(sketch, index: int):
    if index >= 0:
        return copy.deepcopy(sketch.Geometry[index])
    return copy.deepcopy(sketch.ExternalGeo[-index - 1])


def _reflected_point(point, first, second):
    dx = float(second.x) - float(first.x)
    dy = float(second.y) - float(first.y)
    length_squared = dx * dx + dy * dy
    projection = (
        (float(point.x) - float(first.x)) * dx + (float(point.y) - float(first.y)) * dy
    ) / length_squared
    closest_x = float(first.x) + projection * dx
    closest_y = float(first.y) + projection * dy
    return SimpleNamespace(
        x=2.0 * closest_x - point.x,
        y=2.0 * closest_y - point.y,
        z=0.0,
    )


def _mirror_line(sketch, line, reference_index: int, position: int):
    if position == 0:
        reference = sketch.getGeometry(reference_index)
        line.StartPoint = _reflected_point(
            line.StartPoint, reference.StartPoint, reference.EndPoint
        )
        line.EndPoint = _reflected_point(
            line.EndPoint, reference.StartPoint, reference.EndPoint
        )
        return
    pivot = sketch.getPoint(reference_index, position)
    for name in ("StartPoint", "EndPoint"):
        point = getattr(line, name)
        setattr(
            line,
            name,
            SimpleNamespace(
                x=2.0 * pivot.x - point.x,
                y=2.0 * pivot.y - point.y,
                z=0.0,
            ),
        )


def _symmetry_state(sketch, args):
    geometry_indices, reference_index, position, source_value = args
    assert position in {0, 1, 2, 3}
    assert source_value in {0, 1, 2}
    before_geometry_tags = [facade.Tag for facade in sketch.GeometryFacadeList]
    before_constraint_tags = [constraint.Tag for constraint in sketch.Constraints]
    delete_sources = source_value == 1
    selected_internal = {index for index in geometry_indices if index >= 0}
    geometry = [
        copy.deepcopy(item)
        for index, item in enumerate(sketch.Geometry)
        if not delete_sources or index not in selected_internal
    ]
    facades = [
        copy.deepcopy(item)
        for index, item in enumerate(sketch.GeometryFacadeList)
        if not delete_sources or index not in selected_internal
    ]
    constraints = [] if delete_sources else copy.deepcopy(sketch.Constraints)
    expressions = [] if delete_sources else list(sketch.ExpressionEngine)
    for source_offset, source_index in enumerate(geometry_indices):
        result = _source_geometry(sketch, source_index)
        if not isinstance(result, (FakeLine, FakeExternalLine)):
            raise AssertionError("The focused Symmetry fake only supports lines.")
        _mirror_line(sketch, result, reference_index, position)
        result.Tag = f"symmetry-geometry-{source_offset}"
        geometry.append(result)
        facade = fake_facade(result, 600 + len(facades))
        facade.Tag = result.Tag
        facades.append(facade)
    if source_value == 2:
        for position_code in (1, 2):
            constraint = FakeConstraint(
                "Symmetric",
                geometry_indices[0],
                position_code,
                len(geometry) - 1,
                position_code,
                reference_index,
                0,
            )
            constraint.Tag = f"symmetry-constraint-{position_code}"
            constraints.append(constraint)
    receipt = _receipt(
        before_geometry_tags,
        before_constraint_tags,
        [facade.Tag for facade in facades],
        [constraint.Tag for constraint in constraints],
    )
    return geometry, facades, constraints, expressions, receipt


def _diagnostic(sketch, args) -> dict[str, object]:
    geometry_indices, reference_index, position, source_value = args
    geometry, facades, constraints, expressions, receipt = _symmetry_state(sketch, args)
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
            _external_metadata(item) for item in external_geometry
        ],
        "input_geometry_indices": list(geometry_indices),
        "reference_geometry_index": reference_index,
        "reference_position": ("whole", "start", "end", "center")[position],
        "source_mode": ("keep", "delete", "constrain")[source_value],
        "deleted_originals": source_value == 1,
        "constrained_symmetry": source_value == 2,
        "geometry_tags": [facade.Tag for facade in facades],
        "constraint_tags": [constraint.Tag for constraint in constraints],
        "expressions": expressions,
        "mutation_receipt": receipt,
    }


def _install_symmetry_host(sketch):
    calls = []
    commits = []
    state = {"mutate_diagnostic": None}

    def diagnose(self, *args):
        calls.append(args)
        result = _diagnostic(self, args)
        mutation = state["mutate_diagnostic"]
        if mutation is not None:
            mutation(result, len(calls))
        return result

    def commit(self, *args):
        commits.append(args)
        geometry, facades, constraints, expressions, receipt = _symmetry_state(
            self, args
        )
        self.Geometry = geometry
        self.GeometryFacadeList = facades
        self.Constraints = constraints
        self.ExpressionEngine = expressions
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.DoF = 4
        return receipt

    sketch.diagnoseSymmetry = MethodType(diagnose, sketch)
    sketch.symmetryExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0].StartPoint.x = 2.0
    sketch.Geometry[0].StartPoint.y = 1.0
    sketch.Geometry[0].EndPoint.x = 5.0
    sketch.Geometry[0].EndPoint.y = 3.0
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    sketch.GeometryFacadeList[0].Id = 413
    calls, commits, state = _install_symmetry_host(sketch)
    return document, sketch, context, _values(), calls, commits, state


def test_symmetry_target_is_closed_bounded_and_explicit(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_symmetry(document.Uid, values)
    assert spec.geometry_indices == (0,)
    assert spec.reference.geometry_index == -1
    assert spec.reference.position == "whole"
    assert spec.reference.position_code == 0
    assert spec.source_mode == "keep" and spec.source_value == 0

    invalid = (
        ({**values, "unexpected": True}, "incorrect fields"),
        ({**values, "geometry_indices": [0, 0]}, "unique"),
        (
            {**values, "reference": {"geometry_index": -2000, "position": "whole"}},
            "range",
        ),
        (
            {**values, "reference": {"geometry_index": True, "position": "whole"}},
            "range",
        ),
        (
            {**values, "reference": {"geometry_index": -1, "position": "edge"}},
            "position",
        ),
        ({**values, "source_mode": "replace"}, "source mode"),
    )
    for arguments, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            prepare_symmetry(document.Uid, arguments)


def test_symmetry_preflight_validates_exact_reference_semantics(monkeypatch) -> None:
    document, _sketch, context, values, *_rest = _host(monkeypatch)
    valid = (
        {"geometry_index": -1, "position": "whole"},
        {"geometry_index": -1, "position": "start"},
        {"geometry_index": -2, "position": "whole"},
        {"geometry_index": 0, "position": "whole"},
        {"geometry_index": 0, "position": "start"},
        {"geometry_index": 0, "position": "end"},
    )
    for reference in valid:
        preflight_symmetry(
            context,
            prepare_symmetry(document.Uid, {**values, "reference": reference}),
        )
    invalid = (
        ({"geometry_index": -1, "position": "center"}, "index -1"),
        ({"geometry_index": -2, "position": "start"}, "vertical axis"),
        ({"geometry_index": 1, "position": "whole"}, "stale"),
        ({"geometry_index": -3, "position": "whole"}, "stale"),
    )
    for reference, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            preflight_symmetry(
                context,
                prepare_symmetry(document.Uid, {**values, "reference": reference}),
            )


@pytest.mark.parametrize(
    ("source_mode", "created", "deleted", "constraints"),
    (
        ("keep", [1], [], []),
        ("delete", [0], [0], []),
        ("constrain", [1], [], [0, 1]),
    ),
)
def test_symmetry_diagnosis_is_pure_and_modes_commit_once(
    monkeypatch, source_mode, created, deleted, constraints
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    arguments = {**values, "source_mode": source_mode}
    before = copy.deepcopy(sketch.Geometry)
    prepared = preflight_symmetry(context, prepare_symmetry(document.Uid, arguments))
    assert sketch.Geometry[0].StartPoint.y == before[0].StartPoint.y
    draft = create_symmetry(document, prepared)
    result = verify_symmetry(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and len(commits) == 1
    assert result["reference"] == {"geometry_index": -1, "position": "whole"}
    assert result["source_mode"] == source_mode
    assert result["created_geometry_indices"] == created
    assert result["deleted_geometry_indices"] == deleted
    assert result["created_constraint_indices"] == constraints


def test_symmetry_supports_point_and_external_references(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, _state = _host(monkeypatch)
    point_result = verify_symmetry(
        document,
        create_symmetry(
            document,
            preflight_symmetry(
                context,
                prepare_symmetry(
                    document.Uid,
                    {
                        **values,
                        "reference": {"geometry_index": -1, "position": "start"},
                    },
                ),
            ),
        ),
    )
    assert point_result["reference"]["position"] == "start"
    assert math.isclose(sketch.Geometry[1].StartPoint.x, -2.0)

    document, sketch, context, values, _calls, _commits, _state = _host(monkeypatch)
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
    external = FakeExternalLine("Source.Edge1")
    external.StartPoint.x = 10.0
    external.EndPoint.x = 10.0
    external.EndPoint.y = 5.0
    sketch.ExternalGeo.append(external)
    arguments = {
        **values,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 1,
        "reference": {"geometry_index": -3, "position": "whole"},
    }
    result = verify_symmetry(
        document,
        create_symmetry(
            document,
            preflight_symmetry(context, prepare_symmetry(document.Uid, arguments)),
        ),
    )
    assert result["reference"]["geometry_index"] == -3
    assert len(sketch.ExternalGeo) == 3


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda sketch: setattr(sketch, "GeometryCount", 2), "count"),
        (lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 9.0), "changed"),
        (lambda sketch: sketch.ExpressionEngine.append(("A", "B")), "changed"),
    ),
)
def test_symmetry_refuses_stale_state(monkeypatch, change, message: str) -> None:
    document, sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    spec = prepare_symmetry(document.Uid, values)
    if message == "count":
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            preflight_symmetry(context, spec)
    else:
        prepared = preflight_symmetry(context, spec)
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            create_symmetry(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"reference_geometry_index": -2}),
            "different operation",
        ),
        (
            lambda result, _count: result.update({"source_mode": "delete"}),
            "different operation",
        ),
        (lambda result, _count: result.update({"accepted": False}), "solver issue"),
        (
            lambda result, _count: result["mutation_receipt"]["geometry"].update(
                {"created": []}
            ),
            "account for every",
        ),
    ),
)
def test_symmetry_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message: str
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_symmetry(context, prepare_symmetry(document.Uid, values))


def test_symmetry_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.Geometry[0].EndPoint.x = 8.0

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_symmetry(context, prepare_symmetry(document.Uid, values))

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drifting(result, count):
        if count == 2:
            result["geometry"][1].EndPoint.x = 99.0

    state["mutate_diagnostic"] = drifting
    prepared = preflight_symmetry(context, prepare_symmetry(document.Uid, values))
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_symmetry(document, prepared)
    assert commits == []


def test_symmetry_verifier_rejects_semantic_constraint_drift(monkeypatch) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    arguments = {**values, "source_mode": "constrain"}
    prepared = preflight_symmetry(context, prepare_symmetry(document.Uid, arguments))
    draft = create_symmetry(document, prepared)
    sketch.Constraints[-1].FirstPos = 3
    with pytest.raises(NativeSketchError, match="final constraints"):
        verify_symmetry(document, draft)


def test_geometry_runtime_routes_symmetry_through_one_exact_transaction(
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
        {"operation": "symmetry", **values},
        ticket=None,
    )

    assert len(calls) == 2 and len(commits) == 1
    assert captured["transaction_name"] == "Mirror Native Sketch Geometry"
    assert result["operation"] == "symmetry"
