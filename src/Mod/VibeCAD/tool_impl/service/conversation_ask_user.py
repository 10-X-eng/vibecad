# SPDX-License-Identifier: LGPL-2.1-or-later

"""Interactive model-to-user question request."""

from __future__ import annotations

TOOL_SPEC = {
    "name": "conversation.ask_user",
    "description": (
        "Ask one compact round only when an answer materially changes the design. "
        "Include choices and a recommendation; decide ordinary details yourself."
    ),
    "safety": "READ",
    "parameters": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "description": (
                    "Material design questions."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable short id.",
                        },
                        "question": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "recommended_answer": {
                            "type": "string",
                            "description": "Recommended default.",
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "answer": {"type": "string"},
                                },
                                "required": ["label", "answer"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "id",
                        "question",
                        "why_it_matters",
                        "recommended_answer",
                        "options",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
}

RUNNER_HANDLED = True
