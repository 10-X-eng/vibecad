// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Gui/TreeViewDetail.h>
#include "ViewProviderPython.h"

namespace PartGui
{

/// Opt-in cached presentation with Python-provided, non-object tree details.
/// A separate subtype preserves the layout and behavior of existing providers.
class PartGuiExport ViewProviderCachedDetails: public ViewProviderCached,
                                               public Gui::TreeViewDetailProvider,
                                               public Gui::TreeViewDetailActionProvider
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartGui::ViewProviderCachedDetails);

public:
    ViewProviderCachedDetails();
    ~ViewProviderCachedDetails() override;
    std::vector<Gui::TreeViewDetail> getTreeViewDetails() const override;
    bool activateTreeViewDetail(const std::string& key) override;
    bool treeViewDetailsAffectedBy(const std::string& propertyName) const override;
};

using ViewProviderCachedDetailsPython = Gui::ViewProviderFeaturePythonT<ViewProviderCachedDetails>;

} // namespace PartGui
