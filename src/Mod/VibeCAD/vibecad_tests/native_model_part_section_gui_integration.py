# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real VibeCAD GUI and provider lifecycle gate for standalone Part Section."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
import PartGui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelBooleanSchema import model_boolean_capability_definition
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelBooleanRuntime as runtime_module
from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTurn import NativeTurnSnapshot
from VibeCADNativeUndo import NativeAssistantUndoLedger


def _process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _close(left: float, right: float, tolerance: float = 5.0e-3) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    return {
        "shape_type": str(shape.ShapeType),
        "topology": (
            len(shape.Vertexes),
            len(shape.Edges),
            len(shape.Wires),
            len(shape.Faces),
            len(shape.Shells),
            len(shape.Solids),
        ),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "length": float(shape.Length),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    assert all(
        _close(left, right)
        for left, right in zip(actual["bounds"], expected["bounds"], strict=True)
    ), (actual["bounds"], expected["bounds"])
    assert _close(actual["length"], expected["length"])


def _material_signature(material) -> tuple[object, ...]:
    colors = tuple(
        tuple(float(component) for component in getattr(material, name))
        for name in (
            "AmbientColor",
            "DiffuseColor",
            "EmissiveColor",
            "SpecularColor",
        )
    )
    return (*colors, float(material.Shininess), float(material.Transparency))


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None):
    source = document.addObject("Part::Feature", name)
    source.Label = name
    source.Shape = shape
    if placement is not None:
        source.Placement = placement
    return _publish_object(document, source)


