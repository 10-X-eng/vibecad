// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 *                                                                         *
 ***************************************************************************/

#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include "DocumentObject.h"
#include "PropertyLinks.h"
#include "PropertyStandard.h"

namespace App
{

class Document;

/**
 * One explicit many-to-many semantic timeline segment replacement.
 *
 * stagedSegmentIndex identifies one exact segment previously staged on the
 * document. orderedNewBlocks contains its replacement semantic blocks, each
 * in canonical resource-first, root-last order.
 *
 * stateSourceIndices is parallel to the flattened orderedNewBlocks. Each
 * non-negative entry identifies one member of the flattened staged old
 * segment whose accepted visibility/suppression state is copied to that new
 * member; -1 retains the newly accepted member state. State indices may
 * repeat because display state is not an identity claim.
 *
 * consumerReplacementIndices is parallel to the flattened staged old
 * segment. Each entry identifies the one flattened new member to which every
 * retained direct consumer of that old identity has already been relinked.
 * -1 is valid only when staging proved that the old identity had no retained
 * direct consumers.
 *
 * activeRootCount is -1 when the staged segment was wholly active or wholly
 * future; the core derives the invariant result. When the old marker lay
 * between roots inside the segment, activeRootCount explicitly declares how
 * many replacement root blocks remain active.
 */
struct AppExport TimelineSegmentReplacementMapping
{
    std::size_t stagedSegmentIndex {0};
    std::vector<std::vector<DocumentObject*>> orderedNewBlocks;
    std::vector<long> stateSourceIndices;
    std::vector<long> consumerReplacementIndices;
    long activeRootCount {-1};
};

/**
 * Exact final resource graph for one surviving semantic operation.
 *
 * orderedFinalResources is the complete caller-declared canonical resource
 * graph before owner. Each member must be either the same live identity from
 * the staged old graph or an exact current-transaction provisional identity.
 * stateSourceIndices is parallel and uses a flattened old-resource index or
 * -1 for accepted live state. consumerReplacementIndices is parallel to the
 * flattened old graph and names the final-resource index already used by
 * every retained direct consumer.
 *
 * consumerReplacementObjects is optional. When present, it is also parallel
 * to the flattened old graph. A non-null object is an exact live replacement
 * outside this owner's final resource graph and requires the corresponding
 * consumerReplacementIndices entry to be -1. A null object falls back to the
 * indexed mapping. Both forms may be absent only when the old resource had no
 * retained consumer. This supports operations which stop affecting one Body:
 * downstream consumers can be relinked to that Body's exact preceding state
 * without falsely making the preceding state a resource of the edited
 * operation.
 */
struct AppExport TimelineResourceReconciliationMapping
{
    DocumentObject* owner {nullptr};
    std::vector<DocumentObject*> orderedFinalResources;
    std::vector<long> stateSourceIndices;
    std::vector<long> consumerReplacementIndices;
    std::vector<DocumentObject*> consumerReplacementObjects;
};

/**
 * Persistent, document-wide modeling history.
 *
 * The object intentionally has no ViewProvider.  It is a native document
 * object because link properties can only restore reliably when their
 * container belongs to a document.  Operations, VisibilityAtEnd, and
 * SuppressionAtEnd are parallel arrays; Position is a boundary in the
 * inclusive range [0, N].
 */
class AppExport DocumentTimeline: public DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::DocumentTimeline);

