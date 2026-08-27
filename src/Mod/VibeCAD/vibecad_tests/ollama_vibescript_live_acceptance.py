# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live Ollama acceptance runner for a real VibeCAD authoring path."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import traceback
import zipfile

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtSvg, QtWidgets

import VibeCADCodex as CodexModule
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADMCP import get_control_mode_controller
from VibeCADLiveAcceptanceOracle import (
    AssemblyExpectations,
    copy_linked_document_dependencies,
    validate_assembly_input_snapshot,
    validate_assembly_snapshot,
)
from VibeCADProvider import CodexProvider, provider_tool_schema_digest
from VibeCADSession import run_native_surface_continuation, run_prompt


class _FilteredCodexProvider(CodexProvider):
    """Hide selected tools from one live acceptance run."""

    def __init__(self, *args, excluded_tools: frozenset[str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._excluded_tools = excluded_tools

    def _filtered_context(self, context: dict) -> dict:
        filtered = dict(context)

        def filter_surface(source: dict) -> dict:
            result = dict(source)
            schemas = [
                dict(schema)
                for schema in source.get("provider_tool_schemas") or []
                if str(schema.get("name") or "") not in self._excluded_tools
            ]
            surface = dict(source.get("provider_tool_surface") or {})
            surface["tool_names"] = [schema["name"] for schema in schemas]
            surface["schema_count"] = len(schemas)
            surface["schema_sha256"] = provider_tool_schema_digest(schemas)
            result["provider_tool_schemas"] = schemas
            result["provider_tool_surface"] = surface
            return result

        filtered.update(filter_surface(filtered))
        return filtered

    def run(self, prompt, context, *args, **kwargs):
        return super().run(
            prompt,
            self._filtered_context(context),
            *args,
            **kwargs,
        )


def _shape_summary(document) -> dict:
    objects = []
    solid_count = 0
    for obj in document.Objects:
        shape = getattr(obj, "Shape", None)
        is_null = getattr(shape, "isNull", None)
        if not callable(is_null) or bool(is_null()):
            continue
        solids = int(len(shape.Solids))
        solid_count += solids
        bounds = shape.BoundBox
        objects.append(
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
                "solids": solids,
                "valid": bool(shape.isValid()),
                "bounds_mm": [
                    float(bounds.XLength),
                    float(bounds.YLength),
                    float(bounds.ZLength),
                ],
            }
        )
    return {
        "document_object_count": int(len(document.Objects)),
        "shape_object_count": len(objects),
        "solid_count": solid_count,
        "objects": objects,
    }


def _drawing_acceptance_state(document):
    from VibeCADNativeDrawingReadiness import drawing_page_readiness
    from VibeCADNativeDrawingSnapshot import build_drawing_snapshot
    from VibeCADNativeDrawingState import drawing_page_state

    snapshot = build_drawing_snapshot(document)
    pages = list(snapshot["pages"])
    if int(snapshot["page_count"]) < 1 or not pages:
        raise AssertionError("Drawing acceptance produced no Drawing page.")
    if sum(int(page["view_count"]) for page in pages) < 1:
        raise AssertionError("Drawing acceptance produced no page views.")

    readiness = []
    for page_summary in pages:
        page = document.getObject(str(page_summary["object_name"]))
        if page is None:
            raise AssertionError(
                "Drawing acceptance page disappeared before verification: "
                f"{page_summary['object_name']}."
            )
        page.ViewObject.show()
        for _index in range(24):
            Gui.updateGui()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )
        state = drawing_page_state(page)
        readiness.append(
            drawing_page_readiness(
                document,
                target={
                    "object_name": state["object_name"],
                    "expected_state_sha256": state["state_sha256"],
                },
            )
        )
    return snapshot, pages, readiness


def _optional_nonnegative_integer(name: str) -> int | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 0:
        raise RuntimeError(f"{name} must be a non-negative integer.")
    return value


