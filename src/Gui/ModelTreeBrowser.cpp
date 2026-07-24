// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelTreeBrowser.h"

#include <string_view>
#include <unordered_set>

#include <App/Datums.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Origin.h>
#include <App/OriginGroupExtension.h>


using namespace Gui;

namespace
{

bool isDerivedFrom(const App::DocumentObject* object, std::string_view typeName)
{
    if (!object) {
        return false;
    }
    const Base::Type type = Base::Type::fromName(typeName);
    return !type.isBad() && object->getTypeId().isDerivedFrom(type);
}

bool hasTypeNameFragment(const App::DocumentObject* object, std::string_view fragment)
{
    if (!object) {
        return false;
    }
    return object->getTypeId().getName().find(fragment) != std::string_view::npos;
}

bool isLink(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "App::Link");
}

bool isParameterObject(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "App::VarSet")
        || isDerivedFrom(object, "Spreadsheet::Sheet");
}

bool isSketch(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Sketcher::SketchObject");
}

bool isConstruction(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Part::Datum")
        || isDerivedFrom(object, "PartDesign::CoordinateSystem")
        || isDerivedFrom(object, "PartDesign::Plane")
        || isDerivedFrom(object, "PartDesign::Line")
        || isDerivedFrom(object, "PartDesign::Point");
}

bool isReference(const App::DocumentObject* object)
{
    return isLink(object) || hasTypeNameFragment(object, "ShapeBinder")
        || hasTypeNameFragment(object, "SubShapeBinder")
        || hasTypeNameFragment(object, "Reference");
}

bool hasGeometry(const App::DocumentObject* object)
{
    return object
        && (object->getPropertyByName("Shape") || object->getPropertyByName("Mesh")
            || object->getPropertyByName("Points"));
}

App::DocumentObject* geoParent(const App::DocumentObject* object)
{
    return App::GeoFeatureGroupExtension::getGroupOfObject(object);
}

}  // namespace

ModelTreeBrowserProjection::ModelTreeBrowserProjection(App::Document* document)
{
    if (!document) {
        return;
    }

    const auto& objects = document->getObjects();
    _entries.reserve(objects.size());

    std::unordered_set<const App::DocumentObject*> publishedImplementations;
    for (auto* object : objects) {
        if (!isLink(object) || geoParent(object)
            || App::GroupExtension::getGroupOfObject(object)) {
            continue;
        }
        auto* linked = object->getLinkedObject(false);
        if (!linked || linked == object || linked->getDocument() != document
            || isComponent(linked) || isBody(linked)) {
            continue;
        }
        if (resolveOwnership(linked).component) {
            publishedImplementations.insert(linked);
        }
    }

    for (auto* object : objects) {
        if (!object || !object->isAttachedToDocument()
            || object->testStatus(App::PartialObject)) {
            continue;
        }

        Ownership ownership = resolveOwnership(object);
        App::DocumentObject* normalGroup = App::GroupExtension::getGroupOfObject(object);
        if (normalGroup
            && normalGroup->hasExtension(
                App::GeoFeatureGroupExtension::getExtensionClassTypeId()
            )) {
            // Bodies and Parts also implement GroupExtension. They are ownership
            // contexts, not user-created organizational groups.
            normalGroup = nullptr;
        }
        bool publishedOutput = false;

        // VibeScript and other generators commonly keep implementation geometry in
        // an App::Part and expose stable public objects as root-level App::Links.
        // Present those links with the component that owns their target.  This is
        // inferred from native ownership and works for documents made before this
        // browser existed.
        if (isLink(object) && !geoParent(object) && !normalGroup) {
            auto* linked = object->getLinkedObject(false);
            if (linked && linked != object && linked->getDocument() == document
                && !isComponent(linked) && !isBody(linked)) {
                Ownership linkedOwnership = resolveOwnership(linked);
                if (linkedOwnership.component) {
                    ownership = linkedOwnership;
                    publishedOutput = true;
                }
            }
        }

        Entry entry;
        entry.object = object;
        entry.component = ownership.component;
        entry.body = ownership.body;
        entry.group = normalGroup;
        entry.publishedOutput = publishedOutput;
        entry.publishedImplementation = publishedImplementations.contains(object);
        entry.role = classify(object, ownership, publishedOutput);

        if (entry.role == Role::OriginFeature) {
            entry.logicalParent = findOriginParent(object);
        }
        else if (publishedOutput) {
            // The link is displayed with its target's component, but it remains a
            // document-root object for selection and subelement addressing.
            entry.logicalParent = nullptr;
        }
        else if (entry.role == Role::Component) {
            // resolveOwnership() starts at the object's parent, so this is the
            // containing component for nested components and null at document root.
            entry.logicalParent = ownership.component;
        }
        else if (normalGroup) {
            entry.logicalParent = normalGroup;
        }
        else if (entry.role == Role::Body) {
            entry.logicalParent = ownership.component;
        }
        else if (ownership.body) {
            entry.logicalParent = ownership.body;
        }
        else {
            entry.logicalParent = ownership.component;
        }

        _index.emplace(object, _entries.size());
        _entries.push_back(entry);
    }
}

