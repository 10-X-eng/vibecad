# SPDX-License-Identifier: LGPL-2.1-or-later

"""AeroReport FeaturePython / spreadsheet fields."""

from __future__ import annotations

import AeroResults as results


class _Obj:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.Proxy = None
        self.ViewObject = None
        self._props = {}

    def addProperty(self, typ, name, group="", doc="", **_kwargs):
        setattr(self, name, None)
        self._props[name] = typ
        return self


class _Sheet(_Obj):
    def __init__(self, name):
        super().__init__(name)
        self.cells = {}

    def set(self, cell, value):
        self.cells[cell] = value


class _Doc:
    def __init__(self):
        self.Objects = []
        self.Name = "Unnamed"
        self.FileName = ""
        self.recomputed = False
        self._by_name = {}

    def addObject(self, typ, name):
        obj = _Sheet(name) if "Spreadsheet" in typ else _Obj(name)
        obj.TypeId = typ
        self.Objects.append(obj)
        self._by_name[name] = obj
        return obj

    def getObject(self, name):
        return self._by_name.get(name)

    def recompute(self):
        self.recomputed = True


class _FreeCADLikeDoc(_Doc):
    """Reject setattr of a name that getObject already owns.

    Real FreeCAD raises DocumentPy::setCustomAttributes when a string is
    assigned over a named object. getattr does not always return that
    object, so helpers must not fall through to setattr just because
    getattr missed.
    """

    def __getattr__(self, name):
        raise AttributeError(name)

    def __setattr__(self, name, value):
        obj = self.__dict__.get("_by_name", {}).get(name)
        if obj is not None and value is not obj:
            raise RuntimeError(
                f"'Document' object attribute '{name}' must not be set this way"
            )
        super().__setattr__(name, value)


def _payload():
    return {
        "CL": 0.81,
        "CD": 0.037,
        "CM": -0.021,
        "CLalpha": 5.1,
        "Cmalpha": 0.02,
        "Re": 42000.0,
        "V_loaf": 7.4,
        "P_hover": 18.2,
        "P_cruise": 4.1,
        "source": "AeroBuildup",
        "PitchUnstable": True,
        "hover": {"source": "momentum-theory"},
        "geometry_source": "defaults",
        "airfoil": "e63",
        "Corrections": [
            "Grew the horizontal tail span from 150 mm to 180 mm.",
            "Lengthened the boom from 315 mm to 378 mm.",
        ],
        "RepairPasses": 1,
    }


def test_featurepython_report_exposes_required_fields():
    doc = _Doc()
    obj = results.write_report(doc, _payload())
    assert obj.Name == "AeroReport"
    for field in (
        "CL",
        "CD",
        "CM",
        "CLalpha",
        "Cmalpha",
        "Re",
        "V_loaf",
        "P_hover",
        "P_cruise",
        "Source",
        "PitchUnstable",
        "Corrections",
        "RepairPasses",
    ):
        assert hasattr(obj, field)
    assert obj.CL == 0.81
    assert obj.Source == "AeroBuildup"
    assert obj.PitchUnstable is True
    assert obj.V_loaf == 7.4
    assert "Grew the horizontal tail span" in obj.Corrections
    assert obj.RepairPasses == 1


def test_optional_spreadsheet_and_markdown_objects():
    doc = _Doc()
    written = results.write_report(doc, _payload(), spreadsheet=True, markdown=True)
    assert doc.getObject("AeroReport") is written
    sheet = doc.getObject("AeroSpreadsheet")
    assert sheet is not None
    assert any(value == "CL" for value in sheet.cells.values())
    text = doc.getObject("AeroReportMarkdown")
    assert text is not None
    assert "momentum-theory" in text.Text
    assert "AeroBuildup" in text.Text


def test_spreadsheet_type_error_does_not_fail_report():
    class _NoSheetDoc(_Doc):
        def addObject(self, typ, name):
            if "Spreadsheet" in typ:
                raise RuntimeError(
                    "Document::addObject: 'Spreadsheet::Sheet' is not a document object type"
                )
            return super().addObject(typ, name)

    doc = _NoSheetDoc()
    written = results.write_report(doc, _payload(), spreadsheet=True, markdown=True)
    assert written.Name == "AeroReport"
    assert written.CL == 0.81
    assert doc.getObject("AeroSpreadsheet") is None
    assert doc.getObject("AeroReportMarkdown") is not None


def test_spreadsheet_module_is_loaded_before_adding_sheet(monkeypatch):
    calls = []

    def fake_load():
        calls.append("load")
        return True

    monkeypatch.setattr(results, "ensure_spreadsheet_type", fake_load)
    doc = _Doc()
    results.write_report(doc, _payload(), spreadsheet=True)
    assert calls == ["load"]
    assert doc.getObject("AeroSpreadsheet") is not None


def test_jsbsim_path_is_stored_on_document_and_report():
    doc = _Doc()
    obj = results.write_report(doc, _payload(), jsbsim_path="/tmp/vibecad_aero.xml")
    assert obj.JSBSimPlantPath == "/tmp/vibecad_aero.xml"
    assert getattr(doc, "JSBSimPlantPath") == "/tmp/vibecad_aero.xml"


def test_report_stores_plant_geometry_and_boot_error():
    doc = _Doc()
    payload = _payload()
    payload.update(
        {
            "span_m": 0.8,
            "chord_m": 0.12,
            "span_mm": 800.0,
            "chord_mm": 120.0,
            "reference_area_m2": 0.192,
            "mass_kg": 0.25,
            "alpha_deg": 3.5,
            "xyz_ref": [0.03, 0.0, 0.08],
        }
    )
    obj = results.write_report(
        doc,
        payload,
        jsbsim_path="/tmp/custom_aero.xml",
        jsbsim_boot_error="jsbsim.FGFDMExec.run_ic returned false.",
    )
    assert obj.reference_area_m2 == 0.192
    assert obj.span_m == 0.8
    assert obj.chord_m == 0.12
    assert obj.mass_kg == 0.25
    assert obj.alpha_deg == 3.5
    assert obj.xyz_ref_x == 0.03
    assert obj.xyz_ref_y == 0.0
    assert obj.xyz_ref_z == 0.08
    assert obj.JSBSimBootError == "jsbsim.FGFDMExec.run_ic returned false."


def test_report_writes_corrections_and_assistant_json():
    doc = _Doc()
    obj = results.write_report(doc, _payload())
    assert hasattr(obj, "Corrections")
    assert "Grew the horizontal tail span" in obj.Corrections
    assert obj.RepairPasses == 1
    assistant = doc.getObject("AeroAssistantJson")
    assert assistant is not None
    text = assistant.Text
    assert '"CL": 0.81' in text or '"CL":0.81' in text
    assert "PitchUnstable" in text
    assert "corrections" in text
    assert "Grew the horizontal tail span" in text
    raw = getattr(doc, "AeroAssistantJsonText", "")
    if raw:
        assert "0.81" in str(raw)
        assert "PitchUnstable" in str(raw)


def test_report_preserves_freecad_named_assistant_object_without_overwriting_it():
    doc = _FreeCADLikeDoc()

    obj = results.write_report(doc, _payload())

    assistant = doc.getObject("AeroAssistantJson")
    assert obj.Name == "AeroReport"
    assert assistant is not None
    assert '"CL": 0.81' in assistant.Text
    assert "AeroAssistantJson" not in doc.__dict__
    assert doc.recomputed is True
