// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Gui/TreeViewDetail.h>

#include "ViewProviderDesignOperation.h"


namespace PartDesignGui
{

/** Presentation for one source-owned VibeScript Design History operation. */
class PartDesignGuiExport ViewProviderDesignScriptOperation: public ViewProviderDesignOperation,
                                                             public Gui::TreeViewDetailProvider
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesignGui::ViewProviderDesignScriptOperation);

public:
    ViewProviderDesignScriptOperation();
    ~ViewProviderDesignScriptOperation() override;

    void attach(App::DocumentObject* object) override;
    std::vector<Gui::TreeViewDetail> getTreeViewDetails() const override;
};

}  // namespace PartDesignGui
