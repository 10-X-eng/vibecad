# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared native-command boundary for the VibeCAD Manufacture ribbon."""

import FreeCAD
from VibeCADNativeTransaction import _OwnedDocumentTransaction

if FreeCAD.GuiUp:
    import FreeCADGui


_pending_task_launches = {}


def can_start_ui_command():
    """Return whether a new modal/modeless CAM editor may start."""

    if not FreeCAD.GuiUp:
        return False
    if FreeCADGui.Control.activeDialog():
        return False

    document = FreeCAD.ActiveDocument
    gui_document = FreeCADGui.activeDocument()
    if document is None:
        return gui_document is None
    return (
        gui_document is not None
        and gui_document.Document is document
        and document.getBookedTransactionID() == 0
        and not document.HasPendingTransaction
    )


def can_start_document_command(document=None):
    """Return whether a CAM command may begin work in the active document."""

    if document is None:
        document = FreeCAD.ActiveDocument
    return (
        document is not None
        and document is FreeCAD.ActiveDocument
        and can_start_ui_command()
    )


def ensure_task_transaction(name, document=None):
    """Return the one transaction owned by the current CAM task launch.

    A create command opens its transaction before provisional objects exist.
    The task-panel constructor must reuse that exact transaction; opening a
    second one would commit the provisional creation and make Cancel unable to
    restore the pre-command document. Existing-object editors call this with
    no transaction and receive a fresh one.
    """

    if document is None:
        document = FreeCAD.ActiveDocument
    if document is None or document is not FreeCAD.ActiveDocument:
        raise RuntimeError("A CAM task requires the active document")

    transaction = int(document.getBookedTransactionID())
    if transaction:
        return transaction

    document.openTransaction(name)
    transaction = int(document.getBookedTransactionID())
    if not transaction:
        raise RuntimeError("The CAM task could not open its transaction")
    return transaction


def document_is_open(document):
    """Return whether *document* is still the live document with its name."""

    if document is None:
        return False
    try:
        return FreeCAD.getDocument(document.Name) is document
    except (NameError, ReferenceError, RuntimeError):
        return False


def is_document_object(obj, document=None):
    """Return whether *obj* is a live, valid object in *document*."""

    if document is None:
        document = FreeCAD.ActiveDocument
    if obj is None or document is None:
        return False
    try:
        name = getattr(obj, "Name", "")
        return (
            bool(name)
            and getattr(obj, "Document", None) is document
            and document_is_open(document)
            and document.getObject(name) is obj
            and obj.isValid()
        )
    except (NameError, ReferenceError, RuntimeError):
        return False


def is_timeline_input_usable(obj, document=None):
    """Return whether an exact object may feed work at the History marker.

    Hidden objects remain usable. Future, suppressed, internal, replaced, or
    malformed History objects do not. Every occurrence and definition in a
    nested link chain must be usable in its own document.
    """

    if document is None:
        document = getattr(obj, "Document", None)
    try:
        if getattr(obj, "Document", None) is not document or not is_document_object(
            obj,
            document,
        ):
            return False
        if FreeCAD.GuiUp:
            import PartGui

            # Bodies are stable human-facing identities. Their active History
            # state, rather than the structural container, is the exact model
            # input used by every modeling workbench.
            return bool(PartGui.isModelingObjectActive(obj))
        current = obj
        visited = set()
        while current is not None:
            identity = id(current)
            if identity in visited:
                return False
            visited.add(identity)
            current_document = getattr(current, "Document", None)
            if current is obj and current_document is not document:
                return False
            if not is_document_object(
                current,
                current_document,
            ) or not current_document.isObjectUsableAtCurrentTimelinePosition(current):
                return False
            linked = current.getLinkedObject(recursive=False)
            if linked is None or linked is current:
                return True
            current = linked
        return False
    except (AttributeError, NameError, ReferenceError, RuntimeError):
        return False


