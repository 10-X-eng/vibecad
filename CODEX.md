# Codex review notes — Aero / Grok follow-ups

This file is for the Codex reviewer. It is written in plain language so the
intent of the pull request is not reconstructed from the diff.

## What this PR is

Follow-up to already-merged upstream PR #60 (Grok sign-in, agent control, Aero
ribbon). This branch is the same 52-file delta that `halthinks/main` already
carries over `10-X-eng/main`, rebased onto current upstream `main` with
checkpoint and merge commits squashed.

It adds hardening, Connect Grok Bot, an in-process geometry-worker fallback,
and the Codex review fixes already landed on the fork.

## What this PR is not

This PR does **not** include a full Aero user manual.

This PR does **not** include the full updated ribbon-use explanations or
tool-use explanations. Those docs are a later PR.

`docs/vibecad-aero.md` only has a small delta (Analyze default, tail gating).
Treat that as incomplete on purpose. Do not ask this PR to grow into the
manual.

This PR does **not** restore HTTP MCP (`:8765/mcp`) and does **not** restore
the legacy Workbenches preferences page. Both absences are intentional.

This PR does **not** touch TextPCB.

## User-visible behavior

- Analyze repair is opt-in: `run_analyze(..., repair=False)`. Report and
  JSBSim stay observational unless the user requests repair.
- If the user does request repair, pitch-unstable geometry can be corrected
  and the change list is shown.
- Solver horizontal tail is used only when CAD has a named `h_tail` or
  explicit tail sizes. No invented tail defaults.
- BoundBox repair never assigns getter-only `XLength` / `YLength` /
  `ZLength`.
- Aero Analyze JSON is stored on the `AeroAssistantJson` TextDocument
  (`obj.Text`). The document string, if any, is the distinct
  `AeroAssistantJsonText`. Never setattr over the object name.
- Aero steering is queued only while an in-app Grok run is active. Toolbar
  Analyze still persists and appends the report.
- In-app Grok gets a multi-view visual protocol: isometric, front, and top;
  pixels are not dimensions; stop retrying a view when the screenshot is
  unchanged.
- Preferences: Connect Grok Bot on loopback `127.0.0.1:8766`, writes an
  `AGENTS.md` brief, and launches
  `C:\Program Files\Grok Bot\Grok Bot.exe` (not Grok Build `grok.exe`).
  Copy connection includes `brief_path`. Apply persists `GrokBotCommand`.
- If `VibeCADGeometryWorker.exe` is missing, `read_geometry` can fall back
  in-process.
- Compact reference images still downscale when the long edge exceeds
  `max_edge`.
- Fetch-models probes Ollama `/api/version` once.

## Native Aero

Airplane is on the Native tool list. A flight-card wrapper exists.

Do not claim a finished Aero manual or a finished ribbon / tool tutorial.
Those are later work.

## Intentional non-goals (do not "fix" these)

- HTTP MCP / `:8765/mcp` stays stdio-only by design
  (`MCP_TRANSPORT = "stdio"`).
- The legacy Workbenches preferences page stays unregistered
  (`test_vibecad_does_not_expose_the_legacy_workbench_preferences_page`).

## Codex comment map (already addressed on the fork)

| Comment | Status |
| --- | --- |
| PR5 3800704154 repair opt-in | On this PR (`repair=False`) |
| PR5 3800704158 has_h_tail | On this PR |
| PR5 3800704162 BoundBox lengths | On this PR |
| PR6 3800770744 AeroAssistantJson overwrite | On this PR |
| PR6 3800770747 steering only while run active | On this PR |
| PR7 3810898849 InitGui `_setup_agent_control` test | On this PR |
| PR8 3811086675 manufacturing-review flush-left | On this PR |
| PR9 3811348714 brief_path in Copy connection | On this PR |
| PR9 3811348727 persist GrokBotCommand | On this PR |
| PR2 3797487298 max_edge on compact images | On this PR |
| PR2 3797487304 one Ollama probe | On this PR |
| PR2 3797487290 HTTP MCP | Left alone on purpose |
| PR2 3797487295 Workbenches page | Left alone on purpose |

## How to test

```
python3 -m pytest -q \
  src/Mod/VibeCADAero/tests \
  src/Mod/VibeCAD/vibecad_tests/test_aero_ribbon_and_context.py \
  src/Mod/VibeCAD/vibecad_tests/test_aero_ribbon_install.py \
  src/Mod/VibeCAD/vibecad_tests/test_agent_control.py \
  src/Mod/VibeCAD/vibecad_tests/test_agent_control_grok_bot.py \
  src/Mod/VibeCAD/vibecad_tests/test_geometry_worker_fallback.py \
  src/Mod/VibeCAD/vibecad_tests/test_prompt_starters.py \
  src/Mod/VibeCAD/vibecad_tests/test_ollama_inspect.py \
  src/Mod/VibeCAD/vibecad_tests/test_reference_image_downscale.py \
  src/Mod/VibeCAD/vibecad_tests/test_branding_contract.py::test_setup_agent_control_invokes_local_vibecadgui_import \
  src/Mod/VibeCAD/vibecad_tests/test_branding_contract.py::test_vibecad_does_not_expose_the_legacy_workbench_preferences_page \
  src/Mod/VibeCAD/vibecad_tests/test_mcp_control_mode.py::test_connection_configuration_is_a_clean_stdio_launch_specification
```

## Additive / compatibility

No public API removals. New optional `AeroAssistantJsonText`. Agent control
is still `127.0.0.1` only. OpenAI / Anthropic paths are unchanged.

Repair default is now `False`. That **is** a default change versus current
upstream `repair=True`. It is the Codex-requested opt-in: Analyze still
works; it just does not mutate CAD unless asked.

## Geometry note for reviewers

AeroSandbox +X is aft. CAD +X is nose. Upper wing aft of lower is a
biplane, not a canard.
