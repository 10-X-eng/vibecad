# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import VibeCADNativeSnapshot as snapshot_module
import VibeCADNativeAssemblySnapshot as assembly_snapshot_module
import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module
import VibeCADNativeDrawingSnapshot as drawing_snapshot_module
import VibeCADNativeModelSnapshot as model_snapshot_module
import VibeCADNativeManufactureSnapshot as manufacture_snapshot_module
import VibeCADNativeMeshSnapshot as mesh_snapshot_module
import VibeCADNativeSketchSnapshot as sketch_snapshot_module
from VibeCADNativeManufactureReadiness import resolve_active_job
from VibeCADNativeSnapshot import (
    NativeSnapshotError,
    build_active_snapshot,
    capture_active_snapshot_base,
    concise_object,
    complete_active_snapshot,
)


class _Object:
    def __init__(self, document, name: str, type_id: str):
        self.Document = document
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.State = []
        self.ViewObject = SimpleNamespace(Visibility=True)
        self.getParentGroup = lambda: None
        self.getParentGeoFeatureGroup = lambda: None

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected

    def isValid(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _snapshot_runtime_stubs(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "PartDesign",
        SimpleNamespace(validateDesign=lambda _sketch: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "TechDrawGui",
        SimpleNamespace(drawingLineAttributes=lambda _view: []),
    )
    monkeypatch.setattr(
        drawing_snapshot_module,
        "drawing_line_defaults_state",
        lambda: {
            "state_sha256": "0" * 64,
            "scope": "application_session",
            "line_standard": "ISO",
            "standards_body": "ISO",
            "line_number": 1,
            "style_code": 0,
            "style_name": "Continuous",
            "width_mm": 0.25,
            "width_choice": "thin",
            "available_widths": {
                "thin_mm": 0.25,
                "middle_mm": 0.5,
                "thick_mm": 0.7,
            },
            "color_rgb": {"red": 0.0, "green": 0.0, "blue": 0.0},
            "visible": True,
            "cascade_spacing_mm": 5.0,
            "delta_distance_mm": 2.0,
            "available_style_count": 1,
            "valid": True,
            "issues": [],
        },
    )


class _Document:
    Uid = "document-a"
    Name = "DocumentA"

    def __init__(self):
        self.Objects = []

    def add(self, name: str, type_id: str):
        value = _Object(self, name, type_id)
        value.ID = len(self.Objects) + 1
        self.Objects.append(value)
        return value

    def getObject(self, name: str):
        return next((value for value in self.Objects if value.Name == name), None)

    def getBookedTransactionID(self) -> int:
        return 0


class _ReadTouchObject:
    def __init__(self, document, *, touched: bool = False):
        self.Document = document
        self.Name = "ToolBit"
        self.TypeId = "Part::FeaturePython"
        self.State = ["Touched"] if touched else []

    @property
    def Label(self):
        if "Touched" not in self.State:
            self.State.append("Touched")
        return "Tool Bit"

    def purgeTouched(self):
        self.State = [value for value in self.State if value != "Touched"]


def _document() -> _Document:
    document = _Document()
    feature = document.add("Pad", "PartDesign::Feature")
    feature.Shape = SimpleNamespace(Solids=[1], Faces=[1] * 6, Edges=[1] * 12)
    body = document.add("Body", "PartDesign::Body")
    body.Group = [feature]
    body.Tip = feature
    component = document.add("Component", "App::Part")
    component.Group = [body]
    sketch = document.add("Sketch", "Sketcher::SketchObject")
    sketch.GeometryCount = 2
    sketch.Constraints = [1]
    sketch.MapMode = "FlatFace"
    sketch.Support = (feature, ["Face1"])
    sketch.FullyConstrained = False
    sketch.getConstruction = lambda index: index == 1

    assembly = document.add("Assembly", "Assembly::AssemblyObject")
    occurrence = document.add("Occurrence", "App::Link")
    joint_group = document.add("Joints", "Assembly::JointGroup")
    joint = document.add("Joint", "Assembly::JointObject")
    joint.ObjectToGround = None
    joint_group.Group = [joint]
    assembly.Group = [occurrence, joint_group]

    mesh = document.add("Mesh", "Mesh::Feature")
    mesh.Mesh = SimpleNamespace(CountPoints=8, CountEdges=18, CountFacets=12)
    analysis = document.add("Analysis", "Fem::FemAnalysis")
    solver = document.add("Solver", "Fem::FemSolverObjectPython")
    constraint = document.add("Constraint", "Fem::ConstraintFixed")
    analysis.Group = [solver, constraint]

    object_job = type("ObjectJob", (), {})
    object_job.__module__ = "Path.Main.Job"
    job = document.add("Job", "Path::FeaturePython")
    job.Proxy = object_job()
    operation = document.add("Profile", "Path::FeaturePython")
    operation.Active = True
    job.Operations = SimpleNamespace(Group=[operation])
    job.Tools = SimpleNamespace(Group=[])
    job.Model = SimpleNamespace(Group=[body])
    job.PostProcessor = "linuxcnc"

    page = document.add("Page", "TechDraw::DrawPage")
    view = document.add("View", "TechDraw::DrawViewPart")
    view.Source = [feature]
    view.X = 20.0
    view.Y = 30.0
    view.Scale = 1.0
    page.Views = [view]
    page.Template = None

    sheet = document.add("Parameters", "Spreadsheet::Sheet")
    sheet.getNonEmptyCells = lambda: ["A1", "B2"]
    sheet.getAlias = lambda cell: "width" if cell == "A1" else ""
    sheet.getContents = lambda cell: "42 mm" if cell == "A1" else "=A1 * 2"
    feature.ExpressionEngine = [("Length", "Parameters.width")]
    return document


def test_drawing_base_context_omits_hidden_and_fem_working_objects() -> None:
    document = _Document()
    visible = document.add("VisibleBody", "PartDesign::Body")
    hidden = document.add("HiddenBody", "PartDesign::Body")
    fem = document.add("FEMMeshGmsh", "Fem::FemMeshShapeBaseObjectPython")
    for obj, shown in ((visible, True), (hidden, False), (fem, True)):
        obj.ViewObject = SimpleNamespace(Visibility=shown)
        obj.getParentGroup = lambda: None
        obj.getParentGeoFeatureGroup = lambda: None
    selection = {
        "document_uid": document.Uid,
        "selected_count": 3,
        "items": [
            {
                "object": {
                    "document_uid": document.Uid,
                    "object_name": obj.Name,
                    "type_id": obj.TypeId,
                },
                "subelements": [],
            }
            for obj in (visible, hidden, fem)
        ],
    }
    native_state = {
        "document_uid": document.Uid,
        "structural_revision": 7,
        "recent_receipts": [
            {
                "created": [
                    {
                        "document_uid": document.Uid,
                        "object_name": obj.Name,
                        "type_id": obj.TypeId,
                    }
                    for obj in (visible, hidden, fem)
                ]
            }
        ],
    }

    result = capture_active_snapshot_base(
        document,
        "drawing",
        native_state,
        selection=selection,
    )

    assert [item["object"]["object_name"] for item in result["selection"]["items"]] == [
        "VisibleBody"
    ]
    assert result["selection"]["selected_count"] == 1
    assert [item["object_name"] for item in result["working_set"]] == [
        "VisibleBody"
    ]


def test_analyze_base_context_keeps_fem_graph_but_omits_hidden_geometry() -> None:
    document = _Document()
    visible = document.add("VisibleBody", "PartDesign::Body")
    hidden = document.add("HiddenBody", "PartDesign::Body")
    suppressed = document.add("SuppressedBody", "PartDesign::Body")
    solver = document.add("Solver", "Fem::FemSolverObjectPython")
    for obj, shown in (
        (visible, True),
        (hidden, False),
        (suppressed, True),
        (solver, False),
    ):
        obj.ViewObject = SimpleNamespace(Visibility=shown)
        obj.getParentGroup = lambda: None
        obj.getParentGeoFeatureGroup = lambda: None
    suppressed.Suppressed = True
    document.isObjectUsableAtCurrentTimelinePosition = lambda obj: obj is not suppressed
    selection = {
        "document_uid": document.Uid,
        "selected_count": 4,
        "items": [
            {
                "object": {
                    "document_uid": document.Uid,
                    "object_name": obj.Name,
                    "type_id": obj.TypeId,
                },
                "subelements": [],
            }
            for obj in (visible, hidden, suppressed, solver)
        ],
    }
    native_state = {
        "document_uid": document.Uid,
        "structural_revision": 7,
        "recent_receipts": [
            {
                "created": [
                    {
                        "document_uid": document.Uid,
                        "object_name": obj.Name,
                        "type_id": obj.TypeId,
                    }
                    for obj in (visible, hidden, suppressed, solver)
                ]
            }
        ],
    }

    result = capture_active_snapshot_base(
        document,
        "analyze",
        native_state,
        selection=selection,
    )

    assert [item["object"]["object_name"] for item in result["selection"]["items"]] == [
        "VisibleBody",
        "Solver",
    ]
    assert [item["object_name"] for item in result["working_set"]] == [
        "VisibleBody",
        "Solver",
    ]


def test_detached_drawing_sources_preserve_the_snapshot_bound() -> None:
    sources = [
        {"object_name": f"Body{index}", "type_id": "PartDesign::Body"}
        for index in range(drawing_snapshot_module.MAX_DRAWING_SOURCES + 2)
    ]

    count, bounded = drawing_snapshot_module._drawing_sources(
        None,
        detached_sources=sources,
    )

    assert count == len(sources)
    assert len(bounded) == drawing_snapshot_module.MAX_DRAWING_SOURCES


def _state() -> dict:
    return {
        "document_uid": "document-a",
        "structural_revision": 7,
        "recent_receipts": [
            {
                "created": [
                    {
                        "document_uid": "document-a",
                        "object_name": "Mesh",
                        "type_id": "Mesh::Feature",
                    }
                ],
                "changed": [],
                "deleted": [],
                "replaced": [],
            }
        ],
    }


def test_concise_object_restores_a_read_induced_touch() -> None:
    obj = _ReadTouchObject(_Document())

    result = concise_object(obj)

    assert result == {
        "document_uid": "document-a",
        "object_name": "ToolBit",
        "type_id": "Part::FeaturePython",
        "label": "Tool Bit",
    }
    assert obj.State == []


def test_concise_object_preserves_a_preexisting_touch() -> None:
    obj = _ReadTouchObject(_Document(), touched=True)

    result = concise_object(obj)

    assert result["state"] == ["Touched"]
    assert obj.State == ["Touched"]


@pytest.mark.parametrize(
    ("surface_id", "kind"),
    (
        ("model", "model"),
        ("sketch.setup", "sketch"),
        ("sketch.edit", "sketch"),
        ("assemble", "assembly"),
        ("mesh", "mesh"),
        ("analyze", "analyze"),
        ("manufacture", "manufacture"),
        ("drawing", "drawing"),
        ("parameters", "parameters"),
        ("aero", "aero"),
    ),
)
def test_each_surface_builds_only_its_live_domain(
    surface_id: str,
    kind: str,
    monkeypatch,
) -> None:
    document = _document()
    monkeypatch.setattr(
        assembly_snapshot_module,
        "read_active_assembly",
        lambda _document: None,
    )
    if surface_id == "sketch.edit":
        monkeypatch.setattr(
            sketch_snapshot_module,
            "build_sketch_snapshot",
            lambda _document, _surface_id, *, selection=None: {
                "kind": "sketch",
                "context": "edit",
                "revision": "sketch-v1:" + ("a" * 64),
                "active_sketch": {"object_name": "Sketch"},
            },
        )
    if surface_id == "analyze":
        monkeypatch.setattr(
            analyze_snapshot_module,
            "build_analyze_snapshot",
            lambda _document, *, background_job=None: {
                "kind": "analyze",
                "run_status": {
                    "phase": "idle" if background_job is None else "queued"
                },
            },
        )
    if surface_id == "manufacture":
        monkeypatch.setattr(
            manufacture_snapshot_module,
            "capture_job_creation_environment",
            lambda: SimpleNamespace(
                summary=lambda: {
                    "state_sha256": "a" * 64,
                    "template_count": 0,
                    "templates": [],
                    "templates_truncated": False,
                    "default_template_id": None,
                }
            ),
        )
        monkeypatch.setattr(
            manufacture_snapshot_module,
            "capture_tool_catalog",
            lambda: SimpleNamespace(
                page=lambda _offset, _page_size: {
                    "state_sha256": "b" * 64,
                    "count": 0,
                    "offset": 0,
                    "items": [],
                    "next_offset": None,
                }
            ),
        )
    selection = {
        "document_uid": "document-a",
        "selected_count": 1,
        "items": [
            {
                "object": {
                    "document_uid": "document-a",
                    "object_name": "Body",
                    "type_id": "PartDesign::Body",
                }
            }
        ],
    }

    result = build_active_snapshot(
        document,
        surface_id,
        _state(),
        selection=selection,
    )

    assert result["surface_id"] == surface_id
    assert result["structural_revision"] == 7
    assert result["domain"]["kind"] == kind
    if surface_id == "sketch.edit":
        assert result["revision"] == "sketch-v1:" + ("a" * 64)
        assert "revision" not in result["domain"]
    assert [item["object_name"] for item in result["working_set"]] == [
        "Body",
        "Mesh",
    ]
    assert result["selection"] == selection


def test_drawing_snapshot_builder_receives_the_structural_revision(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        drawing_snapshot_module,
        "build_drawing_snapshot",
        lambda document, **options: captured.append((document, options))
        or {"kind": "drawing"},
    )
    document = object()
    selection = {"document_uid": "document-a", "items": []}

    result = snapshot_module._domain_builder(
        "drawing",
        selection=selection,
        structural_revision=12,
    )(document)

    assert result == {"kind": "drawing"}
    assert captured == [
        (
            document,
            {"selection": selection, "structural_revision": 12},
        )
    ]


def test_working_set_rebuild_ignores_deleted_receipt_targets() -> None:
    document = _document()
    state = _state()
    state["recent_receipts"][0]["created"][0]["object_name"] = "DeletedObject"
    selection = {
        "document_uid": "document-a",
        "selected_count": 0,
        "items": [],
    }

    result = build_active_snapshot(document, "model", state, selection=selection)

    assert result["working_set"] == []
    assert "selection" not in result


def test_mesh_snapshot_prioritizes_selected_mesh_beyond_inventory_page() -> None:
    document = _Document()
    for index in range(40):
        mesh = document.add(f"Mesh{index:02d}", "Mesh::Feature")
        mesh.Mesh = SimpleNamespace(
            CountPoints=8 + index,
            CountEdges=18 + index,
            CountFacets=12 + index,
        )
    selected = document.getObject("Mesh39")
    selection = {
        "document_uid": "document-a",
        "selected_count": 1,
        "items": [
            {
                "object": {
                    "document_uid": "document-a",
                    "object_name": selected.Name,
                    "type_id": selected.TypeId,
                }
            }
        ],
    }
    state = {
        "document_uid": "document-a",
        "structural_revision": 1,
        "recent_receipts": [],
    }

    result = build_active_snapshot(document, "mesh", state, selection=selection)

    domain = result["domain"]
    assert domain["counts"]["mesh"] == 40
    assert domain["truncated"] is True
    assert domain["total_objects"] == 40
    assert len(domain["objects"]) == 32
    assert domain["objects"][0]["object_name"] == selected.Name
    assert domain["objects"][0]["topology"] == {
        "points": 47,
        "edges": 57,
        "facets": 51,
    }
    assert len(domain["objects"][0]["state_sha256"]) == 64


def test_mesh_snapshot_identifies_converted_shape_representation() -> None:
    document = _Document()
    converted = document.add("ConvertedMesh", "MeshPart::ShapeFromMesh")
    converted.Shape = SimpleNamespace(
        ShapeType="Shell",
        Solids=[],
        Shells=[object()],
        Faces=[object()] * 12,
        Wires=[],
        Edges=[object()] * 18,
        Vertexes=[object()] * 8,
    )

    result = mesh_snapshot_module.build_mesh_snapshot(document)

    assert result["objects"][0]["shape_type"] == "Shell"
    assert result["objects"][0]["topology"]["solids"] == 0
    assert result["objects"][0]["topology"]["shells"] == 1


def test_mesh_snapshot_includes_active_design_shape_sources(monkeypatch) -> None:
    document = _Document()
    source = document.add("SourceBox", "Part::Box")
    source.Shape = SimpleNamespace(
        ShapeType="Solid",
        Solids=[object()],
        Shells=[object()],
        Faces=[object()] * 6,
        Wires=[],
        Edges=[object()] * 12,
        Vertexes=[object()] * 8,
    )
    monkeypatch.setattr(
        mesh_snapshot_module,
        "active_design_geometry_sources",
        lambda exact_document: (source,) if exact_document is document else (),
        raising=False,
    )

    result = mesh_snapshot_module.build_mesh_snapshot(document)

    assert result["counts"]["shape"] == 1
    assert [item["object_name"] for item in result["objects"]] == [source.Name]
    assert result["objects"][0]["shape_type"] == "Solid"


def test_mesh_snapshot_omits_replaced_sources_from_domain_and_working_set(
    monkeypatch,
) -> None:
    document = _Document()
    source_a = document.add("MeshA", "Mesh::Feature")
    source_b = document.add("MeshB", "Mesh::Feature")
    result_mesh = document.add("Combined", "Mesh::Merge")
    for obj, facets in ((source_a, 4), (source_b, 4), (result_mesh, 8)):
        obj.Mesh = SimpleNamespace(
            CountPoints=facets,
            CountEdges=facets + 2,
            CountFacets=facets,
        )
    monkeypatch.setattr(
        mesh_snapshot_module,
        "mesh_object_is_context_active",
        lambda obj: obj is result_mesh,
        raising=False,
    )
    state = {
        "document_uid": "document-a",
        "structural_revision": 2,
        "recent_receipts": [
            {
                "created": [{"object_name": result_mesh.Name}],
                "changed": [],
                "replaced": [
                    {"object_name": source_a.Name},
                    {"object_name": source_b.Name},
                ],
            }
        ],
    }

    snapshot = build_active_snapshot(
        document,
        "mesh",
        state,
        selection={"document_uid": "document-a", "items": []},
    )

    assert snapshot["domain"]["counts"]["mesh"] == 1
    assert [item["object_name"] for item in snapshot["domain"]["objects"]] == [
        result_mesh.Name
    ]
    assert [item["object_name"] for item in snapshot["working_set"]] == [
        result_mesh.Name
    ]


def test_model_snapshot_exposes_meshes_needed_by_model_surface_tools() -> None:
    document = _document()
    no_selection = {"document_uid": "document-a", "items": []}

    result = build_active_snapshot(
        document,
        "model",
        _state(),
        selection=no_selection,
    )

    assert result["domain"]["counts"]["meshes"] == 1
    assert result["domain"]["meshes"] == [
        {
            "document_uid": "document-a",
            "object_name": "Mesh",
            "type_id": "Mesh::Feature",
            "points": 8,
            "facets": 12,
            "visible": False,
        }
    ]


def test_model_snapshot_exposes_exact_sketch_feature_readiness(monkeypatch) -> None:
    document = _document()
    sketch = document.getObject("Sketch")
    observed = []
    monkeypatch.setattr(
        model_snapshot_module,
        "sketch_readiness",
        lambda exact_document, exact_target: observed.append(
            (exact_document, dict(exact_target))
        )
        or {
            "fully_constrained": False,
            "profile": {
                "wire_count": 2,
                "closed_wire_count": 2,
                "open_wire_count": 0,
            },
            "valid": True,
            "surface_feature_ready": True,
            "solid_feature_ready": True,
        },
        raising=False,
    )

    sketch.ViewObject = SimpleNamespace(Visibility=True)
    summary = model_snapshot_module._sketch_summary(sketch)
    assert summary["fully_constrained"] is False
    assert summary["profile"] == {
        "wire_count": 2,
        "closed_wire_count": 2,
        "open_wire_count": 0,
    }
    assert summary["valid"] is True
    assert summary["surface_feature_ready"] is True
    assert summary["solid_feature_ready"] is True
    assert observed == [
        (
            document,
            {
                "document_uid": document.Uid,
                "object_name": sketch.Name,
            },
        )
    ]


def test_model_snapshot_exposes_exact_editable_standard_fastener_definition(
    monkeypatch,
) -> None:
    document = _document()
    body = document.getObject("Body")
    operation = document.add("FastenerFeature", "PartDesign::DesignGeneratedOperation")
    operation.GeneratorKind = "standard-fastener"
    state = document.add("FastenerState", "PartDesign::DesignBodyState")
    state.Operation = operation
    publication = document.add(
        "FastenerPublication",
        "PartDesign::DesignBodyPublication",
    )
    publication.CurrentState = state
    body.Tip = publication
    graph = SimpleNamespace(
        body=body,
        operation=operation,
        identity={
            "part_number": "ISO4762 M3x10",
            "canonical_key": "freecad-fasteners:exact",
            "standard": "ISO4762",
            "nominal_size": "M3",
            "length_mm": 10.0,
            "model_thread": False,
            "left_handed": False,
            "options": {},
        },
    )
    monkeypatch.setattr(
        model_snapshot_module,
        "model_fastener_graph_from_body",
        lambda exact_document, exact_body: (
            graph
            if exact_document is document and exact_body is body
            else pytest.fail("wrong standard-fastener snapshot target")
        ),
    )

    result = build_active_snapshot(
        document,
        "model",
        _state(),
        selection={"document_uid": "document-a", "items": []},
    )

    assert result["domain"]["counts"]["standard_fasteners"] == 1
    assert result["domain"]["standard_fasteners"] == [
        {
            "body": {
                "document_uid": "document-a",
                "object_name": "Body",
                "type_id": "PartDesign::Body",
            },
            "operation": {
                "document_uid": "document-a",
                "object_name": "FastenerFeature",
                "type_id": "PartDesign::DesignGeneratedOperation",
            },
            "part_number": "ISO4762 M3x10",
            "canonical_key": "freecad-fasteners:exact",
            "definition": {
                "standard": "ISO4762",
                "nominal_thread": "M3",
                "length_mm": 10.0,
                "model_thread": False,
                "left_handed": False,
                "catalog_option_overrides": {},
            },
        }
    ]


def test_model_snapshot_exposes_component_lcs_and_published_interface(monkeypatch) -> None:
    document = _document()
    body = document.getObject("Body")
    lcs = document.add("MountLCS", "PartDesign::CoordinateSystem")
    body.Group.append(lcs)
    monkeypatch.setattr(
        model_snapshot_module,
        "native_interface_definitions",
        lambda component: (
            {
                "MountAxis": {
                    "selection": {"type": "frame", "native_lcs": lcs.Name},
                    "connector": {
                        "kind": "axis",
                        "allowed_joints": ["revolute", "fixed"],
                        "compatibility": "mount-v1",
                    },
                    "resolved": {
                        "connector_frame": {
                            "origin_mm": [1.0, 2.0, 3.0],
                            "axis_direction": [0.0, 1.0, 0.0],
                            "x_direction": [1.0, 0.0, 0.0],
                        }
                    },
                }
            }
            if component is body
            else {}
        ),
    )

    result = build_active_snapshot(
        document,
        "model",
        _state(),
        selection={"document_uid": "document-a", "items": []},
    )

    summary = next(
        item for item in result["domain"]["bodies"] if item["object_name"] == body.Name
    )
    assert summary["local_coordinate_systems"] == [
        {
            "document_uid": "document-a",
            "object_name": "MountLCS",
            "type_id": "PartDesign::CoordinateSystem",
            "published_interface": "MountAxis",
        }
    ]
    assert summary["published_interfaces"] == [
        {
            "name": "MountAxis",
            "kind": "axis",
            "allowed_joints": ["revolute", "fixed"],
            "compatibility": "mount-v1",
            "lcs": {
                "document_uid": "document-a",
                "object_name": "MountLCS",
                "type_id": "PartDesign::CoordinateSystem",
            },
            "origin_mm": [1.0, 2.0, 3.0],
            "axis_direction": [0.0, 1.0, 0.0],
            "x_direction": [1.0, 0.0, 0.0],
        }
    ]


def test_live_state_continues_without_any_prior_tool_transcript() -> None:
    document = _document()
    empty_host_state = {
        "document_uid": "document-a",
        "structural_revision": 12,
        "recent_receipts": [],
    }
    no_selection = {"document_uid": "document-a", "items": []}

    before = build_active_snapshot(
        document,
        "model",
        empty_host_state,
        selection=no_selection,
    )
    new_feature = document.add("HumanFeature", "PartDesign::Feature")
    new_feature.Shape = SimpleNamespace(Solids=[1], Faces=[1], Edges=[1])
    after = build_active_snapshot(
        document,
        "model",
        {**empty_host_state, "structural_revision": 13},
        selection=no_selection,
    )

    assert (
        before["domain"]["counts"]["shaped_objects"] + 1
        == after["domain"]["counts"]["shaped_objects"]
    )
    assert "conversation" not in after
    assert "transcript" not in after


def test_manufacture_active_job_is_human_selected_or_unambiguous() -> None:
    document = _Document()
    first_job = document.add("FirstJob", "App::FeaturePython")
    second_job = document.add("SecondJob", "App::FeaturePython")
    first_operation = document.add("FirstOperation", "Path::Feature")
    second_operation = document.add("SecondOperation", "Path::Feature")
    for job, operation in (
        (first_job, first_operation),
        (second_job, second_operation),
    ):
        job.Model = SimpleNamespace(Group=[])
        job.Tools = SimpleNamespace(Group=[])
        job.Operations = SimpleNamespace(Group=[operation])
        job.SetupSheet = None
        job.Stock = None

    empty = {"document_uid": document.Uid, "items": []}
    assert resolve_active_job(document, (first_job,), empty) == (
        first_job,
        "only_job",
    )
    assert resolve_active_job(document, (first_job, second_job), empty) == (
        None,
        "choose_job",
    )
    first_selected = {
        "document_uid": document.Uid,
        "items": [{"object": {"object_name": first_operation.Name}}],
    }
    assert resolve_active_job(
        document,
        (first_job, second_job),
        first_selected,
    ) == (first_job, "selection")
    both_selected = {
        "document_uid": document.Uid,
        "items": [
            {"object": {"object_name": first_operation.Name}},
            {"object": {"object_name": second_operation.Name}},
        ],
    }
    assert resolve_active_job(
        document,
        (first_job, second_job),
        both_selected,
    ) == (None, "ambiguous_selection")


def test_snapshot_refuses_state_from_another_document() -> None:
    document = _document()
    with pytest.raises(NativeSnapshotError, match="another document"):
        build_active_snapshot(
            document,
            "model",
            {**_state(), "document_uid": "document-b"},
            selection={"document_uid": "document-a", "items": []},
        )


def test_oversized_drawing_snapshot_defers_repeated_page_view_details(
    monkeypatch,
) -> None:
    monkeypatch.setattr(snapshot_module, "MAX_NATIVE_SNAPSHOT_BYTES", 8 * 1024)
    base = {
        "surface_id": "drawing",
        "document": {
            "document_uid": "document-a",
            "document_name": "DocumentA",
        },
        "structural_revision": 7,
        "working_set": [],
    }
    views = [
        {
            "document_uid": "document-a",
            "object_name": f"View{index}",
            "type_id": "TechDraw::DrawViewPart",
            "label": f"Projected View {index}",
            "state_sha256": str(index).zfill(64),
            "placement": {
                "position_on_page_mm": [float(index), 25.0],
                "locked": False,
            },
            "line_attributes": {"detail": "x" * 1024},
        }
        for index in range(20)
    ]
    selected_dimensions = [{"object_name": "Dimension", "detail": "exact"}]
    domain = {
        "kind": "drawing",
        "page_count": 1,
        "pages": [
            {
                "object_name": "Page",
                "label": "Page",
                "type_id": "TechDraw::DrawPage",
                "state_sha256": "a" * 64,
                "view_count": len(views),
                "views": views,
            }
        ],
        "active_page": {"object_name": "Page", "state_sha256": "a" * 64},
        "selected_dimensions": selected_dimensions,
    }

    result = complete_active_snapshot(base, domain)

    assert len(json.dumps(result, separators=(",", ":")).encode()) <= 8 * 1024
    assert result["domain"]["snapshot_compacted"] is True
    assert result["domain"]["deferred_details"] == ["pages.views"]
    assert result["domain"]["selected_dimensions"] == selected_dimensions
    page = result["domain"]["pages"][0]
    assert page["views_detail_deferred"] is True
    assert [view["object_name"] for view in page["views"]] == [
        f"View{index}" for index in range(20)
    ]
    assert all("line_attributes" not in view for view in page["views"])


def test_oversized_unknown_domain_returns_a_bounded_deferred_manifest(
    monkeypatch,
) -> None:
    monkeypatch.setattr(snapshot_module, "MAX_NATIVE_SNAPSHOT_BYTES", 4 * 1024)
    base = {
        "surface_id": "model",
        "document": {
            "document_uid": "document-a",
            "document_name": "DocumentA",
        },
        "structural_revision": 7,
        "working_set": [],
    }
    domain = {
        "kind": "model",
        "counts": {"bodies": 200},
        "bodies": [
            {"object_name": f"Body{index}", "detail": "x" * 512}
            for index in range(200)
        ],
    }

    result = complete_active_snapshot(base, domain)

    assert len(json.dumps(result, separators=(",", ":")).encode()) <= 4 * 1024
    assert result["domain"]["kind"] == "model"
    assert result["domain"]["snapshot_truncated"] is True
    assert result["domain"]["detail_deferred"] is True
    assert result["domain"]["section_count"] == 3
    assert any(
        section["name"] == "bodies" and section["item_count"] == 200
        for section in result["domain"]["sections"]
    )
