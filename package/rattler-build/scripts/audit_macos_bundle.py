#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path

from macos_macho import dylib_dependency_paths, load_command_paths, otool


SYSTEM_PREFIXES = (
    Path("/System/Library"),
    Path("/usr/lib"),
    Path("/Library/Apple/System/Library"),
)
MACHO_SUFFIXES = {".dylib", ".so", ".bundle"}
ALLOWED_WEAK_EXTERNAL_DEPENDENCIES = {
    (
        "Contents/Resources/lib/libFreeCADGui.dylib",
        "/Library/Frameworks/3DconnexionClient.framework/Versions/A/"
        "3DconnexionClient",
    )
}
# FreeCADGui's PUBLIC link flags propagate the optional driver to consumers.
# Its location is fixed by the vendor installer, independent of our bundle.
OPTIONAL_DRIVER_FRAMEWORK = (
    "/Library/Frameworks/3DconnexionClient.framework/Versions/A/"
    "3DconnexionClient"
)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject non-relocatable Mach-O references in a macOS app bundle."
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--forbid-prefix",
        action="append",
        default=[],
        type=Path,
        help="Reject any load command that references this source prefix.",
    )
    return parser.parse_args()


def _normalized(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_system_path(path: Path) -> bool:
    return any(_is_relative_to(path, prefix) for prefix in SYSTEM_PREFIXES)


def _candidate(file_path: Path) -> bool:
    if file_path.suffix in MACHO_SUFFIXES:
        return True
    if "MacOS" in file_path.parts:
        return True
    return os.access(file_path, os.X_OK)


def _delocate_install_id(file_path: Path) -> str | None:
    """Return the exact Delocate ID allowed for one wheel-private library."""

    parts = file_path.parts
    try:
        site_packages_index = len(parts) - 1 - parts[::-1].index("site-packages")
    except ValueError:
        return None
    relative_parts = parts[site_packages_index + 1 :]
    if ".dylibs" not in relative_parts or file_path.suffix != ".dylib":
        return None
    return "/DLC/" + Path(*relative_parts).as_posix()


def _validate_path(
    value: str,
    *,
    command: str,
    file_path: Path,
    bundle: Path,
    forbidden_prefixes: tuple[Path, ...],
) -> None:
    relative_file = file_path.relative_to(bundle).as_posix()
    if (
        command == "LC_LOAD_WEAK_DYLIB"
        and (
            (relative_file, value) in ALLOWED_WEAK_EXTERNAL_DEPENDENCIES
            or value == OPTIONAL_DRIVER_FRAMEWORK
        )
    ):
        # FreeCAD deliberately weak-links the optional 3Dconnexion driver.
        # The application remains launchable when the framework is absent.
        return

    if value.startswith("/DLC/"):
        # Delocate gives copied wheel libraries a synthetic absolute install ID
        # so libraries with the same basename remain unique in Python space.
        # It is an identity, not a load location. Actual dependencies still
        # must use @loader_path and are rejected below if they contain /DLC/.
        expected_id = _delocate_install_id(file_path)
        if command in {"LC_ID_DYLIB", "linked library"} and value == expected_id:
            return
        raise RuntimeError(
            f"{file_path}: {command} has an invalid Delocate library ID: "
            f"{value}; expected {expected_id or 'no /DLC identity'}"
        )

    if command == "LC_ID_DYLIB" and value == file_path.name:
        # A bare install ID is metadata carried by the dylib itself. It does not
        # resolve a runtime dependency. Any consumer using a bare load path is
        # audited separately and remains invalid.
        return

    for prefix in forbidden_prefixes:
        if str(prefix) in value:
            raise RuntimeError(
                f"{file_path}: {command} still references source prefix "
                f"{prefix}: {value}"
            )
    if not os.path.isabs(value):
        if value.startswith("@"):
            return
        raise RuntimeError(
            f"{file_path}: {command} uses an unresolved relative load path: {value}"
        )

    path = _normalized(Path(value))
    if _is_system_path(path):
        return
    if _is_relative_to(path, bundle):
        raise RuntimeError(
            f"{file_path}: {command} contains an absolute build-time bundle "
            f"path and will break after installation: {value}"
        )
    raise RuntimeError(
        f"{file_path}: {command} references an external non-system path: {value}"
    )


def main() -> int:
    arguments = _parse_arguments()
    bundle = _normalized(arguments.bundle)
    forbidden_prefixes = tuple(_normalized(path) for path in arguments.forbid_prefix)
    if not bundle.is_dir():
        raise SystemExit(f"macOS app bundle does not exist: {bundle}")

    candidates = 0
    mach_o_files = 0
    references = 0
    violations: list[str] = []
    for file_path in sorted(bundle.rglob("*")):
        if (
            not file_path.is_file()
            or file_path.is_symlink()
            or not _candidate(file_path)
        ):
            continue
        candidates += 1
        try:
            load_output = otool(file_path, "-l")
        except RuntimeError as exc:
            violations.append(str(exc))
            continue
        if load_output is None:
            continue
        mach_o_files += 1
        for command, value in load_command_paths(load_output):
            references += 1
            try:
                _validate_path(
                    value,
                    command=command,
                    file_path=file_path,
                    bundle=bundle,
                    forbidden_prefixes=forbidden_prefixes,
                )
            except RuntimeError as exc:
                violations.append(str(exc))

        for command, value in dylib_dependency_paths(load_output):
            references += 1
            try:
                _validate_path(
                    value,
                    command=command,
                    file_path=file_path,
                    bundle=bundle,
                    forbidden_prefixes=forbidden_prefixes,
                )
            except RuntimeError as exc:
                violations.append(str(exc))

    if violations:
        distinct_violations = list(dict.fromkeys(violations))
        rendered = "\n".join(
            f"- {violation}" for violation in distinct_violations
        )
        raise RuntimeError(
            "macOS bundle relocation audit found "
            f"{len(distinct_violations)} distinct violation(s):\n"
            f"{rendered}"
        )

    print(
        "macOS bundle relocation audit passed: "
        f"{mach_o_files} Mach-O files from {candidates} candidates, "
        f"{references} load references checked.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
