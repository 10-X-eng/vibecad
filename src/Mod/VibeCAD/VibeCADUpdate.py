# SPDX-License-Identifier: LGPL-2.1-or-later

"""Version/build update discovery and package download support.

The GUI layer is intentionally separate.  This module owns release identity,
enterprise policy, GitHub Release verification, optional TUF verification,
resumable downloads, and install plans so the security-sensitive behavior can
be tested without Qt or a running FreeCAD.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


UPDATE_REPOSITORY = "10-X-eng/vibecad"
GITHUB_RELEASES_API_URL = (
    f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases?per_page=100"
)
UPDATE_SCHEMA = 1
DEFAULT_CHECK_INTERVAL_HOURS = 24
MINIMUM_CHECK_INTERVAL_HOURS = 1
_CHANNELS = frozenset({"stable", "preview"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<suffix>[A-Za-z0-9][A-Za-z0-9.-]*))?"
)
_PRERELEASE_PATTERN = re.compile(
    r"(?P<label>dev|alpha|beta|rc)(?:[.-]?(?P<number>[0-9]+))?",
    re.IGNORECASE,
)
_PRERELEASE_RANK = {"dev": 0, "alpha": 1, "beta": 2, "rc": 3}
_MAX_RELEASE_INDEX_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_CHECKSUM_BYTES = 4096


class UpdateError(RuntimeError):
    """Base class for actionable update-system failures."""


class UpdateTrustError(UpdateError):
    """Update metadata is missing, expired, or invalid."""


class DownloadCancelled(UpdateError):
    """The caller cancelled a download; the partial file remains resumable."""


@dataclass(frozen=True)
class ReleaseIdentity:
    version: str
    build: int
    major: int = field(init=False, repr=False)
    minor: int = field(init=False, repr=False)
    patch: int = field(init=False, repr=False)
    suffix: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        match = _VERSION_PATTERN.fullmatch(str(self.version).strip())
        if match is None:
            raise ValueError(f"invalid VibeCAD release version: {self.version!r}")
        if isinstance(self.build, bool) or not isinstance(self.build, int) or self.build < 0:
            raise ValueError(f"invalid VibeCAD build number: {self.build!r}")
        object.__setattr__(self, "version", str(self.version).strip())
        object.__setattr__(self, "major", int(match.group("major")))
        object.__setattr__(self, "minor", int(match.group("minor")))
        object.__setattr__(self, "patch", int(match.group("patch")))
        object.__setattr__(self, "suffix", str(match.group("suffix") or ""))

    @property
    def tag(self) -> str:
        return f"v{self.version}-build{self.build}"

    @property
    def display(self) -> str:
        return f"{self.version} (Build {self.build})"

    @property
    def channel(self) -> str:
        return "preview" if self.suffix else "stable"

    def _prerelease_key(self) -> tuple[int, int, str]:
        if not self.suffix:
            return (4, 0, "")
        match = _PRERELEASE_PATTERN.fullmatch(self.suffix)
        if match is None:
            return (0, 0, self.suffix.casefold())
        label = match.group("label").casefold()
        return (
            _PRERELEASE_RANK[label],
            int(match.group("number") or 0),
            "",
        )

    def precedence_key(self) -> tuple[object, ...]:
        return (
            self.major,
            self.minor,
            self.patch,
            *self._prerelease_key(),
            self.build,
        )

    def is_newer_than(self, other: "ReleaseIdentity") -> bool:
        return self.precedence_key() > other.precedence_key()


@dataclass(frozen=True)
class UpdateAsset:
    platform: str
    architecture: str
    kind: str
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateRelease:
    identity: ReleaseIdentity
    channel: str
    release_tag: str
    release_url: str
    published_at: str
    assets: tuple[UpdateAsset, ...]

    @property
    def display(self) -> str:
        return self.identity.display

    def asset_for(self, system: str | None = None, machine: str | None = None) -> UpdateAsset | None:
        system_name = (system or platform.system()).casefold()
        platform_name = {"windows": "windows", "linux": "linux"}.get(system_name)
        if platform_name is None:
            return None
        architecture = normalize_architecture(machine or platform.machine())
        preferred_kind = "installer" if platform_name == "windows" else "appimage"
        return next(
            (
                asset
                for asset in self.assets
                if asset.platform == platform_name
                and asset.architecture == architecture
                and asset.kind == preferred_kind
            ),
            None,
        )


@dataclass(frozen=True)
class UpdatePolicy:
    enabled: bool = True
    automatic_checks: bool = True
    channel: str = "auto"
    check_interval_hours: int = DEFAULT_CHECK_INTERVAL_HOURS
    automatic_download: bool = False
    install_on_exit: bool = False
    metadata_base_url: str = ""
    target_base_url: str = ""
    trusted_root: str = ""
    managed: bool = False

    def resolved_channel(self, current: ReleaseIdentity) -> str:
        if self.channel == "auto":
            return current.channel
        if self.channel not in _CHANNELS:
            raise ValueError(f"invalid update channel: {self.channel!r}")
        return self.channel


@dataclass(frozen=True)
class PolicyLoadResult:
    policy: UpdatePolicy
    source: str
    error: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str
    current: ReleaseIdentity
    release: UpdateRelease | None = None
    asset: UpdateAsset | None = None
    message: str = ""


@dataclass(frozen=True)
class InstallPlan:
    kind: str
    package: Path
    command: tuple[str, ...]
    current_appimage: Path | None = None
    current_install_root: Path | None = None


def normalize_architecture(value: str) -> str:
    normalized = str(value).strip().casefold().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    return aliases.get(normalized, normalized)


def current_release_identity(config_get: Callable[[str], str] | None = None) -> ReleaseIdentity:
    if config_get is None:
        import FreeCAD as App

        config_get = App.ConfigGet
    major = int(config_get("BuildVersionMajor"))
    minor = int(config_get("BuildVersionMinor"))
    patch = int(config_get("BuildVersionPoint"))
    suffix = str(config_get("BuildVersionSuffix") or "").strip()
    build_value = str(config_get("BuildVersion") or "0").strip()
    version = f"{major}.{minor}.{patch}{f'-{suffix}' if suffix else ''}"
    return ReleaseIdentity(version, int(build_value))


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _require_url(value: object, name: str, *, allow_empty: bool = False) -> str:
    clean = str(value or "").strip()
    if allow_empty and not clean:
        return ""
    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{name} must be an HTTPS URL without credentials")
    return clean.rstrip("/") + "/"


def update_policy_from_mapping(
    values: Mapping[str, object] | None,
    *,
    managed: bool = False,
) -> UpdatePolicy:
    raw = dict(values or {})
    allowed = {
        "enabled",
        "automatic_checks",
        "channel",
        "check_interval_hours",
        "automatic_download",
        "install_on_exit",
        "metadata_base_url",
        "target_base_url",
        "trusted_root",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown update policy fields: {', '.join(unknown)}")
    channel = str(raw.get("channel", "auto") or "auto").strip().casefold()
    if channel not in _CHANNELS | {"auto"}:
        raise ValueError(f"invalid update channel: {channel!r}")
    interval = raw.get("check_interval_hours", DEFAULT_CHECK_INTERVAL_HOURS)
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise ValueError("check_interval_hours must be an integer")
    if interval < MINIMUM_CHECK_INTERVAL_HOURS:
        raise ValueError(
            f"check_interval_hours must be at least {MINIMUM_CHECK_INTERVAL_HOURS}"
        )
    trusted_root = str(raw.get("trusted_root", "") or "").strip()
    return UpdatePolicy(
        enabled=_require_bool(raw.get("enabled", True), "enabled"),
        automatic_checks=_require_bool(
            raw.get("automatic_checks", True), "automatic_checks"
        ),
        channel=channel,
        check_interval_hours=interval,
        automatic_download=_require_bool(
            raw.get("automatic_download", False), "automatic_download"
        ),
        install_on_exit=_require_bool(
            raw.get("install_on_exit", False), "install_on_exit"
        ),
        metadata_base_url=_require_url(
            raw.get("metadata_base_url", ""),
            "metadata_base_url",
            allow_empty=True,
        ),
        target_base_url=_require_url(
            raw.get("target_base_url", ""),
            "target_base_url",
            allow_empty=True,
        ),
        trusted_root=trusted_root,
        managed=managed,
    )


def default_machine_policy_paths() -> tuple[Path, ...]:
    if os.name == "nt":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return (Path(program_data) / "VibeCAD" / "update-policy.json",)
    return (Path("/etc/vibecad/update-policy.json"),)


def load_update_policy(
    user_values: Mapping[str, object] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    machine_policy_paths: Sequence[Path] | None = None,
) -> PolicyLoadResult:
    environment = environ if environ is not None else os.environ
    explicit = str(environment.get("VIBECAD_UPDATE_POLICY_FILE", "") or "").strip()
    paths = (Path(explicit).expanduser(),) if explicit else tuple(
        machine_policy_paths or default_machine_policy_paths()
    )
    for path in paths:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("policy root must be a JSON object")
            return PolicyLoadResult(
                update_policy_from_mapping(payload, managed=True),
                str(path),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A present managed policy fails closed so a malformed enterprise
            # control cannot silently revert to consumer defaults.
            return PolicyLoadResult(
                UpdatePolicy(enabled=False, automatic_checks=False, managed=True),
                str(path),
                str(exc),
            )
    if explicit:
        return PolicyLoadResult(
            UpdatePolicy(enabled=False, automatic_checks=False, managed=True),
            explicit,
            "The configured managed update policy file does not exist.",
        )
    try:
        return PolicyLoadResult(update_policy_from_mapping(user_values), "user")
    except ValueError as exc:
        return PolicyLoadResult(
            UpdatePolicy(enabled=False, automatic_checks=False),
            "user",
            str(exc),
        )


def _manifest_asset(raw: object, identity: ReleaseIdentity) -> UpdateAsset:
    if not isinstance(raw, dict):
        raise ValueError("each update asset must be an object")
    required = {"platform", "architecture", "kind", "name", "url", "size", "sha256"}
    if set(raw) != required:
        raise ValueError("update asset fields do not match schema 1")
    asset_platform = str(raw["platform"] or "").casefold()
    if asset_platform not in {"windows", "linux", "macos"}:
        raise ValueError(f"unsupported update platform: {asset_platform!r}")
    architecture = normalize_architecture(str(raw["architecture"] or ""))
    kind = str(raw["kind"] or "").casefold()
    name = str(raw["name"] or "")
    if not name or Path(name).name != name:
        raise ValueError(f"unsafe update asset name: {name!r}")
    size = raw["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"invalid update asset size for {name!r}")
    digest = str(raw["sha256"] or "").casefold()
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise ValueError(f"invalid SHA-256 for {name!r}")
    url = _require_url(raw["url"], f"asset URL for {name}", allow_empty=False).rstrip("/")
    parsed = urllib.parse.urlparse(url)
    expected_prefix = f"/{UPDATE_REPOSITORY}/releases/download/{identity.tag}/"
    if parsed.hostname != "github.com" or not urllib.parse.unquote(parsed.path).startswith(expected_prefix):
        raise ValueError(f"asset URL is outside the canonical VibeCAD release: {url}")
    if urllib.parse.unquote(parsed.path).rsplit("/", 1)[-1] != name:
        raise ValueError(f"asset URL and name differ for {name!r}")
    return UpdateAsset(
        platform=asset_platform,
        architecture=architecture,
        kind=kind,
        name=name,
        url=url,
        size=size,
        sha256=digest,
    )


def parse_update_manifest(payload: bytes | str | Mapping[str, object]) -> UpdateRelease:
    if isinstance(payload, bytes):
        raw = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        raw = json.loads(payload)
    else:
        raw = dict(payload)
    required = {
        "schema",
        "product",
        "channel",
        "version",
        "build",
        "release_tag",
        "release_url",
        "published_at",
        "assets",
    }
    if set(raw) != required:
        raise ValueError("update manifest fields do not match schema 1")
    if raw["schema"] != UPDATE_SCHEMA or raw["product"] != "VibeCAD":
        raise ValueError("unsupported VibeCAD update manifest")
    identity = ReleaseIdentity(str(raw["version"]), raw["build"])  # type: ignore[arg-type]
    channel = str(raw["channel"] or "").casefold()
    if channel not in _CHANNELS or channel != identity.channel:
        raise ValueError("update channel does not match the release version")
    release_tag = str(raw["release_tag"] or "")
    if release_tag != identity.tag:
        raise ValueError("update release tag does not match version/build identity")
    expected_release_url = f"https://github.com/{UPDATE_REPOSITORY}/releases/tag/{release_tag}"
    release_url = str(raw["release_url"] or "")
    if release_url != expected_release_url:
        raise ValueError("update release URL is not canonical")
    published_at = str(raw["published_at"] or "")
    try:
        published_time = dt.datetime.fromisoformat(
            published_at.removesuffix("Z") + "+00:00"
        )
    except ValueError as exc:
        raise ValueError("update published_at must be a UTC timestamp") from exc
    if (
        not published_at.endswith("Z")
        or "T" not in published_at
        or published_time.utcoffset() != dt.timedelta(0)
    ):
        raise ValueError("update published_at must be a UTC timestamp")
    assets_raw = raw["assets"]
    if not isinstance(assets_raw, list) or not assets_raw:
        raise ValueError("update manifest contains no assets")
    assets = tuple(_manifest_asset(item, identity) for item in assets_raw)
    identities = {(item.platform, item.architecture, item.kind) for item in assets}
    if len(identities) != len(assets):
        raise ValueError("update manifest contains duplicate asset identities")
    return UpdateRelease(
        identity=identity,
        channel=channel,
        release_tag=release_tag,
        release_url=release_url,
        published_at=published_at,
        assets=assets,
    )


def default_metadata_base_url(channel: str) -> str:
    if channel not in _CHANNELS:
        raise ValueError(f"invalid update channel: {channel!r}")
    raise UpdateTrustError("VibeCAD does not configure a default TUF metadata service.")


def default_target_base_url(channel: str) -> str:
    if channel not in _CHANNELS:
        raise ValueError(f"invalid update channel: {channel!r}")
    raise UpdateTrustError("VibeCAD does not configure a default TUF target service.")


def packaged_trusted_root(channel: str) -> Path:
    if channel not in _CHANNELS:
        raise ValueError(f"invalid update channel: {channel!r}")
    return Path(__file__).resolve().with_name("update-trust") / "root.json"


def _release_identity_from_tag(tag: object) -> ReleaseIdentity | None:
    value = str(tag or "").strip()
    if not value.startswith("v") or "-build" not in value:
        return None
    version, separator, build_text = value[1:].rpartition("-build")
    if not separator or not build_text.isdigit():
        return None
    try:
        identity = ReleaseIdentity(version, int(build_text))
    except ValueError:
        return None
    return identity if identity.tag == value else None


def _canonical_release_asset_url(identity: ReleaseIdentity, name: str) -> str:
    quoted_tag = urllib.parse.quote(identity.tag, safe="")
    quoted_name = urllib.parse.quote(name, safe="")
    return (
        f"https://github.com/{UPDATE_REPOSITORY}/releases/download/"
        f"{quoted_tag}/{quoted_name}"
    )


def _read_https_url(url: str, *, maximum: int, timeout: float = 30.0) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise UpdateTrustError("The update service returned an unsafe URL.")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "VibeCAD-Updater",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(maximum + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"The update service could not be reached: {exc}") from exc
    if len(data) > maximum:
        raise UpdateTrustError("The update service returned an oversized response.")
    return data


def _github_assets_by_name(release: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise UpdateTrustError("The GitHub release has no asset list.")
    assets: dict[str, Mapping[str, object]] = {}
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise UpdateTrustError("The GitHub release contains an invalid asset record.")
        name = str(raw_asset.get("name") or "")
        if not name or Path(name).name != name or name in assets:
            raise UpdateTrustError("The GitHub release contains an unsafe or duplicate asset name.")
        assets[name] = raw_asset
    return assets


def _github_asset_url(
    raw_asset: Mapping[str, object], identity: ReleaseIdentity, name: str
) -> str:
    url = str(raw_asset.get("browser_download_url") or "")
    if url != _canonical_release_asset_url(identity, name):
        raise UpdateTrustError(f"GitHub returned a noncanonical URL for {name}.")
    size = raw_asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise UpdateTrustError(f"GitHub returned an invalid size for {name}.")
    return url


def default_update_directory() -> Path:
    try:
        import FreeCAD as App

        base = Path(str(App.getUserAppDataDir())).expanduser()
    except Exception:
        base = Path.home() / ".vibecad"
    return base / "updates"


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class UpdateService:
    def __init__(
        self,
        current: ReleaseIdentity,
        policy: UpdatePolicy,
        *,
        update_directory: Path | None = None,
        system: str | None = None,
        machine: str | None = None,
        clock: Callable[[], float] = time.time,
        fetcher: Any | None = None,
    ) -> None:
        self.current = current
        self.policy = policy
        self.update_directory = (update_directory or default_update_directory()).resolve()
        self.system = system or platform.system()
        self.machine = machine or platform.machine()
        self.clock = clock
        self.fetcher = fetcher
        self.state_path = self.update_directory / "state.json"

    @property
    def channel(self) -> str:
        return self.policy.resolved_channel(self.current)

    def _state(self) -> dict[str, object]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def check_due(self) -> bool:
        state = self._state()
        try:
            last_attempt = float(state.get("last_check_attempt", 0.0))
        except (TypeError, ValueError):
            return True
        interval = self.policy.check_interval_hours * 60 * 60
        return self.clock() - last_attempt >= interval

    def _record_check(self, *, success: bool, message: str = "") -> None:
        state = self._state()
        state["last_check_attempt"] = self.clock()
        if success:
            state["last_check_success"] = self.clock()
        state["last_check_message"] = message
        _atomic_json_write(self.state_path, state)

    def _trusted_root(self) -> Path:
        path = (
            Path(self.policy.trusted_root).expanduser()
            if self.policy.trusted_root
            else packaged_trusted_root(self.channel)
        )
        if not path.is_file():
            raise UpdateTrustError(
                f"No trusted {self.channel} update root is installed. "
                "VibeCAD will not query untrusted update metadata."
            )
        return path

    def _verified_tuf_manifest(self) -> UpdateRelease:
        try:
            from tuf.ngclient import Updater as TUFUpdater
        except ImportError as exc:
            raise UpdateTrustError("The bundled TUF verifier is unavailable.") from exc

        class VibeCADTUFUpdater(TUFUpdater):
            def _update_root_symlink(self) -> None:
                if os.name != "nt":
                    super()._update_root_symlink()
                    return
                # python-tuf 7 stores root history behind a symlink. Creating a
                # symlink on Windows requires a privilege normal desktop users
                # do not have, so preserve the same bytes with an atomic copy.
                # The dependency is pinned while this private compatibility
                # override is needed.
                version = self._trusted_set.root.version
                source = Path(self._dir) / "root_history" / f"{version}.root.json"
                destination = Path(self._dir) / "root.json"
                handle, temporary_name = tempfile.mkstemp(
                    prefix=".root.json.", dir=destination.parent
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(source.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)

        channel_dir = self.update_directory / self.channel
        metadata_dir = channel_dir / "metadata"
        target_dir = channel_dir / "targets"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        metadata_url = self.policy.metadata_base_url
        target_url = self.policy.target_base_url
        target_path = f"channels/{self.channel}.json"
        try:
            updater = VibeCADTUFUpdater(
                str(metadata_dir),
                metadata_url,
                str(target_dir),
                target_url,
                fetcher=self.fetcher,
                bootstrap=self._trusted_root().read_bytes(),
            )
            updater.refresh()
            target_info = updater.get_targetinfo(target_path)
            if target_info is None:
                raise UpdateTrustError(
                    f"The signed {self.channel} channel has no release manifest."
                )
            manifest_path = updater.download_target(target_info)
            return parse_update_manifest(Path(manifest_path).read_bytes())
        except UpdateTrustError:
            raise
        except Exception as exc:
            raise UpdateTrustError(f"Signed update verification failed: {exc}") from exc

    def _github_release_manifest(self) -> UpdateRelease:
        try:
            raw_releases = json.loads(
                _read_https_url(
                    GITHUB_RELEASES_API_URL,
                    maximum=_MAX_RELEASE_INDEX_BYTES,
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateTrustError("GitHub returned an invalid release index.") from exc
        if not isinstance(raw_releases, list):
            raise UpdateTrustError("GitHub returned an invalid release index.")

        candidates: list[tuple[ReleaseIdentity, Mapping[str, object]]] = []
        expected_prerelease = self.channel == "preview"
        for raw_release in raw_releases:
            if not isinstance(raw_release, dict) or raw_release.get("draft") is not False:
                continue
            identity = _release_identity_from_tag(raw_release.get("tag_name"))
            if identity is None or identity.channel != self.channel:
                continue
            if raw_release.get("prerelease") is not expected_prerelease:
                raise UpdateTrustError(
                    f"GitHub release {identity.tag} has the wrong channel classification."
                )
            expected_release_url = (
                f"https://github.com/{UPDATE_REPOSITORY}/releases/tag/{identity.tag}"
            )
            if raw_release.get("html_url") != expected_release_url:
                raise UpdateTrustError(
                    f"GitHub release {identity.tag} has a noncanonical release URL."
                )
            candidates.append((identity, raw_release))

        if not candidates:
            raise UpdateError(f"No {self.channel} VibeCAD release has been published yet.")

        identity, raw_release = max(
            candidates,
            key=lambda candidate: candidate[0].precedence_key(),
        )
        assets = _github_assets_by_name(raw_release)
        manifest_name = f"VibeCAD-update-{identity.version}-build{identity.build}.json"
        checksum_name = f"{manifest_name}-SHA256.txt"
        try:
            manifest_asset = assets[manifest_name]
            checksum_asset = assets[checksum_name]
        except KeyError as exc:
            raise UpdateTrustError(
                f"GitHub release {identity.tag} is missing its update manifest or checksum."
            ) from exc

        manifest_url = _github_asset_url(manifest_asset, identity, manifest_name)
        checksum_url = _github_asset_url(checksum_asset, identity, checksum_name)
        manifest_bytes = _read_https_url(
            manifest_url,
            maximum=_MAX_MANIFEST_BYTES,
        )
        checksum_bytes = _read_https_url(
            checksum_url,
            maximum=_MAX_CHECKSUM_BYTES,
        )
        if len(manifest_bytes) != manifest_asset["size"]:
            raise UpdateTrustError("The update manifest size differs from the GitHub release.")
        if len(checksum_bytes) != checksum_asset["size"]:
            raise UpdateTrustError("The manifest checksum size differs from the GitHub release.")
        try:
            checksum_fields = checksum_bytes.decode("ascii").strip().split()
        except UnicodeDecodeError as exc:
            raise UpdateTrustError("The update manifest checksum is invalid.") from exc
        if len(checksum_fields) != 2:
            raise UpdateTrustError("The update manifest checksum is invalid.")
        expected_digest = checksum_fields[0].casefold()
        checksum_target = checksum_fields[1].removeprefix("*")
        if (
            _SHA256_PATTERN.fullmatch(expected_digest) is None
            or checksum_target != manifest_name
            or hashlib.sha256(manifest_bytes).hexdigest() != expected_digest
        ):
            raise UpdateTrustError("The update manifest checksum does not match.")

        try:
            release = parse_update_manifest(manifest_bytes)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateTrustError(f"The GitHub update manifest is invalid: {exc}") from exc
        if release.identity != identity or release.channel != self.channel:
            raise UpdateTrustError(
                "The GitHub release identity differs from its update manifest."
            )
        for asset in release.assets:
            raw_asset = assets.get(asset.name)
            if raw_asset is None:
                raise UpdateTrustError(
                    f"The update manifest references missing GitHub asset {asset.name}."
                )
            github_url = _github_asset_url(raw_asset, identity, asset.name)
            if raw_asset["size"] != asset.size or github_url != asset.url:
                raise UpdateTrustError(
                    f"GitHub asset {asset.name} differs from the update manifest."
                )
        return release

    def _verified_manifest(self) -> UpdateRelease:
        tuf_configured = bool(
            self.policy.metadata_base_url
            or self.policy.target_base_url
            or self.policy.trusted_root
        )
        if not tuf_configured:
            return self._github_release_manifest()
        if not self.policy.metadata_base_url or not self.policy.target_base_url:
            raise UpdateTrustError(
                "Custom TUF updates require both metadata_base_url and target_base_url."
            )
        return self._verified_tuf_manifest()

    def check_for_updates(self, *, force: bool = False) -> UpdateCheckResult:
        if not self.policy.enabled:
            return UpdateCheckResult("disabled", self.current, message="Updates are disabled by policy.")
        if not force and not self.check_due():
            return UpdateCheckResult("not-due", self.current, message="The next automatic check is not due.")
        try:
            release = self._verified_manifest()
            if release.channel != self.channel:
                raise UpdateTrustError("The verified manifest belongs to a different channel.")
            if not release.identity.is_newer_than(self.current):
                result = UpdateCheckResult(
                    "current",
                    self.current,
                    release=release,
                    message=f"VibeCAD {self.current.display} is current.",
                )
            else:
                asset = release.asset_for(self.system, self.machine)
                if asset is None:
                    result = UpdateCheckResult(
                        "unsupported",
                        self.current,
                        release=release,
                        message="An update exists, but no automatic package matches this installation.",
                    )
                else:
                    result = UpdateCheckResult(
                        "available",
                        self.current,
                        release=release,
                        asset=asset,
                        message=f"VibeCAD {release.display} is available.",
                    )
            self._record_check(success=True, message=result.message)
            return result
        except UpdateError as exc:
            self._record_check(success=False, message=str(exc))
            return UpdateCheckResult("error", self.current, message=str(exc))

    def download_asset(
        self,
        asset: UpdateAsset,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        timeout: float = 60.0,
    ) -> Path:
        downloads = self.update_directory / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        destination = downloads / asset.name
        partial = downloads / f"{asset.name}.part"
        partial_state = downloads / f"{asset.name}.part.json"
        if destination.is_file() and _file_matches(destination, asset):
            try:
                _prepare_verified_asset(destination, asset)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination
        destination.unlink(missing_ok=True)

        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > asset.size:
            partial.unlink(missing_ok=True)
            offset = 0
        etag = ""
        try:
            raw_state = json.loads(partial_state.read_text(encoding="utf-8"))
            if raw_state.get("url") == asset.url and raw_state.get("sha256") == asset.sha256:
                etag = str(raw_state.get("etag") or "")
            else:
                partial.unlink(missing_ok=True)
                offset = 0
        except (OSError, json.JSONDecodeError, AttributeError):
            if offset:
                partial.unlink(missing_ok=True)
                offset = 0

        if partial.is_file() and _file_matches(partial, asset):
            os.replace(partial, destination)
            partial_state.unlink(missing_ok=True)
            try:
                _prepare_verified_asset(destination, asset)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination

        request = urllib.request.Request(asset.url, headers={"User-Agent": "VibeCAD-Updater/1"})
        if offset:
            request.add_header("Range", f"bytes={offset}-")
            if etag:
                request.add_header("If-Range", etag)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except (OSError, urllib.error.URLError) as exc:
            raise UpdateError(f"Update download could not start: {exc}") from exc
        with response:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme != "https":
                raise UpdateError("Update download redirected to a non-HTTPS URL.")
            status = int(getattr(response, "status", response.getcode()))
            append = bool(offset and status == 206)
            if not append:
                offset = 0
            response_etag = str(response.headers.get("ETag") or "")
            _atomic_json_write(
                partial_state,
                {"url": asset.url, "sha256": asset.sha256, "etag": response_etag},
            )
            mode = "ab" if append else "wb"
            downloaded = offset
            with partial.open(mode) as stream:
                while True:
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled("Update download was cancelled.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > asset.size:
                        raise UpdateError("Update download exceeded its authorized size.")
                    if progress is not None:
                        progress(downloaded, asset.size)
                stream.flush()
                os.fsync(stream.fileno())
        if not _file_matches(partial, asset):
            raise UpdateError("Downloaded update failed its authorized size or SHA-256 check.")
        os.replace(partial, destination)
        partial_state.unlink(missing_ok=True)
        try:
            _prepare_verified_asset(destination, asset)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination


def _file_matches(path: Path, asset: UpdateAsset) -> bool:
    if not path.is_file() or path.stat().st_size != asset.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == asset.sha256


def _prepare_verified_asset(path: Path, asset: UpdateAsset) -> None:
    """Apply platform preparation after authorized size and SHA-256 verification."""

    if asset.platform == "linux" and asset.kind == "appimage":
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except OSError as exc:
            raise UpdateError(f"The downloaded AppImage is not executable: {exc}") from exc


def verify_windows_authenticode(path: Path) -> None:
    """Verify Authenticode when explicitly requested by an external caller.

    VibeCAD's updater authorizes packages through a verified release manifest
    (or explicitly configured TUF metadata) plus exact size and SHA-256.
    Authenticode is optional defense in depth and is not part of the default
    download gate.
    """

    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    action = GUID(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = WINTRUST_FILE_INFO(
        ctypes.sizeof(WINTRUST_FILE_INFO), str(path), None, None
    )
    trust_data = WINTRUST_DATA()
    trust_data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    trust_data.dwUIChoice = 2  # WTD_UI_NONE
    trust_data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
    trust_data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    trust_data.pFile = ctypes.pointer(file_info)
    trust_data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
    trust_data.dwProvFlags = 0x00000080  # WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
    win_verify_trust = ctypes.windll.wintrust.WinVerifyTrust
    win_verify_trust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    win_verify_trust.restype = wintypes.LONG
    result = int(win_verify_trust(None, ctypes.byref(action), ctypes.byref(trust_data)))
    trust_data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
    win_verify_trust(None, ctypes.byref(action), ctypes.byref(trust_data))
    if result != 0:
        raise UpdateTrustError(
            f"Windows rejected the update's Authenticode signature (0x{result & 0xFFFFFFFF:08X})."
        )


def create_install_plan(
    package: Path,
    asset: UpdateAsset,
    *,
    environ: Mapping[str, str] | None = None,
    install_root: Path | None = None,
) -> InstallPlan:
    package = package.resolve(strict=True)
    if asset.platform == "windows" and asset.kind == "installer":
        if package.suffix.casefold() != ".exe":
            raise UpdateError("The Windows update package is not an installer executable.")
        if install_root is None:
            try:
                import FreeCAD as App

                install_root = Path(str(App.getHomePath())).resolve(strict=True)
            except Exception as exc:
                raise UpdateError("VibeCAD's Windows install directory is unavailable.") from exc
        return InstallPlan(
            "windows-installer",
            package,
            (
                str(package),
                "/S",
                "/VIBECADUPDATE",
                f"/VIBECADINSTALLROOT={install_root.resolve(strict=True)}",
            ),
            current_install_root=install_root.resolve(strict=True),
        )
    if asset.platform == "linux" and asset.kind == "appimage":
        environment = environ if environ is not None else os.environ
        appimage_value = str(environment.get("APPIMAGE", "") or "").strip()
        if not appimage_value:
            raise UpdateError("This VibeCAD process was not launched from an AppImage.")
        current = Path(appimage_value).expanduser().resolve(strict=True)
        if not os.access(current.parent, os.W_OK):
            raise UpdateError(f"The AppImage directory is not writable: {current.parent}")
        return InstallPlan("appimage", package, (), current_appimage=current)
    raise UpdateError("This package type cannot be installed automatically.")


def record_pending_install(
    plan: InstallPlan,
    current: ReleaseIdentity,
    target: ReleaseIdentity,
    *,
    update_directory: Path | None = None,
) -> Path:
    """Persist the exact install transition before the application exits."""

    root = (update_directory or default_update_directory()).resolve()
    package = plan.package.resolve(strict=True)
    downloads = (root / "downloads").resolve()
    if downloads not in package.parents:
        raise UpdateError("The staged package is outside the update cache.")
    payload: dict[str, object] = {
        "schema": 1,
        "status": "pending",
        "kind": plan.kind,
        "current_version": current.version,
        "current_build": current.build,
        "target_version": target.version,
        "target_build": target.build,
        "package": str(package),
        "created_at": int(time.time()),
    }
    if plan.kind == "appimage":
        if plan.current_appimage is None:
            raise UpdateError("The AppImage install plan has no current executable.")
        appimage = plan.current_appimage.resolve(strict=True)
        backup = appimage.with_name(
            f"{appimage.name}.rollback-{current.version}-build{current.build}"
        )
        payload["current_appimage"] = str(appimage)
        payload["backup"] = str(backup)
    elif plan.kind == "windows-installer":
        if plan.current_install_root is None:
            raise UpdateError("The Windows install plan has no current install directory.")
        install_root = plan.current_install_root.resolve(strict=True)
        payload["current_install_root"] = str(install_root)
        payload["backup"] = f"{install_root}.vibecad-rollback"
    pending = root / "pending-install.json"
    (root / "health-receipt.json").unlink(missing_ok=True)
    _atomic_json_write(pending, payload)
    return pending


def complete_pending_install_health(
    current: ReleaseIdentity,
    *,
    update_directory: Path | None = None,
) -> str:
    """Commit a health receipt after the updated GUI survives startup.

    AppImage rollback files are retained until this function observes the exact
    target version/build.  A restored original version records ``rolled-back``.
    Unrelated versions leave the receipt pending for diagnosis.
    """

    root = (update_directory or default_update_directory()).resolve()
    pending = root / "pending-install.json"
    try:
        payload = json.loads(pending.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise ValueError("invalid pending install receipt")
        original = ReleaseIdentity(
            str(payload["current_version"]), int(payload["current_build"])
        )
        target = ReleaseIdentity(
            str(payload["target_version"]), int(payload["target_build"])
        )
    except FileNotFoundError:
        return "none"
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not validate the pending install receipt: {exc}") from exc

    if current == target:
        status = "healthy"
    elif current == original:
        status = "rolled-back"
    else:
        return "pending"

    backup_value = str(payload.get("backup") or "")
    if status == "healthy" and backup_value:
        backup = Path(backup_value).resolve()
        if payload.get("kind") == "appimage":
            appimage_value = str(payload.get("current_appimage") or "")
            if not appimage_value:
                raise UpdateError("Pending AppImage receipt has no installed executable.")
            appimage = Path(appimage_value).resolve()
            expected_prefix = f"{appimage.name}.rollback-"
            if backup.parent != appimage.parent or not backup.name.startswith(expected_prefix):
                raise UpdateError("Refusing to remove an unexpected rollback path.")
            backup.unlink(missing_ok=True)
        elif payload.get("kind") == "windows-installer":
            install_value = str(payload.get("current_install_root") or "")
            if not install_value:
                raise UpdateError("Pending Windows receipt has no install directory.")
            install_root = Path(install_value).resolve()
            expected_backup = Path(f"{install_root}.vibecad-rollback").resolve()
            if backup != expected_backup:
                raise UpdateError("Unexpected Windows rollback path in the health receipt.")
            # Retain one last-known-good Windows tree. The next elevated
            # installer removes it before creating a fresh rollback snapshot.

    package_value = str(payload.get("package") or "")
    if package_value:
        package = Path(package_value).resolve()
        downloads = (root / "downloads").resolve()
        if downloads not in package.parents:
            raise UpdateError("Pending install package is outside the update cache.")
        package.unlink(missing_ok=True)

    receipt = {
        "schema": 1,
        "status": status,
        "version": current.version,
        "build": current.build,
        "completed_at": int(time.time()),
    }
    _atomic_json_write(root / "health-receipt.json", receipt)
    pending.unlink(missing_ok=True)
    (root / "install-receipt.json").unlink(missing_ok=True)
    return status


def remove_downloaded_package(path: Path) -> None:
    """Delete one updater-owned package without accepting arbitrary paths."""

    resolved = path.resolve(strict=True)
    downloads = (default_update_directory() / "downloads").resolve()
    if downloads not in resolved.parents:
        raise UpdateError("Refusing to remove a package outside the update cache.")
    resolved.unlink()
