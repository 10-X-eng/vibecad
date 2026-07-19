# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native GUI integration gate for production Material VibeScript."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402,F401  # Initialize native view providers.

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    MATERIAL_OWNERSHIP_SCHEMA,
    PROP_APPEARANCE_BASELINE,
    PROP_MATERIAL_BASELINE,
    _material_card_state,
    _material_state_equal,
    _set_physical_material_preserving_view,
    _shape_appearance_payload,
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    _material_target_snapshots,
    accept_candidate,
    complete_inspection,
    execute_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    restore_prepared_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    PROP_PROGRAM_REVISION,
    complete_domain_context,
    domain_context_snapshot,
    get_domain_adapter,
    get_vibescript_pack,
)
from vibescript_material_api import MaterialDomainAPI  # noqa: E402
from vibescript_material_worker import (  # noqa: E402
    material_card_appearance,
    material_card_record,
)


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        import FreeCAD as App

        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "MaterialWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "material-native-fixture-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.root), "project_id": "material-native-fixture"}


def _reference_schema() -> dict:
    return {
        "type": "object",
        "x-vibecad-reference": True,
        "properties": {
            "document_uid": {"type": "string", "minLength": 1},
            "object_name": {"type": "string", "minLength": 1},
        },
        "required": ["document_uid", "object_name"],
        "additionalProperties": False,
    }


def _appearance(target) -> dict:
    view = target.ViewObject
    return {
        "shape_appearance": _shape_appearance_payload(view.ShapeAppearance),
        "line_color": [float(value) for value in tuple(view.LineColor)],
        "point_color": [float(value) for value in tuple(view.PointColor)],
        "line_width": float(view.LineWidth),
        "point_size": float(view.PointSize),
        "display_mode": str(view.DisplayMode),
        "visibility": bool(view.Visibility),
        "selectable": bool(view.Selectable),
    }


def _assert_card_appearance(target, card, *, shape_color=None, transparency=None) -> None:
    expected = material_card_appearance(card)
    if shape_color is not None:
        diffuse = list(expected.get("diffuse_color") or [0.0, 0.0, 0.0, 1.0])
        expected["diffuse_color"] = [*shape_color, diffuse[3]]
    if transparency is not None:
        expected["transparency"] = float(transparency) / 100.0
    native_names = {
        "ambient_color": "AmbientColor",
        "diffuse_color": "DiffuseColor",
        "specular_color": "SpecularColor",
        "emissive_color": "EmissiveColor",
        "shininess": "Shininess",
        "transparency": "Transparency",
    }
    materials = list(target.ViewObject.ShapeAppearance)
    assert materials
    for material in materials:
        for field, value in expected.items():
            observed = getattr(material, native_names[field])
            if field.endswith("_color"):
                assert len(observed) == len(value)
                assert all(
                    abs(float(left) - float(right)) <= (1.0 / 255.0) + 2.0e-6
                    for left, right in zip(observed, value)
                )
            else:
                assert abs(float(observed) - float(value)) <= 2.0e-6


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict,
) -> dict:
    import FreeCAD as App

    pack = get_vibescript_pack("MaterialWorkbench")
    assert pack is not None and pack.production_ready
    return {
        "tool_name": f"vibescript.material.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "material-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "material-native-fixture-revision",
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in document.Objects
        ],
        "material_targets": _material_target_snapshots(document),
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "MaterialWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict):
    prepared = prepare_candidate(captured)
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    return prepared, execution, validated


def _restore_accepted_appearance(carrier, target) -> None:
    ownership = json.loads(carrier.VibeCADMaterialOwnership)
    controlled = list(ownership["controlled_properties"])
    if "ShapeAppearance" in controlled:
        target.ViewObject.ShapeAppearance = list(carrier.VibeCADAppearanceAccepted)
    for name, value in ownership["accepted_simple"].items():
        if name in {"LineColor", "PointColor"}:
            value = tuple(value)
        setattr(target.ViewObject, name, value)


