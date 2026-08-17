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
    ):
        assert hasattr(obj, field)
    assert obj.CL == 0.81
    assert obj.Source == "AeroBuildup"
    assert obj.PitchUnstable is True
    assert obj.V_loaf == 7.4


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


def test_jsbsim_path_is_stored_on_document_and_report():
    doc = _Doc()
    obj = results.write_report(doc, _payload(), jsbsim_path="/tmp/vibecad_aero.xml")
    assert obj.JSBSimPlantPath == "/tmp/vibecad_aero.xml"
    assert getattr(doc, "JSBSimPlantPath") == "/tmp/vibecad_aero.xml"
