# SPDX-License-Identifier: LGPL-2.1-or-later

"""Write AeroReport FeaturePython, optional spreadsheet, and markdown."""

from __future__ import annotations

import json
from typing import Any

REPORT_NAME = "AeroReport"
SHEET_NAME = "AeroSpreadsheet"
MARKDOWN_NAME = "AeroReportMarkdown"
ASSISTANT_JSON_NAME = "AeroAssistantJson"

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
    ("JSBSimBootError", "App::PropertyString", "JSBSim boot error, empty when loaded"),
    ("Notes", "App::PropertyString", "Solver notes"),
    ("Corrections", "App::PropertyString", "Plain-language geometry repairs from Analyze"),
    ("RepairPasses", "App::PropertyInteger", "Number of pitch-stability repair passes"),
    ("span_mm", "App::PropertyFloat", "Span used for the solve, mm"),
    ("chord_mm", "App::PropertyFloat", "Chord used for the solve, mm"),
    ("span_m", "App::PropertyFloat", "Span used for the solve, m"),
    ("chord_m", "App::PropertyFloat", "Chord used for the solve, m"),
    ("reference_area_m2", "App::PropertyFloat", "Biplane reference area, m^2"),
    ("mass_kg", "App::PropertyFloat", "Mass used for the solve, kg"),
    ("alpha_deg", "App::PropertyFloat", "Angle of attack used for the solve, deg"),
    ("xyz_ref_x", "App::PropertyFloat", "Aero reference x, m"),
    ("xyz_ref_y", "App::PropertyFloat", "Aero reference y, m"),
    ("xyz_ref_z", "App::PropertyFloat", "Aero reference z, m"),
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
    jsbsim_boot_error: str | None = None,
) -> Any:
    obj = _get_or_create(doc, "App::FeaturePython", REPORT_NAME)
    if getattr(obj, "Proxy", None) is None:
        try:
            AeroReportFeature(obj)
        except Exception:
            _add_result_properties(obj)
    else:
        _add_result_properties(obj)
    assistant = assistant_payload(payload, jsbsim_path=jsbsim_path)
    payload["corrections"] = list(assistant.get("corrections") or [])
    _apply_payload(obj, payload, jsbsim_path, jsbsim_boot_error)
    _write_assistant_json(doc, assistant)
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
        f"- Repair passes: {payload.get('RepairPasses') or 0}",
    ]
    corrections = corrections_for(payload)
    if corrections:
        lines.append("- Corrections:")
        for item in corrections:
            lines.append(f"  - {item}")
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
        ("Corrections", "\n".join(corrections_for(payload))),
    ]
    if jsbsim_path:
        rows.append(("JSBSimPlantPath", jsbsim_path))
    return rows


def _apply_payload(
    obj: Any,
    payload: dict[str, Any],
    jsbsim_path: str | None,
    jsbsim_boot_error: str | None = None,
) -> None:
    hover = payload.get("hover") or {}
    ref = _xyz_ref_components(payload)
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
        "JSBSimBootError": (
            jsbsim_boot_error
            if jsbsim_boot_error is not None
            else payload.get("jsbsim_boot_error") or ""
        ),
        "Notes": "Hover is momentum-theory, not CFD.",
        "Corrections": "\n".join(corrections_for(payload)),
        "RepairPasses": int(payload.get("RepairPasses") or 0),
        "span_mm": payload.get("span_mm"),
        "chord_mm": payload.get("chord_mm"),
        "span_m": payload.get("span_m"),
        "chord_m": payload.get("chord_m"),
        "reference_area_m2": payload.get("reference_area_m2"),
        "mass_kg": payload.get("mass_kg"),
        "alpha_deg": payload.get("alpha_deg"),
        "xyz_ref_x": ref[0],
        "xyz_ref_y": ref[1],
        "xyz_ref_z": ref[2],
    }
    for name, value in mapping.items():
        try:
            setattr(obj, name, value)
        except Exception:
            pass


def corrections_for(payload: dict[str, Any]) -> list[str]:
    """Return a short, deterministic change list from one solve."""

    existing = _corrections_list(payload.get("corrections"))
    if not existing:
        existing = _corrections_list(payload.get("Corrections"))
    if existing:
        return existing
    if payload.get("PitchUnstable"):
        return [
            "PitchUnstable: Cmα > 0. Increase decalage, add tail volume, "
            "or move CG forward until Cmα < 0."
        ]
    return ["Pitch stable (Cmα ≤ 0). No pitch-stiffness correction required."]


