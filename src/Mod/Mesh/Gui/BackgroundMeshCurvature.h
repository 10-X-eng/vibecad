// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <vector>

#include <Mod/Mesh/MeshGlobal.h>

namespace Mesh
{
class Feature;
}

namespace MeshGui
{
MeshGuiExport void startBackgroundMeshCurvature(
    const std::vector<Mesh::Feature*>& sources
);
}
