# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for bounded CAM Job and toolpath reads."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_INSPECT_CAPABILITY_NAME = "manufacture.inspect"
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_STATE_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "maxLength": 64,
}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_OFFSET = {
    "type": "integer",
    "minimum": 0,
    "maximum": 100_000_000,
    "default": 0,
}
_PAGE_SIZE = {
    "type": "integer",
    "minimum": 1,
    "maximum": 128,
    "default": 32,
    "description": "Return 1 through 128 ordered items.",
}
_EDGE = {
    "type": "string",
    "pattern": r"^Edge[1-9][0-9]*$",
    "maxLength": 32,
}
_FACE = {
    "type": "string",
    "pattern": r"^Face[1-9][0-9]*$",
    "maxLength": 32,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_LOOP_SELECTION = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "all_edges"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "faces"},
                "faces": {
                    "type": "array",
                    "items": _FACE,
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                },
            },
            ("kind", "faces"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "edges"},
                "edges": {
                    "type": "array",
                    "items": _EDGE,
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                },
            },
            ("kind", "edges"),
        ),
    ]
}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    exact_target_type: str,
    parameters: dict,
    *,
    provider_supplemental: bool = False,
    background_required: bool = False,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"manufacture"}),
        exact_target_type=exact_target_type,
        transaction_behavior="none",
        background_required=background_required,
        parameters=parameters,
        provider_supplemental=provider_supplemental,
    )


def manufacture_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_INSPECT_CAPABILITY_NAME,
        description=(
            "Read exact CAM Job structure, sanity findings, paged toolpaths, and "
            "deterministic model loops without changing the document or GUI selection."
        ),
        primary_classification="read",
        variants=(
            _variant(
                "list_setups",
                "Find CAM setups by label or object name and return one exact ordered page.",
                "VibeCAD_ManufactureReadJob",
                "CurrentDocumentCamSetupCatalog",
                _closed(
                    {
                        "query": {
                            "type": "string",
                            "maxLength": 80,
                            "default": "",
                            "description": "Case-insensitive setup label or object-name substring; empty matches all.",
                        },
                        "offset": _OFFSET,
                        "page_size": _PAGE_SIZE,
                    },
                    (),
                ),
                provider_supplemental=True,
            ),
            _variant(
                "list_remaining_stock",
                "Find retained stock by label or object name and return one exact ordered page.",
                "VibeCAD_ManufactureReadJob",
                "CurrentDocumentRetainedStockCatalog",
                _closed(
                    {
                        "query": {
                            "type": "string",
                            "maxLength": 80,
                            "default": "",
                            "description": "Case-insensitive retained-stock label or object-name substring; empty matches all.",
                        },
                        "offset": _OFFSET,
                        "page_size": _PAGE_SIZE,
                    },
                    (),
                ),
                provider_supplemental=True,
            ),
            _variant(
                "read_job",
                "Read one exact CAM Job with bounded ordered operation paging.",
                "VibeCAD_ManufactureReadJob",
                "ExactCamJobGraphAndState",
                _closed(
                    {
                        "target": _TARGET,
                        "operation_offset": _OFFSET,
                        "page_size": _PAGE_SIZE,
                    },
                    ("target",),
                ),
            ),
            _variant(
                "search_setup_options",
                "Find installed machine or postprocessor values accepted by setup editing.",
                "VibeCAD_ManufactureReadJob",
                "InstalledCamSetupCatalog",
                _closed(
                    {
                        "category": {
                            "type": "string",
                            "enum": ["machine", "postprocessor"],
                        },
                        "query": {
                            "type": "string",
                            "maxLength": 80,
                            "default": "",
                            "description": "Case-insensitive name substring; empty matches all.",
                        },
                        "offset": _OFFSET,
                        "page_size": _PAGE_SIZE,
                    },
                    ("category",),
                ),
                provider_supplemental=True,
            ),
            _variant(
                "validate_job",
                "Run the non-exporting CAM sanity validator on one exact Job graph.",
                "CAM_Sanity",
                "ExactCamJobGraphAndState",
                _closed({"target": _TARGET}, ("target",)),
            ),
            _variant(
                "inspect_toolpath",
                "Read a bounded ordered page of commands from one exact placed toolpath.",
                "CAM_Inspect",
                "ExactCamOperationToolpathAndState",
                _closed(
                    {"target": _TARGET, "offset": _OFFSET, "page_size": _PAGE_SIZE},
                    ("target",),
                ),
            ),
            _variant(
                "detect_loop",
                "Infer an exact typed face or edge loop from supplied seeds without changing selection.",
                "CAM_SelectLoop",
                "ExactCurrentCamModelShapeAndLoopSeed",
                _closed(
                    {"target": _TARGET, "selection": _LOOP_SELECTION},
                    ("target", "selection"),
                ),
            ),
            _variant(
                "read_model_geometry",
                "Read exact paged Faces, Edges, or CAM-drillable features from one model.",
                "CAM_Inspect",
                "ExactCurrentCamModelGeometry",
                _closed(
                    {
                        "target": _TARGET,
                        "elements": {
                            "type": "string",
                            "enum": ["faces", "edges", "drillable"],
                        },
                        "offset": _OFFSET,
                        "page_size": _PAGE_SIZE,
                    },
                    ("target", "elements"),
                ),
                provider_supplemental=True,
                background_required=True,
            ),
            _variant(
                "read_thread_catalog",
                "Read one bounded ordered page from a shipped CAM thread-standard catalog.",
                "VibeCAD_ManufactureReadThreadCatalog",
                "ShippedCamThreadCatalog",
                _closed(
                    {
                        "series": {
                            "type": "string",
                            "enum": [
                                "imperial_external_2a",
                                "imperial_external_3a",
                                "imperial_internal_2b",
                                "imperial_internal_3b",
                                "metric_external_4g6g",
                                "metric_external_6g",
                                "metric_internal_6h",
                            ],
                        },
                        "query": {
                            "type": "string",
                            "maxLength": 80,
                            "default": "",
                            "description": "Case-insensitive designation substring; empty matches all.",
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1000,
                            "default": 0,
                        },
                        "page_size": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 64,
                            "default": 32,
                            "description": "Return 1 through 64 ordered designations.",
                        },
                    },
                    ("series",),
                ),
            ),
        ),
    )


def register_manufacture_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_inspect_capability_definition())