const ModelTreeBrowserProjection::Entry*
ModelTreeBrowserProjection::find(const App::DocumentObject* object) const
{
    const auto it = _index.find(object);
    return it == _index.end() ? nullptr : &_entries[it->second];
}

bool ModelTreeBrowserProjection::isBody(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Part::BodyBase")
        || isDerivedFrom(object, "PartDesign::Body");
}

bool ModelTreeBrowserProjection::isComponent(const App::DocumentObject* object)
{
    return object && !isBody(object)
        && object->hasExtension(App::OriginGroupExtension::getExtensionClassTypeId());
}

ModelTreeBrowserProjection::Ownership
ModelTreeBrowserProjection::resolveOwnership(const App::DocumentObject* object)
{
    Ownership result;
    for (auto* parent = geoParent(object); parent; parent = geoParent(parent)) {
        if (!result.body && isBody(parent)) {
            result.body = parent;
        }
        else if (!result.component && isComponent(parent)) {
            result.component = parent;
        }
    }
    return result;
}

App::DocumentObject*
ModelTreeBrowserProjection::findOriginParent(const App::DocumentObject* object)
{
    if (!object || !object->isDerivedFrom<App::DatumElement>()) {
        return nullptr;
    }
    for (auto* incoming : object->getInList()) {
        if (incoming && incoming->isDerivedFrom<App::Origin>()) {
            return incoming;
        }
    }
    return nullptr;
}

ModelTreeBrowserProjection::Role ModelTreeBrowserProjection::classify(
    const App::DocumentObject* object,
    const Ownership& ownership,
    bool publishedOutput
)
{
    if (isComponent(object)) {
        return Role::Component;
    }
    if (isBody(object)) {
        return Role::Body;
    }
    if (object->isDerivedFrom<App::Origin>()) {
        return Role::Origin;
    }
    if (findOriginParent(object)) {
        return Role::OriginFeature;
    }
    if (isParameterObject(object)) {
        return Role::Parameter;
    }
    if (isSketch(object)) {
        return Role::Sketch;
    }
    if (isConstruction(object)) {
        return Role::Construction;
    }
    if (object->hasExtension(App::GroupExtension::getExtensionClassTypeId())
        && !object->hasExtension(App::GeoFeatureGroupExtension::getExtensionClassTypeId())) {
        return Role::Group;
    }
    if (publishedOutput) {
        return Role::Geometry;
    }
    if (isReference(object)) {
        return Role::Reference;
    }
    if (ownership.body) {
        return Role::Feature;
    }
    if (hasGeometry(object)) {
        return Role::Geometry;
    }
    return Role::Other;
}
