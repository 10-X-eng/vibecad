// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <Mod/Mesh/MeshGlobal.h>

namespace Mesh
{
class Feature;
}

namespace MeshGui
{

struct BackgroundMeshModificationTarget
{
    Mesh::Feature* source {nullptr};
    std::string label;
    std::vector<long> pointIndices;
    std::vector<long> facetIndices;
};

/** Start the shared cancellable Mesh modification job used by human and AI paths. */
MeshGuiExport void startBackgroundMeshModification(
    const std::vector<BackgroundMeshModificationTarget>& targets,
    const char* operation,
    const std::string& argumentsJson
);

}  // namespace MeshGui
