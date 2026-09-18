# SPDX-License-Identifier: LGPL-2.1-or-later
"""Optional TypeSafe/Jev step judge for the VibeCAD workflow harness.

Default off. A live System One call happens only when the caller asks for a
judge and ``TYPESAFE_API_KEY`` is set. The harness still owns click, timeout,
and pass/fail. Low confidence never counts as a pass. This module never
prints or writes the API key.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
JUDGE_CONFIDENCE_FLOOR = 0.5
FAILURE_CLASSES = (
    "none",
    "geometry_bug",
    "wrong_button",
    "model_claim",
    "flake",
)


def judge_requested(enabled: bool) -> bool:
    return bool(enabled) and bool(str(os.environ.get("TYPESAFE_API_KEY") or "").strip())


def _system_one_request(state: dict[str, Any], api_key: str) -> dict[str, Any]:
    payload = {
        "state": state,
        "model": TYPESAFE_MODEL,
        "questions": {
            "landed": {
                "type": "noul",
                "instructions": (
                    "Did this VibeCAD workflow step land? The click targeted "
                    "the named control, post-click UI state matches the step, "
                    "and the document tree shows the claimed objects."
                ),
            },
            "failure_class": {
                "type": "choice",
                "instructions": (
                    "If the step failed, classify the failure. Use none when "
                    "the step landed."
                ),
                "criteria": {
                    "none": "The step landed or no failure classification is needed.",
                    "geometry_bug": "The geometry or CAD kernel result is wrong.",
                    "wrong_button": "The click named or hit the wrong control.",
                    "model_claim": (
                        "Status or the model claims work the document tree "
                        "does not show."
                    ),
                    "flake": "A timing, focus, or transient UI flake.",
                },
            },
        },
    }
    http_request = request.Request(
        TYPESAFE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response = request.urlopen(http_request, timeout=30.0)
    try:
        body = json.loads(response.read().decode("utf-8"))
    finally:
        response.close()
    if not isinstance(body, dict):
        raise RuntimeError("TypeSafe did not return a JSON object.")
    return body


def interpret_judge(response: dict[str, Any]) -> dict[str, Any]:
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        return {
            "called": True,
            "landed": False,
            "failure_class": "none",
            "confidence": 0.0,
            "counts_as_pass": False,
            "error": "TypeSafe response had no answers.",
        }
    landed_answer = answers.get("landed") if isinstance(answers.get("landed"), dict) else {}
    class_answer = (
        answers.get("failure_class")
        if isinstance(answers.get("failure_class"), dict)
        else {}
    )
    noul = landed_answer.get("noul")
    landed = isinstance(noul, (int, float)) and float(noul) >= 0.5
    failure_class = str(class_answer.get("choice") or "none")
    if failure_class not in FAILURE_CLASSES:
        failure_class = "none"
    confidence = class_answer.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    # Low confidence does not count as pass, even if noul looks positive.
    counts_as_pass = bool(landed and confidence_value >= JUDGE_CONFIDENCE_FLOOR)
    return {
        "called": True,
        "landed": landed,
        "noul": None if not isinstance(noul, (int, float)) else float(noul),
        "failure_class": failure_class,
        "confidence": confidence_value,
        "counts_as_pass": counts_as_pass,
        "model": str(response.get("model") or TYPESAFE_MODEL),
    }


def judge_step(
    state: dict[str, Any],
    *,
    enabled: bool,
    transport: Any = None,
) -> dict[str, Any]:
    if not enabled:
        return {"called": False, "skipped": "judge_disabled"}
    api_key = str(os.environ.get("TYPESAFE_API_KEY") or "").strip()
    # A provided transport is a test/mock. Live HTTP still requires a key.
    if transport is None and not api_key:
        return {"called": False, "skipped": "typesafe_key_absent"}
    try:
        if transport is None:
            response = _system_one_request(state, api_key)
        else:
            response = transport(state, api_key)
        return interpret_judge(response)
    except (
        error.URLError,
        TimeoutError,
        ConnectionError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RuntimeError,
    ) as exc:
        return {
            "called": True,
            "landed": False,
            "failure_class": "none",
            "confidence": 0.0,
            "counts_as_pass": False,
            "error": str(exc),
        }
