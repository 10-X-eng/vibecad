# SPDX-License-Identifier: MIT
"""Remember last bend-preset Apply and auto-sync when an Unfold is created later."""

from __future__ import annotations

import FreeCAD as App

_pending = None  # dict | None
_observer = None
_PARAM = "User parameter:BaseApp/Preferences/Mod/SendCutSendPresets"


def _log(msg):
    try:
        App.Console.PrintMessage("[Bend Presets] %s\n" % msg)
    except Exception:
        pass


def _param():
    try:
        return App.ParamGet(_PARAM)
    except Exception:
        return None


def remember_pending_unfold_sync(k, sheet_label, doc_name=None, source_note=None):
    """Store last Apply so a future Unfold picks up K + material sheet."""
    global _pending
    try:
        k = float(k)
    except Exception:
        return
    sheet_label = str(sheet_label) if sheet_label else ""
    if not doc_name:
        try:
            doc = App.ActiveDocument
            doc_name = doc.Name if doc else ""
        except Exception:
            doc_name = ""
    _pending = {
        "k": k,
        "sheet": sheet_label,
        "doc": doc_name or "",
        "note": source_note or "",
    }
    p = _param()
    if p is not None:
        try:
            p.SetFloat("pendingK", k)
            p.SetString("pendingSheet", sheet_label)
            p.SetString("pendingDoc", doc_name or "")
            p.SetBool("pendingActive", True)
        except Exception:
            pass
    _log(
        "Remembered preset for next Unfold: K=%s sheet=%s (apply before Unfold is OK)"
        % (k, sheet_label or "(none)")
    )
    ensure_observer()


def clear_pending_unfold_sync():
    global _pending
    _pending = None
    p = _param()
    if p is not None:
        try:
            p.SetBool("pendingActive", False)
        except Exception:
            pass


def get_pending():
    global _pending
    if _pending:
        return dict(_pending)
    p = _param()
    if p is None:
        return None
    try:
        if not p.GetBool("pendingActive", False):
            return None
        return {
            "k": float(p.GetFloat("pendingK", 0.0)),
            "sheet": p.GetString("pendingSheet", ""),
            "doc": p.GetString("pendingDoc", ""),
            "note": "prefs",
        }
    except Exception:
        return None


def _looks_like_unfold_obj(obj):
    try:
        name = (obj.Name or "").lower()
        label = (obj.Label or "").lower()
    except Exception:
        return False
    blob = name + " " + label
    if "sketch" in blob:
        return False
    if "unfold" not in blob:
        # property-based
        try:
            getattr(obj, "KFactor")
            getattr(obj, "MaterialSheet")
            return True
        except Exception:
            return False
    try:
        getattr(obj, "KFactor")
        return True
    except Exception:
        return False


def apply_pending_to_object(obj):
    pending = get_pending()
    if not pending:
        return False
    if not _looks_like_unfold_obj(obj):
        return False
    try:
        doc = obj.Document
        if pending.get("doc") and doc and pending["doc"] not in (doc.Name, ""):
            # Still allow if pending doc was Unnamed / changed — only skip if both set and differ
            # Soft filter: prefer same doc but don't block if user renamed
            pass
    except Exception:
        pass

    from bend_actions import sync_unfold_features

    k = pending.get("k")
    sheet = pending.get("sheet") or ""
    n = sync_unfold_features(sheet, k, log=_log)
    if n:
        _log(
            "Auto-synced new Unfold from earlier Apply: K=%s sheet=%s (%s object(s))"
            % (k, sheet or "(k only)", n)
        )
        clear_pending_unfold_sync()
        return True
    return False


def _defer_apply(obj):
    """Unfold props are often added *after* slotCreatedObject — retry shortly."""
    try:
        name = obj.Name
        doc_name = obj.Document.Name if obj.Document else None
    except Exception:
        return
    if not name or not doc_name:
        return

    def _try(attempt=0):
        try:
            doc = App.getDocument(doc_name)
            if doc is None:
                return
            o = doc.getObject(name)
            if o is None:
                return
            if apply_pending_to_object(o):
                return
            # Not ready yet (no KFactor) or not an Unfold — retry a few times
            if attempt < 6:
                _schedule(lambda: _try(attempt + 1), 150)
        except Exception as exc:
            _log("deferred Unfold sync error: %s" % exc)

    _schedule(lambda: _try(0), 50)


def _schedule(fn, delay_ms):
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
        QTimer.singleShot(int(delay_ms), fn)
    else:
        try:
            fn()
        except Exception:
            pass


class _UnfoldPendingObserver(object):
    """FreeCAD document observer — fires when Unfold is added after Apply."""

    def slotCreatedObject(self, obj):
        try:
            _defer_apply(obj)
        except Exception as exc:
            _log("pending Unfold observer error: %s" % exc)

    def slotAppendObject(self, parent, obj):
        try:
            _defer_apply(obj)
        except Exception:
            pass


def ensure_observer():
    global _observer
    if _observer is not None:
        return
    try:
        _observer = _UnfoldPendingObserver()
        App.addDocumentObserver(_observer)
        _log("Watching for Unfold creation (auto-apply last preset).")
    except Exception as exc:
        _observer = None
        _log("Could not add Unfold observer: %s" % exc)


def setup():
    """Call from InitGui — restore pending flag from prefs and start observer."""
    ensure_observer()
    pending = get_pending()
    if pending and pending.get("k"):
        _log(
            "Pending Unfold preset restored: K=%s sheet=%s"
            % (pending.get("k"), pending.get("sheet") or "")
        )
