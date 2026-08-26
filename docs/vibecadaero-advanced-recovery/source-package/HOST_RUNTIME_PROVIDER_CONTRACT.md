# Host Analysis Runtime — Compute Provider Contract

## Separation law

A **solver/backend** defines engineering computation. A **compute provider** defines where/how an immutable prepared computation executes.

Examples:

- CalculiX + LocalProcessProvider;
- OpenFOAM + LocalProcessProvider;
- OpenFOAM + KaggleProvider;
- FluidX3D + LocalProcessProvider;
- future remote/HPC provider + any portable prepared solver bundle.

The provider does not choose physics, mesh policy, turbulence model, qualification, or result meaning.

## Minimal provider interface

Conceptually:

```python
class ComputeProvider:
    def capabilities(self) -> ProviderCapabilities: ...
    def submit(self, work: PreparedWork, cancel, log) -> ProviderHandle: ...
    def poll(self, handle) -> ProviderStatus: ...
    def cancel(self, handle) -> CancelReceipt: ...
    def reconnect(self, external_job_id) -> ProviderHandle | None: ...
    def collect(self, handle) -> ArtifactManifest: ...
    def cleanup(self, handle) -> None: ...
```

Not every provider supports reconnect. Capability flags must say so explicitly.

## `PreparedWork`

Provider-neutral durable work includes:

- immutable input bundle/artifact manifest;
- explicit entrypoint or solver launch descriptor;
- resource requirements;
- timeout/deadline;
- declared expected outputs;
- environment requirements without embedded secrets;
- solver/backend identity;
- input manifest hash.

It excludes live CAD objects and engineering publication callbacks.

## Provider status

Generic provider state is translated into compatibility-facing job state by the host runtime. Provider status must not directly mutate domain state.

## Remote-provider trust boundary

Remote completion is not enough for CAD attachment. Collected artifacts require:

- identity/checksum verification;
- solver/input receipt correlation;
- domain parse validation;
- exact source/currentness revalidation;
- main-thread publication transaction.

## Security and secrets

Credentials live in provider configuration/credential facilities, not persisted job descriptors or solver bundles. Logs/artifacts are redacted where necessary.
