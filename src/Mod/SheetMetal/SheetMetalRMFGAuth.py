# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct browser authorization and single-use refresh-token ownership.

All methods that can contact RMFG belong on a worker. The caller schedules
poll(), respecting next_poll; this module neither sleeps nor runs a timer.
The credential store must serialize locked() across application processes and
persist writes before returning. No credential enters a document or AI context.
"""

from dataclasses import dataclass, field
import json
import math
import time
from urllib.parse import urlencode, urlsplit
import uuid

from SheetMetalRMFGClient import API_ORIGIN, HTTPResult, _http_request, _reject_constant


SCOPES = "designs dfm quotes carts"
CLIENT_ID = "rmfg-agent"


class RMFGAuthError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _reconnect():
    return RMFGAuthError("reconnect", "Reconnect to RMFG in the browser before continuing.")


def _positive_integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise RMFGAuthError("invalid_response", "RMFG returned invalid authorization timing.")
    return value


def _secret(value):
    if not isinstance(value, str) or not value or len(value) > 16384 or any(c.isspace() for c in value):
        raise RMFGAuthError("invalid_response", "RMFG returned incomplete credentials.")
    return value


def _browser_url(value):
    try:
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in ("rmfg.com", "www.rmfg.com", "api.rmfg.com")
                or url.username is not None or url.password is not None or url.port not in (None, 443)):
            raise ValueError
    except ValueError:
        raise RMFGAuthError("invalid_response", "RMFG returned an invalid browser approval link.") from None
    return value


def _stored_tokens(record):
    try:
        tokens = record["tokens"]
        _secret(tokens["access_token"])
        _secret(tokens["refresh_token"])
        expiry = tokens["expires_at"]
        if (type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0
                or not set(SCOPES.split()).issubset(tokens["scope"])):
            raise ValueError
        return tokens
    except (KeyError, TypeError, ValueError, RMFGAuthError):
        raise _reconnect() from None


@dataclass
class DeviceAuthorization:
    generation: str
    device_code: str = field(repr=False)
    user_code: str = field(repr=False)
    verification_uri: str = field(repr=False)
    verification_uri_complete: str = field(repr=False)
    expires_at: float
    interval: int
    next_poll: float
    state: str = "pending"


class RMFGAuth:
    def __init__(self, store, *, transport=_http_request, clock=time.time, monotonic=time.monotonic):
        self._store, self._transport = store, transport
        self._clock, self._monotonic = clock, monotonic

    def _post(self, path, fields, *, empty=False):
        try:
            response = self._transport("POST", API_ORIGIN + path,
                {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                urlencode({"client_id": CLIENT_ID, **fields}).encode("ascii"))
        except OSError:
            raise RMFGAuthError("network", "RMFG did not return an authorization response.") from None
        if not isinstance(response, HTTPResult):
            raise RMFGAuthError("invalid_response", "RMFG returned an invalid authorization response.")
        try:
            if len(response.body) > 65536:
                raise ValueError
            payload = {} if empty and not response.body else json.loads(
                response.body, parse_constant=_reject_constant)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise RMFGAuthError("invalid_response", "RMFG returned an invalid authorization response.") from None
        return response.status, payload

    def _write(self, record):
        try:
            self._store.write(record)
        except Exception:
            raise RMFGAuthError("credential_store", "RMFG credentials could not be saved securely. Reconnect before continuing.") from None

    def _tokens(self, payload):
        access, refresh = _secret(payload.get("access_token")), _secret(payload.get("refresh_token"))
        scope = payload.get("scope")
        if payload.get("token_type") != "Bearer" or not isinstance(scope, str):
            raise RMFGAuthError("invalid_response", "RMFG returned incomplete credentials.")
        granted = set(scope.split())
        if not set(SCOPES.split()).issubset(granted):
            raise RMFGAuthError("missing_scopes", "Approve designs, manufacturing checks, quotes and checkout links when connecting to RMFG.")
        lifetime = _positive_integer(payload.get("expires_in"), 900)
        return {"access_token": access, "refresh_token": refresh,
                "expires_at": self._clock() + lifetime, "scope": sorted(granted)}

    def begin(self):
        with self._store.locked():
            status, payload = self._post("/v1/oauth/device/code", {"scope": SCOPES})
            if status != 200:
                raise RMFGAuthError("authorization_failed", "RMFG browser sign-in could not be started.")
            interval = _positive_integer(payload.get("interval", 5), 3600)
            lifetime = _positive_integer(payload.get("expires_in"), 86400)
            now = self._monotonic()
            attempt = DeviceAuthorization(uuid.uuid4().hex, _secret(payload.get("device_code")),
                _secret(payload.get("user_code")), _browser_url(payload.get("verification_uri")),
                _browser_url(payload.get("verification_uri_complete")), now + lifetime, interval, now + interval)
            self._write({"state": "authorizing", "generation": attempt.generation})
            return attempt

    def _current_attempt(self, attempt):
        record = self._store.read() or {}
        if (attempt.state != "pending" or record.get("state") != "authorizing"
                or record.get("generation") != attempt.generation):
            raise _reconnect()

    def cancel(self, attempt):
        with self._store.locked():
            self._current_attempt(attempt)
            self._write({"state": "disconnected"})
            attempt.state = "cancelled"

    def poll(self, attempt):
        with self._store.locked():
            self._current_attempt(attempt)
            now = self._monotonic()
            if now >= attempt.expires_at:
                attempt.state = "expired"
                self._write({"state": "disconnected"})
                raise RMFGAuthError("expired", "The RMFG approval code expired. Start sign-in again.")
            if now < attempt.next_poll:
                return "pending"
            # A lost successful exchange is ambiguous too: do not replay it.
            # Only explicit pending/slow_down responses permit another poll.
            self._write({"state": "exchanging", "generation": attempt.generation})
            status, payload = self._post("/v1/oauth/token", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": attempt.device_code})
            error = payload.get("error")
            if status == 400 and error in ("authorization_pending", "slow_down"):
                if error == "slow_down":
                    attempt.interval += 5
                attempt.next_poll = self._monotonic() + attempt.interval
                self._write({"state": "authorizing", "generation": attempt.generation})
                return "pending"
            if status != 200:
                attempt.state = "failed"
                self._write({"state": "disconnected"})
                if error in ("access_denied", "expired_token"):
                    raise RMFGAuthError(error, "RMFG sign-in was declined or expired. Start sign-in again to reconnect.")
                raise RMFGAuthError("authorization_failed", "RMFG sign-in failed. Start sign-in again to reconnect.")
            tokens = self._tokens(payload)
            self._write({"state": "connected", "tokens": tokens})
            attempt.state = "connected"
            return "connected"

    def status(self):
        """Read a public connection summary on a worker, without token refresh."""
        with self._store.locked():
            record = self._store.read() or {}
            state = record.get("state", "disconnected")
            if state == "connected":
                tokens = _stored_tokens(record)
                return {"state": "connected", "scopes": list(tokens["scope"]),
                        "access_token_current": self._clock() + 30 < tokens["expires_at"]}
            return {"state": state if state in ("disconnected", "authorizing") else "reconnect"}

    def access_token(self):
        with self._store.locked():
            record = self._store.read() or {}
            if record.get("state") != "connected":
                raise _reconnect()
            tokens = _stored_tokens(record)
            if self._clock() + 30 < tokens["expires_at"]:
                return tokens["access_token"]
            refresh = tokens["refresh_token"]
            # Persist consumption BEFORE the request. A crash or ambiguous
            # response leaves a reconnect marker, never a reusable old token.
            self._write({"state": "refreshing"})
            try:
                status, payload = self._post("/v1/oauth/token", {
                    "grant_type": "refresh_token", "refresh_token": refresh})
                if status != 200:
                    raise _reconnect()
                replacement = self._tokens(payload)
                self._write({"state": "connected", "tokens": replacement})
            except RMFGAuthError:
                raise _reconnect() from None
            return replacement["access_token"]

    def disconnect(self):
        with self._store.locked():
            record = self._store.read() or {}
            refresh = record.get("tokens", {}).get("refresh_token")
            self._write({"state": "disconnected"})
            if refresh:
                status, _ = self._post("/v1/oauth/revoke", {"token": refresh}, empty=True)
                if status != 200:
                    raise RMFGAuthError("revocation_failed", "Disconnected locally. Review the connection on RMFG's account page to confirm revocation.")
