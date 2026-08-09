# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real GUI/provider lifecycle gate for standalone Part Cross Sections."""

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
from VibeCADNativeModelErrors import NativeModelError
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADNativeModelPartSchema import model_part_capability_definition
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


def _task_button(standard_button):
    _process_events()
    for button_box in Gui.getMainWindow().findChildren(QtWidgets.QDialogButtonBox):
        if not button_box.isVisible():
            continue
        button = button_box.button(standard_button)
        if button is not None and button.isVisible() and button.isEnabled():
            return button
    return None


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
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
    document.openTransaction("Create Part Cross Sections gate sources")
    try:
        sources = {
            "HumanBox": _publish_source(
                document, "HumanBox", Part.makeBox(10, 10, 10)
            ),
            "WholeBox": _publish_source(
                document, "WholeBox", Part.makeBox(12, 9, 10, App.Vector(20, 0, 0))
            ),
            "SelectedCompound": _publish_source(
                document,
                "SelectedCompound",
                Part.makeCompound(
                    [
                        Part.makeBox(6, 6, 8, App.Vector(40, 0, 0)),
                        Part.makeBox(6, 6, 8, App.Vector(50, 0, 0)),
                    ]
                ),
            ),
            "BatchA": _publish_source(
                document, "BatchA", Part.makeBox(8, 8, 10, App.Vector(65, 0, 0))
            ),
            "BatchB": _publish_source(
                document, "BatchB", Part.makeCylinder(4, 10, App.Vector(80, 4, 0))
            ),
            "PlacedBox": _publish_source(
                document,
                "PlacedBox",
                Part.makeBox(8, 7, 9),
                placement=App.Placement(
                    App.Vector(100, 3, 2),
                    App.Rotation(App.Vector(0, 0, 1), 12),
                ),
            ),
            "MissBox": _publish_source(
                document, "MissBox", Part.makeBox(5, 5, 5, App.Vector(120, 0, 0))
            ),
            "RollbackBox": _publish_source(
                document, "RollbackBox", Part.makeBox(6, 6, 8, App.Vector(135, 0, 0))
            ),
            "InactiveBox": _publish_source(
                document, "InactiveBox", Part.makeBox(6, 6, 8, App.Vector(150, 0, 0))
            ),
        }
        stale = _publish_source(
            document,
            "StaleCrossSectionSource",
            Part.makeBox(4, 4, 4, App.Vector(165, 0, 0)),
        )
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete stale Cross Sections source")
    try:
        stale_name = stale.Name
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    return sources, stale_name


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "c" * 64,
            ("Part_CrossSections",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("cross_sections",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _source(name: str, subelements=()) -> dict[str, object]:
    result = {"object_name": name}
    if subelements:
        result["subelements"] = list(subelements)
    return result


def _single(position: float) -> dict[str, object]:
    return {"kind": "single", "position_mm": position}


def _series(position: float, count: int, distance: float, both_sides: bool):
    return {
        "kind": "series",
        "position_mm": position,
        "count": count,
        "distance_mm": distance,
        "both_sides": both_sides,
    }


def _arguments(label: str, sources, plane: str, distribution) -> dict[str, object]:
    return {
        "operation": "cross_sections",
        "label": label,
        "definition": {
            "sources": list(sources),
            "plane": plane,
            "distribution": distribution,
        },
    }


def _positions(distribution) -> tuple[float, ...]:
    if distribution["kind"] == "single":
        return (float(distribution["position_mm"]),)
    count = int(distribution["count"])
    distance = float(distribution["distance_mm"])
    position = float(distribution["position_mm"])
    start = position - 0.5 * (count - 1) * distance if distribution["both_sides"] else position
    return tuple(start + index * distance for index in range(count))


def _normal(plane: str) -> tuple[float, float, float]:
    return {
        "xy": (0.0, 0.0, 1.0),
        "xz": (0.0, 1.0, 0.0),
        "yz": (1.0, 0.0, 0.0),
    }[plane]


def _selected_shape(source, subelements):
    if not subelements:
        return Part.getShape(source, transform=True)
    return Part.makeCompound(
        [
            Part.getShape(source, name, needSubElement=True, transform=True)
            for name in subelements
        ]
    )


def _expected_shapes(document, sources, source_specs, plane, positions):
    document.openTransaction("Probe exact Part Cross Sections features")
    try:
        expected = []
        for index, spec in enumerate(source_specs, start=1):
            copied = document.addObject("Part::Feature", f"CrossOracleSource{index}")
            copied.Shape = _selected_shape(
                sources[spec["object_name"]], spec.get("subelements", ())
            ).copy()
            result = document.addObject("Part::CrossSections", f"CrossOracle{index}")
            result.Source = (copied, [])
            result.PlaneNormal = App.Vector(*_normal(plane))
            result.PlanePositions = list(positions)
            assert document.recompute([result], True, True) is not False
            result.touch()
            assert document.recompute([result], True, True) is not False
            assert result.isValid() and not result.Shape.isNull() and result.Shape.isValid()
            expected.append(result.Shape.copy())
        return tuple(expected)
    finally:
        document.abortTransaction()


def _link_sub(value) -> tuple[object | None, tuple[str, ...]]:
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _assert_human_contract(document, sources) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(sources["HumanBox"])
    _process_events()
    assert Gui.isCommandActive("Part_CrossSections")
    Gui.runCommand("Part_CrossSections", 0)
    _process_events(32)
    assert Gui.Control.activeDialog()
    window = Gui.getMainWindow()
    xy = window.findChild(QtWidgets.QRadioButton, "xyPlane")
    xz = window.findChild(QtWidgets.QRadioButton, "xzPlane")
    yz = window.findChild(QtWidgets.QRadioButton, "yzPlane")
    position = window.findChild(QtWidgets.QAbstractSpinBox, "position")
    sections = window.findChild(QtWidgets.QGroupBox, "sectionsBox")
    both = window.findChild(QtWidgets.QCheckBox, "checkBothSides")
    count = window.findChild(QtWidgets.QSpinBox, "countSections")
    distance = window.findChild(QtWidgets.QAbstractSpinBox, "distance")
    assert all(
        widget is not None
        for widget in (xy, xz, yz, position, sections, both, count, distance)
    )
    assert (xy.text(), xz.text(), yz.text()) == ("XY", "XZ", "YZ")
    assert xy.isChecked() and not xz.isChecked() and not yz.isChecked()
    assert _close(position.property("rawValue"), 5.0)
    assert not sections.isChecked() and not both.isChecked() and count.value() == 1
    sections.setChecked(True)
    both.setChecked(True)
    count.setValue(3)
    assert position.setProperty("rawValue", 5.0)
    assert distance.setProperty("rawValue", 2.0)
    _process_events(24)
    ok_button = _task_button(QtWidgets.QDialogButtonBox.Ok)
    assert ok_button is not None
    ok_button.click()
    _process_events(32)
    assert not Gui.Control.activeDialog()
    created = [
        obj
        for obj in document.Objects
        if obj.Name not in before and obj.TypeId == "Part::CrossSections"
    ]
    assert len(created) == 1
    result = created[0]
    assert _link_sub(result.Source) == (sources["HumanBox"], ())
    assert tuple(float(value) for value in result.PlanePositions) == (3.0, 5.0, 7.0)
    assert tuple(float(getattr(result.PlaneNormal, axis)) for axis in "xyz") == (0.0, 0.0, 1.0)
    assert sources["HumanBox"].Visibility
    document.undo()
    _process_events()
    assert tuple(obj.Name for obj in document.Objects) == before
    assert sources["HumanBox"].Visibility
    Gui.Selection.clearSelection()


def _assert_exact_preflight_rejects_change(document, sources) -> None:
    arguments = _arguments(
        "Exactness Probe",
        (_source("RollbackBox"),),
        "xy",
        _single(4.0),
    )
    spec = runtime_module.prepare_part_cross_sections(
        str(document.Uid), arguments["definition"]
    )
    prepared = runtime_module.preflight_part_cross_sections(document, spec)
    source = sources["RollbackBox"]
    original = source.Placement
    before = tuple(obj.Name for obj in document.Objects)
    rejected = False
    try:
        moved = App.Placement(original)
        moved.Base.x += 1.0
        source.Placement = moved
        try:
            runtime_module.create_part_cross_sections(
                document, label="Must Not Exist", prepared=prepared
            )
        except NativeModelError as exc:
            rejected = "changed after preflight" in str(exc)
    finally:
        source.Placement = original
        assert document.recompute([source], True, True) is not False
    assert rejected and tuple(obj.Name for obj in document.Objects) == before


def _cases():
    return (
        (
            "Gate Whole Cross Section",
            (_source("WholeBox"),),
            "xy",
            _single(5.0),
        ),
        (
            "Gate Exact Compound Cross Sections",
            (_source("SelectedCompound", ("Solid1", "Solid2")),),
            "xy",
            _single(3.0),
        ),
        (
            "Gate Batch Series Cross Sections",
            (_source("BatchA"), _source("BatchB")),
            "xz",
            _series(4.0, 3, 2.0, True),
        ),
        (
            "Gate Placed Cross Section",
            (_source("PlacedBox"),),
            "yz",
            _single(104.0),
        ),
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartCrossSectionsGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale_name = _create_sources(document)
        _assert_human_contract(document, sources)
        _assert_exact_preflight_rejects_change(document, sources)

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-cross-sections-gui")
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
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-cross-sections-call-{call_number}",
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
                "operation": "cross_sections",
                "label": "Incomplete Cross Sections",
                "definition": {
                    "sources": [_source("WholeBox")],
                    "plane": "xy",
                },
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        expected_fields = {
            "ok",
            "root",
            "source_count",
            "result_count",
            "resource_count",
            "plane",
            "plane_count",
            "both_sides",
            "total_wire_count",
            "total_edge_count",
            "total_length_mm",
            "receipt",
            "assistant_undo_available",
        }
        records = []
        for label, source_specs, plane, distribution in _cases():
            positions = _positions(distribution)
            expected_shapes = _expected_shapes(
                document, sources, source_specs, plane, positions
            )
            signatures = tuple(_shape_signature(shape) for shape in expected_shapes)
            source_objects = tuple(sources[item["object_name"]] for item in source_specs)
            source_breps = tuple(
                Part.getShape(source, transform=True).exportBrepToString()
                for source in source_objects
            )
            response = native_call(
                _arguments(label, source_specs, plane, distribution)
            )
            assert set(response) == expected_fields
            assert response["source_count"] == len(source_specs)
            assert response["result_count"] == len(source_specs)
            assert response["resource_count"] == len(source_specs) - 1
            assert response["plane"] == plane
            assert response["plane_count"] == len(positions)
            assert response["both_sides"] is bool(distribution.get("both_sides", False))
            assert response["total_wire_count"] == sum(
                signature["topology"][2] for signature in signatures
            )
            assert response["total_edge_count"] == sum(
                signature["topology"][1] for signature in signatures
            )
            assert _close(
                response["total_length_mm"],
                sum(signature["length"] for signature in signatures),
            )
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == len(source_specs)
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []

            root = document.getObject(response["root"]["object_name"])
            results = tuple(
                obj
                for obj in document.Objects
                if obj is root or getattr(obj, "VibeCADTimelineOwner", None) is root
            )
            results = tuple(sorted(results, key=lambda item: item.ID))
            assert len(results) == len(source_specs)
            for index, (result, item, signature) in enumerate(
                zip(results, source_specs, signatures, strict=True)
            ):
                assert result.TypeId == "Part::CrossSections"
                assert _link_sub(result.Source) == (
                    sources[item["object_name"]],
                    tuple(item.get("subelements", ())),
                )
                assert tuple(float(value) for value in result.PlanePositions) == positions
                assert tuple(
                    float(getattr(result.PlaneNormal, axis)) for axis in "xyz"
                ) == _normal(plane)
                expected_role = "operation" if result is root else "resource"
                assert result.VibeCADTimelineRole == expected_role
                assert (
                    getattr(result, "VibeCADTimelineOwner", None) is None
                    if result is root
                    else result.VibeCADTimelineOwner is root
                )
                assert "VibeCADTimelineReplacedInputs" not in result.PropertiesList
                _assert_shape_signature(result.Shape, signature)
                for _repeat in range(4):
                    assert document.recompute([result], True, True) is not False
                    _assert_shape_signature(result.Shape, signature)
                assert source_objects[index].Visibility
            assert str(root.VibeCADDefinitionId) and str(root.DesignId)
            assert tuple(
                Part.getShape(source, transform=True).exportBrepToString()
                for source in source_objects
            ) == source_breps
            record = {
                "names": tuple(result.Name for result in results),
                "root": root.Name,
                "definition_id": str(root.VibeCADDefinitionId),
                "design_id": str(root.DesignId),
                "sources": tuple(
                    (item["object_name"], tuple(item.get("subelements", ())))
                    for item in source_specs
                ),
                "normal": _normal(plane),
                "positions": positions,
                "signatures": signatures,
            }
            document.undo()
            _process_events()
            assert all(document.getObject(name) is None for name in record["names"])
            assert all(source.Visibility for source in source_objects)
            document.redo()
            _process_events()
            assert all(document.getObject(name) is not None for name in record["names"])
            records.append(record)

        failure_cases = (
            (
                _arguments(
                    "Missing Cross Sections",
                    (_source(stale_name),),
                    "xy",
                    _single(2.0),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                _arguments(
                    "Invalid Subelement Cross Sections",
                    (_source("RollbackBox", ("Face999",)),),
                    "xy",
                    _single(4.0),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                _arguments(
                    "Missed Cross Sections",
                    (_source("MissBox"),),
                    "xy",
                    _single(100.0),
                ),
                "NATIVE_MODEL_INVALID",
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
        timeline.Position = 0
        _process_events()
        before = tuple(obj.Name for obj in document.Objects)
        inactive = native_call(
            _arguments(
                "Inactive Cross Sections",
                (_source("InactiveBox"),),
                "xy",
                _single(4.0),
            ),
            succeeds=False,
        )
        assert inactive["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        timeline.Position = timeline_end
        _process_events()

        before = tuple(obj.Name for obj in document.Objects)
        original_verify = runtime_module.verify_part_cross_sections

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced Cross Sections postcondition failure.")

        runtime_module.verify_part_cross_sections = reject_after_creation
        try:
            rollback = native_call(
                _arguments(
                    "Rollback Cross Sections",
                    (_source("RollbackBox"),),
                    "xy",
                    _single(4.0),
                ),
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_cross_sections = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert sources["RollbackBox"].Visibility

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-cross-"))
        save_path = save_directory / "ModelPartCrossSections.FCStd"
        document.saveAs(str(save_path))
        App.closeDocument(document.Name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()
        for record in records:
            root = document.getObject(record["root"])
            assert str(root.VibeCADDefinitionId) == record["definition_id"]
            assert str(root.DesignId) == record["design_id"]
            results = tuple(document.getObject(name) for name in record["names"])
            for result, source_spec, signature in zip(
                results, record["sources"], record["signatures"], strict=True
            ):
                source, subelements = _link_sub(result.Source)
                assert (source.Name, subelements) == source_spec
                assert tuple(float(value) for value in result.PlanePositions) == record[
                    "positions"
                ]
                assert tuple(
                    float(getattr(result.PlaneNormal, axis)) for axis in "xyz"
                ) == record["normal"]
                _assert_shape_signature(result.Shape, signature)
                assert document.getObject(source.Name).Visibility

        print("VIBECAD_NATIVE_MODEL_PART_CROSS_SECTIONS_GUI_OK", flush=True)
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
