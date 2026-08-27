# VibeCAD common engineering contract change policy

The `VibeCADEngineeringContracts` facade is an additive, installed compatibility
surface. Its contract major version is refused when unknown. Readers accept a
newer minor version only when all required version-1 fields retain their meaning
and any new fields are optional to older readers.

The following changes require a new major version and a separately approved
migration: removing or renaming a required field; changing an identity's
namespace, owner, kind, value, or version semantics; collapsing execution,
verification, currentness, or publication axes; changing digest meaning; or
weakening secret, size, duplicate-ID, provenance-reference, or canonical-JSON
validation.

New domain payload fields remain opaque to the host. A domain may add payload
content without changing the common schema, but it must version and validate its
own payload and may not serialize credentials, live document objects, provider
instances, process handles, callbacks, or absolute temporary paths.

Every contract change must add or update canonical fixtures, round-trip tests,
unknown-version tests, redaction tests, and installed-tree facade coverage. A
new public facade must remain in the default CMake install component unless an
explicit compatibility migration approves a different deployment contract.

