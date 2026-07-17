# Agent Instructions for VibeCAD

These rules apply to coding agents and automated assistants working in this repository. Follow them in addition to `CONTRIBUTING.md`, `AI_POLICY.md`, and the project PR template.

## Goals

1. Prefer **additive** change: extend behavior without breaking existing callers.
2. Keep pull requests **small, coherent, and merge-ready** for the repository owner.
3. Never remove public surface area or change APIs without **explicit human approval**.

## Additive-first change policy

When introducing new features, capabilities, tools, providers, preferences, or internal helpers:

1. **Add** new functions, modules, options, or code paths.
2. **Keep** existing functions, signatures, defaults, and behaviors working unless approval says otherwise.
3. Prefer optional parameters, new wrappers, adapters, or feature flags over rewriting call sites.
4. Prefer deprecation + dual-path support over hard cuts.
5. Do not “clean up” unrelated code while implementing a feature.

Good:

- Add `provider="xai"` while leaving OpenAI/Anthropic paths intact.
- Add a new tool while keeping old tools registered and callable.
- Add a helper used by new code without deleting the previous helper.

Bad without approval:

- Delete a function “because nothing should call it anymore.”
- Rename a public method and only update the call sites you noticed.
- Change return shapes, exception types, preference keys, tool names, or wire formats in place.
- Collapse two APIs into one and remove the old entry point in the same PR.

## Removals and API changes require explicit approval

**Stop and ask the owner** before doing any of the following:

| Change type | Examples |
|-------------|----------|
| Function/method removal | Deleting exports, Python entry points, C++ public methods, CLI flags |
| Signature changes | Reordering args, removing params, changing required/optional status |
| Behavioral breaks | Different defaults, different error semantics, silent no-ops becoming hard failures (or the reverse) |
| Contract/schema changes | Tool JSON schemas, preference keys, config file fields, REST/SDK payloads, document property names |
| Module moves/renames | Relocating public modules without compatibility shims |
| Dependency removals | Dropping packages or raising minimum versions in a way that breaks existing builds |

### How to request approval

In chat (or the PR description if already drafting), state clearly:

1. **What** would be removed or broken.
2. **Why** additive compatibility is insufficient or too costly.
3. **Who/what** is affected (callers, tools, prefs, docs, tests, packaging).
4. **Migration plan** (shim period, rename map, dual-read/write, feature flag).
5. **Rollback plan**.

Do **not** implement the breaking change until the owner explicitly approves it.

If a task seems to require a break, implement the additive path first when possible, and list the deferred breaking follow-up separately.

## Pull requests must be owner-mergeable

Every PR an agent prepares SHOULD be something the owner can merge with high confidence.

### PR shape

1. **One logical change** per PR (or a short, ordered stack of dependent PRs).
2. Title and description explain **user-visible outcome** and **why**.
3. Link related issues when they exist.
4. Call out risk, test plan, and any deliberate non-goals.
5. Disclose AI assistance per `AI_POLICY.md` when applicable.
6. Do not mix unrelated refactors, formatting sweeps, or dependency upgrades with feature work.

### Compatibility checklist (required in the PR body when relevant)

- [ ] Existing public functions/APIs still present and behaviorally compatible.
- [ ] No preference keys, tool names, or schema fields renamed/removed without approval.
- [ ] Defaults preserve previous behavior for users who do not opt into new features.
- [ ] Deprecations (if any) are documented and dual-path supported.
- [ ] Breaking changes are listed with owner approval reference.

### Quality bar before asking for merge

1. Builds for the touched area (or full project when CMake/public headers change).
2. Relevant tests pass; add tests for new behavior when practical.
3. No secrets, credentials, or local machine paths committed.
4. Diff is reviewable: avoid generated noise and drive-by edits.
5. Commit history is intentional (no “WIP / fixup later” left on the branch tip).

### What “logically solid” means here

A merge-ready PR:

- solves one agreed problem,
- does not require the reviewer to reverse-engineer intent,
- does not force follow-up cleanup just to restore previous behavior,
- leaves the tree in a shippable state on its own.

If the work is large, split it:

1. Additive plumbing / interfaces.
2. Feature implementation behind defaults or flags.
3. Optional later PR for removals **only after approval**.

## Working rules for agents

1. Read surrounding code and existing public contracts before editing.
2. Match project style; do not reformat unrelated files.
3. Prefer the smallest diff that achieves the request.
4. When unsure whether something is public API, treat it as public and keep compatibility.
5. If blocked by a required breaking change, report the blocker and wait for approval rather than guessing.
6. After coding, verify with build/tests appropriate to the change, then summarize what was added and what was deliberately left unchanged.

## Out of scope without owner direction

- Force-pushing shared branches.
- Rewriting published history.
- Mass renames across the tree.
- Deleting modules, tools, providers, or preference packs.
- Changing release packaging, signing, or CI gates except as needed for a specifically requested fix.

## Quick decision guide

```text
Need new capability?
  └─ Add new API/path. Keep old one.          → proceed

Need to change behavior for existing users?
  └─ Can it be opt-in / dual-path?            → do that
  └─ Must break old API?                     → get explicit approval first

Tempted to delete “dead” code?
  └─ Unless the owner asked to remove it,    → leave it / ask first
```
