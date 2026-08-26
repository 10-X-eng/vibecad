# VibeCAD local agent control

This is the scriptable control surface for a desktop agent (for example Grok
Bot on Windows) that cannot click VibeCAD menus. It does **not** replace the
in-app Assistant and it does **not** turn MCP on.

Use this channel to open a document, run Python or VibeScript against the
active document, show Preferences, and read provider/auth status. Sign-in
still happens in Preferences (browser or device-code). The agent must never
type passwords or OAuth codes.

## Two ways to call VibeCAD

| When | What to use |
| --- | --- |
| VibeCAD GUI is already running | Loopback HTTP on `127.0.0.1` (default port **8766**) or the CLI as an HTTP client |
| No GUI / headless Windows | `FreeCADCmd.exe` (Windows bundles today) or `VibeCADCmd.exe` if present |

MCP at `http://127.0.0.1:8765/mcp` is a different, mutually exclusive mode.
Enabling MCP **disables** the in-app Grok / ChatGPT assistant. Do not enable
MCP if the human is using the Assistant.

## Native Grok in the Assistant

The in-app Assistant already has a first-class **Grok (X / xAI)** provider:

1. Open **Edit → Preferences → VibeCAD → VibeCAD** (or `preferences` below).
2. Enable **Use online provider** and select **Grok (X / xAI)**.
3. Click **Sign in with X / Grok** (or **Use device code**).
4. Click **Fetch models**, pick a Grok model, Apply.
5. Ask, plan, build, or steer against the open document as with ChatGPT.

xAI publishes real OAuth at `https://auth.x.ai`. xAI does not publish a
VibeCAD-specific OAuth app; VibeCAD reuses the official Grok CLI public
client. If login works but inference returns HTTP 403, use the existing
OpenAI-provider + `https://api.x.ai/v1` API-key fallback. ChatGPT, OpenAI,
and Anthropic are unchanged.

## Discover the live GUI endpoint

On first GUI start VibeCAD writes:

| File | Typical Windows path |
| --- | --- |
| Token | `%LOCALAPPDATA%\VibeCAD\Agent\token` |
| Endpoint | `%LOCALAPPDATA%\VibeCAD\Agent\endpoint.json` |

macOS: `~/Library/Application Support/VibeCAD/Agent/`  
Linux: `~/.local/share/VibeCAD/agent/`  
Override: `VIBECAD_AGENT_HOME`. Port override: `VIBECAD_AGENT_PORT`.

`endpoint.json` contains `host`, `port`, `base_url`, and `token_path`. It
does not contain the token. Read the token file; do not prompt a human.

## Exact commands (Windows)

Replace the install root if VibeCAD is not under `C:\Program Files\VibeCAD`.
Current Windows bundles ship the Cmd process as `FreeCADCmd.exe` (the
internal exe name is still VibeCAD). Use `VibeCADCmd.exe` when that file
exists.

```bat
set "VIBECAD_ROOT=C:\Program Files\VibeCAD"
set "VIBECAD_CMD=%VIBECAD_ROOT%\bin\FreeCADCmd.exe"
if exist "%VIBECAD_ROOT%\bin\VibeCADCmd.exe" set "VIBECAD_CMD=%VIBECAD_ROOT%\bin\VibeCADCmd.exe"
set "VIBECAD_CLI=%VIBECAD_ROOT%\Mod\VibeCAD\VibeCADAgentCli.py"
set "VIBECAD_AGENT=%VIBECAD_ROOT%\Mod\VibeCAD\vibecad-agent.cmd"
```

### Running GUI — HTTP (preferred)

```bat
set /p VIBECAD_TOKEN=<"%LOCALAPPDATA%\VibeCAD\Agent\token"

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" http://127.0.0.1:8766/v1/status

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" http://127.0.0.1:8766/v1/documents

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"path\":\"C:\\Models\\part.FCStd\"}" ^
  http://127.0.0.1:8766/v1/open

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"path\":\"C:\\Models\\part.FCStd\",\"script\":\"C:\\Work\\edit.py\"}" ^
  http://127.0.0.1:8766/v1/run

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"python\":\"result = App.ActiveDocument.Name\"}" ^
  http://127.0.0.1:8766/v1/run

curl -s -X POST -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  http://127.0.0.1:8766/v1/preferences
```

