# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI gate for authenticated MCP control and dynamic tool parity."""

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
            interaction_mode="build",
        ),
        *controller_tool_schemas(),
    ]


def _normalized_contracts(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "parameters": dict(item.get("parameters") or {}),
        }
        for item in contracts
    ]


def run() -> None:
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import VibeCADGui
    from VibeCADMCP import get_control_mode_controller
    from VibeCADPreferences import VibeCADPreferencesPage, load_settings, set_mcp_enabled

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
    target_ready = threading.Event()
    continue_client = threading.Event()
    client_done = threading.Event()
    observed: dict[str, Any] = {}
    preference_page = None

    try:
        preference_page = VibeCADPreferencesPage()
        preference_page.loadSettings()
        preference_page.mcp_enabled.setChecked(True)
        preference_page.saveSettings()
        _wait(lambda: controller.snapshot()["state"] == "mcp")
        assert controller.snapshot()["internal_agent_enabled"] is False
        assert VibeCADGui.AskAICommand().IsActive() is False
        configuration = controller.connection_configuration()
        token_header = configuration["headers"]

        def client_worker() -> None:
            async def execute() -> None:
                async def handle_message(message: Any) -> None:
                    if (
                        str(getattr(message, "method", ""))
                        == "notifications/tools/list_changed"
                    ):
                        observed["tool_list_notifications"] = int(
                            observed.get("tool_list_notifications", 0)
                        ) + 1

                async with httpx2.AsyncClient() as unauthenticated:
                    response = await unauthenticated.post(
                        configuration["url"], content=b"{}"
                    )
                    observed["unauthenticated_status"] = response.status_code
                async with httpx2.AsyncClient(headers=token_header) as client:
                    async with streamable_http_client(
                        configuration["url"], http_client=client
                    ) as (read_stream, write_stream):
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
                            workbench = await session.call_tool(
                                "vibecad.read_workbench", {}
                            )
                            observed["workbench_read"] = workbench.structured_content
                            api_read = await session.call_tool(
                                "vibescript.read_api", {}
                            )
                            observed["api_read_error"] = api_read.is_error
                            review = await session.call_tool(
                                "conversation.review_design",
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
                            switched = await session.call_tool(
                                "vibecad.switch_workbench",
                                {"workbench": "MeshWorkbench"},
                            )
                            observed["mesh_switch"] = switched.structured_content
                            notification_deadline = time.monotonic() + 5.0
                            while (
                                not observed.get("tool_list_notifications")
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
                            restored = await session.call_tool(
                                "vibecad.switch_workbench",
                                {"workbench": "PartDesignWorkbench"},
                            )
                            observed["restore_switch"] = restored.structured_content
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
        _wait(target_ready.is_set)
        if "client_error" in observed:
            raise observed["client_error"]
        assert observed["mesh_switch"]["ok"] is True, observed["mesh_switch"]
        assert observed.get("tool_list_notifications", 0) >= 1
        assert Gui.activeWorkbench().name() == "MeshWorkbench", observed["mesh_switch"]
        expected_mesh = _normalized_contracts(_live_tool_contracts())
        assert observed["unauthenticated_status"] == 401
        assert observed["tools_list_changed_capability"] is True
        assert observed["model_contracts"] == expected_model
        assert observed["mesh_contracts"] == expected_mesh
        assert observed["workbench_read"]["active_workbench"] == (
            "PartDesignWorkbench"
        )
        assert observed["api_read_error"] is False
        assert observed["review"]["failure_code"] == "PROVIDER_CALL_DISABLED", (
            observed["review"]
        )

        preference_page._refresh_mcp_status()
        assert preference_page.mcp_state.text() == "mcp"
        assert preference_page.mcp_endpoint.text() == configuration["url"]
        assert preference_page.mcp_connection.text() in {"listening", "active"}
        expected_token = token_header["Authorization"].removeprefix("Bearer ")
        assert preference_page.mcp_token.text() == expected_token
        assert preference_page.copy_mcp_token.isEnabled()
        assert preference_page.copy_mcp_configuration.isEnabled()

        continue_client.set()
        _wait(client_done.is_set)
        if "client_error" in observed:
            raise observed["client_error"]
        assert observed["restore_switch"]["ok"] is True

        set_mcp_enabled(False)
        controller.request_mcp_enabled(False)
        _wait(lambda: controller.snapshot()["state"] == "internal")
        assert controller.snapshot()["internal_agent_enabled"] is True
        assert controller.snapshot()["pid"] is None
        assert controller.snapshot()["token_available"] is True

        controller.request_mcp_enabled(True)
        _wait(lambda: controller.snapshot()["state"] == "mcp")
        restarted_configuration = controller.connection_configuration()
        assert restarted_configuration["headers"] == token_header
        controller.request_mcp_enabled(False)
        _wait(lambda: controller.snapshot()["state"] == "internal")
        print(
            "VIBECAD_MCP_GUI_OK "
            f"model_tools={len(expected_model)} mesh_tools={len(expected_mesh)}"
        )
    finally:
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
    def test_authenticated_dynamic_mcp_control(self) -> None:
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        run()


if __name__ == "__main__":
    unittest.main()
