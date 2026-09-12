# SPDX-License-Identifier: LGPL-2.1-or-later
"""Window-local Windows input latency for isolated GUI performance probes."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import uuid


class WindowInputProbe:
    """Measure Windows queue age without depending on the GUI's GIL or timers.

    The existing sender posts an otherwise unused function key only to this
    process's window. Its messages are consumed here; physical keyboard state
    and desktop focus are never changed. Close signals the sender without
    waiting on the GUI thread. The sender also exits if its parent disappears.
    """

    def __init__(self, window, on_input):
        import FreeCAD as App
        from PySide6 import QtCore, QtWidgets

        self._closed = False
        self._app = QtWidgets.QApplication.instance()
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._kernel.CreateEventW.argtypes = (
            ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        self._kernel.CreateEventW.restype = wintypes.HANDLE
        self._kernel.SetEvent.argtypes = (wintypes.HANDLE,)
        self._kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel.GetTickCount.restype = wintypes.DWORD
        stop_name = f'Local\\VibeCADInputProbe-{os.getpid()}-{uuid.uuid4().hex}'
        self._stop = self._kernel.CreateEventW(None, True, False, stop_name)
        if not self._stop:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel = self._kernel

        class NativeFilter(QtCore.QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                value = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if value.message == 0x0100 and value.wParam == 0x86:
                    on_input((kernel.GetTickCount() - value.time) & 0xffffffff)
                return False, 0

        class KeyFilter(QtCore.QObject):
            def eventFilter(self, watched, event):
                return (event.type() in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease)
                        and event.key() == QtCore.Qt.Key_F23)

        self._native_filter = NativeFilter()
        self._key_filter = KeyFilter(window)
        self._app.installNativeEventFilter(self._native_filter)
        self._app.installEventFilter(self._key_filter)
        python_bin = Path(os.environ.get(
            'VIBECAD_TEST_PYTHON', str(Path(App.getHomePath()) / 'bin')))
        try:
            self._sender = subprocess.Popen([
                str(python_bin / 'pythonw.exe'),
                str(Path(__file__).with_name('native_input_sender.py')),
                str(int(window.winId())), str(os.getpid()), stop_name,
            ], creationflags=subprocess.CREATE_NO_WINDOW)
        except BaseException:
            self.close()
            raise
        self._app.aboutToQuit.connect(self.close)

    def check(self):
        if not self._closed and self._sender.poll() is not None:
            raise RuntimeError('The independent Windows input sampler exited unexpectedly')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._kernel.SetEvent(self._stop)
        self._kernel.CloseHandle(self._stop)
        self._app.removeNativeEventFilter(self._native_filter)
        self._app.removeEventFilter(self._key_filter)
