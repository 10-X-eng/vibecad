# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from VibeCADNativeInspect import (
    geometry_validity,
    inspect_element,
    visual_inspection_result,
)
from VibeCADNativeMeasure import (
    mass_properties,
    measure_angle,
    measure_distance,
    measure_radius,
)
from VibeCADNativeTargets import NativeElementRef, NativeObjectRef
from VibeCADNativeView import (
    NativeViewError,
    capture_screenshot,
    fit_all,
    set_grid_visible,
    set_isometric,
)


class _Vector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _Element:
    ShapeType = "Edge"
    FirstParameter = 0.0
    LastParameter = 1.0
    Length = 10.0
    Vertexes = []

    def __init__(self, direction=(1.0, 0.0, 0.0), radius=5.0):
        self.direction = _Vector(*direction)
        self.Curve = SimpleNamespace(Radius=radius)

    def tangentAt(self, _parameter):
        return self.direction

    def distToShape(self, _other):
        return (
            12.5,
            [(_Vector(0.0, 0.0, 0.0), _Vector(12.5, 0.0, 0.0))],
            [],
        )


class _Shape:
    def __init__(self, element=None, *, volume=1000.0, area=600.0):
        self.element = element or _Element()
        self.Volume = volume
        self.Area = area
        self.CenterOfMass = _Vector(5.0, 5.0, 5.0)
        self.Solids = [1]
        self.Shells = [1]
        self.Faces = [1] * 6
        self.Edges = [1] * 12
        self.Vertexes = [1] * 8

    def getElement(self, name):
        if name not in {"Edge1", "Edge2"}:
            raise RuntimeError("missing")
        return self.element

    def isNull(self):
        return False

    def isValid(self):
        return True


class _Object:
    def __init__(self, document, name, type_id="PartDesign::Feature", shape=None):
        self.Document = document
        self.Name = name
        self.TypeId = type_id
        self.Shape = shape or _Shape()
        self.State = []

    def isDerivedFrom(self, expected):
        return self.TypeId == expected


class _Document:
    Uid = "document-a"
    Name = "DocumentA"

    def __init__(self):
        self.objects = {}

    def add(self, obj):
        self.objects[obj.Name] = obj
        return obj

    def getObject(self, name):
        return self.objects.get(name)


def _target(name: str, subelement: str = "Edge1") -> NativeElementRef:
    return NativeElementRef(NativeObjectRef("document-a", name), subelement)


def test_exact_distance_angle_and_radius_reads() -> None:
    document = _Document()
    document.add(_Object(document, "First", shape=_Shape(_Element((1, 0, 0), 5))))
    document.add(_Object(document, "Second", shape=_Shape(_Element((0, 1, 0), 8))))

    distance = measure_distance(document, _target("First"), _target("Second"))
    angle = measure_angle(document, _target("First"), _target("Second"))
    radius = measure_radius(document, _target("Second"))
    assert distance["distance_mm"] == 12.5
    assert distance["claim_ceiling"] == "measured"
    assert angle["angle_degrees"] == pytest.approx(90.0)
    assert angle["claim_ceiling"] == "measured"
    assert radius["radius_mm"] == 8.0
    assert radius["claim_ceiling"] == "measured"


def test_mass_properties_use_explicit_units_and_default_density() -> None:
    document = _Document()
    document.add(_Object(document, "Box"))

    result = mass_properties(
        document,
        (NativeObjectRef("document-a", "Box"),),
    )

    assert result["volume_mm3"] == 1000.0
    assert result["area_mm2"] == 600.0
    assert result["mass_kg"] == pytest.approx(0.001)
    assert result["center_of_mass_mm"] == [5.0, 5.0, 5.0]
    assert result["objects"][0]["density_kg_m3"] == pytest.approx(1000.0)
    assert result["objects"][0]["density_source"] == "default_1000_kg_m3"


def test_mass_properties_compute_solid_weighted_center_for_compound_shape() -> None:
    document = _Document()
    shape = _Shape(volume=1000.0)
    del shape.CenterOfMass
    shape.Solids = [
        SimpleNamespace(Volume=250.0, CenterOfMass=_Vector(2.0, 4.0, 6.0)),
        SimpleNamespace(Volume=750.0, CenterOfMass=_Vector(10.0, 12.0, 14.0)),
    ]
    document.add(_Object(document, "CompoundBody", shape=shape))

    result = mass_properties(
        document,
        (NativeObjectRef("document-a", "CompoundBody"),),
    )

    assert result["volume_mm3"] == 1000.0
    assert result["center_of_volume_mm"] == [8.0, 10.0, 12.0]
    assert result["center_of_mass_mm"] == [8.0, 10.0, 12.0]


def test_new_material_wrapper_default_name_uses_mass_properties_fallback() -> None:
    document = _Document()
    obj = document.add(_Object(document, "Box"))
    obj.ShapeMaterial = SimpleNamespace(
        Name="Default",
        hasPhysicalProperty=lambda _name: True,
        getPhysicalQuantity=lambda _name: SimpleNamespace(
            getValueAs=lambda _unit: 1.0e-9
        ),
    )

    result = mass_properties(document, (NativeObjectRef("document-a", "Box"),))

    assert result["mass_kg"] == pytest.approx(0.001)
    assert result["objects"][0]["density_kg_m3"] == pytest.approx(1000.0)
    assert result["objects"][0]["density_source"] == "default_1000_kg_m3"


