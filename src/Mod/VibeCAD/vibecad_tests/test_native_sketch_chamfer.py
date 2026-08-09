# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
import math
from types import MethodType, SimpleNamespace

import pytest

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeSketchChamfer import (
    create_sketch_chamfer,
    preflight_sketch_chamfer,
    prepare_sketch_chamfer,
    verify_sketch_chamfer,
)
from VibeCADNativeSketchChamferDiagnostic import parse_sketch_chamfer_diagnostic
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
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


def _final_state(
    sketch,
    *,
    source_construction: bool,
    preserve_corner: bool,
    after_human_toggle: bool,
):
    first = _tagged_clone(sketch.Geometry[0], sketch.GeometryFacadeList[0].Tag)
    second = _tagged_clone(sketch.Geometry[1], sketch.GeometryFacadeList[1].Tag)
    first.EndPoint = _point(18.0, 0.0)
    second.StartPoint = _point(20.0, 2.0)
    support_arc = FakeArc(
        FakeCircle(_point(18.0, 2.0), _point(0.0, 0.0), 2.0),
        -math.pi / 2,
        0.0,
    )
    support_arc.Tag = "chamfer-support-tag"
    geometry = [first, second, support_arc]
    metadata = [
        _metadata(sketch.GeometryFacadeList[0]),
        _metadata(sketch.GeometryFacadeList[1]),
        {
            "Id": 102,
            "Construction": True,
            "Blocked": False,
            "InternalType": "None",
            "GeometryLayerId": 0,
        },
    ]
    corner_index = None
    if preserve_corner:
        corner_index = len(geometry)
        corner = FakePoint(_point(20.0, 0.0))
        corner.Tag = "chamfer-corner-tag"
        corner_construction = not (source_construction and after_human_toggle)
        geometry.append(corner)
        metadata.append(
            {
                "Id": 103,
                "Construction": corner_construction,
                "Blocked": False,
                "InternalType": "None",
                "GeometryLayerId": 0,
            }
        )
    chamfer_index = len(geometry)
    chamfer = FakeLine(_point(18.0, 0.0), _point(20.0, 2.0))
    chamfer.Tag = "chamfer-line-tag"
    geometry.append(chamfer)
    metadata.append(
        {
            "Id": 100 + chamfer_index,
            "Construction": bool(
                source_construction and after_human_toggle and corner_index is None
            ),
            "Blocked": False,
            "InternalType": "None",
            "GeometryLayerId": 0,
        }
    )

    constraints = []
    if corner_index is not None:
        constraints.extend(
            (
                FakeConstraint("PointOnObject", corner_index, 1, 0),
                FakeConstraint("PointOnObject", corner_index, 1, 1),
            )
        )
    constraints.extend(
        (
            FakeConstraint("Tangent", 0, 2, 2, 2),
            FakeConstraint("Tangent", 1, 1, 2, 1),
            FakeConstraint("Coincident", chamfer_index, 2, 0, 2),
            FakeConstraint("Coincident", chamfer_index, 1, 1, 1),
        )
    )
    return geometry, metadata, constraints, corner_index, chamfer_index


def _diagnostic(
    sketch,
    *,
    form: str = "corner",
    source_construction: bool = False,
    preserve_corner: bool = True,
):
    geometry, metadata, constraints, corner_index, chamfer_index = _final_state(
        sketch,
        source_construction=source_construction,
        preserve_corner=preserve_corner,
        after_human_toggle=True,
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
        "support_arc_geometry_index": 2,
        "chamfer_geometry_index": chamfer_index,
        "corner_geometry_index": corner_index,
        "radius_mm": 2.0,
        "trimmed": True,
        "construction": bool(source_construction and corner_index is None),
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
        "geometry": geometry,
        "geometry_metadata": metadata,
        "constraints": constraints,
    }


def _install_diagnostic(
    sketch,
    *,
    form="corner",
    source_construction=False,
    preserve=True,
):
    calls = []

    def diagnose(self, *arguments):
        calls.append(arguments)
        return _diagnostic(
            self,
            form=form,
            source_construction=source_construction,
            preserve_corner=preserve,
        )

    sketch.diagnoseChamfer = MethodType(diagnose, sketch)
    return calls


