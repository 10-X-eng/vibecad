# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import MethodType

import pytest

import VibeCADNativeSketchGeometryRuntime as runtime_module
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchGeometryRuntime import NativeSketchGeometryRuntime
from VibeCADNativeSketchOffset import (
    create_offset,
    preflight_offset,
    prepare_offset,
    verify_offset,
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
        "offset_distance": {"value": 2.5, "unit": "mm"},
        "join_type": "arc",
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


def _offset_state(sketch, args):
    geometry_indices, distance, join_value, source_value = args
    assert join_value in {0, 2}
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
            raise AssertionError("The focused Offset fake only supports lines.")
        result.StartPoint.y += distance
        result.EndPoint.y += distance
        result.Tag = f"offset-geometry-{source_offset}"
        geometry.append(result)
        facade = fake_facade(result, 500 + len(facades))
        facade.Tag = result.Tag
        facades.append(facade)
    if source_value == 2:
        constraint = FakeConstraint("Distance", 0, 1, 1, 1, abs(distance))
        constraint.Tag = "offset-constraint-0"
        constraints.append(constraint)
    receipt = _receipt(
        before_geometry_tags,
        before_constraint_tags,
        [facade.Tag for facade in facades],
        [constraint.Tag for constraint in constraints],
    )
    return geometry, facades, constraints, expressions, receipt


def _diagnostic(sketch, args) -> dict[str, object]:
    geometry_indices, distance, join_value, source_value = args
    geometry, facades, constraints, expressions, receipt = _offset_state(sketch, args)
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
        "offset_length_mm": float(distance),
        "join_type": "arc" if join_value == 0 else "intersection",
        "source_mode": ("keep", "delete", "constrain")[source_value],
        "deleted_originals": source_value == 1,
        "constrained_offset": source_value == 2,
        "geometry_tags": [facade.Tag for facade in facades],
        "constraint_tags": [constraint.Tag for constraint in constraints],
        "expressions": expressions,
        "mutation_receipt": receipt,
    }


def _install_offset_host(sketch):
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
        geometry, facades, constraints, expressions, receipt = _offset_state(self, args)
        self.Geometry = geometry
        self.GeometryFacadeList = facades
        self.Constraints = constraints
        self.ExpressionEngine = expressions
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        self.DoF = 4
        return receipt

    sketch.diagnoseOffset = MethodType(diagnose, sketch)
    sketch.offsetExact = MethodType(commit, sketch)
    return calls, commits, state


def _host(monkeypatch):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0].StartPoint.x = 2.0
    sketch.Geometry[0].StartPoint.y = 1.0
    sketch.Geometry[0].EndPoint.x = 5.0
    sketch.Geometry[0].EndPoint.y = 1.0
    sketch.GeometryFacadeList[0].Tag = "geometry-0"
    sketch.GeometryFacadeList[0].Id = 313
    calls, commits, state = _install_offset_host(sketch)
    return document, sketch, context, _values(), calls, commits, state


def test_offset_target_is_closed_bounded_signed_and_explicit(monkeypatch) -> None:
    document, _sketch, _context, values, *_rest = _host(monkeypatch)
    spec = prepare_offset(document.Uid, values)
    assert spec.geometry_indices == (0,)
    assert spec.offset_length_mm == 2.5
    assert spec.join_type == "arc" and spec.join_value == 0
    assert spec.source_mode == "keep" and spec.source_value == 0

    invalid = (
        ({**values, "unexpected": True}, "incorrect fields"),
        ({**values, "geometry_indices": [0, 0]}, "unique"),
        ({**values, "offset_distance": {"value": 0.0, "unit": "mm"}}, "magnitude"),
        ({**values, "offset_distance": {"value": True, "unit": "mm"}}, "number"),
        ({**values, "offset_distance": {"value": 2.0, "unit": "in"}}, "unit"),
        ({**values, "join_type": "automatic"}, "join type"),
        ({**values, "source_mode": "replace"}, "source mode"),
    )
    for arguments, message in invalid:
        with pytest.raises(NativeSketchError, match=message):
            prepare_offset(document.Uid, arguments)


def test_offset_preflight_rejects_axes_and_stale_external_counts(monkeypatch) -> None:
    document, _sketch, context, values, *_rest = _host(monkeypatch)
    for geometry_index in (-1, -2, -3):
        with pytest.raises(NativeSketchError, match="axes|external geometry index"):
            preflight_offset(
                context,
                prepare_offset(
                    document.Uid,
                    {**values, "geometry_indices": [geometry_index]},
                ),
            )
    with pytest.raises(NativeSketchError, match="external state changed"):
        preflight_offset(
            context,
            prepare_offset(
                document.Uid,
                {**values, "expected_external_reference_count": 1},
            ),
        )


