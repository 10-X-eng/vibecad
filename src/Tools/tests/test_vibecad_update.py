# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
VIBECAD_MODULE_DIR = REPO_ROOT / "src" / "Mod" / "VibeCAD"
sys.path.insert(0, str(VIBECAD_MODULE_DIR))

from VibeCADUpdate import (  # noqa: E402
    GITHUB_RELEASES_API_URL,
    InstallPlan,
    ReleaseIdentity,
    UpdateAsset,
    UpdateError,
    UpdatePolicy,
    UpdateRelease,
    UpdateService,
    UpdateTrustError,
    current_release_identity,
    default_download_directory,
    complete_pending_install_health,
    create_install_plan,
    load_update_policy,
    macos_install_helper_command,
    normalize_architecture,
    parse_update_manifest,
    record_pending_install,
    spawn_detached_install_helper,
    update_policy_from_mapping,
    write_macos_install_helper,
)


def _manifest(*, version: str = "26.3.1-RC3", build: int = 1) -> dict[str, object]:
    tag = f"v{version}-build{build}"
    basename = f"VibeCAD-{version}-build{build}"
    return {
        "schema": 1,
        "product": "VibeCAD",
        "channel": "preview" if "-" in version else "stable",
        "version": version,
        "build": build,
        "release_tag": tag,
        "release_url": f"https://github.com/10-X-eng/vibecad/releases/tag/{tag}",
        "published_at": "2026-08-06T12:00:00Z",
        "assets": [
            {
                "platform": "windows",
                "architecture": "x86_64",
                "kind": "installer",
                "name": f"{basename}-Windows-x86_64-installer.exe",
                "url": (
                    "https://github.com/10-X-eng/vibecad/releases/download/"
                    f"{tag}/{basename}-Windows-x86_64-installer.exe"
                ),
                "size": 10,
                "sha256": "a" * 64,
            },
            {
                "platform": "linux",
                "architecture": "x86_64",
                "kind": "appimage",
                "name": f"{basename}-Linux-x86_64.AppImage",
                "url": (
                    "https://github.com/10-X-eng/vibecad/releases/download/"
                    f"{tag}/{basename}-Linux-x86_64.AppImage"
                ),
                "size": 20,
                "sha256": "b" * 64,
            },
            {
                "platform": "macos",
                "architecture": "aarch64",
                "kind": "dmg",
                "name": f"{basename}-macOS12-arm64.dmg",
                "url": (
                    "https://github.com/10-X-eng/vibecad/releases/download/"
                    f"{tag}/{basename}-macOS12-arm64.dmg"
                ),
                "size": 30,
                "sha256": "c" * 64,
            },
        ],
    }


class _BytesResponse:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: bytes, url: str) -> None:
        self._stream = io.BytesIO(payload)
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status


def _github_release_fixture(
    manifest: dict[str, object],
) -> tuple[dict[str, object], dict[str, bytes]]:
    identity = ReleaseIdentity(str(manifest["version"]), int(manifest["build"]))
    manifest_name = (
        f"VibeCAD-update-{identity.version}-build{identity.build}.json"
    )
    checksum_name = f"{manifest_name}-SHA256.txt"
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    checksum_bytes = (
        f"{hashlib.sha256(manifest_bytes).hexdigest()}  {manifest_name}\n".encode(
            "ascii"
        )
    )
    release_assets = [
        {
            "name": asset["name"],
            "browser_download_url": asset["url"],
            "size": asset["size"],
        }
        for asset in manifest["assets"]
    ]
    manifest_url = (
        f"https://github.com/10-X-eng/vibecad/releases/download/"
        f"{identity.tag}/{manifest_name}"
    )
    checksum_url = f"{manifest_url}-SHA256.txt"
    release_assets.extend(
        [
            {
                "name": manifest_name,
                "browser_download_url": manifest_url,
                "size": len(manifest_bytes),
            },
            {
                "name": checksum_name,
                "browser_download_url": checksum_url,
                "size": len(checksum_bytes),
            },
        ]
    )
    release = {
        "draft": False,
        "prerelease": identity.channel == "preview",
        "tag_name": identity.tag,
        "html_url": f"https://github.com/10-X-eng/vibecad/releases/tag/{identity.tag}",
        "assets": release_assets,
    }
    return release, {
        manifest_url: manifest_bytes,
        checksum_url: checksum_bytes,
    }


class ReleaseIdentityTests(unittest.TestCase):
    def test_build_participates_in_identity_and_precedence(self) -> None:
        old = ReleaseIdentity("26.3.1-RC3", 4)
        new = ReleaseIdentity("26.3.1-RC3", 5)
        self.assertEqual(new.tag, "v26.3.1-RC3-build5")
        self.assertEqual(new.display, "26.3.1-RC3 (Build 5)")
        self.assertTrue(new.is_newer_than(old))

    def test_final_release_follows_release_candidate(self) -> None:
        candidate = ReleaseIdentity("26.3.1-RC99", 12)
        final = ReleaseIdentity("26.3.1", 0)
        self.assertTrue(final.is_newer_than(candidate))

    def test_current_identity_reads_public_build_not_revision(self) -> None:
        values = {
            "BuildVersionMajor": "26",
            "BuildVersionMinor": "3",
            "BuildVersionPoint": "1",
            "BuildVersionSuffix": "RC3",
            "BuildVersion": "7",
            "BuildRevision": "deadbeef",
        }
        identity = current_release_identity(values.__getitem__)
        self.assertEqual(identity, ReleaseIdentity("26.3.1-RC3", 7))

    def test_architecture_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_architecture("AMD64"), "x86_64")
        self.assertEqual(normalize_architecture("arm64"), "aarch64")


