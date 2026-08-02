// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>

namespace MeasureGui
{

/**
 * Return whether an exact measurement input belongs to the document state at
 * the current History marker.
 *
 * Root links are checked together with their resolved definitions. This keeps
 * quick measurements, durable annotations, and mass properties from reading a
 * future operation through an otherwise-active link occurrence.
 */
inline bool isTimelineSelectionActive(const App::DocumentObject* object) noexcept
{
    try {
        if (!App::DocumentTimeline::
                isObjectUsableAtCurrentPosition(object)) {
            return false;
        }

        const auto* linked = object->getLinkedObject(true);
        return !linked || linked == object
            || App::DocumentTimeline::
                   isObjectUsableAtCurrentPosition(linked);
    }
    catch (...) {
        return false;
    }
}

}  // namespace MeasureGui
