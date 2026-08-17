# SPDX-License-Identifier: LGPL-2.1-or-later

"""Agent-control import surface: VibeCADAero.run_analyze(doc)."""

from __future__ import annotations

import VibeCADAero


class _Obj:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.Proxy = None
        self.ViewObject = None

    def addProperty(self, *_args, **_kwargs):
        return self


class _Doc:
    def __init__(self):
        self.Objects = []
        self.Name = "Unnamed"
        self.FileName = ""
        self._by_name = {}

    def addObject(self, typ, name):
        obj = _Obj(name)
        obj.TypeId = typ
        self.Objects.append(obj)
        self._by_name[name] = obj
        return obj

    def getObject(self, name):
        return self._by_name.get(name)

    def recompute(self):
        return None


def test_run_analyze_writes_report_with_injected_solvers(monkeypatch, tmp_path):
    def fake_analyze(cfg, **_kwargs):
        return {
            "CL": 0.77,
            "CD": 0.03,
            "CM": -0.02,
            "CLalpha": 4.8,
            "Cmalpha": -0.7,
            "Re": 40000.0,
            "V_loaf": 7.1,
            "P_hover": 17.0,
            "P_cruise": 3.5,
            "source": "NeuralFoil",
            "PitchUnstable": False,
            "hover": {"source": "momentum-theory"},
            "geometry_source": cfg["geometry_source"],
            "airfoil": cfg["airfoil"],
        }

    monkeypatch.setattr("AeroSolvers.analyze", fake_analyze)
    monkeypatch.setattr(
        "AeroAirfoil.load_airfoil_coordinates",
        lambda name: ([[1.0, 0.0], [0.0, 0.07], [1.0, 0.0]], "bundled:e63"),
    )
    doc = _Doc()
    result = VibeCADAero.run_analyze(doc)
    assert result["ok"] is True
    assert result["CL"] == 0.77
    assert doc.getObject("AeroReport") is not None
    assert result["source"] == "NeuralFoil"


def test_inferred_geometry_is_not_persisted_onto_aeroconfig(monkeypatch):
    def fake_analyze(cfg, **_kwargs):
        return {
            "CL": 0.77,
            "CD": 0.03,
            "CM": -0.02,
            "CLalpha": 4.8,
            "Cmalpha": -0.7,
            "Re": 40000.0,
            "V_loaf": 7.1,
            "P_hover": 17.0,
            "P_cruise": 3.5,
            "source": "NeuralFoil",
            "PitchUnstable": False,
            "hover": {"source": "momentum-theory"},
            "geometry_source": cfg["geometry_source"],
            "airfoil": cfg["airfoil"],
            "span_mm": cfg["span_mm"],
            "chord_mm": cfg["chord_mm"],
        }

    monkeypatch.setattr("AeroSolvers.analyze", fake_analyze)
    monkeypatch.setattr(
        "AeroAirfoil.load_airfoil_coordinates",
        lambda name: ([[1.0, 0.0], [0.0, 0.07], [1.0, 0.0]], "bundled:e63"),
    )

    class _BBox:
        def __init__(self):
            self.XMin, self.XMax = 0.0, 295.0
            self.YMin, self.YMax = -820.0, 820.0
            self.ZMin, self.ZMax = 0.0, 12.0
            self.XLength, self.YLength, self.ZLength = 295.0, 1640.0, 12.0

    wing = _Obj("lower_wing")
    wing.Shape = type("S", (), {"BoundBox": _BBox()})()
    doc = _Doc()
    doc.Objects.append(wing)
    doc._by_name["lower_wing"] = wing

    result = VibeCADAero.run_analyze(doc, spreadsheet=True)
    assert result["ok"] is True
    assert result["span_mm"] == 500.0
    aero = doc.getObject("AeroConfig")
    if aero is not None:
        assert getattr(aero, "span_mm", 500.0) == 500.0
        assert getattr(aero, "chord_mm", 90.0) == 90.0


def test_run_analyze_returns_install_hint_instead_of_raising(monkeypatch):
    def boom(*_args, **_kwargs):
        raise VibeCADAero.AeroDependencyError(
            "neuralfoil is not installed. Install it into VibeCAD's bundled Python:\n"
            r'  "C:\VibeCAD\bin\python.exe" -m pip install neuralfoil'
        )

    monkeypatch.setattr("AeroSolvers.analyze", boom)
    monkeypatch.setattr(
        "AeroAirfoil.load_airfoil_coordinates",
        lambda name: ([[1.0, 0.0], [0.0, 0.0]], "bundled:e63"),
    )
    result = VibeCADAero.run_analyze(_Doc())
    assert result["ok"] is False
    assert "pip install neuralfoil" in result["error"]
    assert "python.exe" in result["error"]
