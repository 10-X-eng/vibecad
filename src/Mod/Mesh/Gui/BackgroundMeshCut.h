// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>

#include <Mod/Mesh/MeshGlobal.h>

namespace MeshGui
{

MeshGuiExport void startBackgroundMeshCut(
    const char* operation,
    const std::string& argumentsJson
);

}
