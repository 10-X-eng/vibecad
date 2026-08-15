# SPDX-License-Identifier: LGPL-2.1-or-later

"""Real FreeCAD lifecycle gate for Native standalone Shape Builder."""

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


def _shape_signature(shape) -> dict[str, object]:
    bounds = shape.BoundBox
    points = [item.Point for item in shape.Vertexes]
    center = getattr(shape, "CenterOfMass", None)
    if center is None:
        center = App.Vector(
            sum(point.x for point in points) / len(points),
            sum(point.y for point in points) / len(points),
            sum(point.z for point in points) / len(points),
        )
    return {
        "shape_type": str(shape.ShapeType),
        "orientation": str(shape.Orientation),
        "topology": (len(shape.Vertexes), len(shape.Edges), len(shape.Faces)),
        "bounds": tuple(
            float(getattr(bounds, name))
            for name in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")
        ),
        "measures": (float(shape.Length), float(shape.Area), float(shape.Volume)),
        "center": (float(center.x), float(center.y), float(center.z)),
        "vertices": tuple(
            sorted(
                (float(vertex.x), float(vertex.y), float(vertex.z))
                for vertex in points
            )
        ),
    }


def _assert_shape_signature(shape, expected: dict[str, object]) -> None:
    actual = _shape_signature(shape)
    for key in ("shape_type", "orientation", "topology"):
        assert actual[key] == expected[key], (key, actual[key], expected[key])
    for key in ("bounds", "measures", "center", "vertices"):
        actual_values = actual[key]
        expected_values = expected[key]
        if key == "vertices":
            assert len(actual_values) == len(expected_values)
            pairs = zip(actual_values, expected_values, strict=True)
            assert all(
                all(abs(a - b) < 1.0e-7 for a, b in zip(left, right, strict=True))
                for left, right in pairs
            )
        else:
            assert all(
                abs(a - b) < 1.0e-7
                for a, b in zip(actual_values, expected_values, strict=True)
            )


def _turn() -> NativeTurnSnapshot:
    definition = model_part_capability_definition()
    surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            "model",
            1,
            "b" * 64,
            ("Part_Builder",),
            (),
            (),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(definition.name,),
        schemas=(definition.provider_schema(("builder",)),),
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    return NativeTurnSnapshot.from_provider_surface(surface)


def _publish_source(document, name: str, shape):
    obj = document.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    PartDesign.initializeDesignDefinition(obj)
    document.publishProvisionalTimelineOperationBlock(obj, (), ())
    assert document.recompute([obj], True, True) is not False
    PartDesign.finalizeDesignDefinition(obj)
    assert PartGui.isModelingObjectActive(obj)
    return obj


def _sources(document) -> dict[str, object]:
    planar_points = Part.makeCompound(
        [
            Part.Vertex(App.Vector(0, 0, 0)),
            Part.Vertex(App.Vector(8, 0, 0)),
            Part.Vertex(App.Vector(8, 6, 0)),
            Part.Vertex(App.Vector(0, 6, 0)),
        ]
    )
    nonplanar_points = Part.makeCompound(
        [
            Part.Vertex(App.Vector(20, 0, 0)),
            Part.Vertex(App.Vector(28, 0, 1)),
            Part.Vertex(App.Vector(28, 6, 0)),
            Part.Vertex(App.Vector(20, 6, -1)),
        ]
    )
    rectangle = Part.makePolygon(
        [
            App.Vector(0, 15, 0),
            App.Vector(8, 15, 0),
            App.Vector(8, 21, 0),
            App.Vector(0, 21, 0),
        ],
        True,
    )
    nonplanar_wire = Part.makePolygon(
        [
            App.Vector(20, 15, 0),
            App.Vector(28, 15, 1),
            App.Vector(28, 21, 0),
            App.Vector(20, 21, -1),
        ],
        True,
    )
    box = Part.makeBox(8, 7, 6, App.Vector(0, 30, 0))
    shell = Part.Shell(list(Part.makeBox(6, 5, 4, App.Vector(20, 30, 0)).Faces))
    sources = {
        "PointA": _publish_source(
            document,
            "PointA",
            Part.makeCompound([Part.Vertex(App.Vector(-5, 0, 0))]),
        ),
        "PointB": _publish_source(
            document,
            "PointB",
            Part.makeCompound([Part.Vertex(App.Vector(-1, 3, 2))]),
        ),
        "PointACopy": _publish_source(
            document,
            "PointACopy",
            Part.makeCompound([Part.Vertex(App.Vector(-5, 0, 0))]),
        ),
        "PlanarPoints": _publish_source(document, "PlanarPoints", planar_points),
        "NonplanarPoints": _publish_source(
            document,
            "NonplanarPoints",
            nonplanar_points,
        ),
        "RectangleEdges": _publish_source(document, "RectangleEdges", rectangle),
        "NonplanarEdges": _publish_source(
            document,
            "NonplanarEdges",
            nonplanar_wire,
        ),
        "BoxFaces": _publish_source(document, "BoxFaces", box),
        "ClosedShell": _publish_source(document, "ClosedShell", shell),
    }
    assert document.recompute() is not False
    return sources


