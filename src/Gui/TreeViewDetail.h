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

}  // namespace Gui
