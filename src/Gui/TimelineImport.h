// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <iosfwd>
#include <string>
#include <vector>

#include <FCGlobal.h>

namespace App
{
class Document;
class DocumentObject;
}

namespace Gui
{

/**
 * Exact export set for one user-facing object copy.
 *
 * objects is the ordinary/semantic fixed-point closure to serialize.
 * selectedNames and sourceOrderNames use forced export names, so a merge
 * reader can map them without relying on serialization order.
 */
struct GuiExport TimelineExportPlan
{
    std::vector<App::DocumentObject*> objects;
    std::vector<std::string> selectedNames;
    std::vector<std::string> sourceOrderNames;
    std::vector<bool> sourceVisibility;
    std::vector<bool> sourceSuppression;
};

struct GuiExport TimelineObjectIdentity
{
    App::DocumentObject* address {};
    std::string name;
    long id {0};
};

/**
 * Exact identities restored by one import into one captured document.
 *
 * Callers may use objects/selectedObjects to finish placement and grouping.
 * adoptTimelineImport() revalidates every pointer by document UID, name, and
 * object ID before enrolling the final accepted state in History.
 */
struct GuiExport TimelineImportResult
{
    App::Document* document {};
    std::string documentName;
    std::string documentUid;
    int transactionId {0};
    std::vector<App::DocumentObject*> objects;
    std::vector<App::DocumentObject*> selectedObjects;
    std::vector<App::DocumentObject*> sourceOrder;
    std::vector<bool> sourceVisibility;
    std::vector<bool> sourceSuppression;
    std::vector<TimelineObjectIdentity> objectIdentities;
    std::vector<TimelineObjectIdentity> selectedIdentities;
    std::vector<TimelineObjectIdentity> sourceOrderIdentities;
};

/**
 * Expand normal dependencies and tracked semantic blocks to a fixed point.
 */
GuiExport TimelineExportPlan prepareTimelineExport(
    const std::vector<App::DocumentObject*>& selectedObjects,
    bool recursive
);

/**
 * Restore one prepared export into the exact target document.
 *
 * The caller owns the transaction. The target's canonical timeline is
 * created before raw import so a transported source timeline cannot become
 * the target controller.
 */
GuiExport TimelineImportResult restoreTimelineImport(
    App::Document& target,
    std::istream& input,
    const std::vector<std::string>& selectedNames = {},
    const std::vector<std::string>& sourceOrderNames = {},
    const std::vector<bool>& sourceVisibility = {},
    const std::vector<bool>& sourceSuppression = {}
);

/**
 * Copy a complete ordinary/semantic closure into a target transaction.
 *
 * The returned objects are restored but not yet adopted so the caller may
 * finish grouping, placement, and other accepted presentation state first.
 */
GuiExport TimelineImportResult copyTimelineObjects(
    App::Document& target,
    const std::vector<App::DocumentObject*>& selectedObjects,
    bool recursive
);

/**
 * Adopt a restored import after all final semantic/display state is set.
 */
GuiExport void adoptTimelineImport(
    const TimelineImportResult& imported
);

/**
 * Delete one already-copied semantic export from its source document.
 *
 * The caller owns the source transaction. The complete closure is validated
 * before mutation, then removed consumer-first so raw root/resource timeline
 * order cannot orphan implementation objects.
 */
GuiExport void deleteTimelineExportSource(
    const TimelineExportPlan& source
);

}  // namespace Gui