def _apply_fake_chamfer(
    sketch,
    *,
    source_construction: bool,
    preserve: bool,
):
    calls = []

    def fillet(self, *arguments):
        calls.append(arguments)
        geometry, metadata, constraints, _corner_index, _chamfer_index = _final_state(
            self,
            source_construction=source_construction,
            preserve_corner=preserve,
            after_human_toggle=False,
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


def _prepared(
    document,
    sketch,
    context,
    *,
    form="corner",
    source_construction=False,
    preserve=True,
):
    _install_diagnostic(
        sketch,
        form=form,
        source_construction=source_construction,
        preserve=preserve,
    )
    return preflight_sketch_chamfer(
        context,
        prepare_sketch_chamfer(
            document.Uid,
            _values(form=form, preserve_corner=preserve),
        ),
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
def test_chamfer_target_rejects_open_duplicate_or_invalid_values(updates) -> None:
    values = _values()
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_chamfer("document", values)


def test_chamfer_diagnostic_accepts_exact_corner_state(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    spec = prepare_sketch_chamfer(document.Uid, _values())
    _install_diagnostic(sketch)
    prepared = preflight_sketch_chamfer(context, spec)

    assert prepared.plan.input_geometry_indices == (0, 1)
    assert prepared.plan.support_arc_geometry_index == 2
    assert prepared.plan.corner_geometry_index == 3
    assert prepared.plan.chamfer_geometry_index == 4
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
        (lambda value: value.update({"support_arc_geometry_index": 3}), "topology"),
        (lambda value: value.update({"chamfer_geometry_index": 3}), "corner topology"),
        (lambda value: value.update({"radius_mm": 0.0}), "non-positive"),
        (lambda value: value.update({"solver_status": 1}), "inconsistent"),
        (lambda value: value.update({"geometry_count": 99}), "topology"),
    ),
)
def test_chamfer_diagnostic_rejects_untrusted_results(
    monkeypatch, mutate, message
) -> None:
    document, sketch, _context = _host(monkeypatch)
    spec = prepare_sketch_chamfer(document.Uid, _values())
    before = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    value = _diagnostic(sketch)
    mutate(value)

    with pytest.raises(NativeSketchError, match=message):
        parse_sketch_chamfer_diagnostic(value, spec, before)


def test_chamfer_preflight_is_side_effect_free(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    before = copy.deepcopy(sketch.Geometry)
    diagnose = _install_diagnostic(sketch)

    prepared = preflight_sketch_chamfer(
        context,
        prepare_sketch_chamfer(document.Uid, _values()),
    )

    assert len(diagnose) == 1
    assert diagnose[0] == (0, 2, True)
    assert prepared.plan.geometry_records
    assert vars(sketch.Geometry[0].EndPoint) == vars(before[0].EndPoint)
    assert (sketch.GeometryCount, sketch.ConstraintCount) == (2, 1)


def test_chamfer_rejects_diagnostic_side_effect_and_grouped_target(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)

    def mutating_diagnosis(self, *_arguments):
        result = _diagnostic(self)
        self.Geometry[0].EndPoint.x = 99.0
        return result

    sketch.diagnoseChamfer = MethodType(mutating_diagnosis, sketch)
    with pytest.raises(NativeSketchError, match="feasibility changed"):
        preflight_sketch_chamfer(
            context,
            prepare_sketch_chamfer(document.Uid, _values()),
        )

    document, sketch, context = _host(monkeypatch)
    sketch.Constraints.append(SimpleNamespace(Type="Group", Elements=((2, 0), (0, 0))))
    sketch.ConstraintCount = 2
    _install_diagnostic(sketch)
    with pytest.raises(NativeSketchError, match="grouped member"):
        preflight_sketch_chamfer(
            context,
            prepare_sketch_chamfer(
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
def test_chamfer_mutation_rejects_any_post_preflight_drift(monkeypatch, change) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_chamfer(
        sketch,
        source_construction=False,
        preserve=True,
    )
    change(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_chamfer(document, prepared)
    assert calls == []


def test_chamfer_executes_exact_corner_and_verifies_final_state(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    calls = _apply_fake_chamfer(
        sketch,
        source_construction=False,
        preserve=True,
    )

    draft = create_sketch_chamfer(document, prepared)
    result = verify_sketch_chamfer(document, draft)

    assert calls == [(0, 2, 2.0, True, True, True)]
    assert result["operation"] == "create_chamfer"
    assert result["form"] == "corner"
    assert result["input_geometry_indices"] == [0, 1]
    assert result["geometry_count"] == 5
    assert result["constraint_count"] == 6
    assert result["chamfer"]["kind"] == "line"
    assert result["support_arc_geometry_index"] == 2
    assert result["preserved_corner"]["construction"] is True


def test_chamfer_routes_exact_curve_pair_and_construction(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch, construction=True)
    prepared = _prepared(
        document,
        sketch,
        context,
        form="curve_pair",
        source_construction=True,
        preserve=False,
    )
    calls = _apply_fake_chamfer(
        sketch,
        source_construction=True,
        preserve=False,
    )

    result = verify_sketch_chamfer(
        document,
        create_sketch_chamfer(document, prepared),
    )

    assert len(calls) == 1
    assert calls[0][0:2] == (0, 1)
    assert (calls[0][2].x, calls[0][2].y) == (18.0, 0.0)
    assert (calls[0][3].x, calls[0][3].y) == (20.0, 2.0)
    assert calls[0][4:] == (2.0, True, False, True)
    assert result["form"] == "curve_pair"
    assert result["construction"] is True
    assert result["chamfer"]["construction"] is True


def test_chamfer_preserves_human_construction_index_with_corner(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch, construction=True)
    prepared = _prepared(
        document,
        sketch,
        context,
        source_construction=True,
        preserve=True,
    )
    _apply_fake_chamfer(sketch, source_construction=True, preserve=True)

    result = verify_sketch_chamfer(
        document,
        create_sketch_chamfer(document, prepared),
    )

    assert result["construction"] is False
    assert result["chamfer"]["construction"] is False
    assert result["preserved_corner"]["construction"] is False


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
                sketch.GeometryFacadeList[4], "Construction", True
            ),
            "topology or metadata",
        ),
        (
            lambda sketch, draft: setattr(sketch.Geometry[2], "Radius", 3.0),
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
def test_chamfer_verifier_rejects_unrelated_or_receipt_drift(
    monkeypatch,
    corrupt,
    message,
) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, sketch, context)
    _apply_fake_chamfer(sketch, source_construction=False, preserve=True)
    draft = create_sketch_chamfer(document, prepared)
    assert isinstance(draft, NativeMutationDraft)
    corrupt(sketch, draft)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_chamfer(document, draft)
