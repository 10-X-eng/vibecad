# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import VibeCADNativeSnapshot as snapshot_module
import VibeCADNativeAssemblySnapshot as assembly_snapshot_module
import VibeCADNativeAnalyzeSnapshot as analyze_snapshot_module
import VibeCADNativeDrawingSnapshot as drawing_snapshot_module
import VibeCADNativeModelSnapshot as model_snapshot_module
import VibeCADNativeManufactureSnapshot as manufacture_snapshot_module
import VibeCADNativeSketchSnapshot as sketch_snapshot_module
from VibeCADNativeManufactureReadiness import resolve_active_job
from VibeCADNativeSnapshot import (
    NativeSnapshotError,
    build_active_snapshot,
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

    def isDerivedFrom(self, expected: str) -> bool:
        return self.TypeId == expected


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
    feature.ExpressionEngine = [("Length", "Parameters.width")]
    return document


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
                "options": {},
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
