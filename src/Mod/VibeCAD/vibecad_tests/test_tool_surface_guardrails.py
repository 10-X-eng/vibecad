# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guardrail: every provider-callable tool is deliberate and structurally safe.

Four invariants are enforced:

1. No orphan provider tools — every provider-visible tool spec is surfaced through
   ``CORE_PROVIDER_TOOLS``, a workbench-owned VibeScript read surface, or the
   VibeScript session surface. A tool
   registered without any surface fails this test, so stale or
   experimental tools cannot silently become callable by default.
2. No dangling names — every surfaced name resolves to a registered, validating
   :class:`ToolSpec`.
3. Writes are transactional — every non-READ tool either contains a FreeCAD
   transaction marker in its own module or in a same-package module it
   imports, or appears in a justified allowlist.
4. No command-string execution — ``tool_impl`` never contains
   ``runCommand``/``doCommand``/``sendMsgToActiveView``; all FreeCAD
   semantics run through the typed Python APIs.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any, Iterator

import pytest

from VibeCADTools import SafetyLevel, ToolSpec, VibeCADTool
from tool_impl.service import TOOL_MODULE_NAMES

TOOL_PACKAGES = ("tool_impl.service",)

TOOL_IMPL_DIR = Path(__file__).resolve().parent.parent / "tool_impl"

# Write-safety tools that legitimately run without a
# FreeCAD document transaction. Each entry needs a reason.
TRANSACTION_EXEMPT: frozenset[str] = frozenset()

# Runner-handled engine tools carry only a spec in tool_impl; their document
# mutations run inside the engine module, so search it for markers too.
ENGINE_MODULES = {
    "vibescript": TOOL_IMPL_DIR.parent / "VibeCADVibeScriptDomainPublication.py",
}

TRANSACTION_MARKERS = ("run_freecad_transaction", "openTransaction")

FORBIDDEN_COMMAND_STRINGS = ("runCommand", "doCommand", "sendMsgToActiveView")

_INTRA_PACKAGE_IMPORT = re.compile(
    r"^from\s+\.\s+import\s+(?P<plain>[\w,\s]+)$|^from\s+\.(?P<dotted>\w+)\s+import\s+",
    re.MULTILINE,
)


def _collect_specs() -> dict[str, tuple[ToolSpec, Path, str]]:
    """Return {tool name: (validated spec, module path, package name)}."""
    specs: dict[str, tuple[ToolSpec, Path, str]] = {}
    for package_name in TOOL_PACKAGES:
        package = import_module(package_name)
        for module_name in package.TOOL_MODULE_NAMES:
            module = import_module(f"{package_name}.{module_name}")
            spec = ToolSpec.from_mapping(module.TOOL_SPEC)
            if not spec.provider_visible:
                continue
            assert spec.name not in specs, (
                f"Duplicate tool name {spec.name!r} from {module.__file__}"
            )
            specs[spec.name] = (spec, Path(module.__file__), package_name)
    import VibeCADVibeScriptDomains as domains

    domain_path = TOOL_IMPL_DIR.parent / "VibeCADVibeScriptDomainRuntime.py"
    for raw_spec in domains.universal_tool_specs():
        spec = ToolSpec.from_mapping(raw_spec)
        assert spec.name not in specs
        specs[spec.name] = (spec, domain_path, "vibescript.universal")
    for pack in domains.VIBESCRIPT_WORKBENCH_PACKS.values():
        for raw_spec in domains.domain_tool_specs(pack):
            spec = ToolSpec.from_mapping(raw_spec)
            assert spec.name not in specs
            specs[spec.name] = (spec, domain_path, "vibescript.domain")
    return specs


@pytest.fixture(scope="module")
def specs() -> dict[str, tuple[ToolSpec, Path, str]]:
    return _collect_specs()


@pytest.fixture(scope="module")
def core_tools() -> frozenset[str]:
    import VibeCADSession as session

    return frozenset(session.CORE_PROVIDER_TOOLS)


