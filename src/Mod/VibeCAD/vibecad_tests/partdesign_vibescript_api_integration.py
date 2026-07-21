# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native FreeCAD integration coverage for Part Design VibeScript v2."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import shutil
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADReferenceContracts import resolve_interface  # noqa: E402
from VibeCADScriptedPublication import (  # noqa: E402
    PROP_REVISION as PROP_PUBLISHED_REVISION,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
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
    get_domain_adapter,
    get_vibescript_pack,
)
from VibeCADVibeScriptDomainPublication import publish_candidate  # noqa: E402
from vibescript_domain_api import create_domain_api  # noqa: E402
from vibescript_partdesign_worker import (  # noqa: E402
    validate_and_build_partdesign,
)


class _Service:
    def __init__(self, document, project_root: Path) -> None:
        self.document = document
        self.project_root = project_root

    def _active_document(self):
        return self.document

    @staticmethod
    def active_workbench_name() -> str:
        return "PartDesignWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "partdesign-v2-integration-revision"

    def project_scope_snapshot(self) -> dict:
        return {"root": str(self.project_root)}

    def provider_working_set(self) -> dict:
        publications = [
            obj
            for obj in self.document.Objects
            if "VibeCADScriptedOutputKey" in list(obj.PropertiesList)
        ]
        return {
            "target_count": len(publications),
            "targets": [
                {
                    "name": str(obj.Name),
                    "label": str(obj.Label),
                    "type_id": str(obj.TypeId),
                }
                for obj in publications
            ],
        }

    @staticmethod
    def selection_summary() -> dict:
        return {"selection": []}

    @staticmethod
    def _partdesign_body_for_feature(_obj):
        return None


def _capture(base: dict, *, operation: str, arguments: dict) -> dict:
    return {
        **base,
        "operation": operation,
        "tool_name": f"vibescript.partdesign.{operation}",
        "arguments": arguments,
    }


def _run_candidate(captured: dict, service: _Service):
    prepared = prepare_candidate(captured)
    assert prepared["finalized"] is True
    assert prepared["reference_requirements"] == []
    execution = execute_candidate(prepared, cancellation_check=None)
    assert execution.get("ok") is True, execution
    validated = validate_candidate(prepared, execution)
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, publication, accepted


def _rectangle(api, x0: float, y0: float, x1: float, y1: float, **kwargs):
    return api.sketch(
        [
            api.line([x0, y0], [x1, y0]),
            api.line([x1, y0], [x1, y1]),
            api.line([x1, y1], [x0, y1]),
            api.line([x0, y1], [x0, y0]),
        ],
        **kwargs,
    )


