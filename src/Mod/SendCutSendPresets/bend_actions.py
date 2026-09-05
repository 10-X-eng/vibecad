# SPDX-License-Identifier: MIT
"""Shared apply / material-sheet / defaults helpers for bend presets."""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui

from bend_common import (
    RADIUS_PROP_NAMES,
    KFACTOR_PROP_NAMES,
    LENGTH_PROP_NAMES,
    find_property,
    inch_quantity,
    quantity_to_inches,
    material_short_name,
    thickness_thou,
)


def _iter_group_children(obj):
    """Yield child objects from Body / Part / other containers."""
    seen = set()
    stack = [obj]
    while stack:
        cur = stack.pop()
        for attr in ("Group", "OutList"):
            try:
                kids = list(getattr(cur, attr, None) or [])
            except Exception:
                kids = []
            for kid in kids:
                if kid is None:
                    continue
                kid_id = id(kid)
                if kid_id in seen:
                    continue
                seen.add(kid_id)
                yield kid
                stack.append(kid)


def expand_targets(objs):
    """Expand PartDesign Body / App::Part selections into child features."""
    out = []
    seen = set()

    def _add(o):
        if o is None:
            return
        oid = id(o)
        if oid in seen:
            return
        seen.add(oid)
        out.append(o)

    for obj in objs or []:
        _add(obj)
        type_id = ""
        try:
            type_id = obj.TypeId or ""
        except Exception:
            type_id = ""
        needs_expand = (
            "Body" in type_id
            or type_id.startswith("App::Part")
            or find_property(obj, RADIUS_PROP_NAMES) is None
        )
        if needs_expand:
            for kid in _iter_group_children(obj):
                _add(kid)
    return out


def collect_bend_targets(sel=None):
    """Bend-capable features from selection (expanded) or whole document."""
    if sel is None:
        try:
            sel = Gui.Selection.getSelection()
        except Exception:
            sel = []

    expanded = expand_targets(list(sel))
    capable = [
        o
        for o in expanded
        if find_property(o, RADIUS_PROP_NAMES)
        or find_property(o, KFACTOR_PROP_NAMES)
    ]
    if capable:
        return capable

    if App.ActiveDocument is None:
        return []
    found = []
    for obj in App.ActiveDocument.Objects:
        if find_property(obj, RADIUS_PROP_NAMES) or find_property(
            obj, KFACTOR_PROP_NAMES
        ):
            found.append(obj)
    return found


def check_min_flange_lengths(entry, targets=None, log=None, warn_dialog=True):
    """Compare flange/bend lengths on targets to entry['min_flange'].

    Returns (ok: bool, warnings: list[str]).
    Warns when length is strictly below min. Equal-to-min is allowed.
    """
    min_flange = entry.get("min_flange") if entry else None
    try:
        min_flange = float(min_flange) if min_flange is not None else None
    except (TypeError, ValueError):
        min_flange = None

    if min_flange is None or min_flange <= 0:
        return True, []

    if targets is None:
        targets = collect_bend_targets()

    warnings = []
    for obj in targets:
        l_prop = find_property(obj, LENGTH_PROP_NAMES)
        if not l_prop:
            continue
        try:
            length_in = quantity_to_inches(getattr(obj, l_prop))
        except Exception:
            continue
        if length_in is None:
            continue
        if length_in + 1e-6 < min_flange:
            warnings.append(
                "%s: flange length %.4f in < min flange %.4f in"
                % (obj.Label, length_in, min_flange)
            )

    if warnings:
        text = "Min flange length warning (preset min=%.4f in): %s" % (
            min_flange,
            " | ".join(warnings),
        )
        if log:
            log(text)
        try:
            App.Console.PrintWarning("[Bend Presets] %s\n" % text)
        except Exception:
            pass
        if warn_dialog:
            QMessageBox = None
            try:
                from PySide6.QtWidgets import QMessageBox  # type: ignore
            except ImportError:
                try:
                    from PySide2.QtWidgets import QMessageBox  # type: ignore
                except ImportError:
                    try:
                        from PySide.QtGui import QMessageBox  # type: ignore
                    except ImportError:
                        QMessageBox = None
            if QMessageBox is not None:
                try:
                    body = (
                        "Applied bend params, but flange length is below the "
                        "preset minimum (%.4f in).\n\n%s\n\n"
                        "Lengthen the flange(s) or pick a different preset."
                        % (min_flange, "\n".join(warnings))
                    )
                    QMessageBox.warning(None, "Min flange length", body)
                except Exception:
                    pass
        return False, warnings
    return True, []