class ExactDocumentObjectIdentity:
    """Resolve only the exact document object captured before a callback."""

    __slots__ = (
        "document",
        "document_name",
        "document_uid",
        "object",
        "object_name",
        "object_id",
    )

    def __init__(self, obj, document=None):
        if document is None:
            document = getattr(obj, "Document", None)
        if not is_document_object(obj, document):
            raise RuntimeError(
                "A CAM command requires one live document object"
            )
        self.document = document
        self.document_name = str(document.Name)
        self.document_uid = str(document.Uid)
        self.object = obj
        self.object_name = str(obj.Name)
        self.object_id = int(obj.ID)
        if (
            self.object_id <= 0
            or document.getObject(self.object_id) is not obj
        ):
            raise RuntimeError(
                "A CAM command captured an invalid object identity"
            )

    def resolve(self, *, require_timeline=False):
        document = self.document
        try:
            exact = (
                document_is_open(document)
                and str(document.Name) == self.document_name
                and str(document.Uid) == self.document_uid
                and document.getObject(self.object_name) is self.object
                and document.getObject(self.object_id) is self.object
                and int(self.object.ID) == self.object_id
                and getattr(self.object, "Document", None) is document
            )
        except (AttributeError, NameError, ReferenceError, RuntimeError):
            exact = False
        if not exact:
            raise RuntimeError(
                "A CAM command input changed while its callback was running"
            )
        if require_timeline and not is_timeline_input_usable(
            self.object,
            document,
        ):
            raise RuntimeError(
                "A CAM command input is unavailable at the current "
                "History position"
            )
        return self.object


def open_timeline_mode_zero_editor(obj):
    """Open the exact object's real mode-0 editor for document History.

    History calls a ViewProvider's ``doubleClicked`` hook, while CAM's real
    task panels are implemented by ``setEdit(..., 0)``.  This bridge is used
    only by ViewProviders which explicitly advertise a real History editor.
    It never falls back to the active document or selection, and it refuses
    to displace an editor already owned by any open document.
    """

    if not FreeCAD.GuiUp or obj is None:
        return False

    gui_document = None
    try:
        document = obj.Document
        name = str(obj.Name)
        if (
            not name
            or not document_is_open(document)
            or document.getObject(name) is not obj
        ):
            return False

        gui_document = FreeCADGui.getDocument(document.Name)
        if (
            gui_document is None
            or gui_document.Document is not document
            or FreeCADGui.Control.activeDialog()
        ):
            return False
        booked = int(document.getBookedTransactionID())
        if booked and not _gui_command_owns_transaction(
            document,
            booked,
        ):
            return False

        for document_name in FreeCAD.listDocuments():
            candidate = FreeCADGui.getDocument(document_name)
            if candidate is not None and candidate.getInEdit() is not None:
                return False

        opened = bool(gui_document.setEdit(obj, 0))
        in_edit = gui_document.getInEdit()
        editing_exact_object = (
            in_edit is not None
            and in_edit.Object is obj
        )
        if not opened:
            if editing_exact_object:
                gui_document.resetEdit()
            return False
        if not editing_exact_object:
            # No document had an editor before this call, so any editor now
            # present in the target document was introduced by this failed
            # exact-object attempt and must not be left behind.
            if in_edit is not None:
                gui_document.resetEdit()
            return False

        for document_name in FreeCAD.listDocuments():
            candidate = FreeCADGui.getDocument(document_name)
            if (
                candidate is not None
                and candidate is not gui_document
                and candidate.getInEdit() is not None
            ):
                gui_document.resetEdit()
                return False
        return True
    except Exception:
        if gui_document is not None:
            try:
                in_edit = gui_document.getInEdit()
                if in_edit is not None and in_edit.Object is obj:
                    gui_document.resetEdit()
            except Exception:
                pass
        return False


def _gui_command_owns_transaction(document, transaction_id):
    """Return whether the native GUI command owns this exact transaction."""

    if (
        not FreeCAD.GuiUp
        or not document_is_open(document)
        or not transaction_id
    ):
        return False
    try:
        gui_document = FreeCADGui.getDocument(document.Name)
        return bool(
            gui_document
            and FreeCADGui.Control.ownsCommandTransaction(
                gui_document,
                int(transaction_id),
            )
        )
    except (NameError, ReferenceError, RuntimeError):
        return False


