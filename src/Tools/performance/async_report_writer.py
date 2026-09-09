# SPDX-License-Identifier: LGPL-2.1-or-later
"""Coalesced diagnostic output: no serialization or disk I/O on the measured GUI."""
from concurrent.futures import Future
import json
import threading


class AsyncReportWriter:
    """Own one diagnostic writer, at most one in-flight and one pending snapshot.

    Submitted snapshots must not be mutated. Await the final Future by polling
    from the probe's event loop before exiting; never wait on it in a GUI callback.
    This is test instrumentation, not an application execution pool.
    """

    def __init__(self, path):
        self._path = path
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='performance-report', daemon=True)
        self._thread.start()

    def submit(self, snapshot):
        future = Future()
        with self._condition:
            if self._closed:
                raise RuntimeError('Report writer is closed')
            if self._pending is not None:
                self._pending[0].cancel()
            self._pending = future, snapshot
            self._condition.notify()
        return future

    def close(self):
        """Drain pending output and exit without blocking the caller."""
        with self._condition:
            self._closed = True
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._pending is None:
                    return
                future, snapshot = self._pending
                self._pending = None
            if not future.set_running_or_notify_cancel():
                continue
            try:
                self._path.write_text(json.dumps(snapshot, separators=(',', ':')), encoding='utf-8')
            except Exception as error:
                future.set_exception(error)
            else:
                future.set_result(None)
