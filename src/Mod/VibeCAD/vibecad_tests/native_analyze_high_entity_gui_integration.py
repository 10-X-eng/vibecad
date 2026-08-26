# SPDX-License-Identifier: LGPL-2.1-or-later

"""Maximum-size solid-domain and provider-context gate for Native Analyze."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADSession as Session
from VibeCADCore import get_service
from VibeCADNativeAnalyzeAssignments import list_assignments
from VibeCADNativeProviderRunner import NativeProviderToolRunner
from VibeCADNativeSessionFactory import create_native_session_execution
from VibeCADRibbonSurface import read_active_ribbon_surface


ENTITY_COUNT = 256


def _events(rounds: int = 8) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _activate_analyze():
    main_window = Gui.getMainWindow()
    controller = main_window.findChild(QtCore.QObject, "VibeCADRibbonController")
    tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
    assert controller is not None and tabs is not None
    index = next(
        item
        for item in range(tabs.count())
        if str(tabs.tabData(item)) == "FemWorkbench"
    )
    tabs.setCurrentIndex(index)
    _events(24)
    assert read_active_ribbon_surface(controller).surface_id == "analyze"
    return controller


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temporary = None
    runner = None
    exit_code = 1
    try:
        Gui.activateWorkbench("FemWorkbench")
        temporary = tempfile.TemporaryDirectory(
            prefix="vibecad-native-analyze-high-entity-"
        )
        output = Path(temporary.name) / "high-entity.FCStd"
        document = App.newDocument("NativeAnalyzeHighEntityGate")
        document.UndoMode = 1
        document.saveAs(str(output))
        source_names = []
        for index in range(ENTITY_COUNT):
            source = document.addObject("Part::Feature", f"Cell{index + 1:03d}")
            source.Label = f"Cell {index + 1}"
            source.Shape = Part.makeBox(
                4.0,
                4.0,
                4.0,
                App.Vector(float(index % 16) * 6.0, float(index // 16) * 6.0, 0.0),
            )
            source_names.append(str(source.Name))
        assert document.recompute() is not False

        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        controller = _activate_analyze()
        service = get_service()
        service.select_modeling_engine("native")
        call_count = 0

        def context() -> dict:
            return Session._context_for_provider(service)

        def call(tool: str, arguments: dict) -> dict:
            nonlocal call_count, runner
            call_count += 1
            turn = context()
            schemas = list(turn["provider_tool_schemas"])
            names = {str(schema.get("name") or "") for schema in schemas}
            assert tool in names, (tool, sorted(names))
            runner = NativeProviderToolRunner(
                execution=create_native_session_execution(
                    service=service,
                    expected_surface=dict(turn["provider_tool_surface"]),
                    expected_schemas=schemas,
                    controller=controller,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                ),
                document_dispatch=lambda operation: operation(),
                refresh_context=context,
                frozen_surface=dict(turn["provider_tool_surface"]),
                frozen_schemas=schemas,
                frozen_modeling_surface=dict(turn["modeling_surface"]),
                tool_trace=[],
            )
            try:
                response = runner(
                    tool,
                    json.dumps(arguments, separators=(",", ":")),
                    f"native-analyze-high-entity-{call_count}",
                )
            finally:
                runner.close()
                runner = None
            assert response.get("ok") is True, response
            return response

        initial = context()
        initial_domain = initial["native_state"]["domain"]
        assert initial_domain["geometry_source_count"] == ENTITY_COUNT
        assert initial_domain["geometry_sources_truncated"] is True
        initial_state_bytes = len(
            json.dumps(
                initial["native_state"],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

        domain_result = call(
            "analyze.solid_domain",
            {
                "source_names": source_names,
                "interface_mode": "separate",
                "label": "256-solid analysis domain",
            },
        )
        domain_name = str(domain_result["domain"]["object_name"])
        domain = document.getObject(domain_name)
        assert domain is not None and len(domain.Shape.Solids) == ENTITY_COUNT

        analysis_result = call(
            "analyze.model",
            {
                "operation": "create_analysis",
                "label": "High-entity structural analysis",
                "default_solver_policy": "none",
                "study": {"physics": ["mechanical"], "regime": "steady"},
            },
        )
        analysis_name = str(analysis_result["created_analysis"]["object_name"])
        catalog = call(
            "analyze.material_catalog",
            {"query": "steel", "category": "solid", "limit": 10},
        )
        call(
            "analyze.catalog_material",
            {
                "analysis_name": analysis_name,
                "source_name": domain_name,
                "material_name": str(catalog["materials"][0]["properties"]["name"]),
            },
        )

        final = context()
        final_state = final["native_state"]
        final_state_bytes = len(
            json.dumps(
                final_state,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        material = final_state["domain"]["materials"][0]
        subelements = material["references"][0]["subelements"]
        assert len(subelements) == ENTITY_COUNT
        assert subelements[0] == "Solid1"
        assert subelements[-1] == f"Solid{ENTITY_COUNT}"
        analysis = document.getObject(analysis_name)
        assignment_page = list_assignments(
            analysis,
            category="material",
            offset=0,
            page_size=1,
        )
        exact = assignment_page["assignments"][0]["references"][0]["subelements"]
        assert exact == subelements
        assert initial_state_bytes < 65536
        assert final_state_bytes < 65536

        document.save()
        App.closeDocument(document.Name)
        document = App.openDocument(str(output))
        App.setActiveDocument(document.Name)
        _events(12)
        reopened_domain = document.getObject(domain_name)
        reopened_analysis = document.getObject(analysis_name)
        assert reopened_domain is not None
        assert len(reopened_domain.Shape.Solids) == ENTITY_COUNT
        reopened_page = list_assignments(
            reopened_analysis,
            category="material",
            offset=0,
            page_size=1,
        )
        assert len(
            reopened_page["assignments"][0]["references"][0]["subelements"]
        ) == ENTITY_COUNT
        print(
            "VIBECAD_NATIVE_ANALYZE_HIGH_ENTITY_GUI_OK "
            f"solids={ENTITY_COUNT} initial_state_bytes={initial_state_bytes} "
            f"final_state_bytes={final_state_bytes} exact_assignments=true "
            "provider_turns=true save_reopen=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if runner is not None:
            runner.close()
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