class TaskLaunchToken:
    """Exact ribbon-command transaction offered to one resulting task."""

    def __init__(self, transaction):
        document = transaction.document
        self.document = document
        self.document_name = str(document.Name)
        self.transaction_id = int(transaction.transaction_id)
        self._transaction = transaction
        self.claimed = False
        self.owner = None

    def claim(self, document):
        if self.claimed:
            raise RuntimeError("The CAM task launch was already claimed")
        if (
            document is not self.document
            or not document_is_open(document)
            or int(document.getBookedTransactionID())
            != self.transaction_id
        ):
            raise RuntimeError(
                "The CAM task launch no longer owns its exact transaction"
            )
        self.claimed = True
        _pending_task_launches.pop(self.document_name, None)
        return self.transaction_id

    def abort_unclaimed(self):
        if self.claimed:
            return False
        _pending_task_launches.pop(self.document_name, None)
        if not document_is_open(self.document):
            self._transaction.document_deleted()
            self.transaction_id = 0
            return False
        self._transaction.abort()
        self.transaction_id = int(self._transaction.transaction_id)
        return True

    def abort(self):
        """Abort either the unclaimed launch or its claimed task owner."""

        if not self.claimed:
            return self.abort_unclaimed()
        if self.owner is None:
            return False
        self.owner.close_dialog()
        if not self.owner.owns_transaction():
            return True
        return self.owner.abort()

    def require_claimed(self):
        """Fail and roll back if the launch did not produce its task panel."""

        if (
            self.claimed
            and self.owner is not None
            and self.owner.owns_transaction()
            and self.owner.editor_open
        ):
            return
        self.abort()
        raise RuntimeError(
            "The CAM ribbon command did not open its task editor"
        )


def begin_task_launch(name, document=None):
    """Open an exact transaction for one ribbon command and its task panel.

    Public Python ``Create`` functions may intentionally join a caller-owned
    transaction and continue to use :func:`ensure_task_transaction`.  Ribbon
    commands use this explicit token so their panels can never infer ownership
    from an arbitrary booked transaction.
    """

    if document is None:
        document = FreeCAD.ActiveDocument
    if not can_start_document_command(document):
        raise RuntimeError(
            "A CAM ribbon task requires an idle active document"
        )

    previous = _pending_task_launches.pop(str(document.Name), None)
    if previous is not None:
        previous.abort_unclaimed()

    transaction = _OwnedDocumentTransaction(document, name)
    launch = TaskLaunchToken(transaction)
    _pending_task_launches[launch.document_name] = launch
    return launch


def _pending_task_launch(document):
    launch = _pending_task_launches.get(str(document.Name))
    if launch is None:
        return None
    if (
        launch.document is not document
        or launch.claimed
        or not document_is_open(document)
        or int(document.getBookedTransactionID())
        != launch.transaction_id
    ):
        _pending_task_launches.pop(str(document.Name), None)
        if not launch.claimed:
            launch.abort_unclaimed()
        return None
    return launch


