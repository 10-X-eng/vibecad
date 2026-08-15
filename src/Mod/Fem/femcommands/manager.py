# ***************************************************************************
# *   Copyright (c) 2015 Przemo Fiszt <przemo@firszt.eu>                    *
# *   Copyright (c) 2016 Bernd Hahnebach <bernd@bimstatik.org>              *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

__title__ = "FreeCAD FEM command base class"
__author__ = "Przemo Firszt, Bernd Hahnebach"
__url__ = "https://www.freecad.org"

## @package manager
#  \ingroup FEM
#  \brief FreeCAD FEM command base class

import FreeCAD

from femtools import membertools
from femtools.femutils import expandParentObject
from femtools.femutils import is_of_type

if FreeCAD.GuiUp:
    from PySide import QtCore
    import FreeCADGui
    import FemGui


def can_start_command():
    """Return whether a new native FEM command owns a clean UI boundary."""

    if not FreeCAD.GuiUp or FreeCADGui.Control.activeDialog():
        return False

    # FreeCAD transaction IDs are application-wide.  Opening a FEM
    # transaction while a different document owns one can enlist or close the
    # caller's work instead of creating an independent command boundary.
    return all(
        document.getBookedTransactionID() == 0
        and not document.HasPendingTransaction
        for document in FreeCAD.listDocuments().values()
    )


def _active_document():
    """Return the exact App document represented by the active GUI document."""

    if not FreeCAD.GuiUp:
        return None
    gui_document = FreeCADGui.ActiveDocument
    document = FreeCAD.ActiveDocument
    if gui_document is None or document is None:
        return None
    try:
        return (
            document
            if gui_document.Document is document
            else None
        )
    except (AttributeError, RuntimeError):
        return None


def _is_live_in_document(obj, document):
    if obj is None or document is None:
        return False
    try:
        return (
            obj.Document is document
            and document.getObject(obj.Name) is obj
        )
    except (AttributeError, RuntimeError):
        return False


def _require_provisional_timeline_identity(
    obj,
    document,
    description,
):
    """Validate one exact factory return from the caller-owned transaction."""

    if (
        not _is_live_in_document(obj, document)
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            obj
        )
    ):
        raise RuntimeError(
            f"{description} did not return its exact newly created object"
        )
    return obj


def _canonicalize_timeline_property(obj, property_name):
    """Keep native History metadata internal, immutable, and inert."""

    obj.setEditorMode(property_name, 2)
    obj.setPropertyStatus(
        property_name,
        ("Hidden", "LockDynamic", "NoRecompute"),
    )


def _result_solver_matches(root, solver):
    """Return whether *root* is one direct generated resource of *solver*."""

    try:
        return (
            root is not solver
            and root.Document is solver.Document
            and root.getTypeIdOfProperty("VibeCADTimelineRole")
            == "App::PropertyString"
            and root.VibeCADTimelineRole == "resource"
            and root.getTypeIdOfProperty("VibeCADTimelineOwner")
            == "App::PropertyLinkHidden"
            and root.VibeCADTimelineOwner is solver
            and _timeline_root(root, solver.Document) is solver
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _canonicalize_existing_timeline_property(
    obj,
    property_name,
    expected_type,
):
    """Validate and canonicalize one optional native History contract."""

    if property_name not in obj.PropertiesList:
        return
    actual_type = obj.getTypeIdOfProperty(property_name)
    if actual_type != expected_type:
        raise TypeError(
            f"{obj.Name}.{property_name} must be "
            f"{expected_type}, not {actual_type}"
        )
    _canonicalize_timeline_property(obj, property_name)


def _mark_timeline_operation(operation):
    """Persist one exact user-visible FEM history operation."""

    document = getattr(operation, "Document", None)
    if not _is_live_in_document(operation, document):
        raise ValueError(
            "A FEM timeline operation must be live in its document"
        )

    property_name = "VibeCADTimelineRole"
    type_id = "App::PropertyString"
    if property_name in operation.PropertiesList:
        actual = operation.getTypeIdOfProperty(property_name)
        if actual != type_id:
            raise TypeError(
                f"{operation.Name}.{property_name} must be "
                f"{type_id}, not {actual}"
            )
    else:
        operation.addProperty(
            type_id,
            property_name,
            "Timeline",
            "Document timeline classification",
            attr=16,
            hidden=True,
            locked=True,
        )
    _canonicalize_timeline_property(operation, property_name)

    if "VibeCADTimelineOwner" in operation.PropertiesList:
        if (
            operation.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
        ):
            raise TypeError(
                f"{operation.Name}.VibeCADTimelineOwner must be "
                "App::PropertyLinkHidden"
            )
        _canonicalize_timeline_property(
            operation,
            "VibeCADTimelineOwner",
        )
        operation.VibeCADTimelineOwner = None

    for optional_name, optional_type in (
        ("VibeCADTimelineEditor", "App::PropertyLinkHidden"),
        ("VibeCADTimelineEditCommand", "App::PropertyString"),
        (
            "VibeCADTimelineReplacedInputs",
            "App::PropertyLinkListHidden",
        ),
    ):
        _canonicalize_existing_timeline_property(
            operation,
            optional_name,
            optional_type,
        )

    operation.VibeCADTimelineRole = "operation"
    return operation


def _mark_timeline_resource(resource, owner):
    """Persist one exact FEM implementation object under its result root."""

    document = getattr(owner, "Document", None)
    if (
        resource is None
        or resource is owner
        or not _is_live_in_document(owner, document)
        or not _is_live_in_document(resource, document)
    ):
        raise ValueError(
            "A FEM timeline resource and its distinct owner must be live "
            "in one document"
        )

    expected = {
        "VibeCADTimelineRole": "App::PropertyString",
        "VibeCADTimelineOwner": "App::PropertyLinkHidden",
    }
    for property_name, type_id in expected.items():
        if property_name in resource.PropertiesList:
            actual = resource.getTypeIdOfProperty(property_name)
            if actual != type_id:
                raise TypeError(
                    f"{resource.Name}.{property_name} must be "
                    f"{type_id}, not {actual}"
                )
        else:
            resource.addProperty(
                type_id,
                property_name,
                "Timeline",
                "Document timeline result ownership",
                attr=16,
                hidden=True,
                locked=True,
            )
        _canonicalize_timeline_property(resource, property_name)

    resource.VibeCADTimelineOwner = owner
    resource.VibeCADTimelineRole = "resource"
    return resource


class _ResultResourceReconciliation:
    """Opaque exact-identity snapshot for one solver resource graph."""

    __slots__ = (
        "document",
        "document_uid",
        "solver_identity",
        "root_identity",
        "old_resource_identities",
        "replacement_root_identities",
        "replaced_resource_identities",
        "resource_identities",
        "consumed",
    )

    def __init__(
        self,
        document,
        solver,
        root,
        old_resources,
        retained_result_resources,
        replacement_roots,
        replaced_resources,
    ):
        self.document = document
        self.document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        self.solver_identity = (
            str(solver.Name),
            int(solver.ID),
        )
        self.root_identity = (
            None
            if root is None
            else (str(root.Name), int(root.ID))
        )
        self.old_resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in old_resources
        )
        self.replacement_root_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in replacement_roots
        )
        self.replaced_resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in replaced_resources
        )
        self.resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in retained_result_resources
        )
        self.consumed = False


