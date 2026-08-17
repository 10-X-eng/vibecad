# SPDX-License-Identifier: LGPL-2.1-or-later

"""Write AeroReport FeaturePython, optional spreadsheet, and markdown."""

from __future__ import annotations

from typing import Any

REPORT_NAME = "AeroReport"
SHEET_NAME = "AeroSpreadsheet"
MARKDOWN_NAME = "AeroReportMarkdown"

_FIELDS = (
    ("CL", "App::PropertyFloat", "Lift coefficient"),
    ("CD", "App::PropertyFloat", "Drag coefficient"),
    ("CM", "App::PropertyFloat", "Pitching moment coefficient"),
    ("CLalpha", "App::PropertyFloat", "Lift slope per radian"),
    ("Cmalpha", "App::PropertyFloat", "Pitch stiffness per radian"),
    ("Re", "App::PropertyFloat", "Reynolds number at loaf speed"),
    ("V_loaf", "App::PropertyFloat", "Loaf speed in m/s"),
    ("P_hover", "App::PropertyFloat", "Hover power in W (momentum-theory)"),
    ("P_cruise", "App::PropertyFloat", "Cruise power in W (D*V/0.65)"),
    ("Source", "App::PropertyString", "Coefficient source"),
    ("PitchUnstable", "App::PropertyBool", "True when Cmalpha > 0"),
    ("HoverSource", "App::PropertyString", "Hover model label"),
    ("Airfoil", "App::PropertyString", "Section name"),
    ("GeometrySource", "App::PropertyString", "How geometry was resolved"),
    ("JSBSimPlantPath", "App::PropertyString", "Exported JSBSim XML path"),
    ("Notes", "App::PropertyString", "Solver notes"),
    ("span_mm", "App::PropertyFloat", "Span used for the solve, mm"),
    ("chord_mm", "App::PropertyFloat", "Chord used for the solve, mm"),
    ("span_m", "App::PropertyFloat", "Span used for the solve, m"),
    ("chord_m", "App::PropertyFloat", "Chord used for the solve, m"),
    ("reference_area_m2", "App::PropertyFloat", "Biplane reference area, m^2"),
)


class AeroReportFeature:
    def __init__(self, obj: Any):
        _add_result_properties(obj)
        obj.Proxy = self

    def execute(self, _obj: Any) -> None:
        return None

    def dumps(self) -> None:
        return None

    def loads(self, _state: Any) -> None:
        return None


class AeroReportViewProvider:
    def __init__(self, view: Any):
        view.Proxy = self

    def getIcon(self) -> str:
        try:
            import AeroIcons

            return AeroIcons.aero_icon_path()
        except Exception:
            return _AERO_ICON

    def dumps(self) -> None:
        return None

    def loads(self, _state: Any) -> None:
        return None


def write_report(
    doc: Any,
    payload: dict[str, Any],
    *,
    spreadsheet: bool = False,
    markdown: bool = False,
    jsbsim_path: str | None = None,
) -> Any:
    obj = _get_or_create(doc, "App::FeaturePython", REPORT_NAME)
    if getattr(obj, "Proxy", None) is None:
        try:
            AeroReportFeature(obj)
        except Exception:
            _add_result_properties(obj)
    else:
        _add_result_properties(obj)
    _apply_payload(obj, payload, jsbsim_path)
    view = getattr(obj, "ViewObject", None)
    if view is not None and getattr(view, "Proxy", None) is None:
        try:
            AeroReportViewProvider(view)
        except Exception:
            pass

    if jsbsim_path:
        _set_doc_attr(doc, "JSBSimPlantPath", jsbsim_path)

    if spreadsheet:
        try:
            _write_spreadsheet(doc, payload, jsbsim_path)
        except Exception:
            pass
    if markdown:
        _write_markdown(doc, payload, jsbsim_path)

    recompute = getattr(doc, "recompute", None)
    if callable(recompute):
        recompute()
    return obj


def format_markdown(payload: dict[str, Any], jsbsim_path: str | None = None) -> str:
    hover = payload.get("hover") or {}
    lines = [
        "# VibeCAD Aero report",
        "",
        f"- Source: {payload.get('source')}",
        f"- Airfoil: {payload.get('airfoil')}",
        f"- Geometry: {payload.get('geometry_source')}",
        f"- CL: {payload.get('CL')}",
        f"- CD: {payload.get('CD')}",
        f"- CM: {payload.get('CM')}",
        f"- CLα (per rad): {payload.get('CLalpha')}",
        f"- Cmα (per rad): {payload.get('Cmalpha')}",
        f"- Re: {payload.get('Re')}",
        f"- V_loaf (m/s): {payload.get('V_loaf')}",
        f"- P_hover (W): {payload.get('P_hover')} ({hover.get('source', 'momentum-theory')})",
        f"- P_cruise (W): {payload.get('P_cruise')} (prop η = 0.65)",
        f"- Pitch unstable: {payload.get('PitchUnstable')}",
    ]
    if jsbsim_path:
        lines.append(f"- JSBSim plant: `{jsbsim_path}`")
    lines.append("")
    lines.append("Hover power is actuator-disk / momentum-theory, not CFD.")
    return "\n".join(lines)


