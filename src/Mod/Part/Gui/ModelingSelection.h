// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <memory>
#include <set>
#include <vector>

#include <Mod/Part/PartGlobal.h>

#include <Gui/Selection/SelectionObject.h>

namespace App
{
class Document;
class DocumentObject;
class PropertyLinkSubList;
}
namespace Part
{
class BodyBase;
}

namespace PartGui
{

enum class ModelingResultOwnership
{
    Automatic,
    DocumentRoot,
    Body
};

struct ModelingResultOwner
{
    ModelingResultOwnership ownership {ModelingResultOwnership::Automatic};
    Part::BodyBase* body {nullptr};
};

/**
 * Infer durable ownership from a result's exact dependency occurrences.
 *
 * Exact results registered by the same attempt are transparent to one
 * another: traversal continues through them until it reaches established
 * operands. Other objects created in the transaction are not inferred as
 * helpers. A root or non-Body operand, operands from different Bodies, or a
 * cross-document operand requires a document-root result. Established
 * operands all owned by one Body require that exact Body. With no established
 * operand, ownership is automatic so a primitive-like command can use its
 * captured active Body.
 */
PartGuiExport ModelingResultOwner inferModelingResultOwner(
    const App::DocumentObject& result,
    const std::set<long>& trackedResultIds
);

/// Infer ownership directly from exact command operands for shape-only results.
PartGuiExport ModelingResultOwner inferModelingOperandOwner(
    const App::Document& resultDocument,
    const std::vector<const App::DocumentObject*>& operands
);

/**
 * Prepare shape-only results using their command's exact operands.
 *
 * This is for intentionally non-parametric tools whose result stores copied
 * geometry rather than dependency properties. Parametric features should use
 * inferModelingResultOwner through ModelingTaskAttempt or ModelingContext.
 */
PartGuiExport void prepareModelingResultsForOperands(
    const std::vector<App::DocumentObject*>& results,
    const std::vector<const App::DocumentObject*>& operands
);

/**
 * Collapse every output of one multi-result command into one history step.
 *
 * A single output keeps its existing native behavior. For multiple outputs,
 * the final output is the semantic operation and every preceding output is
 * an owned resource. Output tree ownership and viewport visibility are not
 * changed.
 */
PartGuiExport void groupModelingCommandOutputs(
    const std::vector<App::DocumentObject*>& outputs
);

/**
 * Resolve the object a modeling command must use for a visible selection.
 *
 * A Part::BodyBase is a presentation container. Modeling commands consume its
 * exact modeling state rather than the Body or its presentation object. A
 * legacy Body resolves that state through Tip; a modern Body can publish a
 * Design-wide state independently. Ordinary objects and App::Link occurrences
 * are returned unchanged.
 */
PartGuiExport App::DocumentObject* resolveModelingObject(App::DocumentObject* object);
PartGuiExport const App::DocumentObject*
resolveModelingObject(const App::DocumentObject* object);

/**
 * Find the native Body represented by a modeling object.
 *
 * Direct Body members resolve through their GeoFeatureGroup owner. A link or
 * component presentation resolves through its linked definition, including a
 * component containing exactly one Body. Ambiguous multi-Body components do
 * not resolve.
 */
PartGuiExport Part::BodyBase* findModelingBody(App::DocumentObject* object) noexcept;
PartGuiExport const Part::BodyBase*
findModelingBody(const App::DocumentObject* object) noexcept;

/**
 * Resolve an operand which will be stored by a feature in \a body.
 *
 * A visible Body or same-Body component presentation resolves to the Body's
 * exact current modeling state. A presentation of a specific same-Body
 * feature resolves to that feature. Unrelated occurrences retain their exact
 * identity and placement.
 */
PartGuiExport App::DocumentObject*
resolveModelingObjectForBody(App::DocumentObject* object, const Part::BodyBase* body) noexcept;
PartGuiExport const App::DocumentObject* resolveModelingObjectForBody(
    const App::DocumentObject* object,
    const Part::BodyBase* body
) noexcept;

/**
 * Resolve an operand against an explicit result state of \a body.
 *
 * This overload is for an already-created feature whose Body Tip may now be
 * the feature itself. Body/component presentations resolve to \a bodyResult
 * instead of the current Tip, preventing an in-task reference from becoming
 * a self-reference. Specific feature links and unrelated occurrences retain
 * the same semantics as the two-argument overload.
 */
PartGuiExport App::DocumentObject* resolveModelingObjectForBody(
    App::DocumentObject* object,
    const Part::BodyBase* body,
    App::DocumentObject* bodyResult
) noexcept;
PartGuiExport const App::DocumentObject* resolveModelingObjectForBody(
    const App::DocumentObject* object,
    const Part::BodyBase* body,
    const App::DocumentObject* bodyResult
) noexcept;

/**
 * Rewrite attachment/support references which will be owned by \a body.
 *
 * This prevents a native feature from linking back through a visible
 * presentation of its own Body while preserving subelement names.
 */
PartGuiExport void
resolveModelingReferencesForBody(App::PropertyLinkSubList& references, const Part::BodyBase* body);

/**
 * Return whether an exact modeling input belongs to the current History state.
 *
 * The selected occurrence, a Body's resolved Tip, and a link's resolved
 * definition must all be live, unsuppressed, and on or before their own
 * document's current History marker.
 */
PartGuiExport bool
isModelingObjectActive(const App::DocumentObject* object) noexcept;

/**
 * Resolve the viewport object represented by a modeling operand.
 *
 * Computation consumes a Body's Tip, while visibility and external operation
 * replacement must address the Body which actually renders that Tip. A feature
 * owned by a Body therefore resolves to its Body; root objects and App::Link
 * occurrences remain unchanged.
 */
PartGuiExport App::DocumentObject*
resolveModelingPresentationObject(App::DocumentObject* object);
PartGuiExport const App::DocumentObject*
resolveModelingPresentationObject(const App::DocumentObject* object);

/**
 * Resolve and de-duplicate a list of modeling objects while preserving order.
 *
 * This is intended for task-panel object lists built by enumerating a document:
 * a Body and its Tip otherwise appear as two choices for the same rendered shape.
 */
PartGuiExport std::vector<App::DocumentObject*>
resolveModelingObjects(const std::vector<App::DocumentObject*>& objects);

/**
 * Rebind a selection from a directly selected Body to its Tip while keeping
 * every selected sub-element and viewport pick position.
 */
PartGuiExport Gui::SelectionObject
resolveModelingSelection(const Gui::SelectionObject& selection);

/// Body-aware counterpart to resolveModelingSelection().
PartGuiExport Gui::SelectionObject resolveModelingSelectionForBody(
    const Gui::SelectionObject& selection,
    const Part::BodyBase* body
);

/**
 * Resolve a selection list and omit inactive History entries and Bodies
 * without a Tip.
 */
PartGuiExport std::vector<Gui::SelectionObject>
resolveModelingSelections(const std::vector<Gui::SelectionObject>& selection);

/// Body-aware counterpart to resolveModelingSelections().
PartGuiExport std::vector<Gui::SelectionObject> resolveModelingSelectionsForBody(
    const std::vector<Gui::SelectionObject>& selection,
    const Part::BodyBase* body
);

/**
 * Return the current selection projected onto modeling inputs.
 *
 * Old-style element resolution removes tree-container prefixes while leaving
 * App::Link occurrences intact, so commands receive the picked feature and
 * its usable sub-element names without losing occurrence placement. A Body
 * selected directly is then projected onto its Tip. Empty Bodies are omitted.
 */
PartGuiExport std::vector<Gui::SelectionObject>
getModelingSelection(const char* documentName = nullptr);

/**
 * Return current selections as safe operands for features owned by \a body.
 *
 * A Body and any component presentation of that same Body collapse to one
 * native feature identity, so native commands cannot create self-reference
 * cycles or count one result twice.
 */
PartGuiExport std::vector<Gui::SelectionObject> getModelingSelectionForBody(
    const Part::BodyBase* body,
    const char* documentName = nullptr
);

/// Return only projected selections that provide a non-null Part shape.
PartGuiExport std::vector<Gui::SelectionObject>
getModelingShapeSelection(const char* documentName = nullptr);

/**
 * Return whether a retained modeling task may own its document transaction.
 *
 * A clean document is always safe. A nested command may reuse a transaction
 * only when the outermost GUI command began transaction-free and opened that
 * transaction itself. Caller-owned transactions are never reusable.
 */
PartGuiExport bool
canStartRetainedModelingTask(const App::Document* document);

/**
 * Persist the exact visible inputs intentionally replaced by a root result.
 *
 * Body-owned history uses the Body Tip as its previous representation and is
 * deliberately left untouched. The return value reports whether metadata was
 * written; false means the result belongs to Body-native history.
 */
PartGuiExport bool setModelingReplacedInputs(
    App::DocumentObject& result,
    const std::vector<App::DocumentObject*>& inputs
);

/**
 * Validate and publish one complete reusable Design-definition block.
 *
 * Every block member must remain at Design scope. Geometric dependencies
 * must be reusable definitions or exact immutable Body states; lifecycle
 * metadata is deliberately excluded from that dependency check.
 */
PartGuiExport void finalizeModelingDesignDefinition(
    App::DocumentObject& definition,
    const std::vector<App::DocumentObject*>& semanticBlock
);

/**
 * Classify, group, validate, and publish newly created root-level outputs as
 * one reusable Design definition. The final element is the semantic root.
 */
PartGuiExport void publishModelingDesignDefinitionBlock(
    const std::vector<App::DocumentObject*>& semanticBlock
);

/**
 * Own one native modeling attempt from its first document change through commit.
 *
 * When user-facing Undo is disabled, this guard temporarily enables a private
 * transaction journal for the attempt and removes it again after commit or
 * rollback. Initial object identities remain a defensive cleanup boundary for
 * callers which create an object and throw before returning it. Existing
 * properties, Body ownership, and links are restored by the native journal;
 * selection, active objects, and visibility are restored explicitly.
 *
 * Macro/Python-console lines are buffered for the same lifetime. Failed lines
 * are discarded; accepted lines are published only after the native commit.
 *
 * Existing IDs are never cleanup candidates and cannot be registered as new
 * outputs, so edit operations cannot accidentally delete their inputs.
 */
class PartGuiExport ModelingTaskAttempt
{
public:
    ModelingTaskAttempt(App::Document& document, const char* transactionName);
    ~ModelingTaskAttempt();