def _timeline_root(obj, document):
    """Resolve one persisted FEM timeline owner chain exactly."""

    current = obj
    visited = set()
    while (
        _is_live_in_document(current, document)
        and "VibeCADTimelineRole" in current.PropertiesList
        and current.getTypeIdOfProperty("VibeCADTimelineRole")
        == "App::PropertyString"
        and current.VibeCADTimelineRole == "resource"
    ):
        identity = (str(current.Name), int(current.ID))
        if identity in visited:
            raise RuntimeError(
                "A FEM result resource has a cyclic owner graph"
            )
        visited.add(identity)
        if (
            "VibeCADTimelineOwner" not in current.PropertiesList
            or current.getTypeIdOfProperty(
                "VibeCADTimelineOwner"
            )
            != "App::PropertyLinkHidden"
        ):
            raise RuntimeError(
                "A FEM result resource has invalid owner metadata"
            )
        current = current.VibeCADTimelineOwner
    return (
        current
        if _is_live_in_document(current, document)
        else None
    )


def _timeline_owner_chain_contains(obj, ancestor, document):
    """Return whether one exact resource owner chain contains *ancestor*."""

    current = obj
    visited = set()
    while _is_live_in_document(current, document):
        if current is ancestor:
            return True
        identity = (str(current.Name), int(current.ID))
        if identity in visited:
            raise RuntimeError(
                "A FEM result resource has a cyclic owner graph"
            )
        visited.add(identity)
        if (
            "VibeCADTimelineRole" not in current.PropertiesList
            or current.getTypeIdOfProperty("VibeCADTimelineRole")
            != "App::PropertyString"
            or current.VibeCADTimelineRole != "resource"
            or "VibeCADTimelineOwner" not in current.PropertiesList
            or current.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
        ):
            return False
        current = current.VibeCADTimelineOwner
    return False


def _timeline_owned_resource_graph(owner):
    """Return one tracked FEM operation's exact History resource graph."""

    document = getattr(owner, "Document", None)
    if (
        not _is_live_in_document(owner, document)
        or "VibeCADTimelineRole" not in owner.PropertiesList
        or owner.getTypeIdOfProperty("VibeCADTimelineRole")
        != "App::PropertyString"
        or owner.VibeCADTimelineRole != "operation"
        or _timeline_root(owner, document) is not owner
    ):
        raise ValueError(
            "A FEM result producer must be one live tracked solver operation"
        )
    timeline = document.getObject("VibeCADTimeline")
    if (
        timeline is None
        or timeline.TypeId != "App::DocumentTimeline"
    ):
        raise RuntimeError(
            "The FEM solver has no native document timeline"
        )
    operations = tuple(timeline.Operations)
    if owner not in operations:
        raise RuntimeError(
            "The FEM solver is absent from document History"
        )
    resources = [
        candidate
        for candidate in operations
        if candidate is not owner
        and _timeline_root(candidate, document) is owner
    ]
    direct_roots = [
        resource
        for resource in resources
        if getattr(
            resource,
            "VibeCADTimelineOwner",
            None,
        )
        is owner
    ]
    return document, resources, direct_roots


def _canonical_timeline_resource_order(owner, resources):
    """Return canonical nested resource-first, owner-last order."""

    document = getattr(owner, "Document", None)
    exact_resources = []
    for resource in resources:
        if (
            resource is owner
            or resource in exact_resources
            or not _is_live_in_document(resource, document)
            or _timeline_root(resource, document) is not owner
        ):
            raise ValueError(
                "A FEM result graph must contain distinct live resources "
                "of one exact solver"
            )
        exact_resources.append(resource)

    children = {owner: []}
    for resource in exact_resources:
        children[resource] = []
    for resource in exact_resources:
        direct_owner = getattr(
            resource,
            "VibeCADTimelineOwner",
            None,
        )
        if direct_owner not in children:
            raise RuntimeError(
                "A FEM result graph omits an exact intermediate owner"
            )
        children[direct_owner].append(resource)

    result = []
    visiting = set()
    visited = set()

    def visit(resource):
        identity = (str(resource.Name), int(resource.ID))
        if identity in visiting:
            raise RuntimeError(
                "A FEM result graph contains a cyclic owner relation"
            )
        if identity in visited:
            return
        visiting.add(identity)
        for child in children[resource]:
            visit(child)
        visiting.remove(identity)
        visited.add(identity)
        result.append(resource)

    for resource in children[owner]:
        visit(resource)
    if len(result) != len(exact_resources):
        raise RuntimeError(
            "A FEM result graph is not connected to its solver"
        )
    return result


