// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>

#include "PartFeature.h"

namespace Part
{

/**
 * A recomputable set of planar cross-sections of one source shape.
 *
 * PlanePositions are signed distances along the normalized PlaneNormal.
 * Source preserves either the complete source object or the exact selected
 * subelements.  The resulting Shape is rebuilt whenever any input changes.
 */
class PartExport CrossSections: public Part::Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(Part::CrossSections);

public:
    CrossSections();

    App::PropertyLinkSub Source;
    App::PropertyVector PlaneNormal;
    App::PropertyFloatList PlanePositions;

    App::DocumentObjectExecReturn* execute() override;
};

}  // namespace Part
