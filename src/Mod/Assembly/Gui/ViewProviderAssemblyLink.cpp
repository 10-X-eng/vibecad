// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/


#include <QAction>
#include <QMenu>
#include <algorithm>
#include <exception>
#include <optional>
#include <ranges>
#include <string>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>


#include <App/Application.h>
#include <App/Link.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <Base/Console.h>
#include <Base/Exception.h>

#include <Gui/Action.h>
#include <Gui/ActionFunction.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/MainWindow.h>

#include <Mod/Assembly/App/AssemblyObject.h>
#include <Mod/Assembly/App/AssemblyLink.h>
#include <Mod/Assembly/App/AssemblyUtils.h>

#include "ViewProviderAssembly.h"
#include "ViewProviderAssemblyLink.h"


using namespace Assembly;
using namespace AssemblyGui;

namespace
{
struct AssemblyLinkIdentity
{
    std::string documentName;
    std::string documentUid;
    std::string objectName;
    long objectId {-1};
};

AssemblyLinkIdentity assemblyLinkIdentityOf(
    const Assembly::AssemblyLink* link
)
{
    if (!link || !link->isAttachedToDocument()) {
        return {};
    }
    return {
        link->getDocument()->getName(),
        link->getDocument()->Uid.getValueStr(),
        link->getNameInDocument(),
        link->getID(),
    };
}

std::pair<App::Document*, Assembly::AssemblyLink*> resolveAssemblyLink(
    const AssemblyLinkIdentity& identity
) noexcept
{
    try {
        auto* document = identity.documentName.empty()
            ? nullptr
            : App::GetApplication().getDocument(
                  identity.documentName.c_str()
              );
        if (!document
            || document->Uid.getValueStr() != identity.documentUid) {
            return {};
        }
        auto* link = freecad_cast<Assembly::AssemblyLink*>(
            document->getObject(identity.objectName.c_str())
        );
        if (!link || link->getID() != identity.objectId) {
            return {document, nullptr};
        }
        return {document, link};
    }
    catch (...) {
        return {};
    }
}

const App::DocumentObject* semanticTimelineRoot(
    const App::DocumentObject* object
) noexcept
{
    std::unordered_set<const App::DocumentObject*> visited;
    while (object
           && App::DocumentTimeline::hasTimelineResourceRole(object)) {
        if (!visited.insert(object).second) {
            return nullptr;
        }
        object = App::DocumentTimeline::timelineOwner(object);
    }
    return object;
}

struct TimelineMemberSnapshot
{
    long objectId {-1};
    bool visible {false};
    bool suppressed {false};
};

struct TimelineBlockSnapshot
{
    long rootId {-1};
    std::vector<TimelineMemberSnapshot> members;
};

struct TimelineSnapshot
{
    std::vector<TimelineBlockSnapshot> blocks;
    long position {0};
    std::size_t memberCount {0};
};

bool sameTimelineMember(
    const TimelineMemberSnapshot& left,
    const TimelineMemberSnapshot& right
) noexcept
{
    return left.objectId == right.objectId
        && left.visible == right.visible
        && left.suppressed == right.suppressed;
}

bool sameTimelineBlock(
    const TimelineBlockSnapshot& left,
    const TimelineBlockSnapshot& right
) noexcept
{
    if (left.rootId != right.rootId
        || left.members.size() != right.members.size()) {
        return false;
    }
    return std::ranges::equal(
        left.members,
        right.members,
        sameTimelineMember
    );
}

std::optional<TimelineSnapshot> captureTimelineSnapshot(
    const App::DocumentTimeline* timeline
) noexcept
{
    if (!timeline) {
        return TimelineSnapshot {};
    }
    try {
        const auto operations = timeline->Operations.getValues();
        const auto visibility = timeline->VisibilityAtEnd.getValues();
        const auto suppression = timeline->SuppressionAtEnd.getValues();
        if (visibility.size() != operations.size()
            || suppression.size() != operations.size()) {
            return std::nullopt;
        }

        TimelineSnapshot snapshot;
        snapshot.position = timeline->Position.getValue();
        snapshot.memberCount = operations.size();
        std::unordered_set<const App::DocumentObject*> operationSet;
        operationSet.reserve(operations.size());
        for (const auto* operation : operations) {
            if (!operation || !operationSet.insert(operation).second) {
                return std::nullopt;
            }
        }

        std::unordered_set<long> seenRootIds;
        for (std::size_t index = 0; index < operations.size(); ++index) {
            const auto* operation = operations[index];
            const auto* root = semanticTimelineRoot(operation);
            if (!root || !operationSet.contains(root)) {
                return std::nullopt;
            }
            const long rootId = root->getID();
            if (snapshot.blocks.empty()
                || snapshot.blocks.back().rootId != rootId) {
                if (!seenRootIds.insert(rootId).second) {
                    return std::nullopt;
                }
                snapshot.blocks.push_back(
                    {.rootId = rootId, .members = {}}
                );
            }
            snapshot.blocks.back().members.push_back(
                {
                    .objectId = operation->getID(),
                    .visible = visibility[index],
                    .suppressed = suppression[index],
                }
            );
        }
        for (const auto& block : snapshot.blocks) {
            if (block.members.empty()
                || block.members.back().objectId != block.rootId
                || std::ranges::count_if(
                       block.members,
                       [&block](const TimelineMemberSnapshot& member) {
                           return member.objectId == block.rootId;
                       }
                   ) != 1) {
                return std::nullopt;
            }
        }
        if (snapshot.position < 0
            || snapshot.position > static_cast<long>(snapshot.memberCount)) {
            return std::nullopt;
        }
        return snapshot;
    }
    catch (...) {
        return std::nullopt;
    }
}
}  // namespace


