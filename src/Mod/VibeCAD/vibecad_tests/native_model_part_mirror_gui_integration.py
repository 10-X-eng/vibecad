# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native standalone Part Mirror."""

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
import VibeCADNativeModelPartRuntime as runtime_module
from VibeCADCore import get_service
from VibeCADNativeCapabilityRegistry import NativeProviderSurface
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeModelErrors import NativeModelError
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


def _close(left: float, right: float, tolerance: float = 1.0e-7) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    return {
        "shape_type": str(shape.ShapeType),
        "topology": (len(shape.Vertexes), len(shape.Edges), len(shape.Faces)),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "measures": (float(shape.Length), float(shape.Area), float(shape.Volume)),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    assert actual["shape_type"] == expected["shape_type"]
    assert actual["topology"] == expected["topology"]
    for field in ("bounds", "measures"):
        assert all(
            _close(left, right, tolerance=5.0e-3 if field == "bounds" else 1.0e-7)
            for left, right in zip(actual[field], expected[field], strict=True)
        ), (field, actual[field], expected[field])


def _current_shape(obj):
    return Part.getShape(obj, transform=True)


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "m" * 64,
            ("Part_Mirror",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("mirror",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _publish_object(document, obj):
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _publish_source(document, name: str, shape, *, placement=None):
    obj = document.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    if placement is not None:
        obj.Placement = placement
    return _publish_object(document, obj)


def _publish_plane(document, name: str):
    plane = document.addObject("Part::Plane", name)
    plane.Label = name
    plane.Length = 40.0
    plane.Width = 40.0
    plane.Placement = App.Placement(
        App.Vector(0, 0, 2),
        App.Rotation(App.Vector(1, 0, 0), 25),
    )
    return _publish_object(document, plane)


def _create_sources(document) -> tuple[dict[str, object], dict[str, str]]:
    document.openTransaction("Create Part Mirror gate sources")
    try:
        transformed = _publish_source(
            document,
            "TransformedSolid",
            Part.makeBox(4, 5, 6),
            placement=App.Placement(
                App.Vector(11, 6, 9),
                App.Rotation(App.Vector(0, 0, 1), 20),
            ),
        )
        sources = {
            transformed.Name: transformed,
            "XZSolid": _publish_source(
                document, "XZSolid", Part.makeBox(5, 4, 3, App.Vector(22, 7, 5))
            ),
            "YZSolid": _publish_source(
                document, "YZSolid", Part.makeCylinder(3, 7, App.Vector(34, 4, 2))
            ),
            "PlaneObjectSolid": _publish_source(
                document, "PlaneObjectSolid", Part.makeBox(4, 3, 5, App.Vector(44, 5, 4))
            ),
            "FaceReferenceSolid": _publish_source(
                document,
                "FaceReferenceSolid",
                Part.makeBox(3, 6, 4, App.Vector(54, 4, 6)),
            ),
            "CircleReferenceSolid": _publish_source(
                document,
                "CircleReferenceSolid",
                Part.makeBox(3, 4, 5, App.Vector(64, 6, 3)),
            ),
            "WholeFaceSolid": _publish_source(
                document, "WholeFaceSolid", Part.makeSphere(3, App.Vector(76, 5, 6))
            ),
            "WholeCircleSolid": _publish_source(
                document, "WholeCircleSolid", Part.makeBox(4, 4, 4, App.Vector(84, 5, 4))
            ),
            "CompoundSource": _publish_source(
                document,
                "CompoundSource",
                Part.makeCompound(
                    [
                        Part.makeBox(2, 3, 4, App.Vector(94, 4, 5)),
                        Part.makeSphere(1.5, App.Vector(99, 6, 7)),
                    ]
                ),
            ),
            "MultiSolid": _publish_source(
                document, "MultiSolid", Part.makeBox(3, 5, 4, App.Vector(106, 3, 4))
            ),
            "MultiWire": _publish_source(
                document,
                "MultiWire",
                Part.makePolygon(
                    [
                        App.Vector(112, 2, 3),
                        App.Vector(116, 2, 3),
                        App.Vector(116, 6, 5),
                    ]
                ),
            ),
            "RollbackSource": _publish_source(
                document,
                "RollbackSource",
                Part.makeBox(3, 4, 5, App.Vector(122, 4, 6)),
            ),
        }
        sources.update(
            {
                "ReferencePlane": _publish_plane(document, "ReferencePlane"),
                "PlanarReference": _publish_source(
                    document,
                    "PlanarReference",
                    Part.makePlane(150, 40, App.Vector(0, 0, 1)),
                ),
                "CircleReference": _publish_source(
                    document,
                    "CircleReference",
                    Part.makeCircle(20, App.Vector(60, 0, 2), App.Vector(0, 0, 1)),
                ),
                "WholePlanarReference": _publish_source(
                    document,
                    "WholePlanarReference",
                    Part.makePlane(150, 40, App.Vector(0, 0, 3)),
                ),
                "WholeCircleReference": _publish_source(
                    document,
                    "WholeCircleReference",
                    Part.makeCircle(24, App.Vector(80, 0, 4), App.Vector(0, 0, 1)),
                ),
                "NonplanarReference": _publish_source(
                    document,
                    "NonplanarReference",
                    Part.makeCylinder(4, 8, App.Vector(132, 0, 0)),
                ),
                "LineReference": _publish_source(
                    document,
                    "LineReference",
                    Part.makeLine(App.Vector(140, 0, 0), App.Vector(140, 0, 8)),
                ),
                "AmbiguousReference": _publish_source(
                    document,
                    "AmbiguousReference",
                    Part.makeBox(5, 5, 5, App.Vector(146, 0, 0)),
                ),
            }
        )
        transient_source = _publish_source(
            document,
            "TransientMirrorSource",
            Part.makeBox(2, 3, 4, App.Vector(156, 2, 3)),
        )
        transient_reference = _publish_source(
            document,
            "TransientMirrorReference",
            Part.makePlane(20, 20, App.Vector(150, 0, 0)),
        )
        stale = {
            "source": transient_source.Name,
            "reference": transient_reference.Name,
        }
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete Part Mirror stale targets")
    try:
        document.removeObject(stale["source"])
        document.removeObject(stale["reference"])
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert all(document.getObject(name) is None for name in stale.values())
    return sources, stale


def _axis_plane(
    kind: str,
    base: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, object]:
    return {
        "kind": kind,
        "base_mm": dict(zip(("x", "y", "z"), base, strict=True)),
    }


def _reference_plane(name: str, subelement: str | None = None) -> dict[str, object]:
    reference = {"object_name": name}
    if subelement is not None:
        reference["subelement"] = subelement
    return {"kind": "reference", "reference": reference}


def _definition(
    sources: tuple[str, ...],
    plane: dict[str, object],
) -> dict[str, object]:
    return {
        "sources": [{"object_name": name} for name in sources],
        "plane": plane,
    }


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate XY Mirror",
            "definition": _definition(
                ("TransformedSolid",),
                _axis_plane("xy", (0.0, 0.0, 2.0)),
            ),
            "reference_kind": None,
        },
        {
            "label": "Gate XZ Mirror",
            "definition": _definition(
                ("XZSolid",),
                {"kind": "xz"},
            ),
            "reference_kind": None,
        },
        {
            "label": "Gate YZ Mirror",
            "definition": _definition(
                ("YZSolid",),
                _axis_plane("yz", (30.0, 0.0, 0.0)),
            ),
            "reference_kind": None,
        },
        {
            "label": "Gate Plane Object Mirror",
            "definition": _definition(
                ("PlaneObjectSolid",),
                _reference_plane("ReferencePlane"),
            ),
            "reference_kind": "plane_object",
        },
        {
            "label": "Gate Face Mirror",
            "definition": _definition(
                ("FaceReferenceSolid",),
                _reference_plane("PlanarReference", "Face1"),
            ),
            "reference_kind": "planar_face",
        },
        {
            "label": "Gate Circle Mirror",
            "definition": _definition(
                ("CircleReferenceSolid",),
                _reference_plane("CircleReference", "Edge1"),
            ),
            "reference_kind": "circular_edge",
        },
        {
            "label": "Gate Whole Face Mirror",
            "definition": _definition(
                ("WholeFaceSolid",),
                _reference_plane("WholePlanarReference"),
            ),
            "reference_kind": "planar_face",
        },
        {
            "label": "Gate Whole Circle Mirror",
            "definition": _definition(
                ("WholeCircleSolid",),
                _reference_plane("WholeCircleReference"),
            ),
            "reference_kind": "circular_edge",
        },
        {
            "label": "Gate Compound Mirror",
            "definition": _definition(
                ("CompoundSource",),
                _axis_plane("xy", (0.0, 0.0, 1.0)),
            ),
            "reference_kind": None,
        },
        {
            "label": "Gate Multi Mirror",
            "definition": _definition(
                ("MultiSolid", "MultiWire"),
                _axis_plane("xy", (0.0, 0.0, 0.0)),
            ),
            "reference_kind": None,
        },
    )


def _widget(window, widget_type, name: str):
    widget = window.findChild(widget_type, name)
    assert widget is not None, name
    return widget


def _raw_value(window, name: str) -> float:
    return float(_widget(window, QtWidgets.QAbstractSpinBox, name).property("rawValue"))


def _assert_live_human_contract(document, source, reference) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    Gui.runCommand("Part_Mirror", 0)
    _process_events(32)
    window = Gui.getMainWindow()
    combo = _widget(window, QtWidgets.QComboBox, "comboBox")
    assert tuple(combo.itemText(index) for index in range(combo.count())) == (
        "XY-plane",
        "XZ-plane",
        "YZ-plane",
        "Use selected reference",
    )
    assert combo.currentIndex() == 0
    assert _widget(window, QtWidgets.QGroupBox, "groupBox").title() == "Base Point"
    assert tuple(_raw_value(window, name) for name in ("baseX", "baseY", "baseZ")) == (
        0.0,
        0.0,
        0.0,
    )
    select = _widget(window, QtWidgets.QPushButton, "selectButton")
    line = _widget(window, QtWidgets.QLineEdit, "referenceLineEdit")
    assert select.isCheckable() and select.isChecked() and select.text() == "Selecting"
    assert line.isReadOnly() and line.placeholderText() == "Mirror plane reference"
    tree = _widget(window, QtWidgets.QTreeWidget, "shapes")
    selected = tree.selectedItems()
    assert len(selected) == 1 and selected[0].text(0) == source.Label

    select.click()
    _process_events(8)
    assert not select.isChecked() and select.text() == "Select Reference"
    select.click()
    _process_events(8)
    assert select.isChecked() and select.text() == "Selecting"
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(reference, "Face1")
    _process_events(16)
    assert combo.currentIndex() == 3
    assert reference.Name in line.text() and "Face1" in line.text()

    Gui.Control.closeDialog()
    _process_events(16)
    Gui.Selection.clearSelection()
    assert tuple(obj.Name for obj in document.Objects) == before


def _result_objects(document, response) -> tuple[object, ...]:
    names = tuple(item["object_name"] for item in response["receipt"]["created"])
    results = tuple(document.getObject(name) for name in names)
    assert all(result is not None for result in results)
    return results


def _link_sub(value) -> tuple[object | None, tuple[str, ...]]:
    if not value:
        return None, ()
    target, names = value if isinstance(value, tuple) else (value, ())
    if isinstance(names, str):
        names = (names,) if names else ()
    return target, tuple(str(name) for name in names)


def _assert_stable_recompute(document, results, signatures) -> None:
    for _index in range(4):
        assert document.recompute(list(results), True, True) is not False
        for result, signature in zip(results, signatures, strict=True):
            _assert_shape_signature(result.Shape, signature)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartMirrorGate")
        VibeGui._connect_document_observer()
        _process_events()
        sources, stale = _create_sources(document)
        _assert_live_human_contract(
            document,
            sources["TransformedSolid"],
            sources["PlanarReference"],
        )
        source_names = tuple(
            dict.fromkeys(
                source["object_name"]
                for case in _cases()
                for source in case["definition"]["sources"]
            )
        )
        geometric_reference_names = (
            "PlanarReference",
            "CircleReference",
            "WholePlanarReference",
            "WholeCircleReference",
            "NonplanarReference",
            "LineReference",
            "AmbiguousReference",
        )
        source_signatures = {
            name: _shape_signature(_current_shape(sources[name])) for name in source_names
        }
        reference_signatures = {
            name: _shape_signature(_current_shape(sources[name]))
            for name in geometric_reference_names
        }
        plane_placement = sources["ReferencePlane"].Placement

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-mirror-gui")
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
                f"model-part-mirror-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (
                arguments.get("label"),
                response,
                debug_events[-1] if debug_events else None,
            )
            return response

        before = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {"operation": "mirror", "definition": _cases()[0]["definition"]},
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before

        records = []
        expected_response_fields = {
            "ok",
            "root",
            "source_count",
            "result_count",
            "resource_count",
            "plane_mode",
            "reference_kind",
            "shape_types",
            "total_length_mm",
            "total_area_mm2",
            "total_volume_mm3",
            "receipt",
            "assistant_undo_available",
        }
        normals = {
            "xy": (0.0, 0.0, 1.0),
            "xz": (0.0, 1.0, 0.0),
            "yz": (1.0, 0.0, 0.0),
        }
        for case in _cases():
            definition = case["definition"]
            case_source_names = tuple(
                source["object_name"] for source in definition["sources"]
            )
            assert all(bool(sources[name].Visibility) for name in case_source_names)
            response = native_call(
                {
                    "operation": "mirror",
                    "label": case["label"],
                    "definition": definition,
                }
            )
            assert set(response) == expected_response_fields
            assert response["source_count"] == len(case_source_names)
            assert response["result_count"] == len(case_source_names)
            assert response["resource_count"] == len(case_source_names) - 1
            assert response["plane_mode"] == definition["plane"]["kind"]
            assert response["reference_kind"] == case["reference_kind"]
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == len(case_source_names)
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []
            results = _result_objects(document, response)
            root = document.getObject(response["root"]["object_name"])
            assert root is results[-1]
            assert root.VibeCADTimelineRole == "operation"
            assert str(root.VibeCADDefinitionId) and str(root.DesignId)
            assert root.getParentGeoFeatureGroup() is None
            assert tuple(root.VibeCADTimelineReplacedInputs) == tuple(
                sources[name] for name in case_source_names
            )
            assert all(not bool(sources[name].Visibility) for name in case_source_names)

            plane = definition["plane"]
            expected_link = (None, ())
            if plane["kind"] == "reference":
                reference = plane["reference"]
                expected_link = (
                    sources[reference["object_name"]],
                    (reference["subelement"],) if "subelement" in reference else (),
                )
            for index, result in enumerate(results):
                assert result.TypeId == "Part::Mirroring"
                assert result.Label == (
                    case["label"]
                    if result is root
                    else f"{case['label']} — output {index + 1}"
                )
                assert result.Source is sources[case_source_names[index]]
                assert _link_sub(result.MirrorPlane) == expected_link
                assert result.getParentGeoFeatureGroup() is None
                if result is root:
                    assert getattr(result, "VibeCADTimelineOwner", None) is None
                else:
                    assert result.VibeCADTimelineRole == "resource"
                    assert result.VibeCADTimelineOwner is root
                assert result.Shape.isValid() and not result.Shape.isNull()
                if plane["kind"] != "reference":
                    expected_base = plane.get(
                        "base_mm",
                        {"x": 0.0, "y": 0.0, "z": 0.0},
                    )
                    assert all(
                        _close(actual, expected)
                        for actual, expected in zip(
                            tuple(result.Base),
                            tuple(expected_base[axis] for axis in "xyz"),
                            strict=True,
                        )
                    )
                    assert all(
                        _close(actual, expected)
                        for actual, expected in zip(
                            tuple(result.Normal),
                            normals[plane["kind"]],
                            strict=True,
                        )
                    )

            result_signatures = tuple(_shape_signature(result.Shape) for result in results)
            _assert_stable_recompute(document, results, result_signatures)
            record = {
                "label": case["label"],
                "root_name": root.Name,
                "definition_id": str(root.VibeCADDefinitionId),
                "design_id": str(root.DesignId),
                "source_names": case_source_names,
                "mirror_plane": (
                    expected_link[0].Name if expected_link[0] else None,
                    expected_link[1],
                ),
                "base": tuple(float(value) for value in root.Base),
                "normal": tuple(float(value) for value in root.Normal),
                "results": tuple(
                    {
                        "name": result.Name,
                        "label": str(result.Label),
                        "source_name": case_source_names[index],
                        "role": str(result.VibeCADTimelineRole),
                        "signature": result_signatures[index],
                    }
                    for index, result in enumerate(results)
                ),
            }

            document.undo()
            _process_events()
            assert all(document.getObject(item["name"]) is None for item in record["results"])
            assert all(bool(sources[name].Visibility) for name in case_source_names)
            document.redo()
            _process_events()
            root = document.getObject(record["root_name"])
            assert root is not None
            assert all(not bool(sources[name].Visibility) for name in case_source_names)
            for item in record["results"]:
                _assert_shape_signature(
                    document.getObject(item["name"]).Shape,
                    item["signature"],
                )
            records.append(record)

        transformed = document.getObject(records[0]["root_name"])
        assert _close(transformed.Shape.BoundBox.ZMin, -11.0)
        assert _close(transformed.Shape.BoundBox.ZMax, -5.0)
        for name, signature in source_signatures.items():
            _assert_shape_signature(_current_shape(sources[name]), signature)
        for name, signature in reference_signatures.items():
            _assert_shape_signature(_current_shape(sources[name]), signature)
        assert sources["ReferencePlane"].Placement == plane_placement

        failure_cases = (
            (
                "Stale Part Mirror Source",
                _definition((stale["source"],), _axis_plane("xy")),
                "NATIVE_TARGET_INVALID",
            ),
            (
                "Stale Part Mirror Reference",
                _definition(
                    ("RollbackSource",),
                    _reference_plane(stale["reference"]),
                ),
                "NATIVE_TARGET_INVALID",
            ),
            (
                "Nonplanar Part Mirror",
                _definition(
                    ("RollbackSource",),
                    _reference_plane("NonplanarReference", "Face1"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Noncircular Part Mirror",
                _definition(
                    ("RollbackSource",),
                    _reference_plane("LineReference", "Edge1"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
            (
                "Ambiguous Part Mirror",
                _definition(
                    ("RollbackSource",),
                    _reference_plane("AmbiguousReference"),
                ),
                "NATIVE_MODEL_INVALID",
            ),
        )
        for label, definition, error_code in failure_cases:
            before = tuple(obj.Name for obj in document.Objects)
            response = native_call(
                {"operation": "mirror", "label": label, "definition": definition},
                succeeds=False,
            )
            assert response["error_code"] == error_code
            assert tuple(obj.Name for obj in document.Objects) == before
            assert document.HasPendingTransaction is False
            assert bool(sources["RollbackSource"].Visibility)

        before = tuple(obj.Name for obj in document.Objects)
        assert bool(sources["RollbackSource"].Visibility)
        original_verify = runtime_module.verify_part_mirror

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Part Mirror postcondition failure.")

        runtime_module.verify_part_mirror = reject_after_creation
        try:
            rollback = native_call(
                {
                    "operation": "mirror",
                    "label": "Rollback Part Mirror",
                    "definition": _definition(
                        ("RollbackSource",),
                        _axis_plane("xy", (0.0, 0.0, 2.0)),
                    ),
                },
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_mirror = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False
        assert bool(sources["RollbackSource"].Visibility)

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-mirror-"))
        save_path = save_directory / "ModelPartMirror.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            root = document.getObject(record["root_name"])
            assert root is not None and root.TypeId == "Part::Mirroring"
            assert root.Label == record["label"]
            assert root.VibeCADTimelineRole == "operation"
            assert str(root.VibeCADDefinitionId) == record["definition_id"]
            assert str(root.DesignId) == record["design_id"]
            assert tuple(obj.Name for obj in root.VibeCADTimelineReplacedInputs) == record[
                "source_names"
            ]
            target, subelements = _link_sub(root.MirrorPlane)
            assert (target.Name if target else None, subelements) == record["mirror_plane"]
            assert all(
                _close(left, right)
                for left, right in zip(tuple(root.Base), record["base"], strict=True)
            )
            assert all(
                _close(left, right)
                for left, right in zip(tuple(root.Normal), record["normal"], strict=True)
            )
            reopened_results = []
            reopened_signatures = []
            for item in record["results"]:
                result = document.getObject(item["name"])
                assert result is not None and result.TypeId == "Part::Mirroring"
                assert str(result.Label) == item["label"]
                assert result.Source.Name == item["source_name"]
                assert str(result.VibeCADTimelineRole) == item["role"]
                if result is not root:
                    assert result.VibeCADTimelineOwner is root
                _assert_shape_signature(result.Shape, item["signature"])
                reopened_results.append(result)
                reopened_signatures.append(item["signature"])
            _assert_stable_recompute(document, reopened_results, reopened_signatures)

        for name, signature in source_signatures.items():
            source = document.getObject(name)
            assert source is not None and not bool(source.Visibility)
            _assert_shape_signature(_current_shape(source), signature)
        for name, signature in reference_signatures.items():
            reference = document.getObject(name)
            assert reference is not None and bool(reference.Visibility)
            _assert_shape_signature(_current_shape(reference), signature)
        assert document.getObject("ReferencePlane").Placement == plane_placement

        print("VIBECAD_NATIVE_MODEL_PART_MIRROR_GUI_OK", flush=True)
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