def test_element_and_validity_reads_are_concise() -> None:
    document = _Document()
    document.add(_Object(document, "Box"))

    element = inspect_element(document, _target("Box"))
    validity = geometry_validity(
        document,
        NativeObjectRef("document-a", "Box"),
    )

    assert element["shape_type"] == "Edge"
    assert element["length_mm"] == 10.0
    assert validity == {
        "target": {"document_uid": "document-a", "object_name": "Box"},
        "valid": True,
        "is_null": False,
        "shape_counts": {
            "solids": 1,
            "shells": 1,
            "faces": 6,
            "edges": 12,
            "vertices": 8,
        },
        "object_state": [],
    }


def test_visual_inspection_reads_bounded_statistics() -> None:
    document = _Document()
    actual = document.add(_Object(document, "Actual"))
    nominal = document.add(_Object(document, "Nominal"))
    feature = document.add(
        _Object(document, "Inspection", type_id="Inspection::Feature")
    )
    feature.Actual = actual
    feature.Nominals = [nominal]
    feature.Distances = [-0.2, 0.1, 0.3]

    result = visual_inspection_result(
        document,
        NativeObjectRef("document-a", "Inspection"),
    )

    assert result["distance_count"] == 3
    assert result["distance_statistics_mm"] == {
        "minimum": -0.2,
        "maximum": 0.3,
        "mean": pytest.approx(0.06666666666666667),
        "maximum_absolute": 0.3,
    }


def test_view_operations_call_direct_view_api_without_command_dispatch() -> None:
    document = _Document()
    calls = []
    view = SimpleNamespace(
        fitAll=lambda: calls.append("fit"),
        viewAxonometric=lambda: calls.append("isometric"),
    )
    gui_document = SimpleNamespace(Document=document, activeView=lambda: view)
    gui = SimpleNamespace(activeDocument=lambda: gui_document)

    assert fit_all(document, gui=gui) == {"fit_all": True}
    assert set_isometric(document, gui=gui) == {"orientation": "isometric"}
    assert calls == ["fit", "isometric"]

    other = _Document()
    with pytest.raises(NativeViewError, match="another document"):
        fit_all(other, gui=gui)


def test_grid_visibility_waits_for_the_deferred_view_update(monkeypatch) -> None:
    observed = iter((False, False, True))
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "VibeCADGrid",
        SimpleNamespace(
            is_grid_visible=lambda: next(observed),
            toggle_grid=lambda visible: calls.append(("toggle", visible)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "FreeCADGui",
        SimpleNamespace(updateGui=lambda: calls.append(("update",))),
    )
    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(
            QtCore=SimpleNamespace(QEventLoop=SimpleNamespace(AllEvents=1)),
            QtWidgets=SimpleNamespace(
                QApplication=SimpleNamespace(
                    processEvents=lambda *_args: calls.append(("events",))
                )
            ),
        ),
    )

    assert set_grid_visible(_Document(), True) == {"grid_visible": True}
    assert calls == [
        ("toggle", True),
        ("update",),
        ("events",),
        ("update",),
        ("events",),
    ]


def test_screenshot_wrapper_returns_only_bounded_verified_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    from tool_impl.service import core_capture_view_screenshot

    document = _Document()
    artifact = tmp_path / "view.png"
    artifact.write_bytes(b"PNG")
    service = SimpleNamespace(_active_document=lambda: document)
    monkeypatch.setattr(
        core_capture_view_screenshot,
        "run",
        lambda *_args, **_kwargs: {
            "ok": True,
            "requested": {"noisy": True},
            "observed": {"noisy": True},
            "artifact": {"path": str(artifact), "file_size": 3},
            "visual_observation": {"mostly_blank": False},
            "_vibecad_image_attachment": {
                "path": str(artifact),
                "name": "exact view",
                "private": "removed",
            },
        },
    )

    result = capture_screenshot(service, document)

    assert result == {
        "captured": True,
        "image": {"mime_type": "image/png", "size_bytes": 3},
        "new_observation": True,
        "presentation_only": True,
        "artifact_class": "presentation",
        "claim_ceiling": "not_measured",
        "visual_observation": {"mostly_blank": False},
        "_vibecad_image_attachment": {
            "path": str(artifact),
            "name": "exact view",
        },
    }


def test_screenshot_wrapper_rejects_unverified_or_inconsistent_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    from tool_impl.service import core_capture_view_screenshot

    document = _Document()
    service = SimpleNamespace(_active_document=lambda: document)
    artifact = tmp_path / "view.png"
    artifact.write_bytes(b"PNG")
    raw = {
        "ok": True,
        "artifact": {"path": str(artifact), "file_size": 7},
    }
    monkeypatch.setattr(
        core_capture_view_screenshot,
        "run",
        lambda *_args, **_kwargs: raw,
    )
    with pytest.raises(NativeViewError, match="size bound"):
        capture_screenshot(service, document)

    raw["artifact"]["file_size"] = 3
    raw["_vibecad_image_attachment"] = {
        "path": str(tmp_path / "other.png"),
    }
    with pytest.raises(NativeViewError, match="inconsistent"):
        capture_screenshot(service, document)
