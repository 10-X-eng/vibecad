# SPDX-License-Identifier: LGPL-2.1-or-later

"""Authentication state helpers for VibeCAD providers.

API providers use environment variables, explicit dotenv files, or the OS
keyring. ChatGPT subscriptions are managed exclusively by Codex app-server;
VibeCAD never reads, copies, or stores those OAuth tokens. Grok / X sign-in
uses xAI OAuth and stores tokens in a private VibeCAD Grok credential
directory, not ordinary user.cfg.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request


KEYRING_SERVICE = "FreeCAD VibeCAD"
KEYRING_USERNAME = "openai-api-key"

DEFAULT_PROVIDER = "openai"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_GEMINI_API_BASE = (
    "https://generativelanguage.googleapis.com/v1beta/openai/"
)


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of how to authenticate against one LLM provider."""

    provider_id: str
    display_name: str
    auth_kind: str
    env_var: str
    keyring_username: str
    models_url: str

    @property
    def uses_api_key(self) -> bool:
        return self.auth_kind == "api_key"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        if not self.uses_api_key:
            raise ValueError(
                f"{self.display_name} does not use API-key authentication."
            )
        if self.provider_id == "anthropic":
            return {
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            }
        return {"Authorization": f"Bearer {api_key}"}

    def models_url_for(self, base_url: str | None = None) -> str:
        """Return the models endpoint URL, honoring an optional base URL override.

        Follows each SDK's base-URL convention: OpenAI base URLs include the
        ``/v1`` segment (e.g. ``http://localhost:8000/v1``), while Anthropic
        base URLs do not (e.g. ``https://api.anthropic.com``).
        """

        clean = (base_url or "").strip().rstrip("/")
        if not self.uses_api_key:
            raise ValueError(f"{self.display_name} has no HTTP models endpoint.")
        if not clean:
            return self.models_url
        if self.provider_id == "anthropic":
            return f"{clean}/v1/models"
        return f"{clean}/models"


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        provider_id="openai",
        display_name="OpenAI API key (Codex)",
        auth_kind="api_key",
        env_var="OPENAI_API_KEY",
        keyring_username=KEYRING_USERNAME,
        models_url="https://api.openai.com/v1/models",
    ),
    "anthropic": ProviderSpec(
        provider_id="anthropic",
        display_name="Anthropic",
        auth_kind="api_key",
        env_var="ANTHROPIC_API_KEY",
        keyring_username="anthropic-api-key",
        models_url="https://api.anthropic.com/v1/models",
    ),
    "chatgpt": ProviderSpec(
        provider_id="chatgpt",
        display_name="ChatGPT subscription",
        auth_kind="chatgpt_subscription",
        env_var="",
        keyring_username="",
        models_url="",
    ),
    "grok": ProviderSpec(
        provider_id="grok",
        display_name="Grok (X / xAI)",
        auth_kind="xai_oauth",
        env_var="",
        keyring_username="",
        models_url="https://api.x.ai/v1/models",
    ),
    "gemini": ProviderSpec(
        provider_id="gemini",
        display_name="Google Gemini",
        auth_kind="api_key",
        env_var="GEMINI_API_KEY",
        keyring_username="gemini-api-key",
        models_url=(
            "https://generativelanguage.googleapis.com/v1beta/openai/models"
        ),
    ),
}


def provider_spec(provider: str) -> ProviderSpec:
    spec = PROVIDERS.get((provider or "").strip().lower())
    if spec is None:
        raise ValueError(
            f"Unknown provider {provider!r}; expected one of {sorted(PROVIDERS)}."
        )
    return spec


class AuthStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED_UNVERIFIED = "configured_unverified"
    VERIFIED = "verified"
    INVALID = "invalid"
    OFFLINE = "offline"


@dataclass(frozen=True)
class AuthState:
    status: AuthStatus
    source: str | None = None
    redacted_key: str | None = None
    message: str = ""

    @property
    def can_call_provider(self) -> bool:
        return self.status in {AuthStatus.CONFIGURED_UNVERIFIED, AuthStatus.VERIFIED}


@dataclass(frozen=True)
class AuthCredential:
    value: str
    source: str


