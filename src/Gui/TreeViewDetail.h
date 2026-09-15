// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <FCGlobal.h>


namespace Gui
{

/** One read-only, presentation-only row shown beneath a document object.
 *
 * Tree details never become document objects, selection identities, or
 * transaction participants. They let data-backed objects such as BOM sheets
 * summarize their useful contents without polluting the model graph.
 */
struct TreeViewDetail
{
    std::string key;
    std::string label;
    std::string secondaryText;
    std::string toolTip;
    std::string iconName;
};

/** Optional presentation capability implemented only by view providers that
 * have useful non-object rows to expose in the VibeCAD model browser.
 *
 * Keeping this separate from ViewProviderDocumentObject avoids changing the
 * ABI or imposing a virtual call on every existing view provider.
 */
class GuiExport TreeViewDetailProvider
{
public:
    virtual ~TreeViewDetailProvider();
    virtual std::vector<TreeViewDetail> getTreeViewDetails() const = 0;
};

/** Optional activation for detail rows. Existing providers remain read-only.
 * The tree passes only the stable row key and opens no model transaction.
 * Implementations must revalidate the owning object and key, and may switch
 * presentation or open an editor using its own ordinary transaction path.
 */
class GuiExport TreeViewDetailActionProvider
{
public:
    virtual ~TreeViewDetailActionProvider();
    virtual bool activateTreeViewDetail(const std::string& key) = 0;
    /// Opt in only properties which change these rows; ordinary geometry and
    /// playback property updates must not force browser reconstruction.
    virtual bool treeViewDetailsAffectedBy(const std::string& propertyName) const
    {
        (void)propertyName;
        return false;
    }
};

}  // namespace Gui
