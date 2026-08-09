# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt responsiveness gate for expensive Native background preparation."""

from __future__ import annotations

import sys
import threading
import time
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    document_name = ""
    manager = None
    tick_timer = QtCore.QTimer()
    poll_timer = QtCore.QTimer()
    tick_count = 0
    main_thread = threading.get_ident()
    prepare_thread = None
    commit_thread = None
    exit_code = 1

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        tick_timer.stop()
        poll_timer.stop()
        if document_name and document_name in App.listDocuments():
            App.closeDocument(document_name)
        application.exit(exit_code)

    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeBackgroundGate")
        document_name = document.Name
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        manager = get_service().native_background_manager()

        def tick() -> None:
            nonlocal tick_count
            tick_count += 1

        def prepare(cancelled, progress):
            nonlocal prepare_thread
            prepare_thread = threading.get_ident()
            for index in range(30):
                if cancelled():
                    return {}
                progress(5 + index * 2, f"Detached phase {index + 1}")
                time.sleep(0.02)
            return {"detached": True}

        def validate() -> None:
            assert threading.get_ident() == main_thread
            assert App.getDocument(document_name) is document

        def commit(prepared):
            nonlocal commit_thread
            commit_thread = threading.get_ident()
            assert prepared == {"detached": True}
            return {"committed": True}

        submitted = manager.submit(
            document_uid=str(document.Uid),
            capability_name="analyze.solve",
            prepare=prepare,
            validate_before_commit=validate,
            commit=commit,
            dispatch_to_document_thread=VibeGui._dispatch_to_document_thread,
        )

        def poll() -> None:
            try:
                snapshot = manager.snapshot(submitted.job_id)
                if not snapshot.terminal:
                    return
                assert snapshot.phase == "completed"
                assert snapshot.result == {"committed": True}
                assert prepare_thread is not None and prepare_thread != main_thread
                assert commit_thread == main_thread
                assert tick_count >= 2

                def prepare_until_closed(cancelled, progress):
                    progress(10, "Waiting for document close")
                    while not cancelled():
                        time.sleep(0.01)
                    return {}

                def closed_document_commit(_prepared):
                    raise AssertionError("closed document job committed")

                close_job = manager.submit(
                    document_uid=str(document.Uid),
                    capability_name="mesh.generate",
                    prepare=prepare_until_closed,
                    validate_before_commit=lambda: None,
                    commit=closed_document_commit,
                    dispatch_to_document_thread=VibeGui._dispatch_to_document_thread,
                )

                def poll_close() -> None:
                    try:
                        closed = manager.snapshot(close_job.job_id)
                        if not closed.terminal:
                            return
                        assert closed.phase == "cancelled"
                        print("VIBECAD_NATIVE_BACKGROUND_GUI_OK", flush=True)
                        finish(0)
                    except Exception:
                        traceback.print_exc(file=sys.__stderr__)
                        finish(1)

                poll_timer.stop()
                poll_timer.timeout.disconnect(poll)
                poll_timer.timeout.connect(poll_close)
                poll_timer.start(20)
                QtCore.QTimer.singleShot(
                    100,
                    lambda: App.closeDocument(document_name),
                )
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        tick_timer.timeout.connect(tick)
        tick_timer.start(5)
        poll_timer.timeout.connect(poll)
        poll_timer.start(20)
        QtCore.QTimer.singleShot(5000, lambda: finish(1))
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
