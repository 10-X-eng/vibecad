# Host Analysis Runtime — Local Process Supervision Contract

## Current limitation

The frozen FEM helper supervises a direct `subprocess.Popen` object and terminates/kills that direct process. That does not guarantee termination of solver descendants such as MPI ranks, wrapper scripts, helper executables, or spawned workers.

## Provider-owned process tree

`LocalProcessProvider` SHALL own the complete process tree it launches.

### POSIX target

- launch in a dedicated session/process group (`start_new_session=True` or equivalent);
- graceful cancellation signals the owned process group;
- after bounded grace period, hard-kill the group;
- reap the root process;
- stop/join output readers;
- verify no owned children remain where feasible.

### Windows target

- create an isolated process group where needed;
- use a Windows Job Object abstraction for reliable descendant ownership/termination when available;
- graceful signal where solver supports it;
- bounded fallback termination of the owned job/process tree;
- drain/join output readers;
- do not kill unrelated processes by image name.

## Cancellation vs timeout vs host shutdown

They share mechanics but produce distinct structured exit reasons:

- `user_cancelled`;
- `deadline_exceeded`;
- `host_shutdown`;
- `provider_failure`;
- `process_exit_nonzero`.

The solver/domain error adapter may map these into existing FEM-visible errors during compatibility migration.

## Log handling

- stream stdout/stderr without blocking solver progress;
- preserve bounded in-memory tail for status;
- optionally spool complete log to an artifact file;
- redact secrets before durable storage;
- reader thread/task termination is part of cleanup;
- log transport failure must not silently convert a solver success into a false failure unless output integrity is actually required.

## Command/environment evidence

Execution receipt records normalized command metadata and environment policy without persisting secrets. Provider-specific launch details are evidence; they are not domain currentness.

## Cleanup law

Cleanup is idempotent and divided into:

1. stop owned execution resources;
2. close readers/handles;
3. seal required artifacts;
4. release provider resources;
5. remove disposable workspace only after artifacts/publication no longer depend on it.

A retry of cleanup after partial failure must be safe.
