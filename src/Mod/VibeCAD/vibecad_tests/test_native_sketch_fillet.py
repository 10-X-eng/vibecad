# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
from types import MethodType, SimpleNamespace

import pytest

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchFillet import (
    create_sketch_fillet,
    preflight_sketch_fillet,
    prepare_sketch_fillet,
    verify_sketch_fillet,
)
from VibeCADNativeSketchFilletDiagnostic import parse_sketch_fillet_diagnostic
from VibeCADNativeSketchState import iter_sketch_geometry_records
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


def _values(*, form: str = "corner", preserve_corner: bool = True, **updates):
    target = (
        {"form": "corner", "geometry_index": 0, "position": "end"}
        if form == "corner"
        else {
            "form": "curve_pair",
            "curves": [
                {
                    "geometry_index": 0,
                    "reference_point_mm": {"x": 18.0, "y": 0.0},
                },
                {
                    "geometry_index": 1,
                    "reference_point_mm": {"x": 20.0, "y": 2.0},
                },
            ],
        }
    )
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_constraint_count": 1,
            "expected_external_geometry_count": 0,
            "target": target,
            "preserve_corner": preserve_corner,
            **updates,
        }
    )


def _host(monkeypatch, *, construction: bool = False):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakeLine(_point(0.0, 0.0), _point(20.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    sketch.GeometryFacadeList[0].Construction = construction
    sketch.enablePersistentGeometryTags()
    assert (
        sketch.addGeometry(
            FakeLine(_point(20.0, 0.0), _point(20.0, 15.0)),
            construction,
        )
        == 1
    )
    assert sketch.addConstraint(FakeConstraint("Coincident", 0, 2, 1, 1)) == 0
    return document, sketch, context


def _metadata(facade) -> dict[str, object]:
    return {
        "Id": int(facade.Id),
        "Construction": bool(facade.Construction),
        "Blocked": bool(facade.Blocked),
        "InternalType": str(facade.InternalType or "None"),
        "GeometryLayerId": int(facade.GeometryLayerId),
    }


def _tagged_clone(geometry, tag: str):
    result = copy.deepcopy(geometry)
    result.Tag = tag
    return result


def _final_state(sketch, *, construction: bool, preserve_corner: bool):
    first = _tagged_clone(sketch.Geometry[0], sketch.GeometryFacadeList[0].Tag)
    second = _tagged_clone(sketch.Geometry[1], sketch.GeometryFacadeList[1].Tag)
    first.EndPoint = _point(18.0, 0.0)
    second.StartPoint = _point(20.0, 2.0)
    arc = FakeArc(
        FakeCircle(_point(18.0, 2.0), _point(0.0, 0.0), 2.0), -math.pi / 2, 0.0
    )
    arc.Tag = "fillet-arc-tag"
    geometry = [first, second, arc]
    metadata = [
        _metadata(sketch.GeometryFacadeList[0]),
        _metadata(sketch.GeometryFacadeList[1]),
        {
            "Id": 102,
            "Construction": construction,
            "Blocked": False,
            "InternalType": "None",
            "GeometryLayerId": 0,
        },
    ]
    if preserve_corner:
        corner = FakePoint(_point(20.0, 0.0))
        corner.Tag = "fillet-corner-tag"
        geometry.append(corner)
        metadata.append(
            {
                "Id": 103,
                "Construction": True,
                "Blocked": False,
                "InternalType": "None",
                "GeometryLayerId": 0,
            }
        )
    constraints = [
        FakeConstraint("PointOnObject", 2, 1, 0),
        FakeConstraint("PointOnObject", 2, 2, 1),
        FakeConstraint("Tangent", 0, 2),
        FakeConstraint("Tangent", 1, 2),
    ]
    return geometry, metadata, constraints


def _diagnostic(
    sketch,
    *,
    form: str = "corner",
    construction: bool = False,
    preserve_corner: bool = True,
):
    geometry, metadata, constraints = _final_state(
        sketch,
        construction=construction,
        preserve_corner=preserve_corner,
    )
    return {
        "accepted": True,
        "degrees_of_freedom": 3,
        "solver_status": 0,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
        "form": form,
        "input_geometry_indices": [0, 1],
        "fillet_geometry_index": 2,
        "corner_geometry_index": 3 if preserve_corner else None,
        "radius_mm": 2.0,
        "trimmed": True,
        "construction": construction,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
    }


def _install_diagnostic(sketch, *, form="corner", construction=False, preserve=True):
    calls = []

    def diagnose(self, *arguments):
        calls.append(arguments)
        return _diagnostic(
            self,
            form=form,
            construction=construction,
            preserve_corner=preserve,
        )

    sketch.diagnoseFillet = MethodType(diagnose, sketch)
    return calls


def _apply_fake_fillet(sketch, *, construction: bool, preserve: bool):
    calls = []

    def fillet(self, *arguments):
        calls.append(arguments)
        geometry, metadata, constraints = _final_state(
            self,
            construction=False,
            preserve_corner=preserve,
        )
        self.Geometry = geometry
        self.GeometryFacadeList = []
        for index, (item, details) in enumerate(zip(geometry, metadata, strict=True)):
            facade = fake_facade(
                item,
                index,
                construction=bool(details["Construction"]),
                internal_type=(
                    ""
                    if details["InternalType"] == "None"
                    else str(details["InternalType"])
                ),
            )
            facade.Id = int(details["Id"])
            facade.Blocked = bool(details["Blocked"])
            facade.GeometryLayerId = int(details["GeometryLayerId"])
            facade.Tag = str(item.Tag)
            self.GeometryFacadeList.append(facade)
        self.Constraints = constraints
        self.GeometryCount = len(geometry)
        self.ConstraintCount = len(constraints)
        return {
            "geometry": {
                "identity": "native_tag",
                "old_to_new": {"0": 0, "1": 1},
                "deleted": [],
                "created": [
                    {"index": index, "tag": str(geometry[index].Tag)}
                    for index in range(2, len(geometry))
                ],
            },
            "constraints": {
                "identity": "native_tag",
                "old_to_new": {},
                "deleted": [{"index": 0, "tag": "old-coincident-tag"}],
                "created": [
                    {"index": index, "tag": f"new-constraint-{index}"}
                    for index in range(len(constraints))
                ],
            },
        }

    sketch.fillet = MethodType(fillet, sketch)
    return calls


def _prepared(document, sketch, context, *, form="corner", construction=False):
    _install_diagnostic(
        sketch,
        form=form,
        construction=construction,
        preserve=True,
    )
    return preflight_sketch_fillet(
        context,
        prepare_sketch_fillet(document.Uid, _values(form=form)),
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"form": "corner", "geometry_index": 0}},
        {
            "target": {
                "form": "corner",
                "geometry_index": 0,
                "position": "center",
            }
        },
        {
            "target": {
                "form": "curve_pair",
                "curves": [
                    {
                        "geometry_index": 0,
                        "reference_point_mm": {"x": 0.0, "y": 0.0},
                    }
                ],
            }
        },
        {
            "target": {
                "form": "curve_pair",
                "curves": [
                    {
                        "geometry_index": 0,
                        "reference_point_mm": {"x": 0.0, "y": 0.0},
                    },
                    {
                        "geometry_index": 0,
                        "reference_point_mm": {"x": 1.0, "y": 1.0},
                    },
                ],
            }
        },
        {"preserve_corner": 1},
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_fillet_target_rejects_open_duplicate_or_invalid_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_fillet("document", values)


def test_fillet_diagnostic_accepts_exact_corner_state(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    spec = prepare_sketch_fillet(document.Uid, _values())
    _install_diagnostic(sketch)
    prepared = preflight_sketch_fillet(context, spec)

    assert prepared.plan.input_geometry_indices == (0, 1)
    assert prepared.plan.fillet_geometry_index == 2
    assert prepared.plan.corner_geometry_index == 3
    assert prepared.plan.radius_mm == 2.0
    assert prepared.plan.trimmed is True


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda value: value.update({"extra": True}), "incomplete"),
        (lambda value: value.update({"accepted": False}), "nothing changed"),
        (
            lambda value: value.update({"input_geometry_indices": [0, 99]}),
            "outside the preflight",
        ),
        (
            lambda value: value.update({"input_geometry_indices": [0, 0]}),
            "distinct curves",
        ),
        (lambda value: value.update({"radius_mm": 0.0}), "non-positive"),
        (lambda value: value.update({"solver_status": 1}), "inconsistent"),
        (lambda value: value.update({"geometry_count": 99}), "topology"),
    ),
)
def test_fillet_diagnostic_rejects_untrusted_results(
    monkeypatch, mutate, message
) -> None:
    document, sketch, _context = _host(monkeypatch)
    spec = prepare_sketch_fillet(document.Uid, _values())
    before = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    value = _diagnostic(sketch)
    mutate(value)

    with pytest.raises(NativeSketchError, match=message):
        parse_sketch_fillet_diagnostic(value, spec, before)