public:
    static constexpr const char* ObjectName = "VibeCADTimeline";
    static constexpr long CurrentSchemaVersion = 2;
    static constexpr long CurrentDesignSchemaVersion = 1;
    static constexpr const char* RolePropertyName = "VibeCADTimelineRole";
    static constexpr const char* OwnerPropertyName = "VibeCADTimelineOwner";
    static constexpr const char* EditorPropertyName = "VibeCADTimelineEditor";
    static constexpr const char* EditCommandPropertyName = "VibeCADTimelineEditCommand";
    static constexpr const char* DeleteCommandPropertyName = "VibeCADTimelineDeleteCommand";
    static constexpr const char* ReplacedInputsPropertyName = "VibeCADTimelineReplacedInputs";
    static constexpr const char* DefinitionIdPropertyName = "VibeCADDefinitionId";
    static constexpr const char* DesignIdPropertyName = "DesignId";
    static constexpr const char* OperationRole = "operation";
    static constexpr const char* ResourceRole = "resource";
    static constexpr const char* InternalRole = "internal";

    /**
     * Persistent identity and schema of the saved Design which owns every
     * reusable definition, Body, Component, and History operation in this
     * document.
     */
    PropertyUUID DesignId;
    PropertyInteger DesignSchemaVersion;

    // Timeline properties are occurrence-local dynamic metadata. They never
    // inherit through App::Link or another forwarding property provider: a
    // linked occurrence and its source may participate in the same history
    // under independent roles, owners, editors, and inputs.

    struct ReplacementInputContract
    {
        bool declared {false};
        bool valid {true};
        std::vector<DocumentObject*> inputs;
    };

    struct TimelineDeletionPlan
    {
        bool applicable {false};
        bool valid {true};
        std::vector<DocumentObject*> replacedInputs;
        std::vector<DocumentObject*> objectsToReveal;
        std::vector<DocumentObject*> ownedResources;
    };

    DocumentTimeline();
    ~DocumentTimeline() override;

    static DocumentTimeline* get(Document* document) noexcept;
    static const DocumentTimeline* get(const Document* document) noexcept;
    static DocumentTimeline* get(Document& document) noexcept
    {
        return get(&document);
    }
    static const DocumentTimeline* get(const Document& document) noexcept
    {
        return get(&document);
    }

    static DocumentTimeline* ensure(Document* document);
    static DocumentTimeline* ensure(Document& document)
    {
        return ensure(&document);
    }

    /**
     * Assign the persistent identity shared by every Design-scope definition.
     *
     * This core entry point deliberately does not depend on a modeling
     * workbench. Sketches, datums, curves, surfaces, and other reusable
     * definitions all use the same saved identity and Design ownership
     * contract.
     */
    static void initializeDesignDefinition(DocumentObject& definition);

    /**
     * Publish one accepted Design-scope definition as one global History root.
     *
     * The caller owns the surrounding transaction and must already have
     * resolved every modeling input to an exact earlier state. Calling this
     * for an existing accepted definition validates its identity and History
     * membership without adding a second entry.
     */
    static void finalizeDesignDefinition(DocumentObject& definition);

    /**
     * Insert a newly-created user-visible operation at the current boundary
     * and advance Position by one.  Infrastructure objects are ignored.
     */
    void recordOperation(DocumentObject* operation);
    void recordOperation(DocumentObject& operation)
    {
        recordOperation(&operation);
    }

    /**
     * Record exact current-transaction creation provenance before operation
     * candidate filtering, then perform ordinary automatic enrollment.
     *
     * Document calls this once from its successful add-object path. It is
     * separate from recordOperation() because later role/owner changes must
     * never make a pre-existing identity appear newly created.
     */
    void recordCreatedObject(DocumentObject* object);

    /**
     * Remove an object from the persisted operation sequence before its
     * document teardown begins.
     *
     * Document calls this while the object is still fully attached.  Doing
     * the timeline-specific bookkeeping at that point keeps the parallel
     * state arrays exact and prevents generic link cleanup from having to
     * inspect an object whose properties are already being dismantled.
     */
    void forgetOperation(DocumentObject* operation);

    /**
     * Move complete semantic operation blocks after one target block.
     *
     * Each requested object is resolved to its outermost operation owner.
     * That root and every recursively owned resource move together while
     * preserving their existing relative order. The target resolves the same
     * way and insertion occurs after its complete block. The operation is
     * accepted only at the full-history boundary and only when the resulting
     * global chronology contains no forward dependency.
     *
     * The caller owns the surrounding transaction. Invalid identities,
     * ownership, timeline state, or dependency order throw without changing
     * Operations. A false result means the requested order was already
     * present.
     */
    bool reorderOperationBlocksAfter(
        const std::vector<DocumentObject*>& operations,
        DocumentObject* target
    );

    /**
     * Move complete semantic operation blocks before one target block.
     *
     * This is the beginning-boundary counterpart to
     * reorderOperationBlocksAfter(). It preserves complete ownership blocks
     * and applies the same transaction, full-history, identity, and
     * dependency validation.
     */
    bool reorderOperationBlocksBefore(
        const std::vector<DocumentObject*>& operations,
        DocumentObject* target
    );

    /**
     * Move one semantic operation and every tracked operation which depends
     * on it after one newly required dependency.
     *
     * This is the safe rebase form for a persistent operation whose native
     * resource graph acquires a dependency created later in the same
     * document. The complete downstream semantic closure moves with the
     * operation, while unrelated intervening blocks keep their position.
     * Structural group membership is not treated as an execution dependency.
     *
     * The same transaction, full-history, identity, ownership, and chronology
     * requirements as reorderOperationBlocksAfter() apply.
     */
    bool reorderOperationDependentClosureAfter(DocumentObject* operation, DocumentObject* target);

    /**
     * Return the complete tracked semantic closure required to copy objects.
     *
     * Selected resources resolve to their outermost operation. Every root is
     * returned with all recursively owned resources and every recursively
     * declared replacement-input block. Role, owner, editor, and replacement
     * metadata must be correctly typed, live, acyclic, and confined to this
     * document. The result follows the persisted timeline order.
     *
     * This closure covers timeline semantics only. A copy/export caller must
     * alternate it with Document::getDependencyList() until the exact object
     * set reaches a fixed point: ordinary dependencies may own semantic
     * resources, and semantic resources may introduce ordinary dependencies.
     */
    [[nodiscard]] std::vector<DocumentObject*> semanticCopyClosure(
        const std::vector<DocumentObject*>& selectedObjects
    ) const;

    /**
     * Adopt one completely restored import into the current history boundary.
     *
     * importedObjects is the exact set created by the import. sourceOrder is
     * the subset which participated in the source document timeline, already
     * mapped to target-document identities and kept in source chronology.
     * Ordinary imported candidates which predate the timeline contract are
     * added deterministically after that explicit sequence.
     *
     * The complete graph and final parallel arrays are validated before any
     * mutation. Role, owner, editor, and replacement properties are accepted
     * only when their entire semantic block was imported. The caller owns one
     * surrounding transaction and must call this only after final grouping,
     * placement, visibility, and semantic metadata have been established.
     * Optional sourceVisibility/sourceSuppression arrays preserve the
     * source's accepted end-of-history state even when its live marker was
     * rolled back; both arrays must exactly parallel sourceOrder.
     *
     * Setting that final metadata may synchronously auto-enroll copied
     * objects. Only enrollments proven to have been inserted by this same
     * active transaction are removed from the parallel arrays and replaced at
     * their first original marker. Copy-import creation proof is consumed when
     * the import owns its complete pending generation; if unrelated creations
     * still share that generation, adopted operations retain exact provisional
     * markers so the earlier proof can be published in order. A pre-existing
     * overlap always rejects the import.
     */
    void adoptImportedOperations(
        const std::vector<DocumentObject*>& importedObjects,
        const std::vector<DocumentObject*>& sourceOrder = {},
        const std::vector<bool>& sourceVisibility = {},
        const std::vector<bool>& sourceSuppression = {}
    );

    /**
     * Finalize one native command's provisionally enrolled semantic block.
     *
     * Every object in orderedNewObjects must have been automatically enrolled
     * by this same still-active caller transaction. For a new operation, the
     * operation itself must be the final ordered object. For an existing
     * active operation, orderedNewObjects contains only newly created owned
     * resources; existing resources keep their relative order and the root is
     * moved to the canonical block tail.
     *
     * orderedStagedResources is accepted only for a new root and must match
     * one exact selection previously passed to
     * stageExistingOperationResources() in the same transaction.
     *
     * Pre-existing replacement inputs are allowed. The complete ownership and
     * dependency graph, retained future, parallel state arrays, and final
     * history boundary are validated before mutation. Invalid input throws
     * without changing the timeline.
     */
    void finalizeProvisionalOperationBlock(
        DocumentObject* operation,
        const std::vector<DocumentObject*>& orderedNewObjects
    );
    void finalizeProvisionalOperationBlock(
        DocumentObject* operation,
        const std::vector<DocumentObject*>& orderedNewObjects,
        const std::vector<DocumentObject*>& orderedStagedResources
    );

    /**
     * Atomically publish one exact current-transaction semantic block.
     *
     * This direct path supports newly created container/group objects which
     * are intentionally excluded from automatic enrollment until semantic
     * role metadata exists. orderedResources is canonical nested post-order.
     * resourceOwners is either empty (every resource is directly owned by
     * operation) or exactly parallel to orderedResources. The entire
     * pre-creation timeline, creation provenance, declared ownership graph,
     * metadata types, dependency order, state, and marker are validated
     * before role, owner, enrollment, or timeline state is changed.
     */
    void publishProvisionalOperationBlock(
        DocumentObject* operation,
        const std::vector<DocumentObject*>& orderedResources,
        const std::vector<DocumentObject*>& resourceOwners = {}
    );

    /**
     * Adopt one contiguous block of pre-existing independent operations as
     * the exact semantic resource graph of one pre-existing root.
     *
     * Every identity must already be tracked exactly once, predate the
     * caller-owned transaction, and occupy one unsplit History segment.
     * orderedResources is canonical nested post-order. resourceOwners is
     * either empty (every resource belongs directly to operation) or exactly
     * parallel to orderedResources. The same identities and marker side are
     * preserved while semantic metadata and canonical resource-first,
     * root-last order are applied atomically.
     */
    void adoptExistingOperationBlock(
        DocumentObject* operation,
        const std::vector<DocumentObject*>& orderedResources,
        const std::vector<DocumentObject*>& resourceOwners = {}
    );

    /**
     * Stage an exact set of existing operations for adoption by a newly
     * created provisional operation.
     *
     * This is intentionally separate from finalization. A native task must
     * call it in the same transaction, before changing any selected object's
     * timeline role or owner. Only active, independent operations with exact
     * live identities and no earlier semantic consumer can be staged. The
     * later finalization call must present the same objects in the same order
     * after the task has made them resources of provisionalOperation.
     */
    void stageExistingOperationResources(
        DocumentObject* provisionalOperation,
        const std::vector<DocumentObject*>& selectedOperations
    );

    /**
     * Remove one exact same-transaction provisional object from History and
     * classify it as persistent internal document state.
     *
     * The object's role property is hidden, locked, and excluded from
     * operation discovery. Reclassifying that same identity as an operation
     * before the transaction ends enrolls it again at the active-history
     * boundary.
     */
    void classifyProvisionalInternalObject(DocumentObject* object);

    /**
     * Reclassify one exact pre-existing standalone leaf operation as
     * persistent internal state.
     *
     * This migration-only path never accepts a current-transaction
     * provisional identity and never consumes an owned semantic block.
     */
    void classifyExistingLeafInternalObject(DocumentObject* object);

    /**
     * Retire one exact pre-existing semantic block into persistent internal
     * state without deleting or replacing any document identity.
     *
     * This migration-only path accepts the explicit root of one canonical
     * resource-first/root-last block.  The complete block is removed from
     * History atomically, resource ownership and editor metadata are cleared,
     * and every retained member receives the internal role.  A marker inside
     * the block is rejected.
     */
    void classifyExistingSemanticBlockInternal(DocumentObject* operation);

    /**
     * Stage exact live canonical semantic blocks for many-to-many replacement.
     *
     * Roots must be distinct, independent, supplied in document-history
     * order, and each resource-first/root-last block must lie wholly on one
     * side of the current marker. Staging is read-only and records exact
     * document, transaction, identity, chronology, state, and retained direct
     * consumer evidence.
     */
    void stageOperationSegmentReplacement(
        const std::vector<std::vector<DocumentObject*>>& oldRootSegments
    );

    /**
     * Atomically replace every staged semantic segment.
     *
     * Every old identity must have been deleted and every replacement member
     * must be an exact current-transaction provisional object. Each mapping
     * may replace any number of adjacent old root blocks with any number of
     * canonical new root blocks. Retained consumers must already point to the
     * explicitly mapped replacement identity.
     */
    void finalizeProvisionalOperationSegmentReplacement(
        const std::vector<TimelineSegmentReplacementMapping>& mappings
    );

    /**
     * Stage the complete selected resource subtrees of one surviving owner
     * for exact retained/new/retired identity-set reconciliation.
     *
     * oldResourceRoots names disjoint roots whose canonical nested subtrees
     * expand to the owner's complete pre-existing resource graph. The owner,
     * transaction, raw chronology, parallel state, marker, and retained
     * direct consumers are captured without mutation.
     */
    void stageOperationResourceReconciliation(
        DocumentObject* owner,
        const std::vector<DocumentObject*>& oldResourceRoots
    );

    /**
     * Reconcile one surviving owner's complete resource graph.
     *
     * orderedFinalResources is the complete canonical nested graph before the
     * surviving owner. Retained staged identities keep their document
     * identity, new identities require exact provisional proof, and absent
     * staged identities are retired. stateSourceIndices is parallel to the
     * final graph; consumerReplacementIndices is parallel to the staged old
     * graph. A retired live identity is removed from History and made
     * owner-null/internal so the caller may delete it safely afterward.
     */
    void finalizeProvisionalOperationResourceReconciliation(
        const TimelineResourceReconciliationMapping& mapping
    );

    /**
     * Return whether object was inserted into the timeline by the document's
     * current still-active transaction.
     */
    [[nodiscard]] bool isProvisionallyEnrolledByCurrentTransaction(const DocumentObject* object
    ) const noexcept;

    /**
     * Return whether object belongs to an exact semantic block published by
     * this document's current still-active transaction.
     *
     * Direct publication consumes the earlier automatic-enrollment and
     * creation proofs. This independent proof remains valid only until the
     * transaction closes and only while the complete published block retains
     * its exact identities, order, roles, and direct owner graph.
     */
    [[nodiscard]] bool isSemanticallyPublishedByCurrentTransaction(const DocumentObject* object
    ) const noexcept;

    /**
     * Return whether orderedBlock is one exact semantic block published by
     * this document's current still-active transaction.
     *
     * orderedBlock must be canonical resource-first/root-last order and
     * operation must be its final member. The check fails closed if any live
     * identity, timeline position, role, or direct owner differs from the
     * atomically published state.
     */
    [[nodiscard]] bool isExactSemanticBlockPublishedByCurrentTransaction(
        const DocumentObject* operation,
        const std::vector<DocumentObject*>& orderedBlock
    ) const noexcept;

    /**
     * Return whether an object is on or before the current history boundary.
     *
     * Objects which are not timeline operations remain active.  This keeps
     * internal references and documents created before the timeline contract
     * usable while allowing modeling domains to exclude future operations
     * from their own solvers and relationship traversal.
     */
    [[nodiscard]] bool isOperationActive(const DocumentObject* operation) const noexcept;

    /**
     * Return whether an exact object is usable at its document's current
     * history position.
     *
     * This is the shared cross-workbench input contract. The object must be
     * live in its own document, must not be explicitly internal, must be on
     * or before the current marker, and neither it nor any semantic resource
     * owner may be suppressed. Visibility is deliberately irrelevant.
     *
     * App::Link definitions are not followed: a domain which consumes both a
     * local occurrence and its external definition must validate each exact
     * object independently.
     */
    [[nodiscard]] static bool
    isObjectUsableAtCurrentPosition(const DocumentObject* object) noexcept;

    /**
     * Return the saved end-of-history visibility of an operation, including
     * the visibility and suppression state of every explicit owner.
     */
    [[nodiscard]] bool isOperationVisibleAtEnd(const DocumentObject* operation) const noexcept;

    /**
     * Return the explicit operation which owns an internal timeline resource.
     *
     * Workbenches may add the hidden dynamic RolePropertyName string and an
     * OwnerPropertyName App::PropertyLinkHidden to distinguish durable user
     * operations from the setup, cache, representation, or controller
     * objects those operations own. The hidden-link type is required so this
     * metadata cannot create a reverse dependency cycle with the owner.
     * Unmarked objects preserve the original timeline behavior.
     */
    [[nodiscard]] static const DocumentObject* timelineOwner(const DocumentObject* object) noexcept;
    [[nodiscard]] static DocumentObject* timelineOwner(DocumentObject* object) noexcept
    {
        return const_cast<DocumentObject*>(timelineOwner(static_cast<const DocumentObject*>(object)));
    }

    /**
     * Return the explicit implementation object which edits a semantic
     * operation controller.
     *
     * The editor must be linked through EditorPropertyName using
     * App::PropertyLinkHidden and must be an owned resource below the
     * controller. This allows a real multi-output controller to remain the
     * visible history step without turning Timeline Edit into an output-folder
     * no-op.
     */
    [[nodiscard]] static const DocumentObject* timelineEditor(const DocumentObject* object) noexcept;
    [[nodiscard]] static DocumentObject* timelineEditor(DocumentObject* object) noexcept
    {
        return const_cast<DocumentObject*>(timelineEditor(static_cast<const DocumentObject*>(object)));
    }

    /**
     * Return whether an object is explicitly classified as an internal
     * timeline resource, independently of whether its owner link is valid.
     *
     * This distinction keeps orphaned or malformed implementation objects
     * internal instead of silently promoting them to user operations.
     */
    [[nodiscard]] static bool hasTimelineResourceRole(const DocumentObject* object) noexcept;
    [[nodiscard]] static bool hasTimelineOperationRole(const DocumentObject* object) noexcept;
    [[nodiscard]] static bool hasTimelineInternalRole(const DocumentObject* object) noexcept;
    /**
     * Return whether resource is a live, explicitly classified resource whose
     * complete acyclic owner chain terminates at operation in the same
     * document.
     */
    [[nodiscard]] static bool isTimelineResourceOwnedBy(
        const DocumentObject* resource,
        const DocumentObject* operation
    ) noexcept;
    [[nodiscard]] static bool isOwnedResource(const DocumentObject* object) noexcept
    {
        return timelineOwner(object) != nullptr;
    }

    /**
     * Validate the explicit semantic-input contract for an operation.
     *
     * A declared contract must use PropertyLinkListHidden on an explicitly
     * classified operation. Every input and recursively declared predecessor
     * must be a distinct live object in the same document with acyclic
     * ownership and replacement graphs. Malformed metadata fails closed.
     */
    [[nodiscard]] static ReplacementInputContract replacementInputContract(DocumentObject* operation);

    /**
     * Build the exact, read-only cleanup plan for deleting a durable
     * operation. This covers explicitly classified operations as well as
     * ordinary visible operations which own internal timeline resources.
     *
     * Callers own transaction boundaries and mutation. A false valid value
     * means explicit metadata was present but unsafe, so deletion must stop
     * instead of exposing or orphaning an indeterminate object graph.
     */
    [[nodiscard]] static TimelineDeletionPlan timelineDeletionPlan(DocumentObject* operation);

    /**
     * Refresh the full-history visibility and suppression baselines.  Capture
     * is intentionally disabled while the marker is rolled back.
     */
    void captureVisibility();

    /**
     * Suppress baseline capture and automatic normalization while a timeline
     * consumer applies a marker.  Calls may be nested.
     */
    void setApplying(bool applying) noexcept;
    void beginApplying() noexcept
    {
        setApplying(true);
    }
    void endApplying() noexcept
    {
        setApplying(false);
    }
    [[nodiscard]] bool isApplying() const noexcept
    {
        return _applyingDepth != 0;
    }

    /**
     * Repair persisted parallel arrays and migrate legacy documents into a
     * deterministic global sequence based on stable object IDs.
     */
    void normalizeAfterRestore();

    PropertyLinkListHidden Operations;
    PropertyInteger Position;
    PropertyBoolList VisibilityAtEnd;
    PropertyBoolList SuppressionAtEnd;
    PropertyInteger SchemaVersion;

