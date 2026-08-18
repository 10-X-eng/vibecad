# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from types import SimpleNamespace

import VibeCADNativeSketchSnapshot as sketch_snapshot_module
from VibeCADNativeSketchState import (
    MAX_SERIALIZED_SKETCH_STATE_BYTES,
    serialize_sketch_state,
)
from VibeCADNativeSnapshot import MAX_NATIVE_SNAPSHOT_BYTES, build_active_snapshot


def _vector(x: float, y: float, z: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


class _Document:
    Uid = "sketch-state-document"
    Name = "SketchStateDocument"

    def __init__(self) -> None:
        self.Objects = []

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


class _Object:
    def __init__(self, document: _Document, name: str, type_id: str) -> None:
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.State = []
        document.Objects.append(self)

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected


class _Line:
    TypeId = "Part::GeomLineSegment"
    FirstParameter = 0.0
    LastParameter = 1.0

    def __init__(self, start, end) -> None:
        self.StartPoint = start
        self.EndPoint = end


class _BSpline:
    TypeId = "Part::GeomBSplineCurve"
    Degree = 3
    NbPoles = 40
    NbKnots = 40
    StartPoint = _vector(0, 0)
    EndPoint = _vector(39, 0)
    FirstParameter = 0.0
    LastParameter = 39.0

    def getPoles(self):
        return [_vector(index, index % 3) for index in range(40)]

    def getWeights(self):
        return [1.0] * 40

    def getKnots(self):
        return [float(index) for index in range(40)]

    def getMultiplicities(self):
        return [1] * 40

    def isRational(self):
        return False

    def isPeriodic(self):
        return False

    def isClosed(self):
        return False


class _ExternalLine(_Line):
    def getExtensionOfType(self, type_id: str):
        assert type_id == "Sketcher::ExternalGeometryExtension"
        return SimpleNamespace(
            Ref="Support.Edge1",
            testFlag=lambda flag: flag == "Defining",
        )


def _facade(geometry, index: int, *, construction: bool = False):
    return SimpleNamespace(
        Geometry=geometry,
        Id=1000 + index,
        Construction=construction,
        Blocked=False,
        InternalType="",
        GeometryLayerId=0,
        Tag=f"geometry-{index}",
    )


def _constraint(
    constraint_type: str,
    first: int,
    first_pos: int,
    *,
    second: int = -2000,
    second_pos: int = 0,
    value: float = 0.0,
    name: str = "",
):
    return SimpleNamespace(
        Type=constraint_type,
        First=first,
        FirstPos=first_pos,
        Second=second,
        SecondPos=second_pos,
        Third=-2000,
        ThirdPos=0,
        Value=value,
        Name=name,
        Driving=True,
        IsActive=True,
        InVirtualSpace=False,
    )


def _sketch(document: _Document):
    support = _Object(document, "Support", "PartDesign::Feature")
    sketch = _Object(document, "Sketch", "Sketcher::SketchObject")
    line = _Line(_vector(0, 0), _vector(20, 0))
    construction = _Line(_vector(0, 0), _vector(0, 10))
    sketch.Geometry = [line, construction]
    sketch.GeometryFacadeList = [
        _facade(line, 0),
        _facade(construction, 1, construction=True),
    ]
    sketch.GeometryCount = 2
    sketch.Constraints = [
        _constraint("Horizontal", 0, 0),
        _constraint(
            "Coincident",
            0,
            1,
            second=-3,
            second_pos=1,
            name="On support edge",
        ),
        _constraint("Distance", 1, 1, value=10.0),
    ]
    sketch.ConstraintCount = 3
    sketch.ExternalGeometry = [(support, ["Edge1"])]
    sketch.ExternalTypes = [0]
    sketch.ExternalGeo = [
        _Line(_vector(0, 0), _vector(1, 0)),
        _Line(_vector(0, 0), _vector(0, 1)),
        _ExternalLine(_vector(0, 0), _vector(20, 0)),
    ]
    sketch.AttachmentSupport = [(support, ["Face1"])]
    sketch.Support = (support, ["Face1"])
    sketch.MapMode = "FlatFace"
    sketch.AttachmentOffset = SimpleNamespace(
        Base=_vector(1, 2, 3),
        Rotation=SimpleNamespace(Q=(0.0, 0.0, 0.0, 1.0)),
    )
    sketch.DoF = 2
    sketch.FullyConstrained = False
    sketch.ConflictingConstraints = []
    sketch.RedundantConstraints = [2]
    sketch.PartiallyRedundantConstraints = []
    sketch.MalformedConstraints = []
    sketch.OpenVertices = [_vector(0, 0), _vector(20, 0)]
    sketch.getConstruction = lambda index: index == 1
    sketch.getGeometryId = lambda index: 1000 + index
    sketch.isValid = lambda: True
    sketch.getStatusString = lambda: "Under-constrained: 2 DoF"
    sketch.getProfileDiagnostics = lambda: {
        "wire_count": 1,
        "closed_wire_count": 0,
        "face_count": 0,
        "face_buildable_wire_count": 0,
        "face_maker_status": "not_applicable",
        "face_maker_succeeded": False,
        "support_plane": "sketch_xy",
        "wires": [
            {
                "wire_index": 0,
                "closed": False,
                "brep_valid": True,
                "open_start": [0.0, 0.0, 0.0],
                "open_end": [20.0, 0.0, 0.0],
                "closure_gap": 20.0,
            }
        ],
        "faces": [],
    }
    return sketch, support


def test_exact_sketch_state_reads_geometry_constraints_and_context() -> None:
    document = _Document()
    sketch, support = _sketch(document)

    result = serialize_sketch_state(sketch)

    assert result["geometry_count"] == 2
    assert result["construction_geometry_count"] == 1
    assert result["geometry"] == [
        {
            "index": 0,
            "geometry_id": 1000,
            "type_id": "Part::GeomLineSegment",
            "kind": "line",
            "construction": False,
            "blocked": False,
            "tag": "geometry-0",
            "start_mm": [0.0, 0.0, 0.0],
            "end_mm": [20.0, 0.0, 0.0],
            "first_parameter": 0.0,
            "last_parameter": 1.0,
        },
        {
            "index": 1,
            "geometry_id": 1001,
            "type_id": "Part::GeomLineSegment",
            "kind": "line",
            "construction": True,
            "blocked": False,
            "tag": "geometry-1",
            "start_mm": [0.0, 0.0, 0.0],
            "end_mm": [0.0, 10.0, 0.0],
            "first_parameter": 0.0,
            "last_parameter": 1.0,
        },
    ]
    assert result["constraints"][1] == {
        "index": 1,
        "type": "Coincident",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0, "position": 1},
            {"slot": 2, "geometry_index": -3, "position": 1},
        ],
        "name": "On support edge",
    }
    assert result["external_references"] == [
        {
            "reference_index": 0,
            "subelement": "Edge1",
            "geometry_indices": [-3],
            "object": {
                "document_uid": document.Uid,
                "object_name": support.Name,
                "type_id": support.TypeId,
            },
            "kind": "projection",
        }
    ]
    assert result["external_geometry"] == [
        {
            "geometry_index": -3,
            "type_id": "Part::GeomLineSegment",
            "kind": "line",
            "defining": True,
            "reference": "Support.Edge1",
            "frozen": False,
            "detached": False,
            "missing": False,
            "synchronized": False,
            "start_mm": [0.0, 0.0, 0.0],
            "end_mm": [20.0, 0.0, 0.0],
            "first_parameter": 0.0,
            "last_parameter": 1.0,
        }
    ]
    assert result["attachment"] == {
        "map_mode": "FlatFace",
        "support": [
            {
                "object": {
                    "document_uid": document.Uid,
                    "object_name": support.Name,
                    "type_id": support.TypeId,
                },
                "subelements": ["Face1"],
            }
        ],
        "offset": {
            "origin_mm": [1.0, 2.0, 3.0],
            "rotation_xyzw": [0.0, 0.0, 0.0, 1.0],
        },
    }
    assert result["profile"]["closed_profile"] is False
    assert result["profile"]["open_wires"][0]["closure_gap_mm"] == 20.0
    assert result["solver"]["degrees_of_freedom"] == 2
    assert result["solver"]["redundant_constraints"] == [2]


