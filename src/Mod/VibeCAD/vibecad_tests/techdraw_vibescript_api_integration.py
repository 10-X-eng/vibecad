# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical TechDraw VibeScript domain."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    accept_candidate,
    capture_reference_inputs,
    execute_candidate,
    finalize_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_TECHDRAW_VALIDATION,
    delete_live_program,
    publish_candidate,
)
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    PROP_PROGRAM_REVISION,
    complete_domain_context,
    domain_context_snapshot,
    get_domain_adapter,
    get_vibescript_pack,
)
from vibescript_techdraw_api import (  # noqa: E402
    TechDrawAPIError,
    TechDrawDomainAPI,
    _EXPORTS,
    _OUTPUT_TYPES,
)
from vibescript_techdraw_worker import (  # noqa: E402
    TechDrawCandidateError,
    _validate_graph,
    validate_techdraw_definition,
)


EXPECTED_OUTPUTS = [
    {"name": "Template", "type": "template"},
    {"name": "Views", "type": "projection"},
    {"name": "Width", "type": "dimension"},
    {"name": "Note", "type": "annotation"},
    {"name": "Sheet", "type": "page"},
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "TechDrawWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "techdraw-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "techdraw-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected TechDraw failure containing {fragment!r}.")


def _expect_candidate_error(stage: str, call) -> TechDrawCandidateError:
    try:
        call()
    except TechDrawCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
        return exc
    raise AssertionError(f"Expected TechDraw candidate failure at stage {stage!r}.")


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "solid": {
                "type": "object",
                "x-vibecad-reference": True,
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            },
            "x": {"type": "number", "minimum": 0, "maximum": 297},
        },
        "required": ["solid", "x"],
        "additionalProperties": False,
    }


def _program_source() -> str:
    return (
        "template = api.template('a4_landscape', "
        "editable_texts={'TITLE':'Bracket'})\n"
        "views = api.projection([inputs['solid']], "
        "directions=['front','top','right'], convention='third_angle', "
        "x_mm=inputs['x'], y_mm=105, spacing_x_mm=20, spacing_y_mm=20, "
        "label='Orthographic Views')\n"
        "width = api.dimension(views, 'distance', ['Edge0'], "
        "projection_direction='top', x_mm=105, y_mm=35, label='Top Width')\n"
        "note = api.annotation(['ALL DIMENSIONS IN MM'], x_mm=30, "
        "y_mm=18, alignment='left')\n"
        "sheet = api.page(template, [views, width, note], "
        "convention='third_angle', label='Bracket Drawing')\n"
        "result = {'Template':template,'Views':views,'Width':width,"
        "'Note':note,'Sheet':sheet}\n"
    )


def _assert_projection_timeline_block(document, projection) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    resources = list(projection.Views)
    indices = [operations.index(resource) for resource in resources]
    projection_index = operations.index(projection)
    assert indices == list(
        range(projection_index - len(resources), projection_index)
    )
    assert all(
        resource.VibeCADTimelineRole == "resource"
        and resource.VibeCADTimelineOwner is projection
        and resource.getTypeIdOfProperty("VibeCADTimelineOwner")
        == "App::PropertyLinkHidden"
        for resource in resources
    )
    assert projection.VibeCADTimelineRole == "operation"


def _assert_page_timeline_block(document, page, template) -> None:
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    assert operations.index(template) == operations.index(page) - 1
    assert page.VibeCADTimelineRole == "operation"
    assert template.VibeCADTimelineRole == "resource"
    assert template.VibeCADTimelineOwner is page
    assert (
        template.getTypeIdOfProperty("VibeCADTimelineOwner")
        == "App::PropertyLinkHidden"
    )