protected:
    void onBeforeChange(const Property* property) override;
    void onChanged(const Property* property) override;
    void onUndoRedoFinished() override;

private:
    friend class Document;

    struct ProvisionalEnrollment
    {
        int transactionId {0};
        long objectId {0};
        std::string objectName;
        long insertionMarker {0};
    };

    struct StagedExistingResource
    {
        long objectId {0};
        std::string objectName;
        long timelineIndex {0};
    };

    struct StagedResourceAdoption
    {
        int transactionId {0};
        long operationId {0};
        std::string operationName;
        std::vector<StagedExistingResource> resources;
    };

    struct TimelineObjectIdentity
    {
        long objectId {0};
        std::string objectName;
    };

    struct ProvisionalPublicationMember
    {
        TimelineObjectIdentity object;
        TimelineObjectIdentity owner;
    };

    struct ProvisionalPublication
    {
        int transactionId {0};
        std::string documentName;
        std::string documentUid;
        TimelineObjectIdentity operation;
        std::vector<ProvisionalPublicationMember> orderedMembers;
    };

    struct ProvisionalInternalObject
    {
        int transactionId {0};
        TimelineObjectIdentity object;
    };

    struct CreationSnapshotOperation
    {
        TimelineObjectIdentity object;
        bool visibility {false};
        bool suppression {false};
    };

    struct ProvisionalTransactionCreations
    {
        int transactionId {0};
        std::string documentName;
        std::string documentUid;
        long position {0};
        std::vector<CreationSnapshotOperation> operations;
        std::vector<TimelineObjectIdentity> objects;
    };

    struct SegmentSnapshotMember
    {
        enum class HiddenConsumerKind
        {
            Editor,
            ReplacedInput,
        };

        struct HiddenConsumer
        {
            TimelineObjectIdentity consumer;
            HiddenConsumerKind kind {HiddenConsumerKind::Editor};
        };

        TimelineObjectIdentity object;
        long timelineIndex {0};
        bool visibility {false};
        bool suppression {false};
        std::vector<TimelineObjectIdentity> retainedConsumers;
        std::vector<HiddenConsumer> retainedHiddenConsumers;
    };

    struct SegmentSnapshot
    {
        std::vector<TimelineObjectIdentity> roots;
        std::vector<SegmentSnapshotMember> members;
        long activeRootCount {0};
    };

    struct TimelineSnapshotOperation
    {
        TimelineObjectIdentity object;
        bool visibility {false};
        bool suppression {false};
    };

    struct StagedSegmentReplacement
    {
        int transactionId {0};
        std::string documentName;
        std::string documentUid;
        long position {0};
        std::vector<TimelineSnapshotOperation> operations;
        std::vector<SegmentSnapshot> segments;
    };

    struct StagedResourceReconciliation
    {
        int transactionId {0};
        std::string documentName;
        std::string documentUid;
        TimelineObjectIdentity owner;
        long position {0};
        bool ownerActive {false};
        std::vector<TimelineSnapshotOperation> operations;
        std::vector<SegmentSnapshotMember> oldResources;
    };

    static bool isOperationCandidate(const DocumentObject* operation) noexcept;

    void pruneProvisionalEnrollments();
    void pruneProvisionalTransactionCreations();
    void pruneProvisionalPublications();
    void pruneStagedResourceAdoptions();
    void pruneProvisionalInternalObjects();
    void pruneStagedSegmentReplacement();
    void pruneStagedResourceReconciliation();
    void classifyTimelineLeafInternalObject(DocumentObject* object, bool requireProvisional);
    void rememberProvisionalEnrollment(const DocumentObject* operation, long insertionMarker);
    void rememberProvisionalCreation(const DocumentObject* object);
    [[nodiscard]] bool isCreatedByCurrentTransaction(const DocumentObject* object) const noexcept;
    [[nodiscard]] bool publicationMatchesLiveState(
        const ProvisionalPublication& publication,
        const std::vector<DocumentObject*>* expectedBlock
    ) const noexcept;
    void discardTransactionProvenance(int transactionId) noexcept;
    void normalizeStoredState(bool migrateLegacy);
    void reconcileOperationsChange();
    void clampPosition();
    bool reorderOperationBlocks(
        const std::vector<DocumentObject*>& operations,
        DocumentObject* target,
        bool insertBefore
    );

    unsigned int _applyingDepth {0};
    std::vector<DocumentObject*> _operationsBeforeChange;
    boost::dynamic_bitset<> _visibilityBeforeChange;
    boost::dynamic_bitset<> _suppressionBeforeChange;
    long _positionBeforeChange {0};
    bool _hasOperationsSnapshot {false};
    std::vector<ProvisionalEnrollment> _provisionalEnrollments;
    std::vector<ProvisionalTransactionCreations> _provisionalTransactionCreations;
    std::vector<ProvisionalPublication> _provisionalPublications;
    std::vector<StagedResourceAdoption> _stagedResourceAdoptions;
    std::vector<ProvisionalInternalObject> _provisionalInternalObjects;
    std::vector<StagedSegmentReplacement> _stagedSegmentReplacements;
    std::vector<StagedResourceReconciliation> _stagedResourceReconciliations;
};

}  // namespace App
