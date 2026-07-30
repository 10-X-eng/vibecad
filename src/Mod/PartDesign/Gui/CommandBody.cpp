// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (C) 2015 Alexander Golubev (Fat-Zer) <fatzer2@gmail.com>    *
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


#include <QApplication>
#include <QByteArray>
#include <QInputDialog>
#include <QMessageBox>
#include <algorithm>
#include <map>
#include <ranges>
#include <set>
#include <sstream>
#include <TopExp_Explorer.hxx>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <App/Application.h>
#include <App/Datums.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/Origin.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Dialogs/DlgObjectSelection.h>
#include <Gui/Document.h>
#include <Gui/Application.h>
#include <Gui/FeatureTimeline.h>
#include <Gui/MainWindow.h>
#include <Gui/MDIView.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/TimelineImport.h>
#include <Gui/View3DInventor.h>
#include <Mod/Sketcher/App/SketchObject.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeatureBase.h>
#include <Mod/PartDesign/App/FeatureSketchBased.h>
#include <Mod/PartDesign/App/PartDesignParameter.h>

#include "ModelingContext.h"
#include "TaskFeaturePick.h"
#include "Utils.h"
#include "WorkflowManager.h"


//===========================================================================
// Shared functions
//===========================================================================

namespace PartDesignGui
{

/// Returns active part, if there is no such, creates a new part, if it fails, shows a message
App::Part* assertActivePart()
{
    App::Part* rv = Gui::Application::Instance->activeView()->getActiveObject<App::Part*>(PARTKEY);

    if (!rv) {
        Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
        rcCmdMgr.runCommandByName("Std_Part");
        rv = Gui::Application::Instance->activeView()->getActiveObject<App::Part*>(PARTKEY);
        if (!rv) {
            QMessageBox::critical(
                nullptr,
                QObject::tr("Part creation failed"),
                QObject::tr("Failed to create a part object.")
            );
        }
    }

    return rv;
}

}  // namespace PartDesignGui

namespace
{
// Fusion-style browser categories preserve native selection paths by
// representing a Body child as Body + "ChildName.". OldStyleElement resolves
// that exact child path but, unlike FollowLink, never redirects an occurrence
// to a shared linked definition.
constexpr Gui::ResolveMode exactBrowserChildResolveMode = Gui::ResolveMode::OldStyleElement;

struct BodyCreationObjectIdentity
{
    std::string documentName;
    std::string documentUid;
    std::string objectName;
    long objectId {-1};
};

BodyCreationObjectIdentity bodyCreationIdentityOf(
    const App::DocumentObject* object
)
{
    if (!object || !object->isAttachedToDocument()) {
        return {};
    }
    return {
        object->getDocument()->getName(),
        object->getDocument()->Uid.getValueStr(),
        object->getNameInDocument(),
        object->getID(),
    };
}

App::Document* resolveBodyCreationDocument(
    const std::string& documentName
)
{
    if (documentName.empty()) {
        return nullptr;
    }
    try {
        return App::GetApplication().getDocument(documentName.c_str());
    }
    catch (...) {
        return nullptr;
    }
}

App::DocumentObject* resolveBodyCreationObject(
    const BodyCreationObjectIdentity& identity
)
{
    auto* document = resolveBodyCreationDocument(identity.documentName);
    if (!document
        || document->Uid.getValueStr() != identity.documentUid) {
        return nullptr;
    }
    auto* object =
        !identity.objectName.empty()
        ? document->getObject(identity.objectName.c_str())
        : nullptr;
    return object && object->getID() == identity.objectId
        ? object
        : nullptr;
}

bool isMoveFeatureCandidate(const App::DocumentObject* object)
{
    return object
        && object->isDerivedFrom<Part::Feature>()
        && PartDesign::Body::isAllowed(object);
}

QStringList disambiguatedObjectLabels(
    const std::vector<App::DocumentObject*>& objects
)
{
    std::map<QString, std::size_t> labelCounts;
    const QString beginning =
        QObject::tr("Beginning of the body");
    for (const auto* object : objects) {
        ++labelCounts[
            object
            ? QString::fromUtf8(object->Label.getValue())
            : beginning
        ];
    }

    QStringList labels;
    labels.reserve(static_cast<qsizetype>(objects.size()));
    for (const auto* object : objects) {
        if (!object) {
            labels.push_back(beginning);
            continue;
        }
        const QString label =
            QString::fromUtf8(object->Label.getValue());
        labels.push_back(
            labelCounts.at(label) > 1
            ? QObject::tr("%1 — %2")
                  .arg(
                      label,
                      QString::fromUtf8(
                          object->getNameInDocument()
                      )
                  )
            : label
        );
    }
    return labels;
}

const App::DocumentObject* semanticTimelineRoot(
    const App::DocumentObject* object,
    const App::Document* document
)
{
    if (!object || !document || object->getDocument() != document
        || !document->containsObject(object)) {
        return nullptr;
    }

    std::unordered_set<const App::DocumentObject*> visited;
    auto* current = object;
    while (App::DocumentTimeline::hasTimelineResourceRole(current)) {
        if (!visited.insert(current).second) {
            return nullptr;
        }
        current = App::DocumentTimeline::timelineOwner(current);
        if (!current || current->getDocument() != document
            || !document->containsObject(current)) {
            return nullptr;
        }
    }
    return current;
}

std::vector<App::DocumentObject*> orderedSemanticTimelineRoots(
    const App::DocumentTimeline& timeline,
    const std::vector<App::DocumentObject*>& objects
)
{
    const auto* document = timeline.getDocument();
    const auto& operations = timeline.Operations.getValues();
    if (!document
        || timeline.Position.getValue() != static_cast<long>(operations.size())) {
        throw Base::RuntimeError(
            "Features can only be reordered at the current end of history"
        );
    }

    std::unordered_map<const App::DocumentObject*, std::size_t> indices;
    indices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!operation || operation->getDocument() != document
            || !document->containsObject(operation)
            || !indices.emplace(operation, index).second) {
            throw Base::RuntimeError(
                "The document history contains a missing or duplicate operation"
            );
        }
    }

    std::unordered_set<const App::DocumentObject*> seenRoots;
    std::vector<App::DocumentObject*> roots;
    roots.reserve(objects.size());
    for (auto* object : objects) {
        const auto* root = semanticTimelineRoot(object, document);
        if (!root || !indices.contains(object) || !indices.contains(root)) {
            throw Base::RuntimeError(
                "A selected feature is not part of a complete document-history block"
            );
        }
        if (seenRoots.insert(root).second) {
            roots.push_back(const_cast<App::DocumentObject*>(root));
        }
    }
    std::ranges::sort(
        roots,
        [&indices](
            const App::DocumentObject* left,
            const App::DocumentObject* right
        ) {
            return indices.at(left) < indices.at(right);
        }
    );
    return roots;
}

std::vector<App::DocumentObject*> bodyMembersForTimelineRoots(
    const PartDesign::Body& body,
    const std::vector<App::DocumentObject*>& roots
)
{
    std::unordered_set<const App::DocumentObject*> rootSet(roots.begin(), roots.end());
    std::vector<App::DocumentObject*> members;
    for (auto* member : body.Group.getValues()) {
        if (rootSet.contains(semanticTimelineRoot(member, body.getDocument()))) {
            members.push_back(member);
        }
    }
    return members;
}

bool bodyRemainderDependsOnMovedMembers(
    const PartDesign::Body& body,
    const std::vector<App::DocumentObject*>& movedMembers
)
{
    const auto* document = body.getDocument();
    if (!document) {
        return true;
    }

    const std::unordered_set<const App::DocumentObject*> moved(movedMembers.begin(), movedMembers.end());
    for (const auto* member : body.Group.getValues()) {
        if (!member || moved.contains(member)) {
            continue;
        }

        std::vector<const App::DocumentObject*> pending {member};
        std::unordered_set<const App::DocumentObject*> visited {member};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || dependency->getDocument() != document
                    || !document->containsObject(dependency)
                    || current->isTimelineStructuralChild(dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                if (moved.contains(dependency)) {
                    return true;
                }
                pending.push_back(dependency);
            }
        }
    }
    return false;
}

App::DocumentObject* lastBodyMemberInTimelineBlock(
    const PartDesign::Body& body,
    const App::DocumentObject* object
)
{
    const auto* root = semanticTimelineRoot(object, body.getDocument());
    App::DocumentObject* result = nullptr;
    for (auto* member : body.Group.getValues()) {
        if (semanticTimelineRoot(member, body.getDocument()) == root) {
            result = member;
        }
    }
    return result;
}

std::vector<const App::DocumentObject*> collapsedTimelineRoots(
    const std::vector<App::DocumentObject*>& objects,
    const App::Document* document
)
{
    std::vector<const App::DocumentObject*> result;
    std::unordered_set<const App::DocumentObject*> completed;
    for (const auto* object : objects) {
        const auto* root = semanticTimelineRoot(object, document);
        if (!root) {
            throw Base::RuntimeError(
                "A document-history block became malformed"
            );
        }
        if (!result.empty() && result.back() == root) {
            continue;
        }
        if (!completed.insert(root).second) {
            throw Base::RuntimeError("A document-history block is no longer contiguous");
        }
        result.push_back(root);
    }
    return result;
}

void validateSemanticRootsAfter(
    const std::vector<App::DocumentObject*>& objects,
    const App::Document* document,
    const std::vector<App::DocumentObject*>& movedRoots,
    const App::DocumentObject* target
)
{
    const auto roots = collapsedTimelineRoots(objects, document);
    const auto* targetRoot = semanticTimelineRoot(target, document);
    const auto targetPosition = std::ranges::find(roots, targetRoot);
    if (!targetRoot || targetPosition == roots.end()) {
        throw Base::RuntimeError("The document-history reorder target was lost");
    }

    auto position = std::next(targetPosition);
    for (const auto* movedRoot : movedRoots) {
        if (position == roots.end() || *position != movedRoot) {
            throw Base::RuntimeError("Body order and document-history order no longer agree");
        }
        ++position;
    }
}

void validateSemanticRootsBefore(
    const std::vector<App::DocumentObject*>& objects,
    const App::Document* document,
    const std::vector<App::DocumentObject*>& movedRoots,
    const App::DocumentObject* target
)
{
    const auto roots = collapsedTimelineRoots(objects, document);
    const auto* targetRoot = semanticTimelineRoot(target, document);
    const auto targetPosition = std::ranges::find(roots, targetRoot);
    if (!targetRoot || targetPosition == roots.end()) {
        throw Base::RuntimeError("The document-history reorder target was lost");
    }
    const auto precedingCount = static_cast<std::size_t>(std::distance(roots.begin(), targetPosition));
    if (precedingCount < movedRoots.size()
        || !std::equal(
            movedRoots.begin(),
            movedRoots.end(),
            targetPosition - static_cast<std::ptrdiff_t>(movedRoots.size())
        )) {
        throw Base::RuntimeError("Body order and document-history order no longer agree");
    }
}

