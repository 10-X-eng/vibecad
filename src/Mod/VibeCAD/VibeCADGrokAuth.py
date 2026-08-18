# SPDX-License-Identifier: LGPL-2.1-or-later

"""First-class Grok / X / xAI OAuth for VibeCAD.

xAI documents browser OIDC and RFC 8628 device-code login against
``https://auth.x.ai`` (see the published OpenID configuration and the official
Grok CLI in ``xai-org/grok-build``). VibeCAD does not ship a private xAI app
registration. The only public client xAI allowlists for this flow is the Grok
CLI public client shipped in that repository. Tokens are stored in a private
VibeCAD Grok credential directory and are never written to ordinary user.cfg.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import hashlib
import base64
import sys
import threading
import time
from typing import Any, Callable
from urllib import error, parse, request
from urllib.parse import urlparse


GROK_HOME_ENV = "VIBECAD_GROK_HOME"
GROK_AUTH_FILENAME = "auth.json"
DEFAULT_XAI_API_BASE = "https://api.x.ai/v1"
XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_AUTHORIZATION_ENDPOINT = "https://auth.x.ai/oauth2/authorize"
XAI_DEVICE_CODE_ENDPOINT = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
XAI_USERINFO_ENDPOINT = "https://auth.x.ai/oauth2/userinfo"
XAI_REVOCATION_ENDPOINT = "https://auth.x.ai/oauth2/revoke"
# Official Grok CLI public client from xai-org/grok-build
# (crates/codegen/xai-grok-shell/src/auth/config.rs). Public client, no secret.
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "grok-cli:access",
    "api:access",
    "conversations:read",
    "conversations:write",
    "workspaces:read",
    "workspaces:write",
)
XAI_OAUTH_REFERRER = "vibecad"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
LOGIN_TIMEOUT_SECONDS = 15.0 * 60.0
DEFAULT_DEVICE_POLL_INTERVAL_SECONDS = 5.0
DEVICE_SLOW_DOWN_INCREMENT_SECONDS = 5.0
TOKEN_REFRESH_SKEW_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 20.0


class GrokAuthError(RuntimeError):
    """Raised when Grok / xAI OAuth or credential storage fails."""


@dataclass(frozen=True)
class GrokAccount:
    email: str = ""
    subject: str = ""
    name: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "email": self.email,
            "subject": self.subject,
            "name": self.name,
        }


@dataclass
class GrokTokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    token_type: str = "Bearer"
    scope: str = ""
    id_token: str = ""
    account: GrokAccount = GrokAccount()

    @property
    def expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        return time.time() >= (self.expires_at - TOKEN_REFRESH_SKEW_SECONDS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "issuer": XAI_OAUTH_ISSUER,
            "client_id": XAI_OAUTH_CLIENT_ID,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type or "Bearer",
            "scope": self.scope,
            "id_token": self.id_token,
            "account": self.account.as_dict(),
        }


def grok_home() -> Path:
    override = str(os.environ.get(GROK_HOME_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return root / "VibeCAD" / "Grok"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VibeCAD" / "Grok"
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return root / "VibeCAD" / "grok"


def grok_auth_path() -> Path:
    return grok_home() / GROK_AUTH_FILENAME


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _scope_string() -> str:
    return " ".join(XAI_OAUTH_SCOPES)


def _form_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "x-grok-client-surface": "ui",
    }


def _json_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }


def _read_json_response(response: Any) -> dict[str, Any]:
    raw = response.read() if hasattr(response, "read") else b""
    if not raw:
        return {}
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _http_form(
    url: str,
    fields: dict[str, str],
    *,
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
    opener: Any | None = None,
) -> dict[str, Any]:
    body = parse.urlencode(fields).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers=_form_headers(),
        method="POST",
    )
    open_call = opener or request.urlopen
    try:
        response = open_call(http_request, timeout=timeout_seconds)
        try:
            return _read_json_response(response)
        finally:
            if hasattr(response, "close"):
                response.close()
    except error.HTTPError as exc:
        detail = ""
        try:
            payload = _read_json_response(exc)
            detail = str(
                payload.get("error_description") or payload.get("error") or ""
            ).strip()
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise GrokAuthError(
            f"xAI OAuth request failed with HTTP {exc.code}{suffix}."
        ) from exc
    except GrokAuthError:
        raise
    except Exception as exc:
        raise GrokAuthError(f"xAI OAuth request failed: {exc}") from exc


def _http_get_json(
    url: str,
    headers: dict[str, str],
    *,
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
    opener: Any | None = None,
) -> dict[str, Any]:
    http_request = request.Request(url, headers=headers, method="GET")
    open_call = opener or request.urlopen
    try:
        response = open_call(http_request, timeout=timeout_seconds)
        try:
            return _read_json_response(response)
        finally:
            if hasattr(response, "close"):
                response.close()
    except error.HTTPError as exc:
        raise GrokAuthError(
            f"xAI request failed with HTTP {exc.code}."
        ) from exc
    except Exception as exc:
        raise GrokAuthError(f"xAI request failed: {exc}") from exc


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _account_from_payload(payload: dict[str, Any]) -> GrokAccount:
    return GrokAccount(
        email=str(payload.get("email") or "").strip(),
        subject=str(payload.get("sub") or payload.get("subject") or "").strip(),
        name=str(payload.get("name") or "").strip(),
    )


def _decode_id_token_claims(id_token: str) -> dict[str, Any]:
    parts = str(id_token or "").split(".")
    if len(parts) < 2:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tokens_from_response(
    payload: dict[str, Any],
    *,
    previous: GrokTokens | None = None,
    opener: Any | None = None,
) -> GrokTokens:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GrokAuthError("xAI OAuth returned no access token.")
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token and previous is not None:
        refresh_token = previous.refresh_token
    expires_in = payload.get("expires_in")
    try:
        lifetime = float(expires_in) if expires_in is not None else 0.0
    except (TypeError, ValueError):
        lifetime = 0.0
    expires_at = time.time() + lifetime if lifetime > 0 else 0.0
    id_token = str(payload.get("id_token") or "").strip()
    account = GrokAccount()
    if previous is not None:
        account = previous.account
    claims = _decode_id_token_claims(id_token)
    if claims:
        account = _account_from_payload(claims)
    if not account.email:
        try:
            info = _http_get_json(
                XAI_USERINFO_ENDPOINT,
                _json_headers(access_token),
                opener=opener,
            )
            account = _account_from_payload(info)
        except GrokAuthError:
            pass
    return GrokTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or _scope_string()),
        id_token=id_token,
        account=account,
    )


def load_tokens() -> GrokTokens | None:
    path = grok_auth_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GrokAuthError(f"Grok credential store is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        return None
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        return None
    account_payload = payload.get("account")
    account = (
        _account_from_payload(account_payload)
        if isinstance(account_payload, dict)
        else GrokAccount()
    )
    try:
        expires_at = float(payload.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return GrokTokens(
        access_token=access_token,
        refresh_token=str(payload.get("refresh_token") or "").strip(),
        expires_at=expires_at,
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or ""),
        id_token=str(payload.get("id_token") or ""),
        account=account,
    )


def store_tokens(tokens: GrokTokens) -> Path:
    path = grok_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(tokens.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _restrict_file(path)
    return path


def delete_tokens() -> bool:
    path = grok_auth_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def refresh_tokens(
    tokens: GrokTokens,
    *,
    opener: Any | None = None,
) -> GrokTokens:
    if not tokens.refresh_token:
        raise GrokAuthError(
            "Grok sign-in has no refresh token. Sign in with X / Grok again."
        )
    payload = _http_form(
        XAI_TOKEN_ENDPOINT,
        {
            "grant_type": "refresh_token",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "refresh_token": tokens.refresh_token,
        },
        opener=opener,
    )
    refreshed = _tokens_from_response(payload, previous=tokens, opener=opener)
    store_tokens(refreshed)
    return refreshed


def resolve_tokens(*, opener: Any | None = None) -> GrokTokens | None:
    tokens = load_tokens()
    if tokens is None:
        return None
    if not tokens.expired:
        return tokens
    if not tokens.refresh_token:
        return tokens
    return refresh_tokens(tokens, opener=opener)


def resolve_access_token(*, opener: Any | None = None) -> str | None:
    tokens = resolve_tokens(opener=opener)
    if tokens is None:
        return None
    return tokens.access_token or None


def cached_account() -> dict[str, str] | None:
    tokens = load_tokens()
    if tokens is None:
        return None
    account = tokens.account.as_dict()
    if not any(account.values()):
        return {"type": "grok"}
    account["type"] = "grok"
    return account


def read_account(*, refresh_token: bool = False, opener: Any | None = None) -> dict[str, Any]:
    tokens = resolve_tokens(opener=opener) if refresh_token else load_tokens()
    if tokens is None:
        return {"account": None}
    account = tokens.account.as_dict()
    account["type"] = "grok"
    return {"account": account}


def logout_account(*, opener: Any | None = None) -> dict[str, Any]:
    tokens = load_tokens()
    if tokens is not None and tokens.access_token:
        try:
            _http_form(
                XAI_REVOCATION_ENDPOINT,
                {
                    "token": tokens.access_token,
                    "client_id": XAI_OAUTH_CLIENT_ID,
                    "token_type_hint": "access_token",
                },
                opener=opener,
            )
        except GrokAuthError:
            pass
        if tokens.refresh_token:
            try:
                _http_form(
                    XAI_REVOCATION_ENDPOINT,
                    {
                        "token": tokens.refresh_token,
                        "client_id": XAI_OAUTH_CLIENT_ID,
                        "token_type_hint": "refresh_token",
                    },
                    opener=opener,
                )
            except GrokAuthError:
                pass
    deleted = delete_tokens()
    return {"ok": True, "deleted": deleted}


def list_models(
    *,
    timeout_seconds: float = 15.0,
    opener: Any | None = None,
) -> dict[str, Any]:
    token = resolve_access_token(opener=opener)
    if not token:
        return {
            "ok": False,
            "models": [],
            "error": "No Grok / X account is signed in.",
        }
    try:
        payload = _http_get_json(
            f"{DEFAULT_XAI_API_BASE}/models",
            _json_headers(token),
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except GrokAuthError as exc:
        return {"ok": False, "models": [], "error": str(exc)}
    models: list[str] = []
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id and model_id not in models:
                models.append(model_id)
    return {"ok": True, "models": models, "error": None}


def request_device_code(*, opener: Any | None = None) -> dict[str, Any]:
    payload = _http_form(
        XAI_DEVICE_CODE_ENDPOINT,
        {
            "client_id": XAI_OAUTH_CLIENT_ID,
            "scope": _scope_string(),
            "referrer": XAI_OAUTH_REFERRER,
        },
        opener=opener,
    )
    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    verification_uri = str(
        payload.get("verification_uri") or payload.get("verification_url") or ""
    ).strip()
    if not device_code or not user_code or not verification_uri:
        raise GrokAuthError("xAI device-code login returned an incomplete response.")
    try:
        interval = float(
            payload.get("interval") or DEFAULT_DEVICE_POLL_INTERVAL_SECONDS
        )
    except (TypeError, ValueError):
        interval = DEFAULT_DEVICE_POLL_INTERVAL_SECONDS
    try:
        expires_in = float(payload.get("expires_in") or LOGIN_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        expires_in = LOGIN_TIMEOUT_SECONDS
    return {
        "type": "grokDeviceCode",
        "device_code": device_code,
        "userCode": user_code,
        "verificationUrl": verification_uri,
        "verificationUrlComplete": str(
            payload.get("verification_uri_complete") or ""
        ).strip(),
        "interval": max(1.0, interval),
        "expires_in": max(60.0, expires_in),
    }


def poll_device_code(
    device_code: str,
    *,
    interval: float = DEFAULT_DEVICE_POLL_INTERVAL_SECONDS,
    expires_in: float = LOGIN_TIMEOUT_SECONDS,
    cancel_check: Callable[[], bool] | None = None,
    opener: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> GrokTokens:
    deadline = time.monotonic() + max(60.0, float(expires_in))
    poll_interval = max(1.0, float(interval))
    while True:
        sleep(poll_interval)
        if cancel_check is not None and cancel_check():
            raise GrokAuthError("Grok sign-in was cancelled.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Grok sign-in timed out.")
        body = parse.urlencode(
            {
                "grant_type": DEVICE_GRANT_TYPE,
                "device_code": device_code,
                "client_id": XAI_OAUTH_CLIENT_ID,
            }
        ).encode("utf-8")
        http_request = request.Request(
            XAI_TOKEN_ENDPOINT,
            data=body,
            headers=_form_headers(),
            method="POST",
        )
        open_call = opener or request.urlopen
        try:
            response = open_call(http_request, timeout=HTTP_TIMEOUT_SECONDS)
            try:
                payload = _read_json_response(response)
            finally:
                if hasattr(response, "close"):
                    response.close()
            tokens = _tokens_from_response(payload, opener=opener)
            store_tokens(tokens)
            return tokens
        except error.HTTPError as exc:
            payload: dict[str, Any] = {}
            try:
                payload = _read_json_response(exc)
            except Exception:
                payload = {}
            oauth_error = str(payload.get("error") or "").strip()
            if oauth_error == "authorization_pending":
                continue
            if oauth_error == "slow_down":
                poll_interval += DEVICE_SLOW_DOWN_INCREMENT_SECONDS
                continue
            if oauth_error == "access_denied":
                raise GrokAuthError("Grok sign-in was denied.") from exc
            if oauth_error == "expired_token":
                raise TimeoutError("Grok sign-in timed out.") from exc
            detail = str(
                payload.get("error_description") or oauth_error or exc.code
            )
            raise GrokAuthError(
                f"Grok device-code exchange failed: {detail}."
            ) from exc


def build_authorization_url(
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    query = parse.urlencode(
        {
            "response_type": "code",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": _scope_string(),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "nonce": nonce,
            "referrer": XAI_OAUTH_REFERRER,
        }
    )
    return f"{XAI_AUTHORIZATION_ENDPOINT}?{query}"


def exchange_authorization_code(
    code: str,
    *,
    redirect_uri: str,
    code_verifier: str,
    opener: Any | None = None,
) -> GrokTokens:
    payload = _http_form(
        XAI_TOKEN_ENDPOINT,
        {
            "grant_type": "authorization_code",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        opener=opener,
    )
    tokens = _tokens_from_response(payload, opener=opener)
    store_tokens(tokens)
    return tokens


class _LoopbackServer(HTTPServer):
    callback: dict[str, list[str]] | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        query = parse.parse_qs(parsed.query)
        self.server.callback = query  # type: ignore[attr-defined]
        error_code = str((query.get("error") or [""])[0] or "").strip()
        body = (
            b"<html><body><p>Grok sign-in was not completed. You can close this tab.</p></body></html>"
            if error_code
            else b"<html><body><p>VibeCAD Grok sign-in complete. You can close this tab.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_loopback_server() -> tuple[HTTPServer, str]:
    server = _LoopbackServer(("127.0.0.1", 0), _CallbackHandler)
    host, port = server.server_address[:2]
    redirect_uri = f"http://{host}:{port}/callback"
    thread = threading.Thread(
        target=server.serve_forever,
        name="VibeCAD-Grok-OAuth-Callback",
        daemon=True,
    )
    thread.start()
    return server, redirect_uri


class GrokLoginSession:
    """One cancellable Grok / X OAuth login (browser PKCE or device code)."""

    def __init__(self, *, opener: Any | None = None) -> None:
        self._completed = threading.Event()
        self._cancel_requested = threading.Event()
        self._state_lock = threading.RLock()
        self._login_id = secrets.token_hex(8)
        self._mode = ""
        self._started: dict[str, Any] = {}
        self._tokens: GrokTokens | None = None
        self._error = ""
        self._opener = opener
        self._loopback: HTTPServer | None = None
        self._code_verifier = ""
        self._expected_state = ""

    @property
    def login_id(self) -> str:
        return self._login_id

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def start(self, mode: str) -> dict[str, Any]:
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in {"browser", "device"}:
            raise ValueError("Grok login mode must be browser or device.")
        self._mode = clean_mode
        if clean_mode == "device":
            started = request_device_code(opener=self._opener)
            started["loginId"] = self._login_id
            self._started = started
            return dict(started)
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        try:
            server, redirect_uri = start_loopback_server()
        except Exception as exc:
            raise GrokAuthError(
                "Could not start a local Grok sign-in callback. "
                f"Use device code instead: {exc}"
            ) from exc
        self._loopback = server
        self._code_verifier = verifier
        self._expected_state = state
        auth_url = build_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=challenge,
        )
        started = {
            "type": "grok",
            "loginId": self._login_id,
            "authUrl": auth_url,
            "redirectUri": redirect_uri,
        }
        self._started = started
        return dict(started)

    def _finish_browser(self, timeout: float) -> GrokTokens:
        server = self._loopback
        if server is None:
            raise GrokAuthError("Grok browser sign-in has no local callback.")
        deadline = time.monotonic() + max(1.0, float(timeout))
        try:
            while getattr(server, "callback", None) is None:
                if self._cancel_requested.is_set():
                    raise GrokAuthError("Grok sign-in was cancelled.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Grok sign-in timed out.")
                time.sleep(0.1)
            query = getattr(server, "callback") or {}
            oauth_error = str((query.get("error") or [""])[0] or "").strip()
            if oauth_error:
                description = str(
                    (query.get("error_description") or [oauth_error])[0]
                )
                raise GrokAuthError(f"Grok browser sign-in failed: {description}.")
            state = str((query.get("state") or [""])[0] or "").strip()
            if not state or state != self._expected_state:
                raise GrokAuthError("Grok browser sign-in returned an invalid state.")
            code = str((query.get("code") or [""])[0] or "").strip()
            if not code:
                raise GrokAuthError("Grok browser sign-in returned no authorization code.")
            return exchange_authorization_code(
                code,
                redirect_uri=str(self._started.get("redirectUri") or ""),
                code_verifier=self._code_verifier,
                opener=self._opener,
            )
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            self._loopback = None

    def wait(self, *, timeout: float = LOGIN_TIMEOUT_SECONDS) -> dict[str, Any]:
        if self._mode == "device":
            tokens = poll_device_code(
                str(self._started.get("device_code") or ""),
                interval=float(
                    self._started.get("interval") or DEFAULT_DEVICE_POLL_INTERVAL_SECONDS
                ),
                expires_in=min(
                    float(self._started.get("expires_in") or timeout),
                    float(timeout),
                ),
                cancel_check=self._cancel_requested.is_set,
                opener=self._opener,
            )
        else:
            tokens = self._finish_browser(timeout)
        self._tokens = tokens
        return read_account(refresh_token=False)

    def close(self) -> None:
        self._cancel_requested.set()
        server = self._loopback
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            self._loopback = None

    def __enter__(self) -> "GrokLoginSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
