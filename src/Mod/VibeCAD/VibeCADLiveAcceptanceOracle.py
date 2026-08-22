# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic evidence checks for live provider acceptance runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Mapping


class LiveAcceptanceError(AssertionError):
    """A saved live-run artifact does not satisfy its declared oracle."""


def copy_linked_document_dependencies(
    document: Any,
    target_directory: str | Path,
) -> tuple[Path, ...]:
    """Copy every loaded external document needed for a cold artifact reopen."""

    dependencies = document.getDependentDocuments()
    if not isinstance(dependencies, list):
        raise LiveAcceptanceError("Linked document dependency evidence is unavailable.")
    target = Path(target_directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Path] = {}
    for dependency in dependencies:
        if dependency is document:
            continue
        source_text = str(getattr(dependency, "FileName", "") or "").strip()
        source = Path(source_text).expanduser().resolve() if source_text else None
        if source is None or not source.is_file():
            raise LiveAcceptanceError(
                "A linked document dependency has no saved source file."
            )
        existing = sources.get(source.name)
        if existing is not None and existing != source:
            raise LiveAcceptanceError(
                f"Linked document dependencies repeat file name {source.name!r}."
            )
        sources[source.name] = source
    copied = []
    for name, source in sorted(sources.items()):
        destination = target / name
        if destination != source:
            shutil.copy2(source, destination)
        copied.append(destination)
    return tuple(copied)


@dataclass(frozen=True, slots=True)
class AssemblyExpectations:
    assemblies: int = 1
    components: int | None = None
    joints: int | None = None
    grounded: int | None = None
    boms: int | None = None
    remaining_degrees_of_freedom: int | None = None

    def __post_init__(self) -> None:
        for field in (
            "assemblies",
            "components",
            "joints",
            "grounded",
            "boms",
            "remaining_degrees_of_freedom",
        ):
            value = getattr(self, field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field} must be a non-negative integer or None.")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveAcceptanceError(f"Assembly evidence {field} is unavailable.")
    return value


def _count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LiveAcceptanceError(f"Assembly evidence {field} is invalid.")
    return value


def _require_count(label: str, actual: int, expected: int | None) -> None:
    if expected is not None and actual != expected:
        raise LiveAcceptanceError(
            f"Assembly {label}: expected {expected}, found {actual}."
        )


def validate_assembly_input_snapshot(
    snapshot: Mapping[str, Any],
    *,
    allow_existing: bool = False,
) -> dict[str, Any]:
    """Require a source-only document or one explicit existing Assembly fixture."""

    if type(allow_existing) is not bool:
        raise TypeError("allow_existing must be true or false")
    if allow_existing:
        return validate_assembly_snapshot(
            snapshot,
            AssemblyExpectations(assemblies=1),
        )

    root = _mapping(snapshot, "input snapshot")
    assembly_count = _count(root.get("assembly_count"), "input assembly_count")
    assemblies = root.get("assemblies")
    if not isinstance(assemblies, list) or len(assemblies) != assembly_count:
        raise LiveAcceptanceError("Assembly input evidence is inconsistent.")
    if assembly_count:
        raise LiveAcceptanceError(
            "Assembly input must be source-only: expected 0 Assemblies, "
            f"found {assembly_count}."
        )
    owned_count = _count(
        root.get("assembly_owned_object_count"),
        "assembly_owned_object_count",
    )
    if owned_count:
        raise LiveAcceptanceError(
            f"Assembly input contains {owned_count} Assembly-owned objects."
        )
    return {
        "assembly_count": 0,
        "assembly_owned_object_count": 0,
    }


def validate_assembly_snapshot(
    snapshot: Mapping[str, Any],
    expectations: AssemblyExpectations,
) -> dict[str, Any]:
    """Validate one live Assembly snapshot and return compact retained evidence."""

    if not isinstance(expectations, AssemblyExpectations):
        raise TypeError("expectations must be AssemblyExpectations")
    root = _mapping(snapshot, "snapshot")
    assembly_count = _count(root.get("assembly_count"), "assembly_count")
    _require_count("count", assembly_count, expectations.assemblies)
    assemblies = root.get("assemblies")
    if not isinstance(assemblies, list) or len(assemblies) != assembly_count:
        raise LiveAcceptanceError("Assembly evidence assemblies is inconsistent.")
    if assembly_count != 1:
        raise LiveAcceptanceError(
            "Live assembly acceptance requires exactly one Assembly."
        )

    assembly = _mapping(assemblies[0], "assemblies[0]")
    counts = _mapping(assembly.get("counts"), "assemblies[0].counts")
    components = _count(counts.get("components"), "component count")
    joints = _count(counts.get("joints"), "joint count")
    grounded = _count(counts.get("grounded"), "grounded count")
    _require_count("components", components, expectations.components)
    _require_count("joints", joints, expectations.joints)
    _require_count("grounded components", grounded, expectations.grounded)

    bom_state = _mapping(assembly.get("bom_state"), "assemblies[0].bom_state")
    if bom_state.get("available") is not True:
        raise LiveAcceptanceError("Assembly BOM evidence is unavailable.")
    boms = _count(bom_state.get("bom_count"), "BOM count")
    _require_count("BOMs", boms, expectations.boms)
    bom_records = bom_state.get("boms")
    if not isinstance(bom_records, list):
        raise LiveAcceptanceError("Assembly BOM records are unavailable.")
    bom_rows = [
        _count(_mapping(record, "BOM record").get("row_count"), "BOM row count")
        for record in bom_records
    ]

    solver = _mapping(
        assembly.get("solver_health"),
        "assemblies[0].solver_health",
    )
    remaining = _count(
        solver.get("remaining_degrees_of_freedom"),
        "remaining degrees of freedom",
    )
    _require_count(
        "remaining degrees of freedom",
        remaining,
        expectations.remaining_degrees_of_freedom,
    )
    return {
        "assembly": {
            "object_name": str(assembly.get("object_name") or ""),
            "label": str(assembly.get("label") or ""),
        },
        "counts": {
            "assemblies": assembly_count,
            "components": components,
            "joints": joints,
            "grounded": grounded,
            "boms": boms,
            "bom_rows": bom_rows,
            "remaining_degrees_of_freedom": remaining,
        },
        "solver_status": str(solver.get("status") or ""),
    }