void validateSemanticRootsAtBeginning(
    const PartDesign::Body& body,
    const std::vector<App::DocumentObject*>& movedRoots
)
{
    const auto roots = collapsedTimelineRoots(body.Group.getValues(), body.getDocument());
    if (roots.size() < movedRoots.size()
        || !std::equal(movedRoots.begin(), movedRoots.end(), roots.begin())) {
        throw Base::RuntimeError("The body beginning and document-history order no longer agree");
    }
}
}  // namespace

// PartDesign_Body
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignBody)

CmdPartDesignBody::CmdPartDesignBody()
    : Command("PartDesign_Body")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("New Body");
    sToolTipText = QT_TR_NOOP("Creates a new body and activates it");
    sWhatsThis = "PartDesign_Body";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Body";
}

void CmdPartDesignBody::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    App::Document* document = getDocument();
    if (!document || Gui::Control().activeDialog(document)) {
        return;
    }

    App::Part* actPart = nullptr;
    if (auto* guiDocument =
            Gui::Application::Instance->getDocument(document)) {
        if (auto* view = guiDocument->getActiveView()) {
            actPart = view->getActiveObject<App::Part*>(PARTKEY);
            if (!actPart) {
                actPart =
                    view->getActiveObject<App::Part*>(ASSEMBLYKEY);
            }
        }
    }
    App::Part* partOfBaseFeature = nullptr;

    std::vector<App::DocumentObject*> features = getSelection().getObjectsOfType(
        Part::Feature::getClassTypeId(),
        document->getName()
    );
    App::DocumentObject* baseFeature = nullptr;
    bool addtogroup = false;

    bool allowCompound = PartDesign::PartDesignParameter::instance()->getAllowCompoundDefault();

    if (!features.empty()) {
        if (features.size() == 1) {
            baseFeature = features[0];
            if (baseFeature->isDerivedFrom(PartDesign::Feature::getClassTypeId())
                && PartDesign::Body::findBodyOf(baseFeature)) {
                // Prevent creating bodies based on features already belonging to other bodies
                QMessageBox::warning(
                    Gui::getMainWindow(),
                    QObject::tr("Bad base feature"),
                    QObject::tr("A body cannot be based on a Part Design feature.")
                );
                baseFeature = nullptr;
            }
            else if (PartDesign::Body::findBodyOf(baseFeature)) {
                QMessageBox::warning(
                    Gui::getMainWindow(),
                    QObject::tr("Bad base feature"),
                    QObject::tr("%1 already belongs to a body and cannot be used as a base feature for another body.")
                        .arg(QString::fromUtf8(baseFeature->Label.getValue()))
                );
                baseFeature = nullptr;
            }
            else if (baseFeature->isDerivedFrom(Part::BodyBase::getClassTypeId())) {
                // Prevent creating bodies based on bodies (but don't pop-up a dialog)
                baseFeature = nullptr;
            }
            else {
                partOfBaseFeature = App::Part::getPartOfObject(baseFeature);
                if (partOfBaseFeature && partOfBaseFeature != actPart) {
                    // prevent cross-part mess
                    QMessageBox::warning(
                        Gui::getMainWindow(),
                        QObject::tr("Bad base feature"),
                        QObject::tr("Base feature (%1) belongs to other part.")
                            .arg(QString::fromUtf8(baseFeature->Label.getValue()))
                    );
                    baseFeature = nullptr;
                }
                else if (baseFeature->isDerivedFrom<Sketcher::SketchObject>()) {
                    // Add sketcher to the body's group property
                    addtogroup = true;
                }
                // if a standard Part feature (not a PartDesign feature) is selected then check
                // the number of solids/shells
                else if (!baseFeature->isDerivedFrom<PartDesign::Feature>()) {
                    const TopoDS_Shape& shape
                        = static_cast<Part::Feature*>(baseFeature)->Shape.getValue();
                    if (!shape.IsNull()) {
                        int numSolids = 0;
                        int numShells = 0;
                        for (TopExp_Explorer xp(shape, TopAbs_SOLID); xp.More(); xp.Next()) {
                            numSolids++;
                        }
                        for (TopExp_Explorer xp(shape, TopAbs_SHELL, TopAbs_SOLID); xp.More();
                             xp.Next()) {
                            numShells++;
                        }

                        QString warning;
                        if (numSolids > 1 && numShells == 0) {
                            warning = QObject::tr(
                                "The selected shape consists of multiple solids.\n"
                                "This may lead to unexpected results."
                            );
                        }
                        else if (numShells > 1 && numSolids == 0) {
                            warning = QObject::tr(
                                "The selected shape consists of multiple shells.\n"
                                "This may lead to unexpected results."
                            );
                        }
                        else if (numShells == 1 && numSolids == 0) {
                            warning = QObject::tr(
                                "The selected shape consists of only a shell.\n"
                                "This may lead to unexpected results."
                            );
                        }
                        else if (numSolids + numShells > 1) {
                            warning = QObject::tr(
                                "The selected shape consists of multiple solids or shells.\n"
                                "This may lead to unexpected results."
                            );
                        }

                        if (!warning.isEmpty()) {
                            QMessageBox::warning(
                                Gui::getMainWindow(),
                                QObject::tr("Base feature"),
                                warning
                            );
                        }
                    }
                }
            }
        }
        else {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Bad base feature"),
                QObject::tr("Body may be based on no more than one feature.")
            );
            return;
        }
    }

    const int bodyTransactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Add a Body")
    );
    if (bodyTransactionId == App::NullTransaction) {
        return;
    }
    bool openedModal = false;

    std::string bodyName = document->getUniqueObjectName("Body");

    // add the Body feature itself, and make it active
    std::ostringstream bodyFactory;
    bodyFactory << "App.getDocument('" << document->getName()
                << "').addObject('PartDesign::Body','" << bodyName << "')";
    auto* body = freecad_cast<PartDesign::Body*>(
        Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            QByteArray(bodyFactory.str().c_str()),
            PartDesign::Body::getClassTypeId()
        )
    );
    if (!body) {
        abortCommand(bodyTransactionId);
        resetTransactionID();
        return;
    }
    const BodyCreationObjectIdentity createdBodyIdentity =
        bodyCreationIdentityOf(body);
    const std::string exactBodyName = body->getNameInDocument();
    const auto refreshBody = [&]() {
        body = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(createdBodyIdentity)
        );
        return body != nullptr;
    };
    // A Body is the structural owner for later modeling features, not a
    // modeling step of its own. Classify this exact same-transaction object
    // before the command can commit; the recorded command preserves the same
    // result during macro replay.
    FCMD_DOC_CMD(
        document,
        "classifyProvisionalTimelineInternalObject("
            << Gui::Command::getObjectCmd(body) << ")"
    );
    if (!refreshBody()) {
        abortCommand(bodyTransactionId);
        resetTransactionID();
        return;
    }

    // set Label for i18n/L10N
    std::string labelString = QObject::tr("Body").toUtf8().toStdString();
    labelString = Base::Tools::escapeEncodeString(labelString);
    FCMD_OBJ_CMD(body, "Label = '" << labelString << "'");
    if (!refreshBody()) {
        abortCommand(bodyTransactionId);
        resetTransactionID();
        return;
    }
    FCMD_OBJ_CMD(body, "AllowCompound = " << Gui::asString(allowCompound));
    if (!refreshBody()) {
        abortCommand(bodyTransactionId);
        resetTransactionID();
        return;
    }
    if (baseFeature) {
        if (partOfBaseFeature) {
            // withdraw base feature from Part, otherwise visibility madness results
            FCMD_OBJ_CMD(
                partOfBaseFeature,
                "removeObject(" << Gui::Command::getObjectCmd(baseFeature)
                                << ")"
            );
        }
        if (addtogroup) {
            FCMD_OBJ_CMD(
                body,
                "Group = [" << Gui::Command::getObjectCmd(baseFeature)
                            << "]"
            );
        }
        else {
            FCMD_OBJ_CMD(
                body,
                "BaseFeature = " << Gui::Command::getObjectCmd(baseFeature)
            );
        }
        if (!refreshBody()) {
            abortCommand(bodyTransactionId);
            resetTransactionID();
            return;
        }
    }
    addModule(Gui, "PartDesignGui");  // import the Gui module only once a session

    if (actPart) {
        FCMD_OBJ_CMD(
            actPart,
            "addObject(" << Gui::Command::getObjectCmd(body) << ")"
        );
        if (!refreshBody()) {
            abortCommand(bodyTransactionId);
            resetTransactionID();
            return;
        }
    }

    if (auto* guiDocument =
            Gui::Application::Instance->getDocument(document)) {
        guiDocument->setActiveView(
            nullptr,
            Gui::View3DInventor::getClassTypeId()
        );
    }
    doCommand(
        Gui::Command::Gui,
        "Gui.getDocument('%s').ActiveView.setActiveObject("
        "'%s', App.getDocument('%s').getObject('%s'))",
        document->getName(),
        PDBODYKEY,
        document->getName(),
        exactBodyName.c_str()
    );
    if (!refreshBody()) {
        abortCommand(bodyTransactionId);
        resetTransactionID();
        return;
    }

    // Make the "Create sketch" prompt appear in the task panel
    Gui::Selection().clearSelection(document->getName());
    Gui::Selection().addSelection(Gui::SelectionObject(body));

    // check if a proxy object has been created for the base feature inside the body
    if (baseFeature) {
        if (body) {
            std::vector<App::DocumentObject*> links = body->Group.getValues();
            for (auto it : links) {
                if (it->isDerivedFrom<PartDesign::FeatureBase>()) {
                    PartDesign::FeatureBase* base = static_cast<PartDesign::FeatureBase*>(it);
                    if (base && base->BaseFeature.getValue() == baseFeature) {
                        Gui::Application::Instance->hideViewProvider(baseFeature);
                        break;
                    }
                }
            }

            // for sketches open the feature dialog to rebase it to a new plane
            // as requested in issue #0002862
            if (addtogroup) {
                std::vector<App::DocumentObject*> planes;
                std::vector<PartDesignGui::TaskFeaturePick::featureStatus> status;
                unsigned validPlaneCount = 0;
                for (auto plane : body->getOrigin()->planes()) {
                    planes.push_back(plane);
                    status.push_back(PartDesignGui::TaskFeaturePick::basePlane);
                    validPlaneCount++;
                }

                if (validPlaneCount > 1) {
                    const std::string documentName = document->getName();
                    const BodyCreationObjectIdentity bodyIdentity =
                        bodyCreationIdentityOf(body);
                    const BodyCreationObjectIdentity baseFeatureIdentity =
                        bodyCreationIdentityOf(baseFeature);

                    // Determines if user made a valid selection in dialog
                    auto accepter = [](const std::vector<App::DocumentObject*>& features) -> bool {
                        return !features.empty();
                    };

                    // Called by dialog when user hits "OK" and accepter returns true
                    auto worker =
                        [
                            documentName,
                            bodyIdentity,
                            baseFeatureIdentity,
                            bodyTransactionId
                        ](
                            const std::vector<App::DocumentObject*>& features
                        ) {
                        try {
                            auto* currentDocument =
                                resolveBodyCreationDocument(documentName);
                            auto* currentBody =
                                freecad_cast<PartDesign::Body*>(
                                    resolveBodyCreationObject(bodyIdentity)
                                );
                            auto* currentBaseFeature =
                                resolveBodyCreationObject(
                                    baseFeatureIdentity
                                );
                            auto* plane =
                                features.empty()
                                ? nullptr
                                : freecad_cast<App::Plane*>(
                                      features.front()
                                  );
                            if (!currentDocument || !currentBody
                                || !currentBaseFeature || !plane
                                || currentBody->getDocument()
                                    != currentDocument
                                || currentBaseFeature->getDocument()
                                    != currentDocument
                                || plane->getDocument()
                                    != currentDocument) {
                                Gui::Command::abortCommand(
                                    bodyTransactionId
                                );
                                return;
                            }

                            std::string supportString =
                                Gui::Command::getObjectCmd(plane, "(", ", [''])");

                            FCMD_OBJ_CMD(
                                currentBaseFeature,
                                "AttachmentSupport = " << supportString
                            );
                            FCMD_OBJ_CMD(
                                currentBaseFeature,
                                "MapMode = '"
                                    << Attacher::AttachEngine::getModeName(Attacher::mmFlatFace)
                                    << "'"
                            );
                            Gui::Command::updateDocument(
                                currentDocument
                            );

                            // Plane selection is an implementation detail of creating the Body.
                            // Leave the newly created Body selected and active when the operation
                            // succeeds.
                            Gui::Selection().clearSelection(
                                currentDocument->getName()
                            );
                            Gui::Selection().addSelection(
                                Gui::SelectionObject(currentBody)
                            );
                            Gui::Command::commitCommand(
                                bodyTransactionId
                            );
                        }
                        catch (...) {
                            Gui::Command::abortCommand(
                                bodyTransactionId
                            );
                        }
                    };

                    // Called by dialog for "Cancel", or "OK" if accepter returns false
                    auto quitter =
                        [documentName, bodyTransactionId]() {
                        if (resolveBodyCreationDocument(documentName)) {
                            Gui::Command::abortCommand(
                                bodyTransactionId
                            );
                        }
                    };

                    // Show dialog and let user pick plane
                    Gui::TaskView::TaskDialog* dlg =
                        Gui::Control().activeDialog(body->getDocument());
                    if (dlg) {
                        Gui::Command::abortCommand(bodyTransactionId);
                        resetTransactionID();
                        return;
                    }

                    Gui::Selection().clearSelection(
                        body->getDocument()->getName()
                    );
                    auto* picker = new PartDesignGui::TaskDlgFeaturePick(
                        planes,
                        status,
                        accepter,
                        worker,
                        true,
                        quitter,
                        body
                    );
                    Gui::Control().showDialog(picker, body->getDocument());
                    openedModal =
                        Gui::Control().activeDialog(body->getDocument())
                        == picker;
                    if (!openedModal) {
                        // showDialog() retains no ownership if installation fails.
                        // Destroying the picker invokes quitter and rolls back the Body.
                        delete picker;
                        resetTransactionID();
                        return;
                    }
                }
            }
        }
    }

    updateDocument(document);

    if (openedModal) {
        // The picker owns completion of this exact transaction from here.
        resetTransactionID();
    }
    else {
        commitCommand(bodyTransactionId);
        resetTransactionID();
    }
}