@pytest.fixture(scope="module")
def engine_tools() -> frozenset[str]:
    import VibeCADSession as session
    import VibeCADVibeScriptDomains as domains
    from VibeCADModelingSurface import PROVIDER_READ_TOOL_OWNERS

    return frozenset(
        session.VIBESCRIPT_PROVIDER_TOOLS
        | set(PROVIDER_READ_TOOL_OWNERS)
        | {
            name
            for pack in domains.VIBESCRIPT_WORKBENCH_PACKS.values()
            for name in pack.tool_names
        }
    )


def _surfaced_names(
    core_tools: frozenset[str],
    engine_tools: frozenset[str] = frozenset(),
) -> set[str]:
    return set(core_tools) | set(engine_tools)


def test_no_orphan_tools(specs, core_tools, engine_tools) -> None:
    """1. Every registered tool must belong to a live provider surface."""
    recognized = _surfaced_names(core_tools, engine_tools)
    orphans = sorted(set(specs) - recognized)
    assert not orphans, (
        "Tools registered but not surfaced by CORE_PROVIDER_TOOLS or the "
        "VibeScript session surface (add to one or remove "
        f"the registration): {orphans}"
    )


def test_no_dangling_names(specs, core_tools, engine_tools) -> None:
    """2. Every surfaced name must resolve to a registered spec."""
    dangling = sorted(_surfaced_names(core_tools, engine_tools) - set(specs))
    assert not dangling, (
        f"Names surfaced by core/VibeScript with no registered tool spec: {dangling}"
    )


def test_retired_direct_native_tools_are_not_registered(specs) -> None:
    """Only VibeScript support tools remain until the new Native registry lands."""
    import VibeCADSession as session
    from VibeCADModelingSurface import PROVIDER_READ_TOOL_OWNERS

    registered_support = {
        name for name in specs if not name.startswith("vibescript.")
    }
    assert registered_support == (
        set(session.CORE_PROVIDER_TOOLS) | set(PROVIDER_READ_TOOL_OWNERS)
    )


def _module_sources_with_local_imports(module_path: Path) -> Iterator[str]:
    """Yield the module source plus sources of same-package imports (BFS)."""
    queue = [module_path]
    visited: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        source = path.read_text(encoding="utf-8")
        yield source
        for match in _INTRA_PACKAGE_IMPORT.finditer(source):
            if match.group("dotted"):
                names = [match.group("dotted")]
            else:
                names = [
                    part.strip()
                    for part in (match.group("plain") or "").split(",")
                    if part.strip()
                ]
            queue.extend(path.parent / f"{name}.py" for name in names)


def test_write_tools_run_in_transactions(specs) -> None:
    """3. Every write tool reaches a FreeCAD transaction (possibly via helpers)."""
    read_levels = {SafetyLevel.READ, SafetyLevel.VIEW}
    offenders = []
    for name, (spec, path, _) in sorted(specs.items()):
        if spec.safety in read_levels or name in TRANSACTION_EXEMPT:
            continue
        module_paths = [path]
        engine_module = ENGINE_MODULES.get(name.split(".", 1)[0])
        if engine_module is not None:
            module_paths.append(engine_module)
        if not any(
            marker in source
            for module_path in module_paths
            for source in _module_sources_with_local_imports(module_path)
            for marker in TRANSACTION_MARKERS
        ):
            offenders.append(name)
    assert not offenders, (
        "Write-safety tools with no transaction marker in their module or "
        f"same-package imports: {offenders}"
    )


def test_transaction_exemptions_are_current(specs) -> None:
    """3b. Transaction exemptions must reference registered tools."""
    unknown = sorted(TRANSACTION_EXEMPT - set(specs))
    assert not unknown, f"Transaction-exempt tools no longer registered: {unknown}"


