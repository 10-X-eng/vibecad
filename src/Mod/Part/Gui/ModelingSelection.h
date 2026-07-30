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
 * A Part::BodyBase is a presentation container whose Shape is supplied by
 * its Tip. Linking a new feature to the Body itself and then making that
 * feature the new Tip creates a circular dependency, so modeling commands
 * must consume the previous Tip instead. Ordinary objects and App::Link
 * occurrences are returned unchanged.
 */
PartGuiExport App::DocumentObject* resolveModelingObject(App::DocumentObject* object);
PartGuiExport const App::DocumentObject*
resolveModelingObject(const App::DocumentObject* object);

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

/**
 * Resolve a selection list and omit inactive History entries and Bodies
 * without a Tip.
 */
PartGuiExport std::vector<Gui::SelectionObject>
resolveModelingSelections(const std::vector<Gui::SelectionObject>& selection);

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
