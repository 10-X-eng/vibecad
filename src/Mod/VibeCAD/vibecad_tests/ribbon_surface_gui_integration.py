# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI gate for the read-only Native ribbon surface contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback

_VIBECAD_MODULE_DIR = Path(__file__).resolve().parent.parent
if str(_VIBECAD_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_VIBECAD_MODULE_DIR))

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADProvider as ProviderModule
from VibeCADNativeActionManifest import (
    ALLOWED_ACTION_IDS_BY_SURFACE,
    KNOWN_ACTIONS_BY_SURFACE,
    classify_native_surface,
)
from VibeCADNativeContextManifest import provider_context_actions_for_surface
from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADModelingSurface import modeling_surface_from_native_provider
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADRibbonSurface import BUILD_FEATURE_KEYS
from VibeCADSession import _turn_start_tool_surface


_PERMANENT_SURFACES = {
    "PartDesignWorkbench": "model",
    "AssemblyWorkbench": "assemble",
    "MeshWorkbench": "mesh",
    "FemWorkbench": "analyze",
    "CAMWorkbench": "manufacture",
    "TechDrawWorkbench": "drawing",
    "SpreadsheetWorkbench": "parameters",
    "VibeCADAeroWorkbench": "aero",
}

_CAM_PREFERENCE_PATH = "User parameter:BaseApp/Preferences/Mod/CAM"
_DRAWING_PREFERENCE_PATH = (
    "User parameter:BaseApp/Preferences/Mod/TechDraw/dimensioning"
)
_CAM_PREFERENCE_FIELDS = (
    ("DefaultSimulatorLegacy", "cam.default_simulator_legacy"),
    ("EnableAdvancedOCLFeatures", "cam.enable_advanced_ocl_features"),
    ("EnableExperimentalFeatures", "cam.enable_experimental_features"),
)

_EXPECTED_MODEL_COMPOSITES = {
    "PartDesign_DesignPrimitive": (
        "PartDesign::DesignBox",
        "PartDesign::DesignCylinder",
        "PartDesign::DesignSphere",
        "PartDesign::DesignCone",
        "PartDesign::DesignEllipsoid",
        "PartDesign::DesignTorus",
        "PartDesign::DesignPrism",
        "PartDesign::DesignWedge",
        "PartDesign::DesignTube",
    ),
    "Part_CompOffset": ("Part_Offset", "Part_Offset2D"),
    "Part_CompJoinFeatures": (
        "Part_JoinConnect",
        "Part_JoinEmbed",
        "Part_JoinCutout",
    ),
}


def _process_events(rounds: int = 12) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.AllEvents,
            25,
        )


def _snapshot_bool(path: str, key: str, default: bool) -> tuple[bool, bool]:
    group = App.ParamGet(path)
    return key in tuple(group.GetBools()), bool(group.GetBool(key, default))


def _set_test_bool(
    snapshots: dict[tuple[str, str], tuple[bool, bool]],
    path: str,
    key: str,
    value: bool,
    *,
    default: bool,
) -> None:
    snapshots.setdefault((path, key), _snapshot_bool(path, key, default))
    App.ParamGet(path).SetBool(key, value)


def _restore_test_bools(
    snapshots: dict[tuple[str, str], tuple[bool, bool]],
) -> None:
    for (path, key), (present, value) in reversed(tuple(snapshots.items())):
        group = App.ParamGet(path)
        if present:
            group.SetBool(key, value)
        else:
            group.RemBool(key)


