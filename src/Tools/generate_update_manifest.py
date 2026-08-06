#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Generate VibeCAD's version/build update manifest from release assets."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from resolve_release_artifact_name import (
    resolve_artifact_basename,
    resolve_release_build,
    resolve_release_channel,
    resolve_release_tag,
    resolve_release_version,
)


SCHEMA_VERSION = 1
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class AssetKind:
    pattern: re.Pattern[str]
    platform: str
    kind: str
    arch_group: str = "arch"


_ASSET_KINDS = (
    AssetKind(
        re.compile(r"-Windows-(?P<arch>x86_64)-installer\.exe$"),
        "windows",
        "installer",
    ),
    AssetKind(
        re.compile(r"-Windows-(?P<arch>x86_64)\.7z$"),
        "windows",
        "portable",
    ),
    AssetKind(
        re.compile(r"-Linux-(?P<arch>x86_64|aarch64)\.AppImage$"),
        "linux",
        "appimage",
    ),
    AssetKind(
        re.compile(r"-Linux-(?P<arch>x86_64|aarch64)\.AppImage\.zsync$"),
        "linux",
        "appimage-zsync",
    ),
    AssetKind(
        re.compile(r"-Linux-(?P<arch>amd64|arm64)\.deb$"),
        "linux",
        "deb",
    ),
    AssetKind(
        re.compile(r"-macOS[0-9]+-(?P<arch>x86_64|arm64)\.dmg$"),
        "macos",
        "dmg",
    ),
)

_NORMALIZED_ARCHITECTURES = {
    "amd64": "x86_64",
    "arm64": "aarch64",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _classify_asset(name: str) -> tuple[str, str, str]:
    for asset_kind in _ASSET_KINDS:
        match = asset_kind.pattern.search(name)
        if match:
            architecture = match.group(asset_kind.arch_group)
            return (
                asset_kind.platform,
                _NORMALIZED_ARCHITECTURES.get(architecture, architecture),
                asset_kind.kind,
            )
    raise ValueError(f"unsupported release asset: {name}")


def _validate_published_at(value: str) -> str:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid published-at timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("published-at timestamp must include a UTC offset")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def generate_manifest(
    repo_root: Path,
    assets_dir: Path,
    *,
    repository: str,
    published_at: str,
) -> dict[str, object]:
    """Return a deterministic manifest for package files in ``assets_dir``."""

    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(f"invalid GitHub repository: {repository!r}")

    artifact_basename = resolve_artifact_basename(repo_root)
    release_tag = resolve_release_tag(repo_root)
    download_base = (
        f"https://github.com/{repository}/releases/download/"
        f"{quote(release_tag, safe='')}"
    )
    assets: list[dict[str, object]] = []
    identities: set[tuple[str, str, str]] = set()

    for path in sorted(assets_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name.endswith("-SHA256.txt"):
            continue
        if not path.name.startswith(f"{artifact_basename}-"):
            raise ValueError(
                f"release asset does not use canonical basename {artifact_basename!r}: "
                f"{path.name}"
            )
        platform, architecture, kind = _classify_asset(path.name)
        identity = (platform, architecture, kind)
        if identity in identities:
            raise ValueError(f"duplicate release asset identity {identity}: {path.name}")
        identities.add(identity)
        assets.append(
            {
                "platform": platform,
                "architecture": architecture,
                "kind": kind,
                "name": path.name,
                "url": f"{download_base}/{quote(path.name, safe='')}",
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    if not assets:
        raise ValueError(f"no release package assets found in {assets_dir}")

    version = resolve_release_version(repo_root)
    build = resolve_release_build(repo_root)
    channel = resolve_release_channel(repo_root)
    return {
        "schema": SCHEMA_VERSION,
        "product": "VibeCAD",
        "channel": channel,
        "version": version,
        "build": build,
        "release_tag": release_tag,
        "release_url": f"https://github.com/{repository}/releases/tag/{quote(release_tag, safe='')}",
        "published_at": _validate_published_at(published_at),
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("assets_dir", type=Path)
    parser.add_argument("--repository", default="10-X-eng/vibecad")
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        repo_root = args.repo_root.resolve(strict=True)
        assets_dir = args.assets_dir.resolve(strict=True)
        manifest = generate_manifest(
            repo_root,
            assets_dir,
            repository=args.repository,
            published_at=args.published_at,
        )
        output = args.output or (
            assets_dir
            / f"VibeCAD-update-{resolve_release_version(repo_root)}-"
            f"build{resolve_release_build(repo_root)}.json"
        )
        output.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
