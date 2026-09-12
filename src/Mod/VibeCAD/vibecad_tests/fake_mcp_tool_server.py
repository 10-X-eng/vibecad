# SPDX-License-Identifier: LGPL-2.1-or-later

"""Minimal stdio MCP server used by the MCP tool-server contract tests.

It is launched as a child process by the tests, so it depends only on the
bundled ``mcp`` SDK and never on FreeCAD.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys


# One transparent 1x1 PNG.
_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


async def _serve() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp_types import (
        CallToolResult,
        ImageContent,
        ListToolsResult,
        TextContent,
        Tool,
    )

    server_name = str(os.environ.get("FAKE_MCP_SERVER_NAME") or "fake")
    banner = str(os.environ.get("FAKE_MCP_BANNER") or "")

    async def list_tools(ctx, params):
        del ctx, params
        return ListToolsResult(
            tools=[
                Tool(
                    name="echo",
                    description="Echo the supplied text back to the caller.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Text to echo."}
                        },
                        "required": ["text"],
                    },
                ),
                Tool(
                    name="add-numbers",
                    description="Add two integers and return a structured sum.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                        "required": ["a", "b"],
                    },
                ),
                Tool(
                    name="picture",
                    description="Return a one pixel PNG image.",
                    inputSchema={"type": "object"},
                ),
                Tool(
                    name="fail",
                    description="Always report a tool error.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="sleep",
                    description="Sleep for the requested number of seconds.",
                    inputSchema={
                        "type": "object",
                        "properties": {"seconds": {"type": "number"}},
                        "required": ["seconds"],
                    },
                ),
            ]
        )

    async def call_tool(ctx, params):
        del ctx
        name = str(params.name)
        arguments = dict(params.arguments or {})
        if name == "echo":
            text = f"{banner}{arguments.get('text', '')}"
            return CallToolResult(
                content=[TextContent(text=text)],
                structuredContent={"echoed": text, "server": server_name},
            )
        if name == "add-numbers":
            total = int(arguments["a"]) + int(arguments["b"])
            return CallToolResult(
                content=[TextContent(text=json.dumps({"sum": total}))],
                structuredContent={"sum": total},
            )
        if name == "picture":
            return CallToolResult(
                content=[
                    TextContent(text="one pixel"),
                    ImageContent(
                        data=base64.b64encode(_PIXEL_PNG).decode("ascii"),
                        mimeType="image/png",
                    ),
                ]
            )
        if name == "fail":
            return CallToolResult(
                content=[TextContent(text="deliberate failure")],
                isError=True,
            )
        if name == "sleep":
            await asyncio.sleep(float(arguments.get("seconds", 0)))
            return CallToolResult(content=[TextContent(text="slept")])
        return CallToolResult(
            content=[TextContent(text=f"unknown tool {name}")],
            isError=True,
        )

    server = Server(
        server_name,
        version="0.0.1",
        instructions="Fake MCP tool server for VibeCAD tests.",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> int:
    if os.environ.get("FAKE_MCP_CRASH_ON_START"):
        print("fake MCP server refused to start", file=sys.stderr, flush=True)
        return 3
    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