def test_profile_plane_reports_effective_global_sketch_axes() -> None:
    document = _Document()
    sketch, _support = _sketch(document)
    half_sqrt_two = 2.0 ** -0.5
    sketch.getGlobalPlacement = lambda: SimpleNamespace(
        Base=_vector(0, 0, 0),
        Rotation=SimpleNamespace(
            Q=(half_sqrt_two, 0.0, 0.0, half_sqrt_two)
        ),
    )

    result = serialize_sketch_state(sketch)

    assert result["profile"]["support_plane"] == {
        "space": "global",
        "origin_mm": [0.0, 0.0, 0.0],
        "x_direction": [1.0, 0.0, 0.0],
        "y_direction": [0.0, 0.0, 1.0],
        "normal": [0.0, -1.0, 0.0],
    }


def test_large_sketch_state_truncates_explicitly_below_snapshot_limit(
    monkeypatch,
) -> None:
    document = _Document()
    sketch, support = _sketch(document)
    curve = _BSpline()
    sketch.Geometry = [curve] * 100
    sketch.GeometryFacadeList = [_facade(curve, index) for index in range(100)]
    sketch.GeometryCount = 100
    sketch.Constraints = [
        _constraint("Distance", index, 1, value=float(index), name=f"Distance {index}")
        for index in range(100)
    ]
    sketch.ConstraintCount = 100
    sketch.ExternalGeometry = [(support, [f"Edge{index + 1}" for index in range(50)])]
    sketch.ExternalTypes = [0] * 50
    sketch.ExternalGeo = sketch.ExternalGeo[:2]
    sketch.getConstruction = lambda _index: False
    sketch.getGeometryId = lambda index: 2000 + index

    state = serialize_sketch_state(sketch)

    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    assert len(encoded) <= MAX_SERIALIZED_SKETCH_STATE_BYTES
    assert state["geometry_count"] == 100
    assert state["constraint_count"] == 100
    assert state["external_reference_count"] == 50
    assert state["geometry_truncated"] is True
    assert state["constraints_truncated"] is True
    assert state["external_references_truncated"] is True

    monkeypatch.setattr(
        sketch_snapshot_module,
        "_active_edit_sketch",
        lambda _document: sketch,
    )
    snapshot = build_active_snapshot(
        document,
        "sketch.edit",
        {
            "document_uid": document.Uid,
            "structural_revision": 3,
            "recent_receipts": [],
        },
        selection={"document_uid": document.Uid, "items": []},
    )
    assert len(json.dumps(snapshot, separators=(",", ":")).encode()) <= (
        MAX_NATIVE_SNAPSHOT_BYTES
    )
    assert snapshot["domain"]["active_sketch"]["geometry_count"] == 100