def _print_live_progress(event: dict) -> None:
    """Keep the exact model/tool sequence visible during long live runs."""

    if str(event.get("event") or "") not in {
        "context_build_completed",
        "provider_tool_requested",
        "provider_tool_result_sent",
        "provider_turn_completed",
        "provider_turn_failed",
    }:
        return
    visible = {
        key: event[key]
        for key in (
            "event",
            "provider",
            "workbench",
            "provider_tool_count",
            "tool_name",
            "arguments",
            "ok",
            "failure_stage",
            "error",
            "tool_count",
        )
        if key in event
    }
    print(
        "VIBECAD_OLLAMA_LIVE_EVENT "
        + json.dumps(visible, ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    prompt = str(os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT") or "").strip()
    artifact = Path(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_ARTIFACT")
        or "/tmp/vibecad-ollama-acceptance.FCStd"
    ).expanduser().resolve()
    input_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_INPUT") or ""
    ).strip()
    input_fixture = (
        Path(input_raw).expanduser().resolve() if input_raw else None
    )
    input_state = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_INPUT_STATE") or "source_only"
    ).strip().lower()
    reference_image_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_REFERENCE_IMAGE") or ""
    ).strip()
    reference_image = (
        Path(reference_image_raw).expanduser().resolve()
        if reference_image_raw
        else None
    )
    step_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_STEP") or ""
    ).strip()
    step_artifact = (
        Path(step_raw).expanduser().resolve()
        if step_raw
        else artifact.with_suffix(".step")
    )
    output_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_OUTPUT") or ""
    ).strip()
    output_artifact = (
        Path(output_raw).expanduser().resolve() if output_raw else None
    )
    model = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_MODEL") or "qwen3.5:9b"
    ).strip()
    base_url = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    ).strip()
    reasoning_effort = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_REASONING_EFFORT") or "high"
    ).strip()
    auth_mode = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_AUTH_MODE") or "api_key"
    ).strip().lower()
    engine = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_ENGINE") or "vibescript"
    ).strip().lower()
    timeout_seconds = int(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_TIMEOUT_SECONDS") or "900"
    )
    expected_volume_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_VOLUME_MM3") or ""
    ).strip()
    expected_volume = (
        float(expected_volume_raw) if expected_volume_raw else None
    )
    expected_bounds_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_BOUNDS_JSON") or ""
    ).strip()
    expected_bounds = json.loads(expected_bounds_raw) if expected_bounds_raw else None
    mesh_expectations_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_MESH_EXPECTATIONS_JSON") or ""
    ).strip()
    mesh_expectations = (
        json.loads(mesh_expectations_raw) if mesh_expectations_raw else {}
    )
    maximum_failures_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_MAX_FAILED_CALLS") or ""
    ).strip()
    maximum_failures = int(maximum_failures_raw) if maximum_failures_raw else None
    expected_result_type = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_RESULT_TYPE")
        or "PartDesign::Body"
    ).strip()
    result_kind = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_RESULT_KIND") or "single_solid"
    ).strip().lower()
    workbench = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_WORKBENCH")
        or "PartDesignWorkbench"
    ).strip()
    active_object_name = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_ACTIVE_OBJECT") or ""
    ).strip()
    assembly_expectations = AssemblyExpectations(
        assemblies=(
            _optional_nonnegative_integer(
                "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_ASSEMBLY_COUNT"
            )
            if str(
                os.environ.get(
                    "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_ASSEMBLY_COUNT"
                )
                or ""
            ).strip()
            else 1
        ),
        components=_optional_nonnegative_integer(
            "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_COMPONENT_COUNT"
        ),
        joints=_optional_nonnegative_integer(
            "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_JOINT_COUNT"
        ),
        grounded=_optional_nonnegative_integer(
            "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_GROUNDED_COUNT"
        ),
        boms=_optional_nonnegative_integer(
            "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_BOM_COUNT"
        ),
        remaining_degrees_of_freedom=_optional_nonnegative_integer(
            "VIBECAD_OLLAMA_ACCEPTANCE_EXPECTED_REMAINING_DOF"
        ),
    )
    excluded_tools = frozenset(
        name.strip()
        for name in str(
            os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_EXCLUDE_TOOLS") or ""
        ).split(",")
        if name.strip()
    )
    result: dict[str, object] = {}
    provider_worker = None
    cancel_requested = threading.Event()
    termination_requested = threading.Event()
    termination_checkpointed = threading.Event()
    timed_out = False
    poll = QtCore.QTimer()
    checkpoint = QtCore.QTimer()
    timeout = QtCore.QTimer()
    document = None
    final_state_saved = False
    copied_dependencies: tuple[Path, ...] = ()

    def save_checkpoint() -> None:
        if document is None:
            return
        try:
            document_name = str(document.Name)
            document_path = Path(str(document.FileName)).resolve()
        except ReferenceError:
            return
        if (
            document_name not in App.listDocuments()
            or document_path != artifact
            or not artifact.is_file()
        ):
            return
        document.save()
        with zipfile.ZipFile(artifact) as archive:
            if (
                "Document.xml" not in archive.namelist()
                or archive.testzip() is not None
            ):
                raise RuntimeError(f"Invalid FCStd checkpoint: {artifact}")

    def save_final_state() -> None:
        nonlocal final_state_saved
        if final_state_saved or document is None:
            return
        # Keep a recoverable checkpoint before closing any active task, then
        # persist the normal post-task presentation that a user will reopen.
        save_checkpoint()
        gui_document = Gui.activeDocument()
        if gui_document is not None and gui_document.getInEdit():
            gui_document.resetEdit()
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)
        document.recompute()
        save_checkpoint()
        final_state_saved = True

    def reopen_final_state() -> None:
        nonlocal document
        if document is None:
            raise RuntimeError("Live acceptance has no document to reopen.")
        document_name = str(document.Name)
        App.closeDocument(document_name)
        document = App.openDocument(str(artifact))
        if document is None:
            raise RuntimeError(f"Live acceptance could not reopen {artifact}.")
        App.setActiveDocument(document.Name)
        for _index in range(12):
            Gui.updateGui()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )

    def finish(code: int) -> None:
        poll.stop()
        checkpoint.stop()
        timeout.stop()
        if document is not None and document.Name in App.listDocuments():
            try:
                save_final_state()
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                code = 1
        # Preserve the rollout JSONL for exact post-run diagnosis. The live
        # acceptance process owns this transport, so closing it is sufficient;
        # deleting the thread would discard the strongest model evidence.
        CodexModule.shutdown_managed_codex_sessions()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(code)

    try:
        if not prompt:
            raise RuntimeError("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT is required.")
        if engine not in {"native", "vibescript"}:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_ENGINE must be native or vibescript."
            )
        if input_state not in {"source_only", "assembled"}:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_INPUT_STATE must be source_only or assembled."
            )
        if auth_mode not in {"api_key", "chatgpt"}:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_AUTH_MODE must be api_key or chatgpt."
            )
        if result_kind not in {
            "single_solid",
            "assembly",
            "analysis",
            "drawing",
            "mesh",
            "manufacture",
        }:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_RESULT_KIND must be single_solid, "
                "assembly, analysis, drawing, mesh, or manufacture."
            )
        if expected_volume is not None and result_kind != "single_solid":
            raise RuntimeError("Expected volume applies only to a single-solid run.")
        if expected_bounds is not None and result_kind != "single_solid":
            raise RuntimeError("Expected bounds apply only to a single-solid run.")
        if not isinstance(mesh_expectations, dict):
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_MESH_EXPECTATIONS_JSON must be an object."
            )
        if mesh_expectations and result_kind != "mesh":
            raise RuntimeError("Mesh expectations apply only to a Mesh run.")
        if input_fixture is not None and not input_fixture.is_file():
            raise RuntimeError(
                f"VIBECAD_OLLAMA_ACCEPTANCE_INPUT does not exist: {input_fixture}"
            )
        if input_fixture is not None and input_fixture == artifact:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_ARTIFACT must not overwrite its input fixture."
            )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if output_artifact is not None:
            output_artifact.parent.mkdir(parents=True, exist_ok=True)
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui.ensure_commands_registered()
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.getMainWindow().resize(1440, 900)
        Gui.getMainWindow().show()
        service = get_service()
        if input_fixture is not None and input_fixture.suffix.lower() == ".fcstd":
            document = App.openDocument(str(input_fixture))
        else:
            document = App.newDocument(
                "OllamaNativeAcceptance"
                if engine == "native"
                else "OllamaVibeScriptAcceptance"
            )
            if input_fixture is not None:
                from VibeCADNativeMeshImport import MESH_IMPORT_SUFFIXES

                if input_fixture.suffix.lower() in MESH_IMPORT_SUFFIXES:
                    import Mesh

                    Mesh.insert(str(input_fixture), document.Name)
                else:
                    import Import

                    Import.insert(str(input_fixture), document.Name)
        if document is None:
            raise RuntimeError("FreeCAD did not open the acceptance document.")
        if Gui.activeWorkbench().name() == workbench:
            Gui.activateWorkbench("NoneWorkbench")
        Gui.activateWorkbench(workbench)
        # Let document and ribbon activation finish before selecting authority;
        # their queued initialization restores the saved document preference.
        for _ in range(24):
            Gui.updateGui()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )
        service.select_modeling_engine(engine)
        if result_kind == "assembly":
            from VibeCADNativeAssemblySnapshot import build_assembly_snapshot

            input_snapshot = build_assembly_snapshot(document)
            input_snapshot["assembly_owned_object_count"] = sum(
                1
                for obj in document.Objects
                if "VibeCADVibeScriptDomain" in obj.PropertiesList
                and str(obj.VibeCADVibeScriptDomain) == "assembly"
            )
            result["input_evidence"] = validate_assembly_input_snapshot(
                input_snapshot,
                allow_existing=input_state == "assembled",
            )
        elif result_kind == "drawing":
            from VibeCADNativeDrawingSnapshot import build_drawing_snapshot

            input_snapshot = build_drawing_snapshot(document)
            if int(input_snapshot["page_count"]) != 0:
                raise AssertionError(
                    "Drawing acceptance input must not contain an existing Drawing page."
                )
            if int(input_snapshot["source_count"]) < 1:
                raise AssertionError(
                    "Drawing acceptance input has no active design geometry."
                )
            result["input_evidence"] = {
                "source_count": int(input_snapshot["source_count"]),
                "page_count": int(input_snapshot["page_count"]),
            }
        elif result_kind == "manufacture":
            from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot

            input_snapshot = build_manufacture_snapshot(document)
            if int(input_snapshot["model_candidate_count"]) < 1:
                raise AssertionError(
                    "Manufacture acceptance input has no machinable model geometry."
                )
            result["input_evidence"] = {
                "model_candidate_count": int(
                    input_snapshot["model_candidate_count"]
                ),
                "job_count": int(input_snapshot["job_count"]),
            }
        document.UndoMode = 1
        copied_dependencies = copy_linked_document_dependencies(
            document,
            artifact.parent,
        )
        document.saveAs(str(artifact))
        save_checkpoint()
        checkpoint.timeout.connect(save_checkpoint)
        checkpoint.start(15_000)
        application.aboutToQuit.connect(save_checkpoint)
        if active_object_name:
            active_object = document.getObject(active_object_name)
            if active_object is None:
                raise RuntimeError(
                    "VIBECAD_OLLAMA_ACCEPTANCE_ACTIVE_OBJECT does not exist: "
                    f"{active_object_name}"
                )
            if not Gui.activeDocument().setEdit(active_object.Name):
                raise RuntimeError(
                    "VibeCAD could not activate acceptance object "
                    f"{active_object_name}."
                )
            for _ in range(12):
                Gui.updateGui()
                QtWidgets.QApplication.processEvents(
                    QtCore.QEventLoop.AllEvents,
                    25,
                )
        service.clear_reference_images()
        if reference_image is not None:
            attached = service.attach_reference_image(
                str(reference_image),
                label="Dimensioned CAD drawing",
            )
            if attached.get("ok") is not True:
                raise RuntimeError(
                    f"Could not attach acceptance reference image: {attached}"
                )
        CodexModule.reset_managed_codex_sessions()
        provider = _FilteredCodexProvider(
            model=model,
            api_key=("ollama-local" if auth_mode == "api_key" else None),
            auth_mode=auth_mode,
            reasoning_effort=reasoning_effort,
            timeout_seconds=float(timeout_seconds),
            base_url=(base_url if auth_mode == "api_key" else None),
            web_search_enabled=False,
            skills_enabled=False,
            excluded_tools=excluded_tools,
        )
        output_authorizer = None
        if output_artifact is not None:
            from VibeCADNativeOutput import authorize_native_output_path

            def output_authorizer(request):
                return authorize_native_output_path(request, output_artifact)

        def run_provider() -> None:
            try:
                responses = []
                response = run_prompt(
                    prompt,
                    service=service,
                    provider=provider,
                    progress_callback=_print_live_progress,
                    cancellation_check=cancel_requested.is_set,
                    output_authorization_callback=output_authorizer,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
                responses.append(response)
                while True:
                    continuation = VibeGui._dispatch_to_document_thread(
                        lambda current=response: (
                            VibeGui._native_surface_continuation_event(current)
                        )
                    )
                    if continuation is None:
                        break
                    response = run_native_surface_continuation(
                        continuation,
                        service=service,
                        provider=provider,
                        progress_callback=_print_live_progress,
                        cancellation_check=cancel_requested.is_set,
                        output_authorization_callback=output_authorizer,
                        document_thread_dispatch=(
                            VibeGui._dispatch_to_document_thread
                        ),
                    )
                    responses.append(response)
                result["response"] = response
                result["responses"] = responses
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        provider_worker = threading.Thread(
            target=run_provider,
            name="VibeCAD-live-Ollama-provider",
            daemon=True,
        )
        provider_worker.start()

        def inspect() -> None:
            if termination_requested.is_set():
                if not termination_checkpointed.is_set():
                    checkpoint.stop()
                    termination_checkpointed.set()
                    save_checkpoint()
                cancel_requested.set()
                if provider_worker is None or not provider_worker.is_alive():
                    finish(130)
                return
            if provider_worker is not None and provider_worker.is_alive():
                return
            try:
                if timed_out:
                    raise TimeoutError(
                        f"Live acceptance exceeded {timeout_seconds} seconds."
                    )
                if "error" in result:
                    raise AssertionError(result.get("traceback")) from result["error"]
                response = result["response"]
                if response.error:
                    raise AssertionError(response.error)
                save_final_state()
                checkpoint.stop()
                reopen_final_state()
                assembly_evidence = None
                analysis_evidence = None
                drawing_evidence = None
                mesh_evidence = None
                manufacture_evidence = None
                if result_kind == "single_solid":
                    neutral_objects = [
                        obj
                        for obj in document.Objects
                        if str(getattr(obj, "TypeId", "")) == expected_result_type
                        and getattr(obj, "Shape", None) is not None
                        and not obj.Shape.isNull()
                        and len(obj.Shape.Solids) == 1
                        and bool(
                            getattr(
                                getattr(obj, "ViewObject", None),
                                "Visibility",
                                True,
                            )
                        )
                    ]
                    if len(neutral_objects) != 1:
                        raise AssertionError(
                            "Live STEP acceptance requires exactly one visible solid "
                            f"{expected_result_type}; found "
                            f"{[(obj.Name, obj.Label) for obj in neutral_objects]}."
                        )
                    final_shape = neutral_objects[0].Shape
                    if expected_volume is not None and not math.isclose(
                        float(final_shape.Volume),
                        expected_volume,
                        rel_tol=1.0e-9,
                        abs_tol=1.0e-7,
                    ):
                        raise AssertionError(
                            "Live acceptance volume mismatch: "
                            f"expected {expected_volume}, found {float(final_shape.Volume)}."
                        )
                    if expected_bounds is not None:
                        bounds = final_shape.optimalBoundingBox(False, False)
                        actual_bounds = {
                            "x": [float(bounds.XMin), float(bounds.XMax)],
                            "y": [float(bounds.YMin), float(bounds.YMax)],
                            "z": [float(bounds.ZMin), float(bounds.ZMax)],
                        }
                        for axis in ("x", "y", "z"):
                            expected_axis = expected_bounds.get(axis)
                            actual_axis = actual_bounds[axis]
                            if (
                                not isinstance(expected_axis, list)
                                or len(expected_axis) != 2
                                or any(
                                    not math.isclose(
                                        float(actual),
                                        float(expected),
                                        rel_tol=1.0e-9,
                                        abs_tol=1.0e-7,
                                    )
                                    for actual, expected in zip(
                                        actual_axis,
                                        expected_axis,
                                        strict=True,
                                    )
                                )
                            ):
                                raise AssertionError(
                                    "Live acceptance bounds mismatch: "
                                    f"expected {expected_bounds}, found {actual_bounds}."
                                )
                elif result_kind == "assembly":
                    from VibeCADNativeAssemblyComponents import assembly_components
                    from VibeCADNativeAssemblySnapshot import build_assembly_snapshot

                    assembly_snapshot = build_assembly_snapshot(document)
                    assembly_evidence = validate_assembly_snapshot(
                        assembly_snapshot,
                        assembly_expectations,
                    )
                    assembly = document.getObject(
                        assembly_evidence["assembly"]["object_name"]
                    )
                    if assembly is None:
                        raise AssertionError("Accepted Assembly no longer exists.")
                    neutral_objects = [
                        component
                        for component in assembly_components(assembly)
                        if getattr(component, "Shape", None) is not None
                        and not component.Shape.isNull()
                    ]
                    if not neutral_objects:
                        raise AssertionError(
                            "Accepted Assembly has no component geometry to export."
                        )
                elif result_kind == "analysis":
                    from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot

                    analysis_snapshot = build_analyze_snapshot(document)
                    if int(analysis_snapshot["analysis_count"]) < 1:
                        raise AssertionError(
                            "Analyze acceptance produced no FEM study."
                        )
                    analysis_evidence = {
                        key: analysis_snapshot[key]
                        for key in (
                            "analysis_count",
                            "material_count",
                            "fluid_constraint_count",
                            "mesh_definition_count",
                            "solver_count",
                            "result_count",
                            "provider_scope",
                            "analysis_workflows",
                        )
                    }
                    neutral_objects = [
                        obj
                        for obj in document.Objects
                        if callable(
                            getattr(getattr(obj, "Shape", None), "isNull", None)
                        )
                        and not obj.Shape.isNull()
                        and len(obj.Shape.Solids) > 0
                    ]
                    if not neutral_objects:
                        raise AssertionError(
                            "Analyze acceptance has no source solid geometry to export."
                        )
                elif result_kind == "drawing":
                    from VibeCADNativeGeometrySources import (
                        active_design_geometry_sources,
                    )

                    drawing_snapshot, pages, page_readiness = (
                        _drawing_acceptance_state(document)
                    )
                    unresolved = sum(
                        int(readiness["references"]["count"])
                        for readiness in page_readiness
                    )
                    unready = [
                        {
                            "object_name": readiness["page"]["object_name"],
                            "issues": list(readiness["issues"]),
                        }
                        for readiness in page_readiness
                        if readiness["ready"] is not True
                    ]
                    if unresolved or unready:
                        raise AssertionError(
                            "Drawing acceptance is not export-ready: "
                            f"unresolved_references={unresolved}, unready_pages={unready}."
                        )

                    def derived_count(type_id: str) -> int:
                        return sum(
                            1
                            for obj in document.Objects
                            if callable(getattr(obj, "isDerivedFrom", None))
                            and bool(obj.isDerivedFrom(type_id))
                        )

                    drawing_evidence = {
                        "page_count": int(drawing_snapshot["page_count"]),
                        "page_view_count": sum(
                            int(page["view_count"]) for page in pages
                        ),
                        "projected_view_count": derived_count(
                            "TechDraw::DrawViewPart"
                        ),
                        "dimension_count": derived_count(
                            "TechDraw::DrawViewDimension"
                        ),
                        "balloon_count": derived_count("TechDraw::DrawViewBalloon"),
                        "unresolved_reference_count": unresolved,
                        "export_ready": not unready,
                    }
                    neutral_objects = [
                        obj
                        for obj in active_design_geometry_sources(document)
                        if getattr(obj, "Shape", None) is not None
                        and not obj.Shape.isNull()
                        and len(obj.Shape.Solids) > 0
                    ]
                    if not neutral_objects:
                        raise AssertionError(
                            "Drawing acceptance has no source solid geometry to export."
                        )
                elif result_kind == "manufacture":
                    from VibeCADNativeManufactureSnapshot import (
                        build_manufacture_snapshot,
                    )

                    manufacture_snapshot = build_manufacture_snapshot(document)
                    jobs = list(manufacture_snapshot["jobs"])
                    if not jobs:
                        raise AssertionError(
                            "Manufacture acceptance produced no machining setup."
                        )
                    operation_count = sum(
                        int(job["counts"]["operations"]) for job in jobs
                    )
                    active_operation_count = sum(
                        int(job["counts"]["active_operations"]) for job in jobs
                    )
                    if operation_count < 1 or active_operation_count < 1:
                        raise AssertionError(
                            "Manufacture acceptance produced no active toolpath operation."
                        )
                    manufacture_evidence = {
                        "job_count": int(manufacture_snapshot["job_count"]),
                        "operation_count": operation_count,
                        "active_operation_count": active_operation_count,
                        "jobs": [
                            {
                                "object_name": job["object_name"],
                                "state_sha256": job["state_sha256"],
                                "counts": dict(job["counts"]),
                                "readiness": dict(job["readiness"]),
                                "toolpath_validity": dict(
                                    job["toolpath_validity"]
                                ),
                            }
                            for job in jobs
                        ],
                    }
                    source_names = {
                        str(model["object_name"])
                        for job in jobs
                        for model in job.get("models", ())
                    }
                    neutral_objects = [
                        document.getObject(name) for name in sorted(source_names)
                    ]
                    neutral_objects = [
                        obj
                        for obj in neutral_objects
                        if obj is not None
                        and getattr(obj, "Shape", None) is not None
                        and not obj.Shape.isNull()
                        and len(obj.Shape.Solids) > 0
                    ]
                    if not neutral_objects:
                        raise AssertionError(
                            "Manufacture acceptance has no source solid geometry to export."
                        )
                else:
                    import Mesh
                    import MeshGui
                    from vibecad_tests.mesh_acceptance import validate_mesh_quality

                    neutral_objects = [
                        obj
                        for obj in document.Objects
                        if bool(obj.isDerivedFrom("Mesh::Feature"))
                        and int(obj.Mesh.CountFacets) > 0
                        and bool(MeshGui.isNativeMeshInputActive(obj))
                        and bool(obj.ViewObject.Visibility)
                    ]
                    if not neutral_objects:
                        raise AssertionError(
                            "Mesh acceptance has no visible active Mesh with facets."
                        )
                    quality = []
                    for obj in neutral_objects:
                        report = Mesh.evaluateNative(obj.Mesh, "strict", 32)
                        quality.append(
                            {
                                "object_name": str(obj.Name),
                                "label": str(obj.Label),
                                "points": int(obj.Mesh.CountPoints),
                                "facets": int(obj.Mesh.CountFacets),
                                "solid": bool(report.get("solid", False)),
                                "watertight": bool(report.get("watertight", False)),
                                "issue_counts": {
                                    name: int(value.get("count", 0) or 0)
                                    for name, value in dict(
                                        report.get("issues") or {}
                                    ).items()
                                    if int(value.get("count", 0) or 0) > 0
                                },
                            }
                        )
                    mesh_evidence = validate_mesh_quality(
                        quality,
                        mesh_expectations,
                    )
                if output_artifact is not None and (
                    not output_artifact.is_file()
                    or output_artifact.stat().st_size <= 0
                ):
                    raise AssertionError(
                        "Live acceptance did not produce its authorized output artifact: "
                        f"{output_artifact}."
                    )
                failed_calls = [
                    item
                    for turn in result.get("responses", [response])
                    for item in turn.tool_trace
                    if item.get("ok") is not True
                ]
                if (
                    maximum_failures is not None
                    and len(failed_calls) > maximum_failures
                ):
                    raise AssertionError(
                        "Live acceptance exceeded its failed-call limit: "
                        f"expected at most {maximum_failures}, found {len(failed_calls)}."
                    )
                mesh_artifact = None
                if result_kind == "mesh":
                    import Mesh

                    mesh_artifact = artifact.with_suffix(".stl")
                    Mesh.export(neutral_objects, str(mesh_artifact))
                    imported_mesh = Mesh.read(str(mesh_artifact))
                    if int(imported_mesh.CountFacets) != mesh_evidence["facet_count"]:
                        raise AssertionError(
                            "Acceptance STL facet count changed during export."
                        )
                else:
                    import Part

                    step_artifact.parent.mkdir(parents=True, exist_ok=True)
                    expected_step_solids = sum(
                        len(obj.Shape.Solids) for obj in neutral_objects
                    )
                    export_shape = Part.makeCompound(
                        [obj.Shape.copy() for obj in neutral_objects]
                    )
                    export_shape.exportStep(str(step_artifact))
                    if not step_artifact.is_file() or step_artifact.stat().st_size <= 0:
                        raise AssertionError("FreeCAD did not write the acceptance STEP file.")
                    imported_step = Part.read(str(step_artifact))
                    if (
                        imported_step.isNull()
                        or len(imported_step.Solids) != expected_step_solids
                    ):
                        raise AssertionError(
                            "Acceptance STEP geometry mismatch: "
                            f"expected {expected_step_solids} solids, found "
                            f"{len(imported_step.Solids)}."
                        )
                screenshot = artifact.with_suffix(".png")
                drawing_svg = None
                if result_kind == "drawing":
                    import TechDrawGui

                    page = document.getObject(str(pages[0]["object_name"]))
                    drawing_svg = artifact.with_suffix(".svg")
                    TechDrawGui.exportPageAsSvg(page, str(drawing_svg))
                    renderer = QtSvg.QSvgRenderer(str(drawing_svg))
                    if not renderer.isValid():
                        raise AssertionError(
                            "Drawing acceptance produced an invalid SVG export."
                        )
                    image_size = renderer.defaultSize()
                    if image_size.width() < 1 or image_size.height() < 1:
                        raise AssertionError(
                            "Drawing acceptance SVG has no renderable page size."
                        )
                    image = QtGui.QImage(
                        image_size,
                        QtGui.QImage.Format.Format_ARGB32,
                    )
                    image.fill(QtGui.QColor("white"))
                    painter = QtGui.QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    if not image.save(str(screenshot)):
                        raise AssertionError(
                            "Drawing acceptance could not render its SVG preview."
                        )
                else:
                    from tool_impl.service import core_capture_view_screenshot

                    capture = core_capture_view_screenshot.run(
                        service,
                        camera="isometric",
                        frame="objects",
                        object_names=[obj.Name for obj in neutral_objects],
                        sketch_annotations="clean",
                    )
                    if capture.get("ok") is not True:
                        raise AssertionError(
                            "Target-aware acceptance screenshot failed: "
                            f"{capture.get('failure_code')}: {capture.get('error')}"
                        )
                    capture_path = Path(str(capture["artifact"]["path"]))
                    if capture_path != screenshot:
                        shutil.copyfile(capture_path, screenshot)
                summary = {
                    "ok": True,
                    "model": model,
                    "engine": engine,
                    "result_kind": result_kind,
                    "workbench": workbench,
                    "active_object": active_object_name or None,
                    "reasoning_effort": reasoning_effort,
                    "auth_mode": auth_mode,
                    "expected_result_type": expected_result_type,
                    "mesh_expectations": mesh_expectations,
                    "excluded_tools": sorted(excluded_tools),
                    "artifact": str(artifact),
                    "input_fixture": (
                        str(input_fixture) if input_fixture is not None else None
                    ),
                    "input_state": input_state,
                    "linked_dependencies": [
                        str(path) for path in copied_dependencies
                    ],
                    "step": str(step_artifact) if result_kind != "mesh" else None,
                    "mesh_output": (
                        str(mesh_artifact) if mesh_artifact is not None else None
                    ),
                    "output": (
                        str(output_artifact)
                        if output_artifact is not None
                        else None
                    ),
                    "screenshot": str(screenshot),
                    "drawing_svg": (
                        str(drawing_svg) if drawing_svg is not None else None
                    ),
                    "reference_image": (
                        str(reference_image) if reference_image is not None else None
                    ),
                    "final_output": response.final_output,
                    "tool_trace": [
                        {
                            "tool": item.get("tool_name"),
                            "ok": item.get("ok"),
                            "failure_code": (
                                item.get("result", {}).get("failure_code")
                                if isinstance(item.get("result"), dict)
                                else None
                            ),
                            "error": (
                                item.get("result", {}).get("error")
                                if isinstance(item.get("result"), dict)
                                else None
                            ),
                        }
                        for turn in result.get("responses", [response])
                        for item in turn.tool_trace
                    ],
                    "turn_count": len(result.get("responses", [response])),
                    "assembly_evidence": assembly_evidence,
                    "analysis_evidence": analysis_evidence,
                    "drawing_evidence": drawing_evidence,
                    "mesh_evidence": mesh_evidence,
                    "manufacture_evidence": manufacture_evidence,
                    "shape_summary": _shape_summary(document),
                }
                print(
                    "VIBECAD_OLLAMA_LIVE_ACCEPTANCE "
                    + json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    flush=True,
                )
                finish(0)
            except BaseException:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        poll.timeout.connect(inspect)
        poll.start(100)
        timeout.setSingleShot(True)

        def request_timeout() -> None:
            nonlocal timed_out
            save_checkpoint()
            timed_out = True
            cancel_requested.set()

            def force_cancel() -> None:
                if provider_worker is not None and provider_worker.is_alive():
                    CodexModule.shutdown_managed_codex_sessions()

            QtCore.QTimer.singleShot(10_000, force_cancel)

        timeout.timeout.connect(request_timeout)
        timeout.start(timeout_seconds * 1000)

        def request_termination(_signum, _frame) -> None:
            termination_requested.set()

        signal.signal(signal.SIGINT, request_termination)
        signal.signal(signal.SIGTERM, request_termination)
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
