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

`endpoint.json` contains `host`, `port`, `base_url`, and the exact canonical
`token_path`. It does not contain the token. Read the token file; do not prompt a
human. The endpoint also carries the same process/runtime envelope returned at
the top level of `/v1/status`:

| Field | Contract |
| --- | --- |
| `server_instance_id` | Cryptographically random ID regenerated for each server start |
| `process_id` | Actual VibeCAD process ID |
| `server_started_at_utc` | UTC server-start timestamp |
| `runtime_identity` | Strict attested identity below, or `null` for compatible normal/skip-rebuild startup |
| `fail_closed` | `true` only for the opt-in loopback development server; `false` for the compatibility starter |

The endpoint and authenticated status must match on all five fields. A caller
that launched VibeCAD itself must additionally compare `process_id` with the
process it started; freshness by endpoint-file timestamp or window title alone
is insufficient.

### Attested development runtime identity

A rebuilt Windows checkout launch exports receipts and starts the server only
after the runtime independently reads the actual checkout's canonical Git root,
full `HEAD`, and `HEAD^{tree}` and validates the actual files. In that mode
`runtime_identity` has this additive schema (paths are absolute and hashes are
lowercase SHA-256):

```json
{
  "schema": "vibecad.dev-runtime-identity.v1",
  "repository_root": "<canonical checkout>",
  "commit": "<full Git commit>",
  "tree": "<full Git tree>",
  "executable_path": "<actual process executable>",
  "executable_sha256": "<actual executable hash>",
  "build_attestation_path": "<absolute build receipt>",
  "build_attestation_sha256": "<actual build receipt hash>",
  "launch_attestation_path": "<absolute launch receipt>",
  "launch_attestation_sha256": "<actual launch receipt hash>",
  "qt_runtime": {
    "qt_major": 6,
    "qwindows_path": "<checkout-local qwindows.dll>",
    "qwindows_sha256": "<actual plugin hash>",
    "dlls": [
      {
        "name": "Qt6Core.dll",
        "path": "<checkout-local Qt6Core.dll>",
        "sha256": "<actual DLL hash>"
      }
    ]
  },
  "qt_platform_probe": {
    "complete": true,
    "checked_at_utc": "<UTC timestamp>",
    "platform": "windows",
    "python_executable": "<checkout-local python.exe>",
    "python_sha256": "<actual Python hash>",
    "loaded_qwindows_path": "<same checkout-local qwindows.dll>",
    "loaded_qwindows_sha256": "<same actual plugin hash>"
  },
  "release_evidence": {
    "asserted": false,
    "clean_checkout": null,
    "submodule_dirt_checked": null,
    "git_status_mode": null,
    "cold_build_asserted": false,
    "pre_build_environment_present": true,
    "pre_build_runtime_complete": true,
    "environment_absent_before_install": null,
    "pre_build_checked_at_utc": null,
    "environment_cleaned_at_utc": null,
    "build_cache_cleaned_at_utc": null,
    "pre_receipt_checked_at_utc": null
  },
  "qt_process": {
    "platform": "windows",
    "loaded_qwindows_path": "<module loaded by this VibeCAD PID>",
    "loaded_qwindows_sha256": "<actual loaded-module file hash>"
  },
  "modules": [
    {
      "name": "VibeCADAgentControl.py",
      "source_path": "<absolute checkout source>",
      "source_sha256": "<actual source hash>",
      "runtime_path": "<absolute installed module, or null for source-only assets>",
      "runtime_sha256": "<actual installed hash, or null>"
    }
  ]
}
```

The required module set is `InitGui.py`, `VibeCADAgentControl.py`,
`VibeCADAgentCli.py`, `VibeCADGui.py`, `Invoke-VibeCAD-VisibleTour.ps1`, and
`Launch-VibeCAD-Dev.ps1`. The first four must have installed copies identical
to their checkout sources; the last two are source-only identities. The build
receipt uses `vibecad.dev-build-attestation.v1` and the launch receipt uses
`vibecad.dev-launch-attestation.v1`. Missing, altered, cross-checkout, partial,
stale, or executable-mismatched evidence prevents development control startup.
The Qt and release objects must be complete and identical in both receipts.
The process re-hashes their referenced files and uses Windows module inspection
to prove that the visible VibeCAD PID itself loaded the attested `qwindows.dll`;
the separate PySide6 probe is not accepted as a substitute for that GUI-process
proof.

Normal packaged startup remains compatible when these dev-attestation
environment variables are absent: the existing server entry point, explicit
host option, routes, defaults, and endpoint fields remain, with
`runtime_identity: null`.

### Token access

