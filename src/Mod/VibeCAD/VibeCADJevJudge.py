# SPDX-License-Identifier: LGPL-2.1-or-later
"""Optional post-step Jev classification for the workflow harness.

Jev is a judge, not a clicker. Code owns click, timeout, and pass/fail.
This helper is default-off. A live System One call happens only when both
``VIBECAD_JEV_JUDGE`` is enabled and ``TYPESAFE_API_KEY`` is set. Low
confidence never changes a green result. CI should mock or skip this path.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"
JUDGE_FLAG_ENV = "VIBECAD_JEV_JUDGE"
LOW_CONFIDENCE = 0.5
FAILURE_CLASSES = (
    "geometry_bug",
    "wrong_button",
    "model_lied",
    "flake",
)
ENABLED_FLAGS = frozenset({"1", "true", "yes", "on"})


def judge_enabled(environ: dict[str, str] | None = None) -> bool:
    """Return True only when the operator opted in and a key is present."""

    env = os.environ if environ is None else environ
    flag = str(env.get(JUDGE_FLAG_ENV) or "").strip().lower()
    if flag not in ENABLED_FLAGS:
        return False
    return bool(str(env.get(API_KEY_ENV) or "").strip())


def _system_one_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": state,
        "model": TYPESAFE_MODEL,
        "questions": {
            "landed": {
                "type": "noul",
                "instructions": (
                    "Did this VibeCAD workflow step actually complete against "
                    "the post-click UI and document evidence?"
                ),
            },
            "failure_class": {
                "type": "choice",
                "instructions": (
                    "If the step failed, which class best describes the failure? "
                    "Use flake when the evidence is inconsistent or timing-related."
                ),
                "criteria": {
                    "geometry_bug": "The command ran but the resulting geometry is wrong.",
                    "wrong_button": "The click hit the wrong control or command.",
                    "model_lied": "The UI or document reported success that the evidence contradicts.",
                    "flake": "The failure looks intermittent or environment-related.",
                },
            },
            "progress": {
                "type": "score",
                "instructions": "How far did this workflow get after the step?",
                "criteria": [
                    "The step did not start.",
                    "The click happened but the document or UI did not change.",
                    "The step completed.",
                ],
            },
        },
    }


def _ask_typesafe(state: dict[str, Any], api_key: str) -> dict[str, Any]:
    raw = json.dumps(_system_one_payload(state)).encode("utf-8")
    http_request = request.Request(
        TYPESAFE_ENDPOINT,
        data=raw,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = request.urlopen(http_request, timeout=20)
        try:
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
    except error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        raise RuntimeError(f"TypeSafe HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("TypeSafe returned a non-object payload.")
    return payload


def _answers(payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("answers")
    return answers if isinstance(answers, dict) else {}


def classify_step(
    state: dict[str, Any],
    *,
    asker: Any = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Return one optional classification, or None when the judge is skipped."""

    env = os.environ if environ is None else environ
    if asker is None and not judge_enabled(env):
        return {
            "skipped": True,
            "reason": "jev_disabled_or_unkeyed",
            "used_for_pass_fail": False,
        }

    api_key = str(env.get(API_KEY_ENV) or "").strip()
    try:
        payload = asker(state) if asker is not None else _ask_typesafe(state, api_key)
    except Exception as exc:
        return {
            "skipped": True,
            "reason": "jev_request_failed",
            "error": str(exc),
            "used_for_pass_fail": False,
        }

    answers = _answers(payload)
    landed = answers.get("landed") if isinstance(answers.get("landed"), dict) else {}
    failure = (
        answers.get("failure_class")
        if isinstance(answers.get("failure_class"), dict)
        else {}
    )
    progress = answers.get("progress") if isinstance(answers.get("progress"), dict) else {}
    failure_confidence = failure.get("confidence")
    progress_confidence = progress.get("confidence")
    try:
        lowest_confidence = min(
            float(value)
            for value in (failure_confidence, progress_confidence)
            if value is not None
        )
    except ValueError:
        lowest_confidence = None
    low_confidence = lowest_confidence is None or lowest_confidence < LOW_CONFIDENCE
    return {
        "skipped": False,
        "landed": landed.get("noul"),
        "failure_class": failure.get("choice"),
        "progress": progress.get("score"),
        "confidence": lowest_confidence,
        "low_confidence": low_confidence,
        "used_for_pass_fail": False,
        "answers": answers,
    }


def cannot_flip_green(code_passed: bool, judge: dict[str, Any] | None) -> bool:
    """Code owns pass/fail. Jev never turns a passing step red."""

    del judge
    return bool(code_passed)