class UpdateManifestTests(unittest.TestCase):
    def test_manifest_selects_native_installer(self) -> None:
        release = parse_update_manifest(_manifest())
        asset = release.asset_for("Windows", "AMD64")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "installer")

    def test_manifest_selects_native_appimage(self) -> None:
        release = parse_update_manifest(_manifest())
        asset = release.asset_for("Linux", "x86_64")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.kind, "appimage")

    def test_manifest_selects_native_macos_dmg(self) -> None:
        release = parse_update_manifest(_manifest())
        asset = release.asset_for("Darwin", "arm64")
        self.assertIsNotNone(asset)
        self.assertEqual(asset.platform, "macos")
        self.assertEqual(asset.kind, "dmg")
        self.assertEqual(asset.architecture, "aarch64")

    def test_manifest_rejects_release_tag_mismatch(self) -> None:
        manifest = _manifest()
        manifest["release_tag"] = "v26.3.1-RC3-build2"
        with self.assertRaisesRegex(ValueError, "tag"):
            parse_update_manifest(manifest)

    def test_manifest_rejects_asset_outside_canonical_release(self) -> None:
        manifest = _manifest()
        manifest["assets"][0]["url"] = "https://example.com/update.exe"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "canonical"):
            parse_update_manifest(manifest)

    def test_manifest_schema_is_strict(self) -> None:
        manifest = _manifest()
        manifest["source_sha"] = "ugly-and-not-part-of-update-identity"
        with self.assertRaisesRegex(ValueError, "schema"):
            parse_update_manifest(manifest)

    def test_manifest_rejects_invalid_utc_timestamp(self) -> None:
        manifest = _manifest()
        manifest["published_at"] = "2026-99-99T25:61:00Z"
        with self.assertRaisesRegex(ValueError, "UTC timestamp"):
            parse_update_manifest(manifest)


class UpdatePolicyTests(unittest.TestCase):
    def test_user_policy_is_validated(self) -> None:
        policy = update_policy_from_mapping(
            {"channel": "stable", "check_interval_hours": 8}
        )
        self.assertEqual(policy.channel, "stable")
        self.assertEqual(policy.check_interval_hours, 8)
        self.assertFalse(policy.managed)

    def test_present_managed_policy_overrides_user_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "automatic_checks": False,
                        "channel": "stable",
                    }
                ),
                encoding="utf-8",
            )
            result = load_update_policy(
                {"channel": "preview"}, machine_policy_paths=(path,)
            )
        self.assertTrue(result.policy.managed)
        self.assertFalse(result.policy.automatic_checks)
        self.assertEqual(result.policy.channel, "stable")

    def test_malformed_managed_policy_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.json"
            path.write_text("not-json", encoding="utf-8")
            result = load_update_policy(machine_policy_paths=(path,))
        self.assertTrue(result.policy.managed)
        self.assertFalse(result.policy.enabled)
        self.assertTrue(result.error)