def redact_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def read_dotenv_key(path: Path, provider: str = DEFAULT_PROVIDER) -> str | None:
    spec = provider_spec(provider)
    if not spec.uses_api_key:
        return None
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() != spec.env_var:
            continue
        value = value.strip().strip('"').strip("'")
        return value or None
    return None


def _keyring_module() -> Any | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def read_keyring_key(provider: str = DEFAULT_PROVIDER) -> str | None:
    spec = provider_spec(provider)
    if not spec.uses_api_key:
        return None
    keyring = _keyring_module()
    if keyring is None:
        return None
    return keyring.get_password(KEYRING_SERVICE, spec.keyring_username) or None


def store_keyring_key(
    value: str, provider: str = DEFAULT_PROVIDER
) -> dict[str, str | bool | None]:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        return {
            "stored": False,
            "error": f"{spec.display_name} sign-in uses X / xAI OAuth, not an API key.",
            "redacted_key": None,
        }
    if not spec.uses_api_key:
        return {
            "stored": False,
            "error": f"{spec.display_name} sign-in is managed by Codex app-server.",
            "redacted_key": None,
        }
    clean = value.strip()
    if not clean:
        return {
            "stored": False,
            "error": "API key cannot be empty.",
            "redacted_key": None,
        }
    keyring = _keyring_module()
    if keyring is None:
        return {
            "stored": False,
            "error": "No OS keyring backend is available.",
            "redacted_key": None,
        }
    try:
        keyring.set_password(KEYRING_SERVICE, spec.keyring_username, clean)
        return {"stored": True, "error": None, "redacted_key": redact_secret(clean)}
    except Exception as exc:
        return {"stored": False, "error": str(exc), "redacted_key": None}


def delete_keyring_key(provider: str = DEFAULT_PROVIDER) -> dict[str, str | bool]:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        return {
            "deleted": False,
            "error": f"{spec.display_name} sign-out is handled by Grok logout.",
        }
    if not spec.uses_api_key:
        return {
            "deleted": False,
            "error": f"{spec.display_name} has no VibeCAD API key to delete.",
        }
    keyring = _keyring_module()
    if keyring is None:
        return {"deleted": False, "error": "No OS keyring backend is available."}
    try:
        keyring.delete_password(KEYRING_SERVICE, spec.keyring_username)
        return {"deleted": True, "error": ""}
    except Exception as exc:
        return {"deleted": False, "error": str(exc)}