def _create_sources_in_transaction(document) -> tuple[dict[str, object], str]:
    document.openTransaction("Create Shape Builder gate sources")
    try:
        sources = _sources(document)
        stale = _publish_source(
            document,
            "TransientPoint",
            Part.makeCompound([Part.Vertex(App.Vector(100, 0, 0))]),
        )
        stale_name = stale.Name
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    document.openTransaction("Delete Shape Builder stale source")
    try:
        document.removeObject(stale_name)
        document.commitTransaction()
    except Exception:
        document.abortTransaction()
        raise
    assert document.getObject(stale_name) is None
    return sources, stale_name


def _groups(name: str, *subelements: str) -> list[dict[str, object]]:
    return [{"object_name": name, "subelements": list(subelements)}]


def _cases() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "Gate Edge",
            "definition": {
                "kind": "edge_from_vertices",
                "inputs": [
                    {"object_name": "PointA", "subelements": ["Vertex1"]},
                    {"object_name": "PointB", "subelements": ["Vertex1"]},
                ],
            },
        },
        {
            "label": "Gate Wire",
            "definition": {
                "kind": "wire_from_edges",
                "inputs": _groups("RectangleEdges", "Edge1", "Edge2", "Edge3", "Edge4"),
            },
        },
        {
            "label": "Gate Planar Vertex Face",
            "definition": {
                "kind": "face_from_vertices",
                "inputs": _groups(
                    "PlanarPoints",
                    "Vertex1",
                    "Vertex2",
                    "Vertex3",
                    "Vertex4",
                ),
                "planar": True,
            },
        },
        {
            "label": "Gate Filled Vertex Face",
            "definition": {
                "kind": "face_from_vertices",
                "inputs": _groups(
                    "NonplanarPoints",
                    "Vertex1",
                    "Vertex2",
                    "Vertex3",
                    "Vertex4",
                ),
                "planar": False,
            },
        },
        {
            "label": "Gate Planar Edge Face",
            "definition": {
                "kind": "face_from_edges",
                "inputs": _groups("RectangleEdges", "Edge1", "Edge2", "Edge3", "Edge4"),
                "planar": True,
            },
        },
        {
            "label": "Gate Filled Edge Face",
            "definition": {
                "kind": "face_from_edges",
                "inputs": _groups("NonplanarEdges", "Edge1", "Edge2", "Edge3", "Edge4"),
                "planar": False,
            },
        },
        {
            "label": "Gate Selected Shell",
            "definition": {
                "kind": "shell_from_faces",
                "inputs": _groups("BoxFaces", "Face1", "Face3"),
                "all_faces": False,
                "refine": False,
            },
        },
        {
            "label": "Gate Complete Refined Shell",
            "definition": {
                "kind": "shell_from_faces",
                "inputs": _groups("BoxFaces", "Face1"),
                "all_faces": True,
                "refine": True,
            },
        },
        {
            "label": "Gate Solid",
            "definition": {
                "kind": "solid_from_shell",
                "source": {"object_name": "ClosedShell"},
                "refine": False,
            },
        },
        {
            "label": "Gate Refined Solid",
            "definition": {
                "kind": "solid_from_shell",
                "source": {"object_name": "ClosedShell"},
                "refine": True,
            },
        },
    )


