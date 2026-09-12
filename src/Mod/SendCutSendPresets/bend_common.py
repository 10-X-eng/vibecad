# SPDX-License-Identifier: MIT
"""Shared constants/helpers with no internal FreeCAD-addon imports (avoid cycles)."""

from __future__ import annotations

import os
import re

import FreeCAD as App

INCH_TO_MM = 25.4

RADIUS_PROP_NAMES = (
    "radius",
    "Radius",
    "bendRadius",
    "BendRadius",
    "bend_radius",
    "Bend_Radius",
)
LENGTH_PROP_NAMES = (
    "length",
    "Length",
    "flangeLength",
    "FlangeLength",
    "flange_length",
    "height",  # some wall tools use height as flange length
    "Height",
)
KFACTOR_PROP_NAMES = (
    "kfactor",
    "Kfactor",
    "k_factor",
    "K_Factor",
    "KFactor",
    "kFactor",
)


def material_short_name(name: str) -> str:
    mapping = {
        "4130 Chromoly": "4130",
        "5052 Aluminum": "5052",
        "Brass": "Brass",
        "Copper": "Copper",
        "G90 Steel": "G90",
        "Mild Steel": "Mild",
        "Polycarbonate": "PC",
        "304 Stainless Steel": "304SS",
        "316 Stainless Steel": "316SS",
        "Titanium Grade 2": "TiG2",
    }
    if name in mapping:
        return mapping[name]
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name)
    return cleaned[:12] or "Mat"


def thickness_thou(t_in: float) -> str:
    thou = int(round(float(t_in) * 1000))
    return "%03d" % thou


def find_property(obj, candidates):
    props = list(obj.PropertiesList)
    lower_map = {p.lower(): p for p in props}
    for name in candidates:
        if name in props:
            return name
        key = name.lower()
        if key in lower_map:
            return lower_map[key]
    for prop in props:
        pl = prop.lower().replace("_", "")
        for cand in candidates:
            cl = cand.lower().replace("_", "")
            if pl == cl or pl.endswith(cl) or cl in pl:
                return prop
    return None


def inch_quantity(value_in: float):
    return App.Units.Quantity("%s in" % value_in)


def resolve_wb_dir():
    try:
        here = os.path.dirname(os.path.realpath(__file__))
        if os.path.isfile(os.path.join(here, "data", "sendcutsend_bends.json")):
            return here
    except NameError:
        pass
    try:
        import SCS_locator
        return SCS_locator.PATH
    except Exception:
        pass
    root = App.getUserAppDataDir()
    for rel in (
        os.path.join("Mod", "SendCutSendPresets"),
        os.path.join("v1-1", "Mod", "SendCutSendPresets"),
        os.path.join("v26-3", "Mod", "SendCutSendPresets"),
        os.path.join("v1-1", "v26-3", "Mod", "SendCutSendPresets"),
    ):
        path = os.path.join(root, rel)
        if os.path.isfile(os.path.join(path, "data", "sendcutsend_bends.json")):
            return path
    return os.path.join(root, "Mod", "SendCutSendPresets")


def quantity_to_inches(value):
    """Convert FreeCAD Quantity / float / str to inches (float)."""
    if value is None:
        return None
    try:
        # FreeCAD Quantity
        if hasattr(value, "getValueAs"):
            return float(value.getValueAs("in"))
        if hasattr(value, "Value") and hasattr(value, "Unit"):
            # Fallback: Value is often in mm internally for Length
            try:
                return float(value.getValueAs("in"))
            except Exception:
                # assume mm if Unit says so
                return float(value.Value) / 25.4
        return float(value)
    except Exception:
        try:
            q = App.Units.Quantity(value)
            return float(q.getValueAs("in"))
        except Exception:
            return None
