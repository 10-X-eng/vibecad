# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact workbench-specific VibeScript surface resolution.

The assistant always authors through VibeScript. Each surface also includes
only the focused native read tools owned by the active workbench so it can
understand geometry created with the human ribbon.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable

from VibeCADVibeScriptDomains import (
    UNIVERSAL_SOURCE_OPERATIONS,
    domain_availability,
    get_vibescript_pack,
)
from VibeCADWorkbenchTools import get_tool_pack

MODELING_ENGINES = frozenset({"vibescript"})
UNSUPPORTED_WORKBENCHES = frozenset({"NoneWorkbench", "TestWorkbench"})

CORE_CONVERSATION_VIEW_TOOLS = frozenset(
    {
        "conversation.ask_user",
        "conversation.review_design",
        "core.capture_view_screenshot",
        "core.set_view",
    }
)
FASTENER_CATALOG_TOOL = "fastener_catalog.search"
COMPONENT_CATALOG_TOOL = "component_catalog.search"
COMPONENT_INTERFACE_TOOL = "component.publish_interface"
ASSEMBLY_PLAYBACK_TOOL = "assembly.play_simulation"
MATERIAL_CATALOG_TOOL = "material_catalog.search"
MODEL_ASSEMBLY_WORKBENCHES = frozenset(
    {"PartDesignWorkbench", "AssemblyWorkbench"}
)
MODEL_ASSEMBLY_DOMAINS = ("partdesign", "assembly")
SHARED_CONTEXT_TOOLS = frozenset(
    {
        FASTENER_CATALOG_TOOL,
        COMPONENT_CATALOG_TOOL,
        COMPONENT_INTERFACE_TOOL,
        ASSEMBLY_PLAYBACK_TOOL,
        MATERIAL_CATALOG_TOOL,
    }
)
FASTENER_WORKBENCHES = frozenset({"PartDesignWorkbench", "AssemblyWorkbench"})
COMPONENT_CATALOG_WORKBENCHES = frozenset(
    {"PartDesignWorkbench", "AssemblyWorkbench", "RobotWorkbench"}
)
COMPONENT_INTERFACE_WORKBENCHES = frozenset(
    {"PartDesignWorkbench", "AssemblyWorkbench", "RobotWorkbench"}
)
MATERIAL_CATALOG_WORKBENCHES = frozenset({"PartDesignWorkbench", "MaterialWorkbench"})


def is_model_assembly_workbench(workbench: str | None) -> bool:
    """Return whether the ribbon belongs to the unified authoring surface."""

    return str(workbench or "") in MODEL_ASSEMBLY_WORKBENCHES


def share_authoring_surface(
    first_workbench: str | None,
    second_workbench: str | None,
) -> bool:
    """Return whether two ribbons expose one stable provider contract."""

    first = str(first_workbench or "")
    second = str(second_workbench or "")
    return first == second or (
        first in MODEL_ASSEMBLY_WORKBENCHES
        and second in MODEL_ASSEMBLY_WORKBENCHES
    )


def _core_tool_names(workbench: str | None) -> tuple[str, ...]:
    names = set(CORE_CONVERSATION_VIEW_TOOLS)
    unified = is_model_assembly_workbench(workbench)
    if unified or workbench in FASTENER_WORKBENCHES:
        names.add(FASTENER_CATALOG_TOOL)
    if unified or workbench in COMPONENT_CATALOG_WORKBENCHES:
        names.add(COMPONENT_CATALOG_TOOL)
    if unified or workbench in COMPONENT_INTERFACE_WORKBENCHES:
        names.add(COMPONENT_INTERFACE_TOOL)
    if unified or workbench == "AssemblyWorkbench":
        names.add(ASSEMBLY_PLAYBACK_TOOL)
    if unified or workbench in MATERIAL_CATALOG_WORKBENCHES:
        names.add(MATERIAL_CATALOG_TOOL)
    return tuple(sorted(names))


# Each model-facing focused read belongs to one exact workbench. Universal
# VibeScript source reads are resolved against the active workbench.
PROVIDER_READ_TOOL_OWNERS: dict[str, tuple[str, str]] = {
    "assembly.list_structure": ("AssemblyWorkbench", "vibescript"),
    "cam.list_jobs": ("CAMWorkbench", "vibescript"),
    "draft.list_objects": ("DraftWorkbench", "vibescript"),
    "fem.list_analysis": ("FemWorkbench", "vibescript"),
    "inspection.list_features": ("InspectionWorkbench", "vibescript"),
    "material.list_materials": ("MaterialWorkbench", "vibescript"),
    "mesh.list_meshes": ("MeshWorkbench", "vibescript"),
    "points.list_clouds": ("PointsWorkbench", "vibescript"),
    "robot.list_setup": ("RobotWorkbench", "vibescript"),
    "spreadsheet.read_sheet": ("SpreadsheetWorkbench", "vibescript"),
    "techdraw.list_pages": ("TechDrawWorkbench", "vibescript"),
}