def test_setup_snapshot_keeps_non_active_sketches_as_summaries(monkeypatch) -> None:
    document = _Document()
    sketch, _support = _sketch(document)
    monkeypatch.setattr(
        sketch_snapshot_module,
        "_active_edit_sketch",
        lambda _document: None,
    )

    result = sketch_snapshot_module.build_sketch_snapshot(document, "sketch.setup")

    assert result["sketches"][0]["object_name"] == sketch.Name
    assert result["sketches"][0]["supports"][0]["object_name"] == "Support"
    assert "geometry" not in result["sketches"][0]
    assert "constraints" not in result["sketches"][0]


def test_edit_snapshot_translates_human_selection_to_exact_tool_targets(
    monkeypatch,
) -> None:
    document = _Document()
    sketch, _support = _sketch(document)
    sketch.getGeoVertexIndex = lambda vertex: {
        0: (0, 1),
        1: (0, 2),
        2: (1, 2),
    }.get(vertex, (-2000, 0))
    monkeypatch.setattr(
        sketch_snapshot_module,
        "_active_edit_sketch",
        lambda _document: sketch,
    )
    selection = {
        "document_uid": document.Uid,
        "selected_count": 1,
        "items": [
            {
                "object": {
                    "document_uid": document.Uid,
                    "object_name": sketch.Name,
                    "type_id": sketch.TypeId,
                },
                "subelements": [
                    ";g1000.eEdge1",
                    ";g1001v2.vVertex3",
                    ";e2000.ExternalEdge1",
                    ";H_Axis.H_Axis",
                    ";V_Axis.V_Axis",
                    ";RootPoint.RootPoint",
                    "Constraint2",
                    "Constraint2",
                ],
            }
        ],
    }

    result = sketch_snapshot_module.build_sketch_snapshot(
        document,
        "sketch.edit",
        selection=selection,
    )

    assert result["user_selection"] == {
        "meaning": "Exact turn-start targets for 'this', 'these', or 'selected'.",
        "elements": [
            {"geometry_index": 0, "position": "whole"},
            {"geometry_index": 1, "position": "end"},
            {"geometry_index": -3, "position": "whole"},
            {"geometry_index": -1, "position": "whole"},
            {"geometry_index": -2, "position": "whole"},
            {"geometry_index": -1, "position": "start"},
        ],
        "constraints": [{"constraint_index": 1}],
    }


