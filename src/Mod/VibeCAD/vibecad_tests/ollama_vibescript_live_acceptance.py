# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live Ollama acceptance runner for a real VibeCAD authoring path."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import threading
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADCodex as CodexModule
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADMCP import get_control_mode_controller
from VibeCADProvider import CodexProvider
from VibeCADSession import run_native_surface_continuation, run_prompt


def _shape_summary(document) -> dict:
    objects = []
    solid_count = 0
    for obj in document.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or bool(shape.isNull()):
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


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    prompt = str(os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT") or "").strip()
    artifact = Path(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_ARTIFACT")
        or "/tmp/vibecad-ollama-acceptance.FCStd"
    ).expanduser().resolve()
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
    maximum_failures_raw = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_MAX_FAILED_CALLS") or ""
    ).strip()
    maximum_failures = int(maximum_failures_raw) if maximum_failures_raw else None
    result: dict[str, object] = {}
    provider_worker = None
    cancel_requested = threading.Event()
    timed_out = False
    poll = QtCore.QTimer()
    timeout = QtCore.QTimer()
    document = None

    def finish(code: int) -> None:
        poll.stop()
        timeout.stop()
        # Preserve the rollout JSONL for exact post-run diagnosis. The live
        # acceptance process owns this transport, so closing it is sufficient;
        # deleting the thread would discard the strongest model evidence.
        CodexModule.shutdown_managed_codex_sessions()
        if Gui.activeDocument() is not None and Gui.activeDocument().getInEdit():
            Gui.activeDocument().resetEdit()
        if document is not None and document.Name in App.listDocuments():
            try:
                document.recompute()
                document.save()
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
            App.closeDocument(document.Name)
        application.exit(code)

    try:
        if not prompt:
            raise RuntimeError("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT is required.")
        if engine not in {"native", "vibescript"}:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_ENGINE must be native or vibescript."
            )
        if auth_mode not in {"api_key", "chatgpt"}:
            raise RuntimeError(
                "VIBECAD_OLLAMA_ACCEPTANCE_AUTH_MODE must be api_key or chatgpt."
            )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.getMainWindow().resize(1440, 900)
        Gui.getMainWindow().show()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument(
            "OllamaNativeAcceptance"
            if engine == "native"
            else "OllamaVibeScriptAcceptance"
        )
        document.UndoMode = 1
        document.saveAs(str(artifact))
        service = get_service()
        service.select_modeling_engine(engine)
        # Workbench activation publishes the human ribbon surface through the Qt
        # event loop. Do not freeze a turn until that exact surface is available.
        for _ in range(24):
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
        provider = CodexProvider(
            model=model,
            api_key=("ollama-local" if auth_mode == "api_key" else None),
            auth_mode=auth_mode,
            reasoning_effort=reasoning_effort,
            timeout_seconds=float(timeout_seconds),
            base_url=(base_url if auth_mode == "api_key" else None),
            web_search_enabled=False,
            skills_enabled=False,
        )

        def run_provider() -> None:
            try:
                responses = []
                response = run_prompt(
                    prompt,
                    service=service,
                    provider=provider,
                    cancellation_check=cancel_requested.is_set,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
                responses.append(response)
                if engine == "native":
                    for _transition_index in range(12):
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
                            cancellation_check=cancel_requested.is_set,
                            document_thread_dispatch=(
                                VibeGui._dispatch_to_document_thread
                            ),
                        )
                        responses.append(response)
                    else:
                        raise RuntimeError(
                            "Native acceptance exceeded 12 exact CAD transitions."
                        )
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
                document.recompute()
                document.save()
                final_bodies = [
                    obj
                    for obj in document.Objects
                    if str(getattr(obj, "TypeId", "")) == "PartDesign::Body"
                    and getattr(obj, "Shape", None) is not None
                    and not obj.Shape.isNull()
                    and len(obj.Shape.Solids) == 1
                    and bool(getattr(getattr(obj, "ViewObject", None), "Visibility", True))
                ]
                if len(final_bodies) != 1:
                    raise AssertionError(
                        "Live STEP acceptance requires exactly one visible solid Body; "
                        f"found {[(obj.Name, obj.Label) for obj in final_bodies]}."
                    )
                final_shape = final_bodies[0].Shape
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
                import Part

                step_artifact.parent.mkdir(parents=True, exist_ok=True)
                Part.export(final_bodies, str(step_artifact))
                if not step_artifact.is_file() or step_artifact.stat().st_size <= 0:
                    raise AssertionError("FreeCAD did not write the acceptance STEP file.")
                view = Gui.activeDocument().activeView()
                view.viewAxonometric()
                view.fitAll()
                screenshot = artifact.with_suffix(".png")
                view.saveImage(str(screenshot), 1440, 900, "Current")
                summary = {
                    "ok": True,
                    "model": model,
                    "engine": engine,
                    "reasoning_effort": reasoning_effort,
                    "auth_mode": auth_mode,
                    "artifact": str(artifact),
                    "step": str(step_artifact),
                    "screenshot": str(screenshot),
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
            timed_out = True
            cancel_requested.set()

            def force_cancel() -> None:
                if provider_worker is not None and provider_worker.is_alive():
                    CodexModule.shutdown_managed_codex_sessions()

            QtCore.QTimer.singleShot(10_000, force_cancel)

        timeout.timeout.connect(request_timeout)
        timeout.start(timeout_seconds * 1000)
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