def _assert_exact_api() -> None:
    import inspect

    api = MaterialDomainAPI(
        ("material", "assign", "appearance"),
        ("material_assignment", "appearance"),
    )
    assert api.exported_names == ("material", "assign", "appearance")
    assert str(inspect.signature(api.material)) == (
        "(material_uuid: 'str', *, require_physical_properties: 'Sequence[str]' = (), "
        "require_appearance_properties: 'Sequence[str]' = (), label: 'str' = '') -> 'DomainValue'"
    )
    assert str(inspect.signature(api.assign)) == (
        "(target: 'Mapping[str, str]', card: 'DomainValue', *, label: 'str' = '') -> 'DomainValue'"
    )
    assert "card: 'DomainValue | None' = None" in str(inspect.signature(api.appearance))
    assert "shape_color" in str(inspect.signature(api.appearance))
    try:
        api.appearance({"document_uid": "d", "object_name": "Target"})
    except ValueError as exc:
        assert "at least one display change" in str(exc)
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "appearance"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("An empty appearance must be rejected.")
    try:
        api.material("not-a-uuid")
    except ValueError as exc:
        assert "material_uuid" in str(exc)
    else:
        raise AssertionError("An invalid material UUID must be rejected.")
    card = api.material("0051bddf-6f62-4406-b8c9-569322880564")
    card_only = api.appearance(
        {"document_uid": "d", "object_name": "Target"},
        card,
    )
    assert card_only.to_payload()["arguments"][1]["output_type"] == "material_card"
    try:
        api.appearance(
            {"document_uid": "d", "object_name": "Target"},
            object(),
        )
    except ValueError as exc:
        assert "api.appearance" in str(exc) and "api.material" in str(exc)
    else:
        raise AssertionError("Appearance cards must be immutable api.material values.")
    try:
        card.properties["label"] = "mutated"
    except TypeError:
        pass
    else:
        raise AssertionError("Material graph values must be immutable.")
    adapter = get_domain_adapter("material")
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()
    assert description["api_contract"] == "vibecad-vibescript-material-api-v1"
    assert [item["name"] for item in description["runtime_exports"]] == [
        "material",
        "assign",
        "appearance",
    ]
    assert "physical assignment preserves" in description["publication_contract"][
        "separation"
    ]