def _stage_timeline_result_graph(
    solver,
    root=None,
    *,
    replacement_roots=(),
):
    """Stage one solver before a result importer mutates its resource graph."""

    document, resources, direct_roots = (
        _timeline_owned_resource_graph(solver)
    )
    exact_results = tuple(getattr(solver, "Results", ()) or ())
    for result in exact_results:
        if (
            not _is_live_in_document(result, document)
            or _timeline_root(result, document) is not solver
        ):
            raise RuntimeError(
                "solver.Results contains an identity outside the solver's "
                "canonical History resource graph"
            )

    retained_result_resources = []
    if root is not None:
        if (
            root not in exact_results
            or root not in resources
            or getattr(root, "VibeCADTimelineOwner", None) is not solver
        ):
            raise ValueError(
                "A retained FEM result root must be one direct resource "
                "of its exact solver"
            )
        retained_result_resources = [
            resource
            for resource in resources
            if resource is not root
            and _timeline_owner_chain_contains(
                resource,
                root,
                document,
            )
        ]

    exact_replacement_roots = []
    for replacement_root in replacement_roots:
        if (
            replacement_root is root
            or replacement_root in exact_replacement_roots
            or replacement_root not in exact_results
            or replacement_root not in resources
            or getattr(
                replacement_root,
                "VibeCADTimelineOwner",
                None,
            )
            is not solver
        ):
            raise ValueError(
                "Every replaced FEM result root must be one distinct "
                "direct solver.Results resource"
            )
        exact_replacement_roots.append(replacement_root)
    replaced_resources = [
        resource
        for resource in resources
        if any(
            _timeline_owner_chain_contains(
                resource,
                replacement_root,
                document,
            )
            for replacement_root in exact_replacement_roots
        )
    ]

    document.stageTimelineOperationResourceReconciliation(
        solver,
        direct_roots,
    )
    return _ResultResourceReconciliation(
        document,
        solver,
        root,
        resources,
        retained_result_resources,
        exact_replacement_roots,
        replaced_resources,
    )