def _fully_constrained_rectangle(api):
    bottom = api.line([0, 0], [10, 0], name="Bottom")
    right = api.line([10, 0], [10, 8], name="Right")
    top = api.line([10, 8], [0, 8], name="Top")
    left = api.line([0, 8], [0, 0], name="Left")
    constraints = [
        api.constraint(
            "coincident",
            [
                {"geometry": bottom, "point": "end"},
                {"geometry": right, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": right, "point": "end"},
                {"geometry": top, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": top, "point": "end"},
                {"geometry": left, "point": "start"},
            ],
        ),
        api.constraint(
            "coincident",
            [
                {"geometry": left, "point": "end"},
                {"geometry": bottom, "point": "start"},
            ],
        ),
        api.constraint("horizontal", [bottom]),
        api.constraint("horizontal", [top]),
        api.constraint("vertical", [right]),
        api.constraint("vertical", [left]),
        api.constraint("distance", [bottom], value=10, name="Width"),
        api.constraint("distance", [right], value=8, name="Depth"),
        api.constraint(
            "coincident",
            [{"geometry": bottom, "point": "start"}, "origin"],
            name="Anchored",
        ),
    ]
    return api.sketch(
        [bottom, right, top, left],
        constraints,
        require_fully_constrained=True,
        require_closed_profile=True,
    )


def _feature_case(api, case: str):
    if case == "constrained_pad":
        return api.pad(_fully_constrained_rectangle(api), 3)
    if case == "construction_point_pad":
        profile = api.sketch(
            [api.point([0, 0]), api.circle([0, 0], 5)],
            require_closed_profile=True,
        )
        return api.pad(profile, 3)
    if case == "arc_pad":
        profile = api.sketch(
            [
                api.arc([-5, 0], [0, 5], [5, 0]),
                api.line([5, 0], [-5, 0]),
            ]
        )
        return api.pad(profile, 3)
    if case == "ellipse_pad":
        return api.pad(api.sketch([api.ellipse([0, 0], 6, 3)]), 3)
    if case == "bspline_pad":
        profile = api.sketch(
            [
                api.bspline(
                    [[0, 0], [5, 0], [6, 4], [2, 7], [-2, 4]],
                    periodic=True,
                )
            ]
        )
        return api.pad(profile, 3)
    if case == "pocket":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        cut = api.sketch([api.circle([0, 0], 2)], z_offset_mm=5)
        return api.pocket(base, cut, 3)
    if case == "revolve":
        return api.revolve(_rectangle(api, 2, -2, 4, 2), axis="V")
    if case == "groove":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        cut = _rectangle(api, 8, 1, 12, 4, plane="XZ")
        return api.groove(base, cut, axis="V")
    if case == "loft":
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 5)]),
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=10),
            ]
        )
    if case == "additive_loft":
        base = api.pad(api.sketch([api.circle([0, 0], 6)]), 5)
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 3)], z_offset_mm=5),
                api.sketch([api.circle([0, 0], 2)], z_offset_mm=10),
            ],
            base=base,
        )
    if case == "subtractive_loft":
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 10)
        return api.loft(
            [
                api.sketch([api.circle([0, 0], 2)]),
                api.sketch([api.circle([0, 0], 4)], z_offset_mm=10),
            ],
            base=base,
            subtractive=True,
        )
    if case in {"polar_pattern", "mirror"}:
        base = api.pad(api.sketch([api.circle([0, 0], 10)]), 5)
        boss = api.pad(
            api.sketch([api.circle([7, 0], 2)], z_offset_mm=5),
            3,
            base=base,
        )
        if case == "polar_pattern":
            return api.polar_pattern(boss, 4)
        return api.mirror(boss, "YZ")
    if case == "fillet":
        base = api.pad(_rectangle(api, 0, 0, 10, 8), 5)
        return api.fillet(base, {"type": "all_edges"}, 0.5)
    if case == "chamfer":
        base = api.pad(_rectangle(api, 0, 0, 10, 8), 5)
        return api.chamfer(base, {"type": "all_edges"}, 0.5)
    raise AssertionError(f"Unknown Part Design feature case: {case}")


def _exercise_feature_families(root: Path, pack) -> dict[str, dict]:
    import FreeCAD as App

    expected_tips = {
        "constrained_pad": "PartDesign::Pad",
        "construction_point_pad": "PartDesign::Pad",
        "arc_pad": "PartDesign::Pad",
        "ellipse_pad": "PartDesign::Pad",
        "bspline_pad": "PartDesign::Pad",
        "pocket": "PartDesign::Pocket",
        "revolve": "PartDesign::Revolution",
        "groove": "PartDesign::Groove",
        "loft": "PartDesign::AdditiveLoft",
        "additive_loft": "PartDesign::AdditiveLoft",
        "subtractive_loft": "PartDesign::SubtractiveLoft",
        "polar_pattern": "PartDesign::PolarPattern",
        "mirror": "PartDesign::Mirrored",
        "fillet": "PartDesign::Fillet",
        "chamfer": "PartDesign::Chamfer",
    }
    evidence = {}
    for case, expected_tip in expected_tips.items():
        case_root = root / "feature-families" / case
        (case_root / "outputs").mkdir(parents=True)
        document = App.newDocument(f"PartDesignFeature_{case}")
        try:
            api = create_domain_api(
                pack.domain,
                pack.api_exports,
                pack.output_types,
            )
            body = api.body(_feature_case(api, case), label=case)
            outputs, validation = validate_and_build_partdesign(
                document,
                {"Result": body},
                [{"name": "Result", "type": "solid"}],
                case_root,
                max_shape_subelements=256,
            )
            output = outputs[0]
            facts = output["facts"]
            data = output["partdesign_data"]
            assert facts["shape_type"] == "Solid", (case, facts)
            assert facts["solids"] == 1, (case, facts)
            assert facts["volume_mm3"] > 0.0, (case, facts)
            assert data["tip_type_id"] == expected_tip, (case, data)
            assert validation["outputs"][0]["name"] == "Result"
            if case == "constrained_pad":
                sketch = data["sketches"][0]
                assert sketch["fully_constrained"] is True
                assert sketch["degrees_of_freedom"] == 0
            evidence[case] = {
                "tip": data["tip_type_id"],
                "volume_mm3": facts["volume_mm3"],
            }
        finally:
            App.closeDocument(document.Name)
    return evidence