### Running GUI — Python CLI (no FreeCAD bindings needed)

```bat
python "%VIBECAD_CLI%" status
python "%VIBECAD_CLI%" documents
python "%VIBECAD_CLI%" open --path C:\Models\part.FCStd
python "%VIBECAD_CLI%" run --path C:\Models\part.FCStd --script C:\Work\edit.py
python "%VIBECAD_CLI%" run --python "result = [obj.Name for obj in App.ActiveDocument.Objects]"
python "%VIBECAD_CLI%" preferences
```

### Headless / scriptable Cmd

```bat
"%VIBECAD_CMD%" "%VIBECAD_CLI%" --local status
"%VIBECAD_CMD%" "%VIBECAD_CLI%" --local open --path C:\Models\part.FCStd
"%VIBECAD_CMD%" "%VIBECAD_CLI%" --local run --path C:\Models\part.FCStd --script C:\Work\edit.py
"%VIBECAD_CMD%" "%VIBECAD_CLI%" --local run --python "import Part; result = App.ActiveDocument.Name"
```

`preferences` is GUI-only. Headless Cmd returns `GUI_REQUIRED`.

### One-shot wrapper

```bat
"%VIBECAD_AGENT%" status
"%VIBECAD_AGENT%" open --path C:\Models\part.FCStd
"%VIBECAD_AGENT%" run --script C:\Work\edit.py
```

`vibecad-agent.cmd` talks to a listening GUI first, then falls back to
`FreeCADCmd.exe` / `VibeCADCmd.exe`. Set `VIBECAD_CMD` to override the binary.

You can also pass a Python file directly to Cmd (no agent CLI):

```bat
"%VIBECAD_CMD%" C:\Models\part.FCStd C:\Work\edit.py
```

That is the stock FreeCAD/VibeCADCmd worker. The agent CLI is preferred when
you need JSON status, structured errors, or a live GUI without clicking.

## Routes

All routes are loopback-only and require
`Authorization: Bearer <token-file-contents>`.

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET | `/v1/status` | | Provider, auth (no secrets), Grok sign-in flag, documents, endpoint |
| GET | `/v1/documents` | | Open documents |
| POST | `/v1/open` | `{"path":"..."}` | Open/activate a document |
| POST | `/v1/run` | `{"python":"..."}` or `{"script":"..."}` plus optional `path`, `recompute` | Exec against the active doc |
| POST | `/v1/preferences` | | Show VibeCAD Preferences |

`run` executes the source as Python in the VibeCAD process with `App` /
`FreeCAD` (and `Gui` / `FreeCADGui` when the GUI is up). VibeScript files are
the same: they are Python executed against the active document. Assign
`result` or `__result__` to return a JSON value. Stdout, stderr, and
exceptions come back in the JSON payload.

## Errors

Every response is JSON:

```json
{"ok": true, "...": "..."}
```

or

```json
{
  "ok": false,
  "failure_code": "DOCUMENT_NOT_FOUND",
  "failure_stage": "precondition",
  "error": "No file exists at C:\\Models\\missing.FCStd."
}
```

The CLI prints that JSON and exits `0` on success, `1` on a handled error,
and `2` when `--gui-only` is set and nothing is listening.

## Aero workbench

The Aero workbench (`VibeCADAero`) can be triggered from `/v1/run` without
SendKeys. This does not change the assistant, Grok OAuth, or port 8766.

```python
import VibeCADAero
result = VibeCADAero.run_analyze(App.ActiveDocument)
```

See `docs/vibecad-aero.md`.

## What this channel will not do

- It will not start an OAuth login or accept a password / device code.
- It will not enable MCP or disable the in-app Assistant.
- It will not invent a “Grok Bot” brand inside VibeCAD. Grok is the real
  **Grok (X / xAI)** provider. This API is a local control socket that any
  local agent, including Grok Bot, can call.
