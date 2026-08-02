// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <optional>
#include <string>

namespace App
{
class Document;
class DocumentObject;
class Property;
}  // namespace App

namespace Gui
{
class ViewProvider;
}

namespace MatGui
{

/**
 * Stable identity for an object targeted by a modeless or task-panel editor.
 *
 * Dialogs retain this value instead of pointers. Every user edit resolves the
 * exact live document and object again, so closing a document, deleting an
 * object, or creating another object with the same name cannot retarget an
 * existing editor.
 */
struct SelectionTargetIdentity
{
    std::string documentName;
    std::string documentUid;
    const App::Document* documentAddress {nullptr};
    std::string objectName;
    long objectId {-1};

    static std::optional<SelectionTargetIdentity> capture(const App::DocumentObject* object);

    [[nodiscard]] App::Document* resolveDocument() const noexcept;
    [[nodiscard]] App::DocumentObject* resolveObject() const noexcept;
    [[nodiscard]] Gui::ViewProvider* resolveViewProvider() const noexcept;

    friend bool operator==(const SelectionTargetIdentity&, const SelectionTargetIdentity&) = default;
};

/**
 * Stable identity for one model property reached through a selected object.
 *
 * App::Link can forward a property lookup from an occurrence to a definition
 * in another document. Keeping both identities makes that ownership explicit:
 * an editor can enlist the property owner's document and can reject a changed
 * link target instead of silently writing to a different object.
 */
struct SelectionPropertyTargetIdentity
{
    SelectionTargetIdentity occurrence;
    SelectionTargetIdentity owner;
    std::string propertyName;
    const App::Property* propertyAddress {nullptr};

    static std::optional<SelectionPropertyTargetIdentity> capture(
        const App::DocumentObject* occurrence,
        const char* propertyName
    );

    [[nodiscard]] App::Property* resolveProperty() const noexcept;

    friend bool operator==(const SelectionPropertyTargetIdentity&, const SelectionPropertyTargetIdentity&)
        = default;
};

}  // namespace MatGui