bool CmdPartDesignBody::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

//===========================================================================
// PartDesign_Migrate
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignMigrate)

CmdPartDesignMigrate::CmdPartDesignMigrate()
    : Command("PartDesign_Migrate")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Migrate");
    sToolTipText = QT_TR_NOOP("Migrates the document to the modern Part Design workflow");
    sWhatsThis = "PartDesign_Migrate";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Migrate";
}

void CmdPartDesignMigrate::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    App::Document* doc = getDocument();

    std::set<PartDesign::Feature*> migrateFeatures;


    // Retrieve all PartDesign Features objects and filter out features already belonging to some body
    for (const auto& feat : doc->getObjects()) {
        if (feat->isDerivedFrom(PartDesign::Feature::getClassTypeId())
            && !PartDesign::Body::findBodyOf(feat) && PartDesign::Body::isResultFeature(feat)) {
            migrateFeatures.insert(static_cast<PartDesign::Feature*>(feat));
        }
    }

    if (migrateFeatures.empty()) {
        if (!PartDesignGui::isModernWorkflow(doc)) {
            // If there is nothing to migrate and workflow is still old just set it to modern
            PartDesignGui::WorkflowManager::instance()->forceWorkflow(
                doc,
                PartDesignGui::Workflow::Modern
            );
        }
        else {
            // Huh? nothing to migrate?
            QMessageBox::warning(
                nullptr,
                QObject::tr("Nothing to migrate"),
                QObject::tr(
                    "No Part Design features without body found"
                    " Nothing to migrate."
                )
            );
        }
        return;
    }

    // Note: this action is undoable, should it be?
    PartDesignGui::WorkflowManager::instance()->forceWorkflow(doc, PartDesignGui::Workflow::Modern);

    // Put features into chains. Each chain should become a separate body.
    std::list<std::list<PartDesign::Feature*>> featureChains;
    std::list<PartDesign::Feature*> chain;  //< the current chain we are working on

    for (auto featIt = migrateFeatures.begin(); !migrateFeatures.empty();) {
        Part::Feature* base = (*featIt)->getBaseObject(/*silent =*/true);

        chain.push_front(*featIt);

        if (!base || !base->isDerivedFrom(PartDesign::Feature::getClassTypeId())
            || PartDesignGui::isAnyNonPartDesignLinksTo(
                static_cast<PartDesign::Feature*>(base),
                /*respectGroups=*/true
            )) {
            // a feature based on nothing as well as on non-partdesign solid starts a new chain
            auto newChainIt = featureChains.emplace(featureChains.end());
            newChainIt->splice(newChainIt->end(), chain);
        }
        else {
            // we are basing on some partdesign feature which supposed to belong to some body
            PartDesign::Feature* baseFeat = static_cast<PartDesign::Feature*>(base);

            auto baseFeatSetIt = migrateFeatures.find(baseFeat);

            if (baseFeatSetIt != migrateFeatures.end()) {
                // base feature is pending for migration, switch to it and continue over
                migrateFeatures.erase(featIt);
                featIt = baseFeatSetIt;
                continue;
            }
            else {
                // The base feature seems already assigned to some chain. Find which
                std::list<PartDesign::Feature*>::iterator baseFeatIt;
                auto isChain =
                    [baseFeat, &baseFeatIt](std::list<PartDesign::Feature*>& fchain) mutable -> bool {
                    baseFeatIt = std::ranges::find(fchain, baseFeat);
                    return baseFeatIt != fchain.end();
                };

                if (auto chainIt = std::ranges::find_if(featureChains, isChain);
                    chainIt != featureChains.end()) {
                    assert(baseFeatIt != chainIt->end());
                    if (std::next(baseFeatIt) == chainIt->end()) {
                        // just append our chain to already found
                        chainIt->splice(chainIt->end(), chain);
                        // TODO: If we will hit a third part everything will be messed up again.
                        //       Probably it will require a yet another smart-ass find_if.
                        //       (2015-08-10, Fat-Zer)
                    }
                    else {
                        // We have a fork of a partDesign feature here
                        // add a chain for current body
                        auto newChainIt = featureChains.emplace(featureChains.end());
                        newChainIt->splice(newChainIt->end(), chain);
                        // add a chain for forked one
                        newChainIt = featureChains.emplace(featureChains.end());
                        newChainIt->splice(
                            newChainIt->end(),
                            *chainIt,
                            std::next(baseFeatIt),
                            chainIt->end()
                        );
                    }
                }
                else {
                    // The feature is not present in list pending for migration,
                    // This generally shouldn't happen but may be if we run into some broken file
                    // Try to find out the body we should insert into
                    // TODO: Some error/warning is needed here (2015-08-10, Fat-Zer)
                    auto newChainIt = featureChains.emplace(featureChains.end());
                    newChainIt->splice(newChainIt->end(), chain);
                }
            }
        }
        migrateFeatures.erase(featIt);
        featIt = migrateFeatures.begin();
        // TODO: Align visibility (2015-08-17, Fat-Zer)
    } /* for */

    // TODO: make it work without parts (2015-09-04, Fat-Zer)
    // add a part if there is no active yet
    App::Part* actPart = PartDesignGui::assertActivePart();

    if (!actPart) {
        return;
    }

    // do the actual migration
    openCommand(QT_TRANSLATE_NOOP("Command", "Migrate legacy Part Design features to bodies"));

    for (auto chainIt = featureChains.begin(); !featureChains.empty();
         featureChains.erase(chainIt), chainIt = featureChains.begin()) {
#ifndef FC_DEBUG
        if (chainIt->empty()) {  // prevent crash in release in case of errors
            continue;
        }
#else
        assert(!chainIt->empty());
#endif
        Part::Feature* base = chainIt->front()->getBaseObject(/*silent =*/true);

        // Find a suitable chain to work with
        for (; chainIt != featureChains.end(); chainIt++) {
            base = chainIt->front()->getBaseObject(/*silent =*/true);
            if (!base || !base->isDerivedFrom(PartDesign::Feature::getClassTypeId())) {
                break;  // no base is ok
            }
            else {
                // The base feature is a PartDesign, it's a fork, try to reassign it to a body...
                base = PartDesign::Body::findBodyOf(base);
                if (base) {
                    break;
                }
            }
        }

        if (chainIt == featureChains.end()) {
            // Shouldn't happen, may be only in case of some circular dependency?
            // TODO Some error message (2015-08-11, Fat-Zer)
            chainIt = featureChains.begin();
            base = chainIt->front()->getBaseObject(/*silent =*/true);
        }

        // Construct a Pretty Body name based on the Tip
        std::string bodyName = getUniqueObjectName(
            std::string(chainIt->back()->getNameInDocument()).append("Body").c_str()
        );
        bool allowCompound = PartDesign::PartDesignParameter::instance()->getAllowCompoundDefault();

        // Create a body for the chain
        doCommand(Doc, "App.activeDocument().addObject('PartDesign::Body','%s')", bodyName.c_str());
        doCommand(
            Doc,
            "App.ActiveDocument.getObject('%s').AllowCompound = %s",
            bodyName.c_str(),
            Gui::asString(allowCompound)
        );
        doCommand(
            Doc,
            "App.activeDocument().%s.addObject(App.ActiveDocument.%s)",
            actPart->getNameInDocument(),
            bodyName.c_str()
        );
        if (base) {
            doCommand(
                Doc,
                "App.activeDocument().%s.BaseFeature = App.activeDocument().%s",
                bodyName.c_str(),
                base->getNameInDocument()
            );
        }

        // Fill the body with features
        for (auto feature : *chainIt) {
            if (feature->isDerivedFrom(PartDesign::ProfileBased::getClassTypeId())) {
                // add the sketch and also reroute it if needed
                PartDesign::ProfileBased* sketchBased = static_cast<PartDesign::ProfileBased*>(feature);
                Part::Part2DObject* sketch = sketchBased->getVerifiedSketch(/*silent =*/true);
                if (sketch) {
                    doCommand(
                        Doc,
                        "App.activeDocument().%s.addObject(App.activeDocument().%s)",
                        bodyName.c_str(),
                        sketch->getNameInDocument()
                    );

                    if (sketch->isDerivedFrom(Sketcher::SketchObject::getClassTypeId())) {
                        try {
                            PartDesignGui::fixSketchSupport(
                                static_cast<Sketcher::SketchObject*>(sketch)
                            );
                        }
                        catch (Base::Exception&) {
                            QMessageBox::critical(
                                Gui::getMainWindow(),
                                QObject::tr("Sketch plane cannot be migrated"),
                                QObject::tr(
                                    "Edit '%1' and redefine it to use a Base or "
                                    "Datum plane as the sketch plane."
                                )
                                    .arg(QString::fromUtf8(sketch->Label.getValue()))
                            );
                        }
                    }
                    else {
                        // TODO: Message that sketchbased is based not on a sketch (2015-08-11, Fat-Zer)
                    }
                }
            }
            doCommand(
                Doc,
                "App.activeDocument().%s.addObject(App.activeDocument().%s)",
                bodyName.c_str(),
                feature->getNameInDocument()
            );

            PartDesignGui::relinkToBody(feature);
        }
    }

    updateActive();
}

