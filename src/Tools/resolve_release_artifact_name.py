#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Resolve canonical VibeCAD version/build release metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from sync_version import VersionInfo


SHORT_SHA_LENGTH = 12
_SOURCE_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{12,64}")
_RELEASE_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")


def normalize_source_sha(source_sha: str) -> str:
    """Return a stable, lowercase 12-character source revision."""

    source_sha = source_sha.strip()
    if not _SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise ValueError(f"invalid Git source SHA: {source_sha!r}")
    return source_sha[:SHORT_SHA_LENGTH].lower()


def resolve_source_sha(repo_root: Path) -> str:
    """Read HEAD from the checkout that is being packaged."""

    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return normalize_source_sha(result.stdout)


def resolve_release_version(repo_root: Path) -> str:
    """Read the complete release version defined by version.json."""

    release_version = VersionInfo.from_json(repo_root).complete
    if not _RELEASE_VERSION_PATTERN.fullmatch(release_version):
        raise ValueError(
            "version.json produced an unsafe release version: "
            f"{release_version!r}"
        )
    return release_version


def resolve_release_build(repo_root: Path) -> int:
    """Read the non-negative public build number defined by version.json."""

    build = VersionInfo.from_json(repo_root).build
    if isinstance(build, bool) or not isinstance(build, int) or build < 0:
        raise ValueError(f"version.json produced an invalid build number: {build!r}")
    return build


def resolve_release_tag(repo_root: Path) -> str:
    """Return the immutable GitHub tag for this version/build identity."""

    return (
        f"v{resolve_release_version(repo_root)}-"
        f"build{resolve_release_build(repo_root)}"
    )


def resolve_release_title(repo_root: Path) -> str:
    """Return the human-facing GitHub release title."""

    return (
        f"VibeCAD {resolve_release_version(repo_root)} "
        f"(Build {resolve_release_build(repo_root)})"
    )


def resolve_release_channel(repo_root: Path) -> str:
    """Return ``stable`` for finals and ``preview`` for suffixed versions."""

    return "preview" if VersionInfo.from_json(repo_root).suffix else "stable"


def resolve_artifact_basename(
    repo_root: Path, *, source_sha: str | None = None
) -> str:
    """Return VibeCAD-<release-version>-build<build-number>.

    ``source_sha`` remains accepted for compatibility with existing callers.
    When supplied it is validated, but source revisions are intentionally not
    part of the public artifact identity.
    """

    if source_sha is not None:
        normalize_source_sha(source_sha)
    return (
        f"VibeCAD-{resolve_release_version(repo_root)}-"
        f"build{resolve_release_build(repo_root)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument(
        "--component",
        choices=(
            "basename",
            "release-version",
            "build",
            "release-tag",
            "release-title",
            "release-channel",
            "short-sha",
        ),
        default="basename",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    try:
        if args.component == "release-version":
            value = resolve_release_version(repo_root)
        elif args.component == "build":
            value = resolve_release_build(repo_root)
        elif args.component == "release-tag":
            value = resolve_release_tag(repo_root)
        elif args.component == "release-title":
            value = resolve_release_title(repo_root)
        elif args.component == "release-channel":
            value = resolve_release_channel(repo_root)
        elif args.component == "short-sha":
            value = (
                normalize_source_sha(args.source_sha)
                if args.source_sha is not None
                else resolve_source_sha(repo_root)
            )
        else:
            value = resolve_artifact_basename(
                repo_root,
                source_sha=args.source_sha,
            )
    except (
        json.JSONDecodeError,
        KeyError,
        OSError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:
        parser.error(str(exc))

    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
