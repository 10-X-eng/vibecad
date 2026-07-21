# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration gate for the production Assembly domain API."""

from __future__ import annotations

# Import FreeCAD before other module globals. Its embedded-module
# initialization mutates the importing frame in some built configurations.
import FreeCAD as App  # noqa: F401

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_INPUT_OBJECTS,
    PROP_OUTPUT_TYPE,
    delete_live_program,
    mark_programs_stale_from_source,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_inspection_state,
    capture_reference_inputs,
    complete_inspection,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_DOMAIN,
    PROP_PROGRAM_ID,
    PROP_PROGRAM_OUTPUT,
    PROP_PROGRAM_REVISION,
    PROP_PROGRAM_WORKBENCH,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)
from vibescript_assembly_worker import (  # noqa: E402
    AssemblyCandidateError,
    configure_assembly_references,
    validate_and_solve_assembly,
)
from vibescript_domain_api import create_domain_api  # noqa: E402
from vibescript_part_worker import part_shape_facts  # noqa: E402


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


def _placement_matrix_values(placement) -> list[float]:
    matrix = placement.toMatrix()
    return [
        float(getattr(matrix, name))
        for name in (
            "A11",
            "A12",
            "A13",
            "A14",
            "A21",
            "A22",
            "A23",
            "A24",
            "A31",
            "A32",
            "A33",
            "A34",
            "A41",
            "A42",
            "A43",
            "A44",
        )
    ]