bool CmdPartDesignMigrate::isActive()
{
    return hasActiveDocument();
}

//===========================================================================
// PartDesign_MoveTip
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignMoveTip)

CmdPartDesignMoveTip::CmdPartDesignMoveTip()
    : Command("PartDesign_MoveTip")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Set Tip");
    sToolTipText = QT_TR_NOOP("Moves the tip of the body to the selected feature");
    sWhatsThis = "PartDesign_MoveTip";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_MoveTip";
}

void CmdPartDesignMoveTip::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || Gui::Control().activeDialog(document)) {
        return;
    }
    std::vector<App::DocumentObject*> features = getSelection().getObjectsOfType(
        Part::Feature::getClassTypeId(),
        document->getName(),
        exactBrowserChildResolveMode
    );
    App::DocumentObject* selFeature;
    PartDesign::Body* body = nullptr;

    if (features.size() == 1) {
        selFeature = features.front();
        if (selFeature->isDerivedFrom<PartDesign::Body>()) {
            body = static_cast<PartDesign::Body*>(selFeature);
        }
        else {
            body = PartDesignGui::getBodyFor(selFeature, /* messageIfNot =*/false);
        }
    }
    else {
        selFeature = nullptr;
    }

    if (!selFeature) {
        QMessageBox::warning(
            nullptr,
            QObject::tr("Selection error"),
            QObject::tr("Select exactly one Part Design feature or a body.")
        );
        return;
    }
    else if (!body) {
        QMessageBox::warning(
            nullptr,
            QObject::tr("Selection error"),
            QObject::tr(
                "Could not determine a body for the selected feature '%s'.",
                selFeature->Label.getValue()
            )
        );
        return;
    }
    else if (selFeature != body
             && (!body->hasObject(selFeature) || !PartDesign::Body::isResultFeature(selFeature))) {
        QMessageBox::warning(
            nullptr,
            QObject::tr("Selection error"),
            QObject::tr("Only a result feature can be the tip of a body.")
        );
        return;
    }

    auto* controller = App::DocumentTimeline::get(document);
    if (!controller) {
        return;
    }
    const auto operations = controller->Operations.getValues();
    const auto operation = std::ranges::find(operations, selFeature);
    if (operation == operations.end()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("History error"),
            QObject::tr("The selected feature is not in the document timeline.")
        );
        return;
    }
    const long requestedPosition =
        static_cast<long>(std::distance(operations.begin(), operation) + 1);
    if (controller->Position.getValue() == requestedPosition) {
        Base::Console().message(
            "%s is already the current end of history\n",
            selFeature->getNameInDocument()
        );
        return;
    }
    const std::string documentName = document->getName();
    const std::string documentUid = document->Uid.getValueStr();
    const auto bodyIdentity = bodyCreationIdentityOf(body);
    const auto featureIdentity = bodyCreationIdentityOf(selFeature);

    auto* timeline = Gui::getMainWindow()
        ? Gui::getMainWindow()->findChild<Gui::FeatureTimeline*>(
              QStringLiteral("VibeCADFeatureTimeline")
          )
        : nullptr;
    if (!timeline
        || !timeline->moveCurrentStateAfterOperation(
            QString::fromStdString(documentName),
            QString::fromStdString(documentUid),
            QString::fromStdString(featureIdentity.objectName),
            featureIdentity.objectId
        )) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("History error"),
            QObject::tr(
                "The document history could not be moved to the selected feature."
            )
        );
        return;
    }

    auto* currentDocument =
        resolveBodyCreationDocument(documentName);
    auto* currentController =
        App::DocumentTimeline::get(currentDocument);
    auto* currentBody = freecad_cast<PartDesign::Body*>(
        resolveBodyCreationObject(bodyIdentity)
    );
    auto* currentFeature =
        resolveBodyCreationObject(featureIdentity);
    if (!currentDocument
        || currentDocument->Uid.getValueStr() != documentUid
        || !currentController || !currentBody || !currentFeature
        || currentController->Position.getValue() != requestedPosition
        || currentBody->Tip.getValue()
            != (currentFeature == currentBody ? nullptr : currentFeature)) {
        throw Base::RuntimeError(
            "The document timeline did not reach the selected feature"
        );
    }
}

bool CmdPartDesignMoveTip::isActive()
{
    auto* document = getDocument();
    return document
        && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction()
        && !Gui::Control().activeDialog(document);
}

//===========================================================================
// PartDesign_DuplicateSelection
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignDuplicateSelection)

CmdPartDesignDuplicateSelection::CmdPartDesignDuplicateSelection()
    : Command("PartDesign_DuplicateSelection")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Duplicate &Object");
    sToolTipText = QT_TR_NOOP("Duplicates the selected object and adds it to the active body");
    sWhatsThis = "PartDesign_DuplicateSelection";
    sStatusTip = sToolTipText;
}

