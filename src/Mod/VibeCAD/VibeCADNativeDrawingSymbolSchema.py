# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing engineering symbols."""

from __future__ import annotations

from copy import deepcopy

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_SYMBOL_CAPABILITY_NAME = "drawing.symbol"
DRAWING_SYMBOL_OPERATIONS = (
    "create_iso_surface_finish",
    "create_asme_surface_finish",
    "create_weld",
    "edit_weld",
    "read_weld_catalog",
)
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_TEXT = {"type": "string", "maxLength": 256}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_WELD_KEYS = [
    "blank",
    "aws_square_down", "aws_square_up", "aws_v_down", "aws_v_up",
    "aws_bead_down", "aws_bead_up", "aws_fillet_down", "aws_fillet_up",
    "aws_plug", "gost_edge_weld", "gost_flanging",
    "gost_flare_bevel_groove", "gost_flare_v_groove", "gost_seam_weld",
    "gost_single_bevel_cjp_groove", "gost_single_bevel_broad_root",
    "gost_single_bevel_groove", "gost_single_j_groove",
    "gost_single_u_groove", "gost_single_v_cjp_groove",
    "gost_single_v_broad_root", "gost_single_v_groove", "gost_spile_weld",
    "gost_square_groove", "gost_surfacing",
]


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_OWNER = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "page"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "view"},
                "object_name": _OBJECT_NAME,
                "expected_owner_state_sha256": _SHA256,
            },
            ("kind", "object_name", "expected_owner_state_sha256"),
        ),
    ]
}
_PLACEMENT = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
        "y_mm": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
    },
    ("x_mm", "y_mm"),
)
_SYMBOL_TYPE = {
    "type": "string",
    "enum": [
        "any_method",
        "removal_prohibited",
        "removal_required",
        "any_method_all_around",
        "removal_prohibited_all_around",
        "removal_required_all_around",
    ],
}
_LAY = {"type": "string", "enum": ["", "=", "⟂", "X", "M", "C", "R"]}
_ROTATION = {"type": "number", "minimum": -360_000, "maximum": 360_000}
_LEADER = _closed(
    {"object_name": _OBJECT_NAME, "expected_leader_state_sha256": _SHA256},
    ("object_name", "expected_leader_state_sha256"),
)
_WELD_SYMBOL = _closed(
    {"object_name": _OBJECT_NAME, "expected_symbol_state_sha256": _SHA256},
    ("object_name", "expected_symbol_state_sha256"),
)
_WELD_TILE = _closed(
    {
        "left_text": deepcopy(_TEXT),
        "center_text": deepcopy(_TEXT),
        "right_text": deepcopy(_TEXT),
        "symbol_key": {"type": "string", "enum": _WELD_KEYS},
    },
    ("left_text", "center_text", "right_text", "symbol_key"),
)


def _surface_parameters(*, standard: str) -> dict:
    common = {
        "page": deepcopy(_PAGE),
        "owner": deepcopy(_OWNER),
        "placement_on_page_mm": deepcopy(_PLACEMENT),
        "symbol_type": deepcopy(_SYMBOL_TYPE),
        "method": deepcopy(_TEXT),
        "machining_allowance": deepcopy(_TEXT),
        "lay": deepcopy(_LAY),
        "rotation_degrees": deepcopy(_ROTATION),
        "label": deepcopy(_LABEL),
    }
    required = [
        "page", "owner", "placement_on_page_mm", "symbol_type", "method",
        "machining_allowance", "lay", "rotation_degrees", "label",
    ]
    if standard == "iso":
        common["roughness"] = {
            "type": "string",
            "enum": [
                "Ra50", "Ra25", "Ra12, 5", "Ra6, 3", "Ra3, 2", "Ra1, 6",
                "Ra0, 8", "Ra0, 4", "Ra0, 2", "Ra0, 1", "Ra0, 05", "Ra0, 025",
            ],
        }
        required.append("roughness")
    else:
        common["sampling_length"] = deepcopy(_TEXT)
        common["minimum_roughness_grade"] = {
            "type": "string", "enum": ["", *[f"N{i}" for i in range(1, 12)]]
        }
        common["maximum_roughness_grade"] = deepcopy(
            common["minimum_roughness_grade"]
        )
        required.extend(
            ["sampling_length", "minimum_roughness_grade", "maximum_roughness_grade"]
        )
    return _closed(common, tuple(required))


def _weld_parameters(*, create: bool) -> dict:
    properties = {
        ("leader" if create else "symbol"): deepcopy(
            _LEADER if create else _WELD_SYMBOL
        ),
        "expected_catalog_sha256": deepcopy(_SHA256),
        "all_around": {"type": "boolean"},
        "field_weld": {"type": "boolean"},
        "alternating_weld": {"type": "boolean"},
        "tail_text": deepcopy(_TEXT),
        "arrow_side": deepcopy(_WELD_TILE),
        "other_side": deepcopy(_WELD_TILE),
        "label": deepcopy(_LABEL),
    }
    return _closed(properties, tuple(properties))


def drawing_symbol_capability_definition() -> NativeCapabilityDefinition:
    surface_action = frozenset({"TechDraw_SurfaceFinishSymbols"})
    weld_action = frozenset({"TechDraw_WeldSymbol"})
    return NativeCapabilityDefinition(
        name=DRAWING_SYMBOL_CAPABILITY_NAME,
        description=(
            "Create canonical surface-finish symbols and create or edit canonical "
            "leader-owned weld symbols using exact page state and embedded assets."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_iso_surface_finish",
                description="Create one ISO surface-texture symbol with explicit meaning and placement.",
                action_ids=surface_action,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageOwnerIsoSurfaceFinishSpec",
                transaction_behavior="document",
                background_required=False,
                parameters=_surface_parameters(standard="iso"),
            ),
            NativeCapabilityVariant(
                operation="create_asme_surface_finish",
                description="Create one ASME surface-texture symbol with explicit grades and placement.",
                action_ids=surface_action,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageOwnerAsmeSurfaceFinishSpec",
                transaction_behavior="document",
                background_required=False,
                parameters=_surface_parameters(standard="asme"),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="create_weld",
                description="Create one leader-owned weld symbol with two complete sides and embedded catalog SVGs.",
                action_ids=weld_action,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingLeaderAndCompleteWeldSymbolSpec",
                transaction_behavior="document",
                background_required=False,
                parameters=_weld_parameters(create=True),
            ),
            NativeCapabilityVariant(
                operation="edit_weld",
                description="Replace the complete state of one hash-pinned weld symbol.",
                action_ids=weld_action,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingWeldSymbolAndCompleteReplacementSpec",
                transaction_behavior="document",
                background_required=False,
                parameters=_weld_parameters(create=False),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="read_weld_catalog",
                description="Read the bounded weld SVG keys and hashes accepted by create and edit.",
                action_ids=weld_action,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="DrawingWeldSymbolCatalog",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed({}, ()),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_symbol_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_symbol_capability_definition())
