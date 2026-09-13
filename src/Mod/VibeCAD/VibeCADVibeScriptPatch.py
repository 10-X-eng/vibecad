# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Codex-style contextual patches for VibeScript source text."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

MAX_PATCH_BYTES = 256_000


class SourcePatchError(ValueError):
    """One contextual patch could not be applied without guessing."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = dict(details or {})


@dataclass(frozen=True)
class _Hunk:
    number: int
    locator: str
    operations: tuple[tuple[str, str], ...]
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    added_lines: int
    removed_lines: int
    end_of_file: bool


@dataclass(frozen=True)
class _ResolvedHunk:
    hunk: _Hunk
    start: int
    end: int


def _fail(
    code: str,
    message: str,
    *,
    hunk: int | None = None,
    **details: Any,
) -> None:
    if hunk is not None:
        details = {"hunk": hunk, **details}
    raise SourcePatchError(code, message, details=details)


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _source_lines(source: str) -> tuple[list[str], list[str], str]:
    lines: list[str] = []
    endings: list[str] = []
    for match in re.finditer(r"([^\r\n]*)(\r\n|\r|\n|$)", source):
        text, ending = match.groups()
        if not text and not ending:
            break
        lines.append(text)
        endings.append(ending)
    default_newline = next((ending for ending in endings if ending), "\n")
    return lines, endings, default_newline


def _unwrap_patch(patch: str) -> list[str]:
    if not isinstance(patch, str):
        _fail("PATCH_FORMAT_INVALID", "patch must be a string.")
    if not patch.strip():
        _fail("PATCH_FORMAT_INVALID", "patch must contain at least one update hunk.")
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        _fail(
            "PATCH_FORMAT_INVALID",
            f"patch exceeds the {MAX_PATCH_BYTES}-byte limit.",
        )
    if "\x00" in patch:
        _fail("PATCH_FORMAT_INVALID", "patch cannot contain a NUL byte.")

    lines = _normalize_newlines(patch).split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        _fail("PATCH_FORMAT_INVALID", "patch must contain at least one update hunk.")
    if lines[0] != "*** Begin Patch":
        if any(
            line.startswith(
                (
                    "*** Begin Patch",
                    "*** End Patch",
                    "*** Update File:",
                    "*** Add File:",
                    "*** Delete File:",
                    "*** Move to:",
                )
            )
            for line in lines
        ):
            _fail(
                "PATCH_FORMAT_INVALID",
                "A patch envelope must begin with '*** Begin Patch'.",
            )
        return lines
    if lines[-1] != "*** End Patch":
        _fail(
            "PATCH_FORMAT_INVALID",
            "A patch envelope must end with '*** End Patch'.",
        )
    body = lines[1:-1]
    file_markers = [
        (index, line)
        for index, line in enumerate(body)
        if line.startswith(("*** Update File:", "*** Add File:", "*** Delete File:"))
    ]
    if len(file_markers) != 1 or not file_markers[0][1].startswith("*** Update File:"):
        _fail(
            "PATCH_FORMAT_INVALID",
            "A VibeScript patch must contain exactly one Update File operation.",
            operation_count=len(file_markers),
        )
    marker_index, marker = file_markers[0]
    if marker_index != 0 or not marker.removeprefix("*** Update File:").strip():
        _fail(
            "PATCH_FORMAT_INVALID",
            "The Update File operation must name one source and precede its hunks.",
        )
    content = body[1:]
    while content and content[-1] == "":
        content.pop()
    if any(line.startswith("*** Move to:") for line in content):
        _fail(
            "PATCH_FORMAT_INVALID",
            "VibeScript source patches cannot move or rename their program.",
        )
    return content


def _parse_hunks(patch: str) -> list[_Hunk]:
    lines = _unwrap_patch(patch)
    hunks: list[_Hunk] = []
    index = 0
    while index < len(lines):
        header = lines[index]
        has_header = header == "@@" or header.startswith("@@ ")
        if not has_header and (hunks or not header or header[0] not in {" ", "+", "-"}):
            _fail(
                "PATCH_FORMAT_INVALID",
                "Only the first contextual hunk may omit '@@'.",
                line=index + 1,
            )
        locator = header[2:].strip() if has_header else ""
        if has_header:
            index += 1
        old_lines: list[str] = []
        new_lines: list[str] = []
        operations: list[tuple[str, str]] = []
        added = 0
        removed = 0
        end_of_file = False
        while index < len(lines) and not (
            lines[index] == "@@" or lines[index].startswith("@@ ")
        ):
            line = lines[index]
            if line == "*** End of File":
                end_of_file = True
                index += 1
                if index < len(lines) and not (
                    lines[index] == "@@" or lines[index].startswith("@@ ")
                ):
                    _fail(
                        "PATCH_FORMAT_INVALID",
                        "'*** End of File' must be the final line in its hunk.",
                        hunk=len(hunks) + 1,
                    )
                break
            if not line or line[0] not in {" ", "+", "-"}:
                _fail(
                    "PATCH_FORMAT_INVALID",
                    "Hunk lines must begin with a space, '+', or '-'.",
                    hunk=len(hunks) + 1,
                    line=index + 1,
                )
            prefix, value = line[0], line[1:]
            operations.append((prefix, value))
            if prefix != "+":
                old_lines.append(value)
            if prefix != "-":
                new_lines.append(value)
            added += int(prefix == "+")
            removed += int(prefix == "-")
            index += 1
        if added == 0 and removed == 0:
            _fail(
                "PATCH_FORMAT_INVALID",
                "Each hunk must add or remove at least one line.",
                hunk=len(hunks) + 1,
            )
        hunks.append(
            _Hunk(
                number=len(hunks) + 1,
                locator=locator,
                operations=tuple(operations),
                old_lines=tuple(old_lines),
                new_lines=tuple(new_lines),
                added_lines=added,
                removed_lines=removed,
                end_of_file=end_of_file,
            )
        )
    if not hunks:
        _fail("PATCH_FORMAT_INVALID", "patch must contain at least one update hunk.")
    return hunks


def _matches(lines: list[str], needle: tuple[str, ...]) -> list[int]:
    if not needle:
        return list(range(len(lines) + 1))
    width = len(needle)
    return [
        index
        for index in range(0, len(lines) - width + 1)
        if tuple(lines[index : index + width]) == needle
    ]


def _locator_allows(
    lines: list[str],
    candidate: int,
    locator: str,
    lower_bound: int,
) -> bool:
    if not locator:
        return True
    return any(
        line == locator and lower_bound <= index < candidate
        for index, line in enumerate(lines)
    )


def _resolve_hunks(lines: list[str], hunks: list[_Hunk]) -> list[_ResolvedHunk]:
    resolved: list[_ResolvedHunk] = []
    lower_bound = 0
    last_start = -1
    for hunk in hunks:
        if not hunk.old_lines:
            if hunk.locator:
                locator_matches = [
                    index
                    for index, line in enumerate(lines)
                    if line == hunk.locator and index >= lower_bound
                ]
                if not locator_matches:
                    _fail(
                        "PATCH_CONTEXT_NOT_FOUND",
                        "The patch context does not match the current source.",
                        hunk=hunk.number,
                        locator=hunk.locator,
                    )
                if len(locator_matches) != 1:
                    _fail(
                        "PATCH_CONTEXT_AMBIGUOUS",
                        "The patch context matches more than one source region.",
                        hunk=hunk.number,
                        match_count=len(locator_matches),
                        locator=hunk.locator,
                    )
            # Codex apply_patch treats a chunk with no old/context lines as an
            # append, even when the hunk does not carry an explicit EOF marker.
            candidates = [len(lines)]
        else:
            all_matches = _matches(lines, hunk.old_lines)
            candidates = [
                candidate
                for candidate in all_matches
                if candidate >= lower_bound
                and _locator_allows(lines, candidate, hunk.locator, lower_bound)
                and (
                    not hunk.end_of_file
                    or candidate + len(hunk.old_lines) == len(lines)
                )
            ]
            if not candidates and any(
                candidate < lower_bound for candidate in all_matches
            ):
                _fail(
                    "PATCH_HUNKS_OVERLAP",
                    "A patch hunk overlaps or precedes an earlier hunk.",
                    hunk=hunk.number,
                )
        if not candidates:
            _fail(
                "PATCH_CONTEXT_NOT_FOUND",
                "The patch context does not match the current source.",
                hunk=hunk.number,
                locator=hunk.locator or None,
            )
        unique_candidates = sorted(set(candidates))
        if len(unique_candidates) != 1:
            _fail(
                "PATCH_CONTEXT_AMBIGUOUS",
                "The patch context matches more than one source region.",
                hunk=hunk.number,
                match_count=len(unique_candidates),
                locator=hunk.locator or None,
            )
        start = unique_candidates[0]
        end = start + len(hunk.old_lines)
        if start < lower_bound or (not hunk.old_lines and start == last_start):
            _fail(
                "PATCH_HUNKS_OVERLAP",
                "A patch hunk overlaps or conflicts with an earlier hunk.",
                hunk=hunk.number,
            )
        resolved.append(_ResolvedHunk(hunk=hunk, start=start, end=end))
        lower_bound = end
        last_start = start
    return resolved


def apply_source_patch(source: str, patch: str) -> dict[str, Any]:
    """Apply all contextual hunks to one source atomically.

    Both the standard ``*** Begin Patch`` / ``*** Update File`` envelope and
    the contained ``@@`` update hunks are accepted. The caller supplies the
    authoritative VibeScript program separately, so the envelope path is only
    descriptive and can never redirect the write.
    """

    if not isinstance(source, str):
        _fail("PATCH_FORMAT_INVALID", "source must be a string.")
    lines, endings, default_newline = _source_lines(source)
    hunks = _parse_hunks(patch)
    resolved = _resolve_hunks(lines, hunks)

    output: list[tuple[str, str]] = []
    cursor = 0
    for item in resolved:
        output.extend(zip(lines[cursor : item.start], endings[cursor : item.start]))
        replacement_newline = next(
            (ending for ending in endings[item.start : item.end] if ending),
            (
                endings[item.start]
                if item.start < len(endings) and endings[item.start]
                else (
                    endings[item.start - 1]
                    if item.start > 0 and endings[item.start - 1]
                    else default_newline
                )
            ),
        )
        local_cursor = item.start
        replacement: list[tuple[str, str]] = []
        for prefix, value in item.hunk.operations:
            if prefix == " ":
                replacement.append((lines[local_cursor], endings[local_cursor]))
                local_cursor += 1
            elif prefix == "-":
                local_cursor += 1
            else:
                replacement.append((value, replacement_newline))
        if local_cursor != item.end:
            raise AssertionError("Resolved patch hunk did not consume its old lines.")
        touches_unterminated_eof = (
            item.end == len(lines) and bool(endings) and endings[-1] == ""
        )
        if item.start == item.end == len(lines) and replacement:
            if output and output[-1][1] == "":
                output[-1] = (output[-1][0], default_newline)
            touches_unterminated_eof = not endings or endings[-1] == ""
        if touches_unterminated_eof and replacement:
            replacement = [
                (
                    text,
                    (
                        default_newline
                        if index < len(replacement) - 1 and not ending
                        else ending
                    ),
                )
                for index, (text, ending) in enumerate(replacement)
            ]
            replacement[-1] = (replacement[-1][0], "")
        output.extend(replacement)
        cursor = item.end
    output.extend(zip(lines[cursor:], endings[cursor:]))
    updated = "".join(text + ending for text, ending in output)
    if updated == source:
        _fail("PATCH_NO_CHANGES", "The patch does not change the current source.")

    return {
        "source": updated,
        "summary": {
            "hunk_count": len(resolved),
            "added_lines": sum(item.hunk.added_lines for item in resolved),
            "removed_lines": sum(item.hunk.removed_lines for item in resolved),
            "first_changed_line": min(item.start for item in resolved) + 1,
            "last_changed_line": max(
                item.start + max(len(item.hunk.old_lines), len(item.hunk.new_lines), 1)
                for item in resolved
            ),
        },
    }