class TaskDocumentTransaction:
    """Keep a CAM task bound to its launch document and exact transaction.

    CAM task panels stay open while the active document can change through
    tabs, macros, or direct Python callbacks.  Looking up ``ActiveDocument``
    from a later callback can therefore commit, abort, recompute, or reset the
    wrong document.  This owner captures the model document and transaction
    once, verifies exact ownership for every mutation, and treats deletion of
    the launch document as a safe terminal state.
    """

    def __init__(
        self,
        owner,
        name,
        launch=None,
        *,
        allow_caller_transaction=False,
    ):
        document = getattr(owner, "Document", None)
        if document is None and hasattr(owner, "getBookedTransactionID"):
            document = owner
        if document is None:
            raise RuntimeError("A CAM task requires a document object")
        if not document_is_open(document):
            raise RuntimeError("The CAM task document has been closed")
        if owner is not document:
            owner_name = getattr(owner, "Name", "")
            if (
                not owner_name
                or document.getObject(owner_name) is not owner
            ):
                raise RuntimeError(
                    "The CAM task object is no longer in its document"
                )

        self.document = document
        self.document_name = str(document.Name)
        self.editor_open = False
        self._native_transaction = None
        self._native_command_owns_close = False
        self._task_dialog_owns_close = False
        self._task_dialog = None
        if launch is None:
            launch = _pending_task_launch(document)
        if launch is not None:
            self.transaction_id = launch.claim(document)
            self._native_transaction = launch._transaction
            launch.owner = self
        else:
            booked = int(document.getBookedTransactionID())
            command_owns_booked = _gui_command_owns_transaction(
                document,
                booked,
            )
            if (
                booked
                and not allow_caller_transaction
                and not command_owns_booked
            ):
                raise RuntimeError(
                    "A CAM task editor cannot borrow a caller transaction"
                )
            # Compatibility is explicit and limited to documented direct
            # Python Create paths, which may deliberately participate in
            # their caller's transaction.  Existing-object editors never
            # infer ownership from an arbitrary booked transaction.
            if booked:
                self.transaction_id = ensure_task_transaction(
                    name,
                    document,
                )
            else:
                self._native_transaction = _OwnedDocumentTransaction(
                    document,
                    name,
                )
                self.transaction_id = int(
                    self._native_transaction.transaction_id
                )
        self._native_command_owns_close = (
            _gui_command_owns_transaction(
                document,
                self.transaction_id,
            )
        )

    def is_open(self):
        return document_is_open(self.document)

    def gui_document(self):
        if not self.is_open() or not FreeCAD.GuiUp:
            return None
        try:
            return FreeCADGui.getDocument(self.document_name)
        except (NameError, RuntimeError):
            return None

    def owns_transaction(self):
        self._sync_native_transaction()
        return (
            self.is_open()
            and self.transaction_id != 0
            and int(self.document.getBookedTransactionID())
            == self.transaction_id
        )

    def require_transaction(self):
        self._sync_native_transaction()
        if not self.is_open():
            raise RuntimeError("The CAM task document has been closed")
        booked = int(self.document.getBookedTransactionID())
        if self.transaction_id == 0 or booked != self.transaction_id:
            raise RuntimeError(
                "The CAM task no longer owns its launch-document transaction"
            )

    def object(self, name):
        if not self.is_open():
            return None
        return self.document.getObject(name)

    def validate_objects(self, objects):
        for obj in objects:
            if obj is None:
                raise RuntimeError("The CAM task produced no result object")
            name = getattr(obj, "Name", "")
            if not name or self.object(name) is not obj:
                raise RuntimeError(
                    "The CAM task result no longer belongs to its launch document"
                )
            if not obj.isValid():
                raise RuntimeError(
                    f"The CAM task result '{name}' is invalid"
                )

    def recompute(self, objects=()):
        self.require_transaction()
        result = self.document.recompute()
        if result is False:
            raise RuntimeError("The CAM task document failed to recompute")
        self.validate_objects(objects)
        return result

    def recompute_after_close(self):
        if not self.is_open():
            return False
        return self.document.recompute()

    def commit(self, objects=(), *, recompute=True):
        if recompute:
            self.recompute(objects)
        else:
            self.require_transaction()
            self.validate_objects(objects)
        self._close(abort=False)

    def abort(self):
        if not self.is_open():
            if self._native_transaction is not None:
                self._native_transaction.document_deleted()
            self.transaction_id = 0
            return False
        self._sync_native_transaction()
        if self.transaction_id == 0:
            return False
        self.require_transaction()
        self._close(abort=True)
        return True

    def reset_edit(self):
        gui_document = self.gui_document()
        if gui_document is not None:
            gui_document.resetEdit()

    def close_dialog(self):
        gui_document = self.gui_document()
        if gui_document is not None:
            FreeCADGui.Control.closeDialog(gui_document)
        self.editor_open = False

    def show_dialog(self, panel):
        gui_document = self.gui_document()
        if gui_document is None:
            raise RuntimeError("The CAM task GUI document has been closed")
        task_dialog = FreeCADGui.Control.showDialog(panel, gui_document)
        if not FreeCADGui.Control.activeDialog(gui_document):
            raise RuntimeError("The CAM task editor could not be shown")
        try:
            task_dialog_owns_close = bool(
                task_dialog.ownsCommandTransaction(
                    self.transaction_id
                )
            )
        except (ReferenceError, RuntimeError):
            task_dialog_owns_close = False
        if (
            self._native_command_owns_close
            and not task_dialog_owns_close
        ):
            FreeCADGui.Control.closeDialog(gui_document)
            raise RuntimeError(
                "The CAM task dialog did not adopt its command transaction"
            )
        self._task_dialog = task_dialog
        self._task_dialog_owns_close = task_dialog_owns_close
        self.editor_open = True

    def _close(self, *, abort):
        if (
            self._native_command_owns_close
            or self._task_dialog_owns_close
        ):
            # The common TaskDialog layer adopted and locked this exact
            # command transaction. It is the only layer that can safely
            # unlock and close that transaction after the Python
            # accept/reject callback unwinds. Trying to close it here fails
            # by design and leaves Cancel stuck open.
            self.require_transaction()
            return

        transaction_id = self.transaction_id
        if self._native_transaction is not None:
            if abort:
                self._native_transaction.abort()
            else:
                self._native_transaction.commit()
            self.transaction_id = int(
                self._native_transaction.transaction_id
            )
            return

        FreeCAD.closeActiveTransaction(abort, transaction_id)
        if (
            self.is_open()
            and int(self.document.getBookedTransactionID())
            == transaction_id
        ):
            action = "abort" if abort else "commit"
            raise RuntimeError(
                f"Could not {action} CAM task transaction {transaction_id}"
            )

        # An observer may have opened a successor transaction.  The exact
        # predecessor is gone, so this task is complete and must not touch it.
        self.transaction_id = 0

    def _sync_native_transaction(self):
        if (
            self._native_transaction is not None
            and self._native_transaction.transaction_id == 0
        ):
            self.transaction_id = 0


