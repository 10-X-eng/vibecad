# SPDX-License-Identifier: MIT
"""Remember last bend-preset Apply and auto-sync when an Unfold is created later."""

from __future__ import annotations

import FreeCAD as App

_pending = None  # dict | None
_observer = None
_dispatcher = None
_queued = set()
_waiting = {}
_applying = False
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
    try:
        owner = App.getDocument(doc_name) if doc_name else None
        doc_uid = str(getattr(owner, "Uid", "") or "")
    except Exception:
        doc_uid = ""
    _pending = {
        "doc_uid": doc_uid,
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
            p.SetString("pendingDocUid", doc_uid)
            p.SetBool("pendingActive", True)
        except Exception:
            pass
    _log(
        "Remembered preset for next Unfold: K=%s sheet=%s (apply before Unfold is OK)"
        % (k, sheet_label or "(none)")
    )
    ensure_observer()


def clear_pending_unfold_sync():
    global _pending, _observer
    _pending = None
    _waiting.clear()
    if _observer is not None:
        try:
            App.removeDocumentObserver(_observer)
        except Exception:
            pass
        _observer = None
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
            "doc_uid": p.GetString("pendingDocUid", ""),
            "note": "prefs",
        }
    except Exception:
        return None


def _pending_matches_document(pending, doc):
    if doc is None:
        return False
    if pending.get("doc") and pending["doc"] != doc.Name:
        return False
    return not pending.get("doc_uid") or pending["doc_uid"] == str(getattr(doc, "Uid", ""))


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
        if doc is None or App.getDocument(doc.Name) is not doc:
            return False
        if not _pending_matches_document(pending, doc):
            return False
    except Exception:
        return False

    from bend_actions import sync_unfold_features

    k = pending.get("k")
    sheet = pending.get("sheet") or ""
    global _applying
    if _applying:
        return False
    _applying = True
    try:
        from preset_update import run_document_update
        n = run_document_update(doc, lambda: sync_unfold_features(
            sheet, k, log=_log, document=doc, objects=[obj], recompute=False,
        ))
    finally:
        _applying = False
    if n:
        _log(
            "Auto-synced new Unfold from earlier Apply: K=%s sheet=%s (%s object(s))"
            % (k, sheet or "(k only)", n)
        )
        clear_pending_unfold_sync()
        return True
    return False


def _defer_apply(obj):
    """Queue one owner-thread apply; later property events handle late setup."""
    if _applying:
        return
    pending = get_pending()
    if not pending:
        return
    try:
        name = obj.Name
        doc = obj.Document
        doc_name = doc.Name if doc else None
        if not name or not doc_name or not _pending_matches_document(pending, doc):
            return
        blob = (name + " " + getattr(obj, "Label", "")).lower()
        if "sketch" in blob or ("unfold" not in blob and not _looks_like_unfold_obj(obj)):
            return
    except Exception:
        return
    key = (doc_name, name, id(obj))
    if key in _queued:
        return
    _queued.add(key)

    def apply():
        _queued.discard(key)
        try:
            current_doc = App.getDocument(doc_name)
            if current_doc is not doc or current_doc.getObject(name) is not obj:
                return
            if any(getattr(doc, flag, False) for flag in
                   ("Recomputing", "RecomputePending", "CooperativeMutationActive")):
                _waiting[key] = (doc, obj)
                return
            _waiting.pop(key, None)
            apply_pending_to_object(obj)
        except Exception as exc:
            _log("deferred Unfold sync error: %s" % exc)

    _post_apply(apply)


def _post_apply(callback):
    """Post to the GUI event queue without polling or delayed retry timers."""
    global _dispatcher
    try:
        from PySide import QtCore
    except ImportError:
        callback()
        return
    application = QtCore.QCoreApplication.instance()
    if application is None:
        callback()
        return
    if _dispatcher is None:
        class Dispatcher(QtCore.QObject):
            requested = QtCore.Signal(object)

            def __init__(self):
                super().__init__(application)
                self.requested.connect(self.run, QtCore.Qt.QueuedConnection)

            def run(self, fn):
                fn()

        _dispatcher = Dispatcher()
    _dispatcher.requested.emit(callback)


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

    def slotChangedObject(self, obj, prop):
        if prop in ("KFactor", "MaterialSheet", "Label"):
            _defer_apply(obj)

    def _retry_waiting(self, document=None):
        for key, (doc, obj) in tuple(_waiting.items()):
            if document is None or doc is document:
                _waiting.pop(key, None)
                _defer_apply(obj)

    def slotRecomputedDocument(self, document):
        self._retry_waiting(document)

    def slotCooperativeMutationChanged(self, document, active):
        if not active:
            self._retry_waiting(document)

    def slotFinishRestoreDocument(self, document):
        self._retry_waiting(document)

    def slotCloseTransaction(self, aborted):
        self._retry_waiting()

    def slotUndoDocument(self, document):
        pending = get_pending()
        if pending and _pending_matches_document(pending, document):
            # Preferences are outside the document's undo journal. Never
            # replay a remembered preset after the user undoes model edits.
            clear_pending_unfold_sync()

    def slotDeletedDocument(self, document):
        for key, (doc, obj) in tuple(_waiting.items()):
            if doc is document:
                _waiting.pop(key, None)


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
    pending = get_pending()
    if pending:
        ensure_observer()
    if pending and pending.get("k"):
        _log(
            "Pending Unfold preset restored: K=%s sheet=%s"
            % (pending.get("k"), pending.get("sheet") or "")
        )
