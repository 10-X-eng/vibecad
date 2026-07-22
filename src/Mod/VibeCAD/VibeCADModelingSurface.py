# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact modeling-engine/workbench surface resolution.

This is the single authority for deciding which CAD authoring surface exists.
It deliberately returns one pack, never a union or fallback.  Runtime filters
may remove tools for document/edit-state reasons, but they may not add tools to
the resolved tuple.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable

from VibeCADVibeScriptDomains import domain_availability, get_vibescript_pack
from VibeCADWorkbenchTools import get_tool_pack

MODELING_ENGINES = frozenset({"native", "vibescript", "build123d", "openscad"})
UNSUPPORTED_WORKBENCHES = frozenset({"NoneWorkbench", "TestWorkbench"})

CORE_CONVERSATION_VIEW_TOOLS = frozenset(
    {
        "conversation.ask_user",
        "conversation.review_design",
        "core.inspect",
        "core.capture_view_screenshot",
        "core.set_view",
    }
)

# Domain-specific read entry points stay available to the application, but are
# not duplicated in provider declarations. ``core.inspect`` is the one
# model-facing read interface and remains bound to the resolved
# workbench/engine tuple.
HIDDEN_PROVIDER_INSPECTION_TOOLS = frozenset(
    {
        "assembly.list_structure",
        "build123d.inspect_model",
        "cam.list_jobs",
        "draft.list_objects",
        "fem.list_analysis",
        "inspection.list_features",
        "material.list_materials",
        "mesh.list_meshes",
        "openscad.inspect_model",
        "points.list_clouds",
        "robot.list_setup",
        "spreadsheet.read_sheet",
        "techdraw.list_pages",
    }
)


def _provider_cad_tool_names(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(name)
            for name in names
            if str(name) not in HIDDEN_PROVIDER_INSPECTION_TOOLS
            and not str(name).endswith(".describe_api")
            and not str(name).endswith(".inspect_program")
        )
    )

PARTDESIGN_BUILD123D_TOOLS = frozenset(
    {
        *CORE_CONVERSATION_VIEW_TOOLS,
        "partdesign.find_subelements",
        "partdesign.measure",
        "build123d.inspect_model",
        "build123d.create_model",
        "build123d.edit_source",
        "build123d.set_parameters",
        "build123d.set_inputs",
        "build123d.reconfigure_model",
        "build123d.delete_model",
    }
)

PARTDESIGN_OPENSCAD_TOOLS = frozenset(
    {
        *CORE_CONVERSATION_VIEW_TOOLS,
        "partdesign.find_subelements",
        "partdesign.measure",
        "openscad.inspect_model",
        "openscad.create_model",
        "openscad.edit_source",
        "openscad.set_parameters",
        "openscad.set_conversion_mode",
        "openscad.delete_model",
    }
)


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


def _surface_id(*, workbench: str | None, engine: str, domain: str | None, generation: str) -> str:
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
        core_tool_names=tuple(sorted(CORE_CONVERSATION_VIEW_TOOLS)),
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

    if clean_engine == "native":
        cad_names = _provider_cad_tool_names(native_pack.tool_names)
        if not cad_names:
            return _unavailable(
                clean_workbench,
                clean_engine,
                f"The {native_pack.domain} native pack has no implemented CAD authoring tools.",
                domain=native_pack.domain,
            )
        return ModelingSurface(
            workbench=clean_workbench,
            engine=clean_engine,
            domain=native_pack.domain,
            surface_id=_surface_id(
                workbench=clean_workbench,
                engine=clean_engine,
                domain=native_pack.domain,
                generation="native-v3-unified-inspect",
            ),
            core_tool_names=tuple(sorted(CORE_CONVERSATION_VIEW_TOOLS)),
            cad_tool_names=cad_names,
            available=True,
            unavailable_reason="",
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
                workbench=clean_workbench,
                engine=clean_engine,
                domain=vibescript_pack.domain,
                generation="domain-v4-unified-lifecycle",
            ),
            core_tool_names=tuple(sorted(CORE_CONVERSATION_VIEW_TOOLS)),
            cad_tool_names=_provider_cad_tool_names(vibescript_pack.tool_names),
            available=True,
            unavailable_reason="",
        )

    if clean_workbench != "PartDesignWorkbench":
        return _unavailable(
            clean_workbench,
            clean_engine,
            f"{clean_engine} is Part Design-only. Leaving Part Design must change "
            "the global modeling engine to VibeScript.",
            domain=native_pack.domain,
        )
    tools = PARTDESIGN_BUILD123D_TOOLS if clean_engine == "build123d" else PARTDESIGN_OPENSCAD_TOOLS
    return ModelingSurface(
        workbench=clean_workbench,
        engine=clean_engine,
        domain="partdesign",
        surface_id=_surface_id(
            workbench=clean_workbench,
            engine=clean_engine,
            domain="partdesign",
            generation=f"{clean_engine}-v2-unified-inspect",
        ),
        core_tool_names=tuple(sorted(CORE_CONVERSATION_VIEW_TOOLS)),
        cad_tool_names=_provider_cad_tool_names(
            name for name in sorted(tools) if name not in CORE_CONVERSATION_VIEW_TOOLS
        ),
        available=True,
        unavailable_reason="",
    )