def _source(*, invalid_offset: bool = False, primary_label: str = "Base Pad") -> str:
    offset = "inputs['height'] + 100" if invalid_offset else "inputs['height']"
    return (
        "profile = api.sketch([api.circle([0,0], inputs['outer_radius'])], "
        "label='Base Profile')\n"
        f"base = api.pad(profile, inputs['height'], label={primary_label!r})\n"
        "hole_profile = api.sketch([api.circle([0,0], inputs['hole_radius'])], "
        f"z_offset_mm={offset}, label='Hole Profile')\n"
        "finished = api.pocket(base, hole_profile, inputs['hole_depth'], "
        "label='Bore')\n"
        "result = {'Part': api.body(finished, interfaces={"
        "'Top': {'selection': {'type':'query','element_type':'face',"
        "'expected_count':1,'geometry_type':'plane','normal':[0,0,1],"
        "'normal_tolerance_degrees':0.1,'min_area':100},"
        "'description':'Top mating face'},"
        "'Origin': {'selection': {'type':'origin'},'description':'Body origin'}"
        "}, label='Parametric Part')}\n"
    )


def _input_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "outer_radius": {"type": "number", "exclusiveMinimum": 0},
            "hole_radius": {"type": "number", "exclusiveMinimum": 0},
            "height": {"type": "number", "exclusiveMinimum": 0},
            "hole_depth": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["outer_radius", "hole_radius", "height", "hole_depth"],
        "additionalProperties": False,
    }


def _document_consumers(document, published):
    whole = document.addObject("App::FeaturePython", "WholeObjectConsumer")
    whole.addProperty("App::PropertyLink", "SourceObject")
    whole.SourceObject = published
    specifications = (
        ("Assembly::AssemblyObject", "AssemblyConsumer"),
        ("Fem::FeaturePython", "FemConsumer"),
        ("Path::FeaturePython", "CamConsumer"),
        ("TechDraw::DrawViewPart", "TechDrawConsumer"),
        ("Robot::RobotObject", "RobotConsumer"),
        ("Inspection::Feature", "InspectionConsumer"),
    )
    consumers = [whole]
    for type_id, name in specifications:
        consumer = document.addObject(type_id, name)
        consumer.addProperty("App::PropertyLink", "VibeCADTestSource")
        consumer.VibeCADTestSource = published
        consumers.append(consumer)
    published.addProperty(
        "App::PropertyString",
        "HumanMaterialCard",
        "Material",
        "Human-authored physical material assignment.",
    )
    published.HumanMaterialCard = "urn:material:steel"
    return consumers


def _assert_consumers(document, consumers, published) -> None:
    assert document.getObject(published.Name) is published
    assert published.HumanMaterialCard == "urn:material:steel"
    for consumer in consumers:
        current = document.getObject(consumer.Name)
        assert current is not None
        if current.Name == "WholeObjectConsumer":
            assert current.SourceObject is published
        else:
            assert current.VibeCADTestSource is published


