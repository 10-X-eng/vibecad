# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD gate for the immediate Native mutation runner."""

from __future__ import annotations

import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeMutation import (
    NATIVE_POSTCONDITION_FAILED,
    NATIVE_TRANSACTION_ACTIVE,
    NativeMutationDraft,
    NativeMutationError,
    NativeMutationRunner,
)
from VibeCADNativeState import NativeObjectIdentity


def _process_events(rounds: int = 12) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeMutationGate")
        VibeGui._connect_document_observer()
        _process_events()
        service = get_service()
        service.select_modeling_engine("native")
        state = service._native_document_states
        runner = NativeMutationRunner(state)
        uid = str(document.Uid)

        def create_box(target_document, name: str):
            feature = target_document.addObject("PartDesign::Feature", name)
            feature.Shape = Part.makeBox(10.0, 12.0, 14.0)
            return NativeMutationDraft(
                value=feature,
                recompute_targets=(feature,),
                created=(
                    NativeObjectIdentity(uid, feature.Name, feature.TypeId),
                ),
            )

        def verify_box(target_document, draft):
            feature = target_document.getObject(draft.value.Name)
            if feature is not draft.value or not feature.Shape.isValid():
                raise RuntimeError("box postcondition failed")
            return {
                "object": draft.created[0].summary(),
                "solid_count": len(feature.Shape.Solids),
            }

        def reject_box(_target_document, _draft):
            raise RuntimeError("deliberate postcondition failure")

        ticket = state.begin_call(uid, "model.feature")
        execution = runner.run(
            ticket=ticket,
            document=document,
            transaction_name="Create exact Native box",
            reauthorize_turn=lambda: None,
            mutate=lambda target: create_box(target, "NativeBox"),
            verify=verify_box,
        )
        _process_events()
        assert execution.result["solid_count"] == 1
        assert execution.receipt.revision_after == ticket.expected_revision + 1
        assert document.getObject("NativeBox") is not None
        assert int(document.UndoCount) == 1

        document.undo()
        _process_events()
        assert document.getObject("NativeBox") is None
        document.redo()
        _process_events()
        assert document.getObject("NativeBox") is not None

        before_failure = state.current_revision(uid)
        failed_ticket = state.begin_call(uid, "model.feature")
        try:
            runner.run(
                ticket=failed_ticket,
                document=document,
                transaction_name="Reject invalid Native box",
                reauthorize_turn=lambda: None,
                mutate=lambda target: create_box(target, "RejectedBox"),
                verify=reject_box,
            )
        except NativeMutationError as exc:
            assert exc.error_code == NATIVE_POSTCONDITION_FAILED
        else:
            raise AssertionError("postcondition failure unexpectedly committed")
        _process_events()
        assert document.getObject("RejectedBox") is None
        assert state.current_revision(uid) == before_failure

        document.openTransaction("Human-owned transaction")
        nested_ticket = state.begin_call(uid, "model.feature")
        try:
            runner.run(
                ticket=nested_ticket,
                document=document,
                transaction_name="Must not nest",
                reauthorize_turn=lambda: None,
                mutate=lambda target: create_box(target, "NestedBox"),
                verify=verify_box,
            )
        except NativeMutationError as exc:
            assert exc.error_code == NATIVE_TRANSACTION_ACTIVE
        else:
            raise AssertionError("nested Native transaction unexpectedly opened")
        document.abortTransaction()
        assert document.getObject("NestedBox") is None

        print("VIBECAD_NATIVE_MUTATION_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
