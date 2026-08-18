# SPDX-License-Identifier: LGPL-2.1-or-later

"""Agent-control import surface: VibeCADAero.run_analyze(doc)."""

from __future__ import annotations

from pathlib import Path

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


def test_run_analyze_defaults_to_repair_true():
    import inspect

    assert inspect.signature(VibeCADAero.run_analyze).parameters["repair"].default is True
    assert inspect.signature(VibeCADAero.run_section).parameters.get("repair") is None
    source = Path(__file__).resolve().parents[1].joinpath("VibeCADAero.py").read_text(
        encoding="utf-8"
    )
    assert "run_section_solve=True" in source
    assert "run_vlm_solve=False" in source


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
    assert result["changes"] == []
    assert result["RepairPasses"] == 0
    assert "user_message" in result
    assert doc.getObject("AeroAssistantJson") is not None
    assert "Pitch stable" in result["corrections"][0]
    assert "0.77" in str(getattr(doc, "AeroAssistantJson", ""))


def test_run_analyze_repairs_when_pitch_unstable(monkeypatch):
    calls = {"n": 0}

    def fake_analyze(cfg, **_kwargs):
        calls["n"] += 1
        unstable = calls["n"] == 1
        return {
            "CL": 0.77,
            "CD": 0.03,
            "CM": -0.02,
            "CLalpha": 4.8,
            "Cmalpha": 0.6 if unstable else -0.4,
            "Re": 40000.0,
            "V_loaf": 7.1,
            "P_hover": 17.0,
            "P_cruise": 3.5,
            "source": "VLM",
            "PitchUnstable": unstable,
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
    assert calls["n"] == 2
    assert result["PitchUnstable"] is False
    assert result["RepairPasses"] == 1
    assert result["changes"]
    assert result["user_message"]
    report = doc.getObject("AeroReport")
    assert report.RepairPasses == 1
    assert report.Corrections
    assert getattr(doc, "AeroAssistantJson", "")
    assert "tail" in result["user_message"].lower() or "boom" in result["user_message"].lower()


def test_run_section_and_vlm_stay_report_only(monkeypatch):
    calls = {"n": 0}

    def fake_analyze(cfg, **_kwargs):
        calls["n"] += 1
        return {
            "CL": 0.6,
            "CD": 0.04,
            "CM": 0.0,
            "CLalpha": 5.0,
            "Cmalpha": 0.5,
            "Re": 30000.0,
            "V_loaf": 8.0,
            "P_hover": 17.0,
            "P_cruise": 3.0,
            "source": "NeuralFoil",
            "PitchUnstable": True,
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
    section = VibeCADAero.run_section(doc)
    vlm = VibeCADAero.run_vlm(doc)
    assert section["ok"] is True
    assert vlm["ok"] is True
    assert calls["n"] == 2
    assert section["RepairPasses"] == 0
    assert vlm["RepairPasses"] == 0
    assert section["changes"] == []
    assert vlm["changes"] == []


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


class _ReportObj:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.Proxy = None
        self.ViewObject = None
        self._props = {}

    def addProperty(self, typ, name, group="", doc="", **_kwargs):
        if not hasattr(self, name):
            setattr(self, name, None)
        self._props[name] = typ
        return self


class _ReportDoc:
    def __init__(self):
        self.Objects = []
        self.Name = "Unnamed"
        self.FileName = ""
        self._by_name = {}

    def addObject(self, typ, name):
        obj = _ReportObj(name)
        obj.TypeId = typ
        self.Objects.append(obj)
        self._by_name[name] = obj
        return obj

    def getObject(self, name):
        return self._by_name.get(name)

    def recompute(self):
        return None


def test_export_jsbsim_from_report_preserves_custom_plant_geometry(tmp_path):
    import AeroResults

    doc = _ReportDoc()
    doc.FileName = str(tmp_path / "custom.FCStd")
    AeroResults.write_report(
        doc,
        {
            "CL": 1.1,
            "CD": 0.05,
            "CM": -0.04,
            "CLalpha": 6.0,
            "Cmalpha": -0.5,
            "Re": 50000.0,
            "V_loaf": 6.0,
            "P_hover": 20.0,
            "P_cruise": 2.0,
            "source": "AeroBuildup",
            "PitchUnstable": False,
            "hover": {"source": "momentum-theory"},
            "airfoil": "e63",
            "geometry_source": "AeroConfig",
            "span_m": 0.8,
            "chord_m": 0.12,
            "span_mm": 800.0,
            "chord_mm": 120.0,
            "reference_area_m2": 0.192,
            "mass_kg": 0.25,
            "alpha_deg": 3.5,
            "xyz_ref": [0.03, 0.0, 0.08],
        },
    )
    written = VibeCADAero.export_jsbsim(doc)
    assert written["ok"] is True
    xml = Path(written["fdm_path"]).read_text(encoding="utf-8")
    assert '<wingarea unit="M2">0.192000</wingarea>' in xml
    assert '<wingspan unit="M">0.800000</wingspan>' in xml
    assert '<chord unit="M">0.120000</chord>' in xml
    assert "<emptywt unit=\"KG\">0.250000</emptywt>" in xml
    assert "<x>0.030000</x>" in xml
    assert "<z>0.080000</z>" in xml
    assert '<wingarea unit="M2">0.090000</wingarea>' not in xml
    assert "<emptywt unit=\"KG\">0.149600</emptywt>" not in xml


def test_export_jsbsim_merges_resolved_config_when_report_omits_geometry(tmp_path):
    import AeroResults

    doc = _ReportDoc()
    doc.FileName = str(tmp_path / "merged.FCStd")
    config = doc.addObject("App::FeaturePython", "AeroConfig")
    config.span_mm = 400.0
    config.chord_mm = 80.0
    config.auw_g = 200.0
    config.alpha_deg = 2.0
    config.gap_c = 1.4
    config.airfoil = "e63"
    AeroResults.write_report(
        doc,
        {
            "CL": 0.9,
            "CD": 0.04,
            "CM": -0.02,
            "CLalpha": 5.0,
            "Cmalpha": 0.0,
            "source": "NeuralFoil",
            "hover": {"source": "momentum-theory"},
            "airfoil": "e63",
        },
    )
    report = doc.getObject("AeroReport")
    report.span_m = None
    report.chord_m = None
    report.span_mm = None
    report.chord_mm = None
    report.reference_area_m2 = None
    written = VibeCADAero.export_jsbsim(doc)
    xml = Path(written["fdm_path"]).read_text(encoding="utf-8")
    assert '<wingspan unit="M">0.400000</wingspan>' in xml
    assert '<chord unit="M">0.080000</chord>' in xml
    assert '<wingarea unit="M2">0.064000</wingarea>' in xml
    assert "<emptywt unit=\"KG\">0.200000</emptywt>" in xml


def test_export_jsbsim_stores_boot_error_instead_of_claiming_loaded(tmp_path, monkeypatch):
    import AeroJSBSim
    import AeroResults

    def fake_write(results, output_dir=None, load_fn=None):
        dest = Path(output_dir)
        dest.mkdir(parents=True, exist_ok=True)
        fdm_path = dest / "vibecad_aero" / "vibecad_aero.xml"
        fdm_path.parent.mkdir(parents=True, exist_ok=True)
        fdm_path.write_text("<fdm_config/>", encoding="utf-8")
        return {
            "fdm_path": str(fdm_path),
            "engine_path": str(dest / "engine" / "electric.xml"),
            "thruster_path": str(dest / "engine" / "direct.xml"),
            "model": "vibecad_aero",
            "loaded": False,
            "boot_error": "jsbsim.FGFDMExec.run_ic returned false.",
            "message": "jsbsim.FGFDMExec.run_ic returned false.",
            "output_dir": str(dest),
        }

    monkeypatch.setattr(AeroJSBSim, "write_plant", fake_write)
    doc = _ReportDoc()
    doc.FileName = str(tmp_path / "boot.FCStd")
    AeroResults.write_report(
        doc,
        {
            "CL": 0.8,
            "CD": 0.04,
            "CM": -0.02,
            "source": "NeuralFoil",
            "hover": {"source": "momentum-theory"},
            "span_m": 0.5,
            "chord_m": 0.09,
            "reference_area_m2": 0.09,
            "mass_kg": 0.1496,
        },
    )
    written = VibeCADAero.export_jsbsim(doc)
    assert written["loaded"] is False
    assert "run_ic" in written["boot_error"]
    report = doc.getObject("AeroReport")
    assert report.JSBSimBootError == "jsbsim.FGFDMExec.run_ic returned false."
    assert Path(written["fdm_path"]).is_file()
