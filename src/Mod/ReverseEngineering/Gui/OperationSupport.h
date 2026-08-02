// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <unordered_set>
#include <vector>

namespace App
{
class Document;
class DocumentObject;
class DocumentObjectT;
}  // namespace App

namespace Mesh
{
class OutputGroup;
}

namespace Gui
{
class ExactTransaction;
}

namespace ReverseEngineeringGui::OperationSupport
{

App::Document* cleanActiveDocument();

App::DocumentObject* usableTaskSource(const App::DocumentObjectT& source) noexcept;

bool isUsableSource(const App::DocumentObject* object, const App::Document* document) noexcept;

bool areUsableSources(
    const std::vector<App::DocumentObject*>& objects,
    const App::Document* document
) noexcept;

std::unordered_set<long> objectIds(const App::Document& document);

std::vector<App::DocumentObject*> createdObjects(
    App::Document& document,
    const std::unordered_set<long>& previousIds
);

void setSource(App::DocumentObject& output, App::DocumentObject& source);

void setSources(App::DocumentObject& output, const std::vector<App::DocumentObject*>& sources);

void publishSourcePreserving(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind
);

void publishGroupedOperation(
    App::Document& document,
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& resources
);

Mesh::OutputGroup* publishOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind,
    bool replacesVisibleSources
);

void commit(Gui::ExactTransaction& transaction);

}  // namespace ReverseEngineeringGui::OperationSupport