def resolve_auth_credential(
    env: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> AuthCredential | None:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        try:
            from VibeCADGrokAuth import resolve_access_token

            token = resolve_access_token()
        except Exception:
            return None
        if not token:
            return None
        return AuthCredential(value=token, source="Grok credential store")
    if not spec.uses_api_key:
        return None
    data = env if env is not None else os.environ
    value = data.get(spec.env_var)
    if value:
        return AuthCredential(value=value, source="environment")

    if dotenv_path is not None:
        value = read_dotenv_key(dotenv_path, provider=provider)
        if value:
            return AuthCredential(value=value, source=str(dotenv_path))

    value = read_keyring_key(provider=provider)
    if value:
        return AuthCredential(value=value, source="OS keyring")

    return None


def resolve_auth_state(
    env: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> AuthState:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        try:
            from VibeCADGrokAuth import resolve_tokens

            tokens = resolve_tokens()
            if tokens is None:
                return AuthState(
                    AuthStatus.NOT_CONFIGURED,
                    source="Grok credential store",
                    message="No Grok / X account is signed in.",
                )
            email = tokens.account.email
            suffix = f" ({email})" if email else ""
            return AuthState(
                AuthStatus.VERIFIED,
                source="Grok credential store",
                message=f"Grok / X account is signed in{suffix}.",
            )
        except Exception as exc:
            return AuthState(
                AuthStatus.INVALID,
                source="Grok credential store",
                message=str(exc),
            )
    if not spec.uses_api_key:
        try:
            from VibeCADCodex import cached_account, runtime_health

            health = runtime_health()
            if not health.get("ready"):
                return AuthState(
                    AuthStatus.INVALID,
                    source="bundled Codex app-server",
                    message=str(health.get("error") or "Codex runtime is unavailable."),
                )
            account = cached_account()
            if isinstance(account, dict) and account.get("type") == "chatgpt":
                plan = str(account.get("planType") or "subscription")
                return AuthState(
                    AuthStatus.VERIFIED,
                    source="Codex credential store",
                    message=f"ChatGPT {plan} account is signed in.",
                )
            return AuthState(
                AuthStatus.CONFIGURED_UNVERIFIED,
                source="Codex credential store",
                message="ChatGPT sign-in will be verified when the provider starts.",
            )
        except Exception as exc:
            return AuthState(
                AuthStatus.INVALID,
                source="bundled Codex app-server",
                message=str(exc),
            )
    try:
        credential = resolve_auth_credential(
            env=env, dotenv_path=dotenv_path, provider=provider
        )
    except Exception as exc:
        return AuthState(
            AuthStatus.INVALID,
            source="OS keyring",
            message=f"OS credential store is unavailable: {exc}",
        )
    if credential is not None:
        return AuthState(
            AuthStatus.CONFIGURED_UNVERIFIED,
            source=credential.source,
            redacted_key=redact_secret(credential.value),
            message=f"{spec.display_name} API key found in {credential.source}.",
        )

    return AuthState(
        AuthStatus.NOT_CONFIGURED,
        message=f"No {spec.display_name} API key is configured.",
    )


def validate_api_key(
    api_key: str | None,
    *,
    provider: str = DEFAULT_PROVIDER,
    source: str | None = None,
    timeout_seconds: float = 10.0,
    opener: Any | None = None,
    base_url: str | None = None,
) -> AuthState:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        return AuthState(
            AuthStatus.INVALID,
            source=source,
            message=f"{spec.display_name} uses X / xAI OAuth, not pasted API keys.",
        )
    if not spec.uses_api_key:
        return AuthState(
            AuthStatus.INVALID,
            source=source,
            message=f"{spec.display_name} does not accept API keys.",
        )
    clean = (api_key or "").strip()
    if not clean:
        return AuthState(
            AuthStatus.NOT_CONFIGURED,
            source=source,
            message=f"No {spec.display_name} API key is configured.",
        )

    http_request = request.Request(
        spec.models_url_for(base_url),
        headers=spec.auth_headers(clean),
        method="GET",
    )
    redacted = redact_secret(clean)
    try:
        open_call = opener or request.urlopen
        response = open_call(http_request, timeout=timeout_seconds)
        try:
            status_code = getattr(response, "status", None)
            if status_code is None and hasattr(response, "getcode"):
                status_code = response.getcode()
            if hasattr(response, "read"):
                response.read(512)
        finally:
            if hasattr(response, "close"):
                response.close()
        if status_code is None or 200 <= int(status_code) < 300:
            return AuthState(
                AuthStatus.VERIFIED,
                source=source,
                redacted_key=redacted,
                message=f"{spec.display_name} API key validated.",
            )
        return AuthState(
            AuthStatus.INVALID,
            source=source,
            redacted_key=redacted,
            message=(
                f"{spec.display_name} credential validation failed with HTTP {status_code}."
            ),
        )
    except error.HTTPError as exc:
        status = AuthStatus.INVALID if exc.code in {401, 403} else AuthStatus.OFFLINE
        return AuthState(
            status,
            source=source,
            redacted_key=redacted,
            message=f"{spec.display_name} credential validation failed with HTTP {exc.code}.",
        )
    except Exception as exc:
        return AuthState(
            AuthStatus.OFFLINE,
            source=source,
            redacted_key=redacted,
            message=(
                f"{spec.display_name} credential validation could not reach the API: {exc}"
            ),
        )


def validate_configured_auth(
    *,
    provider: str = DEFAULT_PROVIDER,
    env: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
    timeout_seconds: float = 10.0,
    opener: Any | None = None,
    base_url: str | None = None,
) -> AuthState:
    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        try:
            from VibeCADGrokAuth import list_models, resolve_tokens

            tokens = resolve_tokens()
            if tokens is None:
                return AuthState(
                    AuthStatus.NOT_CONFIGURED,
                    source="Grok credential store",
                    message="No Grok / X account is signed in.",
                )
            result = list_models()
            email = tokens.account.email
            suffix = f" ({email})" if email else ""
            if result.get("ok"):
                return AuthState(
                    AuthStatus.VERIFIED,
                    source="Grok credential store",
                    message=f"Grok / X account is signed in{suffix}.",
                )
            return AuthState(
                AuthStatus.INVALID,
                source="Grok credential store",
                message=str(result.get("error") or "Grok sign-in could not be verified."),
            )
        except Exception as exc:
            return AuthState(
                AuthStatus.OFFLINE,
                source="Grok credential store",
                message=f"Grok sign-in status could not be checked: {exc}",
            )
    if not spec.uses_api_key:
        try:
            from VibeCADCodex import read_account

            result = read_account(refresh_token=True)
            account = result.get("account")
            if isinstance(account, dict) and account.get("type") == "chatgpt":
                plan = str(account.get("planType") or "subscription")
                return AuthState(
                    AuthStatus.VERIFIED,
                    source="Codex credential store",
                    message=f"ChatGPT {plan} account is signed in.",
                )
            return AuthState(
                AuthStatus.NOT_CONFIGURED,
                source="Codex credential store",
                message="No ChatGPT subscription account is signed in.",
            )
        except Exception as exc:
            return AuthState(
                AuthStatus.OFFLINE,
                source="bundled Codex app-server",
                message=f"ChatGPT sign-in status could not be checked: {exc}",
            )
    credential = resolve_auth_credential(
        env=env, dotenv_path=dotenv_path, provider=provider
    )
    if credential is None:
        return AuthState(
            AuthStatus.NOT_CONFIGURED,
            message=f"No {spec.display_name} API key is configured.",
        )
    return validate_api_key(
        credential.value,
        provider=provider,
        source=credential.source,
        timeout_seconds=timeout_seconds,
        opener=opener,
        base_url=base_url,
    )


def _parse_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id:
                ids.append(model_id)
    return ids


def list_provider_models(
    api_key: str | None,
    *,
    provider: str = DEFAULT_PROVIDER,
    timeout_seconds: float = 15.0,
    opener: Any | None = None,
    max_pages: int = 10,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Query the provider's models endpoint.

    Returns {"ok": bool, "models": [str, ...], "error": str | None}.
    Anthropic paginates via after_id/has_more; OpenAI returns one page.
    """

    spec = provider_spec(provider)
    if spec.auth_kind == "xai_oauth":
        try:
            from VibeCADGrokAuth import list_models as list_grok_models

            return list_grok_models()
        except Exception as exc:
            return {"ok": False, "models": [], "error": str(exc)}
    if not spec.uses_api_key:
        try:
            from VibeCADCodex import list_models

            return list_models()
        except Exception as exc:
            return {"ok": False, "models": [], "error": str(exc)}
    clean = (api_key or "").strip()
    if not clean:
        return {
            "ok": False,
            "models": [],
            "error": f"No {spec.display_name} API key is configured.",
        }

    open_call = opener or request.urlopen
    models: list[str] = []
    after_id: str | None = None
    models_url = spec.models_url_for(base_url)
    try:
        for _ in range(max_pages):
            url = models_url
            if spec.provider_id == "anthropic":
                params = {"limit": "100"}
                if after_id:
                    params["after_id"] = after_id
                url = f"{url}?{parse.urlencode(params)}"
            http_request = request.Request(
                url,
                headers=spec.auth_headers(clean),
                method="GET",
            )
            response = open_call(http_request, timeout=timeout_seconds)
            try:
                raw = response.read()
            finally:
                if hasattr(response, "close"):
                    response.close()
            payload = json.loads(raw.decode("utf-8"))
            models.extend(_parse_model_ids(payload))
            if spec.provider_id != "anthropic":
                break
            if not payload.get("has_more"):
                break
            after_id = payload.get("last_id")
            if not after_id:
                break
        return {"ok": True, "models": models, "error": None}
    except error.HTTPError as exc:
        return {
            "ok": False,
            "models": models,
            "error": f"{spec.display_name} models request failed with HTTP {exc.code}.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "models": models,
            "error": f"{spec.display_name} models request failed: {exc}",
        }
