# SPDX-License-Identifier: LGPL-2.1-or-later

"""Qt responsiveness gate for bounded Native Analyze context capture."""

from __future__ import annotations

import sys
import threading
import time
import traceback

from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADNativeAnalyzeContext import capture_responsive_analyze_snapshot


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    main_thread = threading.get_ident()
    tick_timer = QtCore.QTimer()
    poll_timer = QtCore.QTimer()
    tick_count = 0
    batch_count = 0
    result: dict[str, object] = {}
    worker = None
    exit_code = 1

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        tick_timer.stop()
        poll_timer.stop()
        application.exit(exit_code)

    try:
        VibeGui._ensure_document_thread_invoker()
        request = {
            "document_uid": "responsive-gui-document",
            "structural_revision": 1,
            "object_names": [f"Object{index}" for index in range(80)],
        }

        def tick() -> None:
            nonlocal tick_count
            tick_count += 1

        def capture_batch(_request, names):
            nonlocal batch_count
            assert threading.get_ident() == main_thread
            batch_count += 1
            # Model a costly live-object slice. Qt must run between slices.
            time.sleep(0.02)
            return {"names": list(names)}

        def capture_clipping(_request):
            assert threading.get_ident() == main_thread
            return {"enabled": False}

        def finalize(_request, parts, clipping):
            assert threading.get_ident() != main_thread
            return {
                "object_count": sum(len(part["names"]) for part in parts),
                "clipping": dict(clipping),
            }

        def prepare() -> None:
            try:
                result["snapshot"] = capture_responsive_analyze_snapshot(
                    request,
                    dispatch_to_document_thread=VibeGui._dispatch_to_document_thread,
                    capture_batch=capture_batch,
                    capture_clipping=capture_clipping,
                    finalize=finalize,
                )
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        worker = threading.Thread(
            target=prepare,
            name="VibeCAD-test-Analyze-context",
            daemon=True,
        )
        worker.start()

        def inspect_result() -> None:
            if worker is not None and worker.is_alive():
                return
            try:
                if "error" in result:
                    raise AssertionError(result.get("traceback")) from result["error"]
                assert result.get("snapshot") == {
                    "object_count": 80,
                    "clipping": {"enabled": False},
                }
                assert batch_count == 10
                assert tick_count >= 2, (
                    "Qt timers did not run between bounded Analyze capture slices."
                )
                print(
                    "VIBECAD_NATIVE_ANALYZE_CONTEXT_RESPONSIVE_GUI_OK "
                    f"batches={batch_count} qt_ticks={tick_count}",
                    flush=True,
                )
                finish(0)
            except BaseException:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        tick_timer.timeout.connect(tick)
        tick_timer.start(5)
        poll_timer.timeout.connect(inspect_result)
        poll_timer.start(20)
        QtCore.QTimer.singleShot(15000, lambda: finish(1))
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
