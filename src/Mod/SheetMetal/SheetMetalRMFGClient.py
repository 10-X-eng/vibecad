# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct RMFG REST transport shared by ribbon and native assistant workflows.

Run network calls on a worker. The caller owns sign-in, immutable model/export
snapshots, configuration decisions, and job persistence. This client preserves
pending response IDs and never retries a write or refreshes credentials itself.
There are no payment/order-creation operations here.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


API_ORIGIN = "https://api.rmfg.com"
MAX_STEP_BYTES = 50 * 1024 * 1024


class RMFGError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: Mapping = field(repr=False)
    body: bytes = field(repr=False)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to a response-selected destination.
        return None


def _http_request(method, url, headers, body):
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(_RejectRedirects())
    try:
        response = opener.open(request, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        return HTTPResult(response.status, dict(response.headers), response.read())


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", value):
        raise ValueError(f"{name} must be an exact RMFG identifier or operation key")
    return value


def _reject_constant(value):
    raise ValueError("Non-finite JSON value")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


class RMFGClient:
    def __init__(self, access_token, *, transport=_http_request):
        if not callable(access_token) or not callable(transport):
            raise TypeError("access_token and transport must be callable")
        self._access_token, self._transport = access_token, transport

    def _request(self, method, path, *, body=None, content_type=None, operation_key=None):
        headers = {"Accept": "application/json"}
        if operation_key is not None:
            headers["Idempotency-Key"] = _identifier(operation_key, "operation_key")
        if content_type is not None:
            headers["Content-Type"] = content_type
        token = self._access_token()
        if not isinstance(token, str) or not token or any(char.isspace() for char in token):
            raise RMFGError("Connect to RMFG before requesting manufacturing services.")
        headers["Authorization"] = "Bearer " + token
        try:
            result = self._transport(method, API_ORIGIN + path, headers, body)
        except OSError:
            # A write may already exist remotely. Its owner must resume/retry
            # with the same operation key, not create a second logical request.
            raise RMFGError("RMFG did not return a response. Retain the operation key before retrying.") from None
        if not isinstance(result, HTTPResult):
            raise TypeError("The RMFG transport must return HTTPResult")
        if not 200 <= result.status < 300:
            retry_after = next((value for name, value in result.headers.items()
                                if name.lower() == "retry-after"), None)
            raise RMFGError(f"RMFG returned HTTP {result.status}.",
                            status=result.status, retry_after=retry_after)
        try:
            payload = json.loads(result.body, parse_constant=_reject_constant, parse_float=_finite_float)
            if not isinstance(payload, dict):
                raise ValueError("Expected an object response")
        except (ValueError, UnicodeError):
            raise RMFGError("RMFG returned an invalid JSON response.") from None
        return payload

    def _write_json(self, path, payload, operation_key):
        key = _identifier(operation_key, "operation_key")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
        return self._request("POST", path, body=body, content_type="application/json", operation_key=key)

    def materials(self, *, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Catalog limit must be an integer between 1 and 500")
        query = {"limit": limit}
        if cursor is not None:
            if not isinstance(cursor, str) or not cursor:
                raise ValueError("Use the next_cursor string from the previous catalog page")
            query["cursor"] = cursor
        return self._request("GET", "/v1/materials?" + urlencode(query))

    def analyze(self, step_bytes, *, filename, operation_key):
        if not isinstance(step_bytes, bytes) or not 0 < len(step_bytes) <= MAX_STEP_BYTES:
            raise ValueError("Upload nonempty folded STEP bytes, at most 50 MiB")
        if (not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,119}", filename)
                or not filename.lower().endswith((".step", ".stp"))):
            raise ValueError("Use a STEP/STP basename without directory or control characters")
        key = _identifier(operation_key, "operation_key")
        # Stable multipart encoding makes an identical retry byte-for-byte
        # identical, including the envelope covered by server idempotency.
        digest = hashlib.sha256(filename.encode("ascii") + b"\0" + step_bytes).hexdigest()
        boundary = "vibecad-" + digest
        prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n')
        body = prefix.encode("ascii") + step_bytes + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self._request("POST", "/v1/analyze", body=body,
                             content_type="multipart/form-data; boundary=" + boundary, operation_key=key)

    def design(self, design_id):
        return self._request("GET", "/v1/designs/" + _identifier(design_id, "design_id"))

    def dfm(self, dfm_id):
        return self._request("GET", "/v1/dfm/" + _identifier(dfm_id, "dfm_id"))

    def quote(self, quote_id):
        return self._request("GET", "/v1/quotes/" + _identifier(quote_id, "quote_id"))

    def cart(self, cart_id):
        return self._request("GET", "/v1/carts/" + _identifier(cart_id, "cart_id"))

    def create_dfm(self, design_id, configuration, *, operation_key):
        if not isinstance(configuration, Mapping):
            raise TypeError("configuration must contain the selected part configuration")
        return self._write_json("/v1/dfm", {"design_id": _identifier(design_id, "design_id"),
                                          "configuration": dict(configuration)}, operation_key)

    def create_quote(self, items, *, operation_key):
        if not isinstance(items, (list, tuple)) or not items or any(not isinstance(item, Mapping) for item in items):
            raise ValueError("Quote items must contain the configured designs and design quantities")
        return self._write_json("/v1/quotes", {"items": list(items)}, operation_key)

    def create_checkout(self, items, *, operation_key):
        if not isinstance(items, (list, tuple)) or not items or any(not isinstance(item, Mapping) for item in items):
            raise ValueError("Checkout items must contain the exact ready quote configuration")
        return self._write_json("/v1/carts", {"items": list(items)}, operation_key)