def main() -> int:
    import Materials
    import Part

    _assert_exact_api()
    root = Path(tempfile.mkdtemp(prefix="vibecad-material-native-"))
    document = App.newDocument("VibeScriptMaterialNative")
    service = _Service(root)
    try:
        target = document.addObject("Part::Feature", "Chassis")
        target.Shape = Part.makeBox(20, 10, 4)
        physical_only_target = document.addObject("Part::Feature", "Axle")
        physical_only_target.Shape = Part.makeCylinder(2, 30)
        appearance_only_target = document.addObject("Part::Feature", "FinishCoupon")
        appearance_only_target.Shape = Part.makeBox(5, 5, 1)
        document.recompute()
        target.ViewObject.ShapeColor = (0.62, 0.54, 0.31)
        target.ViewObject.Transparency = 7
        target.ViewObject.LineColor = (0.11, 0.12, 0.13)
        target.ViewObject.PointColor = (0.21, 0.22, 0.23)
        target.ViewObject.LineWidth = 3.0
        target.ViewObject.PointSize = 4.0
        target.ViewObject.DisplayMode = "Flat Lines"
        target.ViewObject.Selectable = True
        physical_only_target.ViewObject.ShapeColor = (0.15, 0.25, 0.35)
        physical_only_target.ViewObject.Transparency = 13
        physical_only_target.ViewObject.LineWidth = 5.0
        appearance_only_target.ViewObject.ShapeColor = (0.71, 0.61, 0.51)
        appearance_only_target.ViewObject.Transparency = 17
        appearance_only_target.ViewObject.PointSize = 6.0

        original_target_material = target.ShapeMaterial
        original_physical_only_material = physical_only_target.ShapeMaterial
        original_appearance_only_material = appearance_only_target.ShapeMaterial
        original_target_appearance = _appearance(target)
        original_physical_only_appearance = _appearance(physical_only_target)
        original_appearance_only_appearance = _appearance(appearance_only_target)
        standard_appearance = {
            "AmbientColor",
            "DiffuseColor",
            "SpecularColor",
            "EmissiveColor",
            "Shininess",
            "Transparency",
        }
        cards = [
            card
            for card in Materials.MaterialManager().Materials.values()
            if str(card.UUID) != str(original_target_material.UUID)
            and card.hasPhysicalProperty("Density")
            and all(card.hasAppearanceProperty(name) for name in standard_appearance)
        ]
        cards.sort(key=lambda card: (str(card.Name), str(card.UUID)))
        assert len(cards) >= 2
        first_card, second_card = cards[:2]
        first_record = material_card_record(
            first_card,
            required_physical_properties=("Density",),
        )

        target_reference = {
            "document_uid": str(document.Uid),
            "object_name": str(target.Name),
        }
        physical_only_reference = {
            "document_uid": str(document.Uid),
            "object_name": str(physical_only_target.Name),
        }
        appearance_only_reference = {
            "document_uid": str(document.Uid),
            "object_name": str(appearance_only_target.Name),
        }
        source = (
            "card = api.material(inputs['material_uuid'], "
            "require_physical_properties=['Density'], label='Structural card')\n"
            "physical = api.assign(inputs['target'], card, label='Chassis physical')\n"
            "axle = api.assign(inputs['physical_only_target'], card, label='Axle physical')\n"
            "display = api.appearance(inputs['target'], card, "
            "shape_color=[0.18,0.24,0.32], "
            "line_color=[0.8,0.82,0.85], point_color=[0.9,0.1,0.2], "
            "transparency=4, line_width=2.5, point_size=3.5, "
            "display_mode='Flat Lines', visibility=True, selectable=False, "
            "label='Chassis display')\n"
            "card_display = api.appearance(inputs['appearance_only_target'], card, "
            "label='Card display')\n"
            "result = {'Physical': physical, 'Axle': axle, 'Display': display, "
            "'CardDisplay': card_display}\n"
        )
        create_arguments = {
            "program_name": "Vehicle Materials",
            "source": source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "material_uuid": {"type": "string"},
                    "target": _reference_schema(),
                    "physical_only_target": _reference_schema(),
                    "appearance_only_target": _reference_schema(),
                },
                "required": [
                    "material_uuid",
                    "target",
                    "physical_only_target",
                    "appearance_only_target",
                ],
                "additionalProperties": False,
            },
            "inputs": {
                "material_uuid": str(first_card.UUID),
                "target": target_reference,
                "physical_only_target": physical_only_reference,
                "appearance_only_target": appearance_only_reference,
            },
            "expected_outputs": [
                {"name": "Physical", "type": "material_assignment"},
                {"name": "Axle", "type": "material_assignment"},
                {"name": "Display", "type": "appearance"},
                {"name": "CardDisplay", "type": "appearance"},
            ],
        }
        captured = _captured(
            root,
            document,
            operation="create_program",
            arguments=create_arguments,
        )
        prepared, execution, validated = _prepare_execute_validate(captured)
        assert execution["material_validation"]["assignment_count"] == 2
        assert execution["material_validation"]["appearance_count"] == 2
        assert validated["outputs"][0]["material_validation"]["material_card"] == first_record
        for index in (2, 3):
            appearance_validation = validated["outputs"][index]["material_validation"]
            assert appearance_validation["material_card"] == first_record
            assert appearance_validation["card_appearance"] == material_card_appearance(
                first_card
            )
            assert appearance_validation["resolved"]["shape_material"] is not None
        retain_candidate(prepared, status="validated")

        original_manager = Materials.MaterialManager

        def forbidden_catalog_access():
            raise AssertionError("The document-thread publisher opened the material catalog.")

        Materials.MaterialManager = forbidden_catalog_access
        try:
            publication = publish_candidate(service, prepared, validated)
        finally:
            Materials.MaterialManager = original_manager
        accepted = accept_candidate(prepared, publication)
        carrier_names = {
            name: value["object_name"] for name, value in accepted["live_outputs"].items()
        }
        physical_carrier = document.getObject(carrier_names["Physical"])
        axle_carrier = document.getObject(carrier_names["Axle"])
        appearance_carrier = document.getObject(carrier_names["Display"])
        card_appearance_carrier = document.getObject(carrier_names["CardDisplay"])
        for carrier in (
            physical_carrier,
            axle_carrier,
            appearance_carrier,
            card_appearance_carrier,
        ):
            assert carrier.TypeId == "App::FeaturePython"
            assert carrier.VibeCADMaterialTarget in {
                target,
                physical_only_target,
                appearance_only_target,
            }
            ownership = json.loads(carrier.VibeCADMaterialOwnership)
            assert ownership["schema"] == MATERIAL_OWNERSHIP_SCHEMA
        assert str(target.ShapeMaterial.UUID) == str(first_card.UUID)
        assert str(physical_only_target.ShapeMaterial.UUID) == str(first_card.UUID)
        assert _material_state_equal(
            _appearance(physical_only_target), original_physical_only_appearance
        ), "Physical assignment changed display-only styling."
        assert tuple(round(value, 5) for value in target.ViewObject.ShapeColor[:3]) == (
            0.18,
            0.24,
            0.32,
        )
        assert target.ViewObject.Transparency == 4
        assert target.ViewObject.Selectable is False
        _assert_card_appearance(
            target,
            first_card,
            shape_color=[0.18, 0.24, 0.32],
            transparency=4,
        )
        assert _material_card_state(
            appearance_only_target.ShapeMaterial
        ) == _material_card_state(original_appearance_only_material)
        _assert_card_appearance(appearance_only_target, first_card)
        assert _material_card_state(physical_carrier.VibeCADMaterialBaseline) == _material_card_state(
            original_target_material
        )
        assert _material_card_state(axle_carrier.VibeCADMaterialBaseline) == _material_card_state(
            original_physical_only_material
        )
        assert list(appearance_carrier.VibeCADAppearanceBaseline)
        assert list(appearance_carrier.VibeCADAppearanceAccepted)
        assert card_appearance_carrier.VibeCADMaterialTarget is appearance_only_target
        assert list(card_appearance_carrier.VibeCADAppearanceBaseline)
        assert list(card_appearance_carrier.VibeCADAppearanceAccepted)

        inspection = complete_inspection(
            {
                **captured,
                "program_id": prepared["program_id"],
                "live_programs": [],
            }
        )
        assert inspection["ok"] is True
        assert inspection["program"]["accepted_revision"] == prepared["revision"]
        inspected_card = inspection["program"]["live_outputs"]["Physical"][
            "validation"
        ]["material_card"]
        assert inspected_card["uuid"] == str(first_card.UUID)
        assert inspected_card["required_physical_properties"] == first_record[
            "required_physical_properties"
        ]
        assert inspected_card["required_physical_properties"][0]["display"]
        assert inspected_card["required_physical_properties"][0]["value_sha256"]
        context = complete_domain_context(domain_context_snapshot(service, "material"))
        catalog = context["material_catalog"]
        assert catalog["available"] is True
        catalog_card = next(
            card for card in catalog["cards"] if card["uuid"] == str(first_card.UUID)
        )
        assert catalog_card["selection_physical_values"]["Density"]
        assert set(catalog_card["selection_physical_values"]) <= set(
            catalog["selection_physical_property_priority"]
        )
        assert set(catalog_card["selection_appearance_values"]) <= set(
            catalog["selection_appearance_property_priority"]
        )
        assert isinstance(catalog_card["selection_physical_values_truncated"], list)
        assert isinstance(catalog_card["selection_appearance_values_truncated"], list)
        assert "inspect accepted output validation" in catalog["selection_contract"]
        target_context = next(
            item for item in context["material_targets"]["targets"] if item["name"] == target.Name
        )
        assert target_context["physical_assignment_supported"] is True
        assert "ShapeAppearance" in target_context["appearance_supported_properties"]

        update_captured = _captured(
            root,
            document,
            operation="set_inputs",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": prepared["revision"],
                "patch": {"material_uuid": str(second_card.UUID)},
            },
        )
        update_prepared, _update_execution, update_validated = _prepare_execute_validate(
            update_captured
        )
        retain_candidate(update_prepared, status="validated")
        updated_publication = publish_candidate(service, update_prepared, update_validated)
        updated = accept_candidate(update_prepared, updated_publication)
        assert {
            name: value["object_name"] for name, value in updated["live_outputs"].items()
        } == carrier_names
        assert str(target.ShapeMaterial.UUID) == str(second_card.UUID)
        assert str(physical_only_target.ShapeMaterial.UUID) == str(second_card.UUID)
        assert _material_state_equal(
            _appearance(physical_only_target), original_physical_only_appearance
        )
        _assert_card_appearance(
            target,
            second_card,
            shape_color=[0.18, 0.24, 0.32],
            transparency=4,
        )
        _assert_card_appearance(appearance_only_target, second_card)
        assert _material_card_state(
            appearance_only_target.ShapeMaterial
        ) == _material_card_state(original_appearance_only_material)

        next_captured = _captured(
            root,
            document,
            operation="edit_source",
            arguments={
                "program_id": prepared["program_id"],
                "expected_revision": update_prepared["revision"],
                "replacements": [{"old": "Chassis display", "new": "Updated display"}],
            },
        )
        next_prepared, _next_execution, next_validated = _prepare_execute_validate(next_captured)

        human_card = first_card
        _set_physical_material_preserving_view(target, human_card)
        state_after_human_material = _material_card_state(target.ShapeMaterial)
        try:
            publish_candidate(service, next_prepared, next_validated)
        except RuntimeError as exc:
            assert "ShapeMaterial changed outside" in str(exc)
        else:
            raise AssertionError("Human physical edits must block regeneration.")
        assert _material_card_state(target.ShapeMaterial) == state_after_human_material
        _set_physical_material_preserving_view(target, physical_carrier.VibeCADMaterialAccepted)

        old_line_width = float(target.ViewObject.LineWidth)
        target.ViewObject.LineWidth = old_line_width + 1.0
        try:
            publish_candidate(service, next_prepared, next_validated)
        except RuntimeError as exc:
            assert "controlled display properties" in str(exc)
        else:
            raise AssertionError("Human controlled appearance edits must block regeneration.")
        _restore_accepted_appearance(appearance_carrier, target)

        before_fault_material = _material_card_state(target.ShapeMaterial)
        before_fault_appearance = _appearance(target)
        before_fault_card_appearance = _appearance(appearance_only_target)
        before_fault_revision = str(physical_carrier.VibeCADVibeScriptRevision)
        original_configure = publication_module._configure_material_carrier

        def fail_after_complete_configuration(*args, **kwargs):
            result = original_configure(*args, **kwargs)
            if str(args[1].get("name") or "") == "Display":
                raise RuntimeError("injected post-appearance publication failure")
            return result

        publication_module._configure_material_carrier = fail_after_complete_configuration
        try:
            try:
                publish_candidate(service, next_prepared, next_validated)
            except RuntimeError as exc:
                assert "injected post-appearance" in str(exc)
            else:
                raise AssertionError("Injected Material publication failure did not fire.")
        finally:
            publication_module._configure_material_carrier = original_configure
        assert _material_card_state(target.ShapeMaterial) == before_fault_material
        assert _material_state_equal(_appearance(target), before_fault_appearance)
        assert _material_state_equal(
            _appearance(appearance_only_target), before_fault_card_appearance
        )
        assert str(physical_carrier.VibeCADVibeScriptRevision) == before_fault_revision

        retain_candidate(next_prepared, status="validated")
        final_publication = publish_candidate(service, next_prepared, next_validated)
        final_accepted = accept_candidate(next_prepared, final_publication)
        assert final_accepted["accepted_revision"] == next_prepared["revision"]
        assert physical_carrier is document.getObject(carrier_names["Physical"])
        assert appearance_carrier is document.getObject(carrier_names["Display"])

        conflict_source = (
            "card = api.material(inputs['material_uuid'])\n"
            "result = {'Conflict': api.assign(inputs['target'], card)}\n"
        )
        conflict_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Conflicting Material Owner",
                "source": conflict_source,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "material_uuid": {"type": "string"},
                        "target": _reference_schema(),
                    },
                    "required": ["material_uuid", "target"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "material_uuid": str(first_card.UUID),
                    "target": target_reference,
                },
                "expected_outputs": [
                    {"name": "Conflict", "type": "material_assignment"}
                ],
            },
        )
        conflict_prepared, _conflict_execution, conflict_validated = _prepare_execute_validate(
            conflict_capture
        )
        try:
            publish_candidate(service, conflict_prepared, conflict_validated)
        except RuntimeError as exc:
            assert "already owned" in str(exc)
        else:
            raise AssertionError("Foreign physical ownership conflicts must be rejected.")

        hardcoded_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Hardcoded Material Target",
                "source": (
                    "card = api.material(inputs['material_uuid'])\n"
                    f"result = {{'Hardcoded': api.assign({target_reference!r}, card)}}\n"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"material_uuid": {"type": "string"}},
                    "required": ["material_uuid"],
                    "additionalProperties": False,
                },
                "inputs": {"material_uuid": str(first_card.UUID)},
                "expected_outputs": [
                    {"name": "Hardcoded", "type": "material_assignment"}
                ],
            },
        )
        hardcoded_prepared = prepare_candidate(hardcoded_capture)
        hardcoded_execution = execute_candidate(hardcoded_prepared, cancellation_check=None)
        assert hardcoded_execution["ok"] is True
        try:
            validate_candidate(hardcoded_prepared, hardcoded_execution)
        except ValueError as exc:
            assert "authenticated stable reference" in str(exc)
        else:
            raise AssertionError("Hard-coded Material target references must be rejected.")

        malformed = copy.deepcopy(_next_execution)
        malformed["material_validation"]["assignment_count"] = 999
        try:
            validate_candidate(next_prepared, malformed)
        except ValueError as exc:
            assert "assignment_count" in str(exc)
        else:
            raise AssertionError("Malformed Material worker summaries must be rejected.")

        malformed_appearance = copy.deepcopy(_next_execution)
        malformed_appearance["outputs"][2]["material_validation"]["card_appearance"][
            "shininess"
        ] = 0.123456
        try:
            validate_candidate(next_prepared, malformed_appearance)
        except ValueError as exc:
            assert "resolved-state validation" in str(exc)
        else:
            raise AssertionError("Malformed card-derived appearance must be rejected.")

        missing_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Missing Material Requirement",
                "source": (
                    "card = api.material(inputs['material_uuid'], "
                    "require_physical_properties=['DefinitelyMissing'])\n"
                    "result = {'Missing': api.assign(inputs['target'], card)}\n"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "material_uuid": {"type": "string"},
                        "target": _reference_schema(),
                    },
                    "required": ["material_uuid", "target"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "material_uuid": str(first_card.UUID),
                    "target": physical_only_reference,
                },
                "expected_outputs": [
                    {"name": "Missing", "type": "material_assignment"}
                ],
            },
        )
        missing_prepared = prepare_candidate(missing_capture)
        missing_execution = execute_candidate(missing_prepared, cancellation_check=None)
        assert missing_execution["ok"] is False
        assert missing_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        assert (
            missing_execution["observed"]["details"]["stage"]
            == "catalog_requirements"
        )
        assert missing_execution["domain_failure_stage"] == "catalog_requirements"
        assert missing_execution["retry"]["required_changes"] == [
            missing_execution["observed"]["details"]["correction"]
        ]
        assert "Choose a catalog UUID" in missing_execution["retry"][
            "required_changes"
        ][0]
        assert "DefinitelyMissing" in missing_execution["error"]

        no_appearance_capture = _captured(
            root,
            document,
            operation="create_program",
            arguments={
                "program_name": "Physical-only Card Appearance",
                "source": (
                    "card = api.material(inputs['material_uuid'])\n"
                    "result = {'Display': api.appearance(inputs['target'], card)}\n"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "material_uuid": {"type": "string"},
                        "target": _reference_schema(),
                    },
                    "required": ["material_uuid", "target"],
                    "additionalProperties": False,
                },
                "inputs": {
                    "material_uuid": "94370b96-c97e-4a3f-83b2-11d7461f7da7",
                    "target": physical_only_reference,
                },
                "expected_outputs": [{"name": "Display", "type": "appearance"}],
            },
        )
        no_appearance_prepared = prepare_candidate(no_appearance_capture)
        no_appearance_execution = execute_candidate(
            no_appearance_prepared,
            cancellation_check=None,
        )
        assert no_appearance_execution["ok"] is False
        assert no_appearance_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
        assert no_appearance_execution["observed"]["details"]["stage"] == (
            "catalog_appearance"
        )
        assert no_appearance_execution["retry"]["required_changes"] == [
            no_appearance_execution["observed"]["details"]["correction"]
        ]

        save_path = root / "material-production.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        reopened = App.openDocument(str(save_path))
        assert reopened is not None
        target = reopened.getObject("Chassis")
        physical_only_target = reopened.getObject("Axle")
        appearance_only_target = reopened.getObject("FinishCoupon")
        physical_carrier = reopened.getObject(carrier_names["Physical"])
        appearance_carrier = reopened.getObject(carrier_names["Display"])
        card_appearance_carrier = reopened.getObject(carrier_names["CardDisplay"])
        assert physical_carrier.VibeCADMaterialTarget is target
        assert appearance_carrier.VibeCADMaterialTarget is target
        assert card_appearance_carrier.VibeCADMaterialTarget is appearance_only_target
        assert physical_carrier.getTypeIdOfProperty(PROP_MATERIAL_BASELINE) == (
            "Materials::PropertyMaterial"
        )
        assert appearance_carrier.getTypeIdOfProperty(PROP_APPEARANCE_BASELINE) == (
            "App::PropertyMaterialList"
        )
        assert str(target.ShapeMaterial.UUID) == str(second_card.UUID)
        assert _material_card_state(
            appearance_only_target.ShapeMaterial
        ) == _material_card_state(original_appearance_only_material)
        _assert_card_appearance(appearance_only_target, second_card)
        assert str(getattr(physical_carrier, PROP_PROGRAM_REVISION)) == next_prepared["revision"]

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": next_prepared["program_id"],
                "expected_revision": next_prepared["revision"],
                "reason": "Material production integration complete",
            },
        )
        target.ViewObject.LineWidth = float(target.ViewObject.LineWidth) + 1.0
        prepared_delete = prepare_delete(delete_capture)
        try:
            delete_live_program(service, prepared_delete)
        except RuntimeError as exc:
            assert "controlled display properties" in str(exc)
            restore_prepared_delete(prepared_delete)
        else:
            raise AssertionError("Human appearance edits must block deletion.")
        _restore_accepted_appearance(appearance_carrier, target)

        blocker = reopened.addObject("App::FeaturePython", "CarrierConsumer")
        blocker.addProperty("App::PropertyLink", "Source")
        blocker.Source = physical_carrier
        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": next_prepared["program_id"],
                "expected_revision": next_prepared["revision"],
                "reason": "Material production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        try:
            delete_live_program(service, prepared_delete)
        except RuntimeError as exc:
            assert "reference" in str(exc).lower()
            restore_prepared_delete(prepared_delete)
        else:
            raise AssertionError("External carrier consumers must block deletion.")
        blocker.Source = None
        reopened.removeObject(blocker.Name)

        accepted_material_before_delete_fault = _material_card_state(target.ShapeMaterial)
        accepted_appearance_before_delete_fault = _appearance(target)
        accepted_card_appearance_before_delete_fault = _appearance(appearance_only_target)
        fault_delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": next_prepared["program_id"],
                "expected_revision": next_prepared["revision"],
                "reason": "Injected deletion rollback",
            },
        )
        fault_prepared_delete = prepare_delete(fault_delete_capture)
        original_remove_owned = publication_module._remove_owned_objects

        def fail_after_carrier_removal(*args, **kwargs):
            original_remove_owned(*args, **kwargs)
            raise RuntimeError("injected post-removal deletion failure")

        publication_module._remove_owned_objects = fail_after_carrier_removal
        try:
            try:
                delete_live_program(service, fault_prepared_delete)
            except RuntimeError as exc:
                assert "injected post-removal" in str(exc)
                restore_prepared_delete(fault_prepared_delete)
            else:
                raise AssertionError("Injected Material deletion failure did not fire.")
        finally:
            publication_module._remove_owned_objects = original_remove_owned
        assert reopened.getObject(carrier_names["Physical"]) is not None
        assert reopened.getObject(carrier_names["Axle"]) is not None
        assert reopened.getObject(carrier_names["Display"]) is not None
        assert reopened.getObject(carrier_names["CardDisplay"]) is not None
        assert _material_card_state(target.ShapeMaterial) == accepted_material_before_delete_fault
        assert _material_state_equal(
            _appearance(target), accepted_appearance_before_delete_fault
        )
        assert _material_state_equal(
            _appearance(appearance_only_target),
            accepted_card_appearance_before_delete_fault,
        )

        delete_capture = _captured(
            root,
            reopened,
            operation="delete_program",
            arguments={
                "program_id": next_prepared["program_id"],
                "expected_revision": next_prepared["revision"],
                "reason": "Material production integration complete",
            },
        )
        prepared_delete = prepare_delete(delete_capture)
        original_manager = Materials.MaterialManager
        Materials.MaterialManager = forbidden_catalog_access
        try:
            deletion = delete_live_program(service, prepared_delete)
        finally:
            Materials.MaterialManager = original_manager
        finished = finish_delete(prepared_delete, deletion)
        assert finished["ok"] is True
        assert reopened.getObject(carrier_names["Physical"]) is None
        assert reopened.getObject(carrier_names["Axle"]) is None
        assert reopened.getObject(carrier_names["Display"]) is None
        assert reopened.getObject(carrier_names["CardDisplay"]) is None
        assert _material_card_state(target.ShapeMaterial) == _material_card_state(
            original_target_material
        )
        assert _material_card_state(physical_only_target.ShapeMaterial) == _material_card_state(
            original_physical_only_material
        )
        assert _material_state_equal(_appearance(target), original_target_appearance)
        assert _material_state_equal(
            _appearance(physical_only_target), original_physical_only_appearance
        )
        assert _material_card_state(
            appearance_only_target.ShapeMaterial
        ) == _material_card_state(original_appearance_only_material)
        assert _material_state_equal(
            _appearance(appearance_only_target), original_appearance_only_appearance
        )
        assert not any(
            str(getattr(obj, PROP_PROGRAM_ID, "") or "") == next_prepared["program_id"]
            for obj in reopened.Objects
        )
        App.closeDocument(reopened.Name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "integration": "material_vibescript_api",
                    "catalog_uuid": str(first_card.UUID),
                    "stable_carriers": carrier_names,
                    "explicit_rollback": True,
                    "delete_restored_baseline": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        if App.ActiveDocument is not None:
            App.closeDocument(App.ActiveDocument.Name)
        shutil.rmtree(root, ignore_errors=True)


class MaterialVibeScriptIntegration(unittest.TestCase):
    """FreeCAD internal-GUI harness entry point for the production lifecycle."""

    def test_production_lifecycle(self) -> None:
        result = main()
        print("Material VibeScript GUI lifecycle returned", result, file=sys.stderr, flush=True)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    result_code = main()
    if result_code:
        raise RuntimeError(f"Material VibeScript integration failed with {result_code}.")
