// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/Part.h>
#include <App/PropertyStandard.h>
#include <Mod/PartDesign/PartDesignGlobal.h>

namespace PartDesign
{

/**
 * A physical product definition inside one VibeCAD Design.
 *
 * Component is an assembly, BOM, motion, and coordinate-frame boundary. It is
 * deliberately not a modeling-history boundary: sketches and operations stay
 * at the Design root, while Bodies may be grouped below a Component.
 */
class PartDesignExport Component: public App::Part
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::Component);

public:
    Component();

    App::PropertyUUID ComponentId;
    App::PropertyUUID DesignId;

    void setupObject() override;
    void onChanged(const App::Property* property) override;
};

}  // namespace PartDesign