void CmdPartDesignDuplicateSelection::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document) {
        return;
    }
    auto* activeBody =
        PartDesignGui::getBody(/*messageIfNot = */ false);
    const std::string documentName = document->getName();
    const std::string documentUid = document->Uid.getValueStr();
    const bool hadActiveBody = activeBody != nullptr;
    const auto activeBodyIdentity =
        bodyCreationIdentityOf(activeBody);

    std::vector<App::DocumentObject*> selectedObjects;
    std::set<App::DocumentObject*> seenObjects;
    for (const auto& selected : getSelection().getCompleteSelection()) {
        auto* object = selected.pObject;
        if (object && object->isAttachedToDocument() && object->getDocument() == document
            && seenObjects.insert(object).second) {
            selectedObjects.push_back(object);
        }
    }
    if (selectedObjects.empty()) {
        return;
    }
    std::vector<BodyCreationObjectIdentity> selectedIdentities;
    selectedIdentities.reserve(selectedObjects.size());
    for (const auto* object : selectedObjects) {
        selectedIdentities.push_back(
            bodyCreationIdentityOf(object)
        );
    }

    const auto resolveLaunchDocument = [&]() {
        auto* current =
            resolveBodyCreationDocument(documentName);
        return current
                && current->Uid.getValueStr() == documentUid
            ? current
            : nullptr;
    };
    const auto resolveExactObjects =
        [&resolveLaunchDocument](
            const std::vector<BodyCreationObjectIdentity>& identities
        ) {
            std::vector<App::DocumentObject*> resolved;
            auto* currentDocument = resolveLaunchDocument();
            if (!currentDocument) {
                return resolved;
            }
            resolved.reserve(identities.size());
            for (const auto& identity : identities) {
                auto* object =
                    resolveBodyCreationObject(identity);
                if (!object
                    || object->getDocument() != currentDocument) {
                    resolved.clear();
                    return resolved;
                }
                resolved.push_back(object);
            }
            return resolved;
        };

    const auto dependencies = App::Document::getDependencyList(selectedObjects);
    std::vector<BodyCreationObjectIdentity> dependencyIdentities;
    dependencyIdentities.reserve(dependencies.size());
    for (const auto* dependency : dependencies) {
        dependencyIdentities.push_back(
            bodyCreationIdentityOf(dependency)
        );
    }
    std::vector<App::DocumentObject*> copyObjects = selectedObjects;
    if (dependencies.size() > selectedObjects.size()) {
        Gui::DlgObjectSelection dialog(selectedObjects, Gui::getMainWindow());
        if (dialog.exec() != QDialog::Accepted) {
            return;
        }
        document = resolveLaunchDocument();
        selectedObjects = resolveExactObjects(selectedIdentities);
        const auto currentDependencies =
            resolveExactObjects(dependencyIdentities);
        if (!document
            || selectedObjects.size() != selectedIdentities.size()
            || currentDependencies.size()
                != dependencyIdentities.size()) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Objects changed"),
                QObject::tr(
                    "The document or one of its dependencies changed while "
                    "choosing what to duplicate."
                )
            );
            return;
        }

        const auto requestedCopies = dialog.getSelections();
        copyObjects = selectedObjects;
        std::set<App::DocumentObject*> copied(
            copyObjects.begin(),
            copyObjects.end()
        );
        for (auto* requested : requestedCopies) {
            if (!requested
                || std::ranges::find(
                       currentDependencies,
                       requested
                   )
                    == currentDependencies.end()) {
                QMessageBox::warning(
                    Gui::getMainWindow(),
                    QObject::tr("Objects changed"),
                    QObject::tr(
                        "A dependency changed while choosing what to duplicate."
                    )
                );
                return;
            }
            if (copied.insert(requested).second) {
                copyObjects.push_back(requested);
            }
        }
    }

    std::vector<BodyCreationObjectIdentity> copyIdentities;
    copyIdentities.reserve(copyObjects.size());
    for (const auto* object : copyObjects) {
        copyIdentities.push_back(
            bodyCreationIdentityOf(object)
        );
    }

    const auto exportPlan =
        Gui::prepareTimelineExport(copyObjects, false);
    std::vector<App::Document*> unsavedDocuments;
    const bool hasExternalLinks = App::PropertyXLink::hasXLink(exportPlan.objects, &unsavedDocuments);
    if (!unsavedDocuments.empty()) {
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Unsaved Document"),
            QObject::tr("The duplicated object contains an external link. Save its "
                        "source document before duplicating it.")
        );
        return;
    }
    if (hasExternalLinks && !document->isSaved()) {
        const auto answer = QMessageBox::question(
            Gui::getMainWindow(),
            QObject::tr("Object Dependencies"),
            QObject::tr("The duplicated object contains an external link. Save the "
                        "active document now?"),
            QMessageBox::Yes,
            QMessageBox::No
        );
        if (answer != QMessageBox::Yes) {
            return;
        }
        auto* guiDocument =
            Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(document)
            : nullptr;
        if (!guiDocument || !guiDocument->saveAs()) {
            return;
        }
    }

    document = resolveLaunchDocument();
    selectedObjects = resolveExactObjects(selectedIdentities);
    copyObjects = resolveExactObjects(copyIdentities);
    activeBody = hadActiveBody
        ? freecad_cast<PartDesign::Body*>(
              resolveBodyCreationObject(activeBodyIdentity)
          )
        : nullptr;
    if (!document
        || selectedObjects.size() != selectedIdentities.size()
        || copyObjects.size() != copyIdentities.size()
        || (hadActiveBody
            && (!activeBody
                || activeBody->getDocument() != document))) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Objects changed"),
            QObject::tr(
                "The document, selection, or active body changed before "
                "duplication could begin."
            )
        );
        return;
    }

    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP(
            "Command",
            "Duplicate a Part Design object"
        )
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        auto imported =
            Gui::copyTimelineObjects(*document, copyObjects, false);
        if (imported.selectedObjects.size() < selectedObjects.size()) {
            throw Base::RuntimeError(
                "The duplicate import did not map every direct selection"
            );
        }

        std::vector<App::DocumentObject*> modelingOutputs;
        std::set<App::DocumentObject*> seenRoots;
        for (std::size_t index = 0;
             index < selectedObjects.size();
             ++index) {
            auto* feature = imported.selectedObjects[index];
            std::set<App::DocumentObject*> ownerChain;
            while (App::DocumentTimeline::hasTimelineResourceRole(feature)) {
                if (!ownerChain.insert(feature).second) {
                    throw Base::RuntimeError(
                        "A duplicated semantic ownership chain is cyclic"
                    );
                }
                feature = const_cast<App::DocumentObject*>(
                    App::DocumentTimeline::timelineOwner(feature)
                );
            }
            if (!feature || !seenRoots.insert(feature).second) {
                continue;
            }
            if (PartDesign::Body::isAllowed(feature)) {
                modelingOutputs.push_back(feature);
            }
        }

        std::unordered_map<App::DocumentObject*, std::size_t>
            sourceOrder;
        sourceOrder.reserve(imported.sourceOrder.size());
        for (std::size_t index = 0;
             index < imported.sourceOrder.size();
             ++index) {
            sourceOrder.emplace(imported.sourceOrder[index], index);
        }
        std::ranges::stable_sort(
            modelingOutputs,
            [&sourceOrder](
                const App::DocumentObject* left,
                const App::DocumentObject* right
            ) {
                const auto leftOrder = sourceOrder.find(
                    const_cast<App::DocumentObject*>(left)
                );
                const auto rightOrder = sourceOrder.find(
                    const_cast<App::DocumentObject*>(right)
                );
                if (leftOrder != sourceOrder.end()
                    && rightOrder != sourceOrder.end()) {
                    return leftOrder->second < rightOrder->second;
                }
                if (leftOrder != sourceOrder.end()) {
                    return true;
                }
                if (rightOrder != sourceOrder.end()) {
                    return false;
                }
                return left->getID() < right->getID();
            }
        );

        if (activeBody) {
            for (auto* feature : modelingOutputs) {
                auto* owner =
                    App::GeoFeatureGroupExtension::getGroupOfObject(
                        feature
                    );
                if (owner == activeBody) {
                    continue;
                }
                if (owner) {
                    throw Base::RuntimeError(
                        "A duplicated result already belongs to another "
                        "modeling container"
                    );
                }
                if (feature->isDerivedFrom<PartDesign::Feature>()) {
                    FCMD_OBJ_CMD(
                        activeBody,
                        "addObject(" << getObjectCmd(feature) << ")"
                    );
                }
                else if (
                    !PartDesignGui::ModelingContext::instance()
                         .adoptPartResult(feature, activeBody)) {
                    throw Base::RuntimeError(
                        std::string("Could not place duplicated result '")
                        + feature->getNameInDocument()
                        + "' in the active Body"
                    );
                }
            }
        }

        Gui::adoptTimelineImport(imported);

        document->recompute();
        for (auto* feature : modelingOutputs) {
            if (!document->containsObject(feature)
                || feature->getDocument() != document
                || !feature->isValid()) {
                throw Base::RuntimeError(
                    "A duplicated result became invalid before commit"
                );
            }
        }
        if (activeBody) {
            if (!document->containsObject(activeBody)
                || activeBody->getDocument() != document) {
                throw Base::RuntimeError(
                    "The active Body changed during duplication"
                );
            }
            FCMD_OBJ_SHOW(activeBody);
        }
        else {
            for (auto* feature : modelingOutputs) {
                FCMD_OBJ_SHOW(feature);
            }
        }

        Gui::Selection().clearSelection(document->getName());
        for (auto* feature : modelingOutputs) {
            Gui::Selection().addSelection(
                Gui::SelectionObject(feature)
            );
        }
        updateDocument(document);

        commitCommand(transactionId);
        resetTransactionID();
    }
    catch (Base::Exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        error.reportException();
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignDuplicateSelection::isActive()
{
    if (!hasActiveDocument() || !PartGui::canStartRetainedModelingTask(getDocument())
        || Gui::Control().activeDialog()) {
        return false;
    }
    const auto selection = Gui::Selection().getSelectionEx(
        getDocument()->getName(),
        App::DocumentObject::getClassTypeId(),
        exactBrowserChildResolveMode
    );
    return !selection.empty()
        && std::ranges::all_of(
            selection,
            [](const Gui::SelectionObject& selected) {
                return selected.getObject()
                    && PartDesign::Body::isAllowed(
                        selected.getObject()
                    );
            }
        );
}

//===========================================================================
// PartDesign_MoveFeature
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignMoveFeature)

CmdPartDesignMoveFeature::CmdPartDesignMoveFeature()
    : Command("PartDesign_MoveFeature")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Move Object To…");
    sToolTipText = QT_TR_NOOP("Moves the selected object to another body");
    sWhatsThis = "PartDesign_MoveFeature";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_MoveFeature";
}

