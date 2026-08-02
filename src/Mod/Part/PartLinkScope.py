# SPDX-License-Identifier: LGPL-2.1-or-later

"""Global-link helpers for general Part FeaturePython operations.

General Part operations are allowed to consume geometry from different
GeoFeatureGroups.  Dynamic Python properties encode their scope in the
property type, so old documents using local properties must be migrated when
their proxy is restored.
"""

from __future__ import annotations

from typing import Any


GLOBAL_LINK_TYPES: dict[str, str] = {
    "App::PropertyLink": "App::PropertyLinkGlobal",
    "App::PropertyLinkList": "App::PropertyLinkListGlobal",
    "App::PropertyLinkSub": "App::PropertyLinkSubGlobal",
    "App::PropertyLinkSubList": "App::PropertyLinkSubListGlobal",
}


def global_type(local_type: str) -> str:
    """Return the global-scope counterpart of one dynamic link property type."""
    try:
        return GLOBAL_LINK_TYPES[local_type]
    except KeyError as error:
        raise ValueError(
            f"Unsupported Part link property type: {local_type}"
        ) from error


def migrate_to_global(obj: Any, name: str) -> bool:
    """Migrate an existing dynamic local link property without losing its value.

    Returns ``True`` when a migration was performed. Unknown or already-global
    property types are left untouched so newer document formats remain
    forward-compatible.
    """
    current_type = obj.getTypeIdOfProperty(name)
    expected_type = GLOBAL_LINK_TYPES.get(current_type)
    if expected_type is None:
        return False

    value = obj.getPropertyByName(name)
    group = obj.getGroupOfProperty(name)
    documentation = obj.getDocumentationOfProperty(name)
    editor_mode = obj.getEditorMode(name)
    property_status = obj.getPropertyStatus(name)

    obj.setPropertyStatus(name, "-LockDynamic")
    obj.removeProperty(name)
    try:
        obj.addProperty(expected_type, name, group, documentation)
        setattr(obj, name, value)
        obj.setEditorMode(name, editor_mode)
        obj.setPropertyStatus(name, property_status)
    except Exception:
        # A type migration is necessarily remove-and-recreate. Restore the original property
        # completely if any part of the replacement fails so document loading cannot silently
        # discard links or metadata.
        if name in obj.PropertiesList:
            obj.setPropertyStatus(name, "-LockDynamic")
            obj.removeProperty(name)
        obj.addProperty(current_type, name, group, documentation)
        setattr(obj, name, value)
        obj.setEditorMode(name, editor_mode)
        obj.setPropertyStatus(name, property_status)
        raise
    return True


def migrate_many_to_global(obj: Any, *names: str) -> None:
    """Migrate every named link property present on a restored object."""
    properties = set(obj.PropertiesList)
    for name in names:
        if name in properties:
            migrate_to_global(obj, name)
