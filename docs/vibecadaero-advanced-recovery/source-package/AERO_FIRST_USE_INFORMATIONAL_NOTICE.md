# Aero First-Use Informational Notice

## Canonical behavior

Show this notice **once**, the first time a user enters Aero. The only acknowledgement checkbox is exactly:

> **I understand.**

After it is checked, store one local unversioned boolean (`ThirdPartyNoticesAcknowledged`). In normal use, never show the notice again, including after product/backend/license-document updates. Do not transmit the flag.

The acknowledgement records only that the notice was seen. It is not an “I agree” contract, a purpose declaration, an entitlement/compliance check, a solver-selection rule, telemetry, or a restriction on what the user designs.

## Recommended UI copy

**Third-Party Software Notice**

VibeCAD Aero can use third-party software with license terms separate from VibeCAD. FluidX3D is one such solver. Those terms apply to the third-party components they govern; they do not make VibeCAD or VibeCADAero non-commercial and do not change ownership of CAD designs created in VibeCAD.

See **Third-Party Notices** for component-specific details and the authoritative license texts.

- [ ] **I understand.**
- **Continue**
