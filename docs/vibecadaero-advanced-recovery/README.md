# VibeCADAero advanced-plan recovery

This directory is the project-owned preservation record for the recovered Advanced VibeCAD / VibeCADAero plan.

The active, current-source-reconciled implementation plan is the repository's [VibeCADAero canonical roadmap](../vibecadaero-roadmap.md). This directory is its complete preserved evidence and design record; it is not a competing current-status document.

It exists so that the architecture, roadmap, FluidX3D first-use behavior, migration contracts, known hazards, reference code, tests, and original source package remain searchable and recoverable from the VibeCAD project itself rather than depending on a ChatGPT conversation or an external handoff directory.

## Start here

- [Canonical active roadmap](../vibecadaero-roadmap.md)
- [Recovered advanced roadmap](RECOVERED_ADVANCED_VIBECAD_ROADMAP.md)
- [Second-pass no-loss preservation supplement](VIBECADAERO_SECOND_PASS_PRESERVATION_SUPPLEMENT.md)
- [FluidX3D first-use notice and vendor-policy contract](RECOVERED_FLUIDX3D_FIRST_USE_GATE.md)

The supplement contains the detailed architecture and complete 21-step dependency-ordered implementation sequence. The shorter roadmap is an executive grouping and current-state handoff; it must not replace the detailed contracts.

## Original recovered source

- [Complete original archive](VibeCADAero_Reconciliation_Pass_03_Correction_01_df07a5e.zip)
- [File-by-file SHA-256 manifest](VIBECADAERO_EXTRACTED_FILE_MANIFEST.sha256)
- [Expanded searchable source package](source-package/README.md)

Archive SHA-256:

```text
AB0E315D811F5FD77D0D4FA9220E5511481C57AA8AA65128F23D4475030915ED
```

The archive and expanded package contain 110 files. The manifest was recomputed against every expanded file with zero mismatches.

## Source anchors

- frozen VibeCAD design source: `halthinks/vibecad@df07a5e82ec2fb31515e10b33822253d69d496ff`;
- FluidX3D source pin: `ProjectPhysX/FluidX3D@8986874e626e0aebd317ab16c420b39e30dfa273`;
- CfdOF source pin: `a90f60c2313ceba09c236c81f0693d93357d1614`;
- reference-overlay validation recorded by the package: 45 tests passed.

The frozen SHA and reference tests are historical design evidence, not proof that every target capability is integrated into the current repository. Before implementation, freeze the then-current `main`, reconcile live drift, and follow the characterization, parity, publication-authority, rollback, and release gates preserved in the package.

## Preservation rule

The ZIP is the byte-for-byte archival source. `source-package/` is its expanded searchable form. The recovered roadmap and supplement explain and index the material without superseding the original files.

Do not silently delete, condense, or replace this package with a smaller handoff. Future corrections should be additive, identify what they supersede, preserve the prior evidence, and update this index.

This documentation package does not itself authorize implementation or claim production readiness. It changes no runtime behavior.