    ModelingTaskAttempt(const ModelingTaskAttempt&) = delete;
    ModelingTaskAttempt& operator=(const ModelingTaskAttempt&) = delete;

    void trackCreatedObject(App::DocumentObject& object);

    /// Keep this result at document root even if a Body remains active/selected.
    void keepResultAtDocumentRoot(App::DocumentObject& result);

    /**
     * Publish the final tracked result as one reusable Design definition.
     *
     * The result remains at Design scope, receives stable definition/Design
     * identities, and must consume exact earlier modeling states. Public
     * geometry commands use this instead of creating a parallel Body-owned
     * or anonymous document-root history.
     */
    void markResultAsDesignDefinition(App::DocumentObject& result);

    /// Place this result in one exact Body, never an inferred active Body.
    void targetResultBody(
        App::DocumentObject& result,
        Part::BodyBase& body
    );

    /**
     * Record exact viewport inputs which this result intentionally hides.
     *
     * Metadata is emitted only when final ownership is document-root. Results
     * adopted into a Body rely on the Body's Tip history instead.
     */
    void trackReplacedInputs(
        App::DocumentObject& result,
        const std::vector<App::DocumentObject*>& inputs
    );

    /// Commit after every output has recomputed and passed shape validation.
    void commit();

private:
    class Private;
    std::unique_ptr<Private> d;
};

}  // namespace PartGui