def _object_names(doc):
    """Snapshot object names (safe across FreeCAD proxy reuse / recompute)."""
    names = []
    try:
        objs = list(doc.Objects)
    except Exception:
        objs = []
    for obj in objs:
        try:
            names.append(str(obj.Name))
        except Exception:
            continue
        # Body/Part children sometimes matter; names are still in doc.Objects usually
        for attr in ("Group",):
            try:
                kids = list(getattr(obj, attr, None) or [])
            except Exception:
                kids = []
            for kid in kids:
                try:
                    kn = str(kid.Name)
                except Exception:
                    continue
                if kn not in names:
                    names.append(kn)
    # Selection names too
    try:
        for obj in Gui.Selection.getSelection():
            try:
                sn = str(obj.Name)
            except Exception:
                continue
            if sn not in names:
                names.append(sn)
    except Exception:
        pass
    return names


def _fetch(doc, name):
    try:
        return doc.getObject(name)
    except Exception:
        return None


def _safe_str_attr(obj, attr):
    try:
        val = getattr(obj, attr)
    except Exception:
        return ""
    try:
        return str(val) if val is not None else ""
    except Exception:
        return ""


def _has_prop(obj, prop):
    """True if property exists and is readable (avoid PropertiesList quirks)."""
    try:
        if hasattr(obj, prop):
            getattr(obj, prop)
            return True
    except Exception:
        pass
    try:
        obj.getPropertyByName(prop)
        return True
    except Exception:
        return False


def _set_kfactor_on_obj(obj, k):
    """Write KFactor; return (ok, prop_name, err)."""
    k = float(k)
    errors = []
    for pname in ("KFactor", "kfactor", "kFactor"):
        if not _has_prop(obj, pname):
            continue
        try:
            try:
                obj.setExpression(pname, None)
            except Exception:
                pass
            try:
                obj.setEditorMode(pname, 0)
            except Exception:
                pass
            setattr(obj, pname, k)
            got = getattr(obj, pname)
            try:
                got_f = float(got)
            except Exception:
                got_f = float(getattr(got, "Value", got))
            if abs(got_f - k) < 1e-9:
                return True, pname, None
            errors.append("%s wrote but read back %s" % (pname, got))
        except Exception as exc:
            errors.append("%s: %s" % (pname, exc))
    # Last resort: addProperty then set (should not be needed for real Unfold)
    return False, None, "; ".join(errors) if errors else "no KFactor property"


