# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for first-class Grok / X / xAI OAuth."""

from __future__ import annotations

from io import BytesIO
import json
from urllib import error

import pytest

import VibeCADAuth as auth
import VibeCADGrokAuth as grok
import VibeCADPreferences as preferences
import VibeCADProvider as provider
import VibeCADSession as session


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self, _size: int | None = None) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        return None


def test_official_xai_oauth_endpoints_match_published_oidc() -> None:
    assert grok.XAI_OAUTH_ISSUER == "https://auth.x.ai"
    assert grok.XAI_AUTHORIZATION_ENDPOINT == "https://auth.x.ai/oauth2/authorize"
    assert grok.XAI_DEVICE_CODE_ENDPOINT == "https://auth.x.ai/oauth2/device/code"
    assert grok.XAI_TOKEN_ENDPOINT == "https://auth.x.ai/oauth2/token"
    assert grok.DEFAULT_XAI_API_BASE == "https://api.x.ai/v1"
    assert grok.XAI_OAUTH_CLIENT_ID == "b1a00492-073a-47ea-816f-4c329264a828"
    assert "grok-cli:access" in grok.XAI_OAUTH_SCOPES
    assert "offline_access" in grok.XAI_OAUTH_SCOPES


def test_grok_is_a_first_class_provider() -> None:
    spec = auth.provider_spec("grok")
    assert spec.display_name == "Grok (X / xAI)"
    assert spec.auth_kind == "xai_oauth"
    assert spec.uses_api_key is False
    assert "grok" in auth.PROVIDERS
    assert preferences.normalize_provider("grok") == "grok"