class _Service:
    def __init__(self, document, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "AssemblyWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "assembly-production-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}

    def provider_working_set(self) -> dict:
        targets = []
        for name in ("SourceBase", "SourceArm", "NativeSubassembly"):
            obj = self.document.getObject(name)
            if obj is not None:
                targets.append(
                    {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
                )
        return {"target_count": len(targets), "targets": targets}

    @staticmethod
    def selection_summary() -> dict:
        return {"selection": []}

    @staticmethod
    def _partdesign_body_for_feature(_obj):
        return None


def _document_objects(document) -> list[dict[str, str]]:
    return [
        {
            "name": str(obj.Name),
            "label": str(obj.Label),
            "type_id": str(obj.TypeId),
        }
        for obj in document.Objects
    ]


def _assert_gui_joint_view_providers(document) -> None:
    if not App.GuiUp:
        return

    import JointObject

    for obj in document.Objects:
        if str(getattr(obj, PROP_OUTPUT_TYPE, "") or "") != "joint":
            continue
        proxy = obj.ViewObject.Proxy
        if isinstance(getattr(obj, "Proxy", None), JointObject.GroundedJoint):
            assert isinstance(proxy, JointObject.ViewProviderGroundedJoint), (
                obj.Name,
                type(proxy).__name__,
            )
            assert hasattr(proxy, "app_obj"), obj.Name
            continue
        assert isinstance(proxy, JointObject.ViewProviderJoint), (
            obj.Name,
            type(proxy).__name__,
        )
        assert hasattr(proxy, "switch_JCS1"), obj.Name
        assert hasattr(proxy, "switch_JCS2"), obj.Name
        proxy.redrawJointPlacements(obj)


def _candidate_capture(base: dict, *, operation: str, tool_name: str, arguments: dict) -> dict:
    return {
        **base,
        "operation": operation,
        "tool_name": tool_name,
        "arguments": arguments,
    }


def _prepare_and_execute(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    if prepared.get("reference_requirements") and not prepared.get("finalized"):
        prepared = finalize_candidate(
            prepared,
            capture_reference_inputs(service, prepared),
        )
    execution = execute_candidate(prepared, cancellation_check=None)
    return prepared, execution


def _run_candidate(captured: dict, service: _Service):
    prepared, execution = _prepare_and_execute(captured, service)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    _assert_gui_joint_view_providers(service._active_document())
    accepted = accept_candidate(prepared, publication)
    assert accepted["model_state"]["status"] == "accepted"
    assert accepted["model_state"]["accepted_is_current"] is True
    assert accepted["model_state"]["next_write_expected_revision"] == accepted[
        "working_revision"
    ]
    assert accepted["model_state"]["verification_call"] == {
        "tool": "core.inspect",
        "arguments": {
            "scope": "program",
            "target": prepared["program_id"],
            "path": "",
            "offset": 0,
            "limit": 50,
            "attach": False,
        },
    }
    return prepared, execution, publication, accepted


def _exercise_worker_result_tamper_rejection(
    prepared: dict, execution: dict
) -> list[str]:
    """Prove the host independently reauthorizes plausible worker metadata."""

    def output(payload: dict, name: str) -> dict:
        return next(item for item in payload["outputs"] if item["name"] == name)

    cases = []

    changed_kind = copy.deepcopy(execution)
    output(changed_kind, "Hinge")["assembly_data"]["kind"] = "fixed"
    cases.append(("joint_kind", changed_kind, "changed kind after source evaluation"))

    changed_anchor = copy.deepcopy(execution)
    changed_anchor_joint = output(changed_anchor, "Hinge")
    for connector_collection in (
        changed_anchor_joint["assembly_data"]["connectors"],
        changed_anchor_joint["connector_frames"],
    ):
        connector_collection[0]["anchor"] = "Vertex999"
        connector_collection[0]["native_reference"]["subelements"][1] = "Vertex999"
    cases.append(("connector_anchor", changed_anchor, "resolved the wrong anchor"))

    changed_readback = copy.deepcopy(execution)
    output(changed_readback, "Hinge")["assembly_data"]["native_readback"][
        "native_type"
    ] = "Fixed"
    cases.append(("native_readback", changed_readback, "native property readback disagrees"))

    changed_graph = copy.deepcopy(execution)
    output(changed_graph, "Model")["definition"]["properties"]["components"][0][
        "properties"
    ]["label"] = "Tampered Base"
    cases.append(
        (
            "assembly_graph",
            changed_graph,
            "does not match its returned definition",
        )
    )

    changed_summary = copy.deepcopy(execution)
    changed_summary["assembly_validation"]["component_placements"]["Arm"][
        "matrix"
    ][3] += 1.0
    cases.append(
        (
            "validation_summary",
            changed_summary,
            "validation field 'component_placements' is inconsistent",
        )
    )

    changed_dependencies = copy.deepcopy(execution)
    fake_issue = {
        "code": "missing_collinear_slider",
        "joint_output": "Hinge",
        "joint_type": "screw",
        "component_outputs": ["Base", "Arm"],
        "available_slider_outputs": [],
        "requirement": "tampered",
        "suggestion": "tampered",
    }
    output(changed_dependencies, "Diagnostics")["diagnostics"][
        "joint_dependency_issues"
    ] = [fake_issue]
    changed_dependencies["assembly_validation"]["joint_dependency_issues"] = [
        fake_issue
    ]
    cases.append(
        (
            "joint_dependencies",
            changed_dependencies,
            "does not match the returned joint graph",
        )
    )

    rejected = []
    for name, payload, message in cases:
        try:
            validate_candidate(prepared, payload)
        except ValueError as exc:
            assert message in str(exc), (name, str(exc))
            rejected.append(name)
        else:
            raise AssertionError(f"Host accepted tampered Assembly result {name!r}.")
    return rejected


def _worker_output(name: str, value) -> dict:
    return {
        "name": name,
        "type": value.output_type,
        "definition": value.to_payload(),
    }


def _tag_v2_output(obj, *, program_id: str, domain: str, output_name: str) -> None:
    values = {
        PROP_PROGRAM_ID: program_id,
        PROP_PROGRAM_DOMAIN: domain,
        PROP_PROGRAM_WORKBENCH: (
            "AssemblyWorkbench" if domain == "assembly" else "PartWorkbench"
        ),
        PROP_PROGRAM_REVISION: "f" * 64,
        PROP_PROGRAM_OUTPUT: output_name,
    }
    for name, value in values.items():
        obj.addProperty("App::PropertyString", name, "VibeCAD")
        setattr(obj, name, value)


def _exercise_provider_context(root: Path, pack) -> dict:
    """Exercise the resolved Assembly-only provider context and its hard bounds."""

    import FreeCAD as App
    import Part
    import VibeCADScriptedPublication as scripted_publication

    document = App.newDocument("VibeScriptAssemblyContext")
    try:
        model_root = document.addObject("App::Part", "LegacyScriptedModel")
        scripted_publication.tag_object(
            model_root,
            role=scripted_publication.ROLE_MODEL,
            engine="vibescript",
            model_id="legacy-context-model",
        )
        scripted_publication.ensure_string_property(
            model_root,
            scripted_publication.PROP_INTERFACES,
        )
        model_root.VibeCADPublishedInterfaces = json.dumps(
            {
                "HingeMount": {
                    "output": "Arm",
                    "resolved": {
                        "subelements": ["Face1"],
                        "geometry": [{"geometry_type": "plane"}],
                    },
                }
            },
            sort_keys=True,
        )
        published = model_root.newObject("Part::Feature", "PublishedArm")
        published.Shape = Part.makeCylinder(3, 30)
        scripted_publication.tag_object(
            published,
            role=scripted_publication.ROLE_PUBLICATION,
            engine="vibescript",
            model_id="legacy-context-model",
            output_key="Arm",
            revision="e" * 64,
        )

        v2_output = document.addObject("Part::Feature", "AssemblyV2Output")
        v2_output.Shape = Part.makeBox(8, 8, 8)
        _tag_v2_output(
            v2_output,
            program_id="a" * 32,
            domain="assembly",
            output_name="Component",
        )
        foreign_output = document.addObject("Part::Feature", "ForeignPartOutput")
        foreign_output.Shape = Part.makeBox(7, 7, 7)
        _tag_v2_output(
            foreign_output,
            program_id="b" * 32,
            domain="part",
            output_name="Solid",
        )
        face_only = document.addObject("Part::Feature", "FaceOnly")
        face_only.Shape = Part.makePlane(6, 6)
        for index in range(24):
            obj = document.addObject("Part::Feature", f"NativeComponent{index:02d}")
            obj.Shape = Part.makeBox(4 + index, 5, 6)
        document.recompute()

        service = _Service(document, root)
        expected_surface_id = resolve_modeling_surface(
            "AssemblyWorkbench", "vibescript"
        ).surface_id
        snapshot = domain_context_snapshot(service, "assembly")
        assert snapshot["surface_id"] == expected_surface_id
        assert snapshot["contract"]["api_exports"] == list(pack.api_exports)
        assert snapshot["native_program_count"] == 1
        raw_candidates = snapshot["assembly_component_shapes"]
        expected_shape_objects = sum(
            1 for obj in document.Objects if getattr(obj, "Shape", None) is not None
        )
        assert expected_shape_objects >= 28
        assert raw_candidates["object_count"] == expected_shape_objects
        assert raw_candidates["objects_truncated"] is True
        assert raw_candidates["objects_omitted"] == expected_shape_objects - 24
        assert len(raw_candidates["objects"]) == 24

        context = complete_domain_context(snapshot)
        json.dumps(context, allow_nan=False)
        assert context["domain"] == "assembly"
        assert context["workbench"] == "AssemblyWorkbench"
        assert context["surface_id"] == expected_surface_id
        assert context["program_count"] == 1
        assert [item["program_id"] for item in context["programs"]] == ["a" * 32]
        candidates = context["component_candidates"]
        assert candidates["object_limit"] == 24
        assert candidates["objects_truncated"] is True
        assert candidates["objects_omitted"] == expected_shape_objects - 24
        by_name = {item["name"]: item for item in candidates["objects"]}
        semantic = by_name["PublishedArm"]
        assert semantic["eligible_component_shape"] is True
        assert semantic["requires_semantic_interfaces"] is True
        assert semantic["transient_topology"] is True
        assert semantic["published_interfaces"] == [
            {
                "interface_name": "HingeMount",
                "subelements": ["Face1"],
                "geometry_types": ["plane"],
            }
        ]
        assert by_name["AssemblyV2Output"]["transient_topology"] is True
        assert by_name["AssemblyV2Output"]["requires_semantic_interfaces"] is False
        assert by_name["FaceOnly"]["eligible_component_shape"] is False
        assert "at least one solid" in by_name["FaceOnly"]["ineligible_reason"]
        assert semantic["reference"] == {
            "document_uid": str(document.Uid),
            "object_name": "PublishedArm",
        }
        assert "part_document_shapes" not in context
        return {
            "component_count": candidates["object_count"],
            "component_limit": candidates["object_limit"],
            "components_omitted": candidates["objects_omitted"],
            "semantic_interfaces": ["HingeMount"],
        }
    finally:
        App.closeDocument(document.Name)


def _exercise_native_joint_matrix(root: Path, pack) -> dict[str, int]:
    """Construct every native joint type through the exact production graph API."""

    import FreeCAD as App
    import Part

    reference_root = root / "joint-type-references"
    reference_root.mkdir()
    shapes = {
        "Box": Part.makeBox(10, 8, 6),
        "Cylinder": Part.makeCylinder(4, 12),
    }
    entries = []
    for name, shape in shapes.items():
        path = reference_root / f"{name}.brep"
        shape.exportBrep(str(path))
        entries.append(
            {
                "document_uid": "joint-matrix",
                "object_name": name,
                "label": name,
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "brep_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "artifact_path": path.name,
                "facts": part_shape_facts(shape, max_subelements=32),
            }
        )
    configure_assembly_references(reference_root, entries)
    selection_shapes = {}
    for name in shapes:
        reloaded = Part.Shape()
        reloaded.importBrep(str(reference_root / f"{name}.brep"))
        selection_shapes[name] = reloaded
    line_edge = next(
        index
        for index, edge in enumerate(selection_shapes["Box"].Edges, start=1)
        if "line" in type(edge.Curve).__name__.lower()
    )
    circle_edge = next(
        index
        for index, edge in enumerate(selection_shapes["Cylinder"].Edges, start=1)
        if "circle" in type(edge.Curve).__name__.lower()
    )
    line_vertex = next(
        index
        for index, vertex in enumerate(selection_shapes["Box"].Vertexes, start=1)
        if selection_shapes["Box"].Edges[line_edge - 1].Vertexes[0].isSame(vertex)
    )
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    joint_arguments = {
        "fixed": {},
        "revolute": {"angle_limits_degrees": [None, 120]},
        "cylindrical": {
            "length_limits_mm": [-20, None],
            "angle_limits_degrees": [None, 180],
        },
        "slider": {"length_limits_mm": [None, 20]},
        "ball": {},
        "distance": {"distance_mm": -8},
        "parallel": {},
        "perpendicular": {},
        "angle": {"angle_degrees": 35},
        "rack_pinion": {"pitch_radius_mm": -4},
        "screw": {"thread_pitch_mm": -2},
        "gears": {"radius1_mm": 4, "radius2_mm": 8},
        "belt": {"radius1_mm": 4, "radius2_mm": 8},
    }
    observed: dict[str, int] = {}
    for index, (kind, arguments) in enumerate(joint_arguments.items()):
        document = App.newDocument(f"AssemblyJointMatrix{index}")
        try:
            first_ref = {"document_uid": "joint-matrix", "object_name": "Box"}
            second_name = "Cylinder" if kind == "rack_pinion" else "Box"
            second_ref = {"document_uid": "joint-matrix", "object_name": second_name}
            base = api.component(first_ref, grounded=True, label="Base")
            moving = api.component(second_ref, placement=[15, 0, 0], label="Moving")
            if kind == "rack_pinion":
                first_connector = api.connector(base, f"Edge{line_edge}")
                second_connector = api.connector(moving, f"Edge{circle_edge}")
            elif kind == "fixed":
                first_connector = api.connector(
                    base,
                    f"Edge{line_edge}",
                    anchor=f"Vertex{line_vertex}",
                )
                second_connector = api.connector(moving)
            else:
                first_connector = api.connector(base)
                second_connector = api.connector(moving)
            joint = api.joint(
                kind,
                first_connector,
                second_connector,
                label=f"{kind} joint",
                **arguments,
            )
            model = api.assembly([base, moving], [joint], label=f"{kind} assembly")
            diagnostics = api.solve(model, require_solved=False)
            result = {
                "Model": model,
                "Base": base,
                "Moving": moving,
                "Joint": joint,
                "Diagnostics": diagnostics,
            }
            outputs = [_worker_output(name, value) for name, value in result.items()]
            validation = validate_and_solve_assembly(document, result, outputs)
            joint_data = next(
                item["assembly_data"] for item in outputs if item["name"] == "Joint"
            )
            assert joint_data["native_type"] in __import__("JointObject").JointTypes
            assert joint_data["native_readback"]["native_type"] == joint_data["native_type"]
            assert len(joint_data["connectors"]) == 2
            assert all(item["native_reference"]["component"] for item in joint_data["connectors"])
            if kind == "fixed":
                first_frame = joint_data["connectors"][0]
                assert first_frame["anchor"] == f"Vertex{line_vertex}"
                assert first_frame["native_reference"]["subelements"] == [
                    f"Edge{line_edge}",
                    f"Vertex{line_vertex}",
                ]
            issues = list(validation["joint_dependency_issues"])
            if kind in {"rack_pinion", "screw"}:
                assert [item["code"] for item in issues] == [
                    "missing_collinear_slider"
                ]
                assert issues[0]["joint_type"] == kind
            else:
                assert issues == []
            observed[kind] = int(validation["solver_code"])
        finally:
            App.closeDocument(document.Name)
    assert set(observed) == set(joint_arguments)
    assert observed["revolute"] == 0, observed
    assert all(code == 0 for code in observed.values()), observed
    return observed


def _exercise_coupled_joint_dependencies(root: Path, pack) -> dict[str, int]:
    """Require the native Slider relationship used by RackPinion and Screw."""

    import FreeCAD as App
    import Part

    reference_root = root / "coupled-joint-references"
    reference_root.mkdir()
    shapes = {
        "Base": Part.makeBox(12, 10, 8),
        "Rack": Part.makeBox(20, 4, 4),
        "Pinion": Part.makeCylinder(4, 8),
    }
    entries = []
    for name, shape in shapes.items():
        path = reference_root / f"{name}.brep"
        shape.exportBrep(str(path))
        entries.append(
            {
                "document_uid": "coupled-joints",
                "object_name": name,
                "label": name,
                "type_id": "Part::Feature",
                "shape_type": str(shape.ShapeType),
                "brep_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "artifact_path": path.name,
                "facts": part_shape_facts(shape, max_subelements=32),
            }
        )
    configure_assembly_references(reference_root, entries)
    line_edges = {
        name: next(
            index
            for index, edge in enumerate(shape.Edges, start=1)
            if "line" in type(edge.Curve).__name__.lower()
        )
        for name, shape in shapes.items()
        if name != "Pinion"
    }
    circle_edge = next(
        index
        for index, edge in enumerate(shapes["Pinion"].Edges, start=1)
        if "circle" in type(edge.Curve).__name__.lower()
    )
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)

    def source(name: str) -> dict[str, str]:
        return {"document_uid": "coupled-joints", "object_name": name}

    invalid_document = App.newDocument("AssemblyMissingSliderDependency")
    try:
        base = api.component(source("Base"), grounded=True)
        rack = api.component(source("Rack"))
        pinion = api.component(source("Pinion"))
        coupling = api.joint(
            "rack_pinion",
            api.connector(rack, f"Edge{line_edges['Rack']}"),
            api.connector(pinion, f"Edge{circle_edge}"),
            pitch_radius_mm=4,
        )
        model = api.assembly([base, rack, pinion], [coupling])
        diagnostics = api.solve(model)
        result = {
            "Model": model,
            "Base": base,
            "Rack": rack,
            "Pinion": pinion,
            "Coupling": coupling,
            "Diagnostics": diagnostics,
        }
        try:
            validate_and_solve_assembly(
                invalid_document,
                result,
                [_worker_output(name, value) for name, value in result.items()],
            )
        except AssemblyCandidateError as exc:
            assert exc.details["stage"] == "joint_dependency"
            issue = exc.details["issues"][0]
            assert issue["code"] == "missing_collinear_slider"
            assert issue["joint_output"] == "Coupling"
            assert "api.joint('slider'" in issue["suggestion"]
        else:
            raise AssertionError("A required RackPinion Slider dependency was omitted.")
    finally:
        App.closeDocument(invalid_document.Name)

    observed = {}
    for kind in ("rack_pinion", "screw"):
        document = App.newDocument(f"AssemblyCoupled{kind}")
        try:
            base = api.component(source("Base"), grounded=True, label="Base")
            moving = api.component(source("Rack"), label="Moving")
            if kind == "rack_pinion":
                driven = api.component(source("Pinion"), label="Pinion")
                base_connector = api.connector(base, f"Edge{line_edges['Base']}")
                moving_connector = api.connector(moving, f"Edge{line_edges['Rack']}")
                slider = api.joint("slider", base_connector, moving_connector)
                coupling = api.joint(
                    kind,
                    api.connector(moving, f"Edge{line_edges['Rack']}"),
                    api.connector(driven, f"Edge{circle_edge}"),
                    pitch_radius_mm=-4,
                )
                components = [base, moving, driven]
                result = {
                    "Model": None,
                    "Base": base,
                    "Moving": moving,
                    "Driven": driven,
                    "Slider": slider,
                    "Coupling": coupling,
                    "Diagnostics": None,
                }
            else:
                slider = api.joint(
                    "slider",
                    api.connector(base),
                    api.connector(moving),
                )
                coupling = api.joint(
                    kind,
                    api.connector(base),
                    api.connector(moving),
                    thread_pitch_mm=-2,
                )
                components = [base, moving]
                result = {
                    "Model": None,
                    "Base": base,
                    "Moving": moving,
                    "Slider": slider,
                    "Coupling": coupling,
                    "Diagnostics": None,
                }
            model = api.assembly(components, [slider, coupling])
            diagnostics = api.solve(model)
            result["Model"] = model
            result["Diagnostics"] = diagnostics
            outputs = [_worker_output(name, value) for name, value in result.items()]
            validation = validate_and_solve_assembly(document, result, outputs)
            assert validation["joint_dependency_issues"] == []
            assert validation["solver_code"] == 0, validation
            observed[kind] = int(validation["solver_code"])
        finally:
            App.closeDocument(document.Name)
    return observed


def _exercise_semantic_connectors(root: Path, pack) -> dict:
    """Prove regenerating sources require and retain semantic connector identity."""

    import FreeCAD as App
    import Part

    reference_root = root / "semantic-connector-references"
    reference_root.mkdir()
    shapes = {
        "NativeBase": Part.makeBox(20, 16, 8),
        "ScriptedArm": Part.makeCylinder(3, 30),
    }
    entries = []
    for name, shape in shapes.items():
        path = reference_root / f"{name}.brep"
        shape.exportBrep(str(path))
        entry = {
            "document_uid": "semantic-connectors",
            "object_name": name,
            "label": name,
            "type_id": "Part::Feature",
            "shape_type": str(shape.ShapeType),
            "brep_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "artifact_path": path.name,
            "facts": part_shape_facts(shape, max_subelements=32),
        }
        if name == "ScriptedArm":
            entry.update(
                {
                    "source_kind": "scripted_publication",
                    "transient_topology": True,
                    "requires_semantic_interfaces": True,
                    "published_interfaces": {
                        "HingeMount": {
                            "model_id": "semantic-model",
                            "publication_name": "Arm",
                            "output_key": "Arm",
                            "subelements": ["Face1"],
                            "geometry": [{"geometry_type": "plane"}],
                        }
                    },
                }
            )
        entries.append(entry)
    configure_assembly_references(reference_root, entries)
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)

    def graph(selection):
        base = api.component(
            {
                "document_uid": "semantic-connectors",
                "object_name": "NativeBase",
            },
            grounded=True,
            label="Base",
        )
        arm = api.component(
            {
                "document_uid": "semantic-connectors",
                "object_name": "ScriptedArm",
            },
            placement=[0, 0, 8],
            label="Arm",
        )
        hinge = api.joint(
            "fixed",
            api.connector(base),
            api.connector(arm, selection),
            label="Semantic mount",
        )
        model = api.assembly([base, arm], [hinge], label="Semantic Assembly")
        diagnostics = api.solve(model)
        result = {
            "Model": model,
            "Base": base,
            "Arm": arm,
            "Hinge": hinge,
            "Diagnostics": diagnostics,
        }
        return result, [_worker_output(name, value) for name, value in result.items()]

    for selection in ("origin", "Face1"):
        document = App.newDocument("AssemblySemanticRejected")
        try:
            result, outputs = graph(selection)
            try:
                validate_and_solve_assembly(document, result, outputs)
            except AssemblyCandidateError as exc:
                assert exc.details["stage"] == "connector_selection"
                assert exc.details["component_output"] == "Arm"
                assert exc.details["available_interfaces"] == ["HingeMount"]
                assert "published_interface" in str(exc)
            else:
                raise AssertionError(
                    f"Transient scripted connector selection {selection!r} was accepted."
                )
        finally:
            App.closeDocument(document.Name)

    document = App.newDocument("AssemblySemanticUnknown")
    try:
        result, outputs = graph(
            {"type": "published_interface", "interface_name": "MissingMount"}
        )
        try:
            validate_and_solve_assembly(document, result, outputs)
        except AssemblyCandidateError as exc:
            assert exc.details["stage"] == "connector_selection"
            assert exc.details["available_interfaces"] == ["HingeMount"]
            assert "does not exist" in str(exc)
        else:
            raise AssertionError("A missing semantic Assembly interface was accepted.")
    finally:
        App.closeDocument(document.Name)

    document = App.newDocument("AssemblySemanticAccepted")
    try:
        result, outputs = graph(
            {"type": "published_interface", "interface_name": "HingeMount"}
        )
        validation = validate_and_solve_assembly(document, result, outputs)
        assert validation["solver_code"] == 0, validation
        joint = next(item for item in outputs if item["name"] == "Hinge")
        connector = joint["assembly_data"]["connectors"][1]
        assert connector["element"] == "Face1"
        assert connector["geometry_type"] == "plane"
        assert connector["semantic_selection"] == {
            "type": "published_interface",
            "interface_name": "HingeMount",
            "model_id": "semantic-model",
            "publication_name": "Arm",
            "output_key": "Arm",
        }
        return {
            "rejected_transient_selections": ["origin", "Face1"],
            "retained_interface": connector["semantic_selection"],
        }
    finally:
        App.closeDocument(document.Name)


