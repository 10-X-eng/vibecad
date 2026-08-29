# VibeCAD local agent control

This is the scriptable control surface for a desktop agent (for example Grok
Bot on Windows). It can inspect and activate exact semantic VibeCAD menus and
ribbon tabs without taking over the user's physical cursor. It does **not**
replace the in-app Assistant and it does **not** turn MCP on.

Use this channel to open, save, save-as, close, and reopen native documents;
use the retained privileged local Python/VibeScript compatibility route against
the active document; inspect or activate semantic UI targets; show Preferences;
and read provider/auth status. Sign-in still happens in Preferences (browser or
device-code). The agent must never type passwords or OAuth codes.

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

The Windows repo-root development launcher deliberately sets
`VIBECAD_AGENT_HOME` to the ignored, checkout-scoped directory below instead of
using the normal per-user location:

```text
<repo>\.vibecad-dev\agent\
```

Use the endpoint path printed by `RUN-VIBECAD-DEV.cmd` or
`Launch-VibeCAD-Dev.ps1` when controlling a development checkout. This prevents
an installed VibeCAD session or another checkout from being mistaken for the
GUI under test. The launcher waits for an authenticated ready status before it
reports success; see
[developer-launch-windows.md](developer-launch-windows.md).

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
  -d "{\"path\":\"C:\\Models\\agent-copy.FCStd\"}" ^
  http://127.0.0.1:8766/v1/save-as

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  http://127.0.0.1:8766/v1/ui/menus

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  http://127.0.0.1:8766/v1/ui/ribbon

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  -H "Content-Type: application/json" ^
  -d "{\"kind\":\"ribbon\",\"text\":\"Aero\"}" ^
  http://127.0.0.1:8766/v1/ui/click

curl -s -H "Authorization: Bearer %VIBECAD_TOKEN%" ^
  http://127.0.0.1:8766/v1/screenshot

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
python "%VIBECAD_CLI%" save-as --path C:\Models\agent-copy.FCStd
python "%VIBECAD_CLI%" save
python "%VIBECAD_CLI%" close
python "%VIBECAD_CLI%" ui-menus
python "%VIBECAD_CLI%" ui-ribbon
python "%VIBECAD_CLI%" ui-click --kind ribbon --text Aero
python "%VIBECAD_CLI%" screenshot --path C:\Evidence\vibecad.png
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
| POST | `/v1/save` | optional `{"document":"Name"}` | Save an already-named document and verify the file/postcondition |
| POST | `/v1/save-as` | `{"path":"..."}`, optional `document`, explicit `overwrite` | Save a native `.FCStd`; existing targets are protected by default |
| POST | `/v1/close` | optional `document`, explicit `discard_unsaved` | Close without silently discarding a modified document |
| GET | `/v1/ui/menus` | | Live top-level menu names, indices, visibility, and screen geometry |
| GET | `/v1/ui/ribbon` | | Live ribbon names, workbenches, indices, selection, and screen geometry |
| POST | `/v1/ui/click` | `{"kind":"menu|ribbon","text":"..."}`, optional exact PID/index | Activate one semantic Qt target without moving or clicking the OS cursor |
| POST | `/v1/run` | `{"python":"..."}` or `{"script":"..."}` plus optional `path`, `recompute` | Exec against the active doc |
| GET/POST | `/v1/aero` | operation payload for POST | Bounded Aero context and operations |
| GET/POST | `/v1/screenshot` | optional absolute `.png` path and explicit `overwrite` | Capture the visible VibeCAD window |
| POST | `/v1/preferences` | | Show VibeCAD Preferences |

`run` executes the source as Python in the VibeCAD process with `App` /
`FreeCAD` (and `Gui` / `FreeCADGui` when the GUI is up). VibeScript files are
the same: they are Python executed against the active document. Assign
`result` or `__result__` to return a JSON value. Stdout, stderr, and
exceptions come back in the JSON payload.

`run` is a privileged local compatibility escape hatch, not a sandbox or an
authority boundary. Only execute source the developer has authorized. Prefer a
bounded domain route such as `/v1/aero` when one exists instead of bypassing its
preconditions and postconditions through arbitrary Python.

### Semantic UI activation and the independent cursor

