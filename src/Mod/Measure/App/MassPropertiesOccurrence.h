// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 VibeCAD contributors

#pragma once

#include <algorithm>
#include <string>
#include <utility>
#include <vector>

#include <App/DocumentObject.h>
#include <App/DocumentObserver.h>
#include <App/GeoFeature.h>
#include <App/Link.h>
#include <Base/Placement.h>
#include <Mod/Part/App/PartFeature.h>

#include <TopoDS_Shape.hxx>

namespace Measure::Internal
{

struct ResolvedOccurrence
{
    App::DocumentObject* root = nullptr;
    App::DocumentObject* endpoint = nullptr;
    App::DocumentObject* materialOwner = nullptr;
    std::string subName;
    TopoDS_Shape shape;
    Base::Placement placement;
    std::vector<App::DocumentObject*> members;
};

inline bool normalizeObjectPath(
    App::DocumentObject* root,
    const char* subName,
    std::string& normalizedPath
)
{
    normalizedPath.clear();
    if (!root || !root->isAttachedToDocument()) {
        return false;
    }

    App::SubObjectT occurrence(root, subName ? subName : "");
    occurrence.normalize(
        App::SubObjectT::NormalizeOption::NoElement
        | App::SubObjectT::NormalizeOption::NoFlatten
        | App::SubObjectT::NormalizeOption::ConvertIndex
    );
    if (occurrence.getObject() != root) {
        return false;
    }

    normalizedPath = occurrence.getSubNameNoElement();
    if (!normalizedPath.empty() && normalizedPath.back() != '.') {
        return false;
    }

    std::vector<int> pathEnds;
    const auto members =
        root->getSubObjectList(normalizedPath.c_str(), &pathEnds, false);
    if (members.empty() || members.front() != root) {
        return false;
    }
    if (!normalizedPath.empty()
        && (members.size() < 2 || pathEnds.empty()
            || pathEnds.back()
                != static_cast<int>(normalizedPath.size()))) {
        return false;
    }

    return true;
}

inline bool resolveShapeOccurrence(
    App::DocumentObject* root,
    const std::string& subName,
    ResolvedOccurrence& result
)
{
    result = {};
    if (!root || !root->isAttachedToDocument()) {
        return false;
    }

    App::SubObjectT occurrence(root, subName.c_str());
    if (occurrence.hasSubElement()) {
        return false;
    }

    std::vector<int> pathEnds;
    auto members =
        root->getSubObjectList(subName.c_str(), &pathEnds, false);
    if (members.empty() || members.front() != root) {
        return false;
    }
    if (!subName.empty()
        && (members.size() < 2 || pathEnds.empty()
            || pathEnds.back() != static_cast<int>(subName.size()))) {
        return false;
    }

    auto* endpoint = members.back();
    if (!endpoint || !endpoint->isAttachedToDocument()) {
        return false;
    }

    App::DocumentObject* materialOwner = nullptr;
    TopoDS_Shape shape = Part::Feature::getShape(
        root,
        Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
        subName.empty() ? nullptr : subName.c_str(),
        nullptr,
        &materialOwner
    );
    if (shape.IsNull()) {
        return false;
    }

    result.root = root;
    result.endpoint = endpoint;
    result.materialOwner = materialOwner ? materialOwner : endpoint;
    result.subName = subName;
    result.shape = std::move(shape);
    result.placement =
        App::GeoFeature::getGlobalPlacement(endpoint, root, subName);
    result.members = std::move(members);

    auto appendMember = [&result](App::DocumentObject* member) {
        if (!member || !member->isAttachedToDocument()) {
            return;
        }
        if (std::find(
                result.members.begin(),
                result.members.end(),
                member
            )
            == result.members.end()) {
            result.members.push_back(member);
        }
    };
    appendMember(result.materialOwner);
    for (std::size_t index = 0;
         index < result.members.size();
         ++index) {
        auto* member = result.members[index];
        appendMember(member ? member->getLinkedObject(true) : nullptr);
    }

    return true;
}

inline bool endpointRepresentsSource(
    App::DocumentObject* endpoint,
    App::DocumentObject* source
)
{
    if (!endpoint || !source) {
        return false;
    }
    if (endpoint == source) {
        return true;
    }

    const bool endpointIsOccurrence =
        endpoint->hasExtension(App::LinkBaseExtension::getExtensionClassTypeId());
    const bool sourceIsOccurrence =
        source->hasExtension(App::LinkBaseExtension::getExtensionClassTypeId());
    if (endpointIsOccurrence && sourceIsOccurrence) {
        return false;
    }
    if (endpointIsOccurrence) {
        return endpoint->getLinkedObject(true) == source;
    }
    if (sourceIsOccurrence) {
        return source->getLinkedObject(true) == endpoint;
    }
    return false;
}

}  // namespace Measure::Internal