def sync_unfold_features(mat_sheet_label, k, log=None):
    """Push K-factor (+ material sheet) onto Unfold feature(s).

    SheetMetal grays out Manual K-Factor when a Material Sheet is selected;
    we still write Unfold.KFactor so the Data panel is not stuck at 0.40.
    """
    def _emit(msg):
        try:
            App.Console.PrintMessage("[Bend Presets] %s\n" % msg)
        except Exception:
            pass
        if log:
            try:
                log(msg)
            except Exception:
                pass

    doc = App.ActiveDocument
    if doc is None:
        _emit("sync_unfold: no active document")
        return 0

    k = float(k)
    names = _object_names(doc)
    _emit("sync scan names(%s): %s" % (len(names), ", ".join(names)))

    updated = 0
    considered = []

    for name in names:
        obj = _fetch(doc, name)
        if obj is None:
            continue

        label = _safe_str_attr(obj, "Label") or name
        type_id = _safe_str_attr(obj, "TypeId")
        blob = ("%s %s %s" % (name, label, type_id)).lower()

        has_k = _has_prop(obj, "KFactor")
        has_m = _has_prop(obj, "MaterialSheet")
        mat_val = _safe_str_attr(obj, "MaterialSheet") if has_m else ""

        # Skip sketches named Unfold_Sketch etc.
        if "sketch" in blob:
            continue
        is_unf = (
            (has_k and has_m)
            or (has_k and "unfold" in blob)
            or (has_k and mat_sheet_label and mat_val == str(mat_sheet_label))
        )
        if not is_unf:
            continue

        considered.append("%s/%s hasK=%s hasM=%s mat=%s" % (name, label, has_k, has_m, mat_val or "-"))

        # Assign material sheet (UI-readOnly; Python can still set — task panel does)
        if mat_sheet_label and has_m:
            try:
                try:
                    obj.setEditorMode("MaterialSheet", 0)
                except Exception:
                    pass
                if mat_val != str(mat_sheet_label):
                    obj.MaterialSheet = str(mat_sheet_label)
            except Exception as exc:
                _emit("MaterialSheet on %s failed: %s" % (label, exc))

        ok, pname, err = _set_kfactor_on_obj(obj, k)
        if ok:
            try:
                obj.KFactorStandard = "ansi"
            except Exception:
                pass
            try:
                obj.touch()
            except Exception:
                pass
            updated += 1
            _emit("Synced %s: %s=%s MaterialSheet=%s" % (label, pname, k, mat_sheet_label or mat_val))
        else:
            _emit("Failed KFactor on %s: %s" % (label, err))

    # Absolute fallback: known Unfold names + labels
    if updated == 0:
        fallback_names = []
        for n in names:
            if "unfold" in n.lower():
                fallback_names.append(n)
        try:
            for o in doc.getObjectsByLabel("Unfold") or []:
                try:
                    fallback_names.append(o.Name)
                except Exception:
                    pass
        except Exception:
            pass
        for n in ("Unfold", "Unfold001", "Unfold002", "Unfold003"):
            if n not in fallback_names:
                fallback_names.append(n)

        for name in fallback_names:
            obj = _fetch(doc, name)
            if obj is None:
                continue
            if not _has_prop(obj, "KFactor"):
                _emit("fallback %s: no KFactor prop" % name)
                continue
            ok, pname, err = _set_kfactor_on_obj(obj, k)
            if ok:
                if mat_sheet_label and _has_prop(obj, "MaterialSheet"):
                    try:
                        obj.MaterialSheet = str(mat_sheet_label)
                    except Exception:
                        pass
                try:
                    obj.touch()
                except Exception:
                    pass
                updated += 1
                _emit("Fallback synced %s: %s=%s" % (name, pname, k))
            else:
                _emit("Fallback failed %s: %s" % (name, err))

    if updated and doc is not None:
        try:
            doc.recompute()
        except Exception:
            pass

    if updated == 0:
        _emit(
            "No Unfold KFactor updated. considered=%s"
            % (considered[:8] if considered else "[]")
        )
        # Apply-before-Unfold: stash for auto-sync when Unfold is created
        try:
            from pending_unfold import remember_pending_unfold_sync
            remember_pending_unfold_sync(k, mat_sheet_label)
            _emit(
                "No Unfold yet — will auto-apply K=%s / %s when you create Unfold."
                % (k, mat_sheet_label or "(sheet)")
            )
        except Exception as exc:
            _emit("Could not remember pending Unfold sync: %s" % exc)
    else:
        try:
            from pending_unfold import clear_pending_unfold_sync
            clear_pending_unfold_sync()
        except Exception:
            pass
    return updated


def apply_entry_to_bends(entry, log=None, mat_name=None, sheet_prefix="SCS"):
    if not entry:
        return "No thickness/entry selected."

    radius_q = inch_quantity(entry["r"])
    k = float(entry["k"])
    updated = 0
    skipped = []

    targets = collect_bend_targets()
    if not targets:
        return (
            "Nothing to update. Select a SheetMetal Bend/Wall (or its Body), "
            "or create one first."
        )

    for obj in targets:
        r_prop = find_property(obj, RADIUS_PROP_NAMES)
        k_prop = find_property(obj, KFACTOR_PROP_NAMES)
        changed = False
        try:
            if r_prop:
                setattr(obj, r_prop, radius_q)
                changed = True
            if k_prop:
                try:
                    setattr(obj, k_prop, float(k))
                except Exception:
                    try:
                        obj.setExpression(k_prop, None)
                    except Exception:
                        pass
                    setattr(obj, k_prop, float(k))
                changed = True
        except Exception as exc:
            skipped.append("%s: %s" % (obj.Label, exc))
            continue
        if changed:
            updated += 1
            obj.touch()
        else:
            skipped.append("%s: no radius/kfactor properties" % obj.Label)

    if updated and App.ActiveDocument is not None:
        App.ActiveDocument.recompute()

    _ok, flange_warnings = check_min_flange_lengths(
        entry, targets=targets, log=log, warn_dialog=True
    )

    prefs_ok = False
    try:
        param = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
        param.SetString("defaultRadius", "%s in" % entry["r"])
        param.SetFloat("defaultKFactor", k)
        param.SetFloat("manualKFactor", k)
        param.SetString("kFactorStandard", "ansi")
        prefs_ok = True
    except Exception:
        pass

    unfold_n = 0
    sheet_label = None
    if mat_name:
        short = material_short_name(mat_name)
        thou = thickness_thou(entry["t"])
        sheet_label = "material_%s_%s_%s" % (sheet_prefix, short, thou)
        unfold_n = sync_unfold_features(sheet_label, k, log=log)

    msg = "Applied radius=%s in, k=%s to %s object(s)." % (entry["r"], k, updated)
    if prefs_ok:
        msg += " Prefs: defaultKFactor/manualKFactor=%s." % k
    if unfold_n:
        msg += " Updated %s Unfold feature(s) to K=%s / %s." % (unfold_n, k, sheet_label)
    if flange_warnings:
        msg += " MIN FLANGE WARN: " + "; ".join(flange_warnings)
    if skipped:
        msg += " Notes: " + "; ".join(skipped)
    if log:
        log(msg)
    return msg


