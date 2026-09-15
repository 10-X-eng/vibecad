# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real cross-process locking with a fake credential backend; no user secrets."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock


class TestRMFGCredentialStore(unittest.TestCase):
    def setUp(self):
        from SheetMetalRMFGCredentialStore import CredentialStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.backend = Mock()
        self.backend.get_password.return_value = None
        self.store = CredentialStore(self.path, backend=self.backend)

    def test_tokens_use_keyring_and_never_plaintext_files(self):
        record = {"state": "connected", "tokens": {"access_token": "private-access", "refresh_token": "private-refresh"}}
        with self.store.locked():
            self.store.write(record)
            args = self.backend.set_password.call_args.args
            self.assertEqual(args[0], "VibeCAD.RMFG")
            self.backend.get_password.return_value = args[2]
            self.assertEqual(self.store.read(), record)
            for path in self.path.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"private-", path.read_bytes())
        self.assertEqual(list(self.path.rglob("*.json")), [])

    def test_same_profile_is_locked_across_processes_then_released(self):
        script = '''import sys
from SheetMetalRMFGCredentialStore import CredentialStore
from SheetMetalRMFGAuth import RMFGAuthError
try:
    with CredentialStore(sys.argv[1], backend=object()).locked():
        print("acquired")
except RMFGAuthError as error:
    print(error.code)
'''
        def probe():
            result = subprocess.run([sys.executable, "-c", script, str(self.path)],
                                    capture_output=True, text=True, env=os.environ.copy())
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        with self.store.locked():
            self.assertEqual(probe(), "busy")
        self.assertEqual(probe(), "acquired")

    def test_distinct_profiles_have_distinct_lock_and_keyring_accounts(self):
        from SheetMetalRMFGCredentialStore import CredentialStore
        other_backend = Mock()
        other = CredentialStore(self.path/"other", backend=other_backend)
        with self.store.locked(), other.locked():
            self.store.write({"state": "disconnected"})
            other.write({"state": "disconnected"})
        first = self.backend.set_password.call_args.args[1]
        second = other_backend.set_password.call_args.args[1]
        self.assertNotEqual(first, second)

    def test_keyring_failure_is_redacted_with_no_fallback(self):
        from SheetMetalRMFGAuth import RMFGAuthError
        self.backend.set_password.side_effect = RuntimeError("private-access")
        with self.store.locked():
            with self.assertRaises(RMFGAuthError) as caught:
                self.store.write({"state": "disconnected"})
            self.assertNotIn("private-access", str(caught.exception))
            self.backend.get_password.side_effect = RuntimeError("private-refresh")
            with self.assertRaises(RMFGAuthError) as caught:
                self.store.read()
            self.assertNotIn("private-refresh", str(caught.exception))

    def test_malformed_or_oversized_credential_records_fail_closed(self):
        from SheetMetalRMFGAuth import RMFGAuthError
        with self.store.locked():
            for value in ('["private-access"]', '{broken', 'x'*65537):
                self.backend.get_password.return_value = value
                with self.subTest(size=len(value)), self.assertRaises(RMFGAuthError):
                    self.store.read()
