# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometry and parameter resolution for the Aero workbench."""

from __future__ import annotations

from types import SimpleNamespace

import AeroConfig as config


VOIDER_DEFAULTS = {
    "span_mm": 500.0,
    "chord_mm": 90.0,
    "gap_c": 1.4,
    "stagger_c": 1.15,
    "decalage_deg": 2.0,
    "auw_g": 149.6,
    "airfoil": "e63",
    "alpha_deg": 4.0,
}


class _BoundBox:
    def __init__(self, x_min, x_max, y_min, y_max, z_min, z_max):
        self.XMin = x_min
        self.XMax = x_max
        self.YMin = y_min
        self.YMax = y_max
        self.ZMin = z_min
        self.ZMax = z_max
        self.XLength = x_max - x_min
        self.YLength = y_max - y_min
        self.ZLength = z_max - z_min


class _Shape:
    def __init__(self, bbox):
        self.BoundBox = bbox


class _Doc:
    def __init__(self, objects=None, **attrs):
        self.Objects = list(objects or [])
        self.Name = "Unnamed"
        self.FileName = ""
        for key, value in attrs.items():
            setattr(self, key, value)

    def getObject(self, name):
        for obj in self.Objects:
            if getattr(obj, "Name", None) == name or getattr(obj, "Label", None) == name:
                return obj
        return None


def test_defaults_are_locked_voider_ultimate():
    resolved = config.resolve_geometry(None)
    for key, expected in VOIDER_DEFAULTS.items():
        assert resolved[key] == expected
    assert resolved["geometry_source"] == "defaults"
    assert resolved["span_m"] == 0.5
    assert resolved["chord_m"] == 0.09
    assert resolved["reference_area_m2"] == 2 * 0.5 * 0.09
    assert resolved["gap_m"] == 1.4 * 0.09
    assert resolved["stagger_m"] == 1.15 * 0.09


def test_aeroconfig_object_wins_over_inference_and_defaults():
    aero = SimpleNamespace(
        Name="AeroConfig",
        Label="AeroConfig",
        span_mm=400.0,
        chord_mm=80.0,
        gap_c=1.2,
        stagger_c=1.0,
        decalage_deg=1.5,
        auw_g=120.0,
        airfoil="e63",
        alpha_deg=3.0,
    )
    lower = SimpleNamespace(
        Name="lower_wing",
        Label="lower_wing",
        Shape=_Shape(_BoundBox(0, 90, -250, 250, 0, 8)),
    )
    doc = _Doc(objects=[lower, aero])
    resolved = config.resolve_geometry(doc)
    assert resolved["span_mm"] == 400.0
    assert resolved["chord_mm"] == 80.0
    assert resolved["auw_g"] == 120.0
    assert resolved["geometry_source"] == "AeroConfig"


def test_document_properties_used_when_no_aeroconfig():
    doc = _Doc(span_mm=420.0, chord_mm=85.0, auw_g=130.0, airfoil="e63")
    resolved = config.resolve_geometry(doc)
    assert resolved["span_mm"] == 420.0
    assert resolved["chord_mm"] == 85.0
    assert resolved["auw_g"] == 130.0
    assert resolved["airfoil"] == "e63"
    assert resolved["geometry_source"] == "document"


def test_infer_biplane_from_voider_named_objects():
    lower = SimpleNamespace(
        Name="lower_wing",
        Label="lower_wing",
        Shape=_Shape(_BoundBox(0.0, 90.0, -250.0, 250.0, 0.0, 8.0)),
    )
    upper = SimpleNamespace(
        Name="upper_wing",
        Label="upper_wing",
        Shape=_Shape(_BoundBox(-103.5, -13.5, -250.0, 250.0, 126.0, 134.0)),
    )
    boom = SimpleNamespace(
        Name="boom",
        Label="boom",
        Shape=_Shape(_BoundBox(-20.0, 280.0, -4.0, 4.0, 60.0, 68.0)),
    )
    tail = SimpleNamespace(
        Name="h_tail",
        Label="h_tail",
        Shape=_Shape(_BoundBox(250.0, 310.0, -80.0, 80.0, 60.0, 66.0)),
    )
    doc = _Doc(objects=[lower, upper, boom, tail])
    resolved = config.resolve_geometry(doc)
    assert resolved["geometry_source"] == "inferred"
    assert abs(resolved["span_mm"] - 500.0) < 1.0
    assert abs(resolved["chord_mm"] - 90.0) < 1.0
    assert abs(resolved["gap_c"] - 1.4) < 0.15
    assert abs(resolved["stagger_c"] - 1.15) < 0.15