def _assert_techdraw_timeline_graph(
    document,
    outputs: dict[str, object],
    source,
) -> dict[str, object]:
    assert document.getObject(source.Name) is source
    projection = outputs["Views"]
    page = outputs["Sheet"]
    template = outputs["Template"]
    _assert_projection_timeline_block(document, projection)
    _assert_page_timeline_block(document, page, template)
    timeline = document.getObject("VibeCADTimeline")
    assert timeline is not None
    operations = list(timeline.Operations)
    for name in ("Views", "Width", "Note", "Sheet"):
        obj = outputs[name]
        assert obj in operations
        assert obj.VibeCADTimelineRole == "operation"
        assert getattr(obj, "VibeCADTimelineOwner", None) is None

    children = list(projection.Views)
    assert list(projection.Source) == [source]
    assert all(list(child.Source) == [source] for child in children)
    assert page.Template is template
    assert set(page.Views) == {
        projection,
        outputs["Width"],
        outputs["Note"],
    }
    dimension_sources = [
        reference[0] for reference in list(outputs["Width"].References2D)
    ]
    assert dimension_sources
    assert all(target in children for target in dimension_sources)
    return {
        "operations": {
            name: str(outputs[name].Name)
            for name in ("Views", "Width", "Note", "Sheet")
        },
        "projection_resources": [str(child.Name) for child in children],
        "projection_owners": {
            str(child.Name): str(child.VibeCADTimelineOwner.Name)
            for child in children
        },
        "page_resource": str(template.Name),
        "page_owner": str(template.VibeCADTimelineOwner.Name),
        "projection_sources": [
            str(item.Name) for item in projection.Source
        ],
        "child_sources": {
            str(child.Name): [str(item.Name) for item in child.Source]
            for child in children
        },
        "dimension_sources": [
            str(target.Name) for target in dimension_sources
        ],
    }


