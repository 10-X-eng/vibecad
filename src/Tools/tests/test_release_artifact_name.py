# SPDX-License-Identifier: LGPL-2.1-or-later

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from resolve_release_artifact_name import (  # noqa: E402
    normalize_source_sha,
    resolve_artifact_basename,
    resolve_release_version,
)


class TestReleaseArtifactName(unittest.TestCase):
    def _repo_root(self, *, suffix: str = "RC1") -> tempfile.TemporaryDirectory:
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name)
        (root / "version.json").write_text(
            json.dumps(
                {
                    "name": "FreeCAD",
                    "version_major": 26,
                    "version_minor": 3,
                    "version_patch": 2,
                    "version_suffix": suffix,
                    "build_version": 0,
                }
            ),
            encoding="utf-8",
        )
        return temporary_directory

    def test_release_version_uses_complete_version_json_value(self) -> None:
        with self._repo_root() as directory:
            self.assertEqual(resolve_release_version(Path(directory)), "26.3.2-RC1")

    def test_release_version_without_suffix_has_no_trailing_separator(self) -> None:
        with self._repo_root(suffix="") as directory:
            self.assertEqual(resolve_release_version(Path(directory)), "26.3.2")

    def test_basename_combines_brand_short_sha_and_release_version(self) -> None:
        with self._repo_root() as directory:
            self.assertEqual(
                resolve_artifact_basename(
                    Path(directory),
                    source_sha="ABCDEF1234567890ABCDEF1234567890ABCDEF12",
                ),
                "VibeCAD-abcdef123456-26.3.2-RC1",
            )

    def test_source_sha_must_be_a_git_object_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid Git source SHA"):
            normalize_source_sha("not-a-sha")

    def test_release_version_rejects_path_characters(self) -> None:
        with self._repo_root(suffix="../../escape") as directory:
            with self.assertRaisesRegex(ValueError, "unsafe release version"):
                resolve_release_version(Path(directory))


if __name__ == "__main__":
    unittest.main()