def _source_text(
    label: str = "Production Assembly",
    *,
    hinge_statement: str | None = None,
    require_solved: bool = True,
) -> str:
    hinge = hinge_statement or (
        "hinge = api.joint('revolute', api.connector(base), api.connector(arm), "
        "angle_limits_degrees=[-90,90], label='Hinge')"
    )
    return (
        "base = api.component(inputs['base'], grounded=True, label='Base')\n"
        "arm = api.component(inputs['arm'], placement=[0,0,inputs['arm_z']], label='Arm')\n"
        "module = api.component(inputs['module'], placement=[40,0,0], label='Module')\n"
        f"{hinge}\n"
        "mount = api.joint('fixed', api.connector(base), api.connector(module), "
        "label='Module Mount')\n"
        f"model = api.assembly([base,arm,module], [hinge,mount], label={label!r})\n"
        f"diagnostics = api.solve(model, require_solved={require_solved!r})\n"
        "result = {'Model':model, 'Base':base, 'Arm':arm, 'Module':module, "
        "'Hinge':hinge, 'Mount':mount, 'Diagnostics':diagnostics}\n"
    )


def _simulation_source(formula: str = "initialValue + pi/2*time") -> str:
    return (
        "base = api.component(inputs['base'], grounded=True, label='Base')\n"
        "arm = api.component(inputs['arm'], label='Arm')\n"
        "hinge = api.joint('revolute', api.connector(base), api.connector(arm), "
        "label='Hinge')\n"
        "model = api.assembly([base,arm], [hinge], label='Driven Assembly')\n"
        "diagnostics = api.solve(model)\n"
        f"drive = api.motion(hinge, {formula!r}, label='Hinge Drive')\n"
        "simulation = api.simulation(model, [drive], start_time_s=0, "
        "end_time_s=0.1, time_step_s=0.02, error_tolerance=1e-6, "
        "frames_per_second=30, label='Kinematic Trace')\n"
        "result = {'Model':model, 'Base':base, 'Arm':arm, 'Hinge':hinge, "
        "'Drive':drive, 'Simulation':simulation, 'Diagnostics':diagnostics}\n"
    )


def _exercise_simulation_lifecycle(root: Path, pack) -> dict:
    """Create, regenerate, retain, reopen, and delete a native kinematic trace."""

    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptAssemblySimulation")
    source_base = document.addObject("Part::Feature", "SimulationBase")
    source_base.Shape = Part.makeBox(20, 20, 8)
    source_arm = document.addObject("Part::Feature", "SimulationArm")
    source_arm.Shape = Part.makeBox(30, 4, 4)
    document.recompute()
    references = {
        "base": {"document_uid": str(document.Uid), "object_name": source_base.Name},
        "arm": {"document_uid": str(document.Uid), "object_name": source_arm.Name},
    }
    input_schema = {
        "type": "object",
        "properties": {"base": _reference_schema(), "arm": _reference_schema()},
        "required": ["base", "arm"],
        "additionalProperties": False,
    }
    expected_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Base", "type": "component_link"},
        {"name": "Arm", "type": "component_link"},
        {"name": "Hinge", "type": "joint"},
        {"name": "Drive", "type": "motion"},
        {"name": "Simulation", "type": "simulation"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "assembly-production-revision",
        "document_objects": _document_objects(document),
        "surface": resolve_modeling_surface("AssemblyWorkbench", "vibescript").summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create_capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.assembly.create_program",
        arguments={
            "program_name": "Native Kinematic Simulation",
            "source": _simulation_source(),
            "input_schema": input_schema,
            "inputs": references,
            "expected_outputs": expected_outputs,
        },
    )
    service = _Service(document, root)
    prepared, execution = _prepare_and_execute(create_capture, service)
    assert execution.get("ok") is True, execution
    summary = execution["assembly_validation"]["simulation"]
    assert summary["native_code"] == 0
    assert summary["frame_count"] == 7
    assert summary["pose_count"] == 14
    observation = summary["motion_observations"][0]
    assert observation["motion_output"] == "Drive"
    assert observation["joint_output"] == "Hinge"
    assert observation["motion_type"] == "angular"
    assert observation["maximum_relative_rotation_degrees"] > 8.9

    changed_motion = copy.deepcopy(execution)
    changed_motion_item = next(
        item for item in changed_motion["outputs"] if item["name"] == "Drive"
    )
    changed_motion_item["assembly_data"]["formula"] = "time"
    try:
        validate_candidate(prepared, changed_motion)
    except ValueError as exc:
        assert "metadata was altered" in str(exc)
    else:
        raise AssertionError("Host accepted altered Assembly motion metadata.")
    changed_trace = copy.deepcopy(execution)
    changed_trace_item = next(
        item for item in changed_trace["outputs"] if item["name"] == "Simulation"
    )
    changed_trace_item["artifact_sha256"] = "0" * 64
    try:
        validate_candidate(prepared, changed_trace)
    except ValueError as exc:
        assert "trace identity changed" in str(exc)
    else:
        raise AssertionError("Host accepted an altered Assembly trace digest.")

    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    identities = {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    }
    drive = document.getObject(identities["Drive"])
    simulation = document.getObject(identities["Simulation"])
    hinge = document.getObject(identities["Hinge"])
    assert drive.TypeId == "App::FeaturePython"
    assert type(drive.Proxy).__name__ == "AssemblyMotionProxy"
    assert drive.Joint[0] is hinge
    assert drive.MotionType == "Angular"
    assert drive.Formula == "initialValue + pi/2*time"
    assert simulation.TypeId == "App::FeaturePython"
    assert type(simulation.Proxy).__name__ == "AssemblySimulationProxy"
    assert list(simulation.Group) == [drive]
    assert simulation.VibeCADFrameCount == 7
    assert simulation.VibeCADPoseCount == 14
    assert len(json.loads(simulation.VibeCADSimulationTracePreview)) == 3
    retained_trace = (
        Path(accepted["attempt_directory"])
        / "outputs"
        / "assembly-simulation-trace.json"
    )
    assert retained_trace.is_file()
    assert hashlib.sha256(retained_trace.read_bytes()).hexdigest() == (
        simulation.VibeCADTraceSHA256
    )
    inspection = complete_inspection(
        capture_inspection_state(
            service,
            "vibescript.assembly.inspect_program",
            prepared["program_id"],
        )
    )
    assert inspection["ok"] is True
    assert inspection["model_state"]["status"] == "accepted_current"
    assert inspection["model_state"]["accepted_is_current"] is True
    assert inspection["model_state"]["next_write_expected_revision"] == accepted[
        "working_revision"
    ]
    inspected_outputs = {
        item["name"]: item
        for item in inspection["program"]["live_state"]["outputs"]
    }
    assert inspected_outputs["Diagnostics"]["accepted_state"]["validation"][
        "status"
    ] == "solved"
    inspected_simulation = inspected_outputs["Simulation"]["accepted_state"]
    assert inspected_simulation["validation"]["native_code"] == 0
    assert inspected_simulation["validation"]["frame_count"] == 7
    assert len(inspected_simulation["trace_preview"]) == 3

    reconfigure_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {
                    "old": "initialValue + pi/2*time",
                    "new": "initialValue + pi*time",
                }
            ],
        },
    )
    _reconfigured, reconfigured_execution, reconfigured_publication, accepted = (
        _run_candidate(reconfigure_capture, service)
    )
    assert reconfigured_publication["created_objects"] == []
    assert document.getObject(identities["Drive"]) is drive
    assert document.getObject(identities["Simulation"]) is simulation
    assert drive.Formula == "initialValue + pi*time"
    assert reconfigured_execution["assembly_validation"]["simulation"][
        "motion_observations"
    ][0]["maximum_relative_rotation_degrees"] > 17.9

    failed_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {"old": "initialValue + pi*time", "new": "initialValue + 0*time"}
            ],
        },
    )
    failed_prepared, failed_execution = _prepare_and_execute(failed_capture, service)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert "produced no measurable angular movement" in failed_execution["error"]
    failed_details = failed_execution["observed"]["details"]
    assert failed_details["stage"] == "simulation_motion_effect"
    assert failed_details["motion_output"] == "Drive"
    assert failed_details["joint_output"] == "Hinge"
    assert "zero multiplier" in failed_details["correction"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert drive.Formula == "initialValue + pi*time"
    assert simulation.VibeCADVibeScriptRevision == accepted["accepted_revision"]
    failed_inspection = complete_inspection(
        capture_inspection_state(
            service,
            "vibescript.assembly.inspect_program",
            prepared["program_id"],
        )
    )
    assert failed_inspection["model_state"]["status"] == (
        "working_candidate_not_accepted"
    )
    assert failed_inspection["model_state"]["accepted_live_state_preserved"] is True
    assert failed_inspection["model_state"]["next_write_expected_revision"] == (
        failed_prepared["revision"]
    )

    recovery_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {"old": "initialValue + 0*time", "new": "initialValue + pi*time"}
            ],
        },
    )
    _recovered, _execution, recovery_publication, accepted = _run_candidate(
        recovery_capture, service
    )
    assert recovery_publication["created_objects"] == []

    path = root / "assembly-simulation.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    service.document = reopened
    reopened_drive = reopened.getObject(identities["Drive"])
    reopened_simulation = reopened.getObject(identities["Simulation"])
    assert reopened_drive is not None and reopened_simulation is not None
    assert type(reopened_drive.Proxy).__name__ == "AssemblyMotionProxy"
    assert type(reopened_simulation.Proxy).__name__ == "AssemblySimulationProxy"
    assert reopened_drive.Formula == "initialValue + pi*time"
    assert list(reopened_simulation.Group) == [reopened_drive]
    assert len(json.loads(reopened_simulation.VibeCADSimulationTracePreview)) == 3

    reopened_base = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": _document_objects(reopened),
    }
    delete_capture = {
        **reopened_base,
        "operation": "delete_program",
        "tool_name": "vibescript.assembly.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "reason": "Assembly simulation lifecycle complete",
        },
    }
    prepared_delete = prepare_delete(delete_capture)
    deletion = delete_live_program(service, prepared_delete)
    deleted = finish_delete(prepared_delete, deletion)
    assert deleted["ok"] is True
    assert reopened.getObject("SimulationBase") is not None
    assert reopened.getObject("SimulationArm") is not None
    assert not any(
        str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
        for obj in reopened.Objects
    )
    App.closeDocument(reopened.Name)
    return {
        "program_id": prepared["program_id"],
        "frame_count": summary["frame_count"],
        "pose_count": summary["pose_count"],
        "initial_rotation_degrees": observation[
            "maximum_relative_rotation_degrees"
        ],
        "stable_outputs": identities,
        "tamper_rejections": ["motion_metadata", "trace_digest"],
        "zero_motion_failure_stage": failed_details["stage"],
    }


