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
        Construction,
        Feature,
        Geometry,
        Reference,
        Group,
        Other,
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

        // True for a root-level App::Link that publishes hidden implementation
        // geometry owned by a component.
        bool publishedOutput {};

        // True when a root-level published link exposes this implementation
        // object. The browser hides it by default without changing ShowInTree.
        bool publishedImplementation {};

        // The stable publication link for a VibeScript output remains in the
        // document for downstream references, but an editable native Body is
        // the canonical browser representation of the same output.
        App::DocumentObject* bodyRepresentation {};

        // The stable publication whose rendered visibility must follow this
        // native Body's browser visibility.
        App::DocumentObject* publicationRepresentation {};

        // Older VibeScript documents gave an adopted result the same label as
        // its Body. FreeCAD uniquified that duplicate into an opaque numeric
        // label. The browser presents that compatibility object as "Result".
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

private:
    struct Ownership
    {
        App::DocumentObject* component {};
        App::DocumentObject* body {};
    };

    static Ownership resolveOwnership(const App::DocumentObject* object);
    static App::DocumentObject* findOriginParent(const App::DocumentObject* object);

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
