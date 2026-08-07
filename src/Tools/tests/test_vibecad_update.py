# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
VIBECAD_MODULE_DIR = REPO_ROOT / "src" / "Mod" / "VibeCAD"
sys.path.insert(0, str(VIBECAD_MODULE_DIR))

from VibeCADUpdate import (  # noqa: E402
    GITHUB_RELEASES_API_URL,
    ReleaseIdentity,
    UpdateAsset,
    UpdateError,
    UpdatePolicy,
    UpdateRelease,
    UpdateService,
    UpdateTrustError,
    current_release_identity,
    complete_pending_install_health,
    create_install_plan,
    load_update_policy,
    normalize_architecture,
    parse_update_manifest,
    record_pending_install,
    update_policy_from_mapping,
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
            self.assertEqual(plan.command[1:3], ("/S", "/VIBECADUPDATE"))
            self.assertEqual(
                plan.command[3],
                f"/VIBECADINSTALLROOT={install_root.resolve()}",
            )
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


if __name__ == "__main__":
    unittest.main()
