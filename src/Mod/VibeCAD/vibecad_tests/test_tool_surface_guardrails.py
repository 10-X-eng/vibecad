# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guardrail: every provider-callable tool is deliberate and structurally safe.

Four invariants are enforced:

1. No orphan tools — every registered tool spec is surfaced through
   ``CORE_PROVIDER_TOOLS``, at least one workbench pack, or one of the
   scripted-engine session surfaces (``BUILD123D_PROVIDER_TOOLS`` /
   ``OPENSCAD_PROVIDER_TOOLS`` / ``VIBESCRIPT_PROVIDER_TOOLS``). A tool
   registered without any surface fails this test, so legacy or
   experimental tools cannot silently become callable by default.
2. No dangling names — every name in ``CORE_PROVIDER_TOOLS`` and every pack
   ``tool_names``/``required_adjacent_tool_names`` entry resolves to a
   registered, validating :class:`ToolSpec`.
3. Writes are transactional — every non-READ tool either contains a FreeCAD
   transaction marker in its own module or in a same-package module it
   imports, or appears in a justified allowlist.
4. No legacy command execution — ``tool_impl`` never contains
   ``runCommand``/``doCommand``/``sendMsgToActiveView``; all FreeCAD
   semantics run through the typed Python APIs.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any, Iterator

import pytest

from VibeCADTools import SafetyLevel, ToolSpec

TOOL_PACKAGES = ("tool_impl.service", "tool_impl.sketcher")

TOOL_IMPL_DIR = Path(__file__).resolve().parent.parent / "tool_impl"

# Write-safety tools that legitimately run without a
# FreeCAD document transaction. Each entry needs a reason.
TRANSACTION_EXEMPT = {
    # Enters native sketch edit mode; changes UI state, not document data.
    "partdesign.edit_sketch",
    # Accepts native sketch edit mode; resetEdit owns the Sketcher transaction commit.
    "sketcher.close_sketch",
}

