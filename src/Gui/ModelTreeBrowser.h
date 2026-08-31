// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <unordered_map>
#include <vector>

namespace App
{
class Document;
class DocumentObject;
}

namespace Gui
{

/**
 * A presentation-only classification of document objects for the model browser.
 *
 * FreeCAD's historical tree uses ViewProvider::claimChildren() for both dependency
 * traversal and presentation.  Those are different concerns: a sketch can be an
 * input to a feature while still belonging in a document-wide Sketches collection.
 * This projection derives a stable browser role and ownership context without
 * creating, moving, or modifying any document objects.
 */
class ModelTreeBrowserProjection
{
public:
    enum class Role
    {
        Component,
        Body,
        Origin,
        OriginFeature,
        Parameter,
        Sketch,
        // Compatibility-only role value. Datum/construction objects are now
        // classified as Reference, but retaining this enumerator preserves
        // source compatibility and the numeric values of subsequent roles.
        Construction,
        Feature,
        Geometry,
        Reference,
        Group,
        Other,
        // User-authored modeling operations remain available in both the
        // ownership-oriented model browser and the chronological History.
        History,
        // Body states, publications, and other owned implementation objects
        // have no independent browser representation.
        Internal,
        // Native component occurrences remain visible in the model browser as
        // present-state structure while the same objects retain their ordered
        // creation/edit entries in History. The established name is retained
        // for source compatibility; it covers Model and Assembly occurrences.
        // Appending these values preserves every established Role numeric value
        // for out-of-tree consumers.
        AssemblyOccurrence,
        AssemblyMotion,
        AssemblyOperation,
        // A stable VibeCAD shape publication without a native Body is a
        // generated model output, not a user-authored reference. Appending
        // this role preserves every established Role numeric value.
        VibeCADOutput,
    };

    struct Entry
    {
        App::DocumentObject* object {};
        Role role {Role::Other};

        // Nearest non-Body OriginGroup that owns the object.
        App::DocumentObject* component {};

        // Nearest modeling Body that owns the object.
        App::DocumentObject* body {};

        // A normal (non-geometric) group that owns the object, if any.
        App::DocumentObject* group {};

        // Object parent used to construct selection subnames.  Virtual category
        // folders never become part of this logical chain.
        App::DocumentObject* logicalParent {};

        // True only for a stable VibeScript publication link carrying a
        // complete, unique persisted output identity that has a unique
        // persisted native Body or private target counterpart.
        bool publishedOutput {};

        // True only for a private publication target paired with one complete,
        // unique persisted VibeScript publication identity. The browser never
        // infers this state from native Link topology, ownership, labels, or
        // names.
        bool publishedImplementation {};

        // The stable publication link for a VibeScript output remains in the
        // document for downstream references, but an editable native Body is
        // the canonical browser representation of the same output.
        App::DocumentObject* bodyRepresentation {};

        // Compatibility-only members retained in their original positions so
        // out-of-tree browser extensions keep the same Entry source and binary
        // layout. The retired publication/history renderer no longer populates
        // or consumes either field.
        App::DocumentObject* publicationRepresentation {};
        std::vector<App::DocumentObject*> bodyResultRepresentations;

        // Compatibility-only layout slot. Adopted-result presentation is not
        // inferred from object names or labels; current documents persist the
        // intended native label explicitly.
        bool compatibilityResultLabel {};
    };

    explicit ModelTreeBrowserProjection(App::Document* document);

    const std::vector<Entry>& entries() const
    {
        return _entries;
    }

    const Entry* find(const App::DocumentObject* object) const;

    static bool isBody(const App::DocumentObject* object);
    static bool isComponent(const App::DocumentObject* object);
    static bool isVibeScriptProgram(const App::DocumentObject* object);

private:
    struct Ownership
    {
        App::DocumentObject* component {};
        App::DocumentObject* body {};
    };

    static Ownership resolveOwnership(const App::DocumentObject* object);
    static App::DocumentObject* findOriginParent(const App::DocumentObject* object);

    // History edits reorder the persisted document timeline independently of
    // creation order. Preserve that chronology inside every ownership folder.
    void orderOperationsByTimeline(const App::Document* document);

    // PartDesign move up/down edits the Body's Group order, so Group order --
    // not creation order -- is the feature history the browser must present.
    void orderFeaturesByBodyHistory();
    static Role classify(
        const App::DocumentObject* object,
        const Ownership& ownership,
        bool publishedOutput
    );

    std::vector<Entry> _entries;
    std::unordered_map<const App::DocumentObject*, std::size_t> _index;
};

}  // namespace Gui
