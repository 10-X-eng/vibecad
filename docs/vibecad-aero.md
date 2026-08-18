# VibeCAD Aero workbench

The **Aero** workbench (internal name `VibeCADAero`) runs the Voider-validated
solver stack inside VibeCAD. It does not clone anything onto a user machine
and it does not vendor NeuralFoil / AeroSandbox / JSBSim wheels.

## Use the Aero tab by hand

Aero is a first-class tab on the main VibeCAD ribbon, sitting after
**Drawing** and **Parameters**. VibeCAD inserts it at startup (next to the
agent-control hook) so it is present on every launch. Clicking Aero does
**not** switch workbenches and does **not** replace Model: the page is the
same Model groups (View, Structure, Solids, Finish, Transform, Geometry,
Modify, Inspect, Fasteners, Surface, Connect) plus an **Aero** group
(Analyze with the drone SVG, Section, 3D VLM, JSBSim).

1. Start VibeCAD and open or create a document.
2. Click **Aero** on the ribbon (next to Parameters). You do not need the
   workbench combo. Model tools stay visible.
3. In **Vehicle**, set the type:
   - **Airplane**
   - **Multirotor drone**
   - **Tailsitter VTOL** (voider default)
4. Keep **Airfoil** at `e63` unless you mean to change it. The workbench
   never silently substitutes NACA 0009.
5. Edit span, chord, AUW, alpha, and (for airplane / tailsitter) gap,
   stagger, and decalage. Prop count, diameter, and T/W apply to hover
   power. Each edit writes `AeroConfig` on the active document.
6. Use the action buttons on the same page:

| Command | What it does |
| --- | --- |
| Analyze | Section + 3D + hover, writes `AeroReport` on the active document. Defaults to `repair=True`: if the solve is pitch-unstable (`Cmα > 0`), Analyze applies bounded CAD/config repairs and re-solves up to two times |
| Section / NeuralFoil | 2D viscous section only (`model_size="large"`) |
| 3D / AeroSandbox | VortexLatticeMethod (8×6) + AeroBuildup |
| Export JSBSim plant | Writes XML and stores `JSBSimPlantPath` |
| Write report | Markdown + spreadsheet objects from a full solve |

**Results** on the Aero page update from `AeroReport`: CL, CD, CM, CLα,
Cmα, Re, V_loaf, P_hover (momentum-theory), P_cruise, pitch flag, source,
and the last JSBSim path plus boot status. Analyze still keeps its
button; the page is live so you do not have to rely on the message box.

The same commands remain on the Aero workbench toolbar if you switch
workbenches from the combo. If no document is open, Analyze creates one
named `Aero`.

The assistant sees the same numbers at turn start as an `aero` object in
`provider_context_summary` (coefficients when `AeroReport` exists,
otherwise the intended `AeroConfig` geometry).

## Geometry

Parameters are read in this order:

1. An `AeroConfig` object on the document (`span_mm`, `chord_mm`, `gap_c`,
   `stagger_c`, `decalage_deg`, `auw_g`, `airfoil`, `alpha_deg`,
   `vehicle_type`, `tail_span_mm`, `tail_chord_mm`, `boom_length_mm`,
   `xyz_ref_c`, prop fields)
2. The same names as document properties
3. Bounding boxes of objects named like the voider (`lower_wing`,
   `upper_wing`, `boom`, `h_tail`), only when inferred span/chord are
   within 0.5×–2× of the locked 500/90 mm airframe. A loft bbox such as
   1640×295 mm is rejected.
4. Locked voider-ultimate defaults: 500 mm span, 90 mm chord, gap 1.4c,
   stagger 1.15c, decalage 2°, AUW 149.6 g, airfoil **e63**, alpha 4°,
   vehicle type **tailsitter**

One-shot bbox inference is never written onto `AeroConfig`. Create or edit
`AeroConfig` yourself (the Aero tab does this as you type) so later
Analyze runs stay at the airframe you set.

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

The AeroSandbox airplane includes the biplane plus an `h_tail` sized from
`tail_span_m` / `tail_chord_m` / `boom_length_m`. Those sizes are seeded
from named `h_tail` and `boom` objects when present. AeroSandbox +X is aft.
Named-part bounding boxes are mapped into that frame (the live voider has
CAD +X toward the nose), so the upper wing is placed at **+stagger** (aft
of the lower wing), not as a canard. The HTail uses a symmetric
NACA 0008 section; the main wings stay on the configured airfoil (E63).

`AeroConfig.xyz_ref_c` is the CG in chords and survives `finalize`. A
missing tail plus a CG aft of the aero center is why a canard / wings-only
solve stays pitch-unstable.

When Analyze sees `PitchUnstable` / `Cmα > 0`, it proposes bounded repairs
to both `AeroConfig` and live named parts: more horizontal-tail volume,
more tail arm / boom length, and CG / `avionics_pod` / `camera_bay` toward
the nose. Optional extra **aft** stagger is allowed. Analyze does not shove
the upper wing toward the CAD nose. It re-solves at most twice and stops if
the model is stable or no CAD/config change landed. The dialog and FreeCAD
console list each change in plain sentences. `AeroReport.Corrections`,
`AeroReport.RepairPasses`, and `document.AeroAssistantJson` carry the same
list for the in-app assistant (`VibeCADCore.aero_summary`).
`VibeCADAero.run_analyze` returns `changes` and `user_message`.

Section / VLM-only commands stay report-only (`repair=False`) unless they
go through Analyze.

Hover power is **momentum-theory** (default 2× 178 mm props, FM=0.55,
T/W=1.9), not CFD. Cruise power is `D*V/0.65`.

## JSBSim XML location

The plant is written under a user-writable directory:

- Next to the saved document: `<document-dir>/jsbsim/vibecad_aero/`
- Otherwise: the FreeCAD user data dir `VibeCADAero/jsbsim/`

`AeroReport.JSBSimPlantPath` and the document property `JSBSimPlantPath`
point at the XML. The report also stores the solved plant geometry
(`reference_area_m2`, `span_m`, `chord_m`, `mass_kg`, `xyz_ref`) so a later
export does not silently substitute Voider defaults. Pitch is
`CM0 + Cmalpha * alpha` with `CM0 = CM - Cmalpha * alpha_solve`. If
`jsbsim.FGFDMExec` fails to load or `run_ic()` returns false (including
IC NaNs), the XML is kept, `JSBSimBootError` is stored, and the UI does
not claim the plant loaded.

## Agent control

Grok Bot can trigger a solve without SendKeys via `POST /v1/run` on
`127.0.0.1:8766`:

```python
import VibeCADAero
result = VibeCADAero.run_analyze(App.ActiveDocument)
```

This does not change the assistant, Grok OAuth, or the agent-control port.
