// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include "ViewProvider.h"

namespace PartDesignGui
{

/**
 * View provider for Design-root operations which have no legacy Body-owned
 * parameter panel.
 */
class PartDesignGuiExport ViewProviderDesignOperation: public ViewProvider
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesignGui::ViewProviderDesignOperation);

public:
    ViewProviderDesignOperation();
    ~ViewProviderDesignOperation() override;

    void attach(App::DocumentObject* object) override;

    bool supportsDocumentTimelineEdit() const noexcept override
    {
        return true;
    }

    void setupContextMenu(QMenu* menu, QObject* receiver, const char* member) override;

protected:
    TaskDlgFeatureParameters* getEditDialog() override;
};

}  // namespace PartDesignGui