def _exercise_lifecycle(root: Path, pack) -> dict:
    import FreeCAD as App
    from pathlib import Path as LocalPath

    document = App.newDocument("PartDesignVibeScriptV2")
    service = _Service(document, root)
    base_capture = {
        "pack": pack,
        "project_root": str(root),
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": service.provider_document_revision(),
        "document_objects": [],
        "surface": resolve_modeling_surface(
            "PartDesignWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(LocalPath(App.getHomePath()).resolve()),
        "timeout_seconds": 60.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }
    create = _capture(
        base_capture,
        operation="create_program",
        arguments={
            "program_name": "Part Design v2 Lifecycle",
            "source": _source(),
            "input_schema": _input_schema(),
            "inputs": {
                "outer_radius": 10.0,
                "hole_radius": 2.0,
                "height": 12.0,
                "hole_depth": 5.0,
            },
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    prepared, publication, accepted = _run_candidate(create, service)
    program_id = prepared["program_id"]
    identity = accepted["live_outputs"]["Part"]["object_name"]
    published = document.getObject(identity)
    assert published is not None
    assert published.Shape.ShapeType == "Solid"
    assert len(published.Shape.Solids) == 1
    assert publication["created_objects"]
    assert publication["recompute_deferred"] is True
    assert publication["interfaces"]["Top"]["resolved"]["object"] == identity
    assert publication["interfaces"]["Top"]["resolved"]["subelements"]
    top = resolve_interface(service, published, "Top")
    assert top["publication_name"] == identity
    assert top["interface_name"] == "Top"

    inspected = complete_inspection(
        {
            "pack": pack,
            "program_id": program_id,
            "project_root": str(root),
            "live_programs": [],
        }
    )
    assert inspected["ok"] is True
    assert inspected["model_state"]["status"] == "accepted_current"
    assert inspected["program"]["accepted_revision"] == accepted["accepted_revision"]

    edit = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {"old": "label='Base Pad'", "new": "label='Primary Pad'"}
            ],
        },
    )
    _edited, edit_publication, accepted = _run_candidate(edit, service)
    assert edit_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity

    failed = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "replacements": [
                {
                    "old": "z_offset_mm=inputs['height']",
                    "new": "z_offset_mm=inputs['height'] + 100",
                }
            ],
        },
    )
    failed_prepared = prepare_candidate(failed)
    failed_execution = execute_candidate(failed_prepared, cancellation_check=None)
    assert failed_execution["ok"] is False
    assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
    assert failed_execution["domain_failure_stage"] == "feature_postcondition"
    assert "did not remove material" in failed_execution["error"]
    retain_candidate(failed_prepared, status="failed", failure=failed_execution)
    assert getattr(published, PROP_PUBLISHED_REVISION) == accepted["accepted_revision"]

    recovery = _capture(
        base_capture,
        operation="edit_source",
        arguments={
            "program_id": program_id,
            "expected_revision": failed_prepared["revision"],
            "replacements": [
                {
                    "old": "z_offset_mm=inputs['height'] + 100",
                    "new": "z_offset_mm=inputs['height']",
                }
            ],
        },
    )
    _recovered, recovery_publication, accepted = _run_candidate(recovery, service)
    assert recovery_publication["created_objects"] == []
    assert document.getObject(identity) is published

    set_inputs = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"height": 15.0},
        },
    )
    _inputs, inputs_publication, accepted = _run_candidate(set_inputs, service)
    assert inputs_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity

    consumers = _document_consumers(document, published)
    unsafe = document.addObject("Part::Feature", "UnsafeFaceConsumer")
    unsafe.addProperty("App::PropertyLinkSub", "SourceFace")
    unsafe.SourceFace = (published, ["Face1"])
    unsafe_update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"outer_radius": 11.0},
        },
    )
    unsafe_prepared = prepare_candidate(unsafe_update)
    unsafe_execution = execute_candidate(unsafe_prepared, cancellation_check=None)
    assert unsafe_execution["ok"] is True, unsafe_execution
    unsafe_validated = validate_candidate(unsafe_prepared, unsafe_execution)
    retain_candidate(unsafe_prepared, status="validated")
    try:
        publish_candidate(service, unsafe_prepared, unsafe_validated)
    except RuntimeError as exc:
        assert "Face/Edge/Vertex references" in str(exc)
        details = getattr(exc, "details", {})
        assert any(
            item.get("owner_name") == "UnsafeFaceConsumer"
            for item in details.get("unsafe_references", [])
        ), details
    else:
        raise AssertionError("An unmanaged Face1 consumer survived regeneration.")
    retain_candidate(
        unsafe_prepared,
        status="publication_failed",
        failure={
            "failure_code": "DOMAIN_PUBLICATION_FAILED",
            "failure_stage": "native_call",
            "error": "unmanaged transient topology consumer",
        },
    )
    assert getattr(published, PROP_PUBLISHED_REVISION) == accepted["accepted_revision"]
    document.removeObject(unsafe.Name)

    safe_update = _capture(
        base_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": unsafe_prepared["revision"],
            "patch": {"outer_radius": 11.5},
        },
    )
    _safe, safe_publication, accepted = _run_candidate(safe_update, service)
    assert safe_publication["created_objects"] == []
    _assert_consumers(document, consumers, published)
    assert resolve_interface(service, published, "Top")["subelements"]

    reconfigured_source = (
        "profile = api.sketch([api.circle([0,0], inputs['outer_radius'])])\n"
        "base = api.pad(profile, inputs['height'], label='Primary Pad')\n"
        "hole_profile = api.sketch([api.circle([0,0], inputs['hole_radius'])], "
        "z_offset_mm=inputs['height'])\n"
        "finished = api.pocket(base, hole_profile, through_all=True, label='Bore')\n"
        "result = {'Part': api.body(finished, interfaces={"
        "'Top': {'selection': {'type':'query','element_type':'face',"
        "'expected_count':1,'geometry_type':'plane','normal':[0,0,1],"
        "'min_area':100}}"
        "}, label='Parametric Part')}\n"
    )
    reconfigure = _capture(
        base_capture,
        operation="reconfigure_program",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "source": reconfigured_source,
            "input_schema": {
                "type": "object",
                "properties": {
                    "outer_radius": {"type": "number", "exclusiveMinimum": 0},
                    "hole_radius": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["outer_radius", "hole_radius", "height"],
                "additionalProperties": False,
            },
            "inputs": {
                "outer_radius": 11.5,
                "hole_radius": 2.0,
                "height": 15.0,
            },
            "expected_outputs": [{"name": "Part", "type": "solid"}],
        },
    )
    _reconfigured, reconfigure_publication, accepted = _run_candidate(
        reconfigure, service
    )
    assert reconfigure_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity
    _assert_consumers(document, consumers, published)

    saved_path = root / "partdesign-v2-lifecycle.FCStd"
    consumer_names = [str(item.Name) for item in consumers]
    document.saveAs(str(saved_path))
    App.closeDocument(document.Name)
    reopened = App.openDocument(str(saved_path))
    assert reopened is not None
    service.document = reopened
    reopened_published = reopened.getObject(identity)
    assert reopened_published is not None
    reopened_consumers = [reopened.getObject(name) for name in consumer_names]
    assert all(item is not None for item in reopened_consumers)
    _assert_consumers(reopened, reopened_consumers, reopened_published)
    assert resolve_interface(service, reopened_published, "Top")["subelements"]

    reopened_capture = {
        **base_capture,
        "document_name": str(reopened.Name),
        "document_uid": str(reopened.Uid),
        "document_objects": [
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
            }
            for obj in reopened.Objects
        ],
    }
    reopened_update = _capture(
        reopened_capture,
        operation="set_inputs",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "patch": {"outer_radius": 12.0},
        },
    )
    _reopened_prepared, reopened_publication, accepted = _run_candidate(
        reopened_update, service
    )
    assert reopened_publication["created_objects"] == []
    assert accepted["live_outputs"]["Part"]["object_name"] == identity
    _assert_consumers(reopened, reopened_consumers, reopened_published)

    delete_capture = _capture(
        reopened_capture,
        operation="delete_program",
        arguments={
            "program_id": program_id,
            "expected_revision": accepted["working_revision"],
            "reason": "Complete Part Design v2 lifecycle integration.",
        },
    )
    adapter = get_domain_adapter("partdesign")
    assert adapter is not None
    deletion = prepare_delete(delete_capture)
    try:
        adapter.delete(service, deletion, deletion["manifest"])
    except RuntimeError as exc:
        assert "Cannot delete" in str(exc)
        assert "WholeObjectConsumer" in str(exc)
    else:
        raise AssertionError("Part Design deletion ignored downstream links.")
    restore_prepared_delete(deletion)
    for consumer in reversed(reopened_consumers):
        reopened.removeObject(consumer.Name)
    deletion = prepare_delete(delete_capture)
    deletion_publication = adapter.delete(service, deletion, deletion["manifest"])
    deleted = finish_delete(deletion, deletion_publication)
    assert deleted["artifacts_deleted"] is True
    assert reopened.getObject(identity) is None
    assert not LocalPath(deletion["program_directory"]).exists()
    App.closeDocument(reopened.Name)
    return {
        "program_id": program_id,
        "stable_output_identity": identity,
        "failed_candidate_retained": failed_prepared["revision"],
        "unsafe_reference_rejected": True,
        "save_reopen_regenerated": True,
        "deleted": [item["object_name"] for item in deleted["deleted_objects"]],
    }


def main() -> int:
    dump_json = json.dumps
    remove_tree = shutil.rmtree
    pack = get_vibescript_pack("PartDesignWorkbench")
    assert pack is not None and pack.production_ready
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    signatures = {
        name: str(inspect.signature(getattr(api, name)))
        for name in api.exported_names
    }
    assert tuple(signatures) == pack.api_exports
    assert all("*args" not in value and "**properties" not in value for value in signatures.values())
    external = api.external_geometry(
        {"document_uid": "source-document", "object_name": "SourcePart"},
        {"type": "published_interface", "interface_name": "MountEdge"},
    )
    assert external.domain == "partdesign"
    assert external.operation == "external_geometry"

    root = Path(tempfile.mkdtemp(prefix="vibecad-partdesign-v2-integration-"))
    try:
        feature_families = _exercise_feature_families(root, pack)
        lifecycle = _exercise_lifecycle(root, pack)
        print(
            dump_json(
                {
                    "ok": True,
                    "domain": "partdesign",
                    "feature_families": feature_families,
                    "lifecycle": lifecycle,
                },
                sort_keys=True,
            )
        )
    finally:
        remove_tree(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
