// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <cstddef>
#include <map>
#include <string>
#include <vector>

#include <App/PropertyLinks.h>
#include <Base/Placement.h>
#include <Base/Vector3D.h>
#include <Mod/PartDesign/PartDesignGlobal.h>

namespace App
{
class Document;
class DocumentObject;
class Part;
}  // namespace App

namespace Part
{
class Feature;
class TopoShape;
}  // namespace Part

namespace PartDesign
{

class Body;
class DesignBodyPublication;
class DesignBodyState;
class DesignOperationProperties;
class DesignSeparate;

/**
 * One in-progress edit of a Design-wide modeling operation.
 *
 * The snapshot is captured before target properties are changed. It keeps the
 * exact existing Body-state resource identities needed by the timeline's
 * many-to-many reconciliation contract. The active document transaction owns
 * rollback; this object owns no document data.
 */
struct PartDesignExport DesignOperationEdit
{
    App::DocumentObject* operation {};
    std::vector<DesignBodyState*> originalStates;
    std::string originalResultMode;
    std::string originalDestinationComponentId;
    std::string newBodyId;
    std::map<std::string, Base::Placement> originalTargetFrames;
    bool provisionalOperation {false};
    bool resourcesStaged {false};
};

/**
 * One exact reusable-definition reference after presentation resolution.
 *
 * Subelement names are canonical for object. In particular, a face or edge
 * selected on a rendered Body is converted to the persistent mapped name on
 * the immutable Body state consumed by the definition.
 */
struct PartDesignExport DesignDefinitionReference
{
    App::DocumentObject* object {};
    std::vector<std::string> subelements;
};

/**
 * Application-layer service for the Design-wide modeling graph.
 *
 * Native task panels, Python/VibeScript bindings, import, and migration must
 * call this service rather than independently constructing Body states or
 * publication links. All methods require one caller-owned document
 * transaction; failures throw before publishing partial results.
 */
class PartDesignExport DesignModel
{
public:
    static Body* bodyWithId(App::Document& document, const std::string& bodyId);
    static App::Part* componentWithId(App::Document& document, const std::string& componentId);
    static std::string componentId(const App::Part& component);

    /**
     * Resolve one user-selected modeling reference at a global definition's
     * History position.
     *
     * Body presentations and legacy Body-owned solid features become the
     * exact immutable Body state active before the definition. Reusable
     * sketches, datums, and standalone definitions remain themselves.
     * Components, publications, and assembly occurrences are rejected.
     */
    static App::DocumentObject* resolveDefinitionReference(
        App::DocumentObject& definition,
        App::DocumentObject& selected
    );

    /**
     * Resolve an object plus selected subelements to one exact definition
     * reference.
     *
     * This is the required boundary for face/edge/vertex references. It
     * preserves whole-object and non-shape references, canonicalizes names on
     * an already exact Part feature, and maps a Body presentation only when
     * its indexed subshape is the same topology as the resolved Body state.
     * It never guesses by geometry, labels, or proximity.
     */
    static DesignDefinitionReference resolveDefinitionSubelementReference(
        App::DocumentObject& definition,
        App::DocumentObject& selected,
        const std::vector<std::string>& subelements
    );

    /** Assign stable Design/definition identities before a creation task opens. */
    static void initializeDefinition(App::DocumentObject& definition);

    /**
     * Publish one new global sketch/datum/reference as one History operation.
     *
     * The caller owns the creation transaction and must call this only after
     * the complete definition recomputes successfully.
     */
    static void finalizeDefinition(App::DocumentObject& definition);

    /**
     * Configure the exact targets of one operation.
     *
     * resultMode is New Body, Join, Cut, Intersect, or Modify. New Body
     * accepts no target Bodies and uses destinationComponent. Modify is
     * reserved for operations such as Fillet and Chamfer whose tool is the
     * exact prior Body state itself. Every other mode requires at least one
     * exact Body. Existing target-frame snapshots are retained when editing so
     * later Component motion cannot rewrite earlier geometry.
     */
    static void setOperationTargets(
        App::DocumentObject& operation,
        const std::string& resultMode,
        const std::vector<Body*>& bodies,
        App::Part* destinationComponent = nullptr,
        const std::map<std::string, Base::Placement>& historicalFrames = {},
        bool allowIncompleteSelection = false
    );