def provider_read_tool_is_visible(
    name: str,
    *,
    workbench: str | None,
    engine: str,
) -> bool:
    """Return whether a focused native read belongs to this authoring surface."""

    owner = PROVIDER_READ_TOOL_OWNERS.get(str(name))
    if owner is None:
        return True
    owner_workbench, owner_engine = owner
    return owner_engine == engine and share_authoring_surface(
        owner_workbench,
        workbench,
    )


def _provider_cad_tool_names(
    names: Iterable[str],
    *,
    workbench: str,
    engine: str,
) -> tuple[str, ...]:
    result: list[str] = []
    for raw_name in names:
        name = str(raw_name)
        if not provider_read_tool_is_visible(
            name,
            workbench=workbench,
            engine=engine,
        ):
            continue
        if name not in result:
            result.append(name)
    return tuple(result)


@dataclass(frozen=True)
class ModelingSurface:
    workbench: str | None
    engine: str
    domain: str | None
    surface_id: str
    core_tool_names: tuple[str, ...]
    cad_tool_names: tuple[str, ...]
    available: bool
    unavailable_reason: str

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.core_tool_names, *self.cad_tool_names)))

    def summary(self) -> dict[str, Any]:
        return {
            "workbench": str(self.workbench or ""),
            "engine": self.engine,
            "domain": self.domain,
            "surface_id": self.surface_id,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "core_tool_names": list(self.core_tool_names),
            "cad_tool_names": list(self.cad_tool_names),
            "tool_names": list(self.tool_names),
        }


def _surface_id(
    *, workbench: str | None, engine: str, domain: str | None, generation: str
) -> str:
    readable = "/".join(
        (
            "vibecad",
            "surface",
            str(workbench or "none"),
            engine,
            str(domain or "unavailable"),
            generation,
        )
    )
    digest = hashlib.sha256(readable.encode("utf-8")).hexdigest()[:12]
    return f"{readable}/{digest}"


def _unavailable(
    workbench: str | None,
    engine: str,
    reason: str,
    *,
    domain: str | None = None,
) -> ModelingSurface:
    return ModelingSurface(
        workbench=workbench,
        engine=engine,
        domain=domain,
        surface_id=_surface_id(
            workbench=workbench,
            engine=engine,
            domain=domain,
            generation="v2-unavailable",
        ),
        core_tool_names=_core_tool_names(workbench),
        cad_tool_names=(),
        available=False,
        unavailable_reason=reason,
    )


def resolve_modeling_surface(
    workbench: str | None,
    engine: str,
) -> ModelingSurface:
    """Resolve exactly one CAD pack for ``(workbench, engine)``."""

    clean_engine = str(engine or "").strip().lower()
    if clean_engine not in MODELING_ENGINES:
        return _unavailable(
            workbench,
            clean_engine or "unknown",
            f"Unknown modeling engine: {clean_engine or '<missing>'}.",
        )
    clean_workbench = str(workbench or "").strip() or None
    if clean_workbench is None:
        return _unavailable(
            clean_workbench,
            clean_engine,
            "No active FreeCAD workbench has a CAD authoring surface.",
        )
    if clean_workbench in UNSUPPORTED_WORKBENCHES:
        return _unavailable(
            clean_workbench,
            clean_engine,
            f"{clean_workbench} intentionally has no CAD authoring surface.",
        )
    native_pack = get_tool_pack(clean_workbench)
    if native_pack is None:
        return _unavailable(
            clean_workbench,
            clean_engine,
            f"Unknown FreeCAD workbench {clean_workbench!r}; no fallback surface is permitted.",
        )

    if clean_engine == "vibescript":
        vibescript_pack = get_vibescript_pack(clean_workbench)
        if vibescript_pack is None:
            return _unavailable(
                clean_workbench,
                clean_engine,
                f"No VibeScript domain is registered for {clean_workbench}.",
            )
        available, reason = domain_availability(clean_workbench)
        if not available:
            return _unavailable(
                clean_workbench,
                clean_engine,
                reason,
                domain=vibescript_pack.domain,
            )
        return ModelingSurface(
            workbench=clean_workbench,
            engine=clean_engine,
            domain=vibescript_pack.domain,
            surface_id=_surface_id(
                workbench=(
                    "ModelAssemblyAuthoring"
                    if is_model_assembly_workbench(clean_workbench)
                    else clean_workbench
                ),
                engine=clean_engine,
                domain=(
                    "partdesign+assembly"
                    if is_model_assembly_workbench(clean_workbench)
                    else vibescript_pack.domain
                ),
                generation="domain-v7-stable-authoring-tools",
            ),
            core_tool_names=_core_tool_names(clean_workbench),
            cad_tool_names=_provider_cad_tool_names(
                (
                    *vibescript_pack.provider_tool_names,
                    *(
                        name
                        for name, owner in PROVIDER_READ_TOOL_OWNERS.items()
                        if provider_read_tool_is_visible(
                            name,
                            workbench=clean_workbench,
                            engine=clean_engine,
                        )
                    ),
                ),
                workbench=clean_workbench,
                engine=clean_engine,
            ),
            available=True,
            unavailable_reason="",
        )

    raise AssertionError(f"Unhandled modeling engine: {clean_engine}")