def _finalize_timeline_result_graph(
    solver,
    root,
    resources=(),
    *,
    root_is_new=True,
    reconciliation=None,
):
    """Publish generated results as exact resources of their solver operation."""

    document = getattr(solver, "Document", None)
    if (
        not _is_live_in_document(solver, document)
        or not _is_live_in_document(root, document)
        or root is solver
    ):
        raise ValueError(
            "A FEM result graph requires distinct live solver and result "
            "root identities"
        )

    exact_resources = []
    for resource in resources:
        if (
            resource is root
            or not _is_live_in_document(resource, document)
            or resource in exact_resources
        ):
            raise ValueError(
                "Every FEM result resource must be a distinct exact live "
                "identity"
            )
        exact_resources.append(resource)

    actual_root_is_new = (
        document
        .isProvisionallyEnrolledInTimelineByCurrentTransaction(root)
    )
    if actual_root_is_new != bool(root_is_new):
        raise RuntimeError(
            "The FEM result importer reported the wrong root lifecycle"
        )
    if (
        not isinstance(
            reconciliation,
            _ResultResourceReconciliation,
        )
        or reconciliation.consumed
    ):
        raise RuntimeError(
            "An existing FEM result root must be staged before its importer "
            "creates resources"
        )
    if (
        reconciliation.document is not document
        or reconciliation.document_uid
        != str(getattr(document, "Uid", "") or "")
        or reconciliation.solver_identity
        != (str(solver.Name), int(solver.ID))
    ):
        raise RuntimeError(
            "The staged FEM solver changed exact identity"
        )

    old_resources = []
    for name, object_id in reconciliation.old_resource_identities:
        resource = document.getObject(int(object_id))
        if (
            not _is_live_in_document(resource, document)
            or int(resource.ID) != object_id
            or str(resource.Name) != name
            or _timeline_root(resource, document) is not solver
        ):
            raise RuntimeError(
                "A retained FEM solver resource changed exact identity"
            )
        old_resources.append(resource)

    replaced_identity_set = set(
        reconciliation.replaced_resource_identities
    )
    replaced_resources = [
        resource
        for resource in old_resources
        if (str(resource.Name), int(resource.ID))
        in replaced_identity_set
    ]
    if len(replaced_resources) != len(replaced_identity_set):
        raise RuntimeError(
            "A replaced FEM result resource changed exact identity"
        )
    retained_resources = [
        resource
        for resource in old_resources
        if resource not in replaced_resources
    ]

    root_identity = (str(root.Name), int(root.ID))
    if actual_root_is_new:
        if reconciliation.root_identity is not None:
            raise RuntimeError(
                "A new FEM result does not match its staged lifecycle"
            )
        if root in old_resources:
            raise RuntimeError(
                "A new FEM result aliases a retained solver resource"
            )
        _mark_timeline_resource(root, solver)
    else:
        if (
            reconciliation.root_identity != root_identity
            or root not in retained_resources
            or getattr(root, "VibeCADTimelineOwner", None) is not solver
        ):
            raise RuntimeError(
                "The retained FEM result root changed identity or ownership"
            )

    for resource in exact_resources:
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            resource
        ):
            raise RuntimeError(
                "A new FEM result resource lacks exact current-transaction "
                "creation proof"
            )
        _mark_timeline_resource(resource, root)

    linked_results = tuple(getattr(solver, "Results", ()) or ())
    if root not in linked_results:
        raise RuntimeError(
            "The FEM result importer did not link its exact result root "
            "through solver.Results"
        )

    replacement_roots = []
    for name, object_id in reconciliation.replacement_root_identities:
        replacement_root = document.getObject(int(object_id))
        if (
            not _is_live_in_document(replacement_root, document)
            or int(replacement_root.ID) != object_id
            or str(replacement_root.Name) != name
            or replacement_root not in replaced_resources
            or getattr(
                replacement_root,
                "VibeCADTimelineOwner",
                None,
            )
            is not solver
        ):
            raise RuntimeError(
                "A replaced FEM result root changed exact identity"
            )
        replacement_roots.append(replacement_root)

    replacement_by_old = {
        replacement_root: root
        for replacement_root in replacement_roots
    }
    replaced_set = set(replaced_resources)
    timeline = document.getObject("VibeCADTimeline")
    analysis = solver.getParentGroup()
    for old_resource in replaced_resources:
        consumers = [
            consumer
            for consumer in tuple(old_resource.InList or ())
            if consumer not in replaced_set
            and consumer is not solver
            and consumer is not timeline
        ]
        unexpected = [
            consumer
            for consumer in consumers
            if consumer is not analysis
        ]
        if unexpected:
            labels = ", ".join(
                str(getattr(consumer, "Label", consumer.Name))
                for consumer in unexpected[:4]
            )
            raise RuntimeError(
                "An existing FEM result has downstream consumers that "
                f"cannot be replaced safely: {labels}"
            )
        if analysis in consumers and old_resource not in replacement_by_old:
            candidates = [
                candidate
                for candidate in (*exact_resources, root)
                if candidate.TypeId == old_resource.TypeId
                and candidate not in replacement_by_old.values()
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    "An analysis-owned FEM result resource has no unique "
                    "same-type replacement"
                )
            replacement_by_old[old_resource] = candidates[0]

    if replaced_resources:
        if analysis is None or not _is_live_in_document(analysis, document):
            raise RuntimeError(
                "The FEM solver lost its analysis during result replacement"
            )
        for old_resource in replaced_resources:
            if old_resource in tuple(analysis.Group or ()):
                analysis.removeObject(old_resource)
                replacement = replacement_by_old.get(old_resource)
                if (
                    replacement is not None
                    and replacement not in tuple(analysis.Group or ())
                ):
                    analysis.addObject(replacement)
        solver_results = [
            result
            for result in tuple(solver.Results or ())
            if result not in replaced_set
        ]
        if root not in solver_results:
            solver_results.append(root)
        solver.Results = solver_results

    new_resources = list(exact_resources)
    if actual_root_is_new:
        new_resources.append(root)
    final_resources = _canonical_timeline_resource_order(
        solver,
        retained_resources + new_resources,
    )
    old_index_by_identity = {
        identity: index
        for index, identity in enumerate(
            reconciliation.old_resource_identities
        )
    }
    final_index_by_identity = {
        (str(resource.Name), int(resource.ID)): index
        for index, resource in enumerate(final_resources)
    }
    replacement_source_by_identity = {
        (str(replacement.Name), int(replacement.ID)):
        old_index_by_identity[(str(old.Name), int(old.ID))]
        for old, replacement in replacement_by_old.items()
    }
    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        solver,
        final_resources,
        [
            old_index_by_identity.get(
                (str(resource.Name), int(resource.ID)),
                replacement_source_by_identity.get(
                    (str(resource.Name), int(resource.ID)),
                    -1,
                ),
            )
            for resource in final_resources
        ],
        [
            (
                final_index_by_identity[
                    (
                        str(replacement_by_old[old_resource].Name),
                        int(replacement_by_old[old_resource].ID),
                    )
                ]
                if old_resource in replacement_by_old
                else final_index_by_identity.get(identity, -1)
            )
            for old_resource, identity in zip(
                old_resources,
                reconciliation.old_resource_identities,
            )
        ],
    )
    for replaced_resource in reversed(replaced_resources):
        if _is_live_in_document(replaced_resource, document):
            document.removeObject(str(replaced_resource.Name))
    reconciliation.consumed = True
    return root