def test_fillet_preflight_is_side_effect_free(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    diagnose = _install_diagnostic(sketch)

    prepared = preflight_sketch_fillet(
        context,
        prepare_sketch_fillet(document.Uid, _values()),
    )

    assert len(diagnose) == 1
    assert diagnose[0] == (0, 2, True)
    assert prepared.plan.geometry_records
    assert vars(sketch.Geometry[0].EndPoint) == vars(before[0].EndPoint)
    assert (sketch.GeometryCount, sketch.ConstraintCount) == (2, 1)


def test_fillet_rejects_diagnostic_side_effect_and_grouped_target(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)

    def mutating_diagnosis(self, *_arguments):
        result = _diagnostic(self)
        self.Geometry[0].EndPoint.x = 99.0
        return result

    sketch.diagnoseFillet = MethodType(mutating_diagnosis, sketch)
    with pytest.raises(NativeSketchError, match="feasibility changed"):
        preflight_sketch_fillet(
            context,
            prepare_sketch_fillet(document.Uid, _values()),
        )

    document, sketch, context = _host(monkeypatch)
    sketch.Constraints.append(SimpleNamespace(Type="Group", Elements=((2, 0), (0, 0))))
    sketch.ConstraintCount = 2
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="grouped member"):
        preflight_sketch_fillet(
            context,
            prepare_sketch_fillet(
                document.Uid,
                _values(expected_constraint_count=2),
            ),
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
def test_fillet_mutation_rejects_any_post_preflight_drift(monkeypatch, change) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_fillet(sketch, construction=False, preserve=True)
    change(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_fillet(document, prepared)
    assert calls == []


def test_fillet_executes_exact_corner_and_verifies_final_state(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_fillet(sketch, construction=False, preserve=True)

    draft = create_sketch_fillet(document, prepared)
    result = verify_sketch_fillet(document, draft)

    assert calls == [(0, 2, 2.0, True, True, False)]
    assert result["operation"] == "create_fillet"
    assert result["form"] == "corner"
    assert result["input_geometry_indices"] == [0, 1]
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 4
    assert result["fillet"]["kind"] == "circular_arc"
    assert result["preserved_corner"]["construction"] is True


def test_fillet_routes_exact_curve_pair_and_construction(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch, construction=True)
    prepared = _prepared(
        document,
        sketch,
        context,
        form="curve_pair",
        construction=True,
    )
    calls = _apply_fake_fillet(sketch, construction=True, preserve=True)

    result = verify_sketch_fillet(
        document,
        create_sketch_fillet(document, prepared),
    )

    assert len(calls) == 1
    assert calls[0][0:2] == (0, 1)
    assert (calls[0][2].x, calls[0][2].y) == (18.0, 0.0)
    assert (calls[0][3].x, calls[0][3].y) == (20.0, 2.0)
    assert calls[0][4:] == (2.0, True, True, False)
    assert result["form"] == "curve_pair"
    assert result["construction"] is True
    assert result["fillet"]["construction"] is True


@pytest.mark.parametrize(
    ("corrupt", "message"),
    (
        (
            lambda sketch, draft: setattr(
                sketch.GeometryFacadeList[0], "Tag", "wrong-existing-tag"
            ),
            "durable geometry identity",
        ),
        (
            lambda sketch, draft: setattr(
                sketch.GeometryFacadeList[2], "Construction", True
            ),
            "topology or metadata",
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
def test_fillet_verifier_rejects_unrelated_or_receipt_drift(
    monkeypatch,
    corrupt,
    message,
) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    _apply_fake_fillet(sketch, construction=False, preserve=True)
    draft = create_sketch_fillet(document, prepared)
    assert isinstance(draft, NativeMutationDraft)
    corrupt(sketch, draft)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_fillet(document, draft)
