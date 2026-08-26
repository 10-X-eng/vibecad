# FluidX3D Vendor Integration — Pass 03

**Pinned source:** `ProjectPhysX/FluidX3D@8986874e626e0aebd317ab16c420b39e30dfa273`

## Target integration

VibeCAD plans to vendor the pinned FluidX3D source tree beneath:

`src/Mod/VibeCADAero/vendor/FluidX3D/`

and build/package the VibeCAD FluidX3D bridge with the Aero capability. An explicitly configured external FluidX3D bridge is also supported as a normal override.

FluidX3D remains third-party software. Keep its authoritative `LICENSE.md` and origin visible with the vendored source. VibeCAD-owned bridge files and any modified FluidX3D source should be clearly identifiable in human-readable documentation/source history.

## No product-wide use profiles

Do not classify VibeCAD or VibeCADAero by FluidX3D-specific use terms. Do not create product-wide purpose profiles, purpose detectors or backend entitlement systems. The current FluidX3D license and `THIRD_PARTY_NOTICES.md` tell users/distributors the applicable FluidX3D terms.

The official FluidX3D repository currently does not publish a standardized commercial agreement/deployment model. If explicit permission is obtained in the future, the actual granted terms control; VibeCAD does not pre-invent deployment terms that are not actually published.

## Runtime behavior

1. Look for an explicitly configured bridge override first when the user supplied one.
2. Otherwise use the packaged vendored bridge.
3. Do not auto-download solver source during a normal run.
4. Do not ask purpose-of-use questions.
5. Do not add per-run or per-solver legal prompts.
6. First Aero entry uses the one product-level informational notice documented elsewhere.

## Re-vendoring engineering checklist

When updating the vendored source:

- freeze the exact new upstream commit;
- read current upstream build/API/license docs and update human-readable notices as needed;
- rebuild the VibeCAD bridge;
- verify the APIs actually used by the bridge;
- rerun scale/unit/force/torque/domain/refinement/field tests;
- rerun platform packaging tests;
- update the recorded source pin.

This checklist is engineering/documentation work, not a purpose-of-use enforcement gate.