def _purge_timeline_result_roots(solver, roots):
    """Remove exact solver result roots while preserving the solver operation.

    The caller owns the active document transaction and must delete any
    independent post-processing consumers first.  This function stages and
    finalizes the solver resource block before deleting the result objects, so
    History, ``solver.Results``, analysis membership, undo, and save/restore
    remain one atomic graph rewrite.
    """

    exact_roots = tuple(roots)
    if not exact_roots or len(exact_roots) != len(set(exact_roots)):
        raise ValueError(
            "A FEM result purge requires distinct solver result roots"
        )
    reconciliation = _stage_timeline_result_graph(
        solver,
        replacement_roots=exact_roots,
    )
    document = reconciliation.document
    old_resources = []
    for name, object_id in reconciliation.old_resource_identities:
        resource = document.getObject(int(object_id))
        if (
            not _is_live_in_document(resource, document)
            or str(resource.Name) != name
            or int(resource.ID) != object_id
            or _timeline_root(resource, document) is not solver
        ):
            raise RuntimeError(
                "A staged FEM result resource changed exact identity"
            )
        old_resources.append(resource)

    purged_identity_set = set(
        reconciliation.replaced_resource_identities
    )
    purged_resources = [
        resource
        for resource in old_resources
        if (str(resource.Name), int(resource.ID))
        in purged_identity_set
    ]
    if len(purged_resources) != len(purged_identity_set):
        raise RuntimeError(
            "A staged FEM result purge lost an exact resource identity"
        )
    purged_set = set(purged_resources)
    retained_resources = [
        resource
        for resource in old_resources
        if resource not in purged_set
    ]
    timeline = document.getObject("VibeCADTimeline")
    analysis = solver.getParentGroup()
    allowed_consumers = {solver, timeline, analysis, *purged_set}
    for resource in purged_resources:
        unexpected = [
            consumer
            for consumer in tuple(resource.InList or ())
            if consumer not in allowed_consumers
        ]
        if unexpected:
            labels = ", ".join(
                str(getattr(consumer, "Label", consumer.Name))
                for consumer in unexpected[:4]
            )
            raise RuntimeError(
                "A FEM result still has downstream consumers and cannot be "
                f"purged safely: {labels}"
            )

    if analysis is None or not _is_live_in_document(analysis, document):
        raise RuntimeError(
            "The FEM solver lost its analysis during result purge"
        )
    for resource in purged_resources:
        if resource in tuple(analysis.Group or ()):
            analysis.removeObject(resource)
    solver.Results = [
        result
        for result in tuple(solver.Results or ())
        if result not in purged_set
    ]

    final_resources = _canonical_timeline_resource_order(
        solver,
        retained_resources,
    )
    old_index_by_identity = {
        identity: index
        for index, identity in enumerate(
            reconciliation.old_resource_identities
        )
    }
    final_index_by_identity = {
        (str(resource.Name), int(resource.ID)): index
        for index, resource in enumerate(final_resources)
    }
    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        solver,
        final_resources,
        [
            old_index_by_identity[(str(resource.Name), int(resource.ID))]
            for resource in final_resources
        ],
        [
            final_index_by_identity.get(identity, -1)
            for identity in reconciliation.old_resource_identities
        ],
    )
    for resource in reversed(purged_resources):
        if _is_live_in_document(resource, document):
            document.removeObject(str(resource.Name))
    reconciliation.consumed = True
    return tuple(purged_resources)


def _mark_timeline_replaced_inputs(operation, inputs):
    """Persist exact visible inputs deliberately hidden by one FEM operation."""

    document = getattr(operation, "Document", None)
    if not _is_live_in_document(operation, document):
        raise ValueError(
            "A FEM replacement operation must be live in its document"
        )

    exact_inputs = []
    for input_obj in inputs:
        if (
            input_obj is operation
            or not _is_live_in_document(input_obj, document)
        ):
            raise ValueError(
                "A FEM replaced input must be a distinct live object "
                "in the operation document"
            )
        if input_obj not in exact_inputs:
            exact_inputs.append(input_obj)
    if not exact_inputs:
        raise ValueError(
            "A FEM replacement operation requires at least one exact input"
        )

    expected = {
        "VibeCADTimelineRole": "App::PropertyString",
        "VibeCADTimelineReplacedInputs": "App::PropertyLinkListHidden",
    }
    for property_name, type_id in expected.items():
        if property_name in operation.PropertiesList:
            actual = operation.getTypeIdOfProperty(property_name)
            if actual != type_id:
                raise TypeError(
                    f"{operation.Name}.{property_name} must be "
                    f"{type_id}, not {actual}"
                )
            # Imported and copied FEM objects may preserve the value but lose
            # the internal-only property-editor presentation.
            _canonicalize_timeline_property(
                operation,
                property_name,
            )
            continue
        operation.addProperty(
            type_id,
            property_name,
            "Timeline",
            "Document timeline replacement contract",
            attr=16,
            hidden=True,
            locked=True,
        )
        _canonicalize_timeline_property(operation, property_name)

    if "VibeCADTimelineOwner" in operation.PropertiesList:
        if (
            operation.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
            or operation.VibeCADTimelineOwner is not None
        ):
            raise TypeError(
                "A FEM replacement operation cannot retain resource-owner metadata"
            )
        _canonicalize_timeline_property(
            operation,
            "VibeCADTimelineOwner",
        )

    for optional_name, optional_type in (
        ("VibeCADTimelineEditor", "App::PropertyLinkHidden"),
        ("VibeCADTimelineEditCommand", "App::PropertyString"),
    ):
        _canonicalize_existing_timeline_property(
            operation,
            optional_name,
            optional_type,
        )

    operation.VibeCADTimelineReplacedInputs = exact_inputs
    operation.VibeCADTimelineRole = "operation"


def _selected_in_active_document():
    document = _active_document()
    if document is None:
        return []
    return [
        obj
        for obj in FreeCADGui.Selection.getSelection()
        if _is_live_in_document(obj, document)
    ]


def _post_pipeline_for_object(obj):
    """Return the one post pipeline that owns *obj*, or ``None``."""

    document = getattr(obj, "Document", None)
    if not _is_live_in_document(obj, document):
        return None
    if obj.isDerivedFrom("Fem::FemPostPipeline"):
        return obj

    pending = [obj]
    visited = set()
    pipelines = {}
    while pending:
        current = pending.pop()
        try:
            identity = int(current.ID)
        except (AttributeError, RuntimeError):
            continue
        if identity in visited or not _is_live_in_document(
            current,
            document,
        ):
            continue
        visited.add(identity)
        for parent in current.InList:
            if not _is_live_in_document(parent, document):
                continue
            if parent.isDerivedFrom("Fem::FemPostPipeline"):
                pipelines[int(parent.ID)] = parent
            else:
                pending.append(parent)
    return next(iter(pipelines.values())) if len(pipelines) == 1 else None


