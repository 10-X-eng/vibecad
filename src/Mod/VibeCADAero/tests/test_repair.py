# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded pitch-stability repairs for Analyze (propose / apply)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import AeroConfig as config
import AeroRepair as repair


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


class _Placement:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.Base = SimpleNamespace(x=float(x), y=float(y), z=float(z))
        self.Rotation = SimpleNamespace(Angle=0.0, Axis=SimpleNamespace(x=0.0, y=1.0, z=0.0))


class _Part:
    def __init__(self, name, bbox, x=0.0, y=0.0, z=0.0):
        self.Name = name
        self.Label = name
        self.Shape = _Shape(bbox)
        self.Placement = _Placement(x, y, z)


class _Cfg:
    def __init__(self):
        self.Name = "AeroConfig"
        self.Label = "AeroConfig"

    def addProperty(self, *_args, **_kwargs):
        return self


class _Doc:
    def __init__(self, objects=None):
        self.Objects = list(objects or [])
        self.Name = "Unnamed"
        self.FileName = ""
        self._by_name = {obj.Name: obj for obj in self.Objects}

    def addObject(self, typ, name):
        obj = _Cfg() if name == "AeroConfig" else _Part(name, _BoundBox(0, 1, 0, 1, 0, 1))
        obj.TypeId = typ
        self.Objects.append(obj)
        self._by_name[name] = obj
        return obj

    def getObject(self, name):
        return self._by_name.get(name)

    def recompute(self):
        return None


def _voider_parts():
    lower = _Part("lower_wing", _BoundBox(0.0, 90.0, -250.0, 250.0, 0.0, 8.0))
    upper = _Part(
        "upper_wing",
        _BoundBox(-103.5, -13.5, -250.0, 250.0, 126.0, 134.0),
        x=-103.5,
    )
    boom = _Part("boom", _BoundBox(-280.0, 20.0, -4.0, 4.0, 60.0, 68.0), x=-20.0)
    tail = _Part("h_tail", _BoundBox(-310.0, -250.0, -80.0, 80.0, 60.0, 66.0), x=-250.0)
    pod = _Part("avionics_pod", _BoundBox(40.0, 80.0, -20.0, 20.0, 20.0, 40.0), x=60.0)
    camera = _Part("camera_bay", _BoundBox(70.0, 100.0, -15.0, 15.0, 10.0, 30.0), x=85.0)
    return lower, upper, boom, tail, pod, camera


def test_propose_empty_when_pitch_stable():
    cfg = config.resolve_geometry(None)
    proposed = repair.propose_repairs(cfg, {"PitchUnstable": False, "Cmalpha": -0.4})
    assert proposed == []


def test_propose_grows_tail_boom_moves_cg_and_nudge_upper_wing():
    cfg = config.resolve_geometry(None)
    cfg["has_h_tail"] = True
    proposed = repair.propose_repairs(
        cfg,
        {"PitchUnstable": True, "Cmalpha": 0.8},
        doc=_Doc(list(_voider_parts())),
    )
    fields = {item["field"] for item in proposed}
    assert "tail_span_mm" in fields
    assert "tail_chord_mm" in fields
    assert "boom_length_mm" in fields
    assert "stagger_c" in fields
    assert "decalage_deg" not in fields
    assert "xyz_ref_c" in fields
    assert {item["part"] for item in proposed} >= {"h_tail", "boom", "avionics_pod", "camera_bay", "upper_wing"}
    tail_span = next(item for item in proposed if item["field"] == "tail_span_mm")
    assert tail_span["after"] > tail_span["before"]
    boom = next(item for item in proposed if item["field"] == "boom_length_mm")
    assert boom["after"] > boom["before"]
    cg = next(item for item in proposed if item["field"] == "xyz_ref_c")
    assert cg["after"] < cg["before"]
    stagger = next(item for item in proposed if item["field"] == "stagger_c")
    assert stagger["after"] > stagger["before"]
    pod = next(item for item in proposed if item["part"] == "avionics_pod")
    assert pod["after"] > pod["before"]
    for item in proposed:
        assert item["sentence"]
        assert item["sentence"].endswith(".")
        assert "canard" not in item["sentence"].lower()
        assert "toward the cad nose" not in item["sentence"].lower()


def test_propose_respects_hard_bounds():
    cfg = config.resolve_geometry(None)
    cfg["has_h_tail"] = True
    cfg["tail_span_mm"] = cfg["span_mm"] * repair.TAIL_SPAN_MAX_FRAC
    cfg["tail_chord_mm"] = cfg["chord_mm"] * repair.TAIL_CHORD_MAX_FRAC
    cfg["boom_length_mm"] = cfg["chord_mm"] * repair.BOOM_MAX_CHORD_MULT
    cfg["stagger_c"] = repair.STAGGER_MAX
    cfg["xyz_ref_c"] = repair.XYZ_REF_C_MIN
    cfg["xyz_ref"] = [cfg["xyz_ref_c"] * cfg["chord_m"], 0.0, cfg["gap_m"] / 2.0]
    proposed = repair.propose_repairs(cfg, {"PitchUnstable": True, "Cmalpha": 1.0})
    fields = {item["field"] for item in proposed}
    assert "tail_span_mm" not in fields
    assert "tail_chord_mm" not in fields
    assert "boom_length_mm" not in fields
    assert "stagger_c" not in fields
    assert "decalage_deg" not in fields
    assert "xyz_ref_c" not in fields


