// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 VibeCAD contributors

#pragma once

#include <vector>

#include <Base/Matrix.h>
#include <Mod/Inspection/InspectionGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}  // namespace App

namespace Inspection
{

enum class SourceKind
{
    None,
    Part,
    Mesh,
    Points,
};

/**
 * Exact geometry reached through one document occurrence.
 *
 * `transform` maps the resolved geometry's local coordinates into document
 * coordinates. It includes the occurrence's complete App::Link chain and all
 * enclosing GeoFeatureGroup placements, without applying the linked
 * definition's container hierarchy to an occurrence that replaces it.
 */
struct InspectionExport ResolvedSource
{
    App::DocumentObject* occurrence = nullptr;
    App::DocumentObject* geometry = nullptr;
    SourceKind kind = SourceKind::None;
    Base::Matrix4D transform;
    std::vector<App::DocumentObject*> dependencies;
};

InspectionExport bool isSourceUsable(
    const App::DocumentObject* object,
    const App::Document* owningDocument
) noexcept;

InspectionExport bool resolveSource(
    App::DocumentObject* occurrence,
    const App::Document* owningDocument,
    ResolvedSource& result
) noexcept;

}  // namespace Inspection
