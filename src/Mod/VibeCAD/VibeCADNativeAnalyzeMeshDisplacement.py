# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact legacy FEM displacement data for deformed surface conversion."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live


@dataclass(frozen=True, slots=True)
class PreparedFemDisplacement:
    target: PreparedResultTarget
    mesh: Any
    surface_node_ids: tuple[int, ...]
    displacement_sha256: str
    magnitude_range_mm: tuple[float, float]

    @property
    def result(self) -> Any:
        return self.target.result

    def response(self) -> dict[str, Any]:
        return {
            "object_name": str(self.result.Name),
            "mesh_object_name": str(self.mesh.Name),
            "surface_node_count": len(self.surface_node_ids),
            "displacement_magnitude_range_mm": list(self.magnitude_range_mm),
        }


def _displacement_snapshot(
    result: Any,
    mesh: Any,
    surface_node_ids: tuple[int, ...],
) -> tuple[str, tuple[float, float]]:
    if getattr(result, "Mesh", None) is not mesh:
        raise NativeAnalyzeError(
            "The exact mechanical result does not belong to the target FEM mesh.",
            error_code="NATIVE_ANALYZE_RESULT_MESH_MISMATCH",
            repair={
                "result": {"object_name": str(result.Name)},
                "required_fem_mesh": {"object_name": str(mesh.Name)},
            },
        )
    try:
        node_numbers = tuple(int(value) for value in tuple(result.NodeNumbers or ()))
        vectors = tuple(result.DisplacementVectors or ())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact mechanical result has no readable displacement data."
        ) from exc
    if (
        not node_numbers
        or len(node_numbers) != len(vectors)
        or len(node_numbers) != len(set(node_numbers))
        or any(value <= 0 for value in node_numbers)
    ):
        raise NativeAnalyzeError(
            "The exact mechanical result has inconsistent displacement node identities.",
            error_code="NATIVE_ANALYZE_DISPLACEMENT_DATA_INVALID",
        )
    by_node = dict(zip(node_numbers, vectors))
    missing = tuple(node_id for node_id in surface_node_ids if node_id not in by_node)
    if missing:
        raise NativeAnalyzeError(
            "The mechanical result is missing displacement data for exterior FEM nodes.",
            error_code="NATIVE_ANALYZE_DISPLACEMENT_INCOMPLETE",
            repair={
                "missing_surface_node_count": len(missing),
                "first_missing_surface_node_ids": list(missing[:16]),
            },
        )
    digest = hashlib.sha256()
    digest.update(struct.pack("!QQ", int(result.ID), int(mesh.ID)))
    magnitudes = []
    for node_id in surface_node_ids:
        vector = by_node[node_id]
        try:
            coordinates = tuple(
                float(getattr(vector, axis)) for axis in ("x", "y", "z")
            )
        except Exception as exc:
            raise NativeAnalyzeError(
                f"Displacement for exterior FEM node {node_id} is unreadable.",
                error_code="NATIVE_ANALYZE_DISPLACEMENT_DATA_INVALID",
            ) from exc
        if any(not math.isfinite(value) for value in coordinates):
            raise NativeAnalyzeError(
                f"Displacement for exterior FEM node {node_id} is non-finite.",
                error_code="NATIVE_ANALYZE_DISPLACEMENT_DATA_INVALID",
            )
        digest.update(struct.pack("!Qddd", node_id, *coordinates))
        magnitudes.append(math.sqrt(sum(value * value for value in coordinates)))
    return digest.hexdigest(), (
        float(format(min(magnitudes), ".15g")),
        float(format(max(magnitudes), ".15g")),
    )


def prepare_fem_displacement(
    document: Any,
    document_uid: str,
    value: Any,
    mesh: Any,
    surface_node_ids: tuple[int, ...],
) -> PreparedFemDisplacement:
    target = prepare_result_target(
        document,
        document_uid,
        value,
        expected_kinds=frozenset({"result"}),
    )
    if not surface_node_ids:
        raise NativeAnalyzeError("The FEM mesh has no exterior nodes to deform.")
    digest, magnitude_range = _displacement_snapshot(
        target.result,
        mesh,
        surface_node_ids,
    )
    return PreparedFemDisplacement(
        target,
        mesh,
        surface_node_ids,
        digest,
        magnitude_range,
    )


def _fem_displacement_exact(
    prepared: PreparedFemDisplacement,
    *,
    require_result_target_state: bool,
) -> bool:
    if not isinstance(prepared, PreparedFemDisplacement):
        return False
    result = prepared.result
    if not is_live(getattr(result, "Document", None), result):
        return False
    try:
        state = (
            result_state(result, include_ranges=False)
            if require_result_target_state
            else None
        )
        digest, magnitude_range = _displacement_snapshot(
            result,
            prepared.mesh,
            prepared.surface_node_ids,
        )
    except Exception:
        return False
    return (
        (
            state is None
            or state["state_sha256"] == prepared.target.expected_state_sha256
        )
        and digest == prepared.displacement_sha256
        and magnitude_range == prepared.magnitude_range_mm
    )


def fem_displacement_still_exact(prepared: PreparedFemDisplacement) -> bool:
    """Check both the frozen result target and its relevant displacement data."""

    return _fem_displacement_exact(prepared, require_result_target_state=True)


def fem_displacement_data_still_exact(prepared: PreparedFemDisplacement) -> bool:
    """Check source identity/data after an output links to the result.

    Creating the converted Mesh changes surrounding document and presentation
    context. The displacement checksum, mesh identity, and magnitude range are
    the source-data postcondition that must remain invariant.
    """

    return _fem_displacement_exact(prepared, require_result_target_state=False)
