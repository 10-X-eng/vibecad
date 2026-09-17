# VibeCAD click-channel workflow harness

`src/Mod/VibeCAD/VibeCADWorkflowHarness.py` drives three operator workflows
through the existing authenticated loopback click channel
(`POST /v1/ui/click` / `VibeCADAgentCli ui-click`). It does not move the OS
cursor and it does not replace
[`Invoke-VibeCAD-VisibleTour.ps1`](../Invoke-VibeCAD-VisibleTour.ps1). The
tour remains the human-watchable demo.

## Workflows

| Workflow | Clicks | Post-click checks |
| --- | --- | --- |
| `new_document` | `command` `VibeCADRibbonNew` (`Std_New`) | Semantic click verified; document count +1; active document present |
| `sketch_then_pad` | New document, `ribbon` `Model`, `Sketcher_NewSketch`, `PartDesign_Pad` | Model tab selected; sketch and pad objects present |
| `export` | New document, `command` `Std_Export` | Semantic click verified; active document still present |

Each step records the click payload plus document/object state. Code owns
timeout, click success, and pass/fail.

## Run

Live GUI (same agent home / token as the visible tour):

```sh
python src/Mod/VibeCAD/VibeCADWorkflowHarness.py --gui-only
```

Headless / CI (fake click channel, no display, no TypeSafe key):

```sh
python src/Mod/VibeCAD/VibeCADWorkflowHarness.py --fake-channel
python -m pytest -q src/Mod/VibeCAD/vibecad_tests/test_workflow_harness.py
```

Set `PYTHONPATH=src/Mod/VibeCAD` when running from the repository root.

## Optional Jev classification

After a step, one System One request may classify:

- `landed` (Noul): did the step actually complete
- `failure_class` (Choice): `geometry_bug`, `wrong_button`, `model_lied`, `flake`
- `progress` (Score): how far the workflow got

This path is default-off. A live call happens only when both
`VIBECAD_JEV_JUDGE=1` and `TYPESAFE_API_KEY` are set. Low confidence never
changes a green result. CI mocks or skips the judge. Do not commit a key.
