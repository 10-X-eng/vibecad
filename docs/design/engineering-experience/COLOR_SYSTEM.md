# Engineering Experience color system

## Two independent color languages

Scientific color represents a numeric field. Governance color represents state.
They must never be inferred from one another.

### Scientific presentation

| Semantic family | Initial default | Notes |
| --- | --- | --- |
| Non-negative stress magnitude | perceptually ordered turbo-style scale | Legend and exact range required |
| Displacement/velocity magnitude | viridis-style sequential scale | Avoid false categorical boundaries |
| Signed pressure/displacement | blue-white-red diverging scale | Center value must be declared |
| Temperature | inferno-style sequential scale | Unit and absolute/delta meaning required |
| Safety factor | explicitly labeled inverted severity scale | Threshold is domain evidence, not palette inference |

Colormap choice, clamp, logarithmic display, and deformation scale are
presentation state. Field values, units, association, components, numeric range,
source identity, and currentness are engineering state.

### Governance/status presentation

| State family | Color role |
| --- | --- |
| verified/current/completed | green |
| selected/running/active technical state | cyan or blue |
| warning/stale/indeterminate | amber |
| failed/blocking/invalid | red |
| unavailable/historical/cancelled | neutral gray |

Text and icons must accompany status color. Color alone is never the status
contract.

## Theme integration

Keep the existing neutral VibeCAD base (`#0e1116`, `#171c23`, `#1d242d`) and
existing accent (`#4dabf7`). Add engineering selectors/tokens only where the Qt
theme mechanism supports them. Approximately ninety percent of the visual area
should remain neutral; saturated signal colors should be scarce and semantic.

Required reusable roles include engineering cyan/blue/green/amber/red, result
card surface/border/active border, viewport grid/edge, finding severity, and
historical/stale treatment. Light-theme equivalents are required before a
shared component is called complete.

