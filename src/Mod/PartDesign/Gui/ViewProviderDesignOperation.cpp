// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ViewProviderDesignOperation.h"

#include <QMenu>

#include <Mod/PartDesign/App/DesignFeature.h>

#include "TaskFeatureParameters.h"

using namespace PartDesignGui;

PROPERTY_SOURCE(PartDesignGui::ViewProviderDesignOperation, PartDesignGui::ViewProvider)

ViewProviderDesignOperation::ViewProviderDesignOperation()
{
    sPixmap = "PartDesign_Boolean.svg";
}

ViewProviderDesignOperation::~ViewProviderDesignOperation() = default;

void ViewProviderDesignOperation::attach(App::DocumentObject* object)
{
    if (freecad_cast<PartDesign::DesignSplit*>(object)) {
        sPixmap = "Part_SliceApart.svg";
    }
    else if (freecad_cast<PartDesign::DesignSeparate*>(object)) {
        sPixmap = "Part_ExplodeCompound.svg";
    }
    else if (freecad_cast<PartDesign::DesignScale*>(object)) {
        sPixmap = "Part_Scale.svg";
    }
    else {
        sPixmap = "PartDesign_Boolean.svg";
    }
    ViewProvider::attach(object);
    // Design-global operations are non-rendering History controllers. Their
    // results are the durable Bodies they own, so an eye on the controller is
    // both ineffective and misleading.
    setToggleVisibility(ToggleVisibilityMode::NoToggleVisibility);
}

void ViewProviderDesignOperation::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    addDefaultAction(menu, QObject::tr("Edit Operation"));
    ViewProvider::setupContextMenu(menu, receiver, member);
}

TaskDlgFeatureParameters* ViewProviderDesignOperation::getEditDialog()
{
    return new TaskDlgFeatureParameters(this);
}