def _configure_variant(
    variant: str,
    snapshots: dict[tuple[str, str], tuple[bool, bool]],
) -> dict[str, dict[str, bool]]:
    if not variant:
        return {}
    result: dict[str, dict[str, bool]] = {}
    if variant == "maximum":
        cam_values = (False, True, True)
        drawing_values = (True, True)
    elif variant.startswith("drawing-"):
        bits = variant[len("drawing-") :]
        assert len(bits) == 2 and set(bits) <= {"0", "1"}, variant
        cam_values = None
        drawing_values = tuple(bit == "1" for bit in bits)
    else:
        prefix = "cam-"
        bits = variant[len(prefix) :] if variant.startswith(prefix) else ""
        assert len(bits) == 3 and set(bits) <= {"0", "1"}, variant
        cam_values = tuple(bit == "1" for bit in bits)
        drawing_values = None

    if cam_values is not None:
        expected_cam = {}
        for (preference_key, environment_key), value in zip(
            _CAM_PREFERENCE_FIELDS,
            cam_values,
            strict=True,
        ):
            _set_test_bool(
                snapshots,
                _CAM_PREFERENCE_PATH,
                preference_key,
                value,
                default=False,
            )
            expected_cam[environment_key] = value
        result["manufacture"] = expected_cam

    if drawing_values is not None:
        separated, single = drawing_values
        for preference_key, value, default in (
            ("SeparatedDimensioningTools", separated, False),
            ("SingleDimensioningTool", single, True),
        ):
            _set_test_bool(
                snapshots,
                _DRAWING_PREFERENCE_PATH,
                preference_key,
                value,
                default=default,
            )
        result["drawing"] = {
            "techdraw.separated_dimensioning_tools": separated,
            "techdraw.single_dimensioning_tool": single,
        }
    return result


def _ordered_groups(page):
    return sorted(
        (
            group
            for group in page.findChildren(QtWidgets.QFrame)
            if group.parentWidget() is page and group.property("ribbonGroup")
        ),
        key=lambda group: int(group.property("ribbonOrder")),
    )


def _page_graph(main_window):
    page = main_window.findChild(QtWidgets.QWidget, "VibeCADRibbonPage")
    assert page is not None
    labels = []
    command_ids = []

    def append_actions(actions):
        for action in actions:
            if action.isSeparator():
                continue
            command_id = str(action.property("VibeCADCommandId") or "").strip()
            if command_id:
                command_ids.append(command_id)
            if action.menu() is not None:
                append_actions(action.menu().actions())

    for group in _ordered_groups(page):
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        assert group_menu is not None and group_menu.menu() is not None
        labels.append(group_menu.text())
        append_actions(group_menu.menu().actions())
    assert len(command_ids) == len(set(command_ids)), command_ids
    return tuple(labels), tuple(command_ids)


def _assert_surface(main_window, controller, expected_surface_id):
    surface = read_active_ribbon_surface(controller)
    plans = classify_native_surface(surface)
    native_provider_surface = resolve_native_provider_surface(surface)
    labels, command_ids = _page_graph(main_window)
    assert surface.surface_id == expected_surface_id
    assert surface.revision > 0
    assert len(surface.environment_sha256) == 64
    environment = surface.to_environment()
    assert set(environment["build_features"]) == set(BUILD_FEATURE_KEYS)
    assert all(type(value) is bool for value in environment["build_features"].values())
    expected_preference_names = {
        "manufacture": {
            "cam.default_simulator_legacy",
            "cam.enable_advanced_ocl_features",
            "cam.enable_experimental_features",
        },
        "drawing": {
            "techdraw.separated_dimensioning_tools",
            "techdraw.single_dimensioning_tool",
        },
    }.get(expected_surface_id, set())
    assert set(environment["preferences"]) == expected_preference_names
    assert tuple(group.label.upper() for group in surface.groups) == labels
    assert surface.command_ids == command_ids
    assert tuple(plan.command_id for plan in plans) == command_ids
    assert native_provider_surface.available is False
    assert native_provider_surface.tool_names == ()
    assert native_provider_surface.schemas == ()
    assert {
        "Std_ViewFitAll",
        "Std_ViewIsometric",
        "VibeCAD_ToggleGrid",
        "VibeCAD_SectionView",
    } <= set(command_ids)
    return surface


