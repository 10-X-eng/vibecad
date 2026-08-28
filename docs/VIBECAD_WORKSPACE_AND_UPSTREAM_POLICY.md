# VibeCAD workspace and upstream policy

## Repository authority

- `C:\VibeCAD` is the only canonical local VibeCAD working repository.
- `origin` is the Halthings product fork: `https://github.com/halthinks/vibecad.git`.
- `upstream` is the source project: `https://github.com/10-X-eng/vibecad.git`.
- Product work targets the Halthings fork. Upstream compatibility is useful, but
  upstream merge readiness does not define the fork's product direction.

Other VibeCAD directories are recovery material, exported artifacts, or retired
workspaces. They are not valid starting points for new product work.

## Upstream intake

Upstream work should continuously benefit this fork without surrendering the
fork's product decisions:

1. Fetch `upstream` and inspect commits not yet contained by `main`.
2. Classify them as bug fixes, reliability and test improvements, platform and
   build maintenance, useful features, or incompatible product-direction work.
3. Integrate useful changes in a temporary branch, resolving them in favor of
   the Halthings product contract where the two projects differ.
4. Run the tests appropriate to every affected area and review the resulting
   user-visible behavior before updating `main`.
5. Record intentionally skipped upstream changes so they are not repeatedly
   rediscovered or accidentally introduced later.

Never merge upstream mechanically merely to make the commit graph look current.
The goal is to retain upstream quality while keeping this version coherent.

## Recovery branches

Imported checkpoint and historical branches use the `recovery/` namespace or
remote-tracking references under `recovery-aero/` and
`recovery-cad-honesty/`. They exist to preserve evidence and unique work. A
recovery branch is not automatically approved for product integration; its
changes must be compared with current `main`, tested, and deliberately merged.

## Generated artifacts

The current roadmap and user-experience paper is stored at:

`docs/reports/VibeCAD_Remaining_Roadmap_and_User_Experience.docx`

Recovery archives and superseded workspace material are stored outside the
working repository under `C:\VibeCAD-Recovery-20260827`.