def _exploded_source(transform_z: int = 20, radial: str = "[base,arm]") -> str:
    return (
        "base = api.component(inputs['part'], grounded=True, label='Base')\n"
        "center = api.component(inputs['part'], placement=[15,0,0], "
        "grounded=True, label='Center')\n"
        "arm = api.component(inputs['part'], placement=[30,0,0], "
        "grounded=True, label='Arm')\n"
        "model = api.assembly([base,center,arm], [], label='Exploded Assembly')\n"
        "diagnostics = api.solve(model)\n"
        "exploded = api.exploded_view(model, ["
        f"{{'components':[arm], 'transform':[0,0,{transform_z}]}}, "
        f"{{'components':{radial}, 'radial_distance_mm':10}}"
        "], label='Service Explosion')\n"
        "result = {'Model':model, 'Base':base, 'Center':center, 'Arm':arm, "
        "'Exploded':exploded, 'Diagnostics':diagnostics}\n"
    )


def _exercise_exploded_view_lifecycle(root: Path, pack) -> dict:
    """Prove native view semantics, stable moves, repair evidence, and reopen."""

    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptAssemblyExplodedView")
    source = document.addObject("Part::Feature", "ExplodedPart")
    source.Shape = Part.makeBox(10, 10, 10)
    source.Placement = App.Placement(App.Vector(2, 3, 4), App.Rotation())
    document.recompute()
    reference = {
        "document_uid": str(document.Uid),
        "object_name": str(source.Name),
    }
    input_schema = {
        "type": "object",
        "properties": {"part": _reference_schema()},
        "required": ["part"],
        "additionalProperties": False,
    }
    expected_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Base", "type": "component_link"},
        {"name": "Center", "type": "component_link"},
        {"name": "Arm", "type": "component_link"},
        {"name": "Exploded", "type": "exploded_view"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "assembly-production-revision",
        "document_objects": _document_objects(document),
        "surface": resolve_modeling_surface("AssemblyWorkbench", "vibescript").summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create_capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.assembly.create_program",
        arguments={
            "program_name": "Native Exploded View",
            "source": _exploded_source(),
            "input_schema": input_schema,
            "inputs": {"part": reference},
            "expected_outputs": expected_outputs,
        },
    )
    service = _Service(document, root)
    prepared, execution = _prepare_and_execute(create_capture, service)
    assert execution.get("ok") is True, execution
    summaries = execution["assembly_validation"]["exploded_views"]
    assert summaries == [
        {
            "exploded_view_output": "Exploded",
            "move_count": 2,
            "component_reference_count": 3,
            "moved_component_outputs": ["Arm", "Base"],
            "line_count": 3,
            "assembly_bounds": summaries[0]["assembly_bounds"],
        }
    ]
    view_item = next(
        item for item in execution["outputs"] if item["name"] == "Exploded"
    )
    view_data = view_item["assembly_data"]
    assert view_data["schema"] == "vibecad-assembly-exploded-view-v1"
    assert view_data["line_count"] == 3
    assert [move["kind"] for move in view_data["moves"]] == ["normal", "radial"]
    assert view_data["moves"][0]["changed_component_outputs"] == ["Arm"]
    assert view_data["moves"][1]["changed_component_outputs"] == ["Base", "Arm"]
    assert all(
        line["length_mm"] > 0
        for move in view_data["moves"]
        for line in move["line_segments"]
    )

    tamper_cases = []
    changed_line = copy.deepcopy(execution)
    changed_line_view = next(
        item for item in changed_line["outputs"] if item["name"] == "Exploded"
    )
    changed_line_view["assembly_data"]["moves"][0]["line_segments"][0][
        "end_mm"
    ][2] += 1.0
    tamper_cases.append(("line_endpoint", changed_line, "independently derived"))
    changed_final = copy.deepcopy(execution)
    changed_final_view = next(
        item for item in changed_final["outputs"] if item["name"] == "Exploded"
    )
    changed_final_view["assembly_data"]["final_component_placements"]["Arm"][
        "matrix"
    ][3] += 1.0
    tamper_cases.append(("final_placement", changed_final, "independently derived"))
    changed_native = copy.deepcopy(execution)
    changed_native_view = next(
        item for item in changed_native["outputs"] if item["name"] == "Exploded"
    )
    changed_native_view["assembly_data"]["native_readback"][
        "view_proxy_class"
    ] = "FlattenedView"
    tamper_cases.append(("native_readback", changed_native, "readback changed"))
    rejected = []
    for name, payload, message in tamper_cases:
        try:
            validate_candidate(prepared, payload)
        except ValueError as exc:
            assert message in str(exc), (name, str(exc))
            rejected.append(name)
        else:
            raise AssertionError(f"Host accepted altered exploded-view {name} evidence.")

    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    identities = {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    }
    model = document.getObject(identities["Model"])
    arm = document.getObject(identities["Arm"])
    view = document.getObject(identities["Exploded"])
    assert view.TypeId == "App::FeaturePython"
    assert type(view.Proxy).__name__ == "ExplodedView"
    assert any(parent.TypeId == "Assembly::ViewGroup" for parent in view.InList)
    steps = list(view.Group)
    assert len(steps) == 2
    assert [type(step.Proxy).__name__ for step in steps] == [
        "ExplodedViewStep",
        "ExplodedViewStep",
    ]
    assert [str(step.MoveType) for step in steps] == ["Normal", "Radial"]
    assert list(steps[0].References[1]) == [f"{arm.Name}."]
    assert steps[0].References[0] is model
    assert abs(float(steps[0].MovementTransform.Base.z) - 20.0) < 1.0e-9
    assert arm.Placement.Base.z == 0.0
    live_evidence = json.loads(view.VibeCADAssemblyExplodedViewValidation)
    assert live_evidence["schema"] == "vibecad-assembly-exploded-view-v1"
    step_identities = [str(step.Name) for step in steps]

    inspection = complete_inspection(
        capture_inspection_state(
            service,
            "vibescript.assembly.inspect_program",
            prepared["program_id"],
        )
    )
    inspected_outputs = {
        item["name"]: item for item in inspection["program"]["live_state"]["outputs"]
    }
    inspected_view = inspected_outputs["Exploded"]["accepted_state"]["validation"]
    assert inspected_view["line_count"] == 3
    assert inspected_view["moves"][0]["changed_component_outputs"] == ["Arm"]

    edit_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [{"old": "[0,0,20]", "new": "[0,0,25]"}],
        },
    )
    _edited, edited_execution, edited_publication, accepted = _run_candidate(
        edit_capture, service
    )
    assert edited_publication["created_objects"] == []
    assert document.getObject(identities["Exploded"]) is view
    assert [str(step.Name) for step in view.Group] == step_identities
    assert abs(float(view.Group[0].MovementTransform.Base.z) - 25.0) < 1.0e-9
    assert edited_execution["assembly_validation"]["exploded_views"][0][
        "line_count"
    ] == 3

    failed_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [{"old": "[base,arm]", "new": "[center]"}],
        },
    )
    failed_prepared, failed_execution = _prepare_and_execute(failed_capture, service)
    assert failed_execution.get("ok") is False
    failed_details = failed_execution["observed"]["details"]
    assert failed_details["stage"] == "exploded_view_effect"
    assert failed_details["exploded_view_output"] == "Exploded"
    assert failed_details["move_index"] == 1
    assert failed_details["unchanged_component_outputs"] == ["Center"]
    assert "assembly centre" in failed_details["correction"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert document.getObject(identities["Exploded"]) is view
    assert [str(step.Name) for step in view.Group] == step_identities
    assert abs(float(view.Group[0].MovementTransform.Base.z) - 25.0) < 1.0e-9
    failed_inspection = complete_inspection(
        capture_inspection_state(
            service,
            "vibescript.assembly.inspect_program",
            prepared["program_id"],
        )
    )
    assert failed_inspection["model_state"]["status"] == (
        "working_candidate_not_accepted"
    )
    assert failed_inspection["model_state"]["accepted_live_state_preserved"] is True

    recovery_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [{"old": "[center]", "new": "[base,arm]"}],
        },
    )
    _recovered, _execution, recovery_publication, accepted = _run_candidate(
        recovery_capture, service
    )
    assert recovery_publication["created_objects"] == []
    assert [str(step.Name) for step in view.Group] == step_identities

    path = root / "assembly-exploded-view.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    service.document = reopened
    reopened_view = reopened.getObject(identities["Exploded"])
    assert reopened_view is not None
    assert type(reopened_view.Proxy).__name__ == "ExplodedView"
    assert [str(step.Name) for step in reopened_view.Group] == step_identities
    assert all(type(step.Proxy).__name__ == "ExplodedViewStep" for step in reopened_view.Group)
    assert json.loads(reopened_view.VibeCADAssemblyExplodedViewValidation)[
        "line_count"
    ] == 3

    reopened_base = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": _document_objects(reopened),
    }
    prepared_delete = prepare_delete(
        {
            **reopened_base,
            "operation": "delete_program",
            "tool_name": "vibescript.assembly.delete_program",
            "arguments": {
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "reason": "Assembly exploded-view lifecycle complete",
            },
        }
    )
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert reopened.getObject("ExplodedPart") is not None
    assert not any(
        str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
        for obj in reopened.Objects
    )
    App.closeDocument(reopened.Name)
    return {
        "program_id": prepared["program_id"],
        "stable_outputs": identities,
        "stable_moves": step_identities,
        "tamper_rejections": rejected,
        "failure_stage": failed_details["stage"],
    }


def _quantity(obj, name: str) -> float:
    value = getattr(obj, name)
    return float(getattr(value, "Value", value))