@pytest.mark.parametrize(
    ("join_type", "source_mode", "distance", "created", "deleted", "constraints"),
    (
        ("arc", "keep", 2.5, [1], [], []),
        ("intersection", "delete", -2.5, [0], [0], []),
        ("arc", "constrain", 3.0, [1], [], [0]),
    ),
)
def test_offset_diagnosis_is_pure_and_modes_commit_once(
    monkeypatch, join_type, source_mode, distance, created, deleted, constraints
) -> None:
    document, sketch, context, values, calls, commits, _state = _host(monkeypatch)
    arguments = {
        **values,
        "offset_distance": {"value": distance, "unit": "mm"},
        "join_type": join_type,
        "source_mode": source_mode,
    }
    before = copy.deepcopy(sketch.Geometry)
    prepared = preflight_offset(context, prepare_offset(document.Uid, arguments))
    assert sketch.Geometry[0].StartPoint.y == before[0].StartPoint.y
    draft = create_offset(document, prepared)
    result = verify_offset(document, draft)
    assert isinstance(draft, NativeMutationDraft)
    assert len(calls) == 2 and len(commits) == 1
    assert result["join_type"] == join_type
    assert result["source_mode"] == source_mode
    assert result["offset_distance"] == {"value": float(distance), "unit": "mm"}
    assert result["created_geometry_indices"] == created
    assert result["deleted_geometry_indices"] == deleted
    assert result["created_constraint_indices"] == constraints


def test_offset_supports_exact_external_geometry(monkeypatch) -> None:
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
    external.StartPoint.y = 4.0
    external.EndPoint.y = 4.0
    sketch.ExternalGeo.append(external)
    arguments = {
        **values,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 1,
        "geometry_indices": [-3],
        "source_mode": "keep",
    }
    result = verify_offset(
        document,
        create_offset(
            document,
            preflight_offset(context, prepare_offset(document.Uid, arguments)),
        ),
    )
    assert result["created_geometry_indices"] == [1]
    assert len(sketch.ExternalGeo) == 3


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda sketch: setattr(sketch, "GeometryCount", 2), "count"),
        (lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 9.0), "changed"),
        (lambda sketch: sketch.ExpressionEngine.append(("A", "B")), "changed"),
    ),
)
def test_offset_refuses_stale_state(monkeypatch, change, message: str) -> None:
    document, sketch, context, values, _calls, commits, _state = _host(monkeypatch)
    spec = prepare_offset(document.Uid, values)
    if message == "count":
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            preflight_offset(context, spec)
    else:
        prepared = preflight_offset(context, spec)
        change(sketch)
        with pytest.raises(NativeSketchError, match=message):
            create_offset(document, prepared)
    assert commits == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda result, _count: result.update({"unexpected": True}), "incomplete"),
        (
            lambda result, _count: result.update({"offset_length_mm": 9.0}),
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
def test_offset_rejects_untrusted_host_diagnostics(
    monkeypatch, mutate, message: str
) -> None:
    document, _sketch, context, values, _calls, _commits, state = _host(monkeypatch)
    state["mutate_diagnostic"] = mutate
    with pytest.raises(NativeSketchError, match=message):
        preflight_offset(context, prepare_offset(document.Uid, values))


def test_offset_rejects_impure_and_drifting_diagnosis(monkeypatch) -> None:
    document, sketch, context, values, _calls, _commits, state = _host(monkeypatch)

    def impure(_result, count):
        if count == 1:
            sketch.Geometry[0].EndPoint.x = 8.0

    state["mutate_diagnostic"] = impure
    with pytest.raises(NativeSketchError, match="changed the live Sketch"):
        preflight_offset(context, prepare_offset(document.Uid, values))

    document, _sketch, context, values, _calls, commits, state = _host(monkeypatch)

    def drifting(result, count):
        if count == 2:
            result["geometry"][1].EndPoint.x = 99.0

    state["mutate_diagnostic"] = drifting
    prepared = preflight_offset(context, prepare_offset(document.Uid, values))
    with pytest.raises(NativeSketchError, match="result changed after preflight"):
        create_offset(document, prepared)
    assert commits == []


def test_offset_verifier_ignores_only_new_dimension_label_placement(
    monkeypatch,
) -> None:
    document, sketch, context, values, *_rest = _host(monkeypatch)
    arguments = {**values, "source_mode": "constrain"}
    prepared = preflight_offset(context, prepare_offset(document.Uid, arguments))
    draft = create_offset(document, prepared)
    sketch.Constraints[-1].LabelDistance = 42.0
    assert verify_offset(document, draft)["created_constraint_indices"] == [0]

    document, sketch, context, values, *_rest = _host(monkeypatch)
    arguments = {**values, "source_mode": "constrain"}
    prepared = preflight_offset(context, prepare_offset(document.Uid, arguments))
    draft = create_offset(document, prepared)
    sketch.Constraints[-1].Value = 9.0
    with pytest.raises(NativeSketchError, match="final constraints"):
        verify_offset(document, draft)


def test_geometry_runtime_routes_offset_through_one_exact_transaction(
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
        {"operation": "offset", **values},
        ticket=None,
    )

    assert len(calls) == 2 and len(commits) == 1
    assert captured["transaction_name"] == "Offset Native Sketch Geometry"
    assert result["operation"] == "offset"