`/v1/ui/click` targets an exact live Qt menu action or
`VibeCADRibbonTabs` entry by visible text. Optional `expected_process_id` and
`expected_index` values make stale geometry fail closed. Ribbon clicks use an
in-process Qt mouse event; top-level menus use a non-blocking in-process Qt
popup. Neither path calls Windows cursor-position or input-injection APIs.

For a human-watchable Windows demonstration, the repo-root
`Invoke-VibeCAD-VisibleTour.ps1` draws its own click-through plain cyan pointer
over those semantic coordinates while calling `/v1/ui/click`. The overlay has
no label, sign, circle, or halo and never moves the user's mouse. See
[developer-launch-windows.md](developer-launch-windows.md).

When no explicit target sequence is supplied, the tour discovers the live
window's visible, enabled top-level menus and enabled ribbon tabs. This keeps the
tester reusable across checkouts without assuming that an optional product
feature is installed.

### Visible-window screenshots

`GET /v1/screenshot` captures the visible VibeCAD main window under the private
agent home. `POST /v1/screenshot` optionally accepts an absolute `.png` path.
Existing files are protected unless the JSON payload contains the literal
boolean `"overwrite": true`. A successful response includes the exact file
path, byte size, SHA-256 digest, pixel dimensions, window title and handle, and
VibeCAD process ID.

### Native file safety

`save-as` accepts only an absolute `.FCStd` path whose parent exists. It refuses
an existing target unless `overwrite=true` is explicitly supplied. `close`
refuses a modified document unless `discard_unsaved=true` is explicit.
FreeCAD's App-document `isSaved()` state means only that the document has an
associated file. In the running GUI, close therefore guards the native GUI
document's `Modified` state, which covers both model data and persisted
`GuiDocument.xml` / view-provider changes. After an agent-controlled save has
produced the requested file and passed its path postcondition, the control
surface clears the stale GUI flag left by FreeCAD's App-level save API, matching
the native **File -> Save** behavior. A later model or view-provider edit sets
the flag again. Open never clears a restore-time modified state, and an
unreadable native GUI dirty state fails closed. The native headless DocumentPy
binding exposes no equivalent document-level `Modified` flag, so generic
headless status and close checks also fail closed. A headless save reports clean
only inside that save response, after the native save call and requested file
and path-association postconditions have all passed.

Partially loaded documents are rejected before `save` or `save-as`, because
FreeCAD can acknowledge a partial-document save without writing the requested
file. The one-click development launcher starts the GUI server in its explicit
fail-closed mode: startup requires the Qt document-thread dispatcher and the
server admits only one document operation at a time. A concurrent request
returns `DOCUMENT_OPERATION_BUSY` before anything is queued into Qt; an
unavailable dispatcher returns `DOCUMENT_THREAD_UNAVAILABLE` without accessing
App or GUI document state. A request that reaches Qt during a native FreeCAD
restore returns `DOCUMENT_RESTORE_IN_PROGRESS` before touching the partially
restored document. Direct execution without that dispatcher is limited to the
explicitly selected FreeCADCmd/headless CLI adapter and only when FreeCAD's
App-level `GuiUp` authority is false.

The original `ensure_server_started()` and omitted-flag `dispatch()` behavior
remains the compatibility default for existing integrations and normal
installed startup. Development sessions opt in through
`ensure_fail_closed_server_started()` when `VIBECAD_DEV_MODE=1`; the launcher
sets that value itself. Newly added save, close, UI-inspection, UI-activation,
and screenshot commands stay guarded even for a direct caller that omits the
new mode flag.

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

The Aero workbench (`VibeCADAero`) is exposed through bounded `/v1/aero`
operations without SendKeys. Use `GET /v1/aero` for context or POST a named
operation such as `{"operation":"analyze"}`. CAD-changing Aero work must not
be smuggled through `/v1/run`; the domain route preserves its authority and
receipt contracts. This does not change the assistant, Grok OAuth, or port
8766.

See `docs/vibecad-aero.md`.

## What this channel will not do

- It will not start an OAuth login or accept a password / device code.
- It will not enable MCP or disable the in-app Assistant.
- It will not invent a “Grok Bot” brand inside VibeCAD. Grok is the real
  **Grok (X / xAI)** provider. This API is a local control socket that any
  local agent, including Grok Bot, can call.