def test_edit_snapshot_omits_semantic_selection_for_another_object(
    monkeypatch,
) -> None:
    document = _Document()
    sketch, support = _sketch(document)
    monkeypatch.setattr(
        sketch_snapshot_module,
        "_active_edit_sketch",
        lambda _document: sketch,
    )

    result = sketch_snapshot_module.build_sketch_snapshot(
        document,
        "sketch.edit",
        selection={
            "document_uid": document.Uid,
            "selected_count": 1,
            "items": [
                {
                    "object": {
                        "document_uid": document.Uid,
                        "object_name": support.Name,
                        "type_id": support.TypeId,
                    },
                    "subelements": ["Edge1"],
                }
            ],
        },
    )

    assert "user_selection" not in result


def test_edit_snapshot_exposes_bounded_carbon_copy_source_summaries(
    monkeypatch,
) -> None:
    document = _Document()
    active, _active_support = _sketch(document)
    source, source_support = _sketch(document)
    source.Name = "SourceSketch"
    source.Label = "Source Sketch"
    source_support.Name = "SourceSupport"
    source_support.Label = "Source Support"
    monkeypatch.setattr(
        sketch_snapshot_module,
        "_active_edit_sketch",
        lambda _document: active,
    )

    result = sketch_snapshot_module.build_sketch_snapshot(document, "sketch.edit")

    assert result["active_sketch"]["object_name"] == active.Name
    assert result["active_sketch"]["external_reference_count"] == 1
    assert result["active_sketch"]["external_geometry_count"] == 1
    assert result["source_sketches"] == [
        {
            "document_uid": document.Uid,
            "object_name": source.Name,
            "label": source.Label,
            "type_id": source.TypeId,
            "geometry_count": 2,
            "constraint_count": 3,
            "construction_geometry_count": 1,
            "external_reference_count": 1,
            "external_geometry_count": 1,
            "map_mode": "FlatFace",
            "supports": [
                {
                    "document_uid": document.Uid,
                    "object_name": source_support.Name,
                    "label": source_support.Label,
                    "type_id": source_support.TypeId,
                }
            ],
            "fully_constrained": False,
        }
    ]
    assert "geometry" not in result["source_sketches"][0]
    assert "constraints" not in result["source_sketches"][0]
