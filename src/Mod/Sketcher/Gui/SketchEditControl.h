// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>

namespace SketcherGui
{

struct LeaveSketchResult
{
    std::string documentName;
    std::string documentUid;
    std::string sketchName;
    bool acceptedTaskDialog = false;
};

/** Finish the exact active Sketch edit session.
 *
 * The document and Sketch identities are checked before and after the edit
 * task is accepted. When @p recordFallbackCommand is true, the uncommon
 * no-task-dialog fallback preserves the human command's macro recording.
 */
LeaveSketchResult leaveActiveSketch(
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName,
    bool recordFallbackCommand
);

/** Set section visibility for the exact active Sketch edit session. */
bool setActiveSketchSectionView(
    const std::string& documentName,
    const std::string& documentUid,
    const std::string& sketchName,
    bool visible
);

}  // namespace SketcherGui
