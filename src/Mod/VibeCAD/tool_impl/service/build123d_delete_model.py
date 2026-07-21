# SPDX-License-Identifier: LGPL-2.1-or-later

"""Delete one exact persisted build123d model and its generated outputs."""

from __future__ import annotations

from typing import Any

from VibeCADBuild123d import delete_model


TOOL_SPEC = {
    "name": "build123d.delete_model",
    "description": (
        "Delete one exact inspected build123d model, including a failed draft, every "
        "accepted output Body, and its project artifact directory. The exact working "
        "revision prevents deleting a model changed since inspection. Use only when "
        "the component itself is no longer part of the intended design."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "model_id": {
                "type": "string",
                "pattern": "^[0-9a-f]{32}$",
                "description": "Exact model id returned by core.inspect scope='program'.",
            },
            "expected_revision": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "Exact current revision returned by core.inspect scope='program'.",
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "Concrete design reason this whole component model must be removed.",
            },
        },
        "required": ["model_id", "expected_revision", "reason"],
        "additionalProperties": False,
    },
}


def run(service: Any, model_id: str, expected_revision: str, reason: str) -> dict[str, Any]:
    return delete_model(service, model_id, expected_revision, reason)