def _assert_live_human_contract(document) -> None:
    before = tuple(obj.Name for obj in document.Objects)
    Gui.runCommand("Part_Builder", 0)
    _process_events(32)
    window = Gui.getMainWindow()
    radio_names = (
        "radioButtonEdgeFromVertex",
        "radioButtonWireFromEdge",
        "radioButtonFaceFromVertex",
        "radioButtonFaceFromEdge",
        "radioButtonShellFromFace",
        "radioButtonSolidFromShell",
    )
    expected_labels = (
        "Edge from vertices",
        "Wire from edges",
        "Face from vertices",
        "Face from edges",
        "Shell from faces",
        "Solid from shell",
    )
    radios = [window.findChild(QtWidgets.QRadioButton, name) for name in radio_names]
    assert all(radio is not None for radio in radios)
    assert tuple(radio.text() for radio in radios) == expected_labels
    controls = {
        name: window.findChild(QtWidgets.QCheckBox, name)
        for name in ("checkPlanar", "checkRefine", "checkFaces")
    }
    assert all(control is not None for control in controls.values())
    enablement = (
        (False, False, False),
        (True, False, False),
        (True, False, False),
        (True, False, False),
        (False, True, True),
        (False, True, False),
    )
    for radio, expected in zip(radios, enablement, strict=True):
        radio.click()
        _process_events(4)
        actual = tuple(
            controls[name].isEnabled()
            for name in ("checkPlanar", "checkRefine", "checkFaces")
        )
        assert actual == expected
    assert controls["checkRefine"].isChecked() is True
    Gui.Control.closeDialog()
    _process_events(16)
    assert tuple(obj.Name for obj in document.Objects) == before


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    save_directory = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeModelPartBuilderGate")
        VibeGui._connect_document_observer()
        _process_events()
        _assert_live_human_contract(document)
        sources, stale_name = _create_sources_in_transaction(document)
        source_breps = {
            name: obj.Shape.exportBrepToString() for name, obj in sources.items()
        }
        source_signatures = {
            name: _shape_signature(obj.Shape) for name, obj in sources.items()
        }

        service = get_service()
        service.select_modeling_engine("native")
        state = service.native_document_state_store()
        ledger = NativeAssistantUndoLedger()
        ledger.begin_run("native-model-part-builder-gui")
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
        dispatcher = NativeTurnDispatcher(
            document=document,
            state=state,
            registry=build_native_capability_registry(),
            turn=turn,
            runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument,
        )
        call_number = 0

        def native_call(arguments, *, succeeds=True):
            nonlocal call_number
            call_number += 1
            response = dispatcher.call(
                "model.part",
                json.dumps(arguments, separators=(",", ":")),
                f"model-part-builder-call-{call_number}",
            )
            assert response.get("ok") is succeeds, (arguments.get("label"), response)
            return response

        before_invalid = tuple(obj.Name for obj in document.Objects)
        invalid_schema = native_call(
            {
                "operation": "builder",
                "definition": {
                    "kind": "solid_from_shell",
                    "source": {"object_name": "ClosedShell"},
                    "refine": False,
                },
            },
            succeeds=False,
        )
        assert invalid_schema["error_code"] == "NATIVE_ARGUMENTS_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before_invalid

        records = []
        metric_by_type = {
            "Edge": "length_mm",
            "Wire": "length_mm",
            "Face": "area_mm2",
            "Shell": "area_mm2",
            "Solid": "volume_mm3",
        }
        for case in _cases():
            response = native_call({"operation": "builder", **case})
            metric = metric_by_type[response["shape_type"]]
            assert set(response) == {
                "ok",
                "object",
                "builder_kind",
                "shape_type",
                "source_count",
                "selected_element_count",
                "vertex_count",
                "edge_count",
                "face_count",
                metric,
                "receipt",
                "assistant_undo_available",
            }
            assert response["builder_kind"] == case["definition"]["kind"]
            assert response[metric] > 0.0
            assert response["assistant_undo_available"] is True
            assert len(response["receipt"]["created"]) == 1
            assert response["receipt"]["changed"] == []
            assert response["receipt"]["deleted"] == []
            object_name = response["object"]["object_name"]
            obj = document.getObject(object_name)
            assert obj is not None and obj.TypeId == "Part::Feature"
            assert obj.Label == case["label"]
            assert obj.getParentGeoFeatureGroup() is None
            assert obj.VibeCADTimelineRole == "operation"
            assert str(obj.VibeCADDefinitionId) and str(obj.DesignId)
            brep = obj.Shape.exportBrepToString()

            document.undo()
            _process_events()
            assert document.getObject(object_name) is None
            document.redo()
            _process_events()
            obj = document.getObject(object_name)
            assert obj is not None and obj.Shape.exportBrepToString() == brep

            records.append(
                {
                    "name": object_name,
                    "label": case["label"],
                    "kind": case["definition"]["kind"],
                    "definition_id": str(obj.VibeCADDefinitionId),
                    "design_id": str(obj.DesignId),
                    "shape_type": response["shape_type"],
                    "topology": (
                        response["vertex_count"],
                        response["edge_count"],
                        response["face_count"],
                    ),
                    "signature": _shape_signature(obj.Shape),
                }
            )

        assert records[2]["shape_type"] == records[3]["shape_type"] == "Face"
        assert records[4]["shape_type"] == records[5]["shape_type"] == "Face"
        assert records[6]["shape_type"] == records[7]["shape_type"] == "Shell"
        assert records[8]["shape_type"] == records[9]["shape_type"] == "Solid"
        changed_sources = [
            name
            for name, obj in sources.items()
            if obj.Shape.exportBrepToString() != source_breps[name]
        ]
        assert not changed_sources, changed_sources

        before = tuple(obj.Name for obj in document.Objects)
        invalid_geometry = native_call(
            {
                "operation": "builder",
                "label": "Invalid Vertex Edge",
                "definition": {
                    "kind": "edge_from_vertices",
                    "inputs": [
                        {"object_name": "PointA", "subelements": ["Vertex1"]},
                        {
                            "object_name": "PointACopy",
                            "subelements": ["Vertex1"],
                        },
                    ],
                },
            },
            succeeds=False,
        )
        assert invalid_geometry["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        before = tuple(obj.Name for obj in document.Objects)
        stale_failure = native_call(
            {
                "operation": "builder",
                "label": "Stale Edge",
                "definition": {
                    "kind": "edge_from_vertices",
                    "inputs": [
                        {"object_name": stale_name, "subelements": ["Vertex1"]},
                        {"object_name": "PointA", "subelements": ["Vertex1"]},
                    ],
                },
            },
            succeeds=False,
        )
        assert stale_failure["error_code"] == "NATIVE_TARGET_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        original_verify = runtime_module.verify_part_builder_shape

        def reject_after_creation(_document, _draft):
            raise NativeModelError("Forced exact Shape Builder postcondition failure.")

        runtime_module.verify_part_builder_shape = reject_after_creation
        try:
            rollback = native_call(
                {"operation": "builder", **_cases()[0], "label": "Rollback Edge"},
                succeeds=False,
            )
        finally:
            runtime_module.verify_part_builder_shape = original_verify
        assert rollback["error_code"] == "NATIVE_MODEL_INVALID"
        assert tuple(obj.Name for obj in document.Objects) == before
        assert document.HasPendingTransaction is False

        save_directory = Path(tempfile.mkdtemp(prefix="vibecad-native-part-builder-"))
        save_path = save_directory / "ModelPartBuilder.FCStd"
        document.saveAs(str(save_path))
        saved_name = document.Name
        App.closeDocument(saved_name)
        document = App.openDocument(str(save_path))
        assert document.recompute() is not False
        _process_events()

        for record in records:
            obj = document.getObject(record["name"])
            assert obj is not None and obj.TypeId == "Part::Feature"
            assert obj.Label == record["label"]
            assert obj.getParentGeoFeatureGroup() is None
            assert obj.VibeCADTimelineRole == "operation"
            assert str(obj.VibeCADDefinitionId) == record["definition_id"]
            assert str(obj.DesignId) == record["design_id"]
            assert obj.Shape.ShapeType == record["shape_type"]
            _assert_shape_signature(obj.Shape, record["signature"])
            assert (
                len(obj.Shape.Vertexes),
                len(obj.Shape.Edges),
                len(obj.Shape.Faces),
            ) == record["topology"]
        for name, signature in source_signatures.items():
            _assert_shape_signature(document.getObject(name).Shape, signature)

        print("VIBECAD_NATIVE_MODEL_PART_BUILDER_GUI_OK", flush=True)
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
