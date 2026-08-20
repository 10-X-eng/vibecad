# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI gate for local stdio MCP control and dynamic tool parity."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets


def _wait(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the MCP integration condition.")


def _live_tool_contracts() -> list[dict[str, Any]]:
    from VibeCADCore import get_service
    from VibeCADMCP import controller_tool_schemas
    from VibeCADSession import _minimal_runtime_state, provider_tool_schemas

    service = get_service()
    workbench = service.active_workbench_name()
    return [
        *provider_tool_schemas(
            service,
            workbench,
            runtime_state=_minimal_runtime_state(service),
        ),
        *controller_tool_schemas(),
    ]


def _normalized_contracts(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from VibeCADMCPToolNames import mcp_wire_tool_schemas

    advertised, _routing = mcp_wire_tool_schemas(contracts)
    return [
        {
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "parameters": dict(item.get("parameters") or {}),
        }
        for item in advertised
    ]


def run() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import VibeCADGui
    from VibeCADMCP import get_control_mode_controller
    from VibeCADPreferences import (
        VibeCADMCPPreferencesPage,
        load_settings,
        set_mcp_enabled,
    )

    VibeCADGui.ensure_commands_registered()
    controller = get_control_mode_controller()
    original_setting = load_settings().mcp_enabled
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/VibeCAD")
    original_design_review = preferences.GetBool("DesignReviewEnabled", False)
    preferences.SetBool("DesignReviewEnabled", True)
    original_workbench = str(Gui.activeWorkbench().name())
    document = App.newDocument("VibeCADMCPIntegration")
    Gui.activateWorkbench("PartDesignWorkbench")
    if Gui.Control.activeDialog() is not None:
        Gui.Control.closeDialog()
        QtWidgets.QApplication.processEvents()
    expected_model = _normalized_contracts(_live_tool_contracts())
    request_assembly = threading.Event()
    assembly_changed = threading.Event()
    request_mesh = threading.Event()
    mesh_changed = threading.Event()
    request_restore = threading.Event()
    restore_changed = threading.Event()
    target_ready = threading.Event()
    continue_client = threading.Event()
    client_done = threading.Event()
    observed: dict[str, Any] = {}
    preference_page = None

    try:
        preference_page = VibeCADMCPPreferencesPage()
        preference_page.loadSettings()
        preference_page.mcp_enabled.setChecked(True)
        preference_page.saveSettings()
        _wait(lambda: controller.snapshot()["state"] == "mcp")
        assert controller.snapshot()["internal_agent_enabled"] is False
        assert VibeCADGui.AskAICommand().IsActive() is False
        configuration = controller.connection_configuration()

        def client_worker() -> None:
            async def execute() -> None:
                async def handle_message(message: Any) -> None:
                    if (
                        str(getattr(message, "method", ""))
                        == "notifications/tools/list_changed"
                    ):
                        observed["tool_list_notifications"] = (
                            int(observed.get("tool_list_notifications", 0)) + 1
                        )

                parameters = StdioServerParameters(
                    command=configuration["command"],
                    args=list(configuration["args"]),
                )
                async with stdio_client(parameters) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        message_handler=handle_message,
                    ) as session:
                        initialized = await session.initialize()
                        tool_capability = getattr(
                            initialized.capabilities, "tools", None
                        )
                        observed["tools_list_changed_capability"] = bool(
                            getattr(tool_capability, "list_changed", False)
                        )
                        listed = await session.list_tools()
                        observed["model_contracts"] = [
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": dict(tool.input_schema),
                            }
                            for tool in listed.tools
                        ]
                        workbench = await session.call_tool("ReadWorkbench", {})
                        observed["workbench_read"] = workbench.structured_content
                        api_read = await session.call_tool("ReadApi", {})
                        observed["api_read_error"] = api_read.is_error
                        review = await session.call_tool(
                            "ReviewDesign",
                            {
                                "customer_intent": "Verify MCP provider isolation.",
                                "design_draft": (
                                    "This test proposes no CAD mutation; it verifies that "
                                    "an MCP tool cannot launch any configured internal "
                                    "model provider."
                                ),
                            },
                        )
                        observed["review"] = review.structured_content
                        assembly_notification_count = int(
                            observed.get("tool_list_notifications", 0)
                        )
                        request_assembly.set()
                        await asyncio.to_thread(assembly_changed.wait)
                        assembly_tools = await session.list_tools()
                        observed["assembly_contracts"] = [
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": dict(tool.input_schema),
                            }
                            for tool in assembly_tools.tools
                        ]
                        await asyncio.sleep(0.25)
                        observed["assembly_tool_list_notifications"] = (
                            int(observed.get("tool_list_notifications", 0))
                            - assembly_notification_count
                        )
                        request_mesh.set()
                        await asyncio.to_thread(mesh_changed.wait)
                        notification_deadline = time.monotonic() + 5.0
                        while (
                            int(observed.get("tool_list_notifications", 0))
                            <= assembly_notification_count
                            and time.monotonic() < notification_deadline
                        ):
                            await asyncio.sleep(0.05)
                        mesh_tools = await session.list_tools()
                        observed["mesh_contracts"] = [
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": dict(tool.input_schema),
                            }
                            for tool in mesh_tools.tools
                        ]
                        target_ready.set()
                        await asyncio.to_thread(continue_client.wait)
                        notification_count = int(
                            observed.get("tool_list_notifications", 0)
                        )
                        request_restore.set()
                        await asyncio.to_thread(restore_changed.wait)
                        notification_deadline = time.monotonic() + 5.0
                        while (
                            int(observed.get("tool_list_notifications", 0))
                            <= notification_count
                            and time.monotonic() < notification_deadline
                        ):
                            await asyncio.sleep(0.05)

            try:
                asyncio.run(execute())
            except BaseException as exc:
                observed["client_error"] = exc
            finally:
                target_ready.set()
                client_done.set()

        threading.Thread(
            target=client_worker,
            name="VibeCAD-MCP-GUI-Test-Client",
            daemon=True,
        ).start()
        _wait(request_assembly.is_set)
        Gui.activateWorkbench("AssemblyWorkbench")
        QtWidgets.QApplication.processEvents()
        assembly_changed.set()
        _wait(request_mesh.is_set)
        Gui.activateWorkbench("MeshWorkbench")
        QtWidgets.QApplication.processEvents()
        mesh_changed.set()
        _wait(target_ready.is_set)
        if "client_error" in observed:
            raise observed["client_error"]
        assert observed["assembly_tool_list_notifications"] == 0
        assert observed.get("tool_list_notifications", 0) >= 1
        assert Gui.activeWorkbench().name() == "MeshWorkbench"
        expected_mesh = _normalized_contracts(_live_tool_contracts())
        assert observed["tools_list_changed_capability"] is True
        assert observed["model_contracts"] == expected_model
        assert observed["assembly_contracts"] == expected_model
        assert observed["mesh_contracts"] == expected_mesh
        assert observed["workbench_read"]["active_workbench"] == ("PartDesignWorkbench")
        assert observed["workbench_read"]["available_workbenches"] == [
            {"name": "PartDesignWorkbench", "label": "Model"},
            {"name": "AssemblyWorkbench", "label": "Assemble"},
            {"name": "MeshWorkbench", "label": "Mesh"},
            {"name": "FemWorkbench", "label": "Analyze"},
            {"name": "CAMWorkbench", "label": "Manufacture"},
            {"name": "TechDrawWorkbench", "label": "Drawing"},
            {"name": "SpreadsheetWorkbench", "label": "Parameters"},
        ]
        assert observed["api_read_error"] is False
        assert observed["review"]["failure_code"] == "PROVIDER_CALL_DISABLED", observed[
            "review"
        ]

        preference_page._refresh_mcp_status()
        assert preference_page.mcp_state.text() == "mcp"
        assert preference_page.mcp_transport.text() == "stdio"
        assert preference_page.mcp_connection.text() in {"listening", "active"}
        assert preference_page.copy_mcp_configuration.isEnabled()

        continue_client.set()
        _wait(request_restore.is_set)
        Gui.activateWorkbench("PartDesignWorkbench")
        QtWidgets.QApplication.processEvents()
        restore_changed.set()
        _wait(client_done.is_set)
        if "client_error" in observed:
            raise observed["client_error"]

        set_mcp_enabled(False)
        controller.request_mcp_enabled(False)
        _wait(lambda: controller.snapshot()["state"] == "internal")
        assert controller.snapshot()["internal_agent_enabled"] is True
        assert controller.snapshot()["pid"] is None

        controller.request_mcp_enabled(True)
        _wait(lambda: controller.snapshot()["state"] == "mcp")
        restarted_configuration = controller.connection_configuration()
        assert restarted_configuration == configuration
        controller.request_mcp_enabled(False)
        _wait(lambda: controller.snapshot()["state"] == "internal")
        print(
            "VIBECAD_MCP_GUI_OK "
            f"model_tools={len(expected_model)} mesh_tools={len(expected_mesh)}"
        )
    finally:
        assembly_changed.set()
        mesh_changed.set()
        restore_changed.set()
        continue_client.set()
        if controller.snapshot()["state"] != "internal":
            controller.request_mcp_enabled(False)
            _wait(lambda: controller.snapshot()["state"] == "internal")
        set_mcp_enabled(original_setting)
        preferences.SetBool("DesignReviewEnabled", original_design_review)
        if preference_page is not None:
            preference_page.form.deleteLater()
        if Gui.activeWorkbench().name() != original_workbench:
            Gui.activateWorkbench(original_workbench)
        App.closeDocument(document.Name)


class TestVibeCADMCPGUI(unittest.TestCase):
    def test_local_stdio_dynamic_mcp_control(self) -> None:
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        run()


if __name__ == "__main__":
    unittest.main()
