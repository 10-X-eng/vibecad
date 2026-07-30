// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Mod/Mesh/MeshGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}

namespace MeshGui
{

/**
 * Return whether a native mesh operation may begin changing a document.
 *
 * Mesh tools must never join or replace an unrelated transaction. Task
 * panels call this at Accept/Apply time; command activation additionally
 * checks that no other task panel is active.
 */
MeshGuiExport bool hasCleanNativeMutationBoundary(const App::Document* document);

/**
 * Return whether a task, modeless editor, or interactive mesh command may
 * start from the ribbon.
 */
MeshGuiExport bool canStartNativeMeshCommand(const App::Document* document);

/**
 * Return whether an exact native mesh input belongs to the current History
 * state.
 *
 * The selected object and any resolved link definition must both be live,
 * non-internal, unsuppressed, and on or before their document's History
 * marker.  This predicate deliberately accepts non-mesh inputs as well
 * because native MeshPart tools also consume planes and shape features.
 */
MeshGuiExport bool isNativeMeshInputActive(const App::DocumentObject* object) noexcept;

}  // namespace MeshGui