def _post_group_for_object(obj, pipeline):
    """Return the one exact post group that should receive a child filter."""

    document = getattr(obj, "Document", None)
    if (
        not _is_live_in_document(obj, document)
        or not _is_live_in_document(pipeline, document)
        or _post_pipeline_for_object(obj) is not pipeline
    ):
        return None
    if obj.hasExtension("Fem::FemPostGroupExtension"):
        return obj

    groups = {
        int(parent.ID): parent
        for parent in obj.InList
        if _is_live_in_document(parent, document)
        and parent.hasExtension("Fem::FemPostGroupExtension")
        and _post_pipeline_for_object(parent) is pipeline
    }
    return next(iter(groups.values())) if len(groups) == 1 else None


def _document_expression(document):
    return f"FreeCAD.getDocument({document.Name!r})"


def _object_expression(obj):
    return (
        f"{_document_expression(obj.Document)}"
        f".getObject({obj.Name!r})"
    )


def _open_exact_transaction(document, label):
    """Open one transaction on *document* and return its application ID."""

    document.openTransaction(label)
    transaction_id = document.getBookedTransactionID()
    if not transaction_id:
        raise RuntimeError("Could not open the FEM command transaction")
    return transaction_id


def _close_exact_transaction(document, transaction_id, abort):
    """Close only the transaction this command opened."""

    try:
        if (
            transaction_id
            and document.getBookedTransactionID() == transaction_id
        ):
            FreeCAD.closeActiveTransaction(abort, transaction_id)
    except RuntimeError:
        # Document closure already rolls its transaction back.
        pass


