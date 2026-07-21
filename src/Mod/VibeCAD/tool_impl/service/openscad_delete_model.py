# SPDX-License-Identifier: LGPL-2.1-or-later

"""Delete one generated OpenSCAD model and its source artifacts."""

from __future__ import annotations

from typing import Any

from VibeCADOpenSCAD import delete_model


TOOL_SPEC = {
    "name": "openscad.delete_model",
    "description": (
        "Delete one OpenSCAD source model, all generated Bodies, and its persisted "
        "source artifacts. The exact inspected working revision and a concrete reason "
        "are required so deletion cannot target stale state."
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
                "description": "Exact working revision returned by core.inspect scope='program'.",
            },
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 240,
                "description": "Concrete reason the entire source-backed model must be removed.",
            },
        },
        "required": ["model_id", "expected_revision", "reason"],
        "additionalProperties": False,
    },
}


def run(service: Any, model_id: str, expected_revision: str, reason: str) -> dict[str, Any]:
    return delete_model(service, model_id, expected_revision, reason)