def _assert_default_inventory_is_live(surface) -> None:
    expected = KNOWN_ACTIONS_BY_SURFACE[surface.surface_id]
    observed = surface.command_ids
    stale = tuple(command_id for command_id in expected if command_id not in observed)
    unclassified = tuple(
        command_id for command_id in observed if command_id not in expected
    )
    assert stale == (), {
        "surface_id": surface.surface_id,
        "stale_manifest_actions": stale,
    }
    assert unclassified == (), {
        "surface_id": surface.surface_id,
        "unclassified_live_actions": unclassified,
    }
    assert observed == expected


def _assert_maximum_variant_inventory_is_live(surface) -> None:
    if surface.surface_id not in {"manufacture", "drawing"}:
        return
    observed_union = set(KNOWN_ACTIONS_BY_SURFACE[surface.surface_id]) | set(
        surface.command_ids
    )
    expected = set(ALLOWED_ACTION_IDS_BY_SURFACE[surface.surface_id])
    if surface.surface_id == "manufacture" and "CAM_Camotics" not in observed_union:
        expected.remove("CAM_Camotics")
    assert observed_union == expected, {
        "surface_id": surface.surface_id,
        "stale_manifest_actions": tuple(sorted(expected - observed_union)),
        "unclassified_live_actions": tuple(sorted(observed_union - expected)),
    }


