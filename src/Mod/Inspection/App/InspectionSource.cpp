// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 VibeCAD contributors

#include "InspectionSource.h"

#include <algorithm>
#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeature.h>
#include <App/GeoFeatureGroupExtension.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Points/App/PointsFeature.h>

namespace Inspection
{

bool isSourceUsable(const App::DocumentObject* object, const App::Document* owningDocument) noexcept
{
    if (!object || !owningDocument || object->getDocument() != owningDocument
        || !owningDocument->containsObject(object)) {
        return false;
    }

    try {
        if (!App::DocumentTimeline::isObjectUsableAtCurrentPosition(object)) {
            return false;
        }

        const auto* linked = object->getLinkedObject(true);
        return linked && linked->isAttachedToDocument()
            && (linked == object || App::DocumentTimeline::isObjectUsableAtCurrentPosition(linked));
    }
    catch (...) {
        return false;
    }
}

bool resolveSource(
    App::DocumentObject* occurrence,
    const App::Document* owningDocument,
    ResolvedSource& result
) noexcept
{
    result = {};
    if (!isSourceUsable(occurrence, owningDocument)) {
        return false;
    }

    try {
        Base::Matrix4D occurrenceTransform;
        auto* geometry = occurrence->getLinkedObject(true, &occurrenceTransform, true);
        if (!geometry || !geometry->isAttachedToDocument()) {
            return false;
        }

        std::vector<App::DocumentObject*> dependencies;
        std::unordered_set<App::DocumentObject*> dependencySet;
        const auto addDependency =
            [&dependencies, &dependencySet, owningDocument](App::DocumentObject* dependency) {
                if (dependency && dependency->getDocument() == owningDocument
                    && owningDocument->containsObject(dependency)
                    && dependencySet.insert(dependency).second) {
                    dependencies.push_back(dependency);
                }
            };

        std::unordered_set<App::DocumentObject*> linkChain;
        for (auto* current = occurrence; current;) {
            if (!linkChain.insert(current).second) {
                return false;
            }
            addDependency(current);
            auto* linked = current->getLinkedObject(false);
            if (!linked || linked == current) {
                break;
            }
            current = linked;
        }
        addDependency(geometry);

        Base::Matrix4D parentTransform;
        std::unordered_set<App::DocumentObject*> groupChain;
        auto* child = occurrence;
        while (auto* parent = App::GeoFeatureGroupExtension::getGroupOfObject(child)) {
            if (!groupChain.insert(parent).second) {
                return false;
            }
            addDependency(parent);
            parentTransform = App::GeoFeature::getPlacementFromProp(parent, "Placement").toMatrix()
                * parentTransform;
            child = parent;
        }
        occurrenceTransform = parentTransform * occurrenceTransform;

        SourceKind kind = SourceKind::None;
        if (geometry->isDerivedFrom<Mesh::Feature>()) {
            kind = SourceKind::Mesh;
        }
        else if (geometry->isDerivedFrom<Points::Feature>()) {
            kind = SourceKind::Points;
        }
        else if (!Part::Feature::getTopoShape(geometry, Part::ShapeOption::NoFlag).isNull()) {
            kind = SourceKind::Part;
        }
        if (kind == SourceKind::None) {
            return false;
        }

        result.occurrence = occurrence;
        result.geometry = geometry;
        result.kind = kind;
        result.transform = occurrenceTransform;
        result.dependencies = std::move(dependencies);
        return true;
    }
    catch (...) {
        result = {};
        return false;
    }
}

}  // namespace Inspection