void CmdPartDesignMoveFeature::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || Gui::Control().activeDialog(document)) {
        return;
    }
    auto selection = Gui::Selection().getSelectionEx(
        document->getName(),
        App::DocumentObject::getClassTypeId(),
        exactBrowserChildResolveMode
    );
    if (selection.empty()) {
        return;
    }

    if (!std::ranges::all_of(
            selection,
            [](const Gui::SelectionObject& selected) {
                return isMoveFeatureCandidate(selected.getObject());
            }
        )) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Objects cannot be moved"),
            QObject::tr("Bodies and other containers cannot be moved into a body")
        );
        return;
    }

    std::vector<App::DocumentObject*> features;
    features.reserve(selection.size());
    std::ranges::transform(
        selection,
        std::back_inserter(features),
        [](Gui::SelectionObject& selected) {
            return selected.getObject();
        }
    );

    // Check if all features are valid to move
    if (std::any_of(std::begin(features), std::end(features), [](App::DocumentObject* obj) {
            return !PartDesignGui::isFeatureMovable(obj);
        })) {
        // show messagebox and cancel
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("Some of the selected features have dependencies in the source body")
        );
        return;
    }

    // Collect dependencies of the selected features
    std::vector<App::DocumentObject*> dependencies = PartDesignGui::collectMovableDependencies(
        features
    );
    if (!dependencies.empty()) {
        features.insert(std::end(features), std::begin(dependencies), std::end(dependencies));
    }
    std::set<long> featureIds;
    std::erase_if(
        features,
        [document, &featureIds](const App::DocumentObject* feature) {
            return !feature || feature->getDocument() != document
                || !featureIds.insert(feature->getID()).second;
        }
    );
    if (features.empty()) {
        return;
    }

    // Create a list of all bodies in this part
    std::vector<App::DocumentObject*> bodies = document->getObjectsOfType(
        Part::BodyBase::getClassTypeId()
    );

    std::set<App::DocumentObject*> source_bodies;
    for (auto feat : features) {
        // Note: 'source' can be null which means that the feature doesn't belong to a body.
        PartDesign::Body* source = PartDesign::Body::findBodyOf(feat);
        source_bodies.insert(static_cast<App::DocumentObject*>(source));
    }

    if (source_bodies.size() != 1) {
        // show messagebox and cancel
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("Only features of a single source body can be moved")
        );
        return;
    }

    auto* source_body = freecad_cast<PartDesign::Body*>(
        *source_bodies.begin()
    );
    if (*source_bodies.begin() && !source_body) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("The selected objects do not belong to a Part Design body.")
        );
        return;
    }

    auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("The document does not have a valid modeling history.")
        );
        return;
    }

    std::vector<App::DocumentObject*> timelineRoots;
    try {
        timelineRoots =
            orderedSemanticTimelineRoots(*timeline, features);
        if (source_body) {
            if (std::ranges::any_of(
                    timelineRoots,
                    [source_body](const App::DocumentObject* root) {
                        return !source_body->hasObject(root);
                    }
                )) {
                throw Base::RuntimeError(
                    "A selected internal resource cannot be moved independently of its operation"
                );
            }

            auto completeMembers =
                bodyMembersForTimelineRoots(*source_body, timelineRoots);
            if (completeMembers.empty()
                || std::ranges::any_of(
                    features,
                    [&completeMembers](const App::DocumentObject* feature) {
                        return std::ranges::find(
                                   completeMembers,
                                   feature
                               )
                            == completeMembers.end();
                    }
                )) {
                throw Base::RuntimeError(
                    "The selected operation block is incomplete in its source body"
                );
            }
            features = std::move(completeMembers);
        }
        else {
            if (timelineRoots.size() != features.size()
                || std::ranges::any_of(
                    features,
                    [document](const App::DocumentObject* feature) {
                        return semanticTimelineRoot(feature, document)
                            != feature;
                    }
                )) {
                throw Base::RuntimeError(
                    "A standalone internal resource cannot be moved independently of its operation"
                );
            }
            features = timelineRoots;
        }
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QString::fromUtf8(error.what())
        );
        return;
    }

    if (source_body && bodyRemainderDependsOnMovedMembers(*source_body, features)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("A feature remaining in the source body depends on the "
                        "selected operation. Move an independent operation or "
                        "remove that dependency first.")
        );
        return;
    }

    std::vector<App::DocumentObject*> target_bodies;
    for (auto body : bodies) {
        if (body->isDerivedFrom<PartDesign::Body>() && !source_bodies.count(body)) {
            target_bodies.push_back(body);
        }
    }

    if (target_bodies.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("There are no other bodies to move to")
        );
        return;
    }

    std::vector<BodyCreationObjectIdentity> targetBodyIdentities;
    targetBodyIdentities.reserve(target_bodies.size());
    std::ranges::transform(
        target_bodies,
        std::back_inserter(targetBodyIdentities),
        bodyCreationIdentityOf
    );
    std::vector<BodyCreationObjectIdentity> featureIdentities;
    featureIdentities.reserve(features.size());
    std::ranges::transform(
        features,
        std::back_inserter(featureIdentities),
        bodyCreationIdentityOf
    );
    const auto sourceIdentity =
        bodyCreationIdentityOf(source_body);
    std::vector<BodyCreationObjectIdentity> timelineRootIdentities;
    timelineRootIdentities.reserve(timelineRoots.size());
    std::ranges::transform(
        timelineRoots,
        std::back_inserter(timelineRootIdentities),
        bodyCreationIdentityOf
    );

    // Ask user to select the target body (remove source bodies from list)
    bool ok;
    const QStringList items =
        disambiguatedObjectLabels(target_bodies);
    QString text = QInputDialog::getItem(
        Gui::getMainWindow(),
        qApp->translate("PartDesign_MoveFeature", "Select Body"),
        qApp->translate("PartDesign_MoveFeature", "Select a body from the list"),
        items,
        0,
        false,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok) {
        return;
    }
    int index = items.indexOf(text);
    if (index < 0
        || static_cast<std::size_t>(index)
            >= targetBodyIdentities.size()) {
        return;
    }

    auto* target = freecad_cast<PartDesign::Body*>(
        resolveBodyCreationObject(
            targetBodyIdentities[static_cast<std::size_t>(index)]
        )
    );
    auto* currentSource = source_body
        ? freecad_cast<PartDesign::Body*>(
              resolveBodyCreationObject(sourceIdentity)
          )
        : nullptr;
    std::vector<App::DocumentObject*> currentFeatures;
    currentFeatures.reserve(featureIdentities.size());
    for (const auto& identity : featureIdentities) {
        currentFeatures.push_back(
            resolveBodyCreationObject(identity)
        );
    }
    std::vector<App::DocumentObject*> currentTimelineRoots;
    currentTimelineRoots.reserve(timelineRootIdentities.size());
    for (const auto& identity : timelineRootIdentities) {
        currentTimelineRoots.push_back(
            resolveBodyCreationObject(identity)
        );
    }
    auto* currentDocument =
        target ? target->getDocument() : nullptr;
    auto* currentTimeline =
        App::DocumentTimeline::get(currentDocument);
    bool timelineOrderMatches = false;
    if (currentTimeline
        && std::ranges::none_of(
            currentFeatures,
            [currentDocument](
                const App::DocumentObject* feature
            ) {
                return !feature
                    || feature->getDocument()
                        != currentDocument;
            }
        )) {
        try {
            timelineOrderMatches =
                orderedSemanticTimelineRoots(
                    *currentTimeline,
                    currentFeatures
                )
                == currentTimelineRoots;
        }
        catch (const Base::Exception&) {
        }
    }
    if (!target || !currentDocument || !currentTimeline
        || (source_body && !currentSource)
        || (currentSource
            && currentSource->getDocument() != currentDocument)
        || currentSource == target
        || std::ranges::any_of(
            currentFeatures,
            [currentDocument](
                const App::DocumentObject* feature
            ) {
                return !feature
                    || feature->getDocument()
                        != currentDocument;
            }
        )
        || std::ranges::any_of(
            currentTimelineRoots,
            [currentDocument](
                const App::DocumentObject* root
            ) {
                return !root
                    || root->getDocument()
                        != currentDocument;
            }
        )
        || !timelineOrderMatches) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr(
                "The document or selected objects changed while choosing a target body."
            )
        );
        return;
    }
    document = currentDocument;
    source_body = currentSource;
    features = std::move(currentFeatures);
    timelineRoots = std::move(currentTimelineRoots);

    App::DocumentObject* timelineAnchor = target;
    if (!target->Group.getValues().empty()) {
        timelineAnchor = target->Group.getValues().back();
    }
    timelineAnchor =
        lastBodyMemberInTimelineBlock(*target, timelineAnchor);
    if (!timelineAnchor) {
        timelineAnchor = target;
    }

    const auto targetIdentity =
        bodyCreationIdentityOf(target);
    const auto anchorIdentity =
        bodyCreationIdentityOf(timelineAnchor);
    std::stringstream featureList;
    featureList << "features_ = [" << getObjectCmd(features.front());
    for (std::size_t featureIndex = 1;
         featureIndex < features.size();
         ++featureIndex) {
        featureList << ", " << getObjectCmd(features[featureIndex]);
    }
    featureList << "]";
    std::stringstream timelineReorder;
    timelineReorder << "reorderTimelineOperationBlocksAfter(["
                    << getObjectCmd(timelineRoots.front());
    for (std::size_t rootIndex = 1;
         rootIndex < timelineRoots.size();
         ++rootIndex) {
        timelineReorder << ", " << getObjectCmd(timelineRoots[rootIndex]);
    }
    timelineReorder << "], " << getObjectCmd(timelineAnchor) << ")";
    const std::string sourceCommand =
        source_body ? getObjectCmd(source_body) : std::string();
    const std::string targetCommand = getObjectCmd(target);

    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Move an object")
    );
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        runCommand(Doc, featureList.str().c_str());
        if (source_body) {
            runCommand(
                Doc,
                (sourceCommand + ".removeObjects(features_)").c_str()
            );
        }
        runCommand(
            Doc,
            (targetCommand + ".addObjects(features_)").c_str()
        );
    /*

        // Find body of this feature
        Part::BodyBase* source = PartDesign::Body::findBodyOf(feat);
        bool featureWasTip = false;

        if (source == target) continue;

        // Remove from the source body if the feature belonged to a body
        if (source) {
            featureWasTip = (source->Tip.getValue() == feat);
            doCommand(Doc,"App.activeDocument().%s.removeObject(App.activeDocument().%s)",
                      source->getNameInDocument(), (feat)->getNameInDocument());
        }

        App::DocumentObject* targetOldTip = target->Tip.getValue();

        // Add to target body (always at the Tip)
        doCommand(Doc,"App.activeDocument().%s.addObject(App.activeDocument().%s)",
                      target->getNameInDocument(), (feat)->getNameInDocument());
        // Recompute to update the shape
        doCommand(Gui,"App.activeDocument().recompute()");

        // Adjust visibility of features
        // TODO: May be something can be done in view provider (2015-08-05, Fat-Zer)
        // If we removed the tip of the source body, make the new tip visible
        if ( featureWasTip ) {
            App::DocumentObject * sourceNewTip = source->Tip.getValue();
            if (sourceNewTip)
                doCommand(Gui,"Gui.activeDocument().show(\"%s\")",
    sourceNewTip->getNameInDocument());
        }

        // Hide old tip and show new tip (the moved feature) of the target body
        App::DocumentObject* targetNewTip = target->Tip.getValue();
        if ( targetOldTip != targetNewTip ) {
            if ( targetOldTip ) {
                doCommand(Gui,"Gui.activeDocument().hide(\"%s\")",
    targetOldTip->getNameInDocument());
            }
            if (targetNewTip) {
                doCommand(Gui,"Gui.activeDocument().show(\"%s\")",
    targetNewTip->getNameInDocument());
            }
        }

        // Fix sketch support
        if (feat->isDerivedFrom<Sketcher::SketchObject>()) {
            Sketcher::SketchObject *sketch = static_cast<Sketcher::SketchObject*>(feat);
            try {
                PartDesignGui::fixSketchSupport(sketch);
            } catch (Base::Exception &) {
                QMessageBox::warning( Gui::getMainWindow(), QObject::tr("Sketch plane cannot be
    migrated"), QObject::tr("Please edit '%1' and redefine it to use a Base or Datum plane as the
    sketch plane."). arg( QString::fromLatin1( sketch->Label.getValue () ) ) );
            }
        }

        //relink origin for sketches and datums (coordinates)
        PartDesignGui::relinkToOrigin(feat, target);
    }*/

        auto* currentDocument =
            resolveBodyCreationDocument(targetIdentity.documentName);
        auto* currentTarget = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(targetIdentity)
        );
        auto* currentSource = source_body
            ? freecad_cast<PartDesign::Body*>(
                  resolveBodyCreationObject(sourceIdentity)
              )
            : nullptr;
        if (!currentDocument || currentDocument != document
            || !currentTarget || (source_body && !currentSource)) {
            throw Base::RuntimeError(
                "A source or target body changed while moving objects"
            );
        }
        for (const auto& identity : featureIdentities) {
            currentTarget = freecad_cast<PartDesign::Body*>(
                resolveBodyCreationObject(targetIdentity)
            );
            currentSource = source_body
                ? freecad_cast<PartDesign::Body*>(
                      resolveBodyCreationObject(sourceIdentity)
                  )
                : nullptr;
            auto* currentFeature =
                resolveBodyCreationObject(identity);
            if (!currentTarget || (source_body && !currentSource)
                || !currentFeature
                || currentFeature->getDocument() != currentDocument
                || !currentTarget->hasObject(currentFeature)
                || (currentSource
                    && currentSource->hasObject(currentFeature))) {
                throw Base::RuntimeError(
                    "Not every selected object reached the target body"
                );
            }

            // Moving a feature between bodies also moves its coordinate
            // system. Relink sketches, datums, and profile axes to the
            // corresponding datum in the target Body before validating the
            // new semantic-history order. The legacy move path performed this
            // migration, but the exact-identity implementation had
            // accidentally left it inside the retired code block.
            if (currentFeature->isDerivedFrom<Sketcher::SketchObject>()) {
                PartDesignGui::fixSketchSupport(
                    static_cast<Sketcher::SketchObject*>(
                        currentFeature
                    )
                );
                currentTarget = freecad_cast<PartDesign::Body*>(
                    resolveBodyCreationObject(targetIdentity)
                );
                currentSource = source_body
                    ? freecad_cast<PartDesign::Body*>(
                          resolveBodyCreationObject(sourceIdentity)
                      )
                    : nullptr;
                currentFeature =
                    resolveBodyCreationObject(identity);
                if (!currentTarget || !currentFeature
                    || currentFeature->getDocument()
                        != currentDocument
                    || !currentTarget->hasObject(currentFeature)
                    || (currentSource
                        && currentSource->hasObject(
                            currentFeature
                        ))) {
                    throw Base::RuntimeError(
                        "A moved sketch changed while migrating its support"
                    );
                }
            }
            PartDesignGui::relinkToOrigin(
                currentFeature,
                currentTarget
            );
        }

        auto* currentAnchor =
            resolveBodyCreationObject(anchorIdentity);
        std::vector<App::DocumentObject*> currentRoots;
        currentRoots.reserve(timelineRootIdentities.size());
        for (const auto& identity : timelineRootIdentities) {
            auto* currentRoot =
                resolveBodyCreationObject(identity);
            if (!currentRoot
                || currentRoot->getDocument() != currentDocument) {
                throw Base::RuntimeError(
                    "A semantic operation changed while moving objects"
                );
            }
            currentRoots.push_back(currentRoot);
        }
        if (!currentAnchor
            || currentAnchor->getDocument() != currentDocument) {
            throw Base::RuntimeError(
                "The target history boundary changed while moving objects"
            );
        }

        FCMD_DOC_CMD(currentDocument, timelineReorder.str());
        updateDocument(currentDocument);
        auto* currentTimeline =
            App::DocumentTimeline::get(currentDocument);
        currentTarget = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(targetIdentity)
        );
        currentSource = source_body
            ? freecad_cast<PartDesign::Body*>(
                  resolveBodyCreationObject(sourceIdentity)
              )
            : nullptr;
        currentAnchor = resolveBodyCreationObject(anchorIdentity);
        currentRoots.clear();
        for (const auto& identity : timelineRootIdentities) {
            currentRoots.push_back(
                resolveBodyCreationObject(identity)
            );
        }
        std::vector<App::DocumentObject*> currentFeatures;
        currentFeatures.reserve(featureIdentities.size());
        for (const auto& identity : featureIdentities) {
            currentFeatures.push_back(
                resolveBodyCreationObject(identity)
            );
        }
        if (!currentTimeline || !currentTarget
            || (source_body && !currentSource) || !currentAnchor
            || std::ranges::any_of(
                currentRoots,
                [](const App::DocumentObject* root) {
                    return !root;
                }
            )
            || std::ranges::any_of(
                currentFeatures,
                [currentDocument, currentTarget, currentSource](
                    const App::DocumentObject* feature
                ) {
                    return !feature
                        || feature->getDocument() != currentDocument
                        || !currentTarget->hasObject(feature)
                        || (currentSource
                            && currentSource->hasObject(feature))
                        || !feature->isValid();
                }
            )
            || !currentTarget->isValid()
            || (currentSource && !currentSource->isValid())) {
            throw Base::RuntimeError(
                "Moving the selected objects produced an invalid body"
            );
        }
        validateSemanticRootsAfter(
            currentTimeline->Operations.getValues(),
            currentDocument,
            currentRoots,
            currentAnchor
        );
        if (currentAnchor == currentTarget) {
            validateSemanticRootsAtBeginning(
                *currentTarget,
                currentRoots
            );
        }
        else {
            validateSemanticRootsAfter(
                currentTarget->Group.getValues(),
                currentDocument,
                currentRoots,
                currentAnchor
            );
        }
        commitCommand(transactionId);
        resetTransactionID();
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignMoveFeature::isActive()
{
    auto* document = getDocument();
    if (!document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || Gui::Control().activeDialog(document)) {
        return false;
    }

    const auto selection = Gui::Selection().getSelectionEx(
        document->getName(),
        App::DocumentObject::getClassTypeId(),
        exactBrowserChildResolveMode
    );
    return !selection.empty()
        && std::ranges::all_of(
            selection,
            [](const Gui::SelectionObject& selected) {
                return isMoveFeatureCandidate(selected.getObject());
            }
        );
}

