# SPDX-License-Identifier: MIT
"""Inject Bend Presets (SendCutSend library + custom) into SheetMetal.

SendCutSend workbench stays as the test bed. Day-to-day use is the single
Bend Presets button on SheetMetal (SCS library + My custom).
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui

_CMD = "SCS_ShowCustomPresets"
_TOOLBAR = "Bend Presets"
_installed = False
_hooked = False


def _log(msg):
    try:
        App.Console.PrintMessage("[SCS->SheetMetal] %s\n" % msg)
    except Exception:
        pass


def _ensure_command_registered():
    """CustomPresets registers SCS_ShowCustomPresets on import when Gui is up."""
    import CustomPresets  # noqa: F401


def _sheetmetal_workbench_keys():
    """Possible FreeCAD keys for SheetMetal (varies by version/install)."""
    keys = []
    try:
        # FreeCAD 1.x
        wbs = Gui.listWorkbenches()
        if isinstance(wbs, dict):
            for k, v in wbs.items():
                label = ""
                try:
                    label = str(v)
                except Exception:
                    pass
                kl = (k or "").lower()
                if "sheetmetal" in kl.replace(" ", "") or "smworkbench" in kl:
                    keys.append(k)
                elif "sheet metal" in label.lower():
                    keys.append(k)
    except Exception:
        pass
    # Well-known fallbacks
    for k in ("SMWorkbench", "SheetMetalWorkbench", "Sheet Metal"):
        if k not in keys:
            keys.append(k)
    return keys


def _try_append(wb):
    """Append toolbar + menu entries if the workbench proxy supports it."""
    ok = False
    try:
        wb.appendToolbar(_TOOLBAR, [_CMD])
        ok = True
    except Exception:
        # Common while SMWorkbench proxy is not fully live yet; retry quietly.
        pass
    try:
        wb.appendMenu("&Sheet Metal", [_CMD])
        ok = True
    except Exception:
        try:
            wb.appendMenu("Sheet Metal", [_CMD])
            ok = True
        except Exception:
            pass
    return ok


def install_into_sheetmetal():
    """Idempotent: register command and attach to SheetMetal workbench."""
    global _installed
    if not App.GuiUp:
        return False
    try:
        _ensure_command_registered()
    except Exception as exc:
        _log("Could not import CustomPresets: %s" % exc)
        return False

    attached = False
    for key in _sheetmetal_workbench_keys():
        try:
            wb = Gui.getWorkbench(key)
        except Exception:
            continue
        if wb is None:
            continue
        if _try_append(wb):
            attached = True
            _log("Attached '%s' to workbench '%s'." % (_CMD, key))
            break

    if attached:
        _installed = True
    # else: still loading — retries are silent until success
    return attached


def _on_workbench_activated(name):
    """Retry attach when user switches to SheetMetal (covers late load)."""
    try:
        n = str(name)
    except Exception:
        n = ""
    nl = n.lower().replace(" ", "")
    if "sheetmetal" in nl or n in ("SMWorkbench", "SheetMetalWorkbench"):
        install_into_sheetmetal()


def hook_workbench_activation():
    """Connect FreeCAD main-window workbenchActivated if available."""
    global _hooked
    if _hooked or not App.GuiUp:
        return
    try:
        mw = Gui.getMainWindow()
    except Exception:
        return
    if mw is None:
        return
    # FreeCAD main window signal (Qt)
    sig = getattr(mw, "workbenchActivated", None)
    if sig is None:
        return
    try:
        sig.connect(_on_workbench_activated)
        _hooked = True
        _log("Hooked workbenchActivated for SheetMetal integration.")
    except Exception as exc:
        _log("Could not hook workbenchActivated: %s" % exc)


def setup():
    """Call from SendCutSendPresets InitGui after our own workbench registers."""
    hook_workbench_activation()
    # Deferred install: SheetMetal InitGui may run after us alphabetically
    try:
        from PySide6.QtCore import QTimer  # type: ignore
    except ImportError:
        try:
            from PySide2.QtCore import QTimer  # type: ignore
        except ImportError:
            try:
                from PySide.QtCore import QTimer  # type: ignore
            except ImportError:
                QTimer = None
    if QTimer is not None:
        # a few retries while other workbenches finish loading
        for delay in (0, 500, 1500, 3000):
            QTimer.singleShot(delay, install_into_sheetmetal)
    else:
        install_into_sheetmetal()
