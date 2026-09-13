# SPDX-License-Identifier: LGPL-2.1-or-later

"""End-to-end flow through registered MCP tool servers.

Search a model library, find a model, download it, and confirm the file is on
disk where VibeCAD can import it. The library is a local web server so the
flow is deterministic; the same Playwright MCP tool calls work against GrabCAD
or Thingiverse on a desktop with network access.

The test needs Node (``npx``) and a Chromium that Playwright can launch, so it
skips unless ``VIBECAD_MCP_FLOW_TEST=1`` is set. Optional overrides:

- ``VIBECAD_TEST_PLAYWRIGHT_MCP``: path to a ``playwright-mcp`` executable.
- ``VIBECAD_TEST_FILESYSTEM_MCP``: path to a ``mcp-server-filesystem`` executable.
- ``VIBECAD_TEST_CHROMIUM``: Chromium executable for ``--executable-path``.
"""

from __future__ import annotations

from functools import partial
import http.server
import os
from pathlib import Path
import re
import shutil
import threading
import time

import pytest

from VibeCADMCPToolServers import MCPToolServer, MCPToolServerManager


pytestmark = pytest.mark.skipif(
    os.environ.get("VIBECAD_MCP_FLOW_TEST") != "1",
    reason="set VIBECAD_MCP_FLOW_TEST=1 to run the Playwright MCP model-download flow",
)

ASCII_STL = """solid bracket
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
  endfacet
endsolid bracket
"""


class _LibraryHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        if self.path.endswith(".stl"):
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{Path(self.path).name}"'
            )
        super().end_headers()

    def log_message(self, *args) -> None:  # keep pytest output quiet
        return


@pytest.fixture
def model_library(tmp_path):
    root = tmp_path / "library"
    (root / "models" / "bracket").mkdir(parents=True)
    (root / "index.html").write_text(
        "<html><body><h1>Model Library</h1>"
        '<form action="/search.html"><input name="q"></form>'
        "<ul>"
        '<li><a href="/models/bracket/">L Bracket 40x40 STEP STL</a></li>'
        '<li><a href="/models/gear/">Spur Gear</a></li>'
        "</ul></body></html>",
        encoding="utf-8",
    )
    (root / "models" / "bracket" / "index.html").write_text(
        "<html><body><h1>L Bracket 40x40</h1>"
        "<p>Mounting bracket, 3 mm steel.</p>"
        '<a href="/models/bracket/bracket.stl" download>Download STL</a>'
        "</body></html>",
        encoding="utf-8",
    )
    (root / "models" / "bracket" / "bracket.stl").write_text(ASCII_STL, encoding="utf-8")
    handler = partial(_LibraryHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _playwright_server(downloads: Path) -> MCPToolServer:
    override = os.environ.get("VIBECAD_TEST_PLAYWRIGHT_MCP", "").strip()
    command, args = (override, []) if override else ("npx", ["-y", "@playwright/mcp@latest"])
    args += ["--headless", "--isolated", "--output-dir", str(downloads)]
    chromium = os.environ.get("VIBECAD_TEST_CHROMIUM", "").strip()
    if chromium:
        args += ["--executable-path", chromium]
    if getattr(os, "geteuid", lambda: 1)() == 0:
        args.append("--no-sandbox")
    env = {}
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    return MCPToolServer(
        name="playwright", command=command, args=tuple(args), env=env, timeout_seconds=120
    )


def _filesystem_server(downloads: Path) -> MCPToolServer:
    override = os.environ.get("VIBECAD_TEST_FILESYSTEM_MCP", "").strip()
    command, args = (
        (override, [])
        if override
        else ("npx", ["-y", "@modelcontextprotocol/server-filesystem"])
    )
    return MCPToolServer(
        name="project-files", command=command, args=tuple(args + [str(downloads)]), timeout_seconds=60
    )


def _text(result: dict) -> str:
    return "\n".join(
        item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"
    )


def test_search_find_download_and_list_a_model_through_mcp_servers(
    model_library, tmp_path, monkeypatch
) -> None:
    if not os.environ.get("VIBECAD_TEST_PLAYWRIGHT_MCP") and shutil.which("npx") is None:
        pytest.skip("npx is not installed")
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path / "home"))
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    manager = MCPToolServerManager(runtime_directory=tmp_path / "runtime")
    try:
        schemas, routing, statuses = manager.tool_schemas_for_turn(
            [_playwright_server(downloads), _filesystem_server(downloads)]
        )
        assert [status["ok"] for status in statuses] == [True, True], statuses
        names = {schema["name"] for schema in schemas}
        assert {"mcp_playwright.browser_navigate", "mcp_playwright.browser_click", "mcp_project_files.list_directory"} <= names

        def open_page(url: str) -> str:
            result = manager.call("mcp_playwright.browser_navigate", {"url": url})
            assert result["ok"], result
            snapshot = manager.call("mcp_playwright.browser_snapshot", {})
            assert snapshot["ok"], snapshot
            return _text(result) + "\n" + _text(snapshot)

        # 1. Search the library and find the model.
        listing = open_page(f"{model_library}/")
        assert "L Bracket 40x40" in listing
        match = re.search(r"/url: ((?:http://127\.0\.0\.1:\d+)?/models/bracket/)", listing)
        assert match, listing
        model_url = match.group(1)
        if model_url.startswith("/"):
            model_url = model_library + model_url

        # 2. Open the model page and download the file.
        page = open_page(model_url)
        ref = re.search(r'link "Download STL" \[ref=([A-Za-z0-9]+)\]', page)
        assert ref, page
        result = manager.call(
            "mcp_playwright.browser_click", {"element": "Download STL", "target": ref.group(1)}
        )
        assert result["ok"], (result.get("error"), _text(result))
        deadline = time.monotonic() + 30
        downloaded = None
        while time.monotonic() < deadline and downloaded is None:
            candidates = [path for path in downloads.rglob("*") if path.is_file() and path.stat().st_size > 0]
            downloaded = next((path for path in candidates if path.read_text(errors="ignore").startswith("solid bracket")), None)
            if downloaded is None:
                time.sleep(0.25)
        assert downloaded is not None, sorted(str(p) for p in downloads.rglob("*"))

        # 3. The project-files server sees the download where VibeCAD can import it.
        result = manager.call("mcp_project_files.list_directory", {"path": str(downloaded.parent)})
        assert result["ok"], result
        assert downloaded.name in _text(result)
    finally:
        manager.shutdown()