# Runner-handled engine tools carry only a spec in tool_impl; their document
# mutations run inside the engine module, so search it for markers too.
ENGINE_MODULES = {
    "build123d": TOOL_IMPL_DIR.parent / "VibeCADBuild123d.py",
    "openscad": TOOL_IMPL_DIR.parent / "VibeCADOpenSCAD.py",
    "vibescript": TOOL_IMPL_DIR.parent / "VibeCADVibeScript.py",
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
            assert spec.name not in specs, (
                f"Duplicate tool name {spec.name!r} from {module.__file__}"
            )
            specs[spec.name] = (spec, Path(module.__file__), package_name)
    return specs


@pytest.fixture(scope="module")
def specs() -> dict[str, tuple[ToolSpec, Path, str]]:
    return _collect_specs()


@pytest.fixture(scope="module")
def packs() -> list[dict[str, Any]]:
    import VibeCADWorkbenchTools as wbt

    return list(wbt.list_tool_packs())


@pytest.fixture(scope="module")
def core_tools() -> frozenset[str]:
    import VibeCADSession as session

    return frozenset(session.CORE_PROVIDER_TOOLS)


@pytest.fixture(scope="module")
def engine_tools() -> frozenset[str]:
    import VibeCADSession as session

    return frozenset(
        session.BUILD123D_PROVIDER_TOOLS
        | session.OPENSCAD_PROVIDER_TOOLS
        | session.VIBESCRIPT_PROVIDER_TOOLS
    )


def _surfaced_names(
    core_tools: frozenset[str],
    packs: list[dict[str, Any]],
    engine_tools: frozenset[str] = frozenset(),
) -> set[str]:
    surfaced = set(core_tools) | set(engine_tools)
    for pack in packs:
        surfaced.update(pack["tool_names"])
        surfaced.update(pack.get("required_adjacent_tool_names", ()))
    return surfaced


def test_no_orphan_tools(specs, packs, core_tools, engine_tools) -> None:
    """1. Every registered tool must belong to core, a pack, or an engine."""
    orphans = sorted(set(specs) - _surfaced_names(core_tools, packs, engine_tools))
    assert not orphans, (
        "Tools registered but not surfaced by CORE_PROVIDER_TOOLS, any "
        "workbench pack, or an engine session surface (add to one or remove "
        f"the registration): {orphans}"
    )


def test_no_dangling_names(specs, packs, core_tools, engine_tools) -> None:
    """2. Every surfaced name must resolve to a registered spec."""
    dangling = sorted(_surfaced_names(core_tools, packs, engine_tools) - set(specs))
    assert not dangling, (
        f"Names surfaced by core/packs/engines with no registered tool spec: {dangling}"
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


def test_intentionally_empty_packs_stay_empty(packs) -> None:
    """TestWorkbench and NoneWorkbench must never surface tools."""
    for pack in packs:
        if pack["workbench"] in {"TestWorkbench", "NoneWorkbench"}:
            assert not pack["tool_names"], (
                f"{pack['workbench']} must stay empty; found {pack['tool_names']}"
            )


def test_adjacent_tools_are_owned_by_another_pack(packs) -> None:
    """Adjacent tools must be borrowed from a pack that lists them natively."""
    owned: set[str] = set()
    for pack in packs:
        owned.update(pack["tool_names"])
    for pack in packs:
        foreign = [
            name
            for name in pack.get("required_adjacent_tool_names", ())
            if name not in owned
        ]
        assert not foreign, (
            f"{pack['workbench']} borrows tools no pack owns natively: {foreign}"
        )


# ---------------------------------------------------------------------------
# Scripted-engine surface guardrails: VibeScript integration is deliberate and
# does not regress the existing build123d/OpenSCAD surfaces.
# ---------------------------------------------------------------------------


def test_every_vibescript_tool_is_surfaced(specs) -> None:
    """No orphan vibescript tools: each registered spec is on the surface."""
    import VibeCADSession as session

    registered = {name for name in specs if name.startswith("vibescript.")}
    assert registered, "expected registered vibescript.* tool specs"
    orphans = sorted(registered - session.VIBESCRIPT_PROVIDER_TOOLS)
    assert not orphans, (
        "vibescript tools registered but missing from "
        f"VIBESCRIPT_PROVIDER_TOOLS: {orphans}"
    )


def test_every_surfaced_vibescript_tool_is_registered(specs) -> None:
    """The vibescript surface names only registered vibescript specs."""
    import VibeCADSession as session

    surfaced = {
        name
        for name in session.VIBESCRIPT_PROVIDER_TOOLS
        if name.startswith("vibescript.")
    }
    dangling = sorted(surfaced - set(specs))
    assert not dangling, (
        f"VIBESCRIPT_PROVIDER_TOOLS names unregistered tools: {dangling}"
    )


def test_engine_surface_table_covers_every_scripted_engine() -> None:
    """Each non-native engine gets a provider surface, and nothing else does."""
    import VibeCADSession as session
    from VibeCADProject import PARTDESIGN_ENGINES

    scripted_engines = set(PARTDESIGN_ENGINES) - {"native"}
    assert set(session.SCRIPTED_ENGINE_PROVIDER_TOOLS) == scripted_engines


def test_default_engine_is_vibescript_with_a_provider_surface() -> None:
    """The out-of-box default engine is vibescript, and its tool surface is
    registered so new projects are immediately usable without configuration."""
    import VibeCADSession as session
    from VibeCADProject import DEFAULT_PARTDESIGN_ENGINE, PARTDESIGN_ENGINES

    assert DEFAULT_PARTDESIGN_ENGINE == "vibescript"
    assert DEFAULT_PARTDESIGN_ENGINE in PARTDESIGN_ENGINES
    surface = session.SCRIPTED_ENGINE_PROVIDER_TOOLS[DEFAULT_PARTDESIGN_ENGINE]
    assert surface, "default engine must expose a non-empty provider tool surface"


def test_runner_tools_are_subsets_of_their_provider_surfaces() -> None:
    """Runner-dispatched tools must always be provider-callable."""
    import VibeCADSession as session

    pairs = (
        ("build123d", session.BUILD123D_RUNNER_TOOLS, session.BUILD123D_PROVIDER_TOOLS),
        ("openscad", session.OPENSCAD_RUNNER_TOOLS, session.OPENSCAD_PROVIDER_TOOLS),
        (
            "vibescript",
            session.VIBESCRIPT_RUNNER_TOOLS,
            session.VIBESCRIPT_PROVIDER_TOOLS,
        ),
    )
    for engine, runner_tools, provider_tools in pairs:
        stranded = sorted(set(runner_tools) - set(provider_tools))
        assert not stranded, (
            f"{engine} runner tools missing from its provider surface: {stranded}"
        )


def test_runner_dispatch_covers_exactly_the_runner_tool_sets() -> None:
    """The table-driven dispatch maps all runner tools and nothing more."""
    import VibeCADSession as session

    expected = (
        set(session.BUILD123D_RUNNER_TOOLS)
        | set(session.OPENSCAD_RUNNER_TOOLS)
        | set(session.VIBESCRIPT_RUNNER_TOOLS)
    )
    assert set(session._SCRIPTED_RUNNER_BY_TOOL) == expected


class _SurfaceService:
    def __init__(
        self,
        engine: str,
        *,
        vibescript_on_bim: bool = False,
    ) -> None:
        self.engine = engine
        self.vibescript_on_bim = vibescript_on_bim

    def partdesign_engine(self) -> str:
        return self.engine

    def vibescript_on_bim_enabled(self) -> bool:
        return self.vibescript_on_bim

    def _active_document(self) -> object:
        return object()

    def design_review_enabled(self) -> bool:
        return True


@pytest.mark.parametrize(
    "workbench",
    ("AssemblyWorkbench", "FemWorkbench", "TechDrawWorkbench"),
)
def test_selected_vibescript_joins_representative_workbench_surfaces(
    workbench: str,
) -> None:
    import VibeCADSession as session
    from VibeCADWorkbenchTools import get_tool_pack

    service = _SurfaceService("vibescript")
    names = session._surface_tool_names(service, workbench)
    pack = get_tool_pack(workbench)

    assert pack is not None
    assert set(pack.provider_tool_names()) <= names
    assert session.VIBESCRIPT_PROVIDER_TOOLS <= names


def test_vibescript_is_excluded_from_bim_until_its_preference_is_enabled() -> None:
    import VibeCADSession as session
    from VibeCADWorkbenchTools import get_tool_pack

    pack = get_tool_pack("BIMWorkbench")
    assert pack is not None
    native_names = set(session.CORE_PROVIDER_TOOLS) | set(pack.provider_tool_names())

    default_names = session._surface_tool_names(
        _SurfaceService("vibescript"), "BIMWorkbench"
    )
    assert default_names == native_names
    assert not any(name.startswith("vibescript.") for name in default_names)

    enabled_names = session._surface_tool_names(
        _SurfaceService("vibescript", vibescript_on_bim=True),
        "BIMWorkbench",
    )
    assert native_names <= enabled_names
    assert session.VIBESCRIPT_PROVIDER_TOOLS <= enabled_names


@pytest.mark.parametrize("engine", ("native", "build123d", "openscad"))
def test_vibescript_does_not_follow_a_different_selected_engine(engine: str) -> None:
    import VibeCADSession as session

    names = session._surface_tool_names(
        _SurfaceService(engine, vibescript_on_bim=True),
        "AssemblyWorkbench",
    )
    assert not any(name.startswith("vibescript.") for name in names)


def test_partdesign_vibescript_surface_is_unchanged() -> None:
    import VibeCADSession as session

    names = session._surface_tool_names(
        _SurfaceService("vibescript"), "PartDesignWorkbench"
    )
    assert names == session.VIBESCRIPT_PROVIDER_TOOLS


def test_non_user_workbenches_do_not_gain_vibescript() -> None:
    import VibeCADSession as session

    service = _SurfaceService("vibescript", vibescript_on_bim=True)
    for workbench in (None, "NoneWorkbench", "TestWorkbench", "UnknownWorkbench"):
        names = session._surface_tool_names(service, workbench)
        assert not any(name.startswith("vibescript.") for name in names)


def test_every_constructed_surface_contains_at_most_one_scripted_engine() -> None:
    import VibeCADSession as session
    from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS

    prefixes = tuple(f"{engine}." for engine in session.SCRIPTED_ENGINE_PROVIDER_TOOLS)
    for engine in ("native", *session.SCRIPTED_ENGINE_PROVIDER_TOOLS):
        service = _SurfaceService(engine, vibescript_on_bim=True)
        for workbench in WORKBENCH_TOOL_PACKS:
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
    """Every extended native pack must survive the subscription wire format."""
    import VibeCADProvider as provider
    import VibeCADSession as session
    from VibeCADWorkbenchTools import WORKBENCH_TOOL_PACKS

    service = _SurfaceService("vibescript", vibescript_on_bim=True)
    for workbench in WORKBENCH_TOOL_PACKS:
        if workbench in {"NoneWorkbench", "TestWorkbench"}:
            continue
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
            }
        )

        assert dynamic_tools, workbench
        assert set(dynamic_names.values()) == {
            str(schema["name"]) for schema in schemas
        }, workbench