def engine_from_service(service: Any) -> str:
    getter = getattr(service, "modeling_engine", None)
    if not callable(getter):
        raise RuntimeError("VibeCAD service has no modeling-engine accessor.")
    engine = str(getter() or "").strip().lower()
    if engine not in MODELING_ENGINES:
        raise RuntimeError(
            f"VibeCAD service returned invalid modeling engine {engine!r}."
        )
    return engine


def resolve_service_surface(service: Any, workbench: str | None) -> ModelingSurface:
    return resolve_modeling_surface(workbench, engine_from_service(service))


def _vibescript_domains(names: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for name in names:
        parts = str(name).split(".")
        if not parts or parts[0] != "vibescript":
            continue
        if len(parts) == 2 and parts[1] in UNIVERSAL_SOURCE_OPERATIONS:
            continue
        if len(parts) == 3:
            result.add(parts[1])
        else:
            result.add("<malformed>")
    return result


def validate_surface_names(
    *,
    workbench: str | None,
    engine: str,
    names: Iterable[str],
    allowed_names: Iterable[str] | None = None,
) -> None:
    """Reject mixed engines, workbenches, domains, or undeclared names."""

    clean_names = [str(name or "").strip() for name in names]
    if any(not name for name in clean_names):
        raise ValueError("Every provider tool must have a non-empty name.")
    if len(clean_names) != len(set(clean_names)):
        raise ValueError("The provider surface contains duplicate tools.")
    scripted = {
        candidate
        for candidate in ("vibescript",)
        if any(name.startswith(f"{candidate}.") for name in clean_names)
    }
    if len(scripted) > 1:
        raise ValueError(
            "The provider surface contains multiple modeling engines: "
            + ", ".join(sorted(scripted))
        )
    allowed = set(allowed_names) if allowed_names is not None else None
    expects_engine_tools = (
        any(name.startswith(f"{engine}.") for name in allowed)
        if allowed is not None
        else True
    )
    non_core_names = [
        name
        for name in clean_names
        if name.partition(".")[0]
        not in {
            "conversation",
            "core",
            "fastener_catalog",
            "component_catalog",
            "component",
            "assembly",
            "material_catalog",
        }
    ]
    if engine == "vibescript":
        if scripted and scripted != {engine}:
            raise ValueError(
                f"The {engine} surface declaration does not match its tool schemas."
            )
        if expects_engine_tools and non_core_names and scripted != {engine}:
            raise ValueError(
                f"The {engine} surface declaration does not match its tool schemas."
            )
    if engine == "vibescript" and scripted:
        allowed_reads = {
            name
            for name in PROVIDER_READ_TOOL_OWNERS
            if provider_read_tool_is_visible(
                name,
                workbench=workbench,
                engine=engine,
            )
        }
        native_cad = [
            name
            for name in clean_names
            if name.partition(".")[0]
            not in {
                "conversation",
                "core",
                "fastener_catalog",
                "component_catalog",
                "component",
                "assembly",
                "material_catalog",
                "vibescript",
            }
            and name not in allowed_reads
        ]
        if native_cad:
            raise ValueError(
                "A VibeScript surface cannot contain native mutation or foreign read tools: "
                + ", ".join(sorted(native_cad))
            )
        domains = _vibescript_domains(clean_names)
        pack = get_vibescript_pack(workbench)
        expected_domain = str(pack.domain if pack is not None else "")
        if "<malformed>" in domains or any(
            domain != expected_domain for domain in domains
        ):
            raise ValueError(
                "A VibeScript surface may contain only its active domain namespace "
                "plus the universal source tools."
            )
    if allowed is not None:
        undeclared = sorted(set(clean_names) - allowed)
        if undeclared:
            raise ValueError(
                "The provider surface contains tools outside the resolved tuple: "
                + ", ".join(undeclared)
            )


def infer_engine_from_names(names: Iterable[str]) -> str:
    values = [str(name or "") for name in names]
    engines = [
        engine
        for engine in ("vibescript",)
        if any(name.startswith(f"{engine}.") for name in values)
    ]
    if len(engines) > 1:
        raise ValueError(
            "The provider surface contains multiple modeling engines: "
            + ", ".join(sorted(engines))
        )
    return engines[0] if engines else "vibescript"