def test_no_legacy_command_execution() -> None:
    """4. tool_impl never shells out to GUI command names or script strings."""
    offenders = []
    for path in sorted(TOOL_IMPL_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_COMMAND_STRINGS:
            if pattern in source:
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, (
        "Legacy FreeCAD command-execution strings found in tool_impl "
        f"(implement via typed APIs instead): {offenders}"
    )


# ---------------------------------------------------------------------------
# VibeScript surface guardrails.
# ---------------------------------------------------------------------------


def test_every_vibescript_tool_is_surfaced(specs) -> None:
    """Every VibeScript spec belongs to exactly one engine/domain pack."""
    import VibeCADSession as session
    import VibeCADVibeScriptDomains as domains

    registered = {name for name in specs if name.startswith("vibescript.")}
    assert registered, "expected registered vibescript.* tool specs"
    surfaced = set(session.VIBESCRIPT_PROVIDER_TOOLS)
    surfaced.update(
        name
        for pack in domains.VIBESCRIPT_WORKBENCH_PACKS.values()
        for name in pack.tool_names
    )
    orphans = sorted(registered - surfaced)
    assert not orphans, (
        f"vibescript tools registered but missing from VIBESCRIPT_PROVIDER_TOOLS: {orphans}"
    )


def test_every_surfaced_vibescript_tool_is_registered(specs) -> None:
    """All Part Design and domain-qualified surface names are registered."""
    import VibeCADSession as session
    import VibeCADVibeScriptDomains as domains

    surfaced = {
        name
        for name in session.VIBESCRIPT_PROVIDER_TOOLS
        if name.startswith("vibescript.")
    }
    surfaced.update(
        name
        for pack in domains.VIBESCRIPT_WORKBENCH_PACKS.values()
        for name in pack.tool_names
    )
    dangling = sorted(surfaced - set(specs))
    assert not dangling, (
        f"VIBESCRIPT_PROVIDER_TOOLS names unregistered tools: {dangling}"
    )


def test_engine_surface_table_covers_every_scripted_engine() -> None:
    """The provider has exactly one VibeScript authoring surface family."""
    import VibeCADSession as session

    assert set(session.SCRIPTED_ENGINE_PROVIDER_TOOLS) == {"vibescript"}
    assert "native" not in session.SCRIPTED_ENGINE_PROVIDER_TOOLS


def test_default_engine_is_vibescript_with_a_provider_surface() -> None:
    """The out-of-box default engine is vibescript, and its tool surface is
    registered so new projects are immediately usable without configuration."""
    import VibeCADSession as session
    from VibeCADProject import DEFAULT_MODELING_ENGINE, MODELING_ENGINES

    assert DEFAULT_MODELING_ENGINE == "vibescript"
    assert DEFAULT_MODELING_ENGINE in MODELING_ENGINES
    surface = session.SCRIPTED_ENGINE_PROVIDER_TOOLS[DEFAULT_MODELING_ENGINE]
    assert surface, "default engine must expose a non-empty provider tool surface"


class _SurfaceService:
    def __init__(self, engine: str) -> None:
        self.engine = engine

    def modeling_engine(self) -> str:
        return self.engine

    def _active_document(self) -> object:
        return object()

    def design_review_enabled(self) -> bool:
        return True


class _SpecRegistry:
    def __init__(self, specs: dict[str, tuple[ToolSpec, Path, str]]) -> None:
        self._specs = specs

    def get(self, name: str) -> VibeCADTool:
        return VibeCADTool(self._specs[name][0], None)


def test_provider_schema_build_captures_runtime_state_once(
    monkeypatch,
    specs: dict[str, tuple[ToolSpec, Path, str]],
) -> None:
    """Exact domain surfaces must not rebuild CAD state once per visible tool."""
    import VibeCADSession as session

    service = _SurfaceService("vibescript")
    service.registry = _SpecRegistry(specs)
    calls: list[str] = []

    def runtime_state(_service: object) -> dict[str, Any]:
        calls.append("runtime")
        return {"edit_mode": None}

    monkeypatch.setattr(session, "_minimal_runtime_state", runtime_state)
    monkeypatch.setattr(
        session, "provider_engine_from_service", lambda _service: "vibescript"
    )

    schemas = session.provider_tool_schemas(service, "PartWorkbench")

    assert calls == ["runtime"]
    assert any(schema["name"] == "vibescript.create_program" for schema in schemas)
    assert not any(schema["name"].startswith("part.") for schema in schemas)


def test_provider_schema_build_reuses_turn_context_runtime_state(
    monkeypatch,
    specs: dict[str, tuple[ToolSpec, Path, str]],
) -> None:
    """Turn-start context may provide its already captured edit state."""
    import VibeCADSession as session

    service = _SurfaceService("vibescript")
    service.registry = _SpecRegistry(specs)

    def unexpected_runtime_state(_service: object) -> dict[str, Any]:
        raise AssertionError("runtime state was captured twice")

    monkeypatch.setattr(session, "_minimal_runtime_state", unexpected_runtime_state)
    monkeypatch.setattr(
        session, "provider_engine_from_service", lambda _service: "vibescript"
    )

    schemas = session.provider_tool_schemas(
        service,
        "PartWorkbench",
        runtime_state={"edit_mode": None},
    )

    assert schemas


def test_all_exact_surfaces_fit_their_model_context_budgets(specs) -> None:
    """All VibeScript surfaces stay bounded without dropping exact schemas."""

    import json

    import VibeCADProvider as provider
    import VibeCADSession as session
    from VibeCADModelingSurface import resolve_modeling_surface
    from VibeCADVibeScriptDomains import VIBESCRIPT_WORKBENCH_PACKS

    observed_workbenches = 0
    for workbench in VIBESCRIPT_WORKBENCH_PACKS:
        observed_workbenches += 1
        surface = resolve_modeling_surface(workbench, "vibescript")
        schemas = [
            session._provider_schema_copy(
                specs[name][0].to_schema(active_workbench=workbench)
            )
            for name in surface.tool_names
        ]
        schema_bytes = len(
            json.dumps(
                schemas,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        snapshot = session._turn_start_tool_surface(
            workbench,
            schemas,
            resolution=surface,
        )
        assert snapshot["schema_count"] == len(schemas)
        assert schema_bytes <= session.MAX_PROVIDER_TOOL_SCHEMAS_JSON_BYTES
        assert schema_bytes <= session.MAX_VIBESCRIPT_TOOL_SCHEMAS_JSON_BYTES

        context = {
            "workbench": workbench,
            "modeling_surface": surface.summary(),
            "provider_tool_schemas": schemas,
        }
        instructions = provider._provider_instructions(context)
        assert (
            len(instructions.encode("utf-8"))
            <= provider.MAX_PROVIDER_INSTRUCTIONS_BYTES
        )

    assert observed_workbenches == len(VIBESCRIPT_WORKBENCH_PACKS) == 17


@pytest.mark.parametrize(
    ("workbench", "production_ready"),
    (
        ("PartWorkbench", True),
        ("SketcherWorkbench", True),
        ("DraftWorkbench", True),
        ("SurfaceWorkbench", True),
        ("AssemblyWorkbench", True),
        ("SpreadsheetWorkbench", True),
        ("MaterialWorkbench", True),
        ("MeshWorkbench", True),
        ("MeshPartWorkbench", True),
        ("FemWorkbench", True),
        ("TechDrawWorkbench", True),
    ),
)
def test_selected_vibescript_excludes_human_mutation_commands(
    workbench: str,
    production_ready: bool,
) -> None:
    import VibeCADSession as session
    from VibeCADModelingSurface import (
        PROVIDER_READ_TOOL_OWNERS,
        provider_read_tool_is_visible,
    )

    service = _SurfaceService("vibescript")
    names = session._surface_tool_names(service, workbench)
    from VibeCADVibeScriptDomains import get_vibescript_pack

    domain_pack = get_vibescript_pack(workbench)
    assert domain_pack is not None
    allowed_reads = {
        name
        for name in PROVIDER_READ_TOOL_OWNERS
        if provider_read_tool_is_visible(
            name,
            workbench=workbench,
            engine="vibescript",
        )
    }
    assert allowed_reads <= names
    assert domain_pack.production_ready is production_ready
    if production_ready:
        assert set(domain_pack.provider_tool_names) <= names
        assert set(domain_pack.tool_names) - set(domain_pack.provider_tool_names)
        assert (
            set(domain_pack.tool_names) - set(domain_pack.provider_tool_names)
        ).isdisjoint(names)
        assert "core.inspect" not in names
        assert len([name for name in names if name.startswith("vibescript.")]) == len(
            domain_pack.provider_tool_names
        )
    else:
        assert not any(name.startswith("vibescript.") for name in names)


def test_removed_bim_surface_is_not_registered() -> None:
    from tool_impl.service import TOOL_MODULE_NAMES
    from VibeCADCore import VibeCADService
    from VibeCADModelingSurface import resolve_modeling_surface
    from VibeCADVibeScriptDomains import domain_availability, get_vibescript_pack

    assert not hasattr(VibeCADService, "vibescript_on_bim_enabled")
    assert not hasattr(VibeCADService, "bim_summary")
    assert not any(name.startswith("bim_") for name in TOOL_MODULE_NAMES)
    assert get_vibescript_pack("BIMWorkbench") is None
    available, reason = domain_availability("BIMWorkbench")
    assert available is False
    assert reason
    surface = resolve_modeling_surface("BIMWorkbench", "vibescript")
    assert surface.available is False
    assert surface.cad_tool_names == ()
    assert surface.unavailable_reason


def test_partdesign_vibescript_surface_is_its_exact_domain_pack() -> None:
    import VibeCADSession as session
    from VibeCADModelingSurface import resolve_modeling_surface

    expected = (
        "assembly.play_simulation",
        "assembly.stop_simulation",
        "component_catalog.search",
        "conversation.ask_user",
        "conversation.review_design",
        "core.capture_view_screenshot",
        "core.set_view",
        "fastener_catalog.search",
        "material_catalog.search",
        "vibescript.read_source",
        "vibescript.read_operation",
        "vibescript.read_api",
        "vibescript.read_geometry",
        "vibescript.read_placement",
        "vibescript.build_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
        "vibescript.delete_object",
        "vibescript.create_part",
        "vibescript.create_assembly",
        "assembly.list_structure",
    )
    surface = resolve_modeling_surface("PartDesignWorkbench", "vibescript")
    names = session._surface_tool_names(
        _SurfaceService("vibescript"), "PartDesignWorkbench"
    )
    assert surface.tool_names == expected
    assert names == set(expected)
    qualified_domains = {
        name.split(".")[1]
        for name in names
        if name.startswith("vibescript.") and name.count(".") == 2
    }
    assert qualified_domains == set()


def test_model_and_assembly_share_one_stable_provider_contract() -> None:
    import VibeCADSession as session
    from VibeCADModelingSurface import resolve_modeling_surface

    service = _SurfaceService("vibescript")
    model = resolve_modeling_surface("PartDesignWorkbench", "vibescript")
    assembly = resolve_modeling_surface("AssemblyWorkbench", "vibescript")

    assert model.surface_id == assembly.surface_id
    assert model.tool_names == assembly.tool_names
    assert session._surface_tool_names(
        service, "PartDesignWorkbench"
    ) == session._surface_tool_names(service, "AssemblyWorkbench")
    assert {
        "assembly.list_structure",
        "assembly.play_simulation",
        "assembly.stop_simulation",
        "material_catalog.search",
        "vibescript.create_part",
        "vibescript.edit_source",
    } <= set(model.tool_names)


def test_model_authoring_contract_survives_document_and_task_transitions(
    specs, monkeypatch
) -> None:
    import VibeCADSession as session

    class Service(_SurfaceService):
        registry = _SpecRegistry(specs)

        def _active_document(self) -> None:
            return None

    service = Service("vibescript")
    monkeypatch.setattr(
        session, "provider_engine_from_service", lambda _service: "vibescript"
    )
    no_document = session.provider_tool_schemas(
        service,
        "PartDesignWorkbench",
        runtime_state={"edit_mode": False, "active_sketch": None},
    )
    sketch_task = session.provider_tool_schemas(
        service,
        "PartDesignWorkbench",
        runtime_state={
            "edit_mode": True,
            "active_sketch": {"name": "Sketch"},
        },
    )

    assert [item["name"] for item in no_document] == [
        item["name"] for item in sketch_task
    ]
    assert "vibescript.create_part" in {
        item["name"] for item in no_document
    }


def test_retired_surface_and_publication_shims_are_absent() -> None:
    from importlib.util import find_spec

    import vibescript_cam_worker as cam_worker
    import VibeCADVibeScriptDomainPublication as publication

    assert find_spec("VibeCADWorkbenchTools") is None
    assert not hasattr(publication, "_configure_material")
    assert not hasattr(cam_worker, "_path_records")
    assert "core_delete_object" not in TOOL_MODULE_NAMES


def test_non_user_workbenches_do_not_gain_vibescript() -> None:
    import VibeCADSession as session

    service = _SurfaceService("vibescript")
    for workbench in (None, "NoneWorkbench", "TestWorkbench", "UnknownWorkbench"):
        names = session._surface_tool_names(service, workbench)
        assert not any(name.startswith("vibescript.") for name in names)


def test_every_constructed_surface_contains_at_most_one_scripted_engine() -> None:
    import VibeCADSession as session
    from VibeCADVibeScriptDomains import VIBESCRIPT_WORKBENCH_PACKS

    prefixes = tuple(f"{engine}." for engine in session.SCRIPTED_ENGINE_PROVIDER_TOOLS)
    workbenches = (
        *VIBESCRIPT_WORKBENCH_PACKS,
        "NoneWorkbench",
        "TestWorkbench",
        "UnknownWorkbench",
    )
    for engine in session.SCRIPTED_ENGINE_PROVIDER_TOOLS:
        service = _SurfaceService(engine)
        for workbench in workbenches:
            names = session._surface_tool_names(service, workbench)
            surfaced_engines = {
                prefix
                for prefix in prefixes
                if any(name.startswith(prefix) for name in names)
            }
            assert len(surfaced_engines) <= 1, (
                f"{workbench} with {engine} surfaced {sorted(surfaced_engines)}"
            )


def test_real_vibescript_workbench_schemas_form_valid_codex_snapshots(specs) -> None:
    """Every workbench-shaped VibeScript surface survives the Codex wire format."""
    import VibeCADProvider as provider
    import VibeCADSession as session
    from VibeCADVibeScriptDomains import VIBESCRIPT_WORKBENCH_PACKS

    service = _SurfaceService("vibescript")
    for workbench in VIBESCRIPT_WORKBENCH_PACKS:
        names = session._surface_tool_names(service, workbench)
        schemas = [
            session._provider_schema_copy(
                specs[name][0].to_schema(active_workbench=workbench)
            )
            for name in sorted(names)
            if specs[name][0].supports_edit_mode("none")
        ]
        snapshot = session._turn_start_tool_surface(workbench, schemas)
        dynamic_tools, dynamic_names = provider._codex_dynamic_tool_surface(
            {
                "provider_tool_schemas": schemas,
                "provider_tool_surface": snapshot,
                "modeling_surface": {
                    key: snapshot[key]
                    for key in (
                        "workbench",
                        "engine",
                        "domain",
                        "surface_id",
                        "available",
                        "unavailable_reason",
                    )
                },
            }
        )

        assert dynamic_tools, workbench
        assert set(dynamic_names.values()) == {
            str(schema["name"]) for schema in schemas
        }, workbench


def test_vibescript_uses_one_universal_lifecycle_and_retains_qualified_aliases(
    specs,
) -> None:
    import VibeCADVibeScriptDomains as domains

    assert {
        "vibescript.read_source",
        "vibescript.read_api",
        "vibescript.create_program",
        "vibescript.build_program",
        "vibescript.edit_source",
        "vibescript.set_inputs",
        "vibescript.reconfigure_program",
        "vibescript.delete_output",
        "vibescript.delete_program",
    } <= set(specs)
    removed_suffixes = {
        "describe_api",
        "inspect_program",
        "edit_source",
    }
    qualified_writes = {
        "create_program",
        "set_inputs",
        "reconfigure_program",
        "delete_program",
    }
    universal_names = {
        f"vibescript.{operation}" for operation in domains.UNIVERSAL_SOURCE_OPERATIONS
    } | {
        f"vibescript.{operation}"
        for operation in domains.MODEL_ASSEMBLY_SOURCE_OPERATIONS
    }
    domain_names = {name for name in specs if name.startswith("vibescript.")}
    assert domain_names
    for name in domain_names:
        if name.count(".") == 1:
            assert name in universal_names
            continue
        namespace, domain, operation = name.split(".")
        assert namespace == "vibescript"
        assert domain
        assert operation in qualified_writes
        assert operation not in removed_suffixes


def test_domain_lifecycle_schemas_accept_bounded_structured_inputs(specs) -> None:
    from VibeCADTools import ToolArgumentValidationError

    create, _, _ = specs["vibescript.partdesign.create_program"]
    valid = {
        "program_name": "Parametric Bracket",
        "source": (
            "profile = api.sketch([api.circle([0,0], inputs['radius'])])\n"
            "feature = api.extrude(profile, inputs['height'], "
            "operation='add_material')\n"
            "result = {'Part': api.body(feature)}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "radius": {"type": "number", "exclusiveMinimum": 0},
                "height": {"type": "number", "exclusiveMinimum": 0},
                "variant": {"type": "string", "enum": ["short", "tall"]},
                "offsets": {
                    "type": "array",
                    "items": {"type": "number"},
                    "maxItems": 8,
                },
            },
            "required": ["radius", "height", "variant", "offsets"],
            "additionalProperties": False,
        },
        "inputs": {
            "radius": 4.0,
            "height": 12.0,
            "variant": "short",
            "offsets": [0.0, 5.0],
        },
        "expected_outputs": [{"name": "Part", "type": "solid"}],
    }
    create.validate_arguments(valid)

    with pytest.raises(ToolArgumentValidationError):
        create.validate_arguments(
            {
                **valid,
                "expected_outputs": [{"name": "Part", "type": "mesh"}],
            }
        )


def test_each_domain_describe_api_matches_its_runtime_and_is_json_safe() -> None:
    import json

    import VibeCADVibeScriptDomains as domains

    for pack in domains.VIBESCRIPT_WORKBENCH_PACKS.values():
        adapter = domains.get_domain_adapter(pack.domain)
        assert adapter is not None
        payload = adapter.describe_api()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["domain"] == pack.domain
        assert payload["workbench"] == pack.workbench
        assert payload["source_globals"] == ["doc", "inputs", "api"]
        assert [entry["name"] for entry in payload["runtime_exports"]] == list(
            pack.api_exports
        )


def test_universal_source_tools_are_the_only_model_facing_vibescript_reads(
    specs,
) -> None:
    import VibeCADSession as session

    assert "core.inspect" not in session.VIBESCRIPT_PROVIDER_TOOLS
    assert {
        "vibescript.read_source",
        "vibescript.read_api",
        "vibescript.read_geometry",
        "vibescript.read_placement",
    } <= set(
        session.VIBESCRIPT_PROVIDER_TOOLS
    )
    assert all(
        not name.endswith((".describe_api", ".inspect_program"))
        for name in session.VIBESCRIPT_PROVIDER_TOOLS
    )
    assert specs["vibescript.read_source"][0].safety == SafetyLevel.READ
    assert specs["vibescript.read_api"][0].safety == SafetyLevel.READ
    assert specs["vibescript.read_geometry"][0].safety == SafetyLevel.READ
    assert specs["vibescript.read_placement"][0].safety == SafetyLevel.READ
    edit = specs["vibescript.edit_source"][0]
    assert edit.safety == SafetyLevel.SAFE_WRITE
    assert edit.parameters["required"] == [
        "program",
        "expected_revision",
        "source",
    ]
    assert "replacements" not in edit.parameters["properties"]


def test_retired_core_inspector_implementation_does_not_exist() -> None:
    assert "core_inspect" not in TOOL_MODULE_NAMES
    assert not (TOOL_IMPL_DIR / "service" / "core_inspect.py").exists()
    assert not (TOOL_IMPL_DIR.parent / "VibeCADInspection.py").exists()


def test_removed_partdesign_runtime_files_do_not_exist() -> None:
    removed = (
        "VibeCADVibeScript.py",
        "vibescript_api.py",
        "vibescript_executor.py",
        "vibescript_worker.py",
    )
    root = TOOL_IMPL_DIR.parent
    assert all(not (root / name).exists() for name in removed)


def test_removed_hidden_delete_compatibility_tool_does_not_exist() -> None:
    assert not (TOOL_IMPL_DIR / "core_delete_object.py").exists()


def test_removed_engine_and_vibescript_forwarders_do_not_exist() -> None:
    import VibeCADCore as core
    import VibeCADProject as project

    for owner in (core.VibeCADService, project.VibeCADProjectStore):
        assert not hasattr(owner, "partdesign_engine")
        assert not hasattr(owner, "partdesign_engine_state")
        assert not hasattr(owner, "set_partdesign_engine")
    assert not hasattr(project, "PARTDESIGN_ENGINES")
    assert not hasattr(project, "DEFAULT_PARTDESIGN_ENGINE")
