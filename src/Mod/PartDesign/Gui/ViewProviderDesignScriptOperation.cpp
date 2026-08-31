// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ViewProviderDesignScriptOperation.h"

#include <algorithm>
#include <cstddef>
#include <string>
#include <unordered_map>
#include <vector>

#include <Mod/PartDesign/App/DesignFeature.h>


using namespace PartDesignGui;

PROPERTY_SOURCE(
    PartDesignGui::ViewProviderDesignScriptOperation,
    PartDesignGui::ViewProviderDesignOperation
)

ViewProviderDesignScriptOperation::ViewProviderDesignScriptOperation() = default;

ViewProviderDesignScriptOperation::~ViewProviderDesignScriptOperation() = default;

void ViewProviderDesignScriptOperation::attach(App::DocumentObject* object)
{
    ViewProviderDesignOperation::attach(object);
    sPixmap = "vibecad.svg";
    setToggleVisibility(ToggleVisibilityMode::NoToggleVisibility);
}

std::vector<Gui::TreeViewDetail> ViewProviderDesignScriptOperation::getTreeViewDetails() const
{
    constexpr std::size_t maxPublishedRows = 256;

    const auto* operation = dynamic_cast<const PartDesign::DesignScriptOperation*>(getObject());
    if (!operation) {
        return {};
    }

    const auto& keys = operation->ProgramOutputKeys.getValues();
    const auto& types = operation->ProgramOutputTypes.getValues();
    const auto& bodyKeys = operation->ScriptOutputKeys.getValues();
    const auto& bodyLabels = operation->ScriptOutputLabels.getValues();

    std::unordered_map<std::string, std::string> humanLabels;
    const std::size_t bodyCount = std::min(bodyKeys.size(), bodyLabels.size());
    humanLabels.reserve(bodyCount);
    for (std::size_t index = 0; index < bodyCount; ++index) {
        if (!bodyKeys[index].empty() && !bodyLabels[index].empty()) {
            humanLabels.emplace(bodyKeys[index], bodyLabels[index]);
        }
    }

    const std::size_t outputCount = std::min(keys.size(), types.size());
    std::vector<Gui::TreeViewDetail> details;
    details.reserve(std::min(outputCount, maxPublishedRows) + 1);
    for (std::size_t index = 0; index < outputCount && index < maxPublishedRows; ++index) {
        const std::string& key = keys[index];
        const std::string& type = types[index];
        if (key.empty() || type.empty()) {
            continue;
        }
        const auto label = humanLabels.find(key);
        const std::string displayName = label == humanLabels.end() ? key : label->second;
        const std::string icon = type == "solid"
            ? "PartDesign_Body"
            : (type == "component_link" ? "Geoassembly" : "vibecad");
        details.push_back({
            "output:" + key,
            "Produces " + displayName,
            type,
            "VibeScript output '" + key + "' (" + type
                + "). Its stable interface is listed under Published Outputs.",
            icon,
        });
    }
    if (outputCount > maxPublishedRows) {
        details.push_back({
            "output:remaining",
            "Produces " + std::to_string(outputCount - maxPublishedRows) + " more outputs",
            {},
            "The Design History output summary is bounded; all stable interfaces "
            "remain available under Published Outputs.",
            "vibecad",
        });
    }
    return details;
}
