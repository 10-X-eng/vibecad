// SPDX-License-Identifier: LGPL-2.0-or-later

/***************************************************************************
 *   Copyright (c) 2024 WandererFan <wandererfan@gmail.com>                *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

//! CommandHelpers is a collection of methods for common actions in commands

#include <QCoreApplication>
#include <QMessageBox>

#include <sstream>
#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentTimeline.h>
#include <App/Link.h>

#include <Base/Exception.h>
#include <Base/Interpreter.h>

#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/MainWindow.h>
#include <Gui/Macro.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>

#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/DrawViewSpreadsheet.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/Preferences.h>

#include <Mod/TechDraw/Gui/PreferencesGui.h>
#include <Mod/TechDraw/Gui/DrawGuiUtil.h>


#include "CommandHelpers.h"

using namespace TechDraw;
using namespace TechDrawGui;

//! find the first DrawView in the current selection for use as a base view (owner)
TechDraw::DrawView* CommandHelpers::firstViewInSelection(Gui::Command* cmd)
{
    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    TechDraw::DrawView* baseView {nullptr};
    if (!selection.empty()) {
        for (auto& selobj : selection) {
            auto* docobj = selobj.getObject();
            if (docobj && docobj->getDocument() == cmd->getDocument()
                && docobj->isDerivedFrom<DrawView>() && DrawUtil::isActiveInDocumentTimeline(docobj)) {
                baseView = static_cast<TechDraw::DrawView*>(docobj);
                if (!baseView->isActiveInDocumentTimeline()) {
                    baseView = nullptr;
                    continue;
                }
                break;
            }
        }
    }
    return baseView;
}

std::vector<std::string> CommandHelpers::getSelectedSubElements(
    Gui::Command* cmd,
    TechDraw::DrawViewPart*& dvp,
    std::string subType
)
{
    std::vector<std::string> selectedSubs;
    std::vector<std::string> subNames;
    dvp = nullptr;
    std::vector<Gui::SelectionObject> selection = cmd->getSelection().getSelectionEx();
    std::vector<Gui::SelectionObject>::iterator itSel = selection.begin();
    for (; itSel != selection.end(); itSel++) {
        auto* object = (*itSel).getObject();
        if (object && object->getDocument() == cmd->getDocument()
            && object->isDerivedFrom<TechDraw::DrawViewPart>()
            && DrawUtil::isActiveInDocumentTimeline(object)) {
            dvp = static_cast<TechDraw::DrawViewPart*>(object);
            if (!dvp->isActiveInDocumentTimeline()) {
                dvp = nullptr;
                continue;
            }
            subNames = (*itSel).getSubNames();
            break;
        }
    }
    if (!dvp) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("No part view in selection")
        );
        return selectedSubs;
    }

    for (auto& s : subNames) {
        if (TechDraw::DrawUtil::getGeomTypeFromName(s) == subType) {
            selectedSubs.push_back(s);
        }
    }

    if (selectedSubs.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("No %1 in selection").arg(QString::fromStdString(subType))
        );
        return selectedSubs;
    }

    return selectedSubs;
}


std::pair<App::DocumentObject*, std::string> CommandHelpers::faceFromSelection()
{
    auto selection = Gui::Selection().getSelectionEx(
        nullptr,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );

    if (selection.empty()) {
        return {nullptr, ""};
    }

    for (auto& sel : selection) {
        auto* object = sel.getObject();
        if (!TechDraw::DrawUtil::isActiveInDocumentTimeline(object)) {
            continue;
        }
        auto* drawingView = dynamic_cast<TechDraw::DrawView*>(object);
        if (drawingView && !drawingView->isActiveInDocumentTimeline()) {
            continue;
        }
        for (auto& sub : sel.getSubNames()) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(sub) == "Face") {
                return {object, sub};
            }
        }
    }

    return {nullptr, ""};
}

App::DocumentObjectGroup* CommandHelpers::groupTimelineOutputs(
    App::Document* document,
    const std::vector<App::DocumentObject*>& outputs,
    const char* name,
    const char* label
)
{
    if (!document || outputs.size() < 2) {
        return nullptr;
    }

    std::unordered_set<App::DocumentObject*> distinct;
    std::vector<std::pair<App::DocumentObject*, long>> identities;
    identities.reserve(outputs.size());
    for (auto* output : outputs) {
        if (!output || output->getDocument() != document || !document->containsObject(output)
            || !distinct.insert(output).second) {
            throw Base::ValueError(
                "A grouped TechDraw operation requires distinct live outputs "
                "from one document"
            );
        }
        if (output->getPropertyByName(App::DocumentTimeline::RolePropertyName)
            || output->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
            throw Base::ValueError(
                "A TechDraw output already belongs to a document-history "
                "operation"
            );
        }
        identities.emplace_back(output, output->getID());
    }

    const std::string groupName = document->getUniqueObjectName(
        name && name[0] != '\0' ? name : "DrawingOperation"
    );
    const std::string documentName = Base::InterpreterSingleton::strToPython(document->getName());
    const std::string groupNameLiteral = Base::InterpreterSingleton::strToPython(groupName.c_str());
    const QString groupFactory
        = QStringLiteral(
              "App.getDocument('%1').addObject"
              "('App::DocumentObjectGroup', '%2')"
        )
              .arg(QString::fromStdString(documentName), QString::fromStdString(groupNameLiteral));
    auto* group = dynamic_cast<App::DocumentObjectGroup*>(Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        groupFactory.toUtf8(),
        App::DocumentObjectGroup::getClassTypeId()
    ));
    if (!group) {
        throw Base::RuntimeError("The TechDraw document-history operation group was not created");
    }
    auto* expectedGroup = group;
    const long groupId = group->getID();
    const auto resolveExactGroup = [document, expectedGroup, groupId]() -> App::DocumentObjectGroup* {
        auto* liveGroup = dynamic_cast<App::DocumentObjectGroup*>(document->getObjectByID(groupId));
        if (liveGroup != expectedGroup) {
            throw Base::RuntimeError(
                "The TechDraw document-history operation group changed "
                "during publication"
            );
        }
        return liveGroup;
    };
    const auto resolveExactOutput =
        [document](App::DocumentObject* expectedOutput, long outputId) -> App::DocumentObject* {
        auto* liveOutput = document->getObjectByID(outputId);
        if (liveOutput != expectedOutput) {
            throw Base::RuntimeError("A TechDraw output changed during history publication");
        }
        return liveOutput;
    };
    const std::string groupCommand = Gui::Command::getObjectCmd(group);
    const std::string displayLabel = label && label[0] != '\0'
        ? QCoreApplication::translate("Command", label).toStdString()
        : group->getNameInDocument();
    const std::string groupLabel = Base::InterpreterSingleton::strToPython(displayLabel);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.Label = '%s'",
        groupCommand.c_str(),
        groupLabel.c_str()
    );
    group = resolveExactGroup();

    for (const auto& [expectedOutput, outputId] : identities) {
        group = resolveExactGroup();
        auto* output = resolveExactOutput(expectedOutput, outputId);
        const std::string liveGroupCommand = Gui::Command::getObjectCmd(group);
        const std::string outputCommand = Gui::Command::getObjectCmd(output);
        Gui::Command::doCommand(
            Gui::Command::Doc,
            "%s.addObject(%s)",
            liveGroupCommand.c_str(),
            outputCommand.c_str()
        );
        group = resolveExactGroup();
        output = resolveExactOutput(expectedOutput, outputId);
        if (!group->hasObject(output)) {
            throw Base::RuntimeError(
                "A TechDraw output could not be added to its history "
                "controller"
            );
        }
    }

    group = resolveExactGroup();
    std::vector<App::DocumentObject*> liveOutputs;
    liveOutputs.reserve(identities.size());
    for (const auto& [expectedOutput, outputId] : identities) {
        liveOutputs.push_back(resolveExactOutput(expectedOutput, outputId));
    }

    auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline) {
        throw Base::RuntimeError("The TechDraw history controller disappeared");
    }
    timeline->publishProvisionalOperationBlock(group, liveOutputs);

    group = resolveExactGroup();
    liveOutputs.clear();
    for (const auto& [expectedOutput, outputId] : identities) {
        liveOutputs.push_back(resolveExactOutput(expectedOutput, outputId));
    }
    if (!App::DocumentTimeline::hasTimelineOperationRole(group)) {
        throw Base::RuntimeError("The TechDraw history controller has invalid operation metadata");
    }
    for (auto* output : liveOutputs) {
        if (!App::DocumentTimeline::hasTimelineResourceRole(output)
            || App::DocumentTimeline::timelineOwner(output) != group) {
            throw Base::RuntimeError("A TechDraw output has invalid history ownership metadata");
        }
    }
    if (Gui::Application::Instance && Gui::Application::Instance->macroManager()) {
        std::ostringstream publication;
        publication << "App.getDocument('" << documentName
                    << "').publishProvisionalTimelineOperationBlock("
                    << Gui::Command::getObjectCmd(group) << ", [";
        for (std::size_t index = 0; index < liveOutputs.size(); ++index) {
            if (index) {
                publication << ", ";
            }
            publication << Gui::Command::getObjectCmd(liveOutputs[index]);
        }
        publication << "])";
        Gui::Application::Instance->macroManager()->addLine(
            Gui::MacroManager::App,
            publication.str().c_str()
        );
    }

    return group;
}