def engine_from_service(service: Any) -> str:
    getter = getattr(service, "modeling_engine", None)
    if not callable(getter):
        raise RuntimeError("VibeCAD service has no modeling-engine accessor.")
    engine = str(getter() or "").strip().lower()
    if engine not in MODELING_ENGINES:
        raise RuntimeError(f"VibeCAD service returned invalid modeling engine {engine!r}.")
    return engine


def resolve_service_surface(service: Any, workbench: str | None) -> ModelingSurface:
    return resolve_modeling_surface(workbench, engine_from_service(service))


def _vibescript_domains(names: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for name in names:
        parts = str(name).split(".")
        if not parts or parts[0] != "vibescript":
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
        for candidate in ("vibescript", "build123d", "openscad")
        if any(name.startswith(f"{candidate}.") for name in clean_names)
    }
    if len(scripted) > 1:
        raise ValueError(
            "The provider surface contains multiple modeling engines: "
            + ", ".join(sorted(scripted))
        )
    if engine == "native" and scripted:
        raise ValueError("A native surface cannot contain scripted-engine tools.")
    allowed = set(allowed_names) if allowed_names is not None else None
    expects_engine_tools = (
        any(name.startswith(f"{engine}.") for name in allowed) if allowed is not None else True
    )
    non_core_names = [
        name
        for name in clean_names
        if name.partition(".")[0] not in {"conversation", "core"}
    ]
    if engine in {"vibescript", "build123d", "openscad"}:
        if scripted and scripted != {engine}:
            raise ValueError(f"The {engine} surface declaration does not match its tool schemas.")
        if expects_engine_tools and non_core_names and scripted != {engine}:
            raise ValueError(f"The {engine} surface declaration does not match its tool schemas.")
    if engine == "vibescript" and scripted:
        native_cad = [
            name
            for name in clean_names
            if name.partition(".")[0] not in {"conversation", "core", "vibescript"}
        ]
        if native_cad:
            raise ValueError(
                "A VibeScript surface cannot contain native workbench CAD tools: "
                + ", ".join(sorted(native_cad))
            )
        domains = _vibescript_domains(clean_names)
        if len(domains) != 1:
            raise ValueError("A VibeScript surface must contain exactly one domain namespace.")
    if engine == "native":
        pack = get_tool_pack(workbench)
        if pack is None:
            cad_names = [
                name
                for name in clean_names
                if name.partition(".")[0] not in {"conversation", "core"}
            ]
            if cad_names:
                raise ValueError("An unknown workbench cannot receive CAD authoring tools.")
        else:
            foreign = [
                name
                for name in clean_names
                if name.partition(".")[0] not in {"conversation", "core"}
                and name not in set(pack.tool_names)
            ]
            if foreign:
                raise ValueError(
                    f"The {workbench} native surface contains tools from another pack: "
                    + ", ".join(sorted(foreign))
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
        for engine in ("vibescript", "build123d", "openscad")
        if any(name.startswith(f"{engine}.") for name in values)
    ]
    if len(engines) > 1:
        raise ValueError(
            "The provider surface contains multiple modeling engines: " + ", ".join(sorted(engines))
        )
    return engines[0] if engines else "native"
