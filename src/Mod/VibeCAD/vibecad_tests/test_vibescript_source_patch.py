# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic contextual patch behavior for persisted VibeScript source."""

from __future__ import annotations

import pytest

from VibeCADVibeScriptPatch import SourcePatchError, apply_source_patch


def test_applies_one_codex_style_update_without_complete_source() -> None:
    result = apply_source_patch(
        "def main():\n    width = 10\n    return width\n",
        """*** Begin Patch
*** Update File: source.py
@@
-    width = 10
+    width = 12
*** End Patch""",
    )

    assert result["source"] == "def main():\n    width = 12\n    return width\n"
    assert result["summary"] == {
        "hunk_count": 1,
        "added_lines": 1,
        "removed_lines": 1,
        "first_changed_line": 2,
        "last_changed_line": 2,
    }


def test_applies_multiple_non_overlapping_hunks_atomically() -> None:
    result = apply_source_patch(
        "alpha = 1\nbeta = 2\ngamma = 3\ndelta = 4\n",
        """*** Begin Patch
*** Update File: source.py
@@
-alpha = 1
+alpha = 10
 beta = 2
@@
 gamma = 3
-delta = 4
+delta = 40
*** End Patch""",
    )

    assert result["source"] == ("alpha = 10\nbeta = 2\ngamma = 3\ndelta = 40\n")
    assert result["summary"]["hunk_count"] == 2


def test_rejects_missing_context_without_returning_a_partial_result() -> None:
    source = "alpha = 1\nbeta = 2\ngamma = 3\n"
    patch = """@@
-alpha = 1
+alpha = 10
@@
-missing = 4
+missing = 40"""

    with pytest.raises(SourcePatchError) as caught:
        apply_source_patch(source, patch)

    assert caught.value.code == "PATCH_CONTEXT_NOT_FOUND"
    assert source == "alpha = 1\nbeta = 2\ngamma = 3\n"


def test_rejects_ambiguous_context_instead_of_guessing() -> None:
    with pytest.raises(SourcePatchError) as caught:
        apply_source_patch(
            "value = 1\nvalue = 1\n",
            """@@
-value = 1
+value = 2""",
        )

    assert caught.value.code == "PATCH_CONTEXT_AMBIGUOUS"
    assert caught.value.details["match_count"] == 2


def test_rejects_overlapping_hunks() -> None:
    with pytest.raises(SourcePatchError) as caught:
        apply_source_patch(
            "alpha\nbeta\ngamma\n",
            """@@
 alpha
-beta
+BETA
 gamma
@@
-gamma
+GAMMA""",
        )

    assert caught.value.code == "PATCH_HUNKS_OVERLAP"


def test_preserves_crlf_unicode_and_indentation_and_supports_deletion() -> None:
    result = apply_source_patch(
        "def main():\r\n    label = 'café'\r\n    obsolete = True\r\n    return label\r\n",
        """@@ def main():
     label = 'café'
-    obsolete = True
     return label""",
    )

    assert result["source"] == (
        "def main():\r\n    label = 'café'\r\n    return label\r\n"
    )
    assert result["summary"]["added_lines"] == 0
    assert result["summary"]["removed_lines"] == 1


def test_preserves_unchanged_mixed_line_endings() -> None:
    result = apply_source_patch(
        "first = 1\r\nsecond = 2\nthird = 3\r",
        """@@
-second = 2
+second = 20""",
    )

    assert result["source"] == "first = 1\r\nsecond = 20\nthird = 3\r"


def test_end_of_file_anchor_supports_an_insertion_only_hunk() -> None:
    result = apply_source_patch(
        "result = main()\n",
        """@@
+assert result is not None
*** End of File""",
    )

    assert result["source"] == "result = main()\nassert result is not None\n"


def test_unanchored_insertion_only_hunk_appends_like_codex_apply_patch() -> None:
    result = apply_source_patch(
        "first = 1\nsecond = 2\n",
        """@@
+third = 3""",
    )

    assert result["source"] == "first = 1\nsecond = 2\nthird = 3\n"


def test_appending_to_an_unterminated_source_preserves_its_eof_style() -> None:
    result = apply_source_patch(
        "result = main()",
        """@@
 result = main()
+assert result is not None
*** End of File""",
    )

    assert result["source"] == "result = main()\nassert result is not None"


def test_accepts_codex_first_chunk_without_an_explicit_at_header() -> None:
    result = apply_source_patch(
        "import api\nresult = api.box(1, 2, 3)\n",
        """*** Begin Patch
*** Update File: source.py
 import api
-result = api.box(1, 2, 3)
+result = api.box(4, 5, 6)
*** End Patch""",
    )

    assert result["source"] == "import api\nresult = api.box(4, 5, 6)\n"


def test_accepts_codex_blank_line_before_end_patch_marker() -> None:
    result = apply_source_patch(
        "result = 1\n",
        """*** Begin Patch
*** Update File: source.py
@@
-result = 1
+result = 2

*** End Patch""",
    )

    assert result["source"] == "result = 2\n"


@pytest.mark.parametrize(
    "patch",
    [
        "*** Begin Patch\n*** Add File: source.py\n+new\n*** End Patch",
        "*** Begin Patch\n*** Delete File: source.py\n*** End Patch",
        "*** Begin Patch\n*** Update File: one.py\n@@\n-a\n+b\n*** Update File: two.py\n@@\n-c\n+d\n*** End Patch",
    ],
)
def test_rejects_non_atomic_or_multi_file_envelopes(patch: str) -> None:
    with pytest.raises(SourcePatchError) as caught:
        apply_source_patch("a\nc\n", patch)

    assert caught.value.code == "PATCH_FORMAT_INVALID"