def _create_sources(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Part Section gate sources")
    try:
        sources = {
            "HumanBase": _publish_source(
                document, "HumanBase", Part.makeBox(10, 10, 10)
            ),
            "HumanTool": _publish_source(
                document, "HumanTool", Part.makeBox(10, 10, 10, App.Vector(5, 2, 1))
            ),
            "BoxBase": _publish_source(
                document, "BoxBase", Part.makeBox(12, 10, 8)
            ),
            "BoxTool": _publish_source(
                document, "BoxTool", Part.makeBox(8, 12, 10, App.Vector(4, -1, 2))
            ),
            "SphereBase": _publish_source(
                document, "SphereBase", Part.makeSphere(5, App.Vector(25, 0, 0))
            ),
            "PlaneTool": _publish_source(
                document,
                "PlaneTool",
                Part.makePlane(14, 14, App.Vector(18, -7, 0)),
            ),
            "PlacedBase": _publish_source(
                document,
                "PlacedBase",
                Part.makeBox(8, 8, 8),
                placement=App.Placement(
                    App.Vector(45, 3, 2),
                    App.Rotation(App.Vector(0, 0, 1), 18),
                ),
            ),
            "PlacedTool": _publish_source(
                document,
                "PlacedTool",
                Part.makeBox(7, 9, 9),
                placement=App.Placement(
                    App.Vector(48, 2, 3),
                    App.Rotation(App.Vector(0, 0, 1), -12),
                ),
            ),
            "CompoundBase": _publish_source(
                document,
                "CompoundBase",
                Part.makeCompound(
                    [
                        Part.makeBox(5, 5, 5, App.Vector(65, 0, 0)),
                        Part.makeBox(5, 5, 5, App.Vector(65, 8, 0)),
                    ]
                ),
            ),
            "CompoundTool": _publish_source(
                document,
                "CompoundTool",
                Part.makePlane(20, 20, App.Vector(60, -2, 2.5)),
            ),
            "DisjointBase": _publish_source(
                document, "DisjointBase", Part.makeBox(3, 3, 3, App.Vector(90, 0, 0))
            ),
            "DisjointTool": _publish_source(
                document,
                "DisjointTool",
                Part.makeBox(3, 3, 3, App.Vector(100, 0, 0)),
            ),
            "RollbackBase": _publish_source(
                document,
                "RollbackBase",
                Part.makeBox(6, 6, 6, App.Vector(115, 0, 0)),
            ),
            "RollbackTool": _publish_source(
                document,
                "RollbackTool",
                Part.makeBox(6, 6, 6, App.Vector(118, 1, 1)),
            ),
            "InactiveBase": _publish_source(
                document,
                "InactiveBase",
                Part.makeBox(5, 5, 5, App.Vector(130, 0, 0)),
            ),
            "NullShape": _publish_source(document, "NullShape", Part.Shape()),
        }
        sources["HumanBase"].ViewObject.ShapeColor = (0.18, 0.44, 0.73)
        sources["HumanBase"].ViewObject.LineColor = (0.07, 0.11, 0.19)
        stale = _publish_source(
            document,
            "StaleSectionOperand",
            Part.makeBox(4, 4, 4, App.Vector(145, 0, 0)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise

    document.openTransaction("Delete stale Part Section source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_boolean_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "e" * 64,
            ("Part_Section",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("section",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _arguments(label: str, first: str, second: str) -> dict[str, object]:
    return {
        "operation": "section",
        "label": label,
        "definition": {
            "operands": [
                {"object_name": first},
                {"object_name": second},
            ]
        },
    }


def _factory_refine_default(document) -> bool:
    document.openTransaction("Probe Part Section default")
    try:
        probe = document.addObject("Part::Section", "SectionDefaultProbe")
        return bool(probe.Refine)
    finally:
        document.abortTransaction()


def _expected_shape(document, sources, first: str, second: str):
    document.openTransaction("Probe exact Part Section feature")
    try:
        copied = []
        for index, name in enumerate((first, second), start=1):
            source_copy = document.addObject("Part::Feature", f"SectionOracle{index}")
            source_copy.Shape = Part.getShape(sources[name], transform=True).copy()
            copied.append(source_copy)
        result = document.addObject("Part::Section", "SectionOracleResult")
        result.Base = copied[0]
        result.Tool = copied[1]
        result.Approximation = False
        assert document.recompute([result], True, True) is not False
        result.touch()
        assert document.recompute([result], True, True) is not False
        assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
        return result.Shape.copy(), bool(result.Refine)
    finally:
        document.abortTransaction()


def _assert_human_contract(document, sources, refine_default: bool) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(sources["HumanBase"])
    Gui.Selection.addSelection(sources["HumanTool"])
    _process_events()
    assert Gui.isCommandActive("Part_Section")
    Gui.runCommand("Part_Section", 0)
    _process_events(24)
    result = document.ActiveObject
    assert result is not None and result.TypeId == "Part::Section"
    assert result.Base is sources["HumanBase"]
    assert result.Tool is sources["HumanTool"]
    assert not bool(result.Approximation)
    assert bool(result.Refine) is refine_default
    assert tuple(result.ViewObject.claimChildren()) == (
        sources["HumanBase"],
        sources["HumanTool"],
    )
    assert not sources["HumanBase"].Visibility
    assert not sources["HumanTool"].Visibility
    assert _material_signature(result.ViewObject.LineMaterial) == _material_signature(
        sources["HumanBase"].ViewObject.ShapeAppearance[0]
    )
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanBase"].Visibility and sources["HumanTool"].Visibility
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    spec = runtime_module.prepare_part_section(
        str(document.Uid),
        _arguments("Exactness Probe", "RollbackBase", "RollbackTool")["definition"],
    )
    prepared = runtime_module.preflight_part_section(document, spec)
    source = sources["RollbackBase"]
    original_placement = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original_placement)
        moved.Base.y += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_section(
                document,
                label="Must Not Exist",
                prepared=prepared,
            )
        except NativeModelError as exc:
            rejected = "changed after preflight" in str(exc)
    finally:
        source.Placement = original_placement
        assert document.recompute([source], True, True) is not False
    assert rejected
    assert tuple(obj.Name for obj in document.Objects) == before


def _cases() -> tuple[tuple[str, str, str], ...]:
    return (
        ("Gate Box Section", "BoxBase", "BoxTool"),
        ("Gate Sphere Plane Section", "SphereBase", "PlaneTool"),
        ("Gate Placed Section", "PlacedBase", "PlacedTool"),
        ("Gate Compound Section", "CompoundBase", "CompoundTool"),
        ("Gate Disjoint Section", "DisjointBase", "DisjointTool"),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartSectionGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        refine_default = _factory_refine_default(document)
        _assert_human_contract(document, sources, refine_default)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-section-gui")
        context = NativeRuntimeContext(
            service=service,
            document=document,
            state=state,
            undo_ledger=ledger,
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False,
        )
        turn = _turn()
        debug_events = []
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
            debug_sink=debug_events.append,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.boolean",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-section-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "section",
                "label": "Incomplete Section",
                "definition": {"operands": [{"object_name": "BoxBase"}]},
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "shape_type",
            "vertex_count",
            "edge_count",
            "length_mm",
            "refined",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for label, first, second in _cases():
            expected_shape, expected_refine = _expected_shape(
                document, sources, first, second
            )
            expected = _shape_signature(expected_shape)
            operand_objects = (sources[first], sources[second])
            operand_breps = tuple(
                Part.getShape(source, transform=True).exportBrepToString()
                for source in operand_objects
            )
            assert all(source.Visibility for source in operand_objects)
            response = native_call(_arguments(label, first, second))
            assert set(response) == expected_fields
            assert response["shape_type"] == expected["shape_type"]
            assert response["vertex_count"] == expected["topology"][0]
            assert response["edge_count"] == expected["topology"][1]
            assert _close(response["length_mm"], expected["length"])
            assert response["refined"] is expected_refine is refine_default
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            result = document.getObject(response["root"]["object_name"])
            assert result is not None and result.TypeId == "Part::Section"
            assert result.Label == label
            assert result.Base is operand_objects[0]
            assert result.Tool is operand_objects[1]
            assert not bool(result.Approximation)
            assert bool(result.Refine) is refine_default
            assert result.getParentGeoFeatureGroup() is None
            assert result.VibeCADTimelineRole == "operation"
            assert getattr(result, "VibeCADTimelineOwner", None) is None
            assert str(result.VibeCADDefinitionId) and str(result.DesignId)
            assert tuple(result.VibeCADTimelineReplacedInputs) == operand_objects
            assert tuple(result.ViewObject.claimChildren()) == operand_objects
            assert not any(source.Visibility for source in operand_objects)
            assert _material_signature(result.ViewObject.LineMaterial) == _material_signature(
                operand_objects[0].ViewObject.ShapeAppearance[0]
            )
            _assert_shape_signature(result.Shape, expected)
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, expected)
            assert tuple(
                Part.getShape(source, transform=True).exportBrepToString()
                for source in operand_objects
            ) == operand_breps

            record = {
                "name": result.Name,
                "label": label,
                "definition_id": str(result.VibeCADDefinitionId),
                "design_id": str(result.DesignId),
                "operands": (first, second),
                "signature": expected,
            }
            document.undo()
            _process_events()
            assert document.getObject(record["name"]) is None
            assert all(source.Visibility for source in operand_objects)
            document.redo()
            _process_events()
            result = document.getObject(record["name"])
            assert result is not None
            assert not any(source.Visibility for source in operand_objects)
            _assert_shape_signature(result.Shape, expected)
            records.append(record)

        failure_cases = (
            (
                _arguments("Missing Section", stale_name, "RollbackTool"),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments("Null Section", "NullShape", "RollbackTool"),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments("Duplicate Section", "RollbackBase", "RollbackBase"),
                "NATIVE_ARGUMENTS_INVALID",
            ),
        )
        for arguments, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(arguments, succeeds=False)
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False

        timeline = document.getObject("VibeCADTimeline")
        timeline_end = int(timeline.Position)
        inactive = sources["InactiveBase"]
        timeline.Position = 0
        _process_events()
        assert not PartGui.isModelingObjectActive(inactive)
        before = tuple(obj.Name for obj in document.Objects)
        inactive_response = native_call(
            _arguments("Inactive Section", "InactiveBase", "RollbackTool"),
            succeeds=False,
        )
        assert inactive_response["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()
        assert PartGui.isModelingObjectActive(inactive)

        rollback_operands = (sources["RollbackBase"], sources["RollbackTool"])
        rollback_breps = tuple(
            Part.getShape(source, transform=True).exportBrepToString()
            for source in rollback_operands
        )
        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_section

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Section postcondition failure.")

        runtime_module.verify_part_section = reject_after_creation
        try:
            rollback = native_call(
                _arguments("Rollback Section", "RollbackBase", "RollbackTool"),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_section = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert all(source.Visibility for source in rollback_operands)
        assert tuple(
            Part.getShape(source, transform=True).exportBrepToString()
            for source in rollback_operands
        ) == rollback_breps

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-section-"))
        save_path = save_directory / "ModelPartSection.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            result = document.getObject(record["name"])
            assert result is not None and result.TypeId == "Part::Section"
            assert result.Label == record["label"]
            assert str(result.VibeCADDefinitionId) == record["definition_id"]
            assert str(result.DesignId) == record["design_id"]
            assert (result.Base.Name, result.Tool.Name) == record["operands"]
            assert not bool(result.Approximation)
            assert bool(result.Refine) is refine_default
            assert tuple(
                source.Name for source in result.VibeCADTimelineReplacedInputs
            ) == record["operands"]
            assert tuple(
                source.Name for source in result.ViewObject.claimChildren()
            ) == record["operands"]
            assert not any(
                document.getObject(name).Visibility for name in record["operands"]
            )
            _assert_shape_signature(result.Shape, record["signature"])
            for _index in range(4):
                assert document.recompute([result], True, True) is not False
                _assert_shape_signature(result.Shape, record["signature"])

        print("VIBECAD_NATIVE_MODEL_PART_SECTION_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if save_directory is not None:
            shutil.rmtree(save_directory, ignore_errors=True)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