def ensure_spreadsheet_type() -> bool:
    """Register ``Spreadsheet::Sheet`` so addObject does not crash Analyze."""

    try:
        import Spreadsheet  # noqa: F401
        return True
    except Exception:
        pass
    try:
        import FreeCAD

        loader = getattr(FreeCAD, "loadModule", None)
        if callable(loader):
            loader("Spreadsheet")
            return True
    except Exception:
        pass
    return False


def _write_spreadsheet(doc: Any, payload: dict[str, Any], jsbsim_path: str | None) -> Any:
    ensure_spreadsheet_type()
    sheet = _get_or_create(doc, "Spreadsheet::Sheet", SHEET_NAME)
    rows = _row_values(payload, jsbsim_path)
    setter = getattr(sheet, "set", None)
    if not callable(setter):
        sheet.cells = {f"A{i}": name for i, (name, _) in enumerate(rows, start=1)}
        return sheet
    for index, (name, value) in enumerate(rows, start=1):
        setter(f"A{index}", name)
        setter(f"B{index}", value)
    return sheet


def _write_markdown(doc: Any, payload: dict[str, Any], jsbsim_path: str | None) -> Any:
    obj = _get_or_create(doc, "App::TextDocument", MARKDOWN_NAME)
    obj.Text = format_markdown(payload, jsbsim_path)
    return obj


def _row_values(payload: dict[str, Any], jsbsim_path: str | None) -> list[tuple[str, Any]]:
    hover = payload.get("hover") or {}
    rows = [
        ("CL", payload.get("CL")),
        ("CD", payload.get("CD")),
        ("CM", payload.get("CM")),
        ("CLalpha", payload.get("CLalpha")),
        ("Cmalpha", payload.get("Cmalpha")),
        ("Re", payload.get("Re")),
        ("V_loaf", payload.get("V_loaf")),
        ("P_hover", payload.get("P_hover")),
        ("P_cruise", payload.get("P_cruise")),
        ("Source", payload.get("source")),
        ("PitchUnstable", payload.get("PitchUnstable")),
        ("HoverSource", hover.get("source", "momentum-theory")),
        ("Airfoil", payload.get("airfoil")),
    ]
    if jsbsim_path:
        rows.append(("JSBSimPlantPath", jsbsim_path))
    return rows


def _apply_payload(obj: Any, payload: dict[str, Any], jsbsim_path: str | None) -> None:
    hover = payload.get("hover") or {}
    mapping = {
        "CL": payload.get("CL"),
        "CD": payload.get("CD"),
        "CM": payload.get("CM"),
        "CLalpha": payload.get("CLalpha"),
        "Cmalpha": payload.get("Cmalpha"),
        "Re": payload.get("Re"),
        "V_loaf": payload.get("V_loaf"),
        "P_hover": payload.get("P_hover"),
        "P_cruise": payload.get("P_cruise"),
        "Source": payload.get("source"),
        "PitchUnstable": bool(payload.get("PitchUnstable")),
        "HoverSource": hover.get("source", "momentum-theory"),
        "Airfoil": payload.get("airfoil"),
        "GeometrySource": payload.get("geometry_source"),
        "JSBSimPlantPath": jsbsim_path or "",
        "Notes": "Hover is momentum-theory, not CFD.",
        "span_mm": payload.get("span_mm"),
        "chord_mm": payload.get("chord_mm"),
        "span_m": payload.get("span_m"),
        "chord_m": payload.get("chord_m"),
        "reference_area_m2": payload.get("reference_area_m2"),
    }
    for name, value in mapping.items():
        try:
            setattr(obj, name, value)
        except Exception:
            pass


def _add_result_properties(obj: Any) -> None:
    for name, typ, doc in _FIELDS:
        if hasattr(obj, name) and name in getattr(obj, "_props", {name: True}):
            continue
        adder = getattr(obj, "addProperty", None)
        if not callable(adder):
            if not hasattr(obj, name):
                setattr(obj, name, None)
            continue
        try:
            adder(typ, name, "Aero", doc)
        except Exception:
            if not hasattr(obj, name):
                setattr(obj, name, None)


def _get_or_create(doc: Any, typ: str, name: str) -> Any:
    getter = getattr(doc, "getObject", None)
    obj = getter(name) if callable(getter) else None
    if obj is not None:
        return obj
    created = doc.addObject(typ, name)
    created.Label = name
    return created


def _set_doc_attr(doc: Any, name: str, value: Any) -> None:
    adder = getattr(doc, "addProperty", None)
    if callable(adder) and not hasattr(doc, name):
        try:
            adder("App::PropertyString", name, "Aero", name)
        except Exception:
            pass
    setattr(doc, name, value)


_AERO_ICON = """
/* XPM */
static const char * vibecad_aero_xpm[] = {
"16 16 3 1",
"  c None",
". c #1B4F72",
"+ c #5DADE2",
"                ",
"                ",
"         ++     ",
"       ++++     ",
"     +++++.     ",
"   +++++..      ",
" +++++...       ",
"+++++...        ",
" +++...         ",
"  ++..          ",
"   +.           ",
"                ",
"  ..........    ",
"                ",
"                ",
"                "};
"""