def is_job(obj, document=None):
    """Return whether *obj* is a real CAM Job in the intended document."""

    if document is None:
        document = FreeCAD.ActiveDocument
    if not is_document_object(obj, document):
        return False

    import Path.Main.Job as PathJob

    return (
        isinstance(getattr(obj, "Proxy", None), PathJob.ObjectJob)
        and is_timeline_input_usable(obj, document)
    )


def active_jobs(*, require_tool=False):
    """Return real Jobs in the active document, optionally with a real tool."""

    document = FreeCAD.ActiveDocument
    if document is None:
        return []

    jobs = []
    for obj in document.Objects:
        if (
            not is_job(obj, document)
            or not is_timeline_input_usable(obj, document)
        ):
            continue
        operations = getattr(obj, "Operations", None)
        if not is_document_object(operations, document):
            continue
        if require_tool:
            tools = getattr(obj, "Tools", None)
            if not is_document_object(tools, document):
                continue

            import Path.Tool.Controller as PathToolController
            from Path.Tool.toolbit import ToolBit

            has_valid_tool = False
            for controller in tools.Group:
                tool = getattr(controller, "Tool", None)
                if (
                    getattr(controller, "Document", None) is document
                    and document.getObject(controller.Name) is controller
                    and controller.isValid()
                    and is_timeline_input_usable(
                        controller,
                        document,
                    )
                    and isinstance(
                        getattr(controller, "Proxy", None),
                        PathToolController.ToolController,
                    )
                    and tool is not None
                    and getattr(tool, "Document", None) is document
                    and document.getObject(tool.Name) is tool
                    and tool.isValid()
                    and is_timeline_input_usable(tool, document)
                    and isinstance(getattr(tool, "Proxy", None), ToolBit)
                ):
                    has_valid_tool = True
                    break
            if not has_valid_tool:
                continue
        jobs.append(obj)
    return jobs
