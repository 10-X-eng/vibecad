// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Mod/MeshPart/MeshPartGlobal.h>


class TopoDS_Shape;

namespace Mesh
{
class Feature;
}

namespace MeshPart
{

/** Build validated OCC solid topology from one closed Mesh feature.
 *
 * Nested shells become cavities and disconnected outer shells become a
 * compound of solids.  The caller decides whether multiple solids are valid
 * for its operation.
 */
MeshPartExport TopoDS_Shape solidShapeFromMesh(
    const Mesh::Feature& source,
    double sewingTolerance,
    const char* propertyName
);

}  // namespace MeshPart