    /** Configure targets using the immutable frames captured for this edit. */
    static void setOperationTargets(
        DesignOperationEdit& edit,
        const std::string& resultMode,
        const std::vector<Body*>& bodies,
        App::Part* destinationComponent = nullptr,
        bool allowIncompleteSelection = false
    );

    /**
     * Repeat one earlier Design feature on explicit target Bodies.
     *
     * sourceOperation supplies one parametric tool and its additive or
     * subtractive semantic. The target states are resolved at the Pattern's
     * own History position; no mutable publication or Body ownership link is
     * stored.
     */
    static void setFeaturePatternTargets(
        DesignOperationEdit& edit,
        App::DocumentObject& sourceOperation,
        const std::vector<Body*>& bodies,
        bool allowIncompleteSelection = false
    );

    /**
     * Copy one exact Body state into independently identified output Bodies.
     *
     * generatedCopyCount excludes the unchanged source occurrence. Existing
     * output identities are retained by occurrence index while editing; added
     * occurrences receive new identities and removed occurrences are retired
     * by normal operation-resource reconciliation.
     */
    static void setBodyPatternSource(
        DesignOperationEdit& edit,
        Body& sourceBody,
        std::size_t generatedCopyCount
    );

    /**
     * Copy one exact Body state into one new, independently identified Body.
     *
     * The Clone stores only the immutable source state and stable identities.
     * It never creates a Body-owned BaseFeature or links to a mutable Body.
     */
    static void setCloneSource(DesignOperationEdit& edit, Body& sourceBody);

    /**
     * Configure one accepted VibeScript program as a global History operation.
     *
     * outputKeys are source-level identities which retain Body identity across
     * edits. An entry in adoptedBodies advances that existing Body; nullptr
     * creates a new Body. Existing operation-created outputs retain their
     * saved identity automatically when the same key is rebuilt.
     */
    static void setScriptOutputs(
        DesignOperationEdit& edit,
        const std::string& programObjectName,
        const std::string& programId,
        const std::string& revision,
        const std::vector<std::string>& outputKeys,
        const std::vector<std::string>& outputLabels,
        const std::vector<Part::TopoShape>& outputShapes,
        const std::vector<Body*>& adoptedBodies,
        const std::vector<std::string>& programOutputKeys,
        const std::vector<std::string>& programOutputTypes
    );

    /**
     * Configure a many-Body Boolean with one explicit result Body.
     *
     * resultMode is Join, Cut, or Intersect. resultBody is always the first
     * exact input. Tool Bodies remain input-only when keepTools is true; when
     * false, each receives an absent output state. No active Body, visibility,
     * tree order, or geometric intersection is used to infer either role.
     */
    static void setCombineBodies(
        App::DocumentObject& operation,
        const std::string& resultMode,
        Body& resultBody,
        const std::vector<Body*>& toolBodies,
        bool keepTools,
        const std::map<std::string, Base::Placement>& historicalFrames = {},
        bool allowIncompleteSelection = false
    );

    /** Configure Combine using the immutable frames captured for this edit. */
    static void setCombineBodies(
        DesignOperationEdit& edit,
        const std::string& resultMode,
        Body& resultBody,
        const std::vector<Body*>& toolBodies,
        bool keepTools,
        bool allowIncompleteSelection = false
    );

    /**
     * Configure the source Body and exact splitting definitions.
     *
     * This records every Body-backed splitter as an immutable input state and
     * snapshots every splitter's containing frame. It returns newly computed
     * strict interior witnesses without assigning Body identities.
     */
    static std::vector<Base::Vector3d> setSplitDefinition(
        DesignOperationEdit& edit,
        Body& sourceBody,
        const std::vector<App::PropertyLinkSubList::SubSet>& splitters
    );