class CommandManager:

    def __init__(self):

        self.command = "FEM" + self.__class__.__name__
        self.pixmap = self.command
        self.menutext = self.__class__.__name__.lstrip("_")
        self.accel = ""
        self.tooltip = f"Creates a {self.menutext}"
        self.resources = None

        self.is_active = None
        self.do_activated = None
        self.selobj = None
        self.selobj2 = None
        self.active_analysis = None

    def GetResources(self):
        if self.resources is None:
            self.resources = {
                "Pixmap": self.pixmap,
                "MenuText": QtCore.QT_TRANSLATE_NOOP(self.command, self.menutext),
                "Accel": self.accel,
                "ToolTip": QtCore.QT_TRANSLATE_NOOP(self.command, self.tooltip),
            }
        return self.resources

    def IsActive(self):
        # FEM commands either mutate the active document or open a modal tool.
        # Starting one while another task owns the GUI, or while any document
        # transaction is still in flight, lets two independent command
        # lifecycles compete for the same document.
        self.selobj = None
        self.selobj2 = None
        self.active_analysis = None

        if not can_start_command():
            return False

        active = False
        if not self.is_active:
            pass
        elif self.is_active == "always":
            active = True
        elif self.is_active == "with_document":
            active = _active_document() is not None
        elif self.is_active == "with_analysis":
            active = FemGui.getActiveAnalysis() is not None and self.active_analysis_in_active_doc()
        elif self.is_active == "with_results":
            active = (
                FemGui.getActiveAnalysis() is not None
                and self.active_analysis_in_active_doc()
                and (self.results_present() or self.result_mesh_present())
            )
        elif self.is_active == "with_selresult":
            active = (
                # on import of Frd file in a empty document not Analysis will be there
                FreeCADGui.ActiveDocument is not None
                and self.result_selected()
            )
        elif self.is_active == "with_vtk_selresult":
            active = self.vtk_result_selected()
        elif self.is_active == "with_part_feature":
            active = FreeCADGui.ActiveDocument is not None and self.part_feature_selected()
        elif self.is_active == "with_femmesh":
            active = FreeCADGui.ActiveDocument is not None and self.femmesh_selected()
        elif self.is_active == "with_gmsh_femmesh":
            active = FreeCADGui.ActiveDocument is not None and self.gmsh_femmesh_selected()
        elif self.is_active == "with_femmesh_andor_res":
            active = (
                FreeCADGui.ActiveDocument is not None and self.with_femmesh_andor_res_selected()
            )
        elif self.is_active == "with_material":
            active = (
                self.active_analysis_in_active_doc()
                and self.material_selected()
            )
        elif self.is_active == "with_material_solid":
            active = (
                self.active_analysis_in_active_doc()
                and self.material_solid_selected()
            )
        elif self.is_active == "with_solver":
            active = (
                self.active_analysis_in_active_doc()
                and self.solver_selected()
            )
        elif self.is_active == "with_solver_elmer":
            active = (
                self.active_analysis_in_active_doc()
                and self.solver_elmer_selected()
            )
        elif self.is_active == "with_analysis_without_solver":
            active = (
                FemGui.getActiveAnalysis() is not None
                and self.active_analysis_in_active_doc()
                and not self.analysis_has_solver()
            )
        return active

    def Activated(self):
        # Commands can also be invoked by macros and Python.  Never trust a
        # cached IsActive() selection or analysis across activations.
        if not self.IsActive():
            return

        if self.do_activated == "add_obj_on_gui_noset_edit":
            self.add_obj_on_gui_noset_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_obj_on_gui_expand_noset_edit":
            self.add_obj_on_gui_expand_noset_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_obj_on_gui_set_edit":
            self.add_obj_on_gui_set_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_obj_on_gui_selobj_noset_edit":
            self.add_obj_on_gui_selobj_noset_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_obj_on_gui_selobj_set_edit":
            self.add_obj_on_gui_selobj_set_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_obj_on_gui_selobj_expand_noset_edit":
            self.add_obj_on_gui_selobj_expand_noset_edit(self.__class__.__name__.lstrip("_"))
        elif self.do_activated == "add_filter_set_edit":
            self.add_filter_set_edit(self.__class__.__name__.lstrip("_"))
        # in all other cases Activated is implemented it the command class

    def results_present(self):
        results = False
        analysis_members = (
            self.active_analysis.Group
            if self.active_analysis is not None
            else ()
        )
        for o in analysis_members:
            if o.isDerivedFrom("Fem::FemResultObject"):
                results = True
        return results

    def result_mesh_present(self):
        result_mesh = False
        analysis_members = (
            self.active_analysis.Group
            if self.active_analysis is not None
            else ()
        )
        for o in analysis_members:
            if is_of_type(o, "Fem::MeshResult"):
                result_mesh = True
        return result_mesh

    def result_selected(self):
        sel = _selected_in_active_document()
        if len(sel) == 1 and sel[0].isDerivedFrom("Fem::FemResultObject"):
            self.selobj = sel[0]
            return True
        return False

    def vtk_result_selected(self):
        sel = _selected_in_active_document()
        if (
            len(sel) == 1
            and sel[0].isDerivedFrom("Fem::FemPostObject")
            and _post_pipeline_for_object(sel[0]) is not None
        ):
            self.selobj = sel[0]
            return True
        return False

    def part_feature_selected(self):
        sel = _selected_in_active_document()
        if len(sel) == 1 and sel[0].isDerivedFrom("Part::Feature"):
            self.selobj = sel[0]
            return True
        else:
            return False

    def femmesh_selected(self):
        sel = _selected_in_active_document()
        if len(sel) == 1 and sel[0].isDerivedFrom("Fem::FemMeshObject"):
            self.selobj = sel[0]
            return True
        else:
            return False

    def gmsh_femmesh_selected(self):
        sel = _selected_in_active_document()
        if len(sel) == 1 and is_of_type(sel[0], "Fem::FemMeshGmsh"):
            self.selobj = sel[0]
            return True
        else:
            return False

    def material_selected(self):
        sel = _selected_in_active_document()
        if (
            len(sel) == 1
            and sel[0].isDerivedFrom(
                "App::MaterialObjectPython"
            )
            and self.active_analysis is not None
            and sel[0] in self.active_analysis.Group
            and not membertools._is_suppressed(sel[0])
        ):
            self.selobj = sel[0]
            return True
        else:
            return False

    def material_solid_selected(self):
        sel = _selected_in_active_document()
        if (
            len(sel) == 1
            and sel[0].isDerivedFrom("App::MaterialObjectPython")
            and hasattr(sel[0], "Category")
            and sel[0].Category == "Solid"
            and self.active_analysis is not None
            and sel[0] in self.active_analysis.Group
        ):
            self.selobj = sel[0]
            return True
        else:
            return False

    def with_femmesh_andor_res_selected(self):
        sel = _selected_in_active_document()
        if len(sel) == 1 and sel[0].isDerivedFrom("Fem::FemMeshObject"):
            self.selobj = sel[0]
            self.selobj2 = None
            return True
        elif len(sel) == 2:
            if sel[0].isDerivedFrom("Fem::FemMeshObject"):
                if sel[1].isDerivedFrom("Fem::FemResultObject"):
                    self.selobj = sel[0]  # mesh
                    self.selobj2 = sel[1]  # res
                    return True
                else:
                    return False
            elif sel[1].isDerivedFrom("Fem::FemMeshObject"):
                if sel[0].isDerivedFrom("Fem::FemResultObject"):
                    self.selobj = sel[1]  # mesh
                    self.selobj2 = sel[0]  # res
                    return True
                else:
                    return False
            else:
                return False
        else:
            return False

    def active_analysis_in_active_doc(self):
        analysis = FemGui.getActiveAnalysis()
        document = _active_document()
        if _is_live_in_document(analysis, document):
            self.active_analysis = analysis
            return True
        else:
            return False

    def solver_selected(self):
        sel = _selected_in_active_document()
        if (
            len(sel) == 1
            and sel[0].isDerivedFrom(
                "Fem::FemSolverObjectPython"
            )
            and self.active_analysis is not None
            and sel[0] in self.active_analysis.Group
            and not membertools._is_suppressed(sel[0])
        ):
            self.selobj = sel[0]
            return True
        else:
            return False

    def solver_elmer_selected(self):
        sel = _selected_in_active_document()
        if (
            len(sel) == 1
            and is_of_type(sel[0], "Fem::SolverElmer")
            and self.active_analysis is not None
            and sel[0] in self.active_analysis.Group
            and not membertools._is_suppressed(sel[0])
        ):
            self.selobj = sel[0]
            return True
        else:
            return False

    def analysis_has_solver(self):
        solver = False
        analysis_members = (
            self.active_analysis.Group
            if self.active_analysis is not None
            else ()
        )
        for o in analysis_members:
            if (
                o.isDerivedFrom("Fem::FemSolverObjectPython")
                and not membertools._is_suppressed(o)
            ):
                solver = True
        if solver is True:
            return True
        else:
            return False

    def hide_meshes_show_parts_constraints(self):
        document = _active_document()
        analysis = self.active_analysis
        if (
            FreeCAD.GuiUp
            and _is_live_in_document(analysis, document)
        ):
            for acnstrmesh in analysis.Group:
                if "Constraint" in acnstrmesh.TypeId:
                    acnstrmesh.ViewObject.Visibility = True
                if "Mesh" in acnstrmesh.TypeId:
                    # OvG: Hide meshes and show constraints and meshed part
                    # e.g. on purging results
                    acnstrmesh.ViewObject.Visibility = False

    # ****************************************************************************************
    # methods to add the objects to the document in FreeCADGui mode

    def _begin_creation(self, label, require_analysis=True):
        document = _active_document()
        analysis = self.active_analysis
        if document is None:
            raise RuntimeError(
                "The active FEM document is no longer available"
            )
        if require_analysis and not _is_live_in_document(
            analysis,
            document,
        ):
            raise RuntimeError(
                "The active FEM analysis is no longer available"
            )
        transaction_id = _open_exact_transaction(document, label)
        return document, analysis, transaction_id

    def _make_object(
        self,
        document,
        analysis,
        objtype,
        source=None,
        add_to_analysis=True,
    ):
        FreeCADGui.addModule("ObjectsFem")
        FreeCADGui.addModule("FemGui")
        arguments = _document_expression(document)
        if source is not None:
            if not _is_live_in_document(source, document):
                raise RuntimeError(
                    "The selected FEM source is no longer available"
                )
            arguments += f", {_object_expression(source)}"
        created = FreeCADGui.runDocumentObjectCommand(
            document,
            f"ObjectsFem.make{objtype}({arguments})",
        )
        _require_provisional_timeline_identity(
            created,
            document,
            "The FEM factory",
        )
        if add_to_analysis:
            if not _is_live_in_document(analysis, document):
                raise RuntimeError(
                    "The active FEM analysis is no longer available"
                )
            FreeCADGui.doCommand(
                f"{_object_expression(analysis)}"
                f".addObject({_object_expression(created)})"
            )
            if created not in analysis.Group:
                raise RuntimeError(
                    "The FEM object was not added to its analysis"
                )
        elif source is not None and source not in created.InList:
            raise RuntimeError(
                "The FEM object was not linked to its selected owner"
            )
        return created

    @staticmethod
    def _start_edit(document, obj):
        gui_document = FreeCADGui.getDocument(document.Name)
        if gui_document is None or not _is_live_in_document(obj, document):
            raise RuntimeError("The FEM editor target is no longer available")
        result = gui_document.setEdit(obj, 0)
        if result is False:
            raise RuntimeError("The FEM editor could not be opened")

    def add_obj_on_gui_set_edit(self, objtype):
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}"
        )
        try:
            created = self._make_object(
                document,
                analysis,
                objtype,
            )
            FreeCADGui.Selection.clearSelection()
            self._start_edit(document, created)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_obj_on_gui_noset_edit(self, objtype):
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}"
        )
        try:
            self._make_object(document, analysis, objtype)
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_obj_on_gui_expand_noset_edit(self, objtype):
        # like add_obj_on_gui_noset_edit but the parent object
        # is expanded in the tree to see the added obj
        # the added obj is also selected to enable direct additions to it
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}"
        )
        try:
            expandParentObject()
            created = self._make_object(
                document,
                analysis,
                objtype,
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(created)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_obj_on_gui_selobj_set_edit(self, objtype):
        source = self.selobj
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}",
            require_analysis=False,
        )
        try:
            created = self._make_object(
                document,
                analysis,
                objtype,
                source,
                add_to_analysis=False,
            )
            FreeCADGui.Selection.clearSelection()
            self._start_edit(document, created)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_obj_on_gui_selobj_noset_edit(self, objtype):
        source = self.selobj
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}",
            require_analysis=False,
        )
        try:
            self._make_object(
                document,
                analysis,
                objtype,
                source,
                add_to_analysis=False,
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            FreeCADGui.Selection.clearSelection()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_obj_on_gui_selobj_expand_noset_edit(self, objtype):
        # like add_obj_on_gui_selobj_noset_edit but the selection is kept
        # and the selobj is expanded in the tree to see the added obj
        source = self.selobj
        document, analysis, transaction_id = self._begin_creation(
            f"Create Fem{objtype}",
            require_analysis=False,
        )
        try:
            self._make_object(
                document,
                analysis,
                objtype,
                source,
                add_to_analysis=False,
            )
            document.recompute()
            _close_exact_transaction(document, transaction_id, False)
            expandParentObject()
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise

    def add_filter_set_edit(self, filtertype):
        # like add_obj_on_gui_selobj_noset_edit but the selection is kept
        # and the selobj is expanded in the tree to see the added obj

        # check if we should use python filter
        from femguiutils.vtk_module_handling import vtk_compatibility_abort

        if vtk_compatibility_abort(True):
            return

        # Resolve the exact selected post group again at activation time.
        source = self.selobj
        document = _active_document()
        if not _is_live_in_document(source, document):
            return
        pipeline = _post_pipeline_for_object(source)
        group = _post_group_for_object(source, pipeline)
        if (
            not _is_live_in_document(pipeline, document)
            or not _is_live_in_document(group, document)
        ):
            return

        transaction_id = _open_exact_transaction(
            document,
            f"Create Fem{filtertype}",
        )
        source_was_visible = bool(source.ViewObject.Visibility)
        try:
            FreeCADGui.addModule("ObjectsFem")
            created = FreeCADGui.runDocumentObjectCommand(
                document,
                f"ObjectsFem.make{filtertype}"
                f"({_document_expression(document)},"
                f" {_object_expression(group)})",
            )
            _require_provisional_timeline_identity(
                created,
                document,
                "The FEM post-filter factory",
            )
            if (
                group not in created.InList
                or _post_pipeline_for_object(created) is not pipeline
            ):
                raise RuntimeError(
                    "The post filter was not added to its pipeline"
                )
            created.ViewObject.DisplayMode = "Surface"
            created.ViewObject.SelectionStyle = "BoundBox"
            created.ViewObject.NoneFieldColor = (
                source.ViewObject.NoneFieldColor
            )
            if source_was_visible:
                FreeCADGui.addModule("femcommands.manager")
                FreeCADGui.doCommand(
                    "femcommands.manager."
                    "_mark_timeline_replaced_inputs("
                    f"{_object_expression(created)}, "
                    f"[{_object_expression(source)}])"
                )
            source.ViewObject.Visibility = False
            created.recompute()
            expandParentObject()
            FreeCADGui.Selection.clearSelection()
            self._start_edit(document, created)
        except Exception:
            _close_exact_transaction(document, transaction_id, True)
            raise
