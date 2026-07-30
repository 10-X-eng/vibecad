// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <algorithm>

#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/SuppressibleExtension.h>

namespace Surface::TimelineSupport
{

inline bool isSuppressedOrInactive(
    const App::DocumentObject& operation,
    const App::SuppressibleExtension& suppressible
) noexcept
{
    if (suppressible.Suppressed.getValue()) {
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

inline bool areUsableInputs(
    const App::DocumentObject& operation,
    const App::PropertyLinkSubList& links
) noexcept
{
    const auto inputs = links.getValues();
    return std::ranges::all_of(inputs, [&operation](const App::DocumentObject* input) {
        return isUsableInput(operation, input);
    });
}

inline bool isUsableOptionalInput(
    const App::DocumentObject& operation,
    const App::PropertyLinkSub& link
) noexcept
{
    const auto* input = link.getValue();
    return !input || isUsableInput(operation, input);
}

}  // namespace Surface::TimelineSupport
