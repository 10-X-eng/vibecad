# SPDX-License-Identifier: LGPL-2.1-or-later

"""Universal, workbench-bound provider inspection contract."""

from __future__ import annotations


RUNNER_HANDLED = True

TOOL_SPEC = {
    "name": "core.inspect",
    "description": (
        "Read exact current state without changing it. Use document for the object "
        "inventory, selection for selected subelements, object for one object's "
        "properties, domain for existing programs and references, program for source, "
        "inputs, revisions, and live outputs, api for callable names and signatures, "
        "or image for stored image data. Call only when the required fact is absent "
        "or may have changed."
    ),
    "contextual": True,
    "safety": "READ",
    "requires_document": False,
    "edit_modes": ["none", "sketch"],
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": [
                    "document",
                    "selection",
                    "object",
                    "domain",
                    "program",
                    "api",
                    "image",
                ],
                "description": "State category; each value is defined in the tool description.",
            },
            "target": {
                "type": "string",
                "maxLength": 512,
                "default": "",
                "description": (
                    "Exact internal object name, stable program/model id, image id, "
                    "sheet name, or domain filter required by the selected scope; "
                    "use an empty string when that scope needs no target."
                ),
            },
            "path": {
                "type": "string",
                "maxLength": 512,
                "default": "",
                "description": (
                    "JSON Pointer into this scope's deterministic result, such as "
                    "'/program/source'; use an empty string for its root."
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100000000,
                "default": 0,
                "description": "Zero-based item or character offset within the selected path.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "Maximum mapping entries, list items, or 1024-character chunks to return.",
            },
            "attach": {
                "type": "boolean",
                "default": False,
                "description": (
                    "For image scope only, attach the exact target image to this one "
                    "tool result; false performs metadata inspection only."
                ),
            },
        },
        "required": ["scope"],
        "additionalProperties": False,
    },
}