def assistant_payload(
    payload: dict[str, Any],
    *,
    jsbsim_path: str | None = None,
) -> dict[str, Any]:
    """Bounded JSON the signed-in in-app Grok turn can reuse."""

    corrections = corrections_for(payload)
    hover = payload.get("hover") or {}
    hover_source = ""
    if isinstance(hover, dict):
        hover_source = str(hover.get("source") or "")
    result: dict[str, Any] = {
        "CL": payload.get("CL"),
        "CD": payload.get("CD"),
        "CM": payload.get("CM"),
        "CLalpha": payload.get("CLalpha"),
        "Cmalpha": payload.get("Cmalpha"),
        "PitchUnstable": bool(payload.get("PitchUnstable")),
        "source": payload.get("source"),
        "hover_source": hover_source or "momentum-theory",
        "evidence_state": payload.get("evidence_state") or "model_unqualified",
        "claim_ceiling": payload.get("claim_ceiling") or "not_airworthy",
        "not_airworthy": True,
        "corrections": corrections,
        "Corrections": corrections,
        "RepairPasses": int(payload.get("RepairPasses") or 0),
        "changes": payload.get("changes") or [],
        "user_message": payload.get("user_message") or "",
        "flight_card": payload.get("flight_card") or {},
    }
    if jsbsim_path:
        result["jsbsim_path"] = jsbsim_path
    return result


def format_human_report(payload: dict[str, Any], title: str = "Aero Analyze") -> str:
    """Human-readable Analyze text for the dialog and in-app Grok chat."""

    unstable = (
        "PITCH UNSTABLE (Cmα > 0)" if payload.get("PitchUnstable") else "pitch stable"
    )
    source = payload.get("source") or ""
    heading = f"{title} ({source})" if source else title
    lines = [
        heading,
        f"CL={payload.get('CL')}  CD={payload.get('CD')}  CM={payload.get('CM')}",
        f"CLα={payload.get('CLalpha')}  Cmα={payload.get('Cmalpha')}  {unstable}",
        f"Re={payload.get('Re')}  V_loaf={payload.get('V_loaf')} m/s",
        f"P_hover={payload.get('P_hover')} W (momentum-theory)",
        f"P_cruise={payload.get('P_cruise')} W (η=0.65)",
        f"Airfoil={payload.get('airfoil')} from {payload.get('airfoil_source')}",
        "Claim ceiling: not_airworthy (not flight test, not CFD).",
    ]
    if payload.get("jsbsim_path"):
        lines.append(f"JSBSim: {payload['jsbsim_path']}")
        if payload.get("jsbsim_boot_error"):
            lines.append(f"JSBSim boot: {payload['jsbsim_boot_error']}")
    corrections = corrections_for(payload)
    if corrections:
        lines.append("Corrections:")
        lines.extend(f"- {item}" for item in corrections)
    message = str(payload.get("user_message") or "").strip()
    if message:
        lines.append("")
        lines.append(message)
    return "\n".join(lines)


def _write_assistant_json(doc: Any, assistant: dict[str, Any]) -> Any:
    encoded = json.dumps(assistant, ensure_ascii=True, indent=2)
    try:
        obj = _get_or_create(doc, "App::TextDocument", ASSISTANT_JSON_NAME)
        obj.Text = encoded
    except Exception:
        obj = None
    # Never setattr doc.AeroAssistantJson: that name is the TextDocument
    # (DocumentPy::setCustomAttributes). Keep a distinctly named string
    # for readers that still want a document-level copy.
    _set_doc_attr(doc, "AeroAssistantJsonText", encoded)
    return obj


def _corrections_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [line.strip().lstrip("- ") for line in text.splitlines() if line.strip()]


def _xyz_ref_components(payload: dict[str, Any]) -> tuple[Any, Any, Any]:
    raw = payload.get("xyz_ref")
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return raw[0], raw[1], raw[2]
    if raw is not None and all(hasattr(raw, axis) for axis in ("x", "y", "z")):
        return raw.x, raw.y, raw.z
    return (
        payload.get("xyz_ref_x"),
        payload.get("xyz_ref_y"),
        payload.get("xyz_ref_z"),
    )


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
    getter = getattr(doc, "getObject", None)
    named_object = getter(name) if callable(getter) else None
    if named_object is not None:
        return
    try:
        current = getattr(doc, name)
    except (AttributeError, RuntimeError):
        current = None
    if current is not None and getattr(current, "Name", None) == name:
        return

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