The additive fail-closed development service binds only to `127.0.0.1`; its
caller cannot select a non-loopback host. The original compatibility starter
retains its explicit-host behavior for existing integrations. Development mode
accepts only the exact
`<repo>\.vibecad-dev\agent` home derived from `VIBECAD_DEV_SOURCE_ROOT`; it
rejects an outside override before creating or changing that directory. On
Windows, token creation first protects the checkout-scoped agent directory and
then the exact token file with one inheritance-protected, current-user
full-control ACE. The DACL is read back and verified. Development mode refuses
server startup if that operation is unavailable or the resulting ACL has any
additional ACE. Normal installed startup keeps its existing best-effort
file-permission compatibility behavior and does not replace the directory DACL.
Checkout-scoped credentials isolate discovery and authentication between
development checkouts; they do **not** sandbox an authenticated caller's file
authority. In particular, `/v1/open`, `/v1/save-as`, and the privileged
`/v1/run` compatibility route can reach paths allowed to the VibeCAD process.

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

The one-click fail-closed development routes are loopback-only and require
`Authorization: Bearer <token-file-contents>`. The compatibility starter keeps
its pre-existing explicit-host contract and should be exposed only under the
owner's network controls.

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET | `/v1/status` | | Process/runtime identity, provider, auth (no secrets), Grok sign-in flag, documents, endpoint |
| GET | `/v1/documents` | | Open documents |
| POST | `/v1/open` | `{"path":"..."}` | Open/activate a document |
| POST | `/v1/save` | optional `{"document":"Name"}` | Save an already-named document and verify the file/postcondition |
| POST | `/v1/save-as` | `{"path":"..."}`, optional `document`, explicit `overwrite` | Save a native `.FCStd`; existing targets are protected by default |
| POST | `/v1/close` | optional `document`, explicit `discard_unsaved` | Close without silently discarding a modified document |
| GET | `/v1/ui/menus` | | Live top-level menu names, indices, visibility, and screen geometry |
| GET | `/v1/ui/ribbon` | | Live ribbon names, workbenches, indices, selection, and screen geometry |
| POST | `/v1/ui/click` | `{"kind":"menu|ribbon","text":"..."}`, optional exact PID/index | Activate one semantic Qt target without moving or clicking the OS cursor |
| POST | `/v1/run` | `{"python":"..."}` or `{"script":"..."}` plus optional `path`, `recompute` | Exec against the active doc |
| GET | `/v1/operations/{operation_id}` | | Read the in-memory state/result of a client-identified operation without entering the document thread |
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

### Proving completion after a client timeout

Any routed request body may add an optional canonical lowercase UUID in the
`operation_id` field. When present, the response repeats that ID and the server
keeps a bounded, thread-safe in-memory record for the lifetime of that server
instance. Read it with:

```text
GET /v1/operations/<operation_id>
```

The operation record moves from `running` to `completed` and includes UTC start
and completion times, its owning `server_instance_id`, the logical result, and
the complete response. This lets a
tester prove that a request which outlived its HTTP client timeout actually
finished before it continues. IDs are single-use within a server instance;
invalid or reused IDs fail closed. Completed records are evicted oldest-first
only when the 256-record bound needs room, and running records are never
evicted. A restart clears the registry and rotates `server_instance_id`; a late
completion from the previous server instance is ignored even if a client reuses
the same UUID after restart.

Each listener also owns an immutable copy of its complete process/runtime
identity envelope. If an old request overlaps shutdown and restart, that old
listener cannot accidentally publish the new listener's instance ID, start
time, or runtime identity.

`/v1/status` advertises this additive surface as
`vibecad.dev-operation-tracking.v1`. The operation-status route reads only the
registry, so it does not enter FreeCAD's document thread. Normal document and
status routes remain serialized in fail-closed development mode: while a long
document operation owns that gate they return `DOCUMENT_OPERATION_BUSY`
immediately, and a normal authenticated status is rechecked after tracked
completion.

When `endpoint.json` advertises `fail_closed: true`, the bundled CLI adds a
fresh operation UUID to every POST automatically. If the POST response times
out, resets, is truncated, or is not valid JSON, the CLI polls that exact
operation on the same authenticated server instance and never falls back to a
second local mutation. Only a proven connection refusal before a listener
accepts the request preserves the original local fallback. If completion
cannot be proven, the CLI returns
`REMOTE_OUTCOME_UNRESOLVED` with the operation ID instead of guessing or
redispatching. Compatibility endpoints retain their pre-existing POST payloads,
explicit-host support, and local-fallback behavior; they can opt into operation
tracking explicitly by supplying an `operation_id` through the HTTP API.

### Semantic UI activation and the independent cursor

`/v1/ui/click` targets an exact live Qt menu action or
`VibeCADRibbonTabs` entry by visible text. Optional `expected_process_id` and
`expected_index` values make stale geometry fail closed. Ribbon clicks use an
in-process Qt mouse event; top-level menus use a non-blocking in-process Qt
popup. A menu popup is displayed for one bounded preview, then closed before
the request returns. The prior focused widget and menu-bar action are restored,
and the active window and popup state are verified unchanged. A pre-existing
popup fails busy rather than being closed by the tester. Neither path calls
Windows cursor-position or input-injection APIs.

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
sets that value itself. If the rebuilt launcher also supplies the attestation
variables, server startup validates them before creating a token, listener, or
endpoint. Newly added save, close, UI-inspection, UI-activation,
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