DEF_STD_CMD_A(CmdPartDesignMoveFeatureInTree)

CmdPartDesignMoveFeatureInTree::CmdPartDesignMoveFeatureInTree()
    : Command("PartDesign_MoveFeatureInTree")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Move Feature After…");
    sToolTipText = QT_TR_NOOP("Moves the selected feature after another feature in the same body");
    sWhatsThis = "PartDesign_MoveFeatureInTree";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_MoveFeatureInTree";
}

void CmdPartDesignMoveFeatureInTree::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* document = getDocument();
    if (!document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()
        || Gui::Control().activeDialog(document)) {
        return;
    }
    std::vector<App::DocumentObject*> features = getSelection().getObjectsOfType(
        Part::Feature::getClassTypeId(),
        document->getName(),
        exactBrowserChildResolveMode
    );

    // also check and include datum objects, ie. plane, line, and point
    std::vector<App::DocumentObject*> datums = getSelection().getObjectsOfType(
        App::DatumElement::getClassTypeId(),
        document->getName(),
        exactBrowserChildResolveMode
    );
    features.insert(features.end(), datums.begin(), datums.end());

    std::vector<App::DocumentObject*> lcs = getSelection().getObjectsOfType(
        App::LocalCoordinateSystem::getClassTypeId(),
        document->getName(),
        exactBrowserChildResolveMode
    );
    features.insert(features.end(), lcs.begin(), lcs.end());

    std::set<long> featureIds;
    std::erase_if(
        features,
        [document, &featureIds](const App::DocumentObject* feature) {
            return !feature || feature->getDocument() != document
                || !featureIds.insert(feature->getID()).second;
        }
    );
    if (features.empty()) {
        return;
    }

    PartDesign::Body* body = PartDesignGui::getBodyFor(features.front(), false);
    App::DocumentObject* bodyBase = nullptr;
    // sanity check
    bool allFeaturesFromSameBody = true;

    if (body) {
        bodyBase = body->BaseFeature.getValue();
        for (auto feat : features) {
            if (!body->hasObject(feat)) {
                allFeaturesFromSameBody = false;
                break;
            }
            if (bodyBase == feat) {
                QMessageBox::warning(
                    nullptr,
                    QObject::tr("Selection error"),
                    QObject::tr("Impossible to move the base feature of a body.")
                );
                return;
            }
        }
    }
    if (!body || !allFeaturesFromSameBody) {
        QMessageBox::warning(
            nullptr,
            QObject::tr("Selection error"),
            QObject::tr("Select one or more features from the same body.")
        );
        return;
    }

    auto* timeline = App::DocumentTimeline::get(document);
    std::vector<App::DocumentObject*> timelineRoots;
    if (!timeline) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr("The document does not have a valid modeling history.")
        );
        return;
    }
    try {
        timelineRoots =
            orderedSemanticTimelineRoots(*timeline, features);
        if (std::ranges::any_of(
                timelineRoots,
                [body](const App::DocumentObject* root) {
                    return !body->hasObject(root);
                }
            )) {
            throw Base::RuntimeError(
                "An internal resource cannot be reordered independently of its operation"
            );
        }
        auto completeMembers =
            bodyMembersForTimelineRoots(*body, timelineRoots);
        if (completeMembers.empty()
            || std::ranges::any_of(
                features,
                [&completeMembers](const App::DocumentObject* feature) {
                    return std::ranges::find(
                               completeMembers,
                               feature
                           )
                        == completeMembers.end();
                }
            )) {
            throw Base::RuntimeError(
                "The selected operation block is incomplete in its body"
            );
        }
        features = std::move(completeMembers);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QString::fromUtf8(error.what())
        );
        return;
    }

    const auto bodyIdentity = bodyCreationIdentityOf(body);
    const auto bodyBaseIdentity =
        bodyCreationIdentityOf(bodyBase);
    std::vector<BodyCreationObjectIdentity> featureIdentities;
    featureIdentities.reserve(features.size());
    std::ranges::transform(
        features,
        std::back_inserter(featureIdentities),
        bodyCreationIdentityOf
    );
    std::vector<BodyCreationObjectIdentity> timelineRootIdentities;
    timelineRootIdentities.reserve(timelineRoots.size());
    std::ranges::transform(
        timelineRoots,
        std::back_inserter(timelineRootIdentities),
        bodyCreationIdentityOf
    );

    // Offer one exact target for each semantic operation block. Internal
    // resources are never independent positions in the user-visible history.
    std::vector<App::DocumentObject*> targetCandidates;
    targetCandidates.push_back(bodyBase);
    std::unordered_set<const App::DocumentObject*> seenTargets;
    if (bodyBase) {
        seenTargets.insert(bodyBase);
    }
    for (auto* member : body->Group.getValues()) {
        auto* root = const_cast<App::DocumentObject*>(
            semanticTimelineRoot(member, document)
        );
        if (!root || seenTargets.contains(root)
            || std::ranges::find(timelineRoots, root)
                != timelineRoots.end()) {
            continue;
        }
        seenTargets.insert(root);
        targetCandidates.push_back(root);
    }
    std::vector<BodyCreationObjectIdentity> targetIdentities;
    targetIdentities.reserve(targetCandidates.size());
    std::ranges::transform(
        targetCandidates,
        std::back_inserter(targetIdentities),
        bodyCreationIdentityOf
    );

    // Ask user to select the exact target feature.
    bool ok;
    const QStringList items =
        disambiguatedObjectLabels(targetCandidates);

    QString text = QInputDialog::getItem(
        Gui::getMainWindow(),
        qApp->translate("PartDesign_MoveFeatureInTree", "Move Feature After…"),
        qApp->translate("PartDesign_MoveFeatureInTree", "Select a feature from the list"),
        items,
        0,
        false,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok) {
        return;
    }
    int index = items.indexOf(text);
    if (index < 0
        || static_cast<std::size_t>(index)
            >= targetIdentities.size()) {
        return;
    }

    auto* currentBody = freecad_cast<PartDesign::Body*>(
        resolveBodyCreationObject(bodyIdentity)
    );
    auto* currentBodyBase =
        resolveBodyCreationObject(bodyBaseIdentity);
    auto* target =
        resolveBodyCreationObject(
            targetIdentities[
                static_cast<std::size_t>(index)
            ]
        );
    std::vector<App::DocumentObject*> currentFeatures;
    currentFeatures.reserve(featureIdentities.size());
    for (const auto& identity : featureIdentities) {
        currentFeatures.push_back(
            resolveBodyCreationObject(identity)
        );
    }
    std::vector<App::DocumentObject*> currentTimelineRoots;
    currentTimelineRoots.reserve(timelineRootIdentities.size());
    for (const auto& identity : timelineRootIdentities) {
        currentTimelineRoots.push_back(
            resolveBodyCreationObject(identity)
        );
    }
    auto* currentDocument =
        currentBody ? currentBody->getDocument() : nullptr;
    auto* currentTimeline =
        App::DocumentTimeline::get(currentDocument);
    bool timelineOrderMatches = false;
    if (currentTimeline
        && std::ranges::none_of(
            currentFeatures,
            [currentDocument](
                const App::DocumentObject* feature
            ) {
                return !feature
                    || feature->getDocument()
                        != currentDocument;
            }
        )) {
        try {
            timelineOrderMatches =
                orderedSemanticTimelineRoots(
                    *currentTimeline,
                    currentFeatures
                )
                == currentTimelineRoots;
        }
        catch (const Base::Exception&) {
        }
    }
    if (!currentBody || !currentDocument || !currentTimeline
        || (bodyBase && !currentBodyBase)
        || currentBody->BaseFeature.getValue()
            != currentBodyBase
        || (targetIdentities[static_cast<std::size_t>(index)]
                .objectId
                >= 0
            && !target)
        || (target && target != currentBodyBase
            && !currentBody->hasObject(target))
        || std::ranges::any_of(
            currentFeatures,
            [currentBody](
                const App::DocumentObject* feature
            ) {
                return !feature
                    || !currentBody->hasObject(feature);
            }
        )
        || std::ranges::any_of(
            currentTimelineRoots,
            [currentDocument](
                const App::DocumentObject* root
            ) {
                return !root
                    || root->getDocument()
                        != currentDocument;
            }
        )
        || !timelineOrderMatches) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Features cannot be moved"),
            QObject::tr(
                "The body or selected features changed while choosing a history position."
            )
        );
        return;
    }

    document = currentDocument;
    body = currentBody;
    bodyBase = currentBodyBase;
    features = std::move(currentFeatures);
    timelineRoots = std::move(currentTimelineRoots);
    if (target) {
        const auto* targetRoot =
            semanticTimelineRoot(target, document);
        if (std::ranges::find(timelineRoots, targetRoot)
            != timelineRoots.end()) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Features cannot be moved"),
                QObject::tr("Select a target outside the features being moved.")
            );
            return;
        }
        target = lastBodyMemberInTimelineBlock(*body, target);
        if (!target) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Features cannot be moved"),
                QObject::tr("The selected target has an incomplete history block.")
            );
            return;
        }
    }
    const bool insertAtBeginning = target == nullptr;
    App::DocumentObject* timelineAnchor = target;
    if (insertAtBeginning) {
        for (auto* member : body->Group.getValues()) {
            auto* root = const_cast<App::DocumentObject*>(semanticTimelineRoot(member, document));
            if (root && std::ranges::find(timelineRoots, root) == timelineRoots.end()) {
                timelineAnchor = root;
                break;
            }
        }
        if (!timelineAnchor) {
            return;
        }
    }
    const auto targetIdentity = bodyCreationIdentityOf(target);
    const auto anchorIdentity = bodyCreationIdentityOf(timelineAnchor);
    auto* expectedLastObject = timelineRoots.back();
    const auto lastIdentity = bodyCreationIdentityOf(expectedLastObject);
    const std::string bodyCommand = getObjectCmd(body);
    std::vector<std::string> featureCommands;
    featureCommands.reserve(features.size());
    for (const auto* feature : features) {
        featureCommands.push_back(getObjectCmd(feature));
    }
    std::stringstream timelineReorder;
    timelineReorder
        << (insertAtBeginning ? "reorderTimelineOperationBlocksBefore(["
                              : "reorderTimelineOperationBlocksAfter([")
        << getObjectCmd(timelineRoots.front());
    for (std::size_t rootIndex = 1; rootIndex < timelineRoots.size(); ++rootIndex) {
        timelineReorder << ", " << getObjectCmd(timelineRoots[rootIndex]);
    }
    timelineReorder << "], " << getObjectCmd(timelineAnchor) << ")";

    const int transactionId
        = openCommand(document, QT_TRANSLATE_NOOP("Command", "Move a feature inside body"));
    if (transactionId == App::NullTransaction) {
        return;
    }
    try {
        std::string lastCommand = getObjectCmd(target);
        for (const auto& featureCommand : featureCommands) {
            // Remove and re-insert the feature to/from the Body, preserving their order.
            runCommand(Doc, (bodyCommand + ".removeObject(" + featureCommand + ")").c_str());
            runCommand(
                Doc,
                (bodyCommand + ".insertObject("
                 + featureCommand + ", " + lastCommand
                 + ", True)")
                    .c_str()
            );
            lastCommand = featureCommand;
        }

        auto* currentBody = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(bodyIdentity)
        );
        auto* currentTarget =
            resolveBodyCreationObject(targetIdentity);
        std::vector<App::DocumentObject*> currentFeatures;
        currentFeatures.reserve(featureIdentities.size());
        for (const auto& identity : featureIdentities) {
            auto* currentFeature =
                resolveBodyCreationObject(identity);
            if (!currentFeature || !currentBody
                || currentFeature->getDocument() != document
                || !currentBody->hasObject(currentFeature)) {
                throw Base::RuntimeError(
                    "A feature changed while reordering the body"
                );
            }
            currentFeatures.push_back(currentFeature);
        }
        if ((target && !currentTarget)
            || !currentBody
            || currentBody->getDocument() != document) {
            throw Base::RuntimeError(
                "The target body changed while reordering features"
            );
        }

        // Dependency order check. Result features must not depend on later
        // result features in the same body.
        std::vector<App::DocumentObject*> bodyFeatures;
        std::map<App::DocumentObject*, size_t> orders;
        for (auto obj : currentBody->Group.getValues()) {
            if (obj->isDerivedFrom<PartDesign::Feature>()) {
                orders.emplace(obj, bodyFeatures.size());
                bodyFeatures.push_back(obj);
            }
        }
        bool failed = false;
        std::ostringstream ss;
        for (size_t i = 0; i < bodyFeatures.size(); ++i) {
            auto feat = bodyFeatures[i];
            for (auto obj : feat->getOutList()) {
                if (obj->isDerivedFrom<PartDesign::Feature>()) {
                    continue;
                }
                for (auto dep : App::Document::getDependencyList({obj})) {
                    auto it = orders.find(dep);
                    if (it != orders.end() && it->second > i) {
                        ss << feat->Label.getValue() << ", "
                           << obj->Label.getValue() << " -> "
                           << it->first->Label.getValue();
                        if (!failed) {
                            failed = true;
                        }
                        else {
                            ss << std::endl;
                        }
                    }
                }
            }
        }
        if (failed) {
            QMessageBox::critical(
                nullptr,
                QObject::tr("Dependency violation"),
                QObject::tr("Early feature must not depend on later feature.\n\n")
                    + QString::fromUtf8(ss.str().c_str())
            );
            abortCommand(transactionId);
            resetTransactionID();
            return;
        }

        auto* currentAnchor =
            resolveBodyCreationObject(anchorIdentity);
        std::vector<App::DocumentObject*> currentRoots;
        currentRoots.reserve(timelineRootIdentities.size());
        for (const auto& identity : timelineRootIdentities) {
            auto* currentRoot =
                resolveBodyCreationObject(identity);
            if (!currentRoot
                || currentRoot->getDocument() != document) {
                throw Base::RuntimeError(
                    "A semantic operation changed while reordering the body"
                );
            }
            currentRoots.push_back(currentRoot);
        }
        if (!currentAnchor
            || currentAnchor->getDocument() != document) {
            throw Base::RuntimeError(
                "The history boundary changed while reordering the body"
            );
        }
        FCMD_DOC_CMD(document, timelineReorder.str());

        currentBody = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(bodyIdentity)
        );
        currentTarget = resolveBodyCreationObject(targetIdentity);
        currentAnchor = resolveBodyCreationObject(anchorIdentity);
        currentRoots.clear();
        for (const auto& identity : timelineRootIdentities) {
            currentRoots.push_back(
                resolveBodyCreationObject(identity)
            );
        }
        auto* currentTimeline =
            App::DocumentTimeline::get(document);
        if (!currentBody || !currentAnchor || !currentTimeline
            || (target && !currentTarget)
            || std::ranges::any_of(
                currentRoots,
                [](const App::DocumentObject* root) {
                    return !root;
                }
            )) {
            throw Base::RuntimeError(
                "The body history changed while applying its global chronology"
            );
        }

        auto* currentLastObject =
            resolveBodyCreationObject(lastIdentity);
        if (expectedLastObject && !currentLastObject) {
            throw Base::RuntimeError(
                "The final moved feature changed while reordering the body"
            );
        }
        // If selected objects moved after the current tip, offer to make the
        // last moved result the new tip.
        if (currentLastObject
            && currentLastObject != currentTarget
            && semanticTimelineRoot(
                   currentBody->Tip.getValue(),
                   document
               )
                == semanticTimelineRoot(currentTarget, document)
            && PartDesign::Body::isResultFeature(currentLastObject)) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setIcon(QMessageBox::Question);
            msgBox.setWindowTitle(qApp->translate("PartDesign_MoveFeatureInTree", "Move Tip"));
            msgBox.setText(qApp->translate(
                "PartDesign_MoveFeatureInTree",
                "The moved feature appears after the currently set tip."
            ));
            msgBox.setInformativeText(
                qApp->translate("PartDesign_MoveFeatureInTree", "Set tip to last feature?")
            );
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::No);
            int ret = msgBox.exec();
            if (ret == QMessageBox::Yes) {
                currentBody = freecad_cast<PartDesign::Body*>(
                    resolveBodyCreationObject(bodyIdentity)
                );
                currentLastObject =
                    resolveBodyCreationObject(lastIdentity);
                if (!currentBody || !currentLastObject
                    || currentBody->getDocument() != document
                    || currentLastObject->getDocument() != document
                    || !currentBody->hasObject(currentLastObject)) {
                    throw Base::RuntimeError(
                        "The body changed while confirming its new tip"
                    );
                }
                FCMD_OBJ_CMD(currentBody, "Tip = " << getObjectCmd(currentLastObject));
            }
        }

        updateDocument(document);
        currentBody = freecad_cast<PartDesign::Body*>(
            resolveBodyCreationObject(bodyIdentity)
        );
        currentAnchor = resolveBodyCreationObject(anchorIdentity);
        currentTimeline = App::DocumentTimeline::get(document);
        currentRoots.clear();
        for (const auto& identity : timelineRootIdentities) {
            currentRoots.push_back(
                resolveBodyCreationObject(identity)
            );
        }
        currentFeatures.clear();
        for (const auto& identity : featureIdentities) {
            currentFeatures.push_back(
                resolveBodyCreationObject(identity)
            );
        }
        if (!currentBody || !currentAnchor || !currentTimeline
            || std::ranges::any_of(
                currentRoots,
                [](const App::DocumentObject* root) { return !root; }
            )
            || !currentBody->isValid()
            || std::ranges::any_of(
                currentFeatures,
                [document, currentBody](const App::DocumentObject* feature) {
                    return !feature || feature->getDocument() != document
                        || !currentBody->hasObject(feature) || !feature->isValid();
                }
            )) {
            throw Base::RuntimeError("Reordering features produced an invalid body history");
        }
        if (insertAtBeginning) {
            validateSemanticRootsBefore(
                currentTimeline->Operations.getValues(),
                document,
                currentRoots,
                currentAnchor
            );
        }
        else {
            validateSemanticRootsAfter(
                currentTimeline->Operations.getValues(),
                document,
                currentRoots,
                currentAnchor
            );
        }
        if (!insertAtBeginning) {
            validateSemanticRootsAfter(
                currentBody->Group.getValues(),
                document,
                currentRoots,
                currentAnchor
            );
        }
        else {
            validateSemanticRootsAtBeginning(*currentBody, currentRoots);
        }

        commitCommand(transactionId);
        resetTransactionID();
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignMoveFeatureInTree::isActive()
{
    auto* document = getDocument();
    return document && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction() && !Gui::Control().activeDialog(document);
}


//===========================================================================
// Initialization
//===========================================================================

void CreatePartDesignBodyCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdPartDesignBody());
    rcCmdMgr.addCommand(new CmdPartDesignMigrate());
    rcCmdMgr.addCommand(new CmdPartDesignMoveTip());

    rcCmdMgr.addCommand(new CmdPartDesignDuplicateSelection());
    rcCmdMgr.addCommand(new CmdPartDesignMoveFeature());
    rcCmdMgr.addCommand(new CmdPartDesignMoveFeatureInTree());
}
