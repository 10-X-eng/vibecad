// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <App/DocumentTimeline.h>
#include <App/SuppressibleExtension.h>

namespace Robot::TimelineSupport
{

inline bool isSuppressedOrInactive(const App::DocumentObject& operation) noexcept
{
    const auto* suppressible = operation.getExtensionByType<App::SuppressibleExtension>(true);
    if (suppressible && suppressible->Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(operation.getDocument());
    return timeline && !timeline->isOperationActive(&operation);
}

inline bool isUsableInput(const App::DocumentObject& operation, const App::DocumentObject* input) noexcept
{
    return input && input != &operation
        && App::DocumentTimeline::isObjectUsableAtCurrentPosition(input);
}

}  // namespace Robot::TimelineSupport
