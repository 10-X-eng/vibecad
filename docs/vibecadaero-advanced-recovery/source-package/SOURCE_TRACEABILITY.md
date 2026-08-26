# Source Traceability — Pass 03 Correction 01

## Primary design-history source

- `sources/CONVERSATION_SOURCE.md`
- SHA-256: `ccec51399b4d9320505300523de4eaf463020765c76ef4050618298dfd498c61`

This source preserves the full evolving discussion: FluidX3D/WebCAD-style CAD→LBM loop, Kaggle, OpenFOAM/CfdOF, Gmsh, mesh/solid conversion, fields, dynamic stall, unsteady coupling, 6DOF, moving bodies, high-Re, FSI, drag decomposition and the user's explicit rejection of scope truncation.

## VibeCAD upstream

- Pass 01: `b10005fa18f218d1c7bcb5880a3689a890af5628`
- Pass 02: `d0a933e40005b4affe9303f27d1eae5cd36eb030`
- Pass 03: `df07a5e82ec2fb31515e10b33822253d69d496ff`
- Pass-03 delta: 41 commits / 50 files

High-value Pass-03 upstream source seams:

- `src/Mod/VibeCAD/VibeCADNativePreviewControl.py`
- `src/Mod/VibeCAD/VibeCADNativePreviewCommands.py`
- `src/Mod/VibeCAD/VibeCADNativeState.py`
- `src/Mod/VibeCAD/VibeCADNativeDispatch.py`
- `src/Mod/VibeCAD/VibeCADAgentControl.py`
- `src/Mod/VibeCAD/VibeCADNativeAnalyzeSolverExecution.py`
- `src/Mod/VibeCAD/VibeCADNativeAnalyzeSolverExecutionProcess.py`
- `src/Mod/VibeCAD/VibeCADNativeAnalyzeSolverState.py`
- `src/Mod/VibeCAD/VibeCADNativeBackground.py`
- `src/Mod/VibeCAD/VibeCADNativeBackgroundRuntime.py`
- `src/Mod/VibeCAD/VibeCADNativeBackgroundSchema.py`
- `src/Mod/VibeCAD/VibeCADNativeMutation.py`
- `src/Mod/VibeCAD/VibeCADNativeAnalyzeAnalysis.py`
- `src/Mod/VibeCAD/VibeCADNativeOutput.py`
- `src/Mod/VibeCAD/VibeCADNativeMeasure.py`
- `src/Mod/VibeCAD/VibeCADIntentMemory.py`
- current VibeCADAero authority files (`VibeCADAero.py`, `AeroPreview.py`, `AeroStamp.py`, `AeroResults.py`, `AeroConfig.py`)

## External implementation anchors

- FluidX3D: `8986874e626e0aebd317ab16c420b39e30dfa273`
- CfdOF: `a90f60c2313ceba09c236c81f0693d93357d1614`
- Kaggle CLI current 2.2.x docs/changelog
- Gmsh 4.15.2 docs

## Traceability rule

Historical code/text is source material, not authority. A statement becomes canonical only after reconciliation against the frozen live upstream and, where relevant, current external dependency API/license documentation.


## Correction 01 host-runtime derivation

The host Analysis Runtime target is derived from responsibilities observed in the frozen upstream, not from an invented parallel framework:

- Native Background is the orchestration/status/cancel/public-surface seed; at this baseline it is in-memory, bounded and one-active-job-per-document.
- Detached FEM execution is the immutable-input/process/stale-before-publish seed.
- Detached process helpers contain the physics-neutral subprocess/cancel/timeout/child-cleanup mechanics.
- FEM solver-state canonicalization remains engineering-domain-specific and is intentionally not pulled into the generic host layer.
- Native mutation remains transaction/publication authority.

Correction 01 is intentionally based on the same Pass-03 SHA so these responsibilities can be reasoned about without mixing later upstream work.