PROPERTY_SOURCE(AssemblyGui::ViewProviderAssemblyLink, Gui::ViewProviderPart)

ViewProviderAssemblyLink::ViewProviderAssemblyLink()
{}

ViewProviderAssemblyLink::~ViewProviderAssemblyLink() = default;

QIcon ViewProviderAssemblyLink::getIcon() const
{
    auto* assembly = dynamic_cast<Assembly::AssemblyLink*>(getObject());
    if (assembly->isRigid()) {
        return Gui::BitmapFactory().pixmap("Assembly_AssemblyLinkRigid.svg");
    }
    else {
        return Gui::BitmapFactory().pixmap("Assembly_AssemblyLink.svg");
    }
}

bool ViewProviderAssemblyLink::setEdit(int mode)
{
    auto* assemblyLink = dynamic_cast<Assembly::AssemblyLink*>(getObject());
    if (!Assembly::isTimelineOperationActive(assemblyLink)) {
        return false;
    }

    if (!assemblyLink->isRigid() && mode == (int)ViewProvider::Transform) {
        Base::Console().userTranslatedNotification("Flexible sub-assemblies cannot be transformed.");
        return true;
    }

    return ViewProviderPart::setEdit(mode);
}

bool ViewProviderAssemblyLink::doubleClicked()
{
    auto* link = freecad_cast<AssemblyLink*>(getObject());
    if (!link || !Assembly::isTimelineOperationActive(link)) {
        return true;
    }
    auto* assembly = link->getLinkedAssembly();
    if (!Assembly::isTimelineOperationActive(assembly)) {
        return true;
    }

    auto* vpa = freecad_cast<ViewProviderAssembly*>(
        Gui::Application::Instance->getViewProvider(assembly)
    );
    if (!vpa) {
        return true;
    }

    auto doc = assembly->getDocument();
    auto guiDoc = vpa->getDocument();
    if (!doc || !guiDoc) {
        return true;
    }

    Gui::MDIView* mdi = guiDoc->getActiveView();

    // Ensure the linked assembly document is fully loaded and has a view
    if (doc->testStatus(App::Document::PartialDoc) || !mdi) {
        Gui::Application::Instance->reopen(doc);

        // reopening invalidates the pointer.
        auto* assembly = link->getLinkedAssembly();
        if (!Assembly::isTimelineOperationActive(assembly)) {
            return true;
        }

        vpa = freecad_cast<ViewProviderAssembly*>(
            Gui::Application::Instance->getViewProvider(assembly)
        );
        if (!vpa) {
            return true;
        }
    }

    return vpa->doubleClicked();
}

bool ViewProviderAssemblyLink::onDelete(const std::vector<std::string>& subNames)
{
    Q_UNUSED(subNames)

    Gui::Command::doCommand(
        Gui::Command::Doc,
        "App.getDocument(\"%s\").getObject(\"%s\").removeObjectsFromDocument()",
        getObject()->getDocument()->getName(),
        getObject()->getNameInDocument()
    );

    // getObject()->purgeTouched();

    return ViewProviderPart::onDelete(subNames);
}

