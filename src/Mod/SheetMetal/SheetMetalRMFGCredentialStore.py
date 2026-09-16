# SPDX-License-Identifier: LGPL-2.1-or-later
"""OS credential storage and a shared process lock for RMFG token rotation.

The only disk file is Qt's lock metadata. Secret Service, macOS Keychain or
Windows Credential Manager stores the record; there is no plaintext fallback.
Each application profile has its own keyring account and lock identity.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

from SheetMetalRMFGAuth import RMFGAuthError


def _backend():
    # Select the native secure backend explicitly. Optional keyrings.alt
    # plugins must not silently substitute a plaintext password file.
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
    elif sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring as Keyring
    else:
        from keyring.backends.SecretService import Keyring
    return Keyring()


class CredentialStore:
    def __init__(self, profile_directory, *, backend=None):
        profile = Path(profile_directory).resolve()
        self._directory = profile/"rmfg"
        self._account = hashlib.sha256(str(profile).encode("utf-8")).hexdigest()
        self._backend_override = backend

    @contextmanager
    def locked(self):
        from PySide import QtCore
        try:
            self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = QtCore.QLockFile(str(self._directory/"credentials.lock"))
            # Long network operations must not make a live lock stale. Qt
            # still detects a dead owning process after an application crash.
            lock.setStaleLockTime(0)
        except (OSError, RuntimeError):
            raise RMFGAuthError("credential_store", "The RMFG connection lock could not be created.") from None
        if not lock.tryLock(0):
            raise RMFGAuthError("busy", "An RMFG connection operation is already running in another window or instance.")
        try:
            yield
        finally:
            lock.unlock()

    def read(self):
        try:
            backend = self._backend_override if self._backend_override is not None else _backend()
            value = backend.get_password("VibeCAD.RMFG", self._account)
            if value is None:
                return None
            if not isinstance(value, str) or len(value) > 65536:
                raise ValueError
            record = json.loads(value)
            if not isinstance(record, dict):
                raise ValueError
            return record
        except Exception:
            raise RMFGAuthError("credential_store", "Unlock the system credential store or reconnect to RMFG.") from None

    def write(self, record):
        try:
            if not isinstance(record, dict):
                raise ValueError
            value = json.dumps(record, separators=(",", ":"), allow_nan=False)
            if len(value) > 65536:
                raise ValueError
            backend = self._backend_override if self._backend_override is not None else _backend()
            backend.set_password("VibeCAD.RMFG", self._account, value)
        except Exception:
            raise RMFGAuthError("credential_store", "RMFG credentials could not be saved in the system credential store.") from None
