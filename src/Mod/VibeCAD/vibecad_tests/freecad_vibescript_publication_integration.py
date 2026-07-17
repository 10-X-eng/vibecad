# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration checks for durable VibeScript publications.

Run with the built FreeCAD Python environment, for example::

    LD_LIBRARY_PATH=build/release/lib \
      PYTHONPATH=build/release/lib \
      python3 src/Mod/VibeCAD/vibecad_tests/freecad_vibescript_publication_integration.py
"""

from __future__ import annotations

import FreeCAD as App
import Part

# Import FreeCAD first: its initialization mutates the importing frame.
import json
import math
import time
from pathlib import Path as FilePath
from tempfile import TemporaryDirectory

import Assembly  # noqa: F401 - registers native Assembly object types
import Fem
import ObjectsFem
import Path as PathModule
import TechDraw  # noqa: F401 - registers native TechDraw object types

import VibeCADReferenceContracts as reference_contracts
import VibeCADScriptedPublication as publication
import VibeCADVibeScript as vibescript
from VibeCADCore import VibeCADService
from tool_impl.service import assembly_create_assembly
from tool_impl.service import assembly_create_joint
from tool_impl.service import assembly_ground_component
from tool_impl.service import assembly_insert_component
from tool_impl.service import cam_create_job
from tool_impl.service import fem_add_constraint
from tool_impl.service import fem_create_analysis
from tool_impl.service import fem_solve
from tool_impl.service import part_boolean
from tool_impl.service import part_chamfer
from tool_impl.service import part_fillet
from tool_impl.service import techdraw_add_dimension
from tool_impl.service import techdraw_add_view
from tool_impl.service import techdraw_create_page


MODEL_SOURCE = '''import Part
params.bind(doc)
body = doc.addObject("Part::Feature", "Body")
body.Shape = Part.makeBox(params.width, 20, 30)
pin = doc.addObject("Part::Feature", "Pin")
pin.Shape = Part.makeCylinder(5, params.pin_height)
result = {"Body": body, "Pin": pin}
interfaces = {
    "width_edge": {
        "output": "Body",
        "selection": {
            "type": "query",
            "element_type": "edge",
            "expected_count": 1,
            "geometry_type": "line",
            "direction": {"x": 1, "y": 0, "z": 0},
            "direction_tolerance_degrees": 1,
            "near_point": {"x": 5, "y": 0, "z": 30},
            "max_distance": 4,
        },
    },
    "fixed_mount": {
        "output": "Body",
        "selection": {
            "type": "query",
            "element_type": "face",
            "expected_count": 1,
            "geometry_type": "plane",
            "normal": {"x": 0, "y": 0, "z": -1},
            "normal_tolerance_degrees": 1,
        },
    },
    "top_face": {
        "output": "Body",
        "selection": {
            "type": "query",
            "element_type": "face",
            "expected_count": 1,
            "geometry_type": "plane",
            "normal": {"x": 0, "y": 0, "z": 1},
            "normal_tolerance_degrees": 1,
        },
    },
    "pin_top": {
        "output": "Pin",
        "selection": {
            "type": "query",
            "element_type": "edge",
            "expected_count": 1,
            "geometry_type": "circle",
            "radius": 5,
            "radius_tolerance": 0.01,
            "near_point": {"x": 5, "y": 0, "z": 20},
            "max_distance": 10,
        },
    },
}
'''


def _native_result(payload: dict) -> dict:
    return dict((payload.get("transaction") or {}).get("result") or {})


def _require_ok(payload: dict, operation: str) -> dict:
    if not payload.get("ok"):
        raise AssertionError(f"{operation} failed: {payload!r}")
    return payload


def _run_prepared(service: VibeCADService, prepared: dict) -> dict:
    execution = vibescript.execute_prepared(prepared)
    if not execution.get("ok"):
        vibescript.cleanup_prepared(prepared)
        return execution
    try:
        imported = vibescript.import_validated_outputs(prepared, execution)
        payload = vibescript.commit_outputs(
            service, prepared, execution, imported
        )
        deadline = time.monotonic() + 60.0
        while isinstance(payload.get("_vibecad_async_commit"), dict) or isinstance(
            payload.get("_vibecad_async_rebind"), dict
        ):
            if isinstance(payload.get("_vibecad_async_rebind"), dict):
                resolved = vibescript.resolve_commit_rebind(payload)
                payload = vibescript.finish_commit_rebind(
                    service, payload, resolved
                )
                continue
            document = service._active_document()
            while document.RecomputePending or document.Recomputing:
                if time.monotonic() >= deadline:
                    return vibescript.cancel_commit(service, payload)
                time.sleep(0.01)
            payload = vibescript.continue_commit(service, payload)
        if isinstance(payload.get("_vibecad_async_validation"), dict):
            validation = vibescript.validate_commit(payload)
            payload = vibescript.finish_commit_validation(
                service, payload, validation
            )
        if isinstance(payload.get("_vibecad_async_artifact"), dict):
            artifacts = vibescript.persist_commit_artifacts(payload)
            payload = vibescript.finish_commit_artifacts(service, payload, artifacts)
        return payload
    finally:
        vibescript.cleanup_prepared(prepared)


def _create_model(service: VibeCADService) -> tuple[dict, dict]:
    prepared = vibescript.prepare_execution(
        service,
        "vibescript.create_model",
        {
            "model_name": "Publication Integration Model",
            "source": MODEL_SOURCE,
            "parameters": {"width": 10.0, "pin_height": 20.0},
            "expected_outputs": ["Body", "Pin"],
        },
    )
    return prepared, _require_ok(
        _run_prepared(service, prepared),
        "create VibeScript model",
    )


def _set_parameters(
    service: VibeCADService,
    model_id: str,
    revision: str,
    patch: dict[str, float],
) -> dict:
    prepared = vibescript.prepare_execution(
        service,
        "vibescript.set_parameters",
        {
            "model_id": model_id,
            "expected_revision": revision,
            "patch": patch,
        },
    )
    payload = _run_prepared(service, prepared)
    if not payload.get("ok"):
        candidate = vibescript.record_failed_attempt(prepared, payload)
        payload.setdefault("observed", {})["model_candidate"] = candidate
    return payload


def _published_by_key(doc, root) -> dict[str, object]:
    return publication.model_publications(root)


def _make_volume_mesh(doc, analysis, source):
    mesh = ObjectsFem.makeMeshGmsh(doc, "IntegrationMesh")
    native = Fem.FemMesh()
    native.addNode(0, 0, 0, 1)
    native.addNode(1, 0, 0, 2)
    native.addNode(0, 1, 0, 3)
    native.addNode(0, 0, 1, 4)
    native.addVolume([1, 2, 3, 4], 1)
    mesh.FemMesh = native
    mesh.Shape = source
    analysis.addObject(mesh)
    doc.recompute()
    return mesh


def _exercise_cross_workbench_contracts(directory: str) -> dict[str, str]:
    path = str(FilePath(directory) / "publication-contracts.FCStd")
    doc = App.newDocument("VibeScriptPublicationContracts")
    doc.saveAs(path)
    service = VibeCADService()
    prepared, created = _create_model(service)
    root = doc.getObject(created["model"]["object_name"])
    outputs = _published_by_key(doc, root)
    body = outputs["Body"]
    pin = outputs["Pin"]
    body_name = body.Name
    pin_name = pin.Name
    body_target = publication.publication_target(body, root)
    pin_target = publication.publication_target(pin, root)
    body_target_name = body_target.Name
    pin_target_name = pin_target.Name
    for published, target in ((body, body_target), (pin, pin_target)):
        assert published.TypeId == "App::Link"
        assert published not in list(root.Group)
        assert target in list(root.Group)
        assert publication.role_of(target) == publication.ROLE_PUBLICATION_TARGET
        linked = published.LinkedObject
        assert isinstance(linked, (tuple, list))
        assert linked[0] is root
        assert str(linked[1]) == f"{target.Name}."
        assert bool(published.LinkTransform)
    parameter_object = publication.model_parameter_object(root)
    assert parameter_object is not None
    parameter_name = parameter_object.Name
    assert math.isclose(float(parameter_object.width), 10.0)
    assert len(root.Shape.Solids) == 2
    assert not publication.implementation_closure(root)

    body.addProperty("App::PropertyString", "EngineeringMaterial", "Integration")
    body.EngineeringMaterial = "7075-T6"
    whole_link = doc.addObject("App::Link", "StableBodyLink")
    whole_link.LinkedObject = body

    direct_fillet_payload = _require_ok(
        part_fillet.run(
            service,
            body.Name,
            {
                "type": "published_interface",
                "interface_name": "width_edge",
            },
            1.0,
            "Managed Body Fillet",
        ),
        "create managed Part fillet",
    )
    direct_fillet_name = _native_result(direct_fillet_payload)["feature"]
    direct_chamfer_payload = _require_ok(
        part_chamfer.run(
            service,
            pin.Name,
            {
                "type": "published_interface",
                "interface_name": "pin_top",
            },
            0.5,
            "Managed Pin Chamfer",
        ),
        "create managed Part chamfer",
    )
    direct_chamfer_name = _native_result(direct_chamfer_payload)["feature"]

    cutter = doc.addObject("Part::Feature", "IntegrationCutter")
    cutter.Shape = Part.makeCylinder(2, 32, App.Vector(5, 10, -1))
    doc.recompute()
    cut_payload = _require_ok(
        part_boolean.run(
            service,
            "cut",
            body.Name,
            [cutter.Name],
            "Managed Native Cut",
            True,
        ),
        "create native Part cut from published output",
    )
    cut_name = _native_result(cut_payload)["feature"]
    cut_volume_before = float(doc.getObject(cut_name).Shape.Volume)
    derived_fillet_payload = _require_ok(
        part_fillet.run(
            service,
            cut_name,
            {
                "type": "query",
                "element_type": "edge",
                "expected_count": 1,
                "geometry_type": "circle",
                "radius": 2,
                "radius_tolerance": 0.01,
                "near_point": {"x": 7, "y": 10, "z": 30},
                "max_distance": 0.1,
            },
            0.5,
            "Managed Cut Fillet",
        ),
        "create managed Part fillet on derived native feature",
    )
    derived_fillet_name = _native_result(derived_fillet_payload)["feature"]

    page_payload = _require_ok(
        techdraw_create_page.run(service, "a4_landscape", "Integration Drawing"),
        "create TechDraw page",
    )
    view_payload = _require_ok(
        techdraw_add_view.run(
            service,
            page_payload["page"],
            [body.Name],
            "top",
            100,
            100,
            1,
            "Top View",
        ),
        "create TechDraw view",
    )
    dimension_payload = _require_ok(
        techdraw_add_dimension.run(
            service,
            page_payload["page"],
            view_payload["view"],
            {
                "type": "horizontal_length",
                "references": [
                    {
                        "type": "published_interface",
                        "interface_name": "width_edge",
                    }
                ],
            },
        ),
        "create managed TechDraw dimension",
    )
    dimension_name = dimension_payload["dimension"]

    assembly_payload = _require_ok(
        assembly_create_assembly.run(service, "Integration Assembly"),
        "create assembly",
    )
    assembly_name = _native_result(assembly_payload)["assembly"]
    first_component = _native_result(
        _require_ok(
            assembly_insert_component.run(
                service,
                assembly_name,
                pin.Name,
                "Fixed Pin",
                {"x": 0, "y": 0, "z": 0},
            ),
            "insert fixed pin",
        )
    )["component"]
    second_component = _native_result(
        _require_ok(
            assembly_insert_component.run(
                service,
                assembly_name,
                pin.Name,
                "Moving Pin",
                {"x": 0, "y": 0, "z": 25},
            ),
            "insert moving pin",
        )
    )["component"]
    assert set(publication.model_publications(root)) == {"Body", "Pin"}
    _require_ok(
        assembly_ground_component.run(
            service,
            assembly_name,
            first_component,
        ),
        "ground first component",
    )
    def reference(component):
        return {
            "component_name": component,
            "selection": {
                "type": "published_interface",
                "interface_name": "pin_top",
            },
        }
    joint_payload = _require_ok(
        assembly_create_joint.run(
            service,
            assembly_name,
            reference(first_component),
            reference(second_component),
            {"type": "revolute"},
            "Integration Revolute",
        ),
        "create managed Assembly joint",
    )
    joint_name = _native_result(joint_payload)["joint"]

    analysis_payload = _require_ok(
        fem_create_analysis.run(service, "Integration Static", "static"),
        "create FEM analysis",
    )
    analysis_name = analysis_payload["analysis"]
    constraint_payload = _require_ok(
        fem_add_constraint.run(
            service,
            analysis_name,
            "Managed Mount",
            {
                "type": "fixed",
                "references": [
                    {
                        "object_name": body.Name,
                        "selection": {
                            "type": "published_interface",
                            "interface_name": "fixed_mount",
                        },
                    }
                ],
            },
        ),
        "create managed FEM constraint",
    )
    constraint_name = constraint_payload["constraint_object"]
    analysis = doc.getObject(analysis_name)
    mesh = _make_volume_mesh(doc, analysis, body)
    mesh_name = mesh.Name

    job_payload = _require_ok(
        cam_create_job.run(
            service,
            "Integration CAM",
            [body.Name],
            {"x": 1, "y": 1, "z": 1},
        ),
        "create CAM job",
    )
    job_name = _native_result(job_payload)["job"]
    job = doc.getObject(job_name)
    clone = list(job.Model.Group)[0]
    operation = doc.addObject("Path::Feature", "ManagedCAMOperation")
    operation.addProperty("App::PropertyLinkSubList", "Base")
    operation.Base = [(clone, ["Face6"])]
    operation.Path = PathModule.Path(
        [PathModule.Command("G0", {"X": 0.0, "Y": 0.0, "Z": 35.0})]
    )
    job.Operations.addObject(operation)
    reference_contracts.set_contract(
        operation,
        "cam_reference",
        {
            "job_name": job.Name,
            "operation_type": "profile",
            "faces": [
                {
                    "object_name": clone.Name,
                    "selection": {
                        "type": "published_interface",
                        "interface_name": "top_face",
                        "model_id": prepared["model_id"],
                        "publication_name": clone.Name,
                        "output_key": "Body",
                    },
                }
            ],
        },
    )
    operation_name = operation.Name
    doc.recompute()

    updated = _require_ok(
        _set_parameters(
            service,
            prepared["model_id"],
            prepared["revision"],
            {"width": 12.0, "pin_height": 25.0},
        ),
        "regenerate cross-workbench model",
    )
    async_refresh = updated["publication"]["reference_refresh"][
        "asynchronous_recompute"
    ]
    assert async_refresh["ui_thread_blocked"] is False
    rebound_domains = {
        item["domain"]
        for item in updated["publication"]["reference_refresh"]["rebound"]
    }
    assert rebound_domains == {
        "assembly_joint",
        "cam_reference",
        "fem_constraint",
        "part_edge_finish",
    }
    deferred = updated["publication"]["reference_refresh"]["deferred"]
    assert [item["domain"] for item in deferred] == ["techdraw_dimension"]
    assert deferred[0]["derived_state"] == "stale"
    assert deferred[0]["projection_recompute_deferred"] is True
    assert doc.getObject(body_name) is body
    assert doc.getObject(pin_name) is pin
    assert publication.publication_target(body, root) is body_target
    assert publication.publication_target(pin, root) is pin_target
    assert doc.getObject(parameter_name) is parameter_object
    assert whole_link.LinkedObject is body
    assert body.EngineeringMaterial == "7075-T6"
    assert math.isclose(doc.getObject(dimension_name).getRawValue(), 12.0)
    assert getattr(
        doc.getObject(dimension_name),
        reference_contracts.PROP_DERIVED_STATE,
    ) == "stale"
    assert reference_contracts.read_contract(doc.getObject(joint_name))["domain"] == (
        "assembly_joint"
    )
    assert reference_contracts.read_contract(
        doc.getObject(constraint_name)
    )["domain"] == "fem_constraint"
    assert getattr(doc.getObject(mesh_name), reference_contracts.PROP_DERIVED_STATE) == (
        "stale"
    )
    prerequisites = fem_solve._structured_prerequisites(
        analysis,
        next(member for member in analysis.Group if "Solver" in member.TypeId),
    )
    assert any(item.get("kind") == "stale_mesh" for item in prerequisites["missing"])
    actual_cam_refs = [
        (linked.Name, str(name))
        for linked, names in doc.getObject(operation_name).Base
        for name in names
    ]
    assert actual_cam_refs == [(clone.Name, "Face6")]
    assert not getattr(job, reference_contracts.PROP_DERIVED_STATE, "")
    for name in (direct_fillet_name, direct_chamfer_name, derived_fillet_name):
        feature = doc.getObject(name)
        assert reference_contracts.read_contract(feature)["domain"] == (
            "part_edge_finish"
        )
        assert feature.Shape.isValid()
        assert "Invalid" not in list(feature.State)
        assert feature.EdgeLinks is not None
        assert not any(
            str(edge_name).startswith("?")
            for edge_name in list(feature.EdgeLinks[1] or [])
        )
    cut = doc.getObject(cut_name)
    assert cut.Shape.isValid()
    assert float(cut.Shape.Volume) > cut_volume_before
    checked_part_names = {
        item["object"]
        for item in updated["publication"]["reference_refresh"]
        ["native_part_validation"]["checked"]
    }
    assert {cut_name, direct_fillet_name, direct_chamfer_name, derived_fillet_name}.issubset(
        checked_part_names
    )
    assert len(root.Shape.Solids) == 2
    assert not publication.implementation_closure(root)

    doc.recompute()
    doc.save()
    latest_revision = updated["model"]["revision"]
    object_names = {
        "body": body_name,
        "pin": pin_name,
        "body_target": body_target_name,
        "pin_target": pin_target_name,
        "root": root.Name,
        "parameter": parameter_name,
        "link": whole_link.Name,
        "dimension": dimension_name,
        "joint": joint_name,
        "constraint": constraint_name,
        "operation": operation_name,
        "part_cut": cut_name,
        "part_fillet": direct_fillet_name,
        "part_chamfer": direct_chamfer_name,
        "part_derived_fillet": derived_fillet_name,
    }
    App.closeDocument(doc.Name)

    reopened = App.openDocument(path)
    service = VibeCADService()
    body = reopened.getObject(object_names["body"])
    pin = reopened.getObject(object_names["pin"])
    reopened_root = reopened.getObject(object_names["root"])
    assert reopened.getObject(object_names["link"]).LinkedObject is body
    assert body.EngineeringMaterial == "7075-T6"
    assert publication.role_of(body) == publication.ROLE_PUBLICATION
    assert publication.publication_target(body, reopened_root).Name == object_names[
        "body_target"
    ]
    assert publication.publication_target(pin, reopened_root).Name == object_names[
        "pin_target"
    ]
    reopened_update = _require_ok(
        _set_parameters(
            service,
            prepared["model_id"],
            latest_revision,
            {"width": 14.0, "pin_height": 27.0},
        ),
        "regenerate after save and reopen",
    )
    assert reopened.getObject(object_names["body"]) is body
    assert reopened.getObject(object_names["pin"]) is pin
    assert math.isclose(
        reopened.getObject(object_names["dimension"]).getRawValue(),
        14.0,
    )
    for key in ("part_cut", "part_fillet", "part_chamfer", "part_derived_fillet"):
        native_part_feature = reopened.getObject(object_names[key])
        assert native_part_feature.Shape.isValid()
        assert "Invalid" not in list(native_part_feature.State)

    unmanaged = reopened.addObject("PartDesign::Feature", "UnmanagedFaceConsumer")
    unmanaged.addProperty("App::PropertyLinkSub", "Reference")
    unmanaged.Reference = (body, ["Face1"])
    reopened.recompute()
    volume_before = float(body.Shape.Volume)
    failed = _set_parameters(
        service,
        prepared["model_id"],
        reopened_update["model"]["revision"],
        {"width": 16.0},
    )
    assert failed.get("failure_code") == "VIBESCRIPT_REFERENCE_PREFLIGHT_FAILED"
    assert (failed.get("observed") or {}).get("transaction", {}).get("opened") is False
    assert math.isclose(float(body.Shape.Volume), volume_before)
    assert unmanaged.Reference[0] is body
    assert list(unmanaged.Reference[1]) == ["Face1"]
    reopened.removeObject(unmanaged.Name)

    renamed_source = MODEL_SOURCE.replace('"width_edge"', '"renamed_width_edge"')
    prepared_rename = vibescript.prepare_execution(
        service,
        "vibescript.reconfigure_model",
        {
            "model_id": prepared["model_id"],
            "expected_revision": failed["observed"]["model_candidate"][
                "working_revision"
            ],
            "source": renamed_source,
            "parameters": {"width": 14.0, "pin_height": 27.0},
            "expected_outputs": ["Body", "Pin"],
        },
    )
    rename_failure = _run_prepared(service, prepared_rename)
    assert rename_failure.get("failure_code") == "VIBESCRIPT_REFERENCE_REBIND_FAILED"
    assert math.isclose(
        reopened.getObject(object_names["dimension"]).getRawValue(),
        14.0,
    )
    reopened.save()
    App.closeDocument(reopened.Name)
    return {"document": path, "model_id": prepared["model_id"]}


def _exercise_legacy_migration_and_source_rollback(directory: str) -> None:
    path = str(FilePath(directory) / "legacy-migration.FCStd")
    doc = App.newDocument("VibeScriptLegacyMigration")
    doc.saveAs(path)
    service = VibeCADService()
    prepared, created = _create_model(service)
    root = doc.getObject(created["model"]["object_name"])
    publications = publication.model_publications(root)
    body_publication = publications["Body"]
    pin_publication = publications["Pin"]

    legacy = doc.addObject("Part::Feature", "LegacyBody")
    legacy.Shape = body_publication.Shape.copy()
    legacy.addProperty("App::PropertyString", vibescript.PROP_MODEL_ID)
    legacy.addProperty("App::PropertyString", vibescript.PROP_OUTPUT_KEY)
    setattr(legacy, vibescript.PROP_MODEL_ID, prepared["model_id"])
    setattr(legacy, vibescript.PROP_OUTPUT_KEY, "Body")
    root.addObject(legacy)
    link = doc.addObject("App::Link", "LegacyConsumer")
    link.LinkedObject = legacy
    root.removeObject(body_publication)
    doc.removeObject(body_publication.Name)
    root.removeObject(pin_publication)
    doc.removeObject(pin_publication.Name)

    legacy_pin = doc.addObject("Part::Feature", "LegacyPin")
    legacy_pin.Shape = Part.makeCylinder(5, 20)
    legacy_pin.addProperty("App::PropertyString", vibescript.PROP_MODEL_ID)
    legacy_pin.addProperty("App::PropertyString", vibescript.PROP_OUTPUT_KEY)
    setattr(legacy_pin, vibescript.PROP_MODEL_ID, prepared["model_id"])
    setattr(legacy_pin, vibescript.PROP_OUTPUT_KEY, "Pin")
    root.addObject(legacy_pin)
    legacy_name = legacy.Name
    legacy_pin_name = legacy_pin.Name
    setattr(
        root,
        vibescript.PROP_OUTPUTS,
        json.dumps(
            {
                "Body": {"object": legacy.Name},
                "Pin": {"object": legacy_pin.Name},
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    doc.recompute()

    migrated = _require_ok(
        _set_parameters(
            service,
            prepared["model_id"],
            prepared["revision"],
            {"width": 11.0},
        ),
        "migrate legacy VibeScript publication",
    )
    migrated_publications = publication.model_publications(root)
    assert set(migrated_publications) == {"Body", "Pin"}
    assert link.LinkedObject is migrated_publications["Body"]
    assert doc.getObject(legacy_name) is None
    assert doc.getObject(legacy_pin_name) is None
    assert not publication.implementation_closure(root)

    bad_source = '''import Part
temporary = doc.addObject("Part::Feature", "MustRollback")
temporary.Shape = Part.makeBox(1, 1, 1)
raise RuntimeError("intentional rollback probe")
'''
    rollback_prepared = vibescript.prepare_execution(
        service,
        "vibescript.reconfigure_model",
        {
            "model_id": prepared["model_id"],
            "expected_revision": migrated["model"]["revision"],
            "source": bad_source,
            "parameters": {"width": 11.0, "pin_height": 20.0},
            "expected_outputs": ["Body", "Pin"],
        },
    )
    body = migrated_publications["Body"]
    volume_before = float(body.Shape.Volume)
    rollback = _run_prepared(service, rollback_prepared)
    assert rollback.get("ok") is False
    assert (rollback.get("observed") or {}).get("transaction", {}).get("aborted") is True
    assert doc.getObject("MustRollback") is None
    assert math.isclose(float(body.Shape.Volume), volume_before)
    assert link.LinkedObject is body
    App.closeDocument(doc.Name)


def _exercise_empty_part_result_rollback(directory: str) -> None:
    path = str(FilePath(directory) / "part-empty-result-rollback.FCStd")
    doc = App.newDocument("VibeScriptPartRollback")
    doc.saveAs(path)
    service = VibeCADService()
    prepared, created = _create_model(service)
    root = doc.getObject(created["model"]["object_name"])
    body = publication.model_publications(root)["Body"]
    probe = doc.addObject("Part::Feature", "IntersectionProbe")
    probe.Shape = Part.makeBox(2, 5, 5, App.Vector(9, 0, 0))
    doc.recompute()
    common_payload = _require_ok(
        part_boolean.run(
            service,
            "intersection",
            body.Name,
            [probe.Name],
            "Must Stay Nonempty",
            True,
        ),
        "create Part intersection rollback probe",
    )
    common = doc.getObject(_native_result(common_payload)["feature"])
    body_volume = float(body.Shape.Volume)
    common_volume = float(common.Shape.Volume)
    failed = _set_parameters(
        service,
        prepared["model_id"],
        prepared["revision"],
        {"width": 8.0},
    )
    assert failed.get("failure_code") == "VIBESCRIPT_PART_RECOMPUTE_FAILED"
    assert (
        (failed.get("observed") or {}).get("transaction", {}).get("rolled_back")
        is True
    ), failed
    invalid_features = (failed.get("observed") or {}).get("invalid_part_features") or []
    assert any(item.get("object") == common.Name for item in invalid_features)
    assert math.isclose(float(body.Shape.Volume), body_volume)
    assert math.isclose(float(common.Shape.Volume), common_volume)
    assert common.Shape.isValid()
    assert "Invalid" not in list(common.State)
    App.closeDocument(doc.Name)


def main() -> None:
    with TemporaryDirectory(prefix="vibecad-publication-integration-") as directory:
        summary = _exercise_cross_workbench_contracts(directory)
        _exercise_legacy_migration_and_source_rollback(directory)
        _exercise_empty_part_result_rollback(directory)
        print(
            json.dumps(
                {"ok": True, "integration": "vibescript_publication", **summary},
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