    /**
     * Assign Split output identities to an explicit retained region.
     *
     * retainedRegion indexes witnesses and is the only choice which determines
     * which result keeps sourceBody's identity. Other outputs receive saved
     * identities and are created in the source Body's Component.
     */
    static void assignSplitRegions(
        DesignOperationEdit& edit,
        Body& sourceBody,
        const std::vector<Base::Vector3d>& witnesses,
        std::size_t retainedRegion
    );

    /**
     * Separate one reusable multi-solid Design definition into new Bodies.
     *
     * The source remains a root definition and every output identity is bound
     * to one strict interior witness. Existing identities are preserved only
     * when their saved witnesses still classify every regenerated solid
     * exactly; no kernel ordering or geometric similarity is used.
     */
    static void setSeparateDefinition(
        DesignOperationEdit& edit,
        App::DocumentObject& source,
        App::Part* destinationComponent = nullptr
    );

    /**
     * Capture one operation's existing resource graph.
     *
     * Call exactly once after opening the task transaction and before changing
     * any persistent operation property.
     */
    static DesignOperationEdit beginOperationEdit(App::DocumentObject& operation);

    /**
     * Validate geometry and atomically publish/reconcile every output state.
     *
     * The operation and all output states become one History block. For an
     * existing operation, retained states keep identity, new targets receive
     * new states, removed targets are rerouted to their exact prior state, and
     * retired resources are deleted only after timeline reconciliation.
     */
    static std::vector<Body*> finalizeOperation(DesignOperationEdit& edit);

    /**
     * Remove one complete Design operation and reconcile every Body output.
     *
     * Operation-created Bodies are removed only when nothing outside the
     * operation still uses their identity. Modified Bodies are rerouted to
     * their exact preceding state. The caller owns the document transaction.
     */
    static std::vector<std::string> removeOperation(App::DocumentObject& operation);

    /**
     * Reconcile and remove every model-owned output resource while retaining
     * the operation object for its caller to delete.
     *
     * This split lifecycle is used by the standard GUI Delete command so a
     * ViewProvider never destroys itself from inside one of its own virtual
     * methods. The caller must remove the operation in the same transaction.
     */
    static std::vector<std::string> removeOperationResources(App::DocumentObject& operation);

    /** Return or create the Body's one stable rendered publication. */
    static DesignBodyPublication* ensurePublication(App::Document& document, Body& body);

    /**
     * Promote one exact legacy Body tip into the Design state graph.
     *
     * The legacy feature's current solid is copied into one immutable initial
     * Body state, a stable publication is installed, and the feature is moved
     * to Design scope without changing its document identity or global
     * placement. The caller must subsequently make that retained feature an
     * internal generator or abort the surrounding transaction.
     */
    static DesignBodyState* initializeLegacyBodyState(Body& body, Part::Feature& legacyTip);

    /**
     * Give one imported Design graph fresh instance identities.
     *
     * Import restores links before calling this method. The remap is then
     * performed in two passes so copied Components, Bodies, sketches,
     * operations, states, and publications agree on every new UUID before
     * import completes. Partial operation graphs are rejected rather than
     * retaining references to source identities.
     */
    static void remapImportedGraph(
        App::Document& document,
        const std::vector<App::DocumentObject*>& importedObjects
    );

    /**
     * Validate the complete Design graph without repairing or guessing.
     *
     * Throws before commit when identities, state chains, operation targets,
     * Component membership, publications, or modeling-reference boundaries
     * are inconsistent.
     */
    static void validateDesign(App::Document& document);

private:
    static void finalizeNewOperation(DesignOperationEdit& edit, std::vector<Body*>& targets);
    static void finalizeExistingOperation(DesignOperationEdit& edit, std::vector<Body*>& targets);
};

}  // namespace PartDesign
