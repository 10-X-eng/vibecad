// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <functional>
#include <optional>
#include <string>
#include <vector>

#include <Mod/Mesh/MeshGlobal.h>
#include <Mod/Mesh/App/Mesh.h>

namespace App
{
class Document;
class DocumentObject;
}  // namespace App

namespace Mesh
{
class Feature;
class OutputGroup;
}  // namespace Mesh

namespace MeshGui
{

struct ParametricMeshFilterSpec
{
    const char* typeName;
    const char* objectName;
    const char* objectLabel;
    const char* transactionName;
    bool requireMeshChange {true};
    bool requireNonEmptyResult {true};
    bool groupOutputsUnderOperation {true};
    const char* groupObjectName {"MeshOutputGroup"};
    const char* groupLabel {nullptr};
    const char* operationKind {nullptr};
};

struct ParametricMeshFilterTarget
{
    Mesh::Feature* source {nullptr};
    std::function<void(App::DocumentObject&)> configure;
    // Optional cache already computed by an asynchronous external tool.
    // configure must persist every typed input and the cache provenance on
    // the native feature so later recomputes remain honest.
    std::optional<Mesh::MeshObject> acceptedInitialResult {std::nullopt};
};

/**
 * Create one linked native mesh filter for every source in one exact
 * transaction.
 *
 * The source meshes are never modified. Successful filters hide their
 * sources, persist the exact subset which was visible as timeline replacement
 * inputs, remain editable through their native properties, and are removed
 * together by one Undo. Any invalid or empty result rolls the complete batch
 * back.
 */
MeshGuiExport std::vector<Mesh::Feature*> createParametricMeshFilters(
    App::Document& document,
    const std::vector<ParametricMeshFilterTarget>& targets,
    const ParametricMeshFilterSpec& spec
);

struct StoredMeshEditTarget
{
    Mesh::Feature* source {nullptr};
    Mesh::MeshObject acceptedResult;
    const char* objectName {nullptr};
    const char* objectLabel {nullptr};
    const char* editKind {nullptr};
    bool allowEmptyResult {false};
};

/**
 * Persist one or more exact accepted mesh edits in one transaction.
 *
 * Stored edits are reserved for operations that cannot be replayed from
 * stable model-space parameters. Each result keeps an exact source snapshot,
 * refuses stale recomputes, and restores only the source that was visible
 * when the edit replaced it as the history marker moves.
 */
MeshGuiExport std::vector<Mesh::Feature*> createStoredMeshEdits(
    App::Document& document,
    const std::vector<StoredMeshEditTarget>& targets,
    const char* transactionName,
    bool groupOutputsUnderOperation = true,
    const char* groupObjectName = "MeshOutputGroup",
    const char* groupLabel = nullptr,
    const char* operationKind = nullptr
);

/**
 * Classify a generated physical result as an internal resource of one
 * semantic timeline operation. The resource remains independently visible in
 * the model tree and viewport, but follows its owner's marker state instead
 * of adding another history entry.
 */
MeshGuiExport void markMeshTimelineResource(App::DocumentObject& resource, App::DocumentObject& owner);

/**
 * Classify a generated object as one semantic operation which replaces the
 * listed visible inputs in the viewport.
 *
 * This is for linked operations whose result cannot carry a suppression
 * bypass, such as MeshPart booleans. Moving the history marker before the
 * operation hides the result and restores these exact input objects.
 */
MeshGuiExport void markMeshTimelineReplacement(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs
);

/**
 * Publish one source-preserving command as one timeline operation. A single
 * physical output is the operation itself; a controller is created only when
 * the command produces several physical outputs.
 *
 * The controller owns every output as an independently selectable resource.
 * Sources remain visible and are never presented through bypass geometry.
 * Snapshot outputs intentionally do not advertise a timeline editor.
 */
MeshGuiExport Mesh::OutputGroup* createSourcePreservingOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind
);

/**
 * Publish one standalone external import as one timeline operation. A single
 * physical output is the operation itself; a controller is created only when
 * the import produces several physical outputs.
 *
 * External input identities are stored as inert document metadata only.
 * Reopening and recomputing the document never reads those paths.
 */
MeshGuiExport Mesh::OutputGroup* createStandaloneOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& outputs,
    const std::vector<std::string>& externalInputs,
    const char* objectName,
    const char* label,
    const char* operationKind
);

}  // namespace MeshGui