def _exercise_live_joint_reconfiguration(
    *,
    base_capture: dict,
    service: _Service,
    program_id: str,
    accepted: dict,
    input_schema: dict,
    inputs: dict,
    expected_outputs: list[dict],
    hinge,
    base_edge: int,
    arm_edge: int,
) -> tuple[dict, dict[str, int]]:
    """Update one stable live JointObject through every supported native type."""

    connector_pair = "api.connector(base), api.connector(arm)"
    rack_connectors = (
        f"api.connector(base, 'Edge{base_edge}'), "
        f"api.connector(arm, 'Edge{arm_edge}')"
    )
    cases = {
        "fixed": f"api.joint('fixed', {connector_pair})",
        "revolute": (
            f"api.joint('revolute', {connector_pair}, "
            "angle_limits_degrees=[None,120])"
        ),
        "cylindrical": (
            f"api.joint('cylindrical', {connector_pair}, "
            "length_limits_mm=[-20,None], angle_limits_degrees=[None,180])"
        ),
        "slider": (
            f"api.joint('slider', {connector_pair}, length_limits_mm=[None,20])"
        ),
        "ball": f"api.joint('ball', {connector_pair})",
        "distance": f"api.joint('distance', {connector_pair}, distance_mm=-8)",
        "parallel": f"api.joint('parallel', {connector_pair}, suppressed=True)",
        "perpendicular": f"api.joint('perpendicular', {connector_pair})",
        "angle": f"api.joint('angle', {connector_pair}, angle_degrees=35)",
        "rack_pinion": f"api.joint('rack_pinion', {rack_connectors}, pitch_radius_mm=-4)",
        "screw": f"api.joint('screw', {connector_pair}, thread_pitch_mm=-2)",
        "gears": f"api.joint('gears', {connector_pair}, radius1_mm=4, radius2_mm=8)",
        "belt": f"api.joint('belt', {connector_pair}, radius1_mm=4, radius2_mm=8)",
    }
    native_names = {
        "fixed": "Fixed",
        "revolute": "Revolute",
        "cylindrical": "Cylindrical",
        "slider": "Slider",
        "ball": "Ball",
        "distance": "Distance",
        "parallel": "Parallel",
        "perpendicular": "Perpendicular",
        "angle": "Angle",
        "rack_pinion": "RackPinion",
        "screw": "Screw",
        "gears": "Gears",
        "belt": "Belt",
    }
    observed: dict[str, int] = {}
    for kind, expression in cases.items():
        source = _source_text(
            hinge_statement=f"hinge = {expression[:-1]}, label='Matrix {kind}')",
            require_solved=kind not in {"rack_pinion", "screw"},
        )
        capture = _candidate_capture(
            base_capture,
            operation="reconfigure_program",
            tool_name="vibescript.assembly.reconfigure_program",
            arguments={
                "program_id": program_id,
                "expected_revision": accepted["working_revision"],
                "source": source,
                "input_schema": input_schema,
                "inputs": inputs,
                "expected_outputs": expected_outputs,
            },
        )
        _prepared, execution, publication, accepted = _run_candidate(capture, service)
        assert publication["created_objects"] == []
        assert int(execution["assembly_validation"]["solver_code"]) == 0, execution
        dependency_issues = execution["assembly_validation"][
            "joint_dependency_issues"
        ]
        if kind in {"rack_pinion", "screw"}:
            assert len(dependency_issues) == 1, dependency_issues
            issue = dependency_issues[0]
            assert issue["code"] == "missing_collinear_slider"
            assert issue["joint_output"] == "Hinge"
            assert issue["joint_type"] == kind
            assert "api.joint('slider'" in issue["suggestion"]
        else:
            assert dependency_issues == []
        assert hinge.JointType == native_names[kind]
        assert hinge.Detach1 is True and hinge.Detach2 is True
        assert hinge.Reference1[0] is not None and hinge.Reference2[0] is not None
        data = json.loads(hinge.VibeCADAssemblyJointValidation)
        assert data["kind"] == kind
        assert data["native_readback"]["native_type"] == native_names[kind]
        assert bool(hinge.Suppressed) is (kind == "parallel")
        assert data["native_readback"]["suppressed"] is (kind == "parallel")
        if kind == "cylindrical":
            assert hinge.EnableLengthMin and not hinge.EnableLengthMax
            assert not hinge.EnableAngleMin and hinge.EnableAngleMax
            assert _quantity(hinge, "LengthMin") == -20.0
            assert _quantity(hinge, "AngleMax") == 180.0
        elif kind == "slider":
            assert not hinge.EnableLengthMin and hinge.EnableLengthMax
            assert not hinge.EnableAngleMin and not hinge.EnableAngleMax
            assert _quantity(hinge, "LengthMax") == 20.0
        elif kind == "revolute":
            assert not hinge.EnableLengthMin and not hinge.EnableLengthMax
            assert not hinge.EnableAngleMin and hinge.EnableAngleMax
            assert _quantity(hinge, "AngleMax") == 120.0
        else:
            assert not hinge.EnableLengthMin and not hinge.EnableLengthMax
            assert not hinge.EnableAngleMin and not hinge.EnableAngleMax
        expected_parameters = {
            "distance": ("Distance", -8.0),
            "angle": ("Angle", 35.0),
            "rack_pinion": ("Distance", -4.0),
            "screw": ("Distance", -2.0),
        }
        if kind in expected_parameters:
            property_name, expected = expected_parameters[kind]
            assert abs(_quantity(hinge, property_name) - expected) < 1.0e-9
        elif kind in {"gears", "belt"}:
            assert abs(_quantity(hinge, "Distance") - 4.0) < 1.0e-9
            assert abs(_quantity(hinge, "Distance2") - 8.0) < 1.0e-9
        observed[kind] = int(execution["assembly_validation"]["solver_code"])

    restore = _candidate_capture(
        base_capture,
        operation="reconfigure_program",
        tool_name="vibescript.assembly.reconfigure_program",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "source": _source_text(),
            "input_schema": input_schema,
            "inputs": inputs,
            "expected_outputs": expected_outputs,
        },
    )
    _prepared, execution, publication, accepted = _run_candidate(restore, service)
    assert publication["created_objects"] == []
    assert execution["assembly_validation"]["solver_code"] == 0
    assert hinge.JointType == "Revolute"
    assert hinge.EnableAngleMin and hinge.EnableAngleMax
    assert not hinge.EnableLengthMin and not hinge.EnableLengthMax
    assert set(observed) == set(cases)
    return accepted, observed


def _exercise_lifecycle(root: Path, pack) -> dict:
    import FreeCAD as App
    import Part
    import Preferences

    document = App.newDocument("VibeScriptAssemblyProduction")
    source_base = document.addObject("Part::Feature", "SourceBase")
    source_base.Label = "Source Base"
    source_base.Shape = Part.makeBox(20, 16, 8)
    source_arm = document.addObject("Part::Feature", "SourceArm")
    source_arm.Label = "Source Arm"
    source_arm.Shape = Part.makeCylinder(3, 30)
    subassembly = document.addObject("Assembly::AssemblyObject", "NativeSubassembly")
    subassembly.Type = "Assembly"
    subassembly.Label = "Native Subassembly"
    subassembly.newObject("Assembly::JointGroup", "NativeSubassemblyJoints")
    sub_link = subassembly.newObject("App::Link", "NativeSubassemblyPart")
    sub_link.LinkedObject = source_base
    document.recompute()
    assert not subassembly.Shape.isNull()
    assert len(subassembly.Shape.Solids) == 1

    references = {
        "base": {"document_uid": str(document.Uid), "object_name": source_base.Name},
        "arm": {"document_uid": str(document.Uid), "object_name": source_arm.Name},
        "module": {"document_uid": str(document.Uid), "object_name": subassembly.Name},
    }
    input_schema = {
        "type": "object",
        "properties": {
            "base": _reference_schema(),
            "arm": _reference_schema(),
            "module": _reference_schema(),
            "arm_z": {"type": "number"},
        },
        "required": ["base", "arm", "module", "arm_z"],
        "additionalProperties": False,
    }
    expected_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Base", "type": "component_link"},
        {"name": "Arm", "type": "component_link"},
        {"name": "Module", "type": "component_link"},
        {"name": "Hinge", "type": "joint"},
        {"name": "Mount", "type": "joint"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "assembly-production-revision",
        "document_objects": _document_objects(document),
        "surface": resolve_modeling_surface("AssemblyWorkbench", "vibescript").summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create_capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.assembly.create_program",
        arguments={
            "program_name": "Production Assembly",
            "source": _source_text(),
            "input_schema": input_schema,
            "inputs": {**references, "arm_z": 0.0},
            "expected_outputs": expected_outputs,
        },
    )
    service = _Service(document, root)
    auto_solve_before = bool(
        Preferences.preferences().GetBool("SolveInJointCreation", True)
    )
    prepared, execution = _prepare_and_execute(create_capture, service)
    assert execution.get("ok") is True, execution
    tamper_rejections = _exercise_worker_result_tamper_rejection(prepared, execution)
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    assert bool(Preferences.preferences().GetBool("SolveInJointCreation", True)) == (
        auto_solve_before
    )
    assert publication["created_objects"]
    assert execution["assembly_validation"]["solver_code"] == 0
    assert execution["assembly_validation"]["joint_count"] == 2
    assert execution["assembly_validation"]["grounded_components"] == ["Base"]
    resolved = {item["object_name"]: item for item in prepared["resolved_references"]}
    assert abs(resolved["SourceBase"]["facts"]["volume_mm3"] - 2560.0) < 1.0e-7
    assert resolved["SourceArm"]["facts"]["volume_mm3"] > 800.0
    assert resolved["NativeSubassembly"]["source_kind"] == "assembly"
    assert resolved["NativeSubassembly"]["reference_contract_sha256"]

    identities = {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    }
    assert set(identities) == {item["name"] for item in expected_outputs}
    model = document.getObject(identities["Model"])
    base = document.getObject(identities["Base"])
    arm = document.getObject(identities["Arm"])
    module = document.getObject(identities["Module"])
    hinge = document.getObject(identities["Hinge"])
    mount = document.getObject(identities["Mount"])
    diagnostics = document.getObject(identities["Diagnostics"])
    assert model.TypeId == "Assembly::AssemblyObject"
    assert base.TypeId == "App::Link" and base.LinkedObject is source_base
    assert arm.TypeId == "App::Link" and arm.LinkedObject is source_arm
    assert module.TypeId == "Assembly::AssemblyLink" and module.LinkedObject is subassembly
    assert model.isPartGrounded(base)
    assert hinge.JointType == "Revolute" and mount.JointType == "Fixed"
    assert hinge.Detach1 is True and hinge.Detach2 is True
    assert hinge.Reference1[0] is base and hinge.Reference2[0] is arm
    assert diagnostics.VibeCADSolverCode == 0
    diagnostic_payload = json.loads(diagnostics.VibeCADSolverDiagnostics)
    assert diagnostic_payload["native"]["available"] is True
    assert diagnostic_payload["component_count"] == 3
    dependency_anchor = next(
        obj
        for obj in document.Objects
        if str(getattr(obj, PROP_OUTPUT_TYPE, "") or "") == "dependency_anchor"
    )
    assert dependency_anchor.TypeId == "App::FeaturePython"
    assert dependency_anchor.getTypeIdOfProperty(PROP_INPUT_OBJECTS) == (
        "App::PropertyXLinkList"
    )
    assert list(getattr(dependency_anchor, PROP_INPUT_OBJECTS)) == [
        source_base,
        source_arm,
        subassembly,
    ]
    assert list(getattr(model, PROP_INPUT_OBJECTS)) == []
    for child in (base, arm, module, hinge, mount, diagnostics):
        assert list(getattr(child, PROP_INPUT_OBJECTS)) == []
    ground = next(
        obj
        for obj in document.Objects
        if str(getattr(obj, "VibeCADVibeScriptOutputName", "")) == "Base.ground"
    )
    assert ground.ObjectToGround is base

    base_edge = next(
        index
        for index, edge in enumerate(source_base.Shape.Edges, start=1)
        if "line" in type(edge.Curve).__name__.lower()
    )
    arm_edge = next(
        index
        for index, edge in enumerate(source_arm.Shape.Edges, start=1)
        if "circle" in type(edge.Curve).__name__.lower()
    )
    accepted, published_joint_codes = _exercise_live_joint_reconfiguration(
        base_capture=base_capture,
        service=service,
        program_id=prepared["program_id"],
        accepted=accepted,
        input_schema=input_schema,
        inputs={**references, "arm_z": 0.0},
        expected_outputs=expected_outputs,
        hinge=hinge,
        base_edge=base_edge,
        arm_edge=arm_edge,
    )

    inspection = complete_inspection(
        {
            "pack": pack,
            "program_id": prepared["program_id"],
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert inspection["ok"] is True
    assert inspection["program"]["accepted_revision"] == accepted["accepted_revision"]
    assert inspection["program"]["resolved_references"][2]["source_kind"] == "assembly"

    failed_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {
                    "old": "base = api.component(inputs['base'], grounded=True, label='Base')",
                    "new": "base = api.component(inputs['base'], label='Base')",
                }
            ],
        },
    )
    failed_prepared, failed_execution = _prepare_and_execute(failed_capture, service)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert "grounded component" in failed_execution["error"]
    details = failed_execution["observed"]["details"]
    assert details["stage"] == "assembly_grounding"
    assert details["solver_code"] == -6
    assert details["solver_verdict"] == "no_grounded_component"
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert document.getObject(identities["Model"]) is model
    assert model.VibeCADVibeScriptRevision == accepted["accepted_revision"]

    recovery_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {
                    "old": "base = api.component(inputs['base'], label='Base')",
                    "new": "base = api.component(inputs['base'], grounded=True, label='Base')",
                },
                {"old": "Production Assembly", "new": "Recovered Assembly"},
            ],
        },
    )
    recovered, _execution, recovery_publication, accepted = _run_candidate(
        recovery_capture,
        service,
    )
    assert recovery_publication["created_objects"] == []
    assert model.Label == "Recovered Assembly"
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == identities

    inputs_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"arm_z": 12.0},
        },
    )
    _inputs_prepared, _execution, inputs_publication, accepted = _run_candidate(
        inputs_capture,
        service,
    )
    assert inputs_publication["created_objects"] == []
    assert document.getObject(identities["Arm"]) is arm

    reconfigured_source = recovered["source"].replace(
        "Recovered Assembly", "Reconfigured Assembly"
    )
    reconfigure_capture = _candidate_capture(
        base_capture,
        operation="reconfigure_program",
        tool_name="vibescript.assembly.reconfigure_program",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "source": reconfigured_source,
            "input_schema": input_schema,
            "inputs": {**references, "arm_z": 12.0},
            "expected_outputs": expected_outputs,
        },
    )
    _reconfigured, _execution, _publication, accepted = _run_candidate(
        reconfigure_capture,
        service,
    )
    assert model.Label == "Reconfigured Assembly"

    whole_consumer = document.addObject("App::FeaturePython", "WholeAssemblyConsumer")
    whole_consumer.addProperty("App::PropertyLink", "AssemblySource")
    whole_consumer.AssemblySource = model
    arm.addProperty(
        "App::PropertyString",
        "HumanMaterialCard",
        "Material",
        "Human-authored material assignment preserved across regeneration.",
    )
    arm.HumanMaterialCard = "urn:material:human-assigned-aluminium"
    prior_digest = next(
        item["brep_sha256"]
        for item in _reconfigured["resolved_references"]
        if item["object_name"] == "SourceArm"
    )
    source_arm.Shape = Part.makeCylinder(4, 30)
    marked = mark_programs_stale_from_source(source_arm, "Shape")
    assert identities["Model"] in marked
    for object_name in identities.values():
        assert document.getObject(object_name).VibeCADDerivedState == "stale"
    stale_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"arm_z": 12.0},
        },
    )
    stale_prepared, _execution, stale_publication, accepted = _run_candidate(
        stale_capture,
        service,
    )
    new_digest = next(
        item["brep_sha256"]
        for item in stale_prepared["resolved_references"]
        if item["object_name"] == "SourceArm"
    )
    assert new_digest != prior_digest
    assert whole_consumer.AssemblySource is model
    assert arm.HumanMaterialCard == "urn:material:human-assigned-aluminium"
    assert "WholeAssemblyConsumer" in stale_publication["downstream_references"]["touched"]
    for object_name in identities.values():
        assert document.getObject(object_name).VibeCADDerivedState == "accepted"

    unsafe = document.addObject("App::FeaturePython", "UnsafeAssemblyFaceConsumer")
    unsafe.addProperty("App::PropertyLinkSub", "ComponentFace")
    unsafe.ComponentFace = (arm, ["Face1"])
    unsafe_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"arm_z": 13.0},
        },
    )
    unsafe_prepared, unsafe_execution = _prepare_and_execute(unsafe_capture, service)
    assert unsafe_execution.get("ok") is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        assert "UnsafeAssemblyFaceConsumer" in str(exc)
    else:
        raise AssertionError("A transient human Face1 consumer was silently accepted.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unsafe assembly subelement consumer",
        },
    )
    document.removeObject(unsafe.Name)
    recovery_inputs_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"arm_z": 14.0},
        },
    )
    _recovery_inputs, _execution, _publication, accepted = _run_candidate(
        recovery_inputs_capture,
        service,
    )

    path = root / "assembly-production.FCStd"
    document.saveAs(str(path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(path))
    assert reopened is not None
    service.document = reopened
    for output_name, object_name in identities.items():
        obj = reopened.getObject(object_name)
        assert obj is not None, output_name
        assert str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
    reopened_hinge = reopened.getObject(identities["Hinge"])
    assert reopened_hinge.Proxy is not None
    _assert_gui_joint_view_providers(reopened)
    assert reopened_hinge.JointType == "Revolute"
    assert reopened_hinge.Detach1 is True and reopened_hinge.Detach2 is True
    reopened_model = reopened.getObject(identities["Model"])
    reopened_consumer = reopened.getObject("WholeAssemblyConsumer")
    assert reopened_consumer.AssemblySource is reopened_model

    reopened_uid = str(reopened.Uid)
    reopened_references = {
        key: {"document_uid": reopened_uid, "object_name": value["object_name"]}
        for key, value in references.items()
    }
    reopened_base = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": reopened_uid,
        "document_objects": _document_objects(reopened),
    }
    reopen_capture = _candidate_capture(
        reopened_base,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {**reopened_references, "arm_z": 15.0},
        },
    )
    _reopened_prepared, _execution, reopen_publication, accepted = _run_candidate(
        reopen_capture,
        service,
    )
    assert reopen_publication["created_objects"] == []
    assert {
        name: details["object_name"] for name, details in accepted["live_outputs"].items()
    } == identities
    assert reopened.getObject(identities["Module"]).TypeId == "Assembly::AssemblyLink"

    delete_capture = {
        **reopened_base,
        "operation": "delete_program",
        "tool_name": "vibescript.assembly.delete_program",
        "arguments": {
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "reason": "Assembly production integration complete",
        },
    }
    prepared_delete = prepare_delete(delete_capture)
    try:
        delete_live_program(service, prepared_delete)
    except RuntimeError as exc:
        assert "WholeAssemblyConsumer" in str(exc)
    else:
        raise AssertionError("Deletion ignored a human whole-assembly consumer.")
    reopened.removeObject(reopened_consumer.Name)
    deletion = delete_live_program(service, prepared_delete)
    deleted = finish_delete(prepared_delete, deletion)
    assert deleted["ok"] is True
    assert reopened.getObject("SourceBase") is not None
    assert reopened.getObject("SourceArm") is not None
    assert reopened.getObject("NativeSubassembly") is not None
    assert not any(
        str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
        for obj in reopened.Objects
    )
    App.closeDocument(reopened.Name)
    return {
        "program_id": prepared["program_id"],
        "accepted_revision": accepted["accepted_revision"],
        "outputs": identities,
        "published_joint_solver_codes": published_joint_codes,
        "host_tamper_rejections": tamper_rejections,
    }