def _captured(
    root: Path,
    document,
    source,
    *,
    operation: str = "create_program",
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    pack = get_vibescript_pack("TechDrawWorkbench")
    assert pack is not None
    if arguments is None:
        arguments = {
            "program_name": "Native TechDraw lifecycle fixture",
            "source": _program_source(),
            "input_schema": _input_schema(),
            "inputs": {
                "solid": {
                    "document_uid": str(document.Uid),
                    "object_name": str(source.Name),
                },
                "x": 100.0,
            },
            "expected_outputs": EXPECTED_OUTPUTS,
        }
    return {
        "tool_name": f"vibescript.techdraw.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "techdraw-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "techdraw-native-fixture-revision",
        "document_objects": [
            {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "TechDrawWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 120.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _prepare_execute(
    captured: dict[str, object],
    service: _Service,
) -> tuple[dict[str, object], dict[str, object]]:
    prepared = prepare_candidate(captured)
    snapshots = capture_reference_inputs(service, prepared)
    prepared = finalize_candidate(prepared, snapshots)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    assert staged_names == {
        "request.json",
        "references",
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_part_worker.py",
        "vibescript_techdraw_api.py",
        "vibescript_techdraw_worker.py",
    }, sorted(staged_names)
    execution = execute_candidate(prepared, cancellation_check=None)
    return prepared, execution


def _prepare_execute_validate(
    captured: dict[str, object],
    service: _Service,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    prepared, execution = _prepare_execute(captured, service)
    assert execution.get("ok") is True, execution
    return prepared, execution, validate_candidate(prepared, execution)


def _publish_guarded(
    service: _Service,
    prepared: dict[str, object],
    validated: dict[str, object],
) -> dict[str, object]:
    import vibescript_techdraw_worker as worker

    guarded = [
        (Path, "read_bytes"),
        (Path, "read_text"),
        (Path, "write_bytes"),
        (Path, "write_text"),
        (subprocess, "Popen"),
        (subprocess, "run"),
        (subprocess, "call"),
        (subprocess, "check_call"),
        (subprocess, "check_output"),
        (worker, "validate_and_build_techdraw"),
    ]
    with ExitStack() as stack:
        for target, name in guarded:
            stack.enter_context(
                patch.object(
                    target,
                    name,
                    side_effect=AssertionError(
                        f"TechDraw document publication called forbidden {name}."
                    ),
                )
            )
        return publish_candidate(service, prepared, validated)


def _publish_and_accept(
    service: _Service,
    prepared: dict[str, object],
    validated: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    retain_candidate(prepared, status="validated")
    publication = _publish_guarded(service, prepared, validated)
    return publication, accept_candidate(prepared, publication)


def _managed(document, program_id: str) -> list[object]:
    return [
        obj
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    ]


def _snapshot(document, program_id: str) -> dict[str, object]:
    result = {}
    for obj in _managed(document, program_id):
        item: dict[str, object] = {
            "name": str(obj.Name),
            "type": str(obj.TypeId),
            "label": str(obj.Label),
            "revision": str(getattr(obj, PROP_PROGRAM_REVISION, "") or ""),
            "frozen": bool(obj.isFrozen()),
            "validation": str(getattr(obj, PROP_TECHDRAW_VALIDATION, "") or ""),
        }
        if obj.TypeId in {"TechDraw::DrawViewPart", "TechDraw::DrawProjGroupItem"}:
            projection = obj.getPrecomputedProjection()
            item["projection"] = {
                "edges": len(projection["edges"].Edges),
                "faces": len(projection["faces"].Faces),
                "classes": list(projection["edge_classes"]),
                "visibility": list(projection["edge_visibility"]),
                "source_indices": list(projection["source_indices"]),
                "centroid": [float(value) for value in projection["centroid"]],
                "x": float(obj.X),
                "y": float(obj.Y),
            }
        elif obj.TypeId == "TechDraw::DrawViewDimension":
            dimension = obj.getPrecomputedDimension()
            item["dimension"] = {
                "vectors": [[float(value) for value in row] for row in dimension["vectors"]],
                "scalars": [float(value) for value in dimension["scalars"]],
                "flags": list(dimension["flags"]),
                "raw_value": float(obj.getRawValue()),
                "text": str(obj.getText()),
            }
        elif obj.TypeId == "TechDraw::DrawPage":
            item["page"] = {
                "template": str(obj.Template.Name),
                "views": [str(value.Name) for value in obj.Views],
                "keep_updated": bool(obj.KeepUpdated),
            }
        elif obj.TypeId == "TechDraw::DrawProjGroup":
            item["group"] = [str(value.Name) for value in obj.Views]
        result[str(obj.Name)] = item
    return result


def _exercise_api() -> None:
    api = TechDrawDomainAPI(_EXPORTS, _OUTPUT_TYPES)
    assert api.exported_names == _EXPORTS
    for name in _EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(getattr(api, name))
    for redundant in (
        "output",
        "svg_template",
        "front_view",
        "top_view",
        "isometric_view",
        "radius_dimension",
        "diameter_dimension",
        "project",
        "recompute",
        "export",
    ):
        assert not hasattr(api, redundant), redundant
    reference = {"document_uid": "document", "object_name": "Solid"}
    template = api.template()
    view = api.view([reference])
    dimension = api.dimension(view, "distance", ["Edge0"])
    annotation = api.annotation("NOTE")
    page = api.page(template, [view, dimension, annotation])
    for value in (template, view, dimension, annotation, page):
        validate_techdraw_definition(value)
    _expect_error(
        "projection_direction",
        lambda: api.dimension(
            view,
            "distance",
            ["Edge0"],
            projection_direction="front",
        ),
    )
    _expect_error(
        "duplicate definitions",
        lambda: api.page(template, [view, view]),
    )
    try:
        api.dimension(view, "radius", ["Vertex0"])
    except TechDrawAPIError as exc:
        assert exc.details == {
            "stage": "source_validation",
            "operation": "dimension",
            "parameter": "references",
            "reason": (
                "must contain exactly one circular EdgeN reference for kind 'radius'"
            ),
            "correction": exc.details["correction"],
        }
        assert "api.dimension parameter 'references'" in exc.details["correction"]
    else:
        raise AssertionError("Expected structured TechDraw source validation failure.")
    other_view = api.view([reference], orientation="top")
    other_dimension = api.dimension(other_view, "distance", ["Edge0"])
    _expect_error(
        "exact source view/projection is not in the same page",
        lambda: api.page(template, [view, other_dimension]),
    )
    first_angle = api.projection(
        [reference], directions=["front", "top"], convention="first_angle"
    )
    _expect_error(
        "must match the page convention",
        lambda: api.page(template, [first_angle], convention="third_angle"),
    )
    projection = api.projection([reference])
    projected_dimension = api.dimension(
        projection,
        "distance",
        ["Edge0"],
        projection_direction="top",
    )
    graph_page = api.page(
        template,
        [projection, projected_dimension, annotation],
    )
    exact_graph = {
        "Template": template,
        "Views": projection,
        "Width": projected_dimension,
        "Note": annotation,
        "Sheet": graph_page,
        "Unexpected": annotation,
    }
    error = _expect_candidate_error(
        "result_contract",
        lambda: _validate_graph(exact_graph, EXPECTED_OUTPUTS),
    )
    assert error.details["extra"] == ["Unexpected"]


def _exercise_lifecycle() -> None:
    surface = resolve_modeling_surface("TechDrawWorkbench", "vibescript")
    assert surface.available, surface.unavailable_reason
    assert len(surface.tool_names) == 10
    assert not any(name.startswith("native.") for name in surface.tool_names)
    adapter = get_domain_adapter("techdraw")
    assert adapter is not None and adapter.production_ready
    description = adapter.describe_api()
    assert [item["name"] for item in description["runtime_exports"]] == list(
        _EXPORTS
    )
    assert "selectors rather than parallel aliases" in description["redundancy_contract"]
    assert "first accept" in " ".join(description["model_authoring_flow"])
    assert "dimension_reference_inventory" in str(
        description["projected_reference_contract"]
    )
    assert "section/detail" in description["operating_scope"]["not_exposed"]
    assert len(json.dumps(description).encode("utf-8")) <= 48 * 1024
    recommended = description["recommended_patterns"][0]
    assert recommended["expected_outputs"] == EXPECTED_OUTPUTS
    assert str(recommended["source"]).strip() == _program_source().strip()

    with tempfile.TemporaryDirectory(prefix="vibecad-techdraw-lifecycle-") as raw_root:
        root = Path(raw_root).resolve()
        document = App.newDocument("TechDrawVibeScriptLifecycle")
        try:
            document.setUndoMode(1)
            source = document.addObject("Part::Feature", "SourceSolid")
            source.Label = "Human source solid"
            source.Shape = Part.makeBox(30, 20, 10)
            source_name = str(source.Name)
            document.commitTransaction()
            service = _Service(root)
            prepared, execution, validated = _prepare_execute_validate(
                _captured(root, document, source), service
            )
            assert validated["techdraw_validation"] == execution["techdraw_validation"]
            by_name = {item["name"]: item for item in validated["outputs"]}
            assert set(by_name["Views"]["detached_projection_children"]) == {
                "front",
                "top",
                "right",
            }
            top_descriptors = by_name["Views"]["techdraw_data"]["children"]["top"][
                "descriptors"
            ]
            assert top_descriptors["edges"][0]["name"] == "Edge0"
            assert top_descriptors["edges"][0]["source_mapping"]["status"] in {
                "exact",
                "ambiguous",
                "generated_projection",
                "unmapped",
            }
            assert len(by_name["Width"]["detached_dimension"]["vectors"]) == 18
            malformed = copy.deepcopy(execution)
            malformed["techdraw_validation"]["output_count"] -= 1
            _expect_error(
                "output count is inconsistent",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][1]["techdraw_data"]["children"]["top"][
                "edges_artifact"
            ]["artifact_sha256"] = "0" * 64
            _expect_error(
                "not an authenticated TechDraw BREP",
                lambda: validate_candidate(prepared, malformed),
            )

            publication, accepted = _publish_and_accept(
                service, prepared, validated
            )
            assert publication["projection_generation_on_document_thread"] is False
            outputs = {
                name: document.getObject(details["object_name"])
                for name, details in accepted["live_outputs"].items()
            }
            assert {name: obj.TypeId for name, obj in outputs.items()} == {
                "Template": "TechDraw::DrawTemplate",
                "Views": "TechDraw::DrawProjGroup",
                "Width": "TechDraw::DrawViewDimension",
                "Note": "TechDraw::DrawViewAnnotation",
                "Sheet": "TechDraw::DrawPage",
            }
            children = {str(child.Type): child for child in outputs["Views"].Views}
            assert set(children) == {"Front", "Top", "Right"}
            _assert_projection_timeline_block(document, outputs["Views"])
            _assert_page_timeline_block(
                document,
                outputs["Sheet"],
                outputs["Template"],
            )
            assert all(child.isFrozen() for child in children.values())
            assert all(obj.isFrozen() for obj in outputs.values())
            assert outputs["Sheet"].KeepUpdated is False
            assert outputs["Note"].TextAlignment == "Left"
            assert abs(float(outputs["Width"].getRawValue()) - 20.0) <= 1.0e-9
            assert all(
                PROP_TECHDRAW_VALIDATION in obj.PropertiesList
                for obj in [*outputs.values(), *children.values()]
            )
            views_validation = json.loads(
                str(getattr(outputs["Views"], PROP_TECHDRAW_VALIDATION))
            )
            top_inventory = views_validation["children"]["top"][
                "dimension_reference_inventory"
            ]
            assert top_inventory["index_base"] == 0
            assert top_inventory["edge_count"] >= 1
            assert top_inventory["edge_samples"][0]["name"].startswith("Edge")
            assert top_inventory["recommended_by_kind"]["distance"]
            deferred_context = domain_context_snapshot(service, "techdraw")
            completed_context = complete_domain_context(deferred_context)
            assert completed_context["domain"] == "techdraw"
            assert completed_context["document_techdraw"]["object_count"] == 8
            assert any(
                item.get("eligible_for_techdraw_reference") is True
                and item.get("name") == source_name
                for item in completed_context["techdraw_reference_candidates"][
                    "objects"
                ]
            )
            encoded_context = str(completed_context)
            assert "artifact_path" not in encoded_context
            assert "descriptors" not in encoded_context
            accepted_objects = [
                item
                for item in completed_context["document_techdraw"]["objects"]
                if item.get("program_id") == prepared["program_id"]
            ]
            accepted_views = next(
                item for item in accepted_objects if item.get("program_output") == "Views"
            )
            accepted_inventory = accepted_views["accepted_validation"]["children"][
                "top"
            ]["dimension_reference_inventory"]
            assert accepted_inventory["edge_count"] == top_inventory["edge_count"]
            assert accepted_inventory["edge_samples"][0]["name"].startswith("Edge")

            stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
            stable_children = {
                str(child.Type): str(child.Name) for child in outputs["Views"].Views
            }
            created_state = _assert_techdraw_timeline_graph(
                document,
                outputs,
                source,
            )
            created_managed_names = {
                str(obj.Name) for obj in _managed(document, prepared["program_id"])
            }
            assert document.undo()
            assert not _managed(document, prepared["program_id"])
            assert document.getObject(source_name) is not None
            assert document.redo()
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(outputs.values())
            assert {
                str(obj.Name) for obj in _managed(document, prepared["program_id"])
            } == created_managed_names
            assert _assert_techdraw_timeline_graph(
                document,
                outputs,
                document.getObject(source_name),
            ) == created_state
            invalid_reference = "['Edge999']"
            failed_reference, failed_execution = _prepare_execute(
                _captured(
                    root,
                    document,
                    source,
                    operation="edit_source",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "source": _program_source().replace(
                            "['Edge0']",
                            invalid_reference,
                        ),
                    },
                ),
                service,
            )
            assert failed_execution["ok"] is False
            assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
            assert failed_execution["domain_failure_stage"] == "dimension_reference"
            failure_details = failed_execution["observed"]["details"]
            assert failure_details["reference"] == "Edge999"
            failure_inventory = failure_details["dimension_reference_inventory"]
            assert failure_inventory["edge_count"] >= 1
            assert failure_inventory["edge_samples"][0]["name"].startswith("Edge")
            assert failed_execution["retry"]["required_changes"] == [
                failure_details["correction"]
            ]
            assert "dimension_reference_inventory" in failure_details["correction"]
            retain_candidate(
                failed_reference,
                status="failed",
                failure=failed_execution,
            )
            assert {
                name: str(obj.Name) for name, obj in outputs.items()
            } == stable_names
            assert all(
                str(getattr(obj, PROP_PROGRAM_REVISION))
                == accepted["accepted_revision"]
                for obj in outputs.values()
            )

            recovered, _recovery_execution, recovered_validated = (
                _prepare_execute_validate(
                    _captured(
                        root,
                        document,
                        source,
                        operation="edit_source",
                        arguments={
                            "program_id": prepared["program_id"],
                            "expected_revision": failed_reference["revision"],
                            "source": _program_source(),
                        },
                    ),
                    service,
                )
            )
            recovery_publication, accepted = _publish_and_accept(
                service,
                recovered,
                recovered_validated,
            )
            assert recovery_publication["created_objects"] == []
            outputs = {
                name: document.getObject(details["object_name"])
                for name, details in accepted["live_outputs"].items()
            }
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
            updated, _execution, update_validated = _prepare_execute_validate(
                _captured(
                    root,
                    document,
                    source,
                    operation="set_inputs",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "patch": {"x": 120.0},
                    },
                ),
                service,
            )
            update_publication, accepted = _publish_and_accept(
                service, updated, update_validated
            )
            assert update_publication["created_objects"] == []
            outputs = {
                name: document.getObject(details["object_name"])
                for name, details in accepted["live_outputs"].items()
            }
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
            assert {
                str(child.Type): str(child.Name) for child in outputs["Views"].Views
            } == stable_children
            _assert_projection_timeline_block(document, outputs["Views"])
            _assert_page_timeline_block(
                document,
                outputs["Sheet"],
                outputs["Template"],
            )
            assert abs(float(outputs["Views"].X) - 120.0) <= 1.0e-9
            updated_state = _assert_techdraw_timeline_graph(
                document,
                outputs,
                document.getObject(source_name),
            )
            assert document.undo()
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(outputs.values())
            assert abs(float(outputs["Views"].X) - 100.0) <= 1.0e-9
            _assert_techdraw_timeline_graph(
                document,
                outputs,
                document.getObject(source_name),
            )
            assert document.redo()
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(outputs.values())
            assert abs(float(outputs["Views"].X) - 120.0) <= 1.0e-9
            assert _assert_techdraw_timeline_graph(
                document,
                outputs,
                document.getObject(source_name),
            ) == updated_state

            accepted_snapshot = _snapshot(document, prepared["program_id"])
            failed, _execution, failed_validated = _prepare_execute_validate(
                _captured(
                    root,
                    document,
                    source,
                    operation="set_inputs",
                    arguments={
                        "program_id": prepared["program_id"],
                        "expected_revision": accepted["working_revision"],
                        "patch": {"x": 130.0},
                    },
                ),
                service,
            )
            retain_candidate(failed, status="validated")

            def fail_publication(stage: str, output_key: str, _obj) -> None:
                if stage == "before_freeze" and output_key == "Width":
                    raise RuntimeError("injected TechDraw publication failure")

            with patch.object(
                publication_module,
                "_techdraw_publication_checkpoint",
                side_effect=fail_publication,
            ):
                _expect_error(
                    "injected TechDraw publication failure",
                    lambda: publish_candidate(service, failed, failed_validated),
                )
            assert _snapshot(document, prepared["program_id"]) == accepted_snapshot

            save_path = root / "techdraw-native-publication.FCStd"
            document.saveAs(str(save_path))
            document_name = str(document.Name)
            App.closeDocument(document_name)
            document = App.openDocument(str(save_path))
            App.setActiveDocument(document.Name)
            document.setUndoMode(1)
            reopened_snapshot = _snapshot(document, prepared["program_id"])
            assert reopened_snapshot == accepted_snapshot
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(obj.isFrozen() for obj in outputs.values())
            _assert_projection_timeline_block(document, outputs["Views"])
            _assert_page_timeline_block(
                document,
                outputs["Sheet"],
                outputs["Template"],
            )
            assert abs(float(outputs["Width"].getRawValue()) - 20.0) <= 1.0e-9

            source = document.getObject(source_name)
            assert source is not None
            delete_request = _captured(
                root,
                document,
                source,
                operation="delete_program",
                arguments={
                    "program_id": prepared["program_id"],
                    # The injected publication failure remains the persisted
                    # working candidate while the older accepted graph stays live.
                    "expected_revision": failed["revision"],
                    "reason": "native TechDraw lifecycle gate",
                },
            )
            prepared_delete = prepare_delete(delete_request)
            deletion = delete_live_program(service, prepared_delete)
            finished = finish_delete(prepared_delete, deletion)
            assert finished["artifacts_deleted"] is True
            assert not _managed(document, prepared["program_id"])
            assert document.getObject(source_name) is not None
            assert document.undo()
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(outputs.values())
            assert _assert_techdraw_timeline_graph(
                document,
                outputs,
                document.getObject(source_name),
            ) == updated_state
            assert document.redo()
            assert not _managed(document, prepared["program_id"])
            assert document.getObject(source_name) is not None
        finally:
            if App.getDocument(document.Name) is not None:
                App.closeDocument(document.Name)


def main() -> None:
    _exercise_api()
    _exercise_lifecycle()
    print("TechDraw VibeScript native API/worker integration passed")


if __name__ == "__main__":
    main()
