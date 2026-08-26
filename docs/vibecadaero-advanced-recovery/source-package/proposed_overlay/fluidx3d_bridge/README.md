# VibeCAD FluidX3D Bridge Reference

This directory contains the VibeCAD-owned `setup_vibecad.cpp` reference bridge intended to be built against the pinned vendored FluidX3D source. It uses verified source-level FluidX3D APIs rather than the unverified Python API assumed in early discussion drafts.

The normal target product uses the packaged vendored bridge; `AeroLBM` also accepts an explicitly configured external bridge override. This is ordinary configuration, not a purpose/use profile.

The bridge contract uses environment/job files for SI physical inputs, geometry scale, domain/resolution, transient/sample controls and result location. VibeCAD computes final aerodynamic coefficients from canonical reference quantities after the bridge returns dimensional body-axis force/moment evidence.

FluidX3D's authoritative third-party license remains readable with its vendored source. Aero's single first-use notice is informational and uses **“I understand.”**
