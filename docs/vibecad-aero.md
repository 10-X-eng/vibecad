# VibeCAD Aero workbench

The **Aero** workbench (internal name `VibeCADAero`) runs the Voider-validated
solver stack inside VibeCAD. It does not clone anything onto a user machine
and it does not vendor NeuralFoil / AeroSandbox / JSBSim wheels.

## Switch to Aero

1. Start VibeCAD.
2. Open the workbench selector (the combo next to the Start / Part / etc. list).
3. Choose **Aero**.

The toolbar has:

| Command | What it does |
| --- | --- |
| Analyze | Section + 3D + hover, writes `AeroReport` on the active document |
| Section / NeuralFoil | 2D viscous section only (`model_size="large"`) |
| 3D / AeroSandbox | VortexLatticeMethod (8×6) + AeroBuildup |
| Export JSBSim plant | Writes XML and stores `JSBSimPlantPath` |
| Write report | Markdown + spreadsheet objects from a full solve |

If no document is open, Analyze creates one named `Aero`.

## Geometry

Parameters are read in this order:

1. An `AeroConfig` object on the document (`span_mm`, `chord_mm`, `gap_c`,
   `stagger_c`, `decalage_deg`, `auw_g`, `airfoil`, `alpha_deg`)
2. The same names as document properties
3. Bounding boxes of objects named like the voider (`lower_wing`,
   `upper_wing`, `boom`, `h_tail`)
4. Locked voider-ultimate defaults: 500 mm span, 90 mm chord, gap 1.4c,
   stagger 1.15c, decalage 2°, AUW 149.6 g, airfoil **e63**, alpha 4°

E63 is bundled as `Mod/VibeCADAero/data/e63.dat` (UIUC coordinates). The
workbench will not silently replace E63 with NACA 0009.

## Install optional solvers

The workbench loads even when the pip packages are missing. Commands then
show the exact install line for VibeCAD's bundled Python:

```bat
"<VibeCAD>\bin\python.exe" -m pip install -r "<VibeCAD>\Mod\VibeCADAero\requirements-aero.txt"
```

Packages: `neuralfoil`, `aerosandbox`, `jsbsim`. Do not copy wheels into git.

## Results

Analyze writes a tree-visible `AeroReport` FeaturePython with CL, CD, CM,
CLα, Cmα, Re, V_loaf, P_hover, P_cruise, source, and a pitch-unstable flag
when Cmα > 0. Coefficient source order is AeroBuildup, else VLM, else
NeuralFoil.

Hover power is **momentum-theory** (default 2× 178 mm props, FM=0.55,
T/W=1.9), not CFD. Cruise power is `D*V/0.65`.

## JSBSim XML location

The plant is written under a user-writable directory:

- Next to the saved document: `<document-dir>/jsbsim/vibecad_aero/`
- Otherwise: the FreeCAD user data dir `VibeCADAero/jsbsim/`

`AeroReport.JSBSimPlantPath` and the document property `JSBSimPlantPath`
point at the XML. If `jsbsim.FGFDMExec` fails to load (including IC NaNs),
the XML is kept and the boot error is reported.

## Agent control

Grok Bot can trigger a solve without SendKeys via `POST /v1/run` on
`127.0.0.1:8766`:

```python
import VibeCADAero
result = VibeCADAero.run_analyze(App.ActiveDocument)
```

This does not change the assistant, Grok OAuth, or the agent-control port.