def test_missing_named_objects_fall_back_to_defaults():
    doc = _Doc(objects=[SimpleNamespace(Name="Cube", Label="Cube")])
    resolved = config.resolve_geometry(doc)
    assert resolved["geometry_source"] == "defaults"
    assert resolved["span_mm"] == 500.0
    assert resolved["airfoil"] == "e63"


def test_wild_loft_bbox_is_rejected_for_locked_defaults():
    """Real voider.FCStd lower_wing loft was ~1640 x 295 mm, not 500 x 90."""

    lower = SimpleNamespace(
        Name="lower_wing",
        Label="lower_wing",
        Shape=_Shape(_BoundBox(0.0, 295.0, -820.0, 820.0, 0.0, 12.0)),
    )
    upper = SimpleNamespace(
        Name="upper_wing",
        Label="upper_wing",
        Shape=_Shape(_BoundBox(-300.0, 0.0, -820.0, 820.0, 400.0, 420.0)),
    )
    doc = _Doc(objects=[lower, upper])
    resolved = config.resolve_geometry(doc)
    assert resolved["geometry_source"] == "defaults"
    assert resolved["span_mm"] == 500.0
    assert resolved["chord_mm"] == 90.0
    assert resolved["reference_area_m2"] == 0.09
    assert resolved.get("inference_rejected") is True


def test_defaults_include_tailsitter_vehicle_type():
    resolved = config.resolve_geometry(None)
    assert resolved["vehicle_type"] == "tailsitter"
    assert resolved["airfoil"] == "e63"


def test_write_config_persists_vehicle_and_geometry():
    created = []

    class _Cfg:
        def __init__(self):
            self.Name = "AeroConfig"
            self.Label = "AeroConfig"

        def addProperty(self, *_args, **_kwargs):
            return self

    class _Writable(_Doc):
        def addObject(self, typ, name):
            obj = _Cfg()
            obj.TypeId = typ
            self.Objects.append(obj)
            created.append(obj)
            return obj

    doc = _Writable()
    written = config.write_config(
        doc,
        {
            "vehicle_type": "Airplane",
            "airfoil": "e63",
            "span_mm": 800.0,
            "chord_mm": 120.0,
            "auw_g": 250.0,
            "alpha_deg": 3.0,
            "n_props": 1,
            "prop_diameter_mm": 200.0,
            "thrust_to_weight": 0.4,
        },
    )
    assert written is created[0]
    assert written.vehicle_type == "airplane"
    assert written.airfoil == "e63"
    assert written.span_mm == 800.0
    assert written.chord_mm == 120.0
    resolved = config.resolve_geometry(doc)
    assert resolved["geometry_source"] == "AeroConfig"
    assert resolved["vehicle_type"] == "airplane"
    assert resolved["span_mm"] == 800.0


def test_plausible_inference_still_accepted():
    lower = SimpleNamespace(
        Name="lower_wing",
        Label="lower_wing",
        Shape=_Shape(_BoundBox(0.0, 90.0, -250.0, 250.0, 0.0, 8.0)),
    )
    doc = _Doc(objects=[lower])
    resolved = config.resolve_geometry(doc)
    assert resolved["geometry_source"] == "inferred"
    assert abs(resolved["span_mm"] - 500.0) < 1.0
    assert abs(resolved["chord_mm"] - 90.0) < 1.0
