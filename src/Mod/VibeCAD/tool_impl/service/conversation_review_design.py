# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independent, read-only review of a written mechanical design draft."""

from __future__ import annotations


TOOL_SPEC = {
    "name": "conversation.review_design",
    "description": (
        "Request an independent adversarial review of a written mechanical-design "
        "proposal when unresolved functional risk warrants it. The reviewer has no "
        "CAD mutation tools. Use its structured findings as design evidence, not as "
        "a required workflow step or user approval gate."
    ),
    "safety": "READ",
    "requires_document": False,
    "parameters": {
        "type": "object",
        "properties": {
            "customer_intent": {
                "type": "string",
                "minLength": 20,
                "description": (
                    "Faithful restatement of the requested outcome and explicit "
                    "requirements, without replacing them with easier geometry."
                ),
            },
            "design_draft": {
                "type": "string",
                "minLength": 80,
                "description": (
                    "Concrete proposed architecture covering components, primary "
                    "geometry, interfaces, mechanisms, load and motion paths, "
                    "fits, materials, manufacturing, tolerances, verification, "
                    "assumptions, and known risks."
                ),
            },
        },
        "required": ["customer_intent", "design_draft"],
        "additionalProperties": False,
    },
}


RUNNER_HANDLED = True
