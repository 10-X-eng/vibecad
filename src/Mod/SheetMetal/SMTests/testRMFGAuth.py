# SPDX-License-Identifier: LGPL-2.1-or-later
"""OAuth protocol tests never contact RMFG or the user's credential store."""

from contextlib import contextmanager
import json
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs


class MemoryStore:
    def __init__(self):
        self.record = None
        self.mutex = threading.Lock()

    @contextmanager
    def locked(self):
        from SheetMetalRMFGAuth import RMFGAuthError
        if not self.mutex.acquire(blocking=False):
            raise RMFGAuthError("busy", "An RMFG connection operation is already running.")
        try:
            yield
        finally:
            self.mutex.release()

    def read(self):
        return json.loads(json.dumps(self.record))

    def write(self, record):
        self.record = json.loads(json.dumps(record))


class TestRMFGAuth(unittest.TestCase):
    def setUp(self):
        from SheetMetalRMFGAuth import RMFGAuth, RMFGAuthError
        from SheetMetalRMFGClient import HTTPResult
        self.error, self.response = RMFGAuthError, HTTPResult
        self.store = MemoryStore()
        self.now = 1000
        self.transport = Mock()
        self.auth = RMFGAuth(self.store, transport=self.transport,
                             clock=lambda: self.now, monotonic=lambda: self.now)

    def payload(self, value, status=200):
        return self.response(status, {}, json.dumps(value).encode())

    def tokens(self, suffix="1", **changes):
        return {"access_token": "private-access-"+suffix,
                "refresh_token": "private-refresh-"+suffix,
                "token_type": "Bearer", "expires_in": 900,
                "scope": "designs dfm quotes carts", **changes}

    def begin(self, **changes):
        self.transport.return_value = self.payload({
            "device_code": "private-device", "user_code": "ABCD-EFGH",
            "verification_uri_complete": "https://www.rmfg.com/activate?user_code=ABCD-EFGH",
            "verification_uri": "https://www.rmfg.com/activate",
            "expires_in": 600, "interval": 5, **changes})
        return self.auth.begin()

    def connect(self):
        attempt = self.begin()
        self.now += 5
        self.transport.return_value = self.payload(self.tokens())
        self.assertEqual(self.auth.poll(attempt), "connected")
        return attempt

    def test_browser_flow_requests_only_quoting_scopes_and_obeys_poll_interval(self):
        attempt = self.begin()
        method, url, headers, body = self.transport.call_args.args
        self.assertEqual((method, url), ("POST", "https://api.rmfg.com/v1/oauth/device/code"))
        self.assertEqual(parse_qs(body.decode()), {
            "client_id": ["rmfg-agent"], "scope": ["designs dfm quotes carts"]})
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("private-device", repr(attempt))
        self.assertNotIn("ABCD-EFGH", repr(attempt))
        calls = self.transport.call_count
        self.assertEqual(self.auth.poll(attempt), "pending")
        self.assertEqual(self.transport.call_count, calls)
        self.now += 5
        self.transport.return_value = self.payload({"error": "authorization_pending"}, 400)
        self.assertEqual(self.auth.poll(attempt), "pending")
        self.now += 5
        self.transport.return_value = self.payload({"error": "slow_down"}, 400)
        self.assertEqual(self.auth.poll(attempt), "pending")
        calls = self.transport.call_count
        self.now += 9
        self.assertEqual(self.auth.poll(attempt), "pending")
        self.assertEqual(self.transport.call_count, calls)
        self.now += 1
        self.transport.return_value = self.payload(self.tokens())
        self.assertEqual(self.auth.poll(attempt), "connected")
        self.assertEqual(self.auth.access_token(), "private-access-1")

    def test_cancel_expiry_denial_and_replaced_attempt_never_keep_polling(self):
        for failure in ("cancel", "expire", "access_denied", "replace"):
            with self.subTest(failure=failure):
                attempt = self.begin()
                if failure == "cancel":
                    self.auth.cancel(attempt)
                elif failure == "expire":
                    self.now += 601
                elif failure == "replace":
                    self.begin()
                else:
                    self.now += 5
                    self.transport.return_value = self.payload({"error": "access_denied"}, 400)
                with self.assertRaises(self.error):
                    self.auth.poll(attempt)
                calls = self.transport.call_count
                with self.assertRaises(self.error):
                    self.auth.poll(attempt)
                self.assertEqual(self.transport.call_count, calls)

    def test_refresh_consumes_old_token_before_network_and_stores_replacement(self):
        self.connect()
        self.now += 880
        def refreshed(*args):
            self.assertEqual(self.store.record["state"], "refreshing")
            self.assertNotIn("private-refresh-1", json.dumps(self.store.record))
            form = parse_qs(args[3].decode())
            self.assertEqual(form["refresh_token"], ["private-refresh-1"])
            return self.payload(self.tokens("2"))
        self.transport.side_effect = refreshed
        self.assertEqual(self.auth.access_token(), "private-access-2")
        self.assertEqual(self.store.record["tokens"]["refresh_token"], "private-refresh-2")
        calls = self.transport.call_count
        self.assertEqual(self.auth.access_token(), "private-access-2")
        self.assertEqual(self.transport.call_count, calls)

    def test_ambiguous_refresh_or_failed_replacement_save_requires_reconnection(self):
        for failure in ("network", "invalid", "save"):
            with self.subTest(failure=failure):
                self.transport.side_effect = None
                self.connect()
                self.now += 880
                original = self.store.write
                if failure == "network":
                    self.transport.side_effect = OSError("private-refresh-1")
                elif failure == "invalid":
                    self.transport.return_value = self.payload(self.tokens(refresh_token=""))
                else:
                    self.transport.return_value = self.payload(self.tokens("2"))
                    def write(record):
                        if record["state"] == "connected":
                            raise RuntimeError("private-access-2")
                        original(record)
                    self.store.write = write
                try:
                    with self.assertRaises(self.error) as caught:
                        self.auth.access_token()
                    self.assertNotIn("private-", str(caught.exception))
                    calls = self.transport.call_count
                    with self.assertRaises(self.error):
                        self.auth.access_token()
                    self.assertEqual(self.transport.call_count, calls)
                finally:
                    self.store.write = original

    def test_second_instance_cannot_refresh_while_first_holds_store_lock(self):
        from SheetMetalRMFGAuth import RMFGAuth
        self.connect()
        self.now += 880
        second = RMFGAuth(self.store, transport=self.transport, clock=lambda: self.now)
        def refreshed(*args):
            with self.assertRaises(self.error) as caught:
                second.access_token()
            self.assertEqual(caught.exception.code, "busy")
            return self.payload(self.tokens("2"))
        self.transport.side_effect = refreshed
        self.assertEqual(self.auth.access_token(), "private-access-2")
        self.assertEqual(second.access_token(), "private-access-2")

    def test_missing_scopes_bad_tokens_and_foreign_browser_links_are_rejected(self):
        for changes in ({"verification_uri_complete": "https://evil.invalid/"},
                        {"verification_uri_complete": "https://www.rmfg.com@evil.invalid/"},
                        {"expires_in": True}, {"interval": 0}):
            with self.subTest(changes=changes), self.assertRaises(self.error):
                self.begin(**changes)
        for changes in ({"scope": "designs"}, {"token_type": "Basic"},
                        {"access_token": "a\nb"}, {"expires_in": float("inf")}):
            attempt = self.begin()
            self.now += 5
            self.transport.return_value = self.payload(self.tokens(**changes))
            with self.subTest(changes=changes), self.assertRaises(self.error):
                self.auth.poll(attempt)

    def test_disconnect_forgets_locally_before_revocation_and_redacts_failures(self):
        self.connect()
        def failed(*args):
            self.assertEqual(self.store.record["state"], "disconnected")
            self.assertEqual(args[1], "https://api.rmfg.com/v1/oauth/revoke")
            raise OSError("private-refresh-1")
        self.transport.side_effect = failed
        with self.assertRaises(self.error) as caught:
            self.auth.disconnect()
        self.assertNotIn("private-refresh-1", str(caught.exception))
        with self.assertRaises(self.error):
            self.auth.access_token()

    def test_corrupt_saved_tokens_are_rejected_before_network(self):
        for tokens in ({}, {"expires_at": float("inf"), "access_token": "private-access",
                           "refresh_token": "private-refresh"},
                       {"expires_at": self.now+900, "access_token": "bad\nvalue", "refresh_token": "good"}):
            self.store.record = {"state": "connected", "tokens": tokens}
            with self.subTest(tokens=list(tokens)), self.assertRaises(self.error):
                self.auth.access_token()
        self.transport.assert_not_called()

    def test_credential_write_failure_prevents_consuming_a_refresh_token(self):
        self.connect()
        self.now += 880
        calls = self.transport.call_count
        self.store.write = Mock(side_effect=RuntimeError("private-refresh"))
        with self.assertRaises(self.error):
            self.auth.access_token()
        self.assertEqual(self.transport.call_count, calls)
        self.assertEqual(self.store.record["state"], "connected")

    def test_connection_status_contains_no_credentials_and_does_not_refresh(self):
        self.assertEqual(self.auth.status(), {"state": "disconnected"})
        self.connect()
        calls = self.transport.call_count
        status = self.auth.status()
        self.assertEqual(status["state"], "connected")
        self.assertTrue(status["access_token_current"])
        self.assertEqual(status["scopes"], ["carts", "designs", "dfm", "quotes"])
        self.assertNotIn("private-", json.dumps(status))
        self.now += 1000
        self.assertFalse(self.auth.status()["access_token_current"])
        self.assertEqual(self.transport.call_count, calls)
        self.store.record = {"state": "refreshing"}
        self.assertEqual(self.auth.status(), {"state": "reconnect"})