def test_existing_engine_surfaces_did_not_regress() -> None:
    """Adding VibeScript must leave the other engine surfaces byte-identical."""
    import VibeCADSession as session

    assert session.BUILD123D_PROVIDER_TOOLS == {
        "conversation.ask_user",
        "conversation.review_design",
        "core.capture_view_screenshot",
        "core.set_view",
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
    assert session.OPENSCAD_PROVIDER_TOOLS == {
        "conversation.ask_user",
        "conversation.review_design",
        "core.capture_view_screenshot",
        "core.set_view",
        "partdesign.find_subelements",
        "partdesign.measure",
        "openscad.inspect_model",
        "openscad.create_model",
        "openscad.edit_source",
        "openscad.set_parameters",
        "openscad.set_conversion_mode",
        "openscad.delete_model",
    }
    assert session.BUILD123D_RUNNER_TOOLS == {
        "build123d.create_model",
        "build123d.edit_source",
        "build123d.set_parameters",
        "build123d.set_inputs",
        "build123d.reconfigure_model",
    }
    assert session.OPENSCAD_RUNNER_TOOLS == {
        "openscad.create_model",
        "openscad.edit_source",
        "openscad.set_parameters",
        "openscad.set_conversion_mode",
    }


def test_vibescript_parameter_schemas_declare_flat_scalars(specs) -> None:
    """The params/patch objects must truthfully declare the flat-scalar rule.

    The engine only accepts flat maps of finite numbers (``vibescript_api.Params``),
    so the JSON schema must reject structured values up front instead of
    advertising ``additionalProperties: True`` and failing later with a less
    actionable engine error.
    """
    cases = {
        "vibescript.create_model": ("parameters", {"type": "number"}),
        "vibescript.reconfigure_model": ("parameters", {"type": "number"}),
        "vibescript.set_parameters": ("patch", {"type": ["number", "null"]}),
        "vibescript.edit_source": ("parameter_patch", {"type": ["number", "null"]}),
    }
    for tool_name, (property_name, expected_values) in cases.items():
        spec, _, _ = specs[tool_name]
        schema = spec.parameters["properties"][property_name]
        assert schema["additionalProperties"] == expected_values, (
            f"{tool_name} {property_name} must constrain values to "
            f"{expected_values}, got {schema.get('additionalProperties')!r}"
        )
        assert schema["propertyNames"] == {"pattern": "^[A-Za-z][A-Za-z0-9_]*$"}, (
            f"{tool_name} {property_name} must constrain keys to identifiers"
        )
        description = schema["description"]
        assert "finite number" in description and "nested" in description, (
            f"{tool_name} {property_name} description must state the "
            f"flat-scalar rule; got: {description!r}"
        )


def test_vibescript_parameter_schemas_reject_structured_values(specs) -> None:
    """Nested params are rejected at the schema stage, flat numbers pass."""
    from VibeCADTools import ToolArgumentValidationError

    spec, _, _ = specs["vibescript.create_model"]
    good = {
        "model_name": "Impeller",
        "source": "result = {}",
        "parameters": {"hub_diameter": 40.0, "blade_count": 12},
        "expected_outputs": ["Impeller"],
    }
    spec.validate_arguments(good)

    for bad_value in ({"sections": [{"r": 1.0}]}, {"name": "steel"}, {"flag": True}):
        with pytest.raises(ToolArgumentValidationError) as excinfo:
            spec.validate_arguments({**good, "parameters": bad_value})
        assert "parameters" in str(excinfo.value)

    patch_spec, _, _ = specs["vibescript.set_parameters"]
    base = {"model_id": "0" * 32, "expected_revision": "0" * 64}
    patch_spec.validate_arguments({**base, "patch": {"hub_diameter": 42.5}})
    patch_spec.validate_arguments({**base, "patch": {"obsolete_param": None}})
    with pytest.raises(ToolArgumentValidationError):
        patch_spec.validate_arguments({**base, "patch": {"table": [1, 2, 3]}})


def test_edit_source_parameter_patch_is_optional_and_flat(specs) -> None:
    """edit_source accepts an optional patch with the set_parameters shape.

    parameter_patch enables atomic schema+source evolution in one call: a
    source edit that starts reading a new param and stops reading an old one
    can supply the new value and null-remove the obsolete key together.
    Patch-free calls must stay valid (backward compatible), and structured or
    empty patches must fail at the schema stage.
    """
    from VibeCADTools import ToolArgumentValidationError

    spec, _, _ = specs["vibescript.edit_source"]
    assert "parameter_patch" not in spec.parameters["required"]

    base = {
        "model_id": "0" * 32,
        "expected_revision": "0" * 64,
        "edits": [{"old_text": "a = 1", "new_text": "a = 2"}],
    }
    spec.validate_arguments(base)
    spec.validate_arguments(
        {**base, "parameter_patch": {"splitter_count": 4.0, "splitter_angle": None}}
    )

    for bad_patch in (
        {"sections": [{"r": 1.0}]},
        {"nested": {"r": 1.0}},
        {"name": "steel"},
        {"flag": True},
        {"_private": 1.0},
        {},
    ):
        with pytest.raises(ToolArgumentValidationError) as excinfo:
            spec.validate_arguments({**base, "parameter_patch": bad_patch})
        assert "parameter_patch" in str(excinfo.value)

    description = spec.description
    assert "without changing its parameters" not in description
    assert "RFC 7396" in description, (
        "edit_source description must document the merge-patch semantics"
    )


def test_describe_api_is_surfaced_and_referenced(specs) -> None:
    """describe_api is provider-visible and the authoring tools point at it."""
    import VibeCADSession as session

    assert "vibescript.describe_api" in session.VIBESCRIPT_PROVIDER_TOOLS
    spec, _, _ = specs["vibescript.describe_api"]
    assert spec.safety == SafetyLevel.READ
    assert not spec.requires_document, (
        "describe_api is a static reference and must not require a document"
    )
    for authoring_tool in (
        "vibescript.create_model",
        "vibescript.edit_source",
        "vibescript.reconfigure_model",
    ):
        authoring_spec, _, _ = specs[authoring_tool]
        assert "vibescript.describe_api" in authoring_spec.description, (
            f"{authoring_tool} description must reference vibescript.describe_api"
        )


def test_describe_api_covers_the_complete_authoring_surface() -> None:
    """The reference payload documents 100% of vibescript_api.__all__.

    Signatures are introspected from the live module, so any callable whose
    signature cannot be resolved fails here rather than silently returning
    ``signature: None`` to the provider.
    """
    import inspect

    import vibescript_api
    import vibescript_executor
    from tool_impl.service import vibescript_describe_api

    payload = vibescript_describe_api.run(service=None)
    assert payload["ok"] is True
    assert payload["engine"] == "vibescript"

    documented = {entry["name"]: entry for entry in payload["api"]}
    assert set(documented) == set(vibescript_api.__all__), (
        "describe_api must document exactly vibescript_api.__all__; "
        f"missing={sorted(set(vibescript_api.__all__) - set(documented))} "
        f"extra={sorted(set(documented) - set(vibescript_api.__all__))}"
    )
    for name, entry in documented.items():
        obj = getattr(vibescript_api, name)
        if inspect.isfunction(obj) or inspect.isclass(obj):
            assert entry["summary"], f"{name} must expose a docstring summary"
            if entry["kind"] != "exception":
                # Exception classes inherit Exception.__init__, whose C-level
                # signature is not introspectable; their summary suffices.
                assert entry["signature"], f"{name} must expose a signature"
        if entry["kind"] == "class":
            method_names = {method["name"] for method in entry["methods"]}
            public = {
                member_name
                for member_name, member in inspect.getmembers(obj)
                if not member_name.startswith("_")
                and (callable(member) or isinstance(member, property))
            }
            assert method_names == public, (
                f"{name} methods drifted: {sorted(public ^ method_names)}"
            )

    namespace = payload["namespace"]
    assert set(namespace["injected"]) == {"doc", "params", *vibescript_api.__all__}
    assert "result" in namespace["result_contract"]

    policy = payload["policy"]
    assert policy["allowed_import_roots"] == sorted(
        vibescript_executor.ALLOWED_IMPORT_ROOTS
    )
    assert "getattr" in policy["disallowed_calls"]
    assert "print" in policy["builtins"]

    budget = payload["budget"]
    assert budget["max_seconds"] == vibescript_executor.DEFAULT_MAX_SECONDS
    assert budget["max_operations"] == vibescript_executor.DEFAULT_MAX_OPERATIONS
    assert budget["max_stdout_chars"] == vibescript_executor.MAX_STDOUT_CHARS


def test_describe_api_payload_is_json_safe() -> None:
    """The reference payload must survive JSON round-tripping unchanged."""
    import json

    from tool_impl.service import vibescript_describe_api

    payload = vibescript_describe_api.run(service=None)
    assert json.loads(json.dumps(payload)) == payload


def test_vibescript_static_policy_never_contradicts_runtime_sandbox() -> None:
    """Static source policy and the runtime sandbox must agree on builtins.

    The field report chased a runtime NameError that static validation
    should have caught: policy lived in two places that could drift. Three
    invariants keep them honest:

    1. Statically disallowed calls are never resolvable at runtime — a name
       the validator rejects must not be in the sandbox allowlist, or the
       rejection would be a lie.
    2. Statically excluded builtins are exactly the real builtins the
       sandbox cannot resolve — no allowlisted or injected name may be
       rejected, and every non-dunder real builtin outside the allowlist
       and namespace must be rejected (so new sandbox exclusions cannot
       reintroduce late NameErrors).
    3. The describe_api reference documents the same runtime allowlist the
       executor enforces, so agents read the truth.
    """
    import builtins

    import VibeCADVibeScript as vibescript
    import vibescript_api
    import vibescript_executor
    from tool_impl.service import vibescript_describe_api

    allowlist = frozenset(vibescript_executor._BUILTIN_ALLOWLIST)

    contradictions = vibescript._DISALLOWED_CALLS & allowlist
    assert not contradictions, (
        "statically disallowed calls are resolvable in the sandbox: "
        f"{sorted(contradictions)}"
    )

    excluded = vibescript._EXCLUDED_BUILTIN_NAMES
    assert not excluded & allowlist
    assert not excluded & vibescript._NAMESPACE_NAMES
    assert not excluded & frozenset(vibescript_api.__all__)
    expected_excluded = {
        name
        for name in vars(builtins)
        if not name.startswith("__")
        and name not in allowlist
        and name not in vibescript._NAMESPACE_NAMES
    }
    missing = expected_excluded - excluded
    assert not missing, (
        f"sandbox-excluded builtins escape static validation: {sorted(missing)}"
    )

    payload = vibescript_describe_api.run(service=None)
    assert set(payload["policy"]["builtins"]) == allowlist
    assert set(payload["policy"]["disallowed_calls"]) == vibescript._DISALLOWED_CALLS