class _FakeUpdateService(UpdateService):
    def __init__(self, release: UpdateRelease, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.release = release

    def _verified_manifest(self) -> UpdateRelease:
        return self.release


class UpdateServiceTests(unittest.TestCase):
    @staticmethod
    def _windows_asset(payload: bytes) -> UpdateAsset:
        name = "VibeCAD-26.3.1-RC3-build2-Windows-x86_64-installer.exe"
        return UpdateAsset(
            "windows",
            "x86_64",
            "installer",
            name,
            "https://github.com/10-X-eng/vibecad/releases/download/"
            f"v26.3.1-RC3-build2/{name}",
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )

    def test_cached_windows_installer_uses_manifest_size_and_hash_without_authenticode(
        self,
    ) -> None:
        payload = b"unsigned installer fixture"
        asset = self._windows_asset(payload)
        with tempfile.TemporaryDirectory() as temp_dir:
            update_dir = Path(temp_dir)
            downloads = update_dir / "downloads"
            downloads.mkdir()
            cached = downloads / asset.name
            cached.write_bytes(payload)
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=update_dir,
            )
            with mock.patch("VibeCADUpdate.verify_windows_authenticode") as verify:
                result = service.download_asset(asset)
        self.assertEqual(result, cached)
        verify.assert_not_called()

    def test_default_download_directory_is_user_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            with (
                mock.patch("VibeCADUpdate.os.name", "posix"),
                mock.patch("VibeCADUpdate.Path.home", return_value=home),
            ):
                downloads = default_download_directory()
        self.assertEqual(downloads, (home / "Downloads").resolve())

    def test_default_service_downloads_packages_to_user_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloads = Path(temp_dir) / "Downloads"
            with mock.patch(
                "VibeCADUpdate.default_download_directory",
                return_value=downloads,
            ):
                service = UpdateService(
                    ReleaseIdentity("26.3.1-RC3", 1),
                    UpdatePolicy(),
                )
        self.assertEqual(service.download_directory, downloads.resolve())

    def test_tampered_cached_windows_installer_is_not_reused(self) -> None:
        asset = self._windows_asset(b"authorized installer fixture")
        with tempfile.TemporaryDirectory() as temp_dir:
            update_dir = Path(temp_dir)
            downloads = update_dir / "downloads"
            downloads.mkdir()
            cached = downloads / asset.name
            cached.write_bytes(b"tampered installer fixture")
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=update_dir,
            )
            with mock.patch(
                "urllib.request.urlopen", side_effect=OSError("offline fixture")
            ):
                with self.assertRaisesRegex(UpdateError, "could not start"):
                    service.download_asset(asset)
            exists = cached.exists()
        self.assertFalse(exists)

    def test_github_release_discovery_uses_version_build_manifest_and_checksum(
        self,
    ) -> None:
        release, responses = _github_release_fixture(_manifest(build=2))
        responses[GITHUB_RELEASES_API_URL] = json.dumps([release]).encode("utf-8")

        def open_request(request, **_kwargs):
            url = request.full_url
            return _BytesResponse(responses[url], url)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            with mock.patch("urllib.request.urlopen", side_effect=open_request):
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "available", result.message)
        self.assertEqual(result.release.identity, ReleaseIdentity("26.3.1-RC3", 2))
        self.assertEqual(result.asset.kind, "installer")

    def test_github_release_discovery_selects_highest_canonical_identity(self) -> None:
        older_release, older_responses = _github_release_fixture(_manifest(build=2))
        newer_release, newer_responses = _github_release_fixture(_manifest(build=3))
        legacy_release = {
            "draft": False,
            "prerelease": True,
            "tag_name": "v26.3.1-RC3",
            "html_url": "https://github.com/10-X-eng/vibecad/releases/tag/v26.3.1-RC3",
            "assets": [],
        }
        responses = {**older_responses, **newer_responses}
        responses[GITHUB_RELEASES_API_URL] = json.dumps(
            [older_release, legacy_release, newer_release]
        ).encode("utf-8")

        def open_request(request, **_kwargs):
            url = request.full_url
            return _BytesResponse(responses[url], url)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            with mock.patch("urllib.request.urlopen", side_effect=open_request):
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "available", result.message)
        self.assertEqual(result.release.identity, ReleaseIdentity("26.3.1-RC3", 3))

    def test_github_release_discovery_rejects_wrong_channel_classification(
        self,
    ) -> None:
        release, responses = _github_release_fixture(_manifest(build=2))
        release["prerelease"] = False
        responses[GITHUB_RELEASES_API_URL] = json.dumps([release]).encode("utf-8")

        def open_request(request, **_kwargs):
            url = request.full_url
            return _BytesResponse(responses[url], url)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
            )
            with mock.patch("urllib.request.urlopen", side_effect=open_request):
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "error")
        self.assertIn("wrong channel classification", result.message)

    def test_github_release_discovery_rejects_tampered_manifest_checksum(self) -> None:
        release, responses = _github_release_fixture(_manifest(build=2))
        checksum_url = next(url for url in responses if url.endswith("-SHA256.txt"))
        original = responses[checksum_url]
        responses[checksum_url] = b"0" * 64 + original[64:]
        for asset in release["assets"]:
            if asset["name"].endswith("-SHA256.txt"):
                asset["size"] = len(responses[checksum_url])
        responses[GITHUB_RELEASES_API_URL] = json.dumps([release]).encode("utf-8")

        def open_request(request, **_kwargs):
            url = request.full_url
            return _BytesResponse(responses[url], url)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            with mock.patch("urllib.request.urlopen", side_effect=open_request):
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "error")
        self.assertIn("checksum", result.message)

    def test_github_release_discovery_rejects_asset_size_disagreement(self) -> None:
        release, responses = _github_release_fixture(_manifest(build=2))
        release["assets"][0]["size"] = 999
        responses[GITHUB_RELEASES_API_URL] = json.dumps([release]).encode("utf-8")

        def open_request(request, **_kwargs):
            url = request.full_url
            return _BytesResponse(responses[url], url)

        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            with mock.patch("urllib.request.urlopen", side_effect=open_request):
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "error")
        self.assertIn("differs", result.message)

    def test_partial_custom_tuf_configuration_fails_without_github_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(metadata_base_url="https://updates.example.test/metadata/"),
                update_directory=Path(temp_dir),
            )
            with mock.patch("urllib.request.urlopen") as urlopen:
                result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "error")
        self.assertIn("both metadata_base_url and target_base_url", result.message)
        urlopen.assert_not_called()

    def test_download_resumes_from_valid_partial_state(self) -> None:
        payload = b"complete resumable payload"
        name = "VibeCAD-26.3.1-RC3-build2-Linux-x86_64.AppImage"
        url = (
            "https://github.com/10-X-eng/vibecad/releases/download/"
            f"v26.3.1-RC3-build2/{name}"
        )
        asset = UpdateAsset(
            "linux",
            "x86_64",
            "appimage",
            name,
            url,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )

        class Response:
            status = 206
            headers = {"ETag": '"fixture-v1"'}

            def __init__(self, data: bytes) -> None:
                self.stream = io.BytesIO(data)

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self, size: int = -1) -> bytes:
                return self.stream.read(size)

            def geturl(self) -> str:
                return url

            def getcode(self) -> int:
                return self.status

        with tempfile.TemporaryDirectory() as temp_dir:
            update_dir = Path(temp_dir)
            downloads = update_dir / "downloads"
            downloads.mkdir()
            partial = downloads / f"{name}.part"
            state = downloads / f"{name}.part.json"
            offset = 9
            partial.write_bytes(payload[:offset])
            state.write_text(
                json.dumps({"url": url, "sha256": asset.sha256, "etag": '"fixture-v1"'}),
                encoding="utf-8",
            )
            seen_headers: dict[str, str] = {}

            def open_request(request, **_kwargs):
                seen_headers.update(request.headers)
                return Response(payload[offset:])

            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=update_dir,
            )
            with (
                mock.patch("urllib.request.urlopen", side_effect=open_request),
                mock.patch.object(Path, "chmod") as chmod,
            ):
                result = service.download_asset(asset)
            downloaded = result.read_bytes()

        self.assertEqual(downloaded, payload)
        self.assertEqual(seen_headers.get("Range"), f"bytes={offset}-")
        self.assertEqual(seen_headers.get("If-range"), '"fixture-v1"')
        chmod.assert_called_once()

    def test_tuf_client_accepts_signed_manifest_and_rejects_unsigned_source(self) -> None:
        try:
            from securesystemslib.signer import CryptoSigner
            from tuf.api import exceptions
            from tuf.api.metadata import (
                MetaFile,
                Metadata,
                Root,
                Snapshot,
                TargetFile,
                Targets,
                Timestamp,
            )
            from tuf.ngclient.fetcher import FetcherInterface
        except ImportError:
            self.skipTest("python-tuf is not installed in this test environment")

        manifest_bytes = json.dumps(_manifest(build=2)).encode("utf-8")
        expires = datetime.now(timezone.utc) + timedelta(days=30)
        signers = {
            role: CryptoSigner.generate_ed25519()
            for role in ("root", "targets", "snapshot", "timestamp")
        }
        root = Metadata(Root(expires=expires, consistent_snapshot=False))
        for role, signer in signers.items():
            root.signed.add_key(signer.public_key, role)
        root.sign(signers["root"])
        targets = Metadata(
            Targets(
                expires=expires,
                targets={
                    "channels/preview.json": TargetFile.from_data(
                        "channels/preview.json", manifest_bytes
                    )
                },
            )
        )
        targets.sign(signers["targets"])
        targets_bytes = targets.to_bytes()
        snapshot = Metadata(
            Snapshot(
                expires=expires,
                meta={
                    "targets.json": MetaFile.from_data(
                        1, targets_bytes, ["sha256"]
                    )
                },
            )
        )
        snapshot.sign(signers["snapshot"])
        snapshot_bytes = snapshot.to_bytes()
        timestamp = Metadata(
            Timestamp(
                expires=expires,
                snapshot_meta=MetaFile.from_data(
                    1, snapshot_bytes, ["sha256"]
                ),
            )
        )
        timestamp.sign(signers["timestamp"])

        class MemoryFetcher(FetcherInterface):
            def __init__(self, content: dict[str, bytes]) -> None:
                self.content = content

            def _fetch(self, url: str):
                data = self.content.get(url)
                if data is None:
                    raise exceptions.DownloadHTTPError("not found", 404)
                yield data

        metadata_base = "https://updates.example.test/metadata/"
        target_base = "https://updates.example.test/targets/"
        fetcher = MemoryFetcher(
            {
                f"{metadata_base}timestamp.json": timestamp.to_bytes(),
                f"{metadata_base}snapshot.json": snapshot_bytes,
                f"{metadata_base}targets.json": targets_bytes,
                f"{target_base}channels/preview.json": manifest_bytes,
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir) / "root.json"
            root_path.write_bytes(root.to_bytes())
            service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(
                    metadata_base_url=metadata_base,
                    target_base_url=target_base,
                    trusted_root=str(root_path),
                ),
                update_directory=Path(temp_dir) / "client",
                system="Windows",
                machine="AMD64",
                fetcher=fetcher,
            )
            result = service.check_for_updates(force=True)
            fetcher.content[f"{target_base}channels/preview.json"] = b"tampered"
            tampered_service = UpdateService(
                ReleaseIdentity("26.3.1-RC3", 1),
                service.policy,
                update_directory=Path(temp_dir) / "tampered-client",
                system="Windows",
                machine="AMD64",
                fetcher=fetcher,
            )
            tampered_result = tampered_service.check_for_updates(force=True)
        self.assertEqual(result.status, "available", result.message)
        self.assertEqual(result.release.identity, ReleaseIdentity("26.3.1-RC3", 2))
        self.assertEqual(tampered_result.status, "error")

    def test_available_update_uses_version_and_build(self) -> None:
        release = parse_update_manifest(_manifest(build=2))
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _FakeUpdateService(
                release,
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.release.identity.build, 2)

    def test_same_version_and_build_is_current(self) -> None:
        release = parse_update_manifest(_manifest(build=2))
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _FakeUpdateService(
                release,
                ReleaseIdentity("26.3.1-RC3", 2),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Windows",
                machine="AMD64",
            )
            result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "current")

    def test_macos_check_selects_the_matching_dmg(self) -> None:
        release = parse_update_manifest(_manifest(build=2))
        with tempfile.TemporaryDirectory() as temp_dir:
            service = _FakeUpdateService(
                release,
                ReleaseIdentity("26.3.1-RC3", 1),
                UpdatePolicy(),
                update_directory=Path(temp_dir),
                system="Darwin",
                machine="arm64",
            )
            result = service.check_for_updates(force=True)
        self.assertEqual(result.status, "available", result.message)
        self.assertIsNotNone(result.asset)
        self.assertEqual(result.asset.kind, "dmg")
        self.assertEqual(result.asset.platform, "macos")

    def test_macos_plan_requires_an_application_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "VibeCAD.dmg"
            package.write_bytes(b"dmg")
            asset = UpdateAsset(
                "macos",
                "aarch64",
                "dmg",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                "v26.3.1-RC3-build2/VibeCAD.dmg",
                3,
                hashlib.sha256(b"dmg").hexdigest(),
            )
            with self.assertRaisesRegex(Exception, "application bundle"):
                create_install_plan(
                    package,
                    asset,
                    install_root=Path(temp_dir),
                )

    def test_macos_plan_uses_the_application_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "VibeCAD.dmg"
            package.write_bytes(b"dmg")
            app = Path(temp_dir) / "VibeCAD.app"
            app.mkdir()
            asset = UpdateAsset(
                "macos",
                "aarch64",
                "dmg",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                "v26.3.1-RC3-build2/VibeCAD.dmg",
                3,
                hashlib.sha256(b"dmg").hexdigest(),
            )
            plan = create_install_plan(package, asset, install_root=app)
            self.assertEqual(plan.kind, "macos-dmg")
            self.assertEqual(plan.current_install_root, app.resolve())

    def test_macos_health_receipt_commits_and_removes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            package = downloads / "VibeCAD.dmg"
            package.write_bytes(b"dmg")
            app = root / "VibeCAD.app"
            app.mkdir()
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            asset = UpdateAsset(
                "macos",
                "aarch64",
                "dmg",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                f"{target.tag}/{package.name}",
                3,
                hashlib.sha256(b"dmg").hexdigest(),
            )
            plan = create_install_plan(package, asset, install_root=app)
            record_pending_install(
                plan,
                original,
                target,
                update_directory=root,
            )
            backup = Path(f"{app}.vibecad-rollback")
            backup.mkdir()
            (backup / "Contents").mkdir()
            status = complete_pending_install_health(
                target,
                update_directory=root,
            )
            receipt = json.loads((root / "health-receipt.json").read_text())
            backup_exists = backup.exists()
            package_exists = package.exists()
        self.assertEqual(status, "healthy")
        self.assertEqual(receipt["status"], "healthy")
        self.assertFalse(backup_exists)
        self.assertFalse(package_exists)

    def test_macos_health_commits_before_removing_the_rollback_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            package = downloads / "VibeCAD.dmg"
            package.write_bytes(b"dmg")
            app = root / "VibeCAD.app"
            app.mkdir()
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            asset = UpdateAsset(
                "macos",
                "aarch64",
                "dmg",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                f"{target.tag}/{package.name}",
                3,
                hashlib.sha256(b"dmg").hexdigest(),
            )
            plan = create_install_plan(package, asset, install_root=app)
            record_pending_install(
                plan,
                original,
                target,
                update_directory=root,
            )
            backup = Path(f"{app}.vibecad-rollback")
            backup.mkdir()
            real_rmtree = shutil.rmtree

            def assert_committed_before_cleanup(path) -> None:
                self.assertFalse((root / "pending-install.json").exists())
                self.assertTrue((root / "health-receipt.json").is_file())
                real_rmtree(path)

            with mock.patch(
                "VibeCADUpdate.shutil.rmtree",
                side_effect=assert_committed_before_cleanup,
            ):
                status = complete_pending_install_health(
                    target,
                    update_directory=root,
                )

        self.assertEqual(status, "healthy")

    def test_updater_gui_launches_a_macos_dmg_helper(self) -> None:
        gui = (
            REPO_ROOT / "src" / "Mod" / "VibeCAD" / "VibeCADUpdateGui.py"
        ).read_text(encoding="utf-8")
        helper = (
            REPO_ROOT / "src" / "Mod" / "VibeCAD" / "VibeCADUpdate.py"
        ).read_text(encoding="utf-8")
        launch = gui.split("def _launch_macos_install_helper", 1)[1].split(
            "class CheckForUpdatesCommand", 1
        )[0]
        restart = gui.split("def _install_and_restart", 1)[1].split(
            "def _show_update_notification", 1
        )[0]
        self.assertIn("macos-dmg", gui.split("def _launch_pending_install", 1)[1])
        self.assertIn("write_macos_install_helper", launch)
        self.assertIn("macos_install_helper_command", launch)
        self.assertIn("launch_pending_install_now()", restart)
        self.assertIn("_prepare_to_quit_for_update()", restart)
        self.assertIn("_exit_process_for_update()", restart)
        self.assertLess(
            restart.index("_prepare_to_quit_for_update()"),
            restart.index("launch_pending_install_now()"),
        )
        self.assertLess(
            restart.index("launch_pending_install_now()"),
            restart.index("_exit_process_for_update()"),
        )
        self.assertIn("closeAllDocuments", gui)
        self.assertIn("os._exit(0)", gui)
        self.assertIn("spawn_detached_install_helper", gui)
        self.assertIn("macos_install_helper_started_path", launch)
        self.assertIn("wait_for_install_helper_start", launch)
        self.assertIn("hdiutil attach", helper)
        self.assertIn("install-helper.started", helper)
        self.assertLess(
            helper.index('printf \'%s\\n\' "$$" > "$started"'),
            helper.index('while kill -0 "$pid"'),
        )
        self.assertIn("ditto", helper)
        self.assertIn('new_app="$mount/VibeCAD.app"', helper)
        self.assertIn("bundle_executable", helper)
        self.assertIn("keeping live updated application", helper)
        self.assertIn("terminate_new_app", helper)
        self.assertIn("rollback_install", helper)
        self.assertIn('index($0, prefix)', helper)
        rollback = helper.split("rollback_install()", 1)[1].split("find_new_pid()", 1)[0]
        self.assertLess(
            rollback.index("terminate_new_app"),
            rollback.index('rm -rf "$app"'),
        )
        self.assertIn("QtCore.QTimer.singleShot(0, _complete_startup_health_check)", gui)
        self.assertIn("QtCore.QTimer.singleShot(2000, _complete_startup_health_check)", gui)
        self.assertNotIn(
            "QtCore.QTimer.singleShot(30000, _complete_startup_health_check)",
            gui,
        )

    def test_macos_install_helper_command_points_at_the_bundle_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = root / "install-macos-update.sh"
            write_macos_install_helper(helper)
            package = root / "VibeCAD.dmg"
            package.write_bytes(b"dmg")
            app = root / "VibeCAD.app"
            app.mkdir()
            plan = InstallPlan(
                "macos-dmg",
                package,
                (),
                current_install_root=app,
            )
            command = macos_install_helper_command(
                helper,
                plan,
                process_id=4242,
                update_directory=root,
            )
        self.assertEqual(command[0], "/bin/sh")
        self.assertEqual(command[1], str(helper))
        self.assertEqual(command[2], "4242")
        self.assertEqual(command[3], str(package))
        self.assertEqual(command[4], str(app))
        self.assertEqual(command[5], f"{app}.vibecad-rollback")
        self.assertEqual(Path(command[6]).resolve(), (root / "install-receipt.json").resolve())
        self.assertEqual(Path(command[7]).resolve(), (root / "pending-install.json").resolve())

    def test_macos_helper_stamps_started_before_waiting_for_the_app(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Requires the macOS install-helper runtime")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            helper = write_macos_install_helper(root / "install-macos-update.sh")
            receipt = root / "install-receipt.json"
            pending = root / "pending-install.json"
            pending.write_text('{"schema":1,"status":"pending"}\n', encoding="utf-8")
            waiter = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
            )
            helper_proc = subprocess.Popen(
                [
                    "/bin/sh",
                    str(helper),
                    str(waiter.pid),
                    str(root / "missing.dmg"),
                    str(root / "VibeCAD.app"),
                    str(root / "VibeCAD.app.vibecad-rollback"),
                    str(receipt),
                    str(pending),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stamp = root / "install-helper.started"
            log_path = root / "install-helper.log"
            waiter_pid = waiter.pid
            try:
                deadline = time.time() + 5
                log = ""
                while time.time() < deadline:
                    if stamp.is_file() and log_path.is_file():
                        log = log_path.read_text(encoding="utf-8")
                        if "helper started" in log:
                            break
                    time.sleep(0.05)
                started = stamp.is_file()
                log = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
            finally:
                helper_proc.kill()
                waiter.kill()
                helper_proc.wait(timeout=5)
                waiter.wait(timeout=5)
        self.assertTrue(started, f"log={log}")
        self.assertIn("helper started", log)
        self.assertIn(f"waiting for {waiter_pid}", log)

    def test_spawn_detached_helper_outlives_the_parent_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            marker = root / "alive"
            log = root / "helper.log"
            helper_command = [
                sys.executable,
                "-c",
                (
                    "import time\n"
                    "from pathlib import Path\n"
                    "time.sleep(0.4)\n"
                    f"Path({str(marker)!r}).write_text('ready', encoding='utf-8')\n"
                ),
            ]
            launcher = (
                "from pathlib import Path\n"
                "from VibeCADUpdate import spawn_detached_install_helper\n"
                "spawn_detached_install_helper(\n"
                f"    {helper_command!r},\n"
                f"    log_path=Path({str(log)!r}),\n"
                ")\n"
            )
            completed = subprocess.run(
                [sys.executable, "-c", launcher],
                check=False,
                cwd=str(VIBECAD_MODULE_DIR),
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": str(VIBECAD_MODULE_DIR)},
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )
            deadline = time.time() + 5
            while time.time() < deadline and not marker.is_file():
                time.sleep(0.05)
            self.assertTrue(
                marker.is_file(),
                f"stderr={completed.stderr}\nlog={log.read_text(encoding='utf-8') if log.is_file() else ''}",
            )
            if os.name == "nt" and log.is_file():
                # The marker is written just before the detached interpreter
                # exits.  Prove that its inherited log handle has closed before
                # TemporaryDirectory removes the workspace; otherwise Windows
                # can report a false cleanup failure after the behavior under
                # test has already succeeded.
                released_log = log.with_name("helper.released.log")
                release_deadline = time.time() + 5
                while time.time() < release_deadline:
                    try:
                        log.replace(released_log)
                        released_log.replace(log)
                        break
                    except PermissionError:
                        time.sleep(0.05)
                else:
                    self.fail(
                        "detached helper kept its log handle open after "
                        "writing the completion marker"
                    )

    def test_macos_helper_replaces_the_app_and_keeps_a_live_update(self) -> None:
        if sys.platform != "darwin" or shutil.which("hdiutil") is None:
            self.skipTest("Requires macOS hdiutil")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            new_app = payload / "VibeCAD.app"
            macos = new_app / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            stub = macos / "FreeCAD"
            stub.write_text(
                "#!/bin/sh\n"
                'if [ -n "${VIBECAD_UPDATE_MARKER:-}" ]; then\n'
                '  printf "started\\n" > "$VIBECAD_UPDATE_MARKER"\n'
                "fi\n"
                "sleep 20\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            dmg = root / "VibeCAD.dmg"
            create = subprocess.run(
                [
                    "hdiutil",
                    "create",
                    "-srcfolder",
                    str(payload),
                    "-volname",
                    "VibeCAD",
                    "-format",
                    "UDZO",
                    "-ov",
                    str(dmg),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if create.returncode != 0:
                self.skipTest(f"hdiutil create failed: {create.stderr}")
            dest = root / "Applications" / "VibeCAD.app"
            dest.mkdir(parents=True)
            (dest / "old.txt").write_text("previous", encoding="utf-8")
            helper = write_macos_install_helper(root / "install-macos-update.sh")
            receipt = root / "install-receipt.json"
            pending = root / "pending-install.json"
            pending.write_text('{"schema":1,"status":"pending"}\n', encoding="utf-8")
            marker = root / "started"
            dead = subprocess.run(
                ["python3", "-c", "import os; print(os.getpid())"],
                check=True,
                capture_output=True,
                text=True,
            )
            dead_pid = dead.stdout.strip()
            env = os.environ.copy()
            env["VIBECAD_UPDATE_HEALTH_WAIT"] = "3"
            env["VIBECAD_UPDATE_MARKER"] = str(marker)
            completed = subprocess.run(
                [
                    "/bin/sh",
                    str(helper),
                    dead_pid,
                    str(dmg),
                    str(dest),
                    f"{dest}.vibecad-rollback",
                    str(receipt),
                    str(pending),
                ],
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            receipt_payload = (
                json.loads(receipt.read_text(encoding="utf-8"))
                if receipt.is_file()
                else {}
            )
            started = marker.read_text(encoding="utf-8") if marker.is_file() else ""
            has_old = (dest / "old.txt").exists()
            has_new = (dest / "Contents" / "MacOS" / "FreeCAD").is_file()
            log = (root / "install-helper.log").read_text(encoding="utf-8") if (
                root / "install-helper.log"
            ).is_file() else ""
            listing = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                check=False,
                capture_output=True,
                text=True,
            )
            for line in listing.stdout.splitlines():
                if f"{dest}/" in line:
                    try:
                        os.kill(int(line.split(None, 1)[0]), 9)
                    except (OSError, ValueError):
                        pass
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout={completed.stdout}\nstderr={completed.stderr}\nlog={log}",
        )
        self.assertEqual(receipt_payload.get("status"), "installed")
        self.assertEqual(started.strip(), "started")
        self.assertFalse(has_old)
        self.assertTrue(has_new)

    def test_macos_helper_rolls_back_when_the_updated_app_never_stays_up(self) -> None:
        if sys.platform != "darwin" or shutil.which("hdiutil") is None:
            self.skipTest("Requires macOS hdiutil")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            new_app = payload / "VibeCAD.app"
            macos = new_app / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            stub = macos / "FreeCAD"
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
            dmg = root / "VibeCAD.dmg"
            create = subprocess.run(
                [
                    "hdiutil",
                    "create",
                    "-srcfolder",
                    str(payload),
                    "-volname",
                    "VibeCAD",
                    "-format",
                    "UDZO",
                    "-ov",
                    str(dmg),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if create.returncode != 0:
                self.skipTest(f"hdiutil create failed: {create.stderr}")
            dest = root / "Applications" / "VibeCAD.app"
            dest.mkdir(parents=True)
            (dest / "old.txt").write_text("previous", encoding="utf-8")
            helper = write_macos_install_helper(root / "install-macos-update.sh")
            receipt = root / "install-receipt.json"
            pending = root / "pending-install.json"
            pending.write_text('{"schema":1,"status":"pending"}\n', encoding="utf-8")
            env = os.environ.copy()
            env["VIBECAD_UPDATE_HEALTH_WAIT"] = "2"
            completed = subprocess.run(
                [
                    "/bin/sh",
                    str(helper),
                    str(os.getpid() + 10_000_000),
                    str(dmg),
                    str(dest),
                    f"{dest}.vibecad-rollback",
                    str(receipt),
                    str(pending),
                ],
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            restored = (
                (dest / "old.txt").read_text(encoding="utf-8")
                if (dest / "old.txt").is_file()
                else ""
            )
            receipt_payload = (
                json.loads(receipt.read_text(encoding="utf-8"))
                if receipt.is_file()
                else {}
            )
        self.assertEqual(completed.returncode, 25)
        self.assertEqual(restored, "previous")
        self.assertEqual(receipt_payload.get("status"), "rolled-back")

    def test_appimage_plan_requires_real_appimage_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "new.AppImage"
            package.write_bytes(b"new")
            asset = UpdateAsset(
                "linux",
                "x86_64",
                "appimage",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/v1.0.0-build0/new.AppImage",
                3,
                hashlib.sha256(b"new").hexdigest(),
            )
            with self.assertRaisesRegex(Exception, "not launched"):
                create_install_plan(package, asset, environ={})

    def test_health_receipt_commits_appimage_update_and_removes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            package = downloads / "new.AppImage"
            package.write_bytes(b"new")
            current_appimage = root / "VibeCAD.AppImage"
            current_appimage.write_bytes(b"current")
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            asset = UpdateAsset(
                "linux",
                "x86_64",
                "appimage",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                f"{target.tag}/{package.name}",
                3,
                hashlib.sha256(b"new").hexdigest(),
            )
            plan = create_install_plan(
                package,
                asset,
                environ={"APPIMAGE": str(current_appimage)},
            )
            record_pending_install(
                plan,
                original,
                target,
                update_directory=root,
            )
            backup = current_appimage.with_name(
                f"{current_appimage.name}.rollback-{original.version}-build{original.build}"
            )
            backup.write_bytes(b"old")
            status = complete_pending_install_health(
                target,
                update_directory=root,
            )
            receipt = json.loads((root / "health-receipt.json").read_text())
            backup_exists = backup.exists()
            package_exists = package.exists()
        self.assertEqual(status, "healthy")
        self.assertEqual(receipt["status"], "healthy")
        self.assertFalse(backup_exists)
        self.assertFalse(package_exists)

    def test_health_receipt_records_rollback_when_original_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            package = downloads / "new.AppImage"
            package.write_bytes(b"new")
            current_appimage = root / "VibeCAD.AppImage"
            current_appimage.write_bytes(b"current")
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            plan = create_install_plan(
                package,
                UpdateAsset(
                    "linux",
                    "x86_64",
                    "appimage",
                    package.name,
                    "https://github.com/10-X-eng/vibecad/releases/download/"
                    f"{target.tag}/{package.name}",
                    3,
                    hashlib.sha256(b"new").hexdigest(),
                ),
                environ={"APPIMAGE": str(current_appimage)},
            )
            record_pending_install(
                plan,
                original,
                target,
                update_directory=root,
            )
            status = complete_pending_install_health(
                original,
                update_directory=root,
            )
        self.assertEqual(status, "rolled-back")

    def test_windows_health_receipt_retains_last_known_good_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            package = downloads / "VibeCAD-installer.exe"
            package.write_bytes(b"installer")
            install_root = root / "VibeCAD 26.3"
            install_root.mkdir()
            backup = Path(f"{install_root}.vibecad-rollback")
            backup.mkdir()
            (backup / "old.dll").write_bytes(b"old")
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            asset = UpdateAsset(
                "windows",
                "x86_64",
                "installer",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                f"{target.tag}/{package.name}",
                package.stat().st_size,
                hashlib.sha256(package.read_bytes()).hexdigest(),
            )
            plan = create_install_plan(
                package,
                asset,
                install_root=install_root,
            )
            self.assertEqual(plan.command, (str(package.resolve()),))
            record_pending_install(
                plan,
                original,
                target,
                update_directory=root,
            )
            status = complete_pending_install_health(
                target,
                update_directory=root,
            )
            backup_exists = backup.exists()
        self.assertEqual(status, "healthy")
        self.assertTrue(backup_exists)

    def test_health_receipt_keeps_installer_in_user_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "state"
            downloads = Path(temp_dir) / "Downloads"
            root.mkdir()
            downloads.mkdir()
            package = downloads / "VibeCAD-installer.exe"
            package.write_bytes(b"installer")
            install_root = Path(temp_dir) / "VibeCAD 26.3"
            install_root.mkdir()
            original = ReleaseIdentity("26.3.1-RC3", 1)
            target = ReleaseIdentity("26.3.1-RC3", 2)
            asset = UpdateAsset(
                "windows",
                "x86_64",
                "installer",
                package.name,
                "https://github.com/10-X-eng/vibecad/releases/download/"
                f"{target.tag}/{package.name}",
                package.stat().st_size,
                hashlib.sha256(package.read_bytes()).hexdigest(),
            )
            with mock.patch(
                "VibeCADUpdate.default_download_directory",
                return_value=downloads,
            ):
                plan = create_install_plan(package, asset, install_root=install_root)
                record_pending_install(
                    plan,
                    original,
                    target,
                    update_directory=root,
                )
                status = complete_pending_install_health(
                    target,
                    update_directory=root,
                )
            package_exists = package.exists()
        self.assertEqual(status, "healthy")
        self.assertTrue(package_exists)


if __name__ == "__main__":
    unittest.main()