def test_grok_tokens_stay_out_of_user_cfg(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    tokens = grok.GrokTokens(
        access_token="access-token-value",
        refresh_token="refresh-token-value",
        expires_at=9_999_999_999,
        account=grok.GrokAccount(email="user@x.ai", subject="sub-1"),
    )
    stored = grok.store_tokens(tokens)
    assert stored == tmp_path / "grok-home" / "auth.json"
    loaded = grok.load_tokens()
    assert loaded is not None
    assert loaded.access_token == "access-token-value"
    assert grok.cached_account() == {
        "email": "user@x.ai",
        "subject": "sub-1",
        "name": "",
        "type": "grok",
    }
    assert grok.resolve_access_token() == "access-token-value"


def test_grok_settings_do_not_store_tokens() -> None:
    settings = preferences.VibeCADSettings(provider="grok", grok_model="grok-4.6")
    assert settings.active_model == "grok-4.6"
    assert settings.active_base_url == grok.DEFAULT_XAI_API_BASE
    assert "access_token" not in settings.__dict__
    assert settings.grok_intent_memory_model == ""


def test_authorization_url_uses_pkce_and_official_client() -> None:
    url = grok.build_authorization_url(
        redirect_uri="http://127.0.0.1:54321/callback",
        state="state-1",
        nonce="nonce-1",
        code_challenge="challenge-1",
    )
    assert url.startswith("https://auth.x.ai/oauth2/authorize?")
    assert "client_id=b1a00492-073a-47ea-816f-4c329264a828" in url
    assert "code_challenge=challenge-1" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A54321%2Fcallback" in url
    assert "referrer=vibecad" in url


def test_device_code_request_and_poll(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    calls: list[str] = []

    def opener(http_request, timeout=None):
        calls.append(http_request.full_url)
        body = http_request.data.decode("utf-8") if http_request.data else ""
        if http_request.full_url == grok.XAI_DEVICE_CODE_ENDPOINT:
            return _FakeResponse(
                {
                    "device_code": "device-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://accounts.x.ai/device",
                    "verification_uri_complete": "https://accounts.x.ai/device?user_code=ABCD-EFGH",
                    "expires_in": 600,
                    "interval": 1,
                }
            )
        if http_request.full_url == grok.XAI_TOKEN_ENDPOINT:
            assert "device_code=device-1" in body
            assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in body
            return _FakeResponse(
                {
                    "access_token": "grok-access",
                    "refresh_token": "grok-refresh",
                    "expires_in": 3600,
                    "id_token": "",
                }
            )
        if http_request.full_url == grok.XAI_USERINFO_ENDPOINT:
            return _FakeResponse({"email": "grok@x.ai", "sub": "user-1"})
        raise AssertionError(http_request.full_url)

    started = grok.request_device_code(opener=opener)
    assert started["type"] == "grokDeviceCode"
    assert started["userCode"] == "ABCD-EFGH"
    tokens = grok.poll_device_code(
        started["device_code"],
        interval=0.01,
        expires_in=30,
        opener=opener,
        sleep=lambda _seconds: None,
    )
    assert tokens.access_token == "grok-access"
    assert grok.load_tokens() is not None
    assert grok.XAI_DEVICE_CODE_ENDPOINT in calls
    assert grok.XAI_TOKEN_ENDPOINT in calls


def test_device_code_poll_waits_while_authorization_pending(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    attempts = {"count": 0}

    def opener(http_request, timeout=None):
        if http_request.full_url != grok.XAI_TOKEN_ENDPOINT:
            raise AssertionError(http_request.full_url)
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise error.HTTPError(
                grok.XAI_TOKEN_ENDPOINT,
                400,
                "pending",
                hdrs=None,
                fp=BytesIO(json.dumps({"error": "authorization_pending"}).encode("utf-8")),
            )
        return _FakeResponse(
            {
                "access_token": "later-access",
                "refresh_token": "later-refresh",
                "expires_in": 3600,
            }
        )

    tokens = grok.poll_device_code(
        "device-pending",
        interval=0.01,
        expires_in=30,
        opener=opener,
        sleep=lambda _seconds: None,
    )
    assert tokens.access_token == "later-access"
    assert attempts["count"] == 2


def test_refresh_and_logout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    grok.store_tokens(
        grok.GrokTokens(
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=1.0,
            account=grok.GrokAccount(email="old@x.ai"),
        )
    )

    def opener(http_request, timeout=None):
        if http_request.full_url == grok.XAI_TOKEN_ENDPOINT:
            return _FakeResponse(
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                }
            )
        if http_request.full_url == grok.XAI_USERINFO_ENDPOINT:
            return _FakeResponse({"email": "old@x.ai", "sub": "user-1"})
        if http_request.full_url == grok.XAI_REVOCATION_ENDPOINT:
            return _FakeResponse({})
        raise AssertionError(http_request.full_url)

    resolved = grok.resolve_tokens(opener=opener)
    assert resolved is not None
    assert resolved.access_token == "new-access"
    result = grok.logout_account(opener=opener)
    assert result["deleted"] is True
    assert grok.load_tokens() is None


def test_resolve_auth_state_and_models(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    grok.store_tokens(
        grok.GrokTokens(
            access_token="live-access",
            refresh_token="live-refresh",
            expires_at=9_999_999_999,
            account=grok.GrokAccount(email="signed-in@x.ai"),
        )
    )
    state = auth.resolve_auth_state(provider="grok")
    assert state.status is auth.AuthStatus.VERIFIED
    assert state.source == "Grok credential store"
    assert "signed-in@x.ai" in state.message
    credential = auth.resolve_auth_credential(provider="grok")
    assert credential is not None
    assert credential.value == "live-access"
    assert credential.source == "Grok credential store"

    def opener(http_request, timeout=None):
        assert http_request.full_url == "https://api.x.ai/v1/models"
        return _FakeResponse({"data": [{"id": "grok-4.6"}, {"id": "grok-4.5"}]})

    monkeypatch.setattr(grok, "resolve_access_token", lambda opener=None: "live-access")
    result = grok.list_models(opener=opener)
    assert result == {
        "ok": True,
        "models": ["grok-4.6", "grok-4.5"],
        "error": None,
    }


def test_choose_provider_uses_grok_oauth_through_codex(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    grok.store_tokens(
        grok.GrokTokens(
            access_token="session-access",
            refresh_token="session-refresh",
            expires_at=9_999_999_999,
        )
    )

    class _Auth:
        can_call_provider = True

    class _Service:
        def provider_name(self) -> str:
            return "grok"

        def auth_state(self):
            return _Auth()

        def provider_model(self) -> str:
            return "grok-4.6"

        def provider_api_key(self) -> str:
            return "session-access"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def provider_base_url(self) -> str | None:
            return grok.DEFAULT_XAI_API_BASE

        def web_search_enabled(self) -> bool:
            return False

        def codex_skills_enabled(self) -> bool:
            return True

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider.CodexProvider)
    assert selected.auth_mode == "api_key"
    assert selected.provider_id == "grok"
    assert selected.provider_label == "Grok via X / xAI OAuth"
    assert selected.api_key == "session-access"
    assert selected.base_url == "https://api.x.ai/v1"
    assert selected.skills_enabled is False
    assert session.provider_execution_identity(selected)["provider_id"] == "grok"


def test_chatgpt_openai_and_anthropic_providers_remain_registered() -> None:
    assert set(auth.PROVIDERS) >= {"openai", "anthropic", "chatgpt", "grok"}
    assert auth.provider_spec("chatgpt").auth_kind == "chatgpt_subscription"
    assert auth.provider_spec("openai").uses_api_key is True
    assert auth.provider_spec("anthropic").uses_api_key is True


def test_login_session_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="browser or device"):
        grok.GrokLoginSession().start("sms")
