"""Window-local diagnostic input, independent of the measured GUI's Python GIL."""
import ctypes
from ctypes import wintypes
import sys

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
user = ctypes.WinDLL('user32', use_last_error=True)
kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel.OpenEventW.restype = wintypes.HANDLE
kernel.WaitForMultipleObjects.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                        wintypes.BOOL, wintypes.DWORD)
kernel.WaitForMultipleObjects.restype = wintypes.DWORD
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user.PostMessageW.restype = wintypes.BOOL
user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user.GetWindowThreadProcessId.restype = wintypes.DWORD

window, pid, stop_name = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
parent = kernel.OpenProcess(0x00100000, False, pid)
stop = kernel.OpenEventW(0x00100000, False, stop_name)
if not parent or not stop:
    raise ctypes.WinError(ctypes.get_last_error())
handles = (wintypes.HANDLE * 2)(parent, stop)
try:
    while kernel.WaitForMultipleObjects(2, handles, False, 250) == 258:
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value != pid:
            break
        for message, flags in ((0x0100, 1), (0x0101, 0xC0000001)):
            if not user.PostMessageW(window, message, 0x86, flags):
                raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.CloseHandle(stop)
    kernel.CloseHandle(parent)