def _exercise_flexible_subassembly_lifecycle(root: Path, pack) -> dict:
    """Prove stable model-authored paths through nested flexible AssemblyLinks."""

    import FreeCAD as App
    import JointObject
    import Part
    import UtilsAssembly

    document = App.newDocument("VibeScriptAssemblyFlexible")
    parent_base = document.addObject("Part::Feature", "FlexibleParentBase")
    parent_base.Label = "Parent Base"
    parent_base.Shape = Part.makeBox(16, 16, 8)
    gear = document.addObject("Part::Feature", "FlexibleGearSource")
    gear.Label = "Drive Gear"
    gear.Shape = Part.makeCylinder(5, 8)
    shaft = document.addObject("Part::Feature", "FlexibleShaftSource")
    shaft.Label = "Output Shaft"
    shaft.Shape = Part.makeCylinder(3, 18)

    core = document.addObject("Assembly::AssemblyObject", "FlexibleDriveCoreSource")
    core.Type = "Assembly"
    core.Label = "Flexible Drive Core Source"
    core_joints = core.newObject("Assembly::JointGroup", "FlexibleDriveCoreJoints")
    gear_occurrence = core.newObject("App::Link", "GearOccurrence")
    gear_occurrence.LinkedObject = gear
    shaft_occurrence = core.newObject("App::Link", "ShaftOccurrence")
    shaft_occurrence.LinkedObject = shaft
    shaft_occurrence.Placement = App.Placement(
        App.Vector(0, 0, 8), App.Rotation()
    )
    source_ground = core_joints.newObject(
        "App::FeaturePython", "GroundGearOccurrence"
    )
    JointObject.GroundedJoint(source_ground, gear_occurrence)
    source_joint = core_joints.newObject(
        "App::FeaturePython", "SourceRevolute"
    )
    JointObject.Joint(
        source_joint, list(JointObject.JointTypes).index("Revolute")
    )
    source_joint.Proxy.setJointConnectors(
        source_joint,
        [
            [gear_occurrence, ["", ""]],
            [shaft_occurrence, ["", ""]],
        ],
    )
    source_joint.EnableAngleMin = True
    source_joint.AngleMin = -90.0
    source_joint.EnableAngleMax = True
    source_joint.AngleMax = 90.0
    document.recompute()
    assert int(core.solve(False)) == 0
    document.recompute()
    assert len(core.Shape.Solids) == 2

    source = document.addObject("Assembly::AssemblyObject", "FlexibleDriveSource")
    source.Type = "Assembly"
    source.Label = "Flexible Drive Source"
    source.newObject("Assembly::JointGroup", "FlexibleDriveJoints")
    core_occurrence = source.newObject(
        "Assembly::AssemblyLink", "CoreOccurrence"
    )
    core_occurrence.LinkedObject = core
    core_occurrence.Rigid = False
    document.recompute()
    assert int(source.solve(False)) == 0
    document.recompute()
    assert len(source.Shape.Solids) == 2

    references = {
        "base": {
            "document_uid": str(document.Uid),
            "object_name": parent_base.Name,
        },
        "drive": {
            "document_uid": str(document.Uid),
            "object_name": source.Name,
        },
    }
    service = _Service(document, root)
    provider_context = complete_domain_context(
        domain_context_snapshot(service, "assembly")
    )
    source_candidate = next(
        item
        for item in provider_context["component_candidates"]["objects"]
        if item["name"] == source.Name
    )
    assert source_candidate["eligible_flexible_subassembly"] is True
    assert source_candidate["eligible_detailed_bom_hierarchy"] is True
    assert [
        item["path"]
        for item in source_candidate["assembly_hierarchy"]["occurrence_paths"]
    ] == [
        "CoreOccurrence",
        "CoreOccurrence/GearOccurrence",
        "CoreOccurrence/ShaftOccurrence",
    ]
    assert source_candidate["assembly_hierarchy"]["counts"]["maximum_depth"] == 2
    input_schema = {
        "type": "object",
        "properties": {
            "base": _reference_schema(),
            "drive": _reference_schema(),
            "drive_x": {"type": "number"},
        },
        "required": ["base", "drive", "drive_x"],
        "additionalProperties": False,
    }
    source_text = "\n".join(
        [
            "base = api.component(inputs['base'], grounded=True, label='Base')",
            "drive = api.component(inputs['drive'], placement=[inputs['drive_x'], 0, 0], flexible=True, label='Drive')",
            "mount = api.joint('revolute', api.connector(base), api.connector(drive, occurrence_path='CoreOccurrence/GearOccurrence'), angle_limits_degrees=[-120, 120], label='Drive Mount')",
            "model = api.assembly([base, drive], [mount], label='Flexible Mechanism')",
            "diagnostics = api.solve(model)",
            "result = {'Model': model, 'Base': base, 'Drive': drive, 'Mount': mount, 'Diagnostics': diagnostics}",
        ]
    )
    expected_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Base", "type": "component_link"},
        {"name": "Drive", "type": "component_link"},
        {"name": "Mount", "type": "joint"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "assembly-production-revision",
        "document_objects": _document_objects(document),
        "surface": resolve_modeling_surface(
            "AssemblyWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.assembly.create_program",
        arguments={
            "program_name": "Flexible Drive Mechanism",
            "source": source_text,
            "input_schema": input_schema,
            "inputs": {**references, "drive_x": 24.0},
            "expected_outputs": expected_outputs,
        },
    )
    prepared, execution, publication, accepted = _run_candidate(capture, service)
    assert execution["assembly_validation"]["solver_code"] == 0
    assert execution["assembly_validation"]["component_occurrence_counts"] == {
        "Drive": 3
    }
    outputs = {item["name"]: item for item in execution["outputs"]}
    drive_data = outputs["Drive"]["assembly_data"]
    assert drive_data["flexible"] is True
    assert drive_data["occurrence_paths"] == [
        "CoreOccurrence",
        "CoreOccurrence/GearOccurrence",
        "CoreOccurrence/ShaftOccurrence",
    ]
    solved_occurrences = {
        item["occurrence_path"]: item for item in drive_data["solved_occurrences"]
    }
    assert list(solved_occurrences) == [
        "CoreOccurrence",
        "CoreOccurrence/GearOccurrence",
        "CoreOccurrence/ShaftOccurrence",
    ]
    mount_connector = outputs["Mount"]["assembly_data"]["connectors"][1]
    assert mount_connector["occurrence_path"] == "CoreOccurrence/GearOccurrence"
    assert mount_connector["native_target_mode"] == "direct_exposed_occurrence"
    assert [
        item["stable_name"] for item in mount_connector["native_hierarchy_chain"]
    ] == ["CoreOccurrence", "GearOccurrence"]
    assert mount_connector["native_reference"]["subelements"] == ["", ""]

    identities = {
        name: details["object_name"]
        for name, details in accepted["live_outputs"].items()
    }
    live_drive = document.getObject(identities["Drive"])
    live_mount = document.getObject(identities["Mount"])
    assert live_drive.TypeId == "Assembly::AssemblyLink"
    assert live_drive.Rigid is False
    live_core = next(
        child
        for child in live_drive.Group
        if getattr(child, "LinkedObject", None) is core_occurrence
    )
    assert live_core.TypeId == "Assembly::AssemblyLink"
    assert live_core.Rigid is False
    live_gear = next(
        child
        for child in live_core.Group
        if getattr(child, "LinkedObject", None) is gear_occurrence
    )
    live_shaft = next(
        child
        for child in live_core.Group
        if getattr(child, "LinkedObject", None) is shaft_occurrence
    )
    live_occurrences = {
        "CoreOccurrence": live_core,
        "CoreOccurrence/GearOccurrence": live_gear,
        "CoreOccurrence/ShaftOccurrence": live_shaft,
    }
    for occurrence_path, live_occurrence in live_occurrences.items():
        worker_matrix = solved_occurrences[occurrence_path]["global_placement"][
            "matrix"
        ]
        live_matrix = _placement_matrix_values(
            UtilsAssembly.getGlobalPlacement((live_occurrence, [""]))
        )
        assert len(worker_matrix) == len(live_matrix) == 16
        assert all(
            abs(worker_value - live_value) <= 1.0e-7
            for worker_value, live_value in zip(worker_matrix, live_matrix)
        )
    assert live_mount.Reference2[0] is live_gear
    assert list(live_mount.Reference2[1]) == ["", ""]
    copied_joints = list(live_core.Joints)
    assert len(copied_joints) == 1
    assert copied_joints[0].JointType == "Revolute"

    edit_capture = _candidate_capture(
        base_capture,
        operation="set_inputs",
        tool_name="vibescript.assembly.set_inputs",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "patch": {"drive_x": 30.0},
        },
    )
    _edited_prepared, edited_execution, edited_publication, edited = _run_candidate(
        edit_capture, service
    )
    assert edited_execution["assembly_validation"]["solver_code"] == 0
    assert edited_publication["created_objects"] == []
    assert {
        name: details["object_name"]
        for name, details in edited["live_outputs"].items()
    } == identities
    live_drive = document.getObject(identities["Drive"])
    live_mount = document.getObject(identities["Mount"])
    live_core = next(
        child
        for child in live_drive.Group
        if getattr(child, "LinkedObject", None) is core_occurrence
    )
    live_gear = next(
        child
        for child in live_core.Group
        if getattr(child, "LinkedObject", None) is gear_occurrence
    )
    assert live_drive.Rigid is False
    assert live_core.Rigid is False
    assert live_mount.Reference2[0] is live_gear

    invalid_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": edited["working_revision"],
            "replacements": [
                {
                    "old": "CoreOccurrence/GearOccurrence",
                    "new": "CoreOccurrence/MissingOccurrence",
                }
            ],
        },
    )
    failed_prepared, failed_execution = _prepare_and_execute(
        invalid_capture, service
    )
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    failed_details = failed_execution["observed"]["details"]
    assert failed_details["stage"] == "assembly_occurrence_path"
    assert failed_details["requested_path"] == (
        "CoreOccurrence/MissingOccurrence"
    )
    assert failed_details["failed_segment_index"] == 1
    assert failed_details["available_segments"] == [
        "GearOccurrence",
        "ShaftOccurrence",
    ]
    assert "Copy one exact occurrence path" in failed_details["correction"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert document.getObject(identities["Drive"]) is live_drive
    assert document.getObject(identities["Mount"]) is live_mount
    assert live_mount.Reference2[0] is live_gear

    recover_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {
                    "old": "CoreOccurrence/MissingOccurrence",
                    "new": "CoreOccurrence/GearOccurrence",
                }
            ],
        },
    )
    _recovered, _recovered_execution, recovered_publication, recovered = (
        _run_candidate(recover_capture, service)
    )
    assert recovered_publication["created_objects"] == []
    assert {
        name: details["object_name"]
        for name, details in recovered["live_outputs"].items()
    } == identities

    save_path = root / "flexible-subassembly.FCStd"
    document.recompute()
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    reopened_drive = reopened.getObject(identities["Drive"])
    reopened_mount = reopened.getObject(identities["Mount"])
    reopened_core_occurrence = reopened.getObject("CoreOccurrence")
    reopened_source_occurrence = reopened.getObject("GearOccurrence")
    reopened_core = next(
        child
        for child in reopened_drive.Group
        if getattr(child, "LinkedObject", None) is reopened_core_occurrence
    )
    reopened_gear = next(
        child
        for child in reopened_core.Group
        if getattr(child, "LinkedObject", None) is reopened_source_occurrence
    )
    assert reopened_drive.Rigid is False
    assert reopened_core.Rigid is False
    assert reopened_mount.Reference2[0] is reopened_gear
    assert len(reopened_core.Joints) == 1
    App.closeDocument(reopened.Name)
    return {
        "program_id": prepared["program_id"],
        "occurrence_paths": drive_data["occurrence_paths"],
        "stable_outputs": identities,
        "native_target_mode": mount_connector["native_target_mode"],
        "repair_stage": failed_details["stage"],
    }


