# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from PropertyContainer import PropertyContainer
from DocumentObject import DocumentObject
from typing import TYPE_CHECKING, Final, Literal, Sequence, overload

if TYPE_CHECKING:
    from Part import Feature as _PartFeature


class Document(PropertyContainer):
    """
    This is the Document class.
    """

    DependencyGraph: Final[str] = ""
    """The dependency graph as GraphViz text"""

    ActiveObject: Final[DocumentObject] = None
    """The last created object in this document"""

    Objects: Final[list[DocumentObject]] = []
    """The list of objects in this document"""

    TopologicalSortedObjects: Final[list[DocumentObject]] = []
    """The list of objects in this document in topological sorted order"""

    RootObjects: Final[list[DocumentObject]] = []
    """The list of root objects in this document"""

    RootObjectsIgnoreLinks: Final[list[DocumentObject]] = []
    """The list of root objects in this document ignoring references from links."""

    UndoMode: int = 0
    """The Undo mode of the Document (0 = no Undo, 1 = Undo/Redo)"""

    UndoRedoMemSize: Final[int] = 0
    """The size of the Undo stack in byte"""

    UndoCount: Final[int] = 0
    """Number of possible Undos"""

    RedoCount: Final[int] = 0
    """Number of possible Redos"""

    UndoNames: Final[list[str]] = []
    """A list of Undo names"""

    RedoNames: Final[list[str]] = []
    """A List of Redo names"""

    Name: Final[str] = ""
    """The internal name of the document"""

    RecomputesFrozen: bool = False
    """Returns or sets if automatic recomputes for this document are disabled."""

    HasPendingTransaction: Final[bool] = False
    """Check if there is a pending transaction"""

    InList: Final[list[Document]] = []
    """A list of all documents that link to this document."""

    OutList: Final[list[Document]] = []
    """A list of all documents that this document links to."""

    Restoring: Final[bool] = False
    """Indicate if the document is restoring"""

    Partial: Final[bool] = False
    """Indicate if the document is partially loaded"""

    Importing: Final[bool] = False
    """Indicate if the document is importing. Note the document will also report Restoring while importing"""

    Recomputing: Final[bool] = False
    """Indicate if the document is recomputing"""

    RecomputePending: Final[bool] = False
    """Indicate if an asynchronous recompute is queued or executing"""

    Transacting: Final[bool] = False
    """Indicate whether the document is undoing/redoing"""

    OldLabel: Final[str] = ""
    """Contains the old label before change"""

    Temporary: Final[bool] = False
    """Check if this is a temporary document"""

    def save(self) -> None:
        """
        Save the document to disk.
        """
        ...

    def saveAs(self, path: str, /) -> None:
        """
        Save the document under a new name to disk.
        """
        ...

    def saveCopy(self, path: str, /) -> None:
        """
        Save a copy of the document under a new name to disk.
        """
        ...

    def canWriteRecoverySnapshot(self) -> bool:
        """
        Return whether the document is in an App-side state that allows writing
        a recovery snapshot.

        This does not account for GUI-only constraints such as an active Gui
        transaction.
        """
        ...

    def load(self, path: str, /) -> None:
        """
        Load the document from the given path.
        """
        ...

    def restore(self) -> None:
        """
        Restore the document from disk
        """
        ...

    def isSaved(self) -> bool:
        """
        Checks if the document is saved
        """
        ...

    def getProgramVersion(self) -> str:
        """
        Get the program version that a project file was created with
        """
        ...

    def getFileName(self) -> str:
        """
        For a regular document it returns its file name property.
        For a temporary document it returns its transient directory.
        """
        ...

    def getUniqueObjectName(self, objName: str, /) -> str:
        """
        Return the same name, or the name made unique, for Example Box -> Box002 if there are conflicting name
        already in the document.

        Args:
            objName: Object name candidate.

        Returns:
            Unique object name based on objName.
        """
        ...

    def mergeProject(self, path: str, /) -> None:
        """
        Merges this document with another project file.
        """
        ...

    def exportGraphviz(self, path: str = None, /) -> str | None:
        """
        Export the dependencies of the objects as graph.

        If path is passed, graph is written to it. if not a string is returned.
        """
        ...

    def openTransaction(self, name: str, /) -> None:
        """
        Open a new Undo/Redo transaction.

        This function no long creates a new transaction, but calls
        FreeCAD.setActiveTransaction(name) instead, which will auto creates a
        transaction with the given name when any change happened in any opened document.
        If more than one document is changed, all newly created transactions will have
        the same internal ID and will be undo/redo together.
        """
        ...

    def abortTransaction(self) -> None:
        """
        Abort an Undo/Redo transaction (rollback)
        """
        ...

    def commitTransaction(self) -> None:
        """
        Commit an Undo/Redo transaction
        """
        ...

    @overload
    def addObject(
        self,
        type: Literal["Part::Feature"],
        name: str = None,
        objProxy: object = None,
        viewProxy: object = None,
        attach: bool = False,
        viewType: str = None,
    ) -> _PartFeature: ...

    @overload
    def addObject(
        self,
        type: str,
        name: str = None,
        objProxy: object = None,
        viewProxy: object = None,
        attach: bool = False,
        viewType: str = None,
    ) -> DocumentObject: ...

    def addObject(
        self,
        type: str,
        name: str = None,
        objProxy: object = None,
        viewProxy: object = None,
        attach: bool = False,
        viewType: str = None,
    ) -> DocumentObject:
        """
        Add an object to document.

        Args:
            type: the type of the document object to create.
                  Call method supportedTypes() to get a list of possible values.
            name: the optional name of the new object.
            objProxy: the Python binding object to attach to the new document object.
            viewProxy: the Python binding object to attach the view provider of this object.
            attach: if True, then bind the document object first before adding to the document
                    to allow Python code to override view provider type. Once bound, and before adding to
                    the document, it will try to call Python binding object's attach(obj) method.
            viewType: override the view provider type directly, only effective when attach is False.
        """
        ...

    def addProperty(
        self,
        type: str,
        name: str,
        group: str = "",
        doc: str = "",
        attr: int = 0,
        read_only: bool = False,
        hidden: bool = False,
        locked: bool = False,
        enum_vals: list[str] | None = None,
    ) -> Document:
        """
        Add a generic property.

        Args:
            type: The type of the property to add.
            name: The name of the property.
            group: The group to which the property belongs. Defaults to "".
            doc: The documentation string for the property. Defaults to "".
            attr: Attribute flags for the property. Defaults to 0.
            read_only: Whether the property is read-only. Defaults to False.
            hidden: Whether the property is hidden. Defaults to False.
            locked: Whether the property is locked. Defaults to False.

        Returns:
            The document instance with the added property.
        """
        ...

    def removeProperty(self, name: str, /) -> None:
        """
        Remove a generic property.

        Note, you can only remove user-defined properties but not built-in ones.
        """
        ...

    def removeObject(self, name: str, /) -> None:
        """
        Remove an object from the document.
        """
        ...

    @overload
    def copyObject(
        self,
        object: Sequence[DocumentObject],
        recursive: bool = False,
        return_all: bool = False,
    ) -> tuple[DocumentObject, ...]: ...

    @overload
    def copyObject(
        self,
        object: DocumentObject,
        recursive: bool = False,
        return_all: Literal[False] = False,
    ) -> DocumentObject: ...

    @overload
    def copyObject(
        self,
        object: DocumentObject,
        recursive: bool = False,
        return_all: Literal[True] = True,
    ) -> DocumentObject | tuple[DocumentObject, ...]: ...

    def copyObject(
        self,
        object: DocumentObject | Sequence[DocumentObject],
        recursive: bool = False,
        return_all: bool = False,
    ) -> DocumentObject | tuple[DocumentObject, ...]:
        """
        Copy an object or objects from another document to this document.

        Args:
            object: can either be a single object or sequence of objects
            recursive: if True, also recursively copies internal objects
            return_all: if True, returns all copied objects, or else return only the copied
                        object corresponding to the input objects.
        """
        ...

    def moveObject(
        self,
        object: DocumentObject,
        with_dependencies: bool = False,
        /,
    ) -> DocumentObject:
        """
        Transfers an object from another document to this document.

        Args:
            object: can either a single object or sequence of objects
            with_dependencies: if True, all internal dependent objects are copied too.
        """
        ...

    def importLinks(
        self,
        object: DocumentObject = None,
        /,
    ) -> tuple[DocumentObject, ...]:
        """
        Import any externally linked object given a list of objects in
        this document.  Any link type properties of the input objects
        will be automatically reassigned to the imported object

        If no object is given as input, it import all externally linked
        object of this document.
        """
        ...

    def undo(self) -> None:
        """
        Undo one transaction
        """
        ...

    def redo(self) -> None:
        """
        Redo a previously undone transaction
        """
        ...

    def clearUndos(self) -> None:
        """
        Clear the undo stack of the document
        """
        ...

    def clearDocument(self) -> None:
        """
        Clear the whole document
        """
        ...

    def setClosable(self, closable: bool, /) -> None:
        """
        Set a flag that allows or forbids to close a document
        """
        ...

    def isClosable(self) -> bool:
        """
        Check if the document can be closed. The default value is True
        """
        ...

    def setAutoCreated(self, autoCreated: bool, /) -> None:
        """
        Set a flag that indicates if a document is autoCreated
        """
        ...

    def isAutoCreated(self) -> bool:
        """
        Check if the document is autoCreated. The default value is False
        """
        ...

    def recompute(
        self,
        objs: Sequence[DocumentObject] = None,
        force: bool = False,
        check_cycle: bool = False,
        /,
    ) -> int:
        """
        Recompute the document and returns the amount of recomputed features.
        """
        ...

    def recomputeAsync(
        self,
        objs: Sequence[DocumentObject] = None,
        recursive: bool = False,
        /,
    ) -> int:
        """
        Queue worker-safe recompute work and return the number of queued requests.

        This method never falls back to the caller or GUI thread. It raises when
        any requested object has a thread-affine recompute implementation.
        """
        ...

    def getRecomputeDiagnostics(self) -> dict:
        """
        Return the generation and structured diagnostics from the latest recompute.
        """
        ...

    def mustExecute(self) -> bool:
        """
        Check if any object must be recomputed
        """
        ...

    def purgeTouched(self) -> None:
        """
        Purge the touched state of all objects
        """
        ...

    def isTouched(self) -> bool:
        """
        Check if any object is in touched state
        """
        ...

    def getObject(self, name: str, /) -> DocumentObject:
        """
        Return the object with the given name
        """
        ...

    def getObjectsByLabel(self, label: str, /) -> list[DocumentObject]:
        """
        Return the objects with the given label name.

        NOTE: It's possible that several objects have the same label name.
        """
        ...

    def findObjects(
        self,
        Type: str = None,
        Name: str = None,
        Label: str = None,
    ) -> list[DocumentObject]:
        """
        Return a list of objects that match the specified type, name or label.

        Name and label support regular expressions. All parameters are optional.

        Args:
            Type: Type of the feature.
            Name: Name
            Label: Label
        """
        ...

    def getLinksTo(
        self,
        obj: DocumentObject,
        options: int = 0,
        maxCount: int = 0,
        /,
    ) -> tuple[DocumentObject, ...]:
        """
        Return objects linked to 'obj'

        Args:
            options: 1: recursive, 2: check link array. Options can combine.
            maxCount: to limit the number of links returned.
        """
        ...

    def supportedTypes(self) -> list[str]:
        """
        A list of supported types of objects
        """
        ...

    def getTempFileName(self) -> str:
        """
        Returns a file name with path in the temp directory of the document.
        """
        ...

    def getDependentDocuments(self, sort: bool = True, /) -> list[DocumentObject]:
        """
        Returns a list of documents that this document directly or indirectly links to including itself.

        Args:
            sort: whether to topologically sort the return list
        """
        ...

    def getBookedTransactionID(self) -> int:
        """
        getBookedTransactionID() -> int

        Returns the currently booked transaction id, which is the id of the current transaction OR the id
        the next transaction will stick to if no change has occurred yet
        """
        ...

    def reorderTimelineOperationBlocksAfter(
        self,
        operations: Sequence[DocumentObject],
        target: DocumentObject,
        /,
    ) -> bool:
        """
        Move complete semantic timeline blocks after the target block.

        The caller must own the document's active transaction and the history
        marker must be at the current end. Each operation moves together with
        all recursively owned resources. Invalid ownership or a resulting
        forward dependency is rejected without changing the timeline.

        Returns:
            True when the order changed, or False when it already matched.
        """
        ...

    def reorderTimelineOperationBlocksBefore(
        self,
        operations: Sequence[DocumentObject],
        target: DocumentObject,
        /,
    ) -> bool:
        """
        Move complete semantic timeline blocks before the target block.

        The caller must own the document's active transaction and the history
        marker must be at the current end. Each operation moves together with
        all recursively owned resources. Invalid ownership or a resulting
        forward dependency is rejected without changing the timeline.

        Returns:
            True when the order changed, or False when it already matched.
        """
        ...

    def adoptImportedTimelineOperations(
        self,
        objects: Sequence[DocumentObject],
        source_order: Sequence[DocumentObject] = (),
        source_visibility: Sequence[bool] = (),
        source_suppression: Sequence[bool] = (),
        /,
    ) -> None:
        """
        Adopt fully restored objects into the native document timeline.

        Call this once, inside the caller-owned transaction, after the
        imported objects have their final grouping, visibility, owner, editor,
        and replacement metadata. ``source_order`` must contain the imported
        identities in the source document's timeline order. When supplied,
        ``source_visibility`` and ``source_suppression`` are the parallel
        accepted end-of-history states from that source timeline. Temporary
        documents deliberately ignore this operation.
        """
        ...

    def finalizeProvisionalTimelineOperationBlock(
        self,
        operation: DocumentObject,
        ordered_new_objects: Sequence[DocumentObject],
        /,
    ) -> None:
        """
        Finalize one native command's provisionally enrolled semantic block.

        Call this after role, owner, editor, replacement, grouping, and display
        metadata are final, but before committing the caller-owned
        transaction. Every ordered object must have been auto-enrolled by that
        same transaction. A newly created operation is listed last; for an
        existing active operation, list only its newly created resources.
        """
        ...

    def publishProvisionalTimelineOperationBlock(
        self,
        operation: DocumentObject,
        ordered_resources: Sequence[DocumentObject],
        resource_owners: Sequence[DocumentObject] = (),
        /,
    ) -> None:
        """
        Atomically publish one exact current-transaction semantic block.

        Use this for newly created groups or containers which are not eligible
        for History until their semantic metadata exists. Every object must
        have exact creation provenance from the current caller-owned
        transaction. ``ordered_resources`` is canonical nested post-order.
        ``resource_owners`` is either empty (all resources belong directly to
        ``operation``) or exactly parallel and explicitly names each resource
        owner. Rejection changes neither metadata nor History.
        """
        ...

    def adoptExistingTimelineOperationBlock(
        self,
        operation: DocumentObject,
        ordered_resources: Sequence[DocumentObject],
        resource_owners: Sequence[DocumentObject] = (),
        /,
    ) -> None:
        """
        Adopt one exact pre-existing semantic operation/resource block.

        Every identity must be a pre-existing independent History operation
        and the supplied identities must occupy one contiguous segment wholly
        before or after the current marker. ``ordered_resources`` is canonical
        nested post-order. ``resource_owners`` is either empty (all resources
        belong directly to ``operation``) or exactly parallel. The operation
        preserves object identities, display state, suppression, and marker
        position while atomically applying role, owner, and canonical block
        order.
        """
        ...

    def classifyProvisionalTimelineInternalObject(
        self,
        object: DocumentObject,
        /,
    ) -> None:
        """
        Remove one exact same-transaction provisional object from History.

        The object becomes persistent internal document state. Its role is
        hidden and locked. Changing that same role back to ``"operation"``
        before the transaction ends enrolls the exact identity again at the
        active-history boundary. Invalid identity or transaction input does
        not change the timeline.
        """
        ...

    def classifyExistingTimelineLeafInternalObject(
        self,
        object: DocumentObject,
        /,
    ) -> None:
        """
        Reclassify one exact pre-existing standalone History leaf as internal.

        This migration-only method requires an active caller-owned
        transaction. It rejects provisional objects, owned semantic blocks,
        and objects with replacement or editor contracts.
        """
        ...

    def isProvisionallyEnrolledInTimelineByCurrentTransaction(
        self,
        object: DocumentObject,
        /,
    ) -> bool:
        """
        Return whether ``object`` is an exact current-transaction enrollment.

        ``object`` must be a live object in this document. This read-only
        query lets a caller validate ownership before assigning semantic role
        or owner metadata; it never weakens finalization validation.
        """
        ...

    def isApplyingTimelineState(self) -> bool:
        """
        Return whether native History is applying a validated state change.

        View providers can use this during visibility callbacks to avoid
        overriding the exact child states being restored by History.
        """
        ...

    def isObjectUsableAtCurrentTimelinePosition(
        self,
        object: DocumentObject,
        /,
    ) -> bool:
        """
        Return whether ``object`` is usable at this document's History marker.

        The exact object must still belong to this document. Future,
        suppressed, explicitly internal, and malformed semantic-resource
        objects return ``False``. Visibility is irrelevant. Links are not
        followed; callers which consume a linked definition must validate
        that exact definition in its own document as well.
        """
        ...

    def stageTimelineOperationSegmentReplacement(
        self,
        old_root_segments: Sequence[Sequence[DocumentObject]],
        /,
    ) -> None:
        """
        Stage exact chronological semantic segments before deleting them.

        Each inner sequence declares one contiguous segment of adjacent,
        canonical resource-first/root-last blocks. Segments must be disjoint
        and supplied in document-history order.
        """
        ...

    def finalizeProvisionalTimelineOperationSegmentReplacement(
        self,
        mappings: Sequence[
            tuple[
                int,
                Sequence[Sequence[DocumentObject]],
                Sequence[int],
                Sequence[int],
                int,
            ]
        ],
        /,
    ) -> None:
        """
        Atomically finalize an exact many-to-many staged segment replacement.

        Every mapping tuple is:
        ``(staged_segment_index, ordered_new_blocks,
        state_source_indices, consumer_replacement_indices,
        active_root_count)``.

        New blocks are canonical resource-first/root-last sequences.
        ``state_source_indices`` parallels their flattened members and uses an
        old flattened member index or ``-1`` for accepted live state.
        ``consumer_replacement_indices`` parallels old flattened members and
        names the flattened new target already used by every retained direct
        consumer; ``-1`` is allowed only when no retained consumer existed.
        ``active_root_count`` is ``-1`` for wholly active/future segments and
        otherwise explicitly states the new active-root boundary.
        """
        ...

    def stageTimelineOperationResourceReconciliation(
        self,
        owner: DocumentObject,
        old_resource_roots: Sequence[DocumentObject],
        /,
    ) -> None:
        """
        Stage one owner's complete pre-existing resource graph.

        ``owner`` must be one pre-existing tracked semantic root.
        ``old_resource_roots`` lists disjoint canonical subtree roots, in
        history order, whose recursive expansion covers every resource owned
        by ``owner``. Staging is read-only and requires one caller-owned
        transaction.
        """
        ...

    def finalizeProvisionalTimelineOperationResourceReconciliation(
        self,
        owner: DocumentObject,
        ordered_final_resources: Sequence[DocumentObject],
        state_source_indices: Sequence[int],
        consumer_replacement_indices: Sequence[int],
        /,
    ) -> None:
        """
        Atomically reconcile one surviving owner's complete resource graph.

        ``ordered_final_resources`` is the exact final canonical nested
        resource-first/owner-last graph before ``owner``. Every member is
        either the same staged old identity or an exact current-transaction
        provisional identity. ``state_source_indices`` is parallel to the
        final graph and uses a staged old-resource index or ``-1`` for
        accepted live state. ``consumer_replacement_indices`` is parallel to
        the staged old graph and names the final-resource index already used
        by each old identity's retained consumers; ``-1`` is valid only when
        no retained consumer existed. Omitted old identities become internal,
        owner-null resources which the caller may safely delete afterward.
        """
        ...

    def semanticTimelineCopyClosure(
        self,
        objects: Sequence[DocumentObject],
        /,
    ) -> tuple[DocumentObject, ...]:
        """
        Return complete tracked semantic blocks in document history order.

        Every input must be a live object in this document and already belong
        to its native timeline. Selected resources resolve to their visible
        operation; recursively owned resources and declared replacement-input
        blocks are included. This method is read-only.
        """
        ...
