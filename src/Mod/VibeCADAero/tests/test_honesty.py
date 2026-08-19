# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeAeroRuntime import native_payload


def test_native_payload_strips_ok() -> None:
    payload = native_payload({"ok": True, "CL": 1.2, "claim_ceiling": "not_airworthy"})
    assert "ok" not in payload
    assert payload["CL"] == 1.2


def test_native_payload_raises_on_failure() -> None:
    try:
        native_payload({"ok": False, "error": "missing report"})
    except RuntimeError as exc:
        assert "missing report" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
