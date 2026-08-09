# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small routing table for current profile-driven Design features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from VibeCADNativeDesignExtrude import (
    create_design_extrude,
    preflight_design_extrude,
    prepare_design_extrude,
)
from VibeCADNativeDesignHelix import (
    create_design_helix,
    preflight_design_helix,
    prepare_design_helix,
)
from VibeCADNativeDesignLoft import (
    create_design_loft,
    preflight_design_loft,
    prepare_design_loft,
)
from VibeCADNativeDesignRevolve import (
    create_design_revolve,
    preflight_design_revolve,
    prepare_design_revolve,
)
from VibeCADNativeDesignResults import DesignResultSpec
from VibeCADNativeDesignSweep import (
    create_design_sweep,
    preflight_design_sweep,
    prepare_design_sweep,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_BASE_FIELDS = frozenset({"label", "profile", "result"})
_FIELDS = {
    "design_extrude": _BASE_FIELDS | {"direction", "extent"},
    "design_revolve": _BASE_FIELDS | {"axis", "extent"},
    "design_loft": _BASE_FIELDS | {"sections", "ruled", "closed"},
    "design_sweep": _BASE_FIELDS | {"path", "options"},
    "design_helix": _BASE_FIELDS
    | {"axis", "definition", "left_handed", "reversed", "outside", "tolerance"},
}
_PREPARE = {
    "design_extrude": prepare_design_extrude,
    "design_revolve": prepare_design_revolve,
    "design_loft": prepare_design_loft,
    "design_sweep": prepare_design_sweep,
    "design_helix": prepare_design_helix,
}
_PREFLIGHT = {
    "design_extrude": preflight_design_extrude,
    "design_revolve": preflight_design_revolve,
    "design_loft": preflight_design_loft,
    "design_sweep": preflight_design_sweep,
    "design_helix": preflight_design_helix,
}
_CREATE = {
    "design_extrude": create_design_extrude,
    "design_revolve": create_design_revolve,
    "design_loft": create_design_loft,
    "design_sweep": create_design_sweep,
    "design_helix": create_design_helix,
}


@dataclass(frozen=True, slots=True)
class PreparedDesignProfile:
    operation: str
    spec: Any

    @property
    def transaction_name(self) -> str:
        return f"Create Native Design {self.operation.removeprefix('design_').title()}"


def profile_argument_fields() -> dict[str, frozenset[str]]:
    return {operation: frozenset(fields) for operation, fields in _FIELDS.items()}


def prepare_design_profile(
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedDesignProfile:
    prepare = _PREPARE.get(operation)
    if prepare is None:
        raise NativeModelError("That profile Design operation is unavailable.")
    return PreparedDesignProfile(operation, prepare(document_uid, values))


def _requires_single_target_context(prepared: PreparedDesignProfile) -> bool:
    if prepared.operation == "design_extrude":
        sides = (prepared.spec.side1, prepared.spec.side2)
        return any(
            side is not None and side.kind in {"up_to_first", "up_to_last"}
            for side in sides
        )
    return (
        prepared.operation == "design_revolve"
        and prepared.spec.extent_kind in {"up_to_first", "up_to_last", "up_to_face"}
    )


def preflight_design_profile(
    document: Any,
    prepared: PreparedDesignProfile,
    result_spec: DesignResultSpec,
) -> None:
    if _requires_single_target_context(prepared) and len(result_spec.target_refs) != 1:
        raise NativeModelError(
            "That target-dependent extent requires exactly one explicit target Body."
        )
    _PREFLIGHT[prepared.operation](document, prepared.spec)


def create_prepared_design_profile(
    document: Any,
    *,
    prepared: PreparedDesignProfile,
    label: str,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    create: Callable[..., NativeMutationDraft] = _CREATE[prepared.operation]
    return create(
        document,
        label=label,
        spec=prepared.spec,
        result_spec=result_spec,
    )