def _production_provider_surface(surface, registry):
    provider = resolve_native_provider_surface(
        surface,
        registry,
    )
    plans = classify_native_surface(surface)
    assert provider.available is True, {
        **provider.summary(),
        "missing_definitions": provider.missing_definition_names,
        "missing_implementations": provider.missing_implementation_names,
        "incomplete_definitions": provider.incomplete_definition_names,
        "requirements": [
            (
                plan.command_id,
                plan.capability_family,
                plan.operation_variant,
                plan.transaction_behavior,
                plan.background_required,
            )
            for plan in plans
            if plan.capability_family in provider.incomplete_definition_names
        ],
        "variants": {
            name: [
                (
                    variant.operation,
                    sorted(variant.action_ids),
                    variant.transaction_behavior,
                    variant.background_required,
                )
                for variant in registry.definition(name).variants
            ]
            for name in provider.incomplete_definition_names
        },
    }
    assert tuple(schema["name"] for schema in provider.schemas) == provider.tool_names
    serialized = json.dumps(
        provider.schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "unknown" not in serialized.casefold()

    workbench = (
        "SketcherWorkbench"
        if surface.surface_id in {"sketch.setup", "sketch.edit"}
        else next(
            name
            for name, surface_id in _PERMANENT_SURFACES.items()
            if surface_id == surface.surface_id
        )
    )
    modeling_surface = modeling_surface_from_native_provider(
        workbench,
        provider,
    )
    schemas = list(provider.schemas)
    frozen = _turn_start_tool_surface(
        workbench,
        schemas,
        resolution=modeling_surface,
    )
    dynamic_tools, dynamic_names = ProviderModule._codex_dynamic_tool_surface(
        {
            "provider_tool_schemas": schemas,
            "provider_tool_surface": frozen,
            "modeling_surface": {
                key: frozen[key]
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
    assert dynamic_tools
    assert tuple(dynamic_names.values()) == provider.tool_names
    return provider


def _canonical_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _schema_operations(schema) -> tuple[str, ...]:
    parameters = schema["parameters"]
    branches = parameters.get("oneOf")
    if branches is not None:
        assert len(branches) == 1
        operation = branches[0]["properties"]["operation"]
    else:
        operation = parameters["properties"]["operation"]
    if "const" in operation:
        return (str(operation["const"]),)
    values = tuple(str(value) for value in operation["enum"])
    assert values and len(values) == len(set(values))
    return values


def _provider_contract(surface, provider) -> dict[str, object]:
    schemas = tuple(provider.schemas)
    schema_payload = _canonical_json_bytes(schemas)
    routes = [
        (plan.command_id, plan.capability_family, plan.operation_variant)
        for plan in classify_native_surface(surface)
        if plan.operation_variant is not None
        and not plan.classification.parent_only
        and not plan.classification.human_only
    ]
    routes.extend(
        (plan.action_id, plan.capability_family, plan.operation_variant)
        for plan in provider_context_actions_for_surface(surface.surface_id)
    )
    assert all(operation is not None for _action, _family, operation in routes)
    assert len(routes) == len({action for action, _family, _operation in routes})
    return {
        "surface_id": surface.surface_id,
        "tool_count": len(schemas),
        "schema_bytes": len(schema_payload),
        "route_count": len(routes),
        "tools": [
            {
                "name": schema["name"],
                "operations": list(_schema_operations(schema)),
                "schema_bytes": len(payload),
                "schema": schema,
            }
            for schema in schemas
            for payload in (_canonical_json_bytes(schema),)
        ],
    }


def _assert_default_provider_contracts(contracts) -> None:
    assert contracts
    assert all(contract["tool_count"] > 0 for contract in contracts.values())
    assert all(contract["route_count"] > 0 for contract in contracts.values())
    assert all(contract["schema_bytes"] > 0 for contract in contracts.values())

    tools = {
        surface_id: {
            tool["name"]: tool
            for tool in contract["tools"]
        }
        for surface_id, contract in contracts.items()
    }
    surface_ids = set(contracts)
    for common_name in ("state.read", "document.undo"):
        values = {
            (
                _canonical_json_bytes(tools[surface_id][common_name]["schema"]),
                tuple(tools[surface_id][common_name]["operations"]),
            )
            for surface_id in surface_ids
        }
        assert len(values) == 1, (common_name, values)

    base_view_operations = {
        tuple(tools[surface_id]["view.control"]["operations"])
        for surface_id in surface_ids - {"model"}
    }
    assert len(base_view_operations) == 1
    assert set(tools["model"]["view.control"]["operations"]) == {
        *next(iter(base_view_operations)),
        "set_object_visibility",
    }

    workspace_values = {
        (
            _canonical_json_bytes(tools[surface_id]["workspace.switch"]["schema"]),
            tuple(tools[surface_id]["workspace.switch"]["operations"]),
        )
        for surface_id in surface_ids - {"sketch.edit"}
    }
    assert len(workspace_values) == 1
    assert "workspace.switch" not in tools["sketch.edit"]

    save_surfaces = surface_ids - {"sketch.edit"}
    assert "document.save" not in tools["sketch.edit"]
    save_values = {
        _canonical_json_bytes(tools[surface_id]["document.save"]["schema"])
        for surface_id in save_surfaces
    }
    assert len(save_values) == 1

    base_inspect = {
        tuple(tools[surface_id]["inspect.query"]["operations"])
        for surface_id in surface_ids - {"drawing"}
    }
    assert len(base_inspect) == 1
    base_operations = next(iter(base_inspect))
    assert tuple(tools["drawing"]["inspect.query"]["operations"]) == (
        *base_operations[:-1],
        "drawing_projected_geometry",
        base_operations[-1],
    )


def _assert_model_provider_scope(provider):
    assert {
        name for name in provider.tool_names if name.startswith("sketch.")
    } == {"sketch.open", "sketch.validate"}
    assert not {
        "sketch.control",
        "sketch.geometry",
        "sketch.constraint",
        "sketch.modify",
        "sketch.bspline",
        "sketch.presentation",
        "sketch.inspect",
        "sketch.setup",
    } & set(provider.tool_names)


def _assert_model_composites(surface):
    actual = {
        action.command_id: tuple(child.command_id for child in action.children)
        for group in surface.groups
        for action in group.actions
        if action.kind == "composite"
    }
    assert actual == _EXPECTED_MODEL_COMPOSITES
    plans = {plan.command_id: plan for plan in classify_native_surface(surface)}
    for parent_id, child_ids in _EXPECTED_MODEL_COMPOSITES.items():
        parent = plans[parent_id]
        assert parent.classification.parent_only is True
        assert parent.operation_variant is None
        for child_id in child_ids:
            assert plans[child_id].parent_command_id == parent_id
    return actual


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    main_window = Gui.getMainWindow()
    document = None
    other_document = None
    preference_snapshots = {}
    exit_code = 1
    try:
        variant = str(os.environ.get("VIBECAD_RIBBON_VARIANT") or "").strip()
        expected_surface_preferences = _configure_variant(
            variant,
            preference_snapshots,
        )

        main_window.resize(1440, 900)
        main_window.show()
        _process_events()

        tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert tabs is not None
        assert controller is not None
        assert tabs.count() == len(_PERMANENT_SURFACES)

        counts = {}
        provider_counts = {}
        provider_contracts = {}
        manifests = {}
        unique_commands = set()
        registry = build_native_capability_registry()
        preceding_revision = 0
        for index in range(tabs.count()):
            workbench = str(tabs.tabData(index))
            assert workbench in _PERMANENT_SURFACES
            tabs.setCurrentIndex(index)
            _process_events()
            assert Gui.activeWorkbench().name() == workbench
            surface = _assert_surface(
                main_window,
                controller,
                _PERMANENT_SURFACES[workbench],
            )
            assert surface.revision > preceding_revision
            preceding_revision = surface.revision
            counts[surface.surface_id] = len(surface.command_ids)
            provider = _production_provider_surface(surface, registry)
            provider_counts[surface.surface_id] = len(provider.tool_names)
            provider_contracts[surface.surface_id] = _provider_contract(
                surface,
                provider,
            )
            if not variant:
                _assert_default_inventory_is_live(surface)
            elif variant == "maximum":
                _assert_maximum_variant_inventory_is_live(surface)
            expected_preferences = expected_surface_preferences.get(
                surface.surface_id
            )
            if expected_preferences is not None:
                assert surface.to_environment()["preferences"] == expected_preferences
            manifests[surface.surface_id] = surface.to_manifest()
            unique_commands.update(surface.command_ids)

        drawing_index = next(
            index
            for index in range(tabs.count())
            if str(tabs.tabData(index)) == "TechDrawWorkbench"
        )
        tabs.setCurrentIndex(drawing_index)
        _process_events()
        drawing_before_preference = _assert_surface(
            main_window,
            controller,
            "drawing",
        )
        drawing_preferences = App.ParamGet(_DRAWING_PREFERENCE_PATH)
        separated_before = drawing_preferences.GetBool(
            "SeparatedDimensioningTools",
            False,
        )
        _set_test_bool(
            preference_snapshots,
            _DRAWING_PREFERENCE_PATH,
            "SeparatedDimensioningTools",
            not separated_before,
            default=False,
        )
        _process_events()
        drawing_after_preference = _assert_surface(
            main_window,
            controller,
            "drawing",
        )
        assert (
            drawing_after_preference.revision
            > drawing_before_preference.revision
        )
        assert (
            drawing_after_preference.manifest_sha256
            != drawing_before_preference.manifest_sha256
        )
        assert (
            drawing_after_preference.environment_sha256
            != drawing_before_preference.environment_sha256
        )
        assert drawing_after_preference.to_environment()["preferences"][
            "techdraw.separated_dimensioning_tools"
        ] is (not separated_before)
        _set_test_bool(
            preference_snapshots,
            _DRAWING_PREFERENCE_PATH,
            "SeparatedDimensioningTools",
            separated_before,
            default=False,
        )
        _process_events()
        drawing_restored = _assert_surface(main_window, controller, "drawing")
        assert drawing_restored.revision > drawing_after_preference.revision
        assert (
            drawing_restored.environment_sha256
            == drawing_before_preference.environment_sha256
        )

        Gui.activateWorkbench("SketcherWorkbench")
        _process_events()
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        setup = _assert_surface(main_window, controller, "sketch.setup")
        assert setup.revision > preceding_revision
        counts[setup.surface_id] = len(setup.command_ids)
        setup_provider = _production_provider_surface(setup, registry)
        provider_counts[setup.surface_id] = len(setup_provider.tool_names)
        provider_contracts[setup.surface_id] = _provider_contract(
            setup,
            setup_provider,
        )
        if not variant:
            _assert_default_inventory_is_live(setup)
        manifests[setup.surface_id] = setup.to_manifest()
        unique_commands.update(setup.command_ids)

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        model_before_edit = _assert_surface(main_window, controller, "model")
        model_composites_before_edit = _assert_model_composites(model_before_edit)
        model_provider_before_edit = _production_provider_surface(
            model_before_edit,
            registry,
        )
        _assert_model_provider_scope(model_provider_before_edit)

        document = App.newDocument("VibeCADNativeRibbonSurface")
        sketch = document.addObject("Sketcher::SketchObject", "SurfaceContractSketch")
        document.recompute()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert all(
            not tabs.isTabEnabled(index)
            for index in range(tabs.count())
            if index != tabs.currentIndex()
        )
        edit = _assert_surface(main_window, controller, "sketch.edit")
        assert edit.revision > model_before_edit.revision
        assert set(model_before_edit.command_ids) & set(edit.command_ids) == {
            "Std_ViewFitAll",
            "Std_ViewIsometric",
            "VibeCAD_ToggleGrid",
            "VibeCAD_SectionView",
        }
        edit_provider = _production_provider_surface(edit, registry)
        counts[edit.surface_id] = len(edit.command_ids)
        provider_counts[edit.surface_id] = len(edit_provider.tool_names)
        provider_contracts[edit.surface_id] = _provider_contract(
            edit,
            edit_provider,
        )
        if not variant:
            _assert_default_inventory_is_live(edit)
        manifests[edit.surface_id] = edit.to_manifest()
        unique_commands.update(edit.command_ids)

        other_document = App.newDocument("VibeCADOtherEditDocument")
        other_sketch = other_document.addObject(
            "Sketcher::SketchObject",
            "OtherSurfaceContractSketch",
        )
        other_document.recompute()
        App.setActiveDocument(other_document.Name)
        assert Gui.activeDocument().setEdit(other_sketch.Name)
        App.setActiveDocument(document.Name)
        _process_events()
        _assert_surface(main_window, controller, "sketch.edit")
        Gui.getDocument(other_document.Name).resetEdit()
        _process_events()
        _assert_surface(main_window, controller, "sketch.edit")
        App.closeDocument(other_document.Name)
        other_document = None

        Gui.activeDocument().resetEdit()
        _process_events()
        returned = _assert_surface(main_window, controller, "model")
        assert returned.revision > edit.revision
        assert _assert_model_composites(returned) == model_composites_before_edit
        returned_provider = _production_provider_surface(returned, registry)
        _assert_model_provider_scope(returned_provider)
        assert returned_provider.tool_names == model_provider_before_edit.tool_names
        assert returned_provider.schemas == model_provider_before_edit.schemas

        if not variant:
            _assert_default_provider_contracts(provider_contracts)

        manifest_output = str(
            os.environ.get("VIBECAD_RIBBON_MANIFEST_OUTPUT") or ""
        ).strip()
        if manifest_output:
            Path(manifest_output).write_text(
                json.dumps(manifests, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        print(
            "VIBECAD_NATIVE_RIBBON_SURFACE_GUI_OK "
            f"counts={counts} provider_tools={provider_counts} "
            f"unique={len(unique_commands)}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        _restore_test_bools(preference_snapshots)
        if Gui.activeDocument() and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if other_document is not None and other_document.Name in App.listDocuments():
            App.closeDocument(other_document.Name)
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