def create_material_sheet_for_entry(
    mat_name, entry, prefix="SCS", source="SendCutSend bending calculator", log=None
):
    if not entry:
        return "No thickness/entry selected."
    doc = App.ActiveDocument
    if doc is None:
        return "No active document. Create or open a document first."

    short = material_short_name(mat_name)
    thou = thickness_thou(entry["t"])
    sheet_name = "material_%s_%s_%s" % (prefix, short, thou)

    existing = doc.getObject(sheet_name)
    if existing is not None:
        sheet = existing
    else:
        sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    sheet.Label = sheet_name

    r_over_t = entry["r"] / entry["t"] if entry["t"] else 0.0
    k = float(entry["k"])

    sheet.set("A1", "Radius / Thickness")
    sheet.set("B1", "K-factor (ANSI)")
    sheet.set("A2", "%.6f" % r_over_t)
    sheet.set("B2", "%.6f" % k)
    sheet.set("A3", "99")
    sheet.set("B3", "0.5")
    sheet.set("A5", "Options")
    sheet.set("A6", "K-factor standard")
    sheet.set("B6", "ansi")
    sheet.set("A8", "Material")
    sheet.set("B8", mat_name)
    sheet.set("A9", "Thickness (in)")
    sheet.set("B9", "%.4f" % entry["t"])
    sheet.set("A10", "Bend radius (in)")
    sheet.set("B10", "%.4f" % entry["r"])
    sheet.set("A11", "Bend deduction (in)")
    sheet.set("B11", "%.4f" % float(entry.get("bd", 0.0)))
    sheet.set("A12", "Source")
    sheet.set("B12", source)

    doc.recompute()
    n = sync_unfold_features(sheet_name, k, log=log)
    if n:
        msg = (
            "Created/updated '%s'. Synced K=%s onto %s Unfold object(s). "
            "Material Sheet should be '%s'. Manual K-Factor spin stays grayed out "
            "while a sheet is selected (SheetMetal UI) — property should now read %s, not 0.40."
            % (sheet_name, k, n, sheet_name, k)
        )
    else:
        msg = (
            "Created/updated '%s' (K=%s). Unfold sync found 0 targets — see Report view "
            "for [Bend Presets] near=/extras= dump, then Apply again."
            % (sheet_name, k)
        )
    if log:
        log(msg)
    return msg


def set_sheetmetal_defaults_for_entry(entry, log=None):
    if not entry:
        return "No thickness/entry selected."
    try:
        param = App.ParamGet("User parameter:BaseApp/Preferences/Mod/SheetMetal")
    except Exception as exc:
        return "Could not open SheetMetal preferences: %s" % exc

    radius_str = "%s in" % entry["r"]
    k = float(entry["k"])
    param.SetString("defaultRadius", radius_str)
    param.SetFloat("defaultKFactor", k)
    param.SetFloat("manualKFactor", k)
    param.SetString("kFactorStandard", "ansi")
    msg = (
        "SheetMetal defaults updated: defaultRadius=%s; defaultKFactor=%s; "
        "manualKFactor=%s; kFactorStandard=ansi" % (radius_str, k, k)
    )
    if log:
        log(msg)
    return msg