def test_apply_writes_aeroconfig_and_moves_mock_cad():
    parts = _voider_parts()
    tail = parts[3]
    boom = parts[2]
    pod = parts[4]
    upper = parts[1]
    doc = _Doc(list(parts))
    cfg = config.resolve_geometry(None)
    cfg["has_h_tail"] = True
    proposed = repair.propose_repairs(cfg, {"PitchUnstable": True, "Cmalpha": 0.5}, doc=doc)
    landed = repair.apply_repairs(doc, cfg, proposed)
    assert landed
    aero = doc.getObject("AeroConfig")
    assert aero is not None
    assert float(aero.tail_span_mm) > 150.0 * 0.99
    assert float(aero.boom_length_mm) > 250.0
    assert float(aero.xyz_ref_c) < 0.25
    assert pod.Placement.Base.x > 60.0
    tail_bb = tail.Shape.BoundBox
    boom_bb = boom.Shape.BoundBox
    assert abs(tail_bb.YMax - tail_bb.YMin) > 160.0
    assert abs(boom_bb.XMax - boom_bb.XMin) > 300.0
    assert upper.Placement.Base.x < -103.5
    sentences = [item["sentence"] for item in landed]
    assert any("horizontal tail" in text.lower() or "tail span" in text.lower() for text in sentences)
    resolved = config.resolve_geometry(doc)
    assert abs(resolved["xyz_ref_c"] - float(aero.xyz_ref_c)) < 1e-9
    assert abs(resolved["xyz_ref"][0] - resolved["xyz_ref_c"] * resolved["chord_m"]) < 1e-9


def test_apply_without_cad_parts_still_lands_non_tail_config():
    doc = _Doc()
    cfg = config.resolve_geometry(None)
    proposed = repair.propose_repairs(cfg, {"PitchUnstable": True, "Cmalpha": 0.4})
    landed = repair.apply_repairs(doc, cfg, proposed)
    assert landed
    assert any(item.get("config") for item in landed)
    aero = doc.getObject("AeroConfig")
    assert float(getattr(aero, "boom_length_mm")) > 0.0


def test_tailless_repair_does_not_invent_a_solver_only_tail():
    doc = _Doc()
    cfg = config.resolve_geometry(doc)
    assert cfg["has_h_tail"] is False

    proposed = repair.propose_repairs(
        cfg,
        {"PitchUnstable": True, "Cmalpha": 0.4},
        doc=doc,
    )
    fields = {item["field"] for item in proposed}
    assert "tail_span_mm" not in fields
    assert "tail_chord_mm" not in fields

    landed = repair.apply_repairs(doc, cfg, proposed)
    assert landed
    assert config.resolve_geometry(doc)["has_h_tail"] is False


class _GetterOnlyBoundBox:
    def __init__(self, x_min, x_max, y_min, y_max, z_min, z_max):
        self.XMin = x_min
        self.XMax = x_max
        self.YMin = y_min
        self.YMax = y_max
        self.ZMin = z_min
        self.ZMax = z_max

    @property
    def XLength(self):
        return abs(self.XMax - self.XMin)

    @XLength.setter
    def XLength(self, _value):
        raise AttributeError("can't set attribute 'XLength'")

    @property
    def YLength(self):
        return abs(self.YMax - self.YMin)

    @YLength.setter
    def YLength(self, _value):
        raise AttributeError("can't set attribute 'YLength'")

    @property
    def ZLength(self):
        return abs(self.ZMax - self.ZMin)

    @ZLength.setter
    def ZLength(self, _value):
        raise AttributeError("can't set attribute 'ZLength'")


def test_scale_bbox_does_not_assign_readonly_lengths():
    bbox = _GetterOnlyBoundBox(0.0, 10.0, -5.0, 5.0, 0.0, 2.0)
    repair._scale_bbox(bbox, 2.0, 1.5, 1.0, (0.0, 0.0, 0.0))
    assert bbox.XMin == 0.0
    assert bbox.XMax == 20.0
    assert bbox.YMin == -7.5
    assert bbox.YMax == 7.5
    assert bbox.XLength == 20.0
    assert bbox.YLength == 15.0
    assert bbox.ZLength == 2.0


def test_scale_part_succeeds_when_length_set_raises():
    bbox = _GetterOnlyBoundBox(-280.0, 20.0, -4.0, 4.0, 60.0, 68.0)
    obj = _Part("boom", bbox)
    obj.Shape.BoundBox = bbox
    ok = repair._scale_part(obj, 1.2, 1.0, 1.0, (-280.0, 0.0, 64.0))
    assert ok is True
    assert abs(bbox.XMax - bbox.XMin) > 300.0


def test_repair_source_does_not_assign_bbox_lengths():
    source = Path(repair.__file__).read_text(encoding="utf-8")
    assert "bbox.XLength =" not in source
    assert "bbox.YLength =" not in source
    assert "bbox.ZLength =" not in source


def test_user_message_lists_each_change():
    changes = [
        {
            "part": "h_tail",
            "field": "tail_span_mm",
            "before": 150.0,
            "after": 180.0,
            "sentence": "Grew the horizontal tail span from 150 mm to 180 mm.",
        }
    ]
    text = repair.format_user_message(
        changes,
        {"PitchUnstable": False, "Cmalpha": -0.2},
        passes=1,
    )
    assert "Grew the horizontal tail span from 150 mm to 180 mm." in text
    assert "pitch-stable" in text.lower()
