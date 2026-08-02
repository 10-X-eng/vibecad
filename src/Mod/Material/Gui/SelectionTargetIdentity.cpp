// SPDX-License-Identifier: LGPL-2.1-or-later

#include "SelectionTargetIdentity.h"

#include <string_view>
#include <utility>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/Property.h>
#include <Gui/Application.h>

using namespace MatGui;

namespace
{
bool isActiveTimelineTarget(const App::DocumentObject* object) noexcept
{
    try {
        if (!App::DocumentTimeline::isObjectUsableAtCurrentPosition(object)) {
            return false;
        }

        const auto* linked = object->getLinkedObject(true);
        return !linked || linked == object
            || App::DocumentTimeline::isObjectUsableAtCurrentPosition(linked);
    }
    catch (...) {
        return false;
    }
}
}  // namespace

std::optional<SelectionTargetIdentity> SelectionTargetIdentity::capture(
    const App::DocumentObject* object
)
{
    const auto* document = object ? object->getDocument() : nullptr;
    const char* documentName = document ? document->getName() : nullptr;
    const char* objectName = object ? object->getNameInDocument() : nullptr;
    if (!document || !documentName || !*documentName || !objectName || !*objectName
        || object->getID() < 0 || !document->containsObject(object)
        || document->getObject(objectName) != object
        || document->getObjectByID(object->getID()) != object || !isActiveTimelineTarget(object)) {
        return std::nullopt;
    }

    return SelectionTargetIdentity {
        documentName,
        document->Uid.getValueStr(),
        document,
        objectName,
        object->getID(),
    };
}

App::Document* SelectionTargetIdentity::resolveDocument() const noexcept
{
    if (documentName.empty() || documentUid.empty()) {
        return nullptr;
    }
    try {
        auto* document = App::GetApplication().getDocument(documentName.c_str());
        return document && document == documentAddress && document->Uid.getValueStr() == documentUid
            ? document
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

App::DocumentObject* SelectionTargetIdentity::resolveObject() const noexcept
{
    if (objectName.empty() || objectId < 0) {
        return nullptr;
    }
    try {
        auto* document = resolveDocument();
        auto* object = document ? document->getObjectByID(objectId) : nullptr;
        return object && object->getNameInDocument() && objectName == object->getNameInDocument()
                && document->containsObject(object)
                && document->getObject(objectName.c_str()) == object
                && isActiveTimelineTarget(object)
            ? object
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

Gui::ViewProvider* SelectionTargetIdentity::resolveViewProvider() const noexcept
{
    try {
        auto* object = resolveObject();
        return object && Gui::Application::Instance
            ? Gui::Application::Instance->getViewProvider(object)
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

std::optional<SelectionPropertyTargetIdentity> SelectionPropertyTargetIdentity::capture(
    const App::DocumentObject* selectedOccurrence,
    const char* selectedPropertyName
)
{
    if (!selectedOccurrence || !selectedPropertyName || !*selectedPropertyName) {
        return std::nullopt;
    }

    try {
        auto occurrenceIdentity = SelectionTargetIdentity::capture(selectedOccurrence);
        auto* property = selectedOccurrence->getPropertyByName(selectedPropertyName);
        auto* owner = property ? dynamic_cast<App::DocumentObject*>(property->getContainer())
                               : nullptr;
        auto ownerIdentity = SelectionTargetIdentity::capture(owner);
        if (!occurrenceIdentity || !ownerIdentity || !property->getName()
            || std::string_view(property->getName()) != selectedPropertyName) {
            return std::nullopt;
        }

        return SelectionPropertyTargetIdentity {
            std::move(*occurrenceIdentity),
            std::move(*ownerIdentity),
            selectedPropertyName,
            property,
        };
    }
    catch (...) {
        return std::nullopt;
    }
}

App::Property* SelectionPropertyTargetIdentity::resolveProperty() const noexcept
{
    if (propertyName.empty() || !propertyAddress) {
        return nullptr;
    }

    try {
        auto* selectedOccurrence = occurrence.resolveObject();
        auto* propertyOwner = owner.resolveObject();
        auto* property = selectedOccurrence
            ? selectedOccurrence->getPropertyByName(propertyName.c_str())
            : nullptr;
        return property && property == propertyAddress && property->getContainer() == propertyOwner
                && property->getName() && propertyName == property->getName()
            ? property
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}