def _bom_source_text(description: str = "Drive gear", path: str = "Module/GearLeft") -> str:
    return "\n".join(
        [
            "base = api.component(inputs['base'], grounded=True, label='Base')",
            "module = api.component(inputs['module'], placement=[24, 0, 0], label='Module')",
            "kit = api.component(inputs['kit'], placement=[48, 0, 0], label='Hardware Kit')",
            "mount = api.joint('fixed', api.connector(base), api.connector(module), label='Module Mount')",
            "kit_mount = api.joint('fixed', api.connector(base), api.connector(kit, occurrence_path='BoltA'), label='Kit Mount')",
            "model = api.assembly([base, module, kit], [mount, kit_mount], label='BOM Assembly')",
            "diagnostics = api.solve(model)",
            "bill = api.bill_of_materials(model, columns=['index', 'name', 'quantity', 'file_name', {'property':'PartNumber','heading':'Part Number'}, {'property':'UnitCost','heading':'Unit Cost'}, {'heading':'Description'}], row_overrides=[{'occurrence_path':"
            f"{path!r},'values':{{'Description':{description!r}}}}}], label='Manufacturing BOM')",
            "result = {'Model':model, 'Base':base, 'Module':module, 'Kit':kit, 'Mount':mount, 'KitMount':kit_mount, 'Bill':bill, 'Diagnostics':diagnostics}",
        ]
    )


def _exercise_native_bom_autogenerate_boundary(root: Path) -> dict:
    """Prove a literal native BOM never generates during recompute or reopen."""

    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptAssemblyBOMBoundary")
    source = document.addObject("Part::Feature", "BoundarySource")
    source.Shape = Part.makeBox(4, 5, 6)
    assembly = document.addObject("Assembly::AssemblyObject", "BoundaryAssembly")
    assembly.Type = "Assembly"
    link = assembly.newObject("App::Link", "BoundaryOccurrence")
    link.LinkedObject = source
    group = assembly.newObject("Assembly::BomGroup", "BoundaryBOMs")
    bom = group.newObject("Assembly::BomObject", "BoundaryBOM")
    assert bom.autoGenerate is True
    bom.columnsNames = ["Name"]
    document.recompute()
    assert str(bom.getContents("A2") or "")

    bom.autoGenerate = False
    bom.clearAll()
    bom.set("A1", "'Accepted Header")
    bom.set("A2", "'ACCEPTED-LITERAL-SENTINEL")
    document.recompute()
    assert str(bom.getContents("A2") or "") == "'ACCEPTED-LITERAL-SENTINEL"
    save_path = root / "assembly-bom-autogenerate-boundary.FCStd"
    document.saveAs(str(save_path))
    App.closeDocument(document.Name)

    reopened = App.openDocument(str(save_path))
    restored = reopened.getObject("BoundaryBOM")
    assert restored is not None
    assert restored.autoGenerate is False
    assert str(restored.getContents("A1") or "") == "'Accepted Header"
    assert str(restored.getContents("A2") or "") == "'ACCEPTED-LITERAL-SENTINEL"
    reopened.recompute()
    assert str(restored.getContents("A2") or "") == "'ACCEPTED-LITERAL-SENTINEL"
    App.closeDocument(reopened.Name)
    return {
        "default_auto_generate": True,
        "literal_auto_generate": False,
        "save_reopen_preserved": True,
    }


