// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ViewProviderDesignScriptOperation.h"

#include <QMenu>
#include <QObject>
#include <QString>

#include <algorithm>
#include <cstddef>
#include <string>
#include <unordered_map>
#include <vector>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Selection/Selection.h>
#include <Mod/PartDesign/App/DesignFeature.h>


using namespace PartDesignGui;

namespace
{

constexpr const char* timelineEditorCapability = "VibeCADTimelineOperationEditor";

std::string utf8(const QString& text)
{
    return text.toUtf8().toStdString();
}

Gui::ApprovedDocumentTimelineCommand approvedScriptedModelCommand(
    App::DocumentObject* operation,
    bool requireActive
)
{
    return Gui::approvedDocumentTimelineCommand(
        operation,
        App::DocumentTimeline::EditCommandPropertyName,
        timelineEditorCapability,
        requireActive
    );
}

bool invokeScriptedModelCommand(App::DocumentObject* operation)
{
    if (!operation || !operation->isAttachedToDocument()
        || !Gui::Application::Instance) {
        return false;
    }
    const auto approved = approvedScriptedModelCommand(operation, false);
    if (!approved.command) {
        return false;
    }

    Gui::Selection().clearSelection();
    Gui::Selection().addSelection(
        operation->getDocument()->getName(),
        operation->getNameInDocument()
    );
    const auto current = approvedScriptedModelCommand(operation, true);
    if (!current.command || current.name != approved.name
        || current.command != approved.command) {
        return false;
    }
    current.command->invoke(0, Gui::Command::TriggerAction);
    return true;
}

}  // namespace

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
}

bool ViewProviderDesignScriptOperation::doubleClicked()
{
    if (invokeScriptedModelCommand(getObject())) {
        return true;
    }
    return ViewProviderDesignOperation::doubleClicked();
}

const char* ViewProviderDesignScriptOperation::getTransactionText() const
{
    // The registered VibeScript editor owns its lifecycle. If that optional
    // command is unavailable, preserve the complete native Part Design edit
    // path, including its enclosing transaction.
    return approvedScriptedModelCommand(getObject(), false).command
        ? nullptr
        : ViewProviderDesignOperation::getTransactionText();
}

void ViewProviderDesignScriptOperation::setupContextMenu(
    QMenu* menu,
    QObject* receiver,
    const char* member
)
{
    const auto approved = approvedScriptedModelCommand(getObject(), true);
    if (approved.command) {
        approved.command->addTo(menu);
        // Skip ViewProviderDesignOperation's generic "Edit Operation" action:
        // VibeScript source is edited by its exact lifecycle command, not by
        // the native Design-target task dialog.
        ViewProvider::setupContextMenu(menu, receiver, member);
        return;
    }
    // Preserve standalone Part Design compatibility if the optional VibeCAD
    // GUI module has not registered its source editor command.
    ViewProviderDesignOperation::setupContextMenu(menu, receiver, member);
}

std::vector<Gui::TreeViewDetail> ViewProviderDesignScriptOperation::getTreeViewDetails() const
{
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

    // Output keys are the authoritative produced-output identities. Older or
    // partially restored documents may not have a parallel type entry; that
    // must not make a real produced Body disappear from Design History.
    const std::size_t outputCount = keys.size();
    std::vector<Gui::TreeViewDetail> details;
    details.reserve(outputCount);
    for (std::size_t index = 0; index < outputCount; ++index) {
        const std::string& key = keys[index];
        const std::string type =
            index < types.size() ? types[index] : std::string {};
        if (key.empty()) {
            continue;
        }
        const auto label = humanLabels.find(key);
        const std::string displayName = label == humanLabels.end() ? key : label->second;
        const std::string icon = type == "solid"
            ? "PartDesign_Body"
            : (type == "component_link" ? "Geoassembly" : "vibecad");
        const QString toolTip = type.empty()
            ? QObject::tr(
                  "VibeScript output '%1'. Its stable interface is listed "
                  "under Published Outputs."
              )
                  .arg(QString::fromUtf8(key.c_str()))
            : QObject::tr(
                  "VibeScript output '%1' (%2). Its stable interface is "
                  "listed under Published Outputs."
              )
                  .arg(QString::fromUtf8(key.c_str()))
                  .arg(QString::fromUtf8(type.c_str()));
        details.push_back({
            "output:" + key,
            utf8(
                QObject::tr("Produces %1")
                    .arg(QString::fromUtf8(displayName.c_str()))
            ),
            type,
            utf8(toolTip),
            icon,
        });
    }
    return details;
}