void ViewProviderAssemblyLink::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    auto func = new Gui::ActionFunction(menu);
    QAction* act;
    auto* assemblyLink = dynamic_cast<Assembly::AssemblyLink*>(getObject());
    if (!Assembly::isTimelineOperationActive(assemblyLink)) {
        return;
    }
    if (assemblyLink->isRigid()) {
        act = menu->addAction(QObject::tr("Turn flexible"));
        act->setObjectName(QStringLiteral("AssemblyContextMakeFlexible"));
        act->setToolTip(
            QObject::tr("Your sub-assembly is currently rigid. This will make it flexible instead.")
        );
    }
    else {
        act = menu->addAction(QObject::tr("Turn rigid"));
        act->setObjectName(QStringLiteral("AssemblyContextMakeRigid"));
        act->setToolTip(
            QObject::tr("Your sub-assembly is currently flexible. This will make it rigid instead.")
        );
    }

    const auto identity = assemblyLinkIdentityOf(assemblyLink);
    func->trigger(act, [identity]() {
        auto [document, link] = resolveAssemblyLink(identity);
        if (!document || !link
            || !Assembly::isTimelineOperationActive(link)
            || document->getBookedTransactionID()
                != App::NullTransaction
            || document->hasPendingTransaction()
            || Gui::Control().activeDialog(document)) {
            return;
        }

        const bool desiredRigid = !link->Rigid.getValue();
        auto* parentAssembly = link->getParentAssembly();
        if (!Assembly::isTimelineOperationActive(parentAssembly)
            || parentAssembly->getDocument() != document
            || !parentAssembly->hasObject(link, true)) {
            return;
        }
        auto* linkedAssembly = link->getLinkedAssembly();
        if (!Assembly::isTimelineOperationActive(linkedAssembly)) {
            return;
        }
        const long parentAssemblyId =
            parentAssembly->getID();
        std::unordered_set<long> expectedDeletedGroundJointIds;
        for (auto* joint : parentAssembly->getGroundedJoints()) {
            const auto* groundedProperty =
                joint
                ? dynamic_cast<const App::PropertyLink*>(
                      joint->getPropertyByName("ObjectToGround")
                  )
                : nullptr;
            auto* groundedObject =
                groundedProperty
                ? groundedProperty->getValue()
                : nullptr;
            if (auto* linkElement =
                    dynamic_cast<App::LinkElement*>(groundedObject)) {
                groundedObject = linkElement->getLinkGroup();
            }
            if (joint && groundedObject
                && (desiredRigid
                        ? link->hasObject(groundedObject)
                        : groundedObject == link)) {
                expectedDeletedGroundJointIds.insert(
                    joint->getID()
                );
            }
        }
        const auto* timeline = App::DocumentTimeline::get(document);
        const bool hadTimeline = timeline != nullptr;
        const auto timelineBefore = captureTimelineSnapshot(timeline);
        if (!timelineBefore
            || timelineBefore->position
                != static_cast<long>(timelineBefore->memberCount)) {
            return;
        }
        const int transactionId =
            Gui::Command::openDocumentCommand(
                document,
                QT_TRANSLATE_NOOP("Command", "Toggle Rigid")
            );
        if (transactionId == App::NullTransaction
            || document->getBookedTransactionID()
                != transactionId
            || !App::GetApplication().transactionIsActive(
                transactionId
            )) {
            return;
        }
        const auto abortOwnedTransaction =
            [&identity, transactionId]() {
                auto [currentDocument, currentLink] =
                    resolveAssemblyLink(identity);
                Q_UNUSED(currentLink)
                if (currentDocument
                    && currentDocument->getBookedTransactionID()
                        == transactionId
                    && App::GetApplication().transactionIsActive(
                        transactionId
                    )) {
                    Gui::Command::abortCommand(transactionId);
                }
            };

        try {
            if (document->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "The Toggle Rigid transaction changed before editing"
                );
            }
            Gui::cmdAppObjectArgs(
                link,
                "Rigid = %s",
                desiredRigid ? "True" : "False"
            );

            std::tie(document, link) =
                resolveAssemblyLink(identity);
            if (!document || !link
                || document->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "The assembly link or Toggle Rigid transaction changed "
                    "while editing"
                );
            }
            Gui::Command::updateDocument(document);

            std::tie(document, link) =
                resolveAssemblyLink(identity);
            if (!document || !link
                || document->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "The assembly link or Toggle Rigid transaction changed "
                    "while recomputing"
                );
            }
            parentAssembly = freecad_cast<AssemblyObject*>(
                document->getObjectByID(parentAssemblyId)
            );
            if (!Assembly::isTimelineOperationActive(parentAssembly)
                || !parentAssembly->hasObject(link, true)) {
                throw Base::RuntimeError(
                    "The parent assembly changed while toggling rigidity"
                );
            }
            timeline = App::DocumentTimeline::get(document);
            if ((timeline != nullptr) != hadTimeline) {
                throw Base::RuntimeError(
                    "Toggling rigidity changed the document History identity"
                );
            }
            const auto timelineAfter = captureTimelineSnapshot(timeline);
            if (!timelineAfter) {
                throw Base::RuntimeError(
                    "Toggling rigidity produced a malformed document History"
                );
            }
            if (hadTimeline) {
                std::size_t afterBlockIndex = 0;
                bool foundLinkBlock = false;
                std::unordered_set<long> removedGroundRootIds;
                for (const auto& beforeBlock : timelineBefore->blocks) {
                    if (expectedDeletedGroundJointIds.contains(
                            beforeBlock.rootId
                        )) {
                        removedGroundRootIds.insert(beforeBlock.rootId);
                        for (const auto& member : beforeBlock.members) {
                            if (document->getObjectByID(member.objectId)) {
                                throw Base::RuntimeError(
                                    "Toggling rigidity left an incompatible "
                                    "grounding History block in the document"
                                );
                            }
                        }
                        continue;
                    }
                    if (afterBlockIndex >= timelineAfter->blocks.size()) {
                        throw Base::RuntimeError(
                            "Toggling rigidity deleted an unrelated semantic "
                            "History block"
                        );
                    }
                    const auto& afterBlock =
                        timelineAfter->blocks[afterBlockIndex++];
                    if (afterBlock.rootId != beforeBlock.rootId) {
                        throw Base::RuntimeError(
                            "Toggling rigidity inserted or reordered a semantic "
                            "History block"
                        );
                    }
                    if (beforeBlock.rootId != identity.objectId) {
                        if (!sameTimelineBlock(beforeBlock, afterBlock)) {
                            throw Base::RuntimeError(
                                "Toggling rigidity changed an unrelated semantic "
                                "History block"
                            );
                        }
                        continue;
                    }

                    foundLinkBlock = true;
                    if (beforeBlock.members.empty()
                        || afterBlock.members.empty()
                        || !sameTimelineMember(
                            beforeBlock.members.back(),
                            afterBlock.members.back()
                        )) {
                        throw Base::RuntimeError(
                            "Toggling rigidity changed the AssemblyLink History root"
                        );
                    }
                    for (const auto& beforeMember : beforeBlock.members) {
                        if (!document->getObjectByID(beforeMember.objectId)) {
                            continue;
                        }
                        const auto retained = std::ranges::find_if(
                            afterBlock.members,
                            [&beforeMember](const TimelineMemberSnapshot& member) {
                                return member.objectId == beforeMember.objectId;
                            }
                        );
                        if (retained == afterBlock.members.end()
                            || !sameTimelineMember(beforeMember, *retained)) {
                            throw Base::RuntimeError(
                                "Toggling rigidity changed or detached a retained "
                                "AssemblyLink History resource"
                            );
                        }
                    }
                }
                if (afterBlockIndex != timelineAfter->blocks.size()) {
                    throw Base::RuntimeError(
                        "Toggling rigidity inserted an unrelated semantic "
                        "History block"
                    );
                }
                if (!foundLinkBlock) {
                    throw Base::RuntimeError(
                        "The AssemblyLink has no semantic History block"
                    );
                }
                if (removedGroundRootIds != expectedDeletedGroundJointIds) {
                    throw Base::RuntimeError(
                        "The expected grounding operations do not match exact "
                        "semantic History roots"
                    );
                }
            }
            for (const long jointId : expectedDeletedGroundJointIds) {
                if (document->getObjectByID(jointId)) {
                    throw Base::RuntimeError(
                        "Toggling rigidity left an incompatible grounded joint in the assembly"
                    );
                }
            }
            if (!document || !link || !link->isValid()
                || link->Rigid.getValue() != desiredRigid
                || document->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )
                || timelineAfter->position
                    != static_cast<long>(timelineAfter->memberCount)) {
                throw Base::RuntimeError(
                    "Toggling assembly rigidity produced an invalid tracked state"
                );
            }

            Gui::Command::commitCommand(transactionId);
            Gui::Selection().clearSelection(
                identity.documentName.c_str()
            );
        }
        catch (const Base::Exception& error) {
            abortOwnedTransaction();
            error.reportException();
        }
        catch (const std::exception& error) {
            abortOwnedTransaction();
            Base::Console().error(
                "Toggling assembly rigidity failed: %s\n",
                error.what()
            );
        }
        catch (...) {
            abortOwnedTransaction();
            Base::Console().error(
                "Toggling assembly rigidity failed\n"
            );
        }
    });

    Gui::CommandManager& mgr = Gui::Application::Instance->commandManager();
    Gui::Command* cmd = mgr.getCommandByName("Assembly_LinkSelectLinked");
    if (cmd && cmd->getAction()) {
        QAction* action = cmd->getAction()->action();
        if (action) {
            menu->addAction(action);
        }
    }

    Q_UNUSED(receiver)
    Q_UNUSED(member)
}