def _exercise_bom_lifecycle(root: Path, pack) -> dict:
    """Prove one model-facing BOM call through worker, live state, and reopen."""

    import FreeCAD as App
    import Part

    document = App.newDocument("VibeScriptAssemblyBOM")
    base = document.addObject("Part::Feature", "BOMBaseSource")
    base.Label = "Base Plate"
    base.Shape = Part.makeBox(20, 20, 8)
    gear = document.addObject("Part::Feature", "BOMGearSource")
    gear.Label = "Drive Gear"
    gear.Shape = Part.makeCylinder(5, 8)
    shaft = document.addObject("Part::Feature", "BOMShaftSource")
    shaft.Label = "Output Shaft"
    shaft.Shape = Part.makeCylinder(3, 18)
    bolt = document.addObject("Part::Feature", "BOMBoltSource")
    bolt.Label = "Mounting Bolt"
    bolt.Shape = Part.makeCylinder(1.5, 10)
    module = document.addObject("Assembly::AssemblyObject", "BOMModuleSource")
    module.Type = "Assembly"
    module.Label = "Drive Module"
    kit = document.addObject("App::Part", "BOMHardwareKit")
    kit.Label = "Hardware Kit"
    for obj, part_number, unit_cost in (
        (base, "BASE-001", 18.5),
        (gear, "GEAR-014", 6.25),
        (shaft, "SHAFT-008", 4.75),
        (bolt, "BOLT-M3-010", 0.2),
        (module, "MODULE-100", 0.0),
        (kit, "KIT-FASTENER", 0.0),
    ):
        obj.addProperty("App::PropertyString", "PartNumber", "BOM")
        obj.PartNumber = part_number
        obj.addProperty("App::PropertyFloat", "UnitCost", "BOM")
        obj.UnitCost = unit_cost
    gear_left = module.newObject("App::Link", "GearLeft")
    gear_left.LinkedObject = gear
    gear_right = module.newObject("App::Link", "GearRight")
    gear_right.LinkedObject = gear
    gear_right.Placement = App.Placement(App.Vector(12, 0, 0), App.Rotation())
    shaft_occurrence = module.newObject("App::Link", "ShaftOccurrence")
    shaft_occurrence.LinkedObject = shaft
    shaft_occurrence.Placement = App.Placement(
        App.Vector(6, 0, 8), App.Rotation()
    )
    bolt_a = kit.newObject("App::Link", "BoltA")
    bolt_a.LinkedObject = bolt
    bolt_b = kit.newObject("App::Link", "BoltB")
    bolt_b.LinkedObject = bolt
    bolt_b.Placement = App.Placement(App.Vector(8, 0, 0), App.Rotation())
    document.recompute()
    assert len(module.Shape.Solids) == 3
    assert len(kit.Shape.Solids) == 2
    save_path = root / "assembly-bom.FCStd"
    document.saveAs(str(save_path))

    references = {
        "base": {"document_uid": str(document.Uid), "object_name": base.Name},
        "module": {
            "document_uid": str(document.Uid),
            "object_name": module.Name,
        },
        "kit": {"document_uid": str(document.Uid), "object_name": kit.Name},
    }
    input_schema = {
        "type": "object",
        "properties": {
            "base": _reference_schema(),
            "module": _reference_schema(),
            "kit": _reference_schema(),
        },
        "required": ["base", "module", "kit"],
        "additionalProperties": False,
    }
    expected_outputs = [
        {"name": "Model", "type": "assembly"},
        {"name": "Base", "type": "component_link"},
        {"name": "Module", "type": "component_link"},
        {"name": "Kit", "type": "component_link"},
        {"name": "Mount", "type": "joint"},
        {"name": "KitMount", "type": "joint"},
        {"name": "Bill", "type": "bom"},
        {"name": "Diagnostics", "type": "solver_diagnostics"},
    ]
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "assembly-production-revision",
        "document_objects": _document_objects(document),
        "surface": resolve_modeling_surface(
            "AssemblyWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(Path(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    service = _Service(document, root)
    provider_context = complete_domain_context(
        domain_context_snapshot(service, "assembly")
    )
    candidates = {
        item["name"]: item
        for item in provider_context["component_candidates"]["objects"]
    }
    assert candidates[module.Name]["eligible_flexible_subassembly"] is True
    assert candidates[module.Name]["eligible_detailed_bom_hierarchy"] is True
    assert [
        item["path"]
        for item in candidates[module.Name]["assembly_hierarchy"][
            "occurrence_paths"
        ]
    ] == ["GearLeft", "GearRight", "ShaftOccurrence"]
    assert candidates[kit.Name]["eligible_flexible_subassembly"] is False
    assert candidates[kit.Name]["eligible_detailed_bom_hierarchy"] is True
    assert [
        item["path"]
        for item in candidates[kit.Name]["assembly_hierarchy"]["occurrence_paths"]
    ] == ["BoltA", "BoltB"]
    create_capture = _candidate_capture(
        base_capture,
        operation="create_program",
        tool_name="vibescript.assembly.create_program",
        arguments={
            "program_name": "Native Manufacturing BOM",
            "source": _bom_source_text(),
            "input_schema": input_schema,
            "inputs": references,
            "expected_outputs": expected_outputs,
        },
    )
    prepared, execution = _prepare_and_execute(create_capture, service)
    assert execution.get("ok") is True, execution
    assert execution["assembly_validation"]["boms"][0]["bom_output"] == "Bill"
    outputs = {item["name"]: item for item in execution["outputs"]}
    data = outputs["Bill"]["assembly_data"]
    assert data["schema"] == "vibecad-assembly-bom-v1"
    assert data["row_count"] == 6
    assert data["used_range"] == ["A1", "G7"]
    rows = {row["index"]: row for row in data["rows"]}
    assert rows["1"]["cells"]["Part Number"] == "BASE-001"
    assert rows["2"]["cells"]["Part Number"] == "MODULE-100"
    gear_row = rows["2.1"]
    assert gear_row["quantity"] == 2
    assert gear_row["occurrence_paths"] == [
        "Module/GearLeft",
        "Module/GearRight",
    ]
    assert gear_row["cells"]["Part Number"] == "GEAR-014"
    assert gear_row["cells"]["Unit Cost"] == "6.25"
    assert gear_row["cells"]["Description"] == "Drive gear"
    assert rows["3"]["source_kind"] == "part"
    assert rows["3"]["cells"]["Part Number"] == "KIT-FASTENER"
    bolt_row = rows["3.1"]
    assert bolt_row["quantity"] == 2
    assert bolt_row["occurrence_paths"] == ["Kit/BoltA", "Kit/BoltB"]
    assert bolt_row["cells"]["Part Number"] == "BOLT-M3-010"
    assert {row["cells"]["File Name"] for row in data["rows"]} == {
        save_path.name
    }
    assert data["native_readback"]["substituted_headings"] == [
        "File Name",
        "Description",
    ]
    kit_mount_data = outputs["KitMount"]["assembly_data"]
    assert kit_mount_data["connectors"][1]["occurrence_path"] == "BoltA"
    assert kit_mount_data["connectors"][1]["native_target_mode"] == (
        "prefixed_rigid_boundary"
    )

    tampered = copy.deepcopy(execution)
    tampered_bill = next(
        item for item in tampered["outputs"] if item["name"] == "Bill"
    )
    tampered_bill["assembly_data"]["rows"][2]["cells"]["Part Number"] = (
        "FORGED"
    )
    try:
        validate_candidate(prepared, tampered)
    except ValueError as exc:
        assert "changed after host-authenticated planning" in str(exc), str(exc)
    else:
        raise AssertionError("Host accepted tampered Assembly BOM cells.")

    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    identities = {
        name: details["object_name"]
        for name, details in accepted["live_outputs"].items()
    }
    bill = document.getObject(identities["Bill"])
    assert bill.TypeId == "Assembly::BomObject"
    assert bill.isFrozen() is True
    assert bill.autoGenerate is False
    restore_guard = next(
        obj
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_OUTPUT, "") or "")
        == "Bill.__bom_restore"
    )
    assert restore_guard.TypeId == "App::FeaturePython"
    assert restore_guard.VibeCADAssemblyBOMRestoreTarget is bill
    assert restore_guard.VibeCADAssemblyBOMRestoreError == ""
    restore_guard_name = str(restore_guard.Name)
    accepted_context = complete_domain_context(
        domain_context_snapshot(service, "assembly")
    )
    accepted_program = next(
        item
        for item in accepted_context["programs"]
        if item["program_id"] == prepared["program_id"]
    )
    assert set(accepted_program["live_outputs"]) == {
        "Model",
        "Base",
        "Module",
        "Kit",
        "Mount",
        "KitMount",
        "Bill",
        "Diagnostics",
    }
    assert list(bill.columnsNames) == [
        "Index",
        "Name",
        "Quantity",
        "File Name",
        ".PartNumber",
        ".UnitCost",
        "Description",
    ]

    def content(address: str) -> str:
        value = str(bill.getContents(address) or "")
        return value[1:] if value.startswith("'") else value

    assert [content(f"{column}1") for column in "ABCDEFG"] == [
        "Index",
        "Name",
        "Quantity",
        "File Name",
        "Part Number",
        "Unit Cost",
        "Description",
    ]
    assert content("C4") == "2"
    assert content("D4") == save_path.name
    assert content("E4") == "GEAR-014"
    assert content("G4") == "Drive gear"
    assert accepted["live_outputs"]["Bill"]["assembly_data"][
        "available_row_override_paths"
    ] == [
        "Base",
        "Module",
        "Module/GearLeft",
        "Module/GearRight",
        "Module/ShaftOccurrence",
        "Kit",
        "Kit/BoltA",
        "Kit/BoltB",
    ]
    document.recompute()
    assert bill.isFrozen() is True
    assert content("G4") == "Drive gear"
    marked = mark_programs_stale_from_source(gear, "PartNumber")
    assert bill.Name in marked
    assert bill.VibeCADDerivedState == "stale"
    assert bill.isFrozen() is True
    assert content("G4") == "Drive gear"

    inspection = complete_inspection(
        capture_inspection_state(
            service,
            "vibescript.assembly.inspect_program",
            prepared["program_id"],
        )
    )
    inspected_outputs = {
        item["name"]: item
        for item in inspection["program"]["live_state"]["outputs"]
    }
    inspected_bill = inspected_outputs["Bill"]["accepted_state"]["validation"]
    assert inspected_bill["table_sha256"] == data["table_sha256"]
    assert inspected_bill["rows"][2]["occurrence_paths"] == [
        "Module/GearLeft",
        "Module/GearRight",
    ]

    edit_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [{"old": "Drive gear", "new": "Primary drive gear"}],
        },
    )
    _edited, edited_execution, edited_publication, accepted = _run_candidate(
        edit_capture, service
    )
    assert edited_publication["created_objects"] == []
    assert document.getObject(identities["Bill"]) is bill
    assert document.getObject(restore_guard_name) is restore_guard
    assert restore_guard.VibeCADAssemblyBOMRestoreTarget is bill
    assert bill.isFrozen() is True
    assert content("G4") == "Primary drive gear"
    assert edited_execution["assembly_validation"]["boms"][0]["row_count"] == 6

    invalid_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {"old": "Module/GearLeft", "new": "Module/UnknownOccurrence"}
            ],
        },
    )
    failed_prepared, failed_execution = _prepare_and_execute(invalid_capture, service)
    assert failed_execution.get("ok") is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    failed_details = failed_execution["observed"]["details"]
    assert failed_details["stage"] == "bom_row_overrides"
    assert failed_details["requested_path"] == "Module/UnknownOccurrence"
    assert "Module/GearLeft" in failed_details["available_occurrence_paths"]
    assert "Copy one exact" in failed_details["correction"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert bill.isFrozen() is True
    assert content("G4") == "Primary drive gear"

    recover_capture = _candidate_capture(
        base_capture,
        operation="edit_source",
        tool_name="vibescript.assembly.edit_source",
        arguments={
            "program_id": prepared["program_id"],
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {"old": "Module/UnknownOccurrence", "new": "Module/GearLeft"}
            ],
        },
    )
    _recovered, _execution, recovered_publication, accepted = _run_candidate(
        recover_capture, service
    )
    assert recovered_publication["created_objects"] == []
    assert document.getObject(identities["Bill"]) is bill
    assert content("G4") == "Primary drive gear"

    document.save()
    assert content("G4") == "Primary drive gear", "BOM changed during document save"
    assert bill.isFrozen() is True
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(save_path))
    assert reopened is not None
    service.document = reopened
    reopened_bill = reopened.getObject(identities["Bill"])
    assert reopened_bill is not None
    assert reopened_bill.TypeId == "Assembly::BomObject"
    assert reopened_bill.isFrozen() is True
    assert reopened_bill.autoGenerate is False
    reopened_guard = reopened.getObject(restore_guard_name)
    assert reopened_guard is not None
    assert reopened_guard.VibeCADAssemblyBOMRestoreTarget is reopened_bill
    assert reopened_guard.VibeCADAssemblyBOMRestoreError == ""
    reopened_value = str(reopened_bill.getContents("G4") or "")
    clean_reopened_value = (
        reopened_value[1:] if reopened_value.startswith("'") else reopened_value
    )
    assert clean_reopened_value == "Primary drive gear", repr(clean_reopened_value)
    reopened_base = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": _document_objects(reopened),
    }
    prepared_delete = prepare_delete(
        {
            **reopened_base,
            "operation": "delete_program",
            "tool_name": "vibescript.assembly.delete_program",
            "arguments": {
                "program_id": prepared["program_id"],
                "expected_revision": accepted["working_revision"],
                "reason": "Assembly BOM lifecycle complete",
            },
        }
    )
    deletion = delete_live_program(service, prepared_delete)
    assert finish_delete(prepared_delete, deletion)["ok"] is True
    assert reopened.getObject("BOMBaseSource") is not None
    assert reopened.getObject("BOMModuleSource") is not None
    assert reopened.getObject("BOMHardwareKit") is not None
    assert not any(
        str(getattr(obj, PROP_PROGRAM_ID, "") or "") == prepared["program_id"]
        for obj in reopened.Objects
    )
    App.closeDocument(reopened.Name)
    return {
        "program_id": prepared["program_id"],
        "stable_outputs": identities,
        "row_count": data["row_count"],
        "aggregated_occurrence_paths": gear_row["occurrence_paths"],
        "table_sha256": data["table_sha256"],
        "failure_stage": failed_details["stage"],
        "host_tamper_rejections": ["bom_cells"],
    }


def main() -> int:
    pack = get_vibescript_pack("AssemblyWorkbench")
    assert pack is not None
    root = Path(tempfile.mkdtemp(prefix="vibecad-assembly-production-"))
    try:
        provider_context = _exercise_provider_context(root, pack)
        joint_codes = _exercise_native_joint_matrix(root, pack)
        coupled_joint_codes = _exercise_coupled_joint_dependencies(root, pack)
        semantic_connectors = _exercise_semantic_connectors(root, pack)
        simulation = _exercise_simulation_lifecycle(root, pack)
        exploded_view = _exercise_exploded_view_lifecycle(root, pack)
        flexible_subassembly = _exercise_flexible_subassembly_lifecycle(root, pack)
        bom_autogenerate_boundary = _exercise_native_bom_autogenerate_boundary(root)
        bom = _exercise_bom_lifecycle(root, pack)
        lifecycle = _exercise_lifecycle(root, pack)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(
        json.dumps(
            {
                "ok": True,
                "integration": "assembly_vibescript_api",
                "provider_context": provider_context,
                "joint_solver_codes": joint_codes,
                "coupled_joint_solver_codes": coupled_joint_codes,
                "semantic_connectors": semantic_connectors,
                "simulation": simulation,
                "exploded_view": exploded_view,
                "flexible_subassembly": flexible_subassembly,
                "bom_autogenerate_boundary": bom_autogenerate_boundary,
                "bom": bom,
                **lifecycle,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
