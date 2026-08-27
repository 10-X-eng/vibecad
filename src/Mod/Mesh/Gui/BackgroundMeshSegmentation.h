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

MeshGuiExport void startBackgroundMeshSegmentation(
    const std::vector<Mesh::Feature*>& sources,
    const char* operation,
    const std::string& argumentsJson
);

}
