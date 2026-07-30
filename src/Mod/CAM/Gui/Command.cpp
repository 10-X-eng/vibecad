// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2014 Yorik van Havre <yorik@uncreated.net>              *
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

#include <TopExp_Explorer.hxx>

#include <QByteArray>

#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <App/DocumentTimeline.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Mod/CAM/App/FeatureArea.h>
#include <Mod/CAM/App/FeaturePathCompound.h>
#include <Mod/CAM/App/FeaturePathShape.h>


namespace
{

App::Document* exactActiveDocument()
{
    auto* document = App::GetApplication().getActiveDocument();
    auto* guiDocument = Gui::Application::Instance->activeDocument();
    return document && guiDocument && guiDocument->getDocument() == document ? document : nullptr;
}

bool canStartCAMMutation()
{
    const auto* document = exactActiveDocument();
    return document && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction() && !document->isTransactionLocked()
        && !document->transacting() && !Gui::Control().activeDialog();
}

class ExactDocumentIdentity
{
public:
    explicit ExactDocumentIdentity(App::Document& document)
        : weakDocument(&document)
        , name(document.getName())
        , uid(document.Uid.getValueStr())
    {}

    App::Document& resolve() const
    {
        auto* document = *weakDocument;
        if (!document || document->getName() != name || document->Uid.getValueStr() != uid
            || App::GetApplication().getDocument(name.c_str()) != document) {
            throw Base::RuntimeError(
                "The CAM command document changed while its factory was running"
            );
        }
        return *document;
    }

private:
    App::DocumentWeakPtrT weakDocument;
    std::string name;
    std::string uid;
};

bool isCAMInputUsable(const App::DocumentObject* object) noexcept
{
    try {
        std::unordered_set<const App::DocumentObject*> visited;
        for (const auto* current = object; current;) {
            if (!visited.insert(current).second || !current->isValid()
                || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(current)) {
                return false;
            }
            const auto* linked = current->getLinkedObject(false);
            if (!linked || linked == current) {
                return true;
            }
            current = linked;
        }
        return false;
    }
    catch (...) {
        return false;
    }
}

class ExactObjectIdentity
{
public:
    ExactObjectIdentity(App::Document& document, App::DocumentObject& object)
        : expectedAddress(&object)
        , name(object.getNameInDocument() ? object.getNameInDocument() : "")
        , id(object.getID())
    {
        if (name.empty() || id <= 0 || object.getDocument() != &document
            || !document.containsObject(&object) || document.getObject(name.c_str()) != &object
            || document.getObjectByID(id) != &object) {
            throw Base::RuntimeError("A CAM command captured an invalid document-object identity");
        }
    }

    App::DocumentObject& resolve(App::Document& document) const
    {
        auto* byName = document.getObject(name.c_str());
        auto* byId = document.getObjectByID(id);
        if (!byName || byName != expectedAddress || byId != expectedAddress || byName != byId
            || byName->getDocument() != &document || !byName->getNameInDocument()
            || name != byName->getNameInDocument() || byName->getID() != id
            || !document.containsObject(byName)) {
            throw Base::RuntimeError(
                "A CAM command object changed while Python callbacks were running"
            );
        }
        return *byName;
    }

    App::DocumentObject& resolveUsable(App::Document& document) const
    {
        auto& object = resolve(document);
        if (!isCAMInputUsable(&object)) {
            throw Base::RuntimeError("A CAM command input is no longer usable at the current "
                                     "History position");
        }
        return object;
    }

private:
    const App::DocumentObject* expectedAddress;
    std::string name;
    long id;
};

std::vector<App::DocumentObject*> resolveExactObjects(
    App::Document& document,
    const std::vector<ExactObjectIdentity>& identities
)
{
    std::vector<App::DocumentObject*> objects;
    objects.reserve(identities.size());
    for (const auto& identity : identities) {
        objects.push_back(&identity.resolveUsable(document));
    }
    return objects;
}

Part::Feature* resolveSelectedPartFeature(App::Document& document, const Gui::SelectionObject& selection)
{
    const auto* selectedObject = selection.getObject();
    auto* liveObject = selectedObject ? document.getObjectByID(selectedObject->getID()) : nullptr;
    auto* feature = freecad_cast<Part::Feature*>(liveObject);
    return feature && feature == selectedObject && feature->getDocument() == &document
            && document.containsObject(feature) && isCAMInputUsable(feature)
        ? feature
        : nullptr;
}

bool hasUsablePartFeatureSelection(bool rejectUnsupportedSubelements)
{
    auto* document = exactActiveDocument();
    if (!document) {
        return false;
    }

    bool hasUsableSource = false;
    const auto selection = Gui::Selection().getSelectionEx(nullptr, Part::Feature::getClassTypeId());
    if (selection.empty()) {
        return false;
    }
    for (const auto& selected : selection) {
        if (!resolveSelectedPartFeature(*document, selected)) {
            return false;
        }
        const auto& subnames = selected.getSubNames();
        if (subnames.empty()) {
            hasUsableSource = true;
            continue;
        }
        for (const auto& subname : subnames) {
            const bool supported = !subname.compare(0, 4, "Face") || !subname.compare(0, 4, "Edge");
            if (!supported && rejectUnsupportedSubelements) {
                return false;
            }
            hasUsableSource = hasUsableSource || supported;
        }
    }
    return hasUsableSource;
}

bool hasUsablePathSelection()
{
    auto* document = exactActiveDocument();
    if (!document) {
        return false;
    }
    const auto selection = Gui::Selection().getSelection();
    if (selection.empty()) {
        return false;
    }
    for (const auto& selected : selection) {
        const auto* source = freecad_cast<Path::Feature*>(selected.pObject);
        if (!source || source->getDocument() != document || !document->containsObject(source)
            || !source->isValid() || source->Path.getValue().getCommands().empty()
            || !isCAMInputUsable(source)) {
            return false;
        }
    }
    return true;
}

bool hasUsableAreaWorkplaneSelection()
{
    auto* document = exactActiveDocument();
    if (!document) {
        return false;
    }

    std::size_t areaCount = 0;
    std::size_t planeCount = 0;
    const auto selection = Gui::Selection().getSelectionEx(nullptr, Part::Feature::getClassTypeId());
    for (const auto& selected : selection) {
        auto* feature = resolveSelectedPartFeature(*document, selected);
        if (!feature || selected.getSubNames().size() > 1) {
            return false;
        }
        const auto& subnames = selected.getSubNames();
        if (subnames.empty() && feature->isDerivedFrom<Path::FeatureArea>()) {
            ++areaCount;
            continue;
        }
        if (subnames.empty()) {
            for (TopExp_Explorer it(feature->Shape.getShape().getShape(), TopAbs_SHELL); it.More();
                 it.Next()) {
                return false;
            }
        }
        for (const auto& subname : subnames) {
            if (subname.compare(0, 4, "Face") && subname.compare(0, 4, "Edge")) {
                return false;
            }
        }
        ++planeCount;
    }
    return areaCount == 1 && planeCount == 1;
}

}  // namespace


// Path Area
// #####################################################################################################

DEF_STD_CMD_A(CmdPathArea)

CmdPathArea::CmdPathArea()
    : Command("CAM_Area")
{
    sAppModule = "Path";
    sGroup = QT_TR_NOOP("CAM");
    sMenuText = QT_TR_NOOP("Area");
    sToolTipText = QT_TR_NOOP("Creates a feature area from the selected objects");
    sWhatsThis = "CAM_Area";
    sStatusTip = sToolTipText;
    sPixmap = "CAM_Area";
}

void CmdPathArea::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!canStartCAMMutation()) {
        return;
    }
    auto* commandDocument = exactActiveDocument();
    if (!commandDocument) {
        return;
    }
    ExactDocumentIdentity documentIdentity(*commandDocument);
    const std::string commandDocumentName = commandDocument->getName();

    struct AreaSource
    {
        App::DocumentObject* existing {nullptr};
        std::string factory;
    };
    std::vector<AreaSource> sourceSpecs;
    Path::FeatureArea* sourceArea = nullptr;
    for (const Gui::SelectionObject& selObj :
         getSelection().getSelectionEx(nullptr, Part::Feature::getClassTypeId())) {
        auto* pcObj = resolveSelectedPartFeature(*commandDocument, selObj);
        if (!pcObj) {
            Base::Console().error("A Path Area cannot span documents\n");
            return;
        }
        const std::vector<std::string>& subnames = selObj.getSubNames();

        if (subnames.empty()) {
            if (pcObj->isDerivedFrom<Path::FeatureArea>()) {
                sourceArea = static_cast<Path::FeatureArea*>(pcObj);
            }
            sourceSpecs.push_back({pcObj, {}});
            continue;
        }
        for (const std::string& name : subnames) {
            if (name.compare(0, 4, "Face") && name.compare(0, 4, "Edge")) {
                Base::Console().error("Selected shape is not 2D\n");
                return;
            }

            std::ostringstream subname;
            subname << pcObj->getNameInDocument() << '_' << name;
            std::string sub_fname = getUniqueObjectName(subname.str().c_str());
            const auto sourceCommand = getObjectCmd(pcObj);

            std::ostringstream factory;
            factory << "PathCommands.createSubshapeResource(" << "App.getDocument('"
                    << commandDocument->getName() << "')," << sourceCommand << ",'" << name << "',";
            if (!name.compare(0, 4, "Edge")) {
                factory << "'Wires'";
            }
            else {
                factory << "None";
            }
            factory << ",'" << sub_fname << "')";
            sourceSpecs.push_back({nullptr, factory.str()});
        }
    }
    if (sourceSpecs.empty()) {
        Base::Console().error("Select at least one shape, face, or edge for a Path Area\n");
        return;
    }
    const bool createAreaView = sourceSpecs.size() == 1 && sourceArea
        && sourceSpecs.front().existing == sourceArea;
    if (createAreaView) {
        ExactObjectIdentity sourceIdentity(*commandDocument, *sourceArea);
        std::string FeatName = getUniqueObjectName("FeatureAreaView");
        const auto sourceCommand = getObjectCmd(sourceArea);
        openCommand(QT_TRANSLATE_NOOP("Command", "Create Path Area View"));
        try {
            const QByteArray factory = QByteArray("App.getDocument('") + commandDocumentName.c_str()
                + "').addObject('Path::FeatureAreaView','" + FeatName.c_str() + "')";
            auto* result = freecad_cast<Path::FeatureAreaView*>(Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *commandDocument,
                factory,
                Path::FeatureAreaView::getClassTypeId()
            ));
            auto& liveDocument = documentIdentity.resolve();
            sourceArea = freecad_cast<Path::FeatureArea*>(&sourceIdentity.resolveUsable(liveDocument));
            if (!result || !sourceArea) {
                throw Base::RuntimeError("The Path Area view factory returned invalid identities");
            }
            ExactObjectIdentity resultIdentity(liveDocument, *result);
            const auto resolveObjects = [&]() {
                auto& document = documentIdentity.resolve();
                auto* liveResult = freecad_cast<Path::FeatureAreaView*>(
                    &resultIdentity.resolve(document)
                );
                auto* liveSource = freecad_cast<Path::FeatureArea*>(
                    &sourceIdentity.resolveUsable(document)
                );
                if (!liveResult || !liveSource) {
                    throw Base::RuntimeError("The Path Area view identities changed");
                }
                return std::pair {liveResult, liveSource};
            };
            const auto resultCommand = getObjectCmd(result);
            doCommand(Doc, "%s.Source = %s", resultCommand.c_str(), sourceCommand.c_str());
            std::tie(result, sourceArea) = resolveObjects();
            doCommand(Doc, "import Path.Base.Util as PathTimeline");
            std::tie(result, sourceArea) = resolveObjects();
            doCommand(Doc, "PathTimeline.markTimelineOperation(%s)", resultCommand.c_str());
            std::tie(result, sourceArea) = resolveObjects();
            documentIdentity.resolve().recompute();
            std::tie(result, sourceArea) = resolveObjects();
            if (!result || result->Source.getValue() != sourceArea || !result->isValid()) {
                abortCommand();
                Base::Console().error("The Path Area view could not be created\n");
                return;
            }
            App::DocumentTimeline::ensure(&documentIdentity.resolve())
                ->finalizeProvisionalOperationBlock(result, {result});
        }
        catch (...) {
            abortCommand();
            throw;
        }
        commitCommand();
        updateActive();
        return;
    }
    std::string FeatName = getUniqueObjectName("FeatureArea");
    openCommand(QT_TRANSLATE_NOOP("Command", "Create Path Area"));
    try {
        std::vector<std::optional<ExactObjectIdentity>> sourceIdentities(sourceSpecs.size());
        std::vector<std::size_t> resourceIndices;
        for (std::size_t index = 0; index < sourceSpecs.size(); ++index) {
            if (sourceSpecs[index].existing) {
                sourceIdentities[index].emplace(*commandDocument, *sourceSpecs[index].existing);
            }
            else {
                resourceIndices.push_back(index);
            }
        }
        const auto resolveSources = [&]() {
            auto& document = documentIdentity.resolve();
            std::vector<App::DocumentObject*> liveSources;
            liveSources.reserve(sourceIdentities.size());
            for (const auto& identity : sourceIdentities) {
                if (!identity) {
                    throw Base::RuntimeError("A Path Area source has not been created");
                }
                liveSources.push_back(&identity->resolveUsable(document));
            }
            return liveSources;
        };
        doCommand(Doc, "import PathCommands");
        for (const auto& identity : sourceIdentities) {
            if (identity) {
                (void)identity->resolveUsable(documentIdentity.resolve());
            }
        }
        for (std::size_t index = 0; index < sourceSpecs.size(); ++index) {
            const auto& sourceSpec = sourceSpecs[index];
            if (sourceSpec.existing) {
                continue;
            }
            auto* resource = Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                documentIdentity.resolve(),
                QByteArray::fromStdString(sourceSpec.factory),
                Part::Feature::getClassTypeId()
            );
            if (!resource) {
                throw Base::RuntimeError("The Path Area subshape factory returned no object");
            }
            auto& liveDocument = documentIdentity.resolve();
            for (const auto& identity : sourceIdentities) {
                if (identity) {
                    (void)identity->resolveUsable(liveDocument);
                }
            }
            sourceIdentities[index].emplace(liveDocument, *resource);
        }
        const QByteArray factory = QByteArray("App.getDocument('") + commandDocumentName.c_str()
            + "').addObject('Path::FeatureArea','" + FeatName.c_str() + "')";
        auto* result = freecad_cast<Path::FeatureArea*>(Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            documentIdentity.resolve(),
            factory,
            Path::FeatureArea::getClassTypeId()
        ));
        auto sources = resolveSources();
        auto& liveDocument = documentIdentity.resolve();
        if (!result) {
            throw Base::RuntimeError("The Path Area factory returned an invalid identity");
        }
        ExactObjectIdentity resultIdentity(liveDocument, *result);
        const auto resolveResult = [&]() {
            auto& document = documentIdentity.resolve();
            auto* liveResult = freecad_cast<Path::FeatureArea*>(&resultIdentity.resolve(document));
            if (!liveResult) {
                throw Base::RuntimeError("The Path Area identity changed");
            }
            return liveResult;
        };
        const auto resultCommand = getObjectCmd(result);
        std::ostringstream sourceCommands;
        for (const auto* source : sources) {
            sourceCommands << getObjectCmd(source) << ",";
        }
        doCommand(Doc, "%s.Sources = [ %s ]", resultCommand.c_str(), sourceCommands.str().c_str());
        result = resolveResult();
        sources = resolveSources();
        doCommand(Doc, "import Path.Base.Util as PathTimeline");
        result = resolveResult();
        sources = resolveSources();
        doCommand(Doc, "PathTimeline.markTimelineOperation(%s)", resultCommand.c_str());
        result = resolveResult();
        sources = resolveSources();
        for (const std::size_t index : resourceIndices) {
            auto* resource = &sourceIdentities[index]->resolveUsable(documentIdentity.resolve());
            const auto resourceCommand = getObjectCmd(resource);
            doCommand(Doc, "%s.ViewObject.Visibility = False", resourceCommand.c_str());
            result = resolveResult();
            sources = resolveSources();
            doCommand(
                Doc,
                "PathTimeline.markTimelineResource(%s, %s)",
                resourceCommand.c_str(),
                resultCommand.c_str()
            );
            result = resolveResult();
            sources = resolveSources();
        }
        documentIdentity.resolve().recompute();
        result = resolveResult();
        sources = resolveSources();
        if (!result || result->Sources.getValues() != sources || !result->isValid()) {
            abortCommand();
            Base::Console().error("The Path Area could not be created\n");
            return;
        }
        std::vector<App::DocumentObject*> block;
        block.reserve(resourceIndices.size() + 1);
        for (const std::size_t index : resourceIndices) {
            block.push_back(&sourceIdentities[index]->resolveUsable(documentIdentity.resolve()));
        }
        block.push_back(result);
        App::DocumentTimeline::ensure(documentIdentity.resolve())
            ->finalizeProvisionalOperationBlock(result, block);
    }
    catch (...) {
        abortCommand();
        throw;
    }
    commitCommand();
    updateActive();
}

bool CmdPathArea::isActive()
{
    return canStartCAMMutation() && hasUsablePartFeatureSelection(true);
}


DEF_STD_CMD_A(CmdPathAreaWorkplane)

CmdPathAreaWorkplane::CmdPathAreaWorkplane()
    : Command("CAM_Area_Workplane")
{
    sAppModule = "Path";
    sGroup = QT_TR_NOOP("CAM");
    sMenuText = QT_TR_NOOP("Area Workplane");
    sToolTipText = QT_TR_NOOP("Selects a workplane for a feature area");
    sWhatsThis = "CAM_Area_Workplane";
    sStatusTip = sToolTipText;
    sPixmap = "CAM_Area_Workplane";
}

void CmdPathAreaWorkplane::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!canStartCAMMutation()) {
        return;
    }
    auto* commandDocument = exactActiveDocument();
    if (!commandDocument) {
        return;
    }

    std::string areaName;
    std::string planeSubname;
    std::string planeElement;
    std::string planeName;
    Path::FeatureArea* areaObject = nullptr;
    Part::Feature* planeObject = nullptr;

    for (const Gui::SelectionObject& selObj :
         getSelection().getSelectionEx(nullptr, Part::Feature::getClassTypeId())) {
        const std::vector<std::string>& subnames = selObj.getSubNames();
        if (subnames.size() > 1) {
            Base::Console().error("Select one sub shape object for plane only\n");
            return;
        }
        auto* pcObj = resolveSelectedPartFeature(*commandDocument, selObj);
        if (!pcObj) {
            Base::Console().error("A Path Area workplane cannot span documents\n");
            return;
        }
        if (subnames.empty()) {
            if (pcObj->isDerivedFrom<Path::FeatureArea>()) {
                if (!areaName.empty()) {
                    Base::Console().error("Select one feature area only\n");
                    return;
                }
                areaName = pcObj->getNameInDocument();
                areaObject = static_cast<Path::FeatureArea*>(pcObj);
                continue;
            }
            for (TopExp_Explorer it(pcObj->Shape.getShape().getShape(), TopAbs_SHELL); it.More();
                 it.Next()) {
                Base::Console().error("Selected shape is not 2D\n");
                return;
            }
        }
        if (!planeName.empty()) {
            Base::Console().error("Select one shape object for plane only\n");
            return;
        }
        else {
            planeName = pcObj->getNameInDocument();
            planeObject = pcObj;
            planeSubname = getObjectCmd(pcObj) + ".Shape";
        }

        for (const std::string& name : subnames) {
            if (name.compare(0, 4, "Face") && name.compare(0, 4, "Edge")) {
                Base::Console().error("Selected shape is not 2D\n");
                return;
            }
            std::ostringstream subname;
            subname << planeSubname << ",'" << name << "','Wires'";
            planeSubname = subname.str();
            planeElement = name;
        }
    }
    if (areaName.empty()) {
        Base::Console().error("Please select one FeatureArea\n");
        return;
    }
    if (planeName.empty()) {
        Base::Console().error("Please select one shape object\n");
        return;
    }
    if (!areaObject || !planeObject) {
        Base::Console().error("The Path Area workplane selection is no longer available\n");
        return;
    }

    ExactDocumentIdentity documentIdentity(*commandDocument);
    ExactObjectIdentity areaIdentity(*commandDocument, *areaObject);
    ExactObjectIdentity planeIdentity(*commandDocument, *planeObject);
    const auto resolveObjects = [&]() {
        auto& document = documentIdentity.resolve();
        auto* area = freecad_cast<Path::FeatureArea*>(&areaIdentity.resolveUsable(document));
        auto* plane = freecad_cast<Part::Feature*>(&planeIdentity.resolveUsable(document));
        if (!area || !plane) {
            throw Base::RuntimeError("The Path Area workplane identities changed");
        }
        return std::pair {area, plane};
    };
    openCommand(QT_TRANSLATE_NOOP("Command", "Select Workplane for Path Area"));
    try {
        const auto areaCommand = getObjectCmd(areaName.c_str(), commandDocument);
        const auto planeCommand = getObjectCmd(planeObject);
        const std::string planeElementCommand = planeElement.empty() ? "" : "'" + planeElement + "'";
        const bool useContainingWire = !planeElement.compare(0, 4, "Edge");
        doCommand(Doc, "import PathCommands");
        std::tie(areaObject, planeObject) = resolveObjects();
        doCommand(Doc, "%s.WorkPlaneSourceEnabled = True", areaCommand.c_str());
        std::tie(areaObject, planeObject) = resolveObjects();
        doCommand(
            Doc,
            "%s.WorkPlaneSource = (%s, [%s])",
            areaCommand.c_str(),
            planeCommand.c_str(),
            planeElementCommand.c_str()
        );
        std::tie(areaObject, planeObject) = resolveObjects();
        doCommand(
            Doc,
            "%s.WorkPlaneSourceCollection = %s",
            areaCommand.c_str(),
            useContainingWire ? "'Wires'" : "''"
        );
        std::tie(areaObject, planeObject) = resolveObjects();
        doCommand(
            Doc,
            "%s.WorkPlane = PathCommands.findShape(%s)",
            areaCommand.c_str(),
            planeSubname.c_str()
        );
        std::tie(areaObject, planeObject) = resolveObjects();
        doCommand(Doc, "%s.ViewObject.Visibility = True", areaCommand.c_str());
        std::tie(areaObject, planeObject) = resolveObjects();
        documentIdentity.resolve().recompute();
        std::tie(areaObject, planeObject) = resolveObjects();
        auto* result = areaObject;
        const auto& workplaneSubelements = result->WorkPlaneSource.getSubValues();
        const bool exactWorkplaneSource = result->WorkPlaneSourceEnabled.getValue()
            && result->WorkPlaneSource.getValue() == planeObject
            && std::string_view(result->WorkPlaneSourceCollection.getValue())
                == (useContainingWire ? "Wires" : "")
            && ((planeElement.empty() && workplaneSubelements.empty())
                || (workplaneSubelements.size() == 1 && workplaneSubelements.front() == planeElement));
        if (!result || !exactWorkplaneSource || result->WorkPlane.getShape().isNull()
            || !result->isValid()) {
            abortCommand();
            Base::Console().error("The Path Area workplane could not be assigned\n");
            return;
        }
    }
    catch (...) {
        abortCommand();
        throw;
    }
    commitCommand();
    updateActive();
}

bool CmdPathAreaWorkplane::isActive()
{
    return canStartCAMMutation() && hasUsableAreaWorkplaneSelection();
}


// Path compound
// #####################################################################################################


DEF_STD_CMD_A(CmdPathCompound)

CmdPathCompound::CmdPathCompound()
    : Command("CAM_Compound")
{
    sAppModule = "Path";
    sGroup = QT_TR_NOOP("CAM");
    sMenuText = QT_TR_NOOP("Compound");
    sToolTipText = QT_TR_NOOP("Creates a compound from the selected toolpaths");
    sWhatsThis = "CAM_Compound";
    sStatusTip = sToolTipText;
    sPixmap = "CAM_Compound";
}

void CmdPathCompound::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!canStartCAMMutation()) {
        return;
    }
    auto* commandDocument = exactActiveDocument();
    if (!commandDocument) {
        return;
    }
    ExactDocumentIdentity documentIdentity(*commandDocument);
    const std::string commandDocumentName = commandDocument->getName();

    const auto selection = getSelection().getSelection();
    if (selection.empty()) {
        Base::Console().error("At least one path object must be selected\n");
        return;
    }

    std::vector<ExactObjectIdentity> sourceIdentities;
    std::ostringstream sourceCommands;
    sourceIdentities.reserve(selection.size());
    for (const auto& selected : selection) {
        auto* source = freecad_cast<Path::Feature*>(selected.pObject);
        if (!source || source->getDocument() != commandDocument
            || !commandDocument->containsObject(source) || !source->isValid()
            || source->Path.getValue().getCommands().empty() || !isCAMInputUsable(source)) {
            Base::Console().error(
                "Only live path objects from the active document may be combined\n"
            );
            return;
        }
        sourceIdentities.emplace_back(*commandDocument, *source);
        sourceCommands << getObjectCmd(source) << ",";
    }

    const std::string featureName = getUniqueObjectName("PathCompound");
    openCommand(QT_TRANSLATE_NOOP("Command", "Create Path Compound"));
    try {
        const QByteArray factory = QByteArray("App.getDocument('") + commandDocumentName.c_str()
            + "').addObject('Path::FeatureCompound','" + featureName.c_str() + "')";
        auto* result = freecad_cast<Path::FeatureCompound*>(Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            documentIdentity.resolve(),
            factory,
            Path::FeatureCompound::getClassTypeId()
        ));
        auto& liveDocument = documentIdentity.resolve();
        auto sources = resolveExactObjects(liveDocument, sourceIdentities);
        if (!result) {
            throw Base::RuntimeError("The Path Compound factory returned an invalid identity");
        }
        ExactObjectIdentity resultIdentity(liveDocument, *result);
        const auto resolveCommandObjects = [&]() {
            auto& document = documentIdentity.resolve();
            auto* liveResult = freecad_cast<Path::FeatureCompound*>(&resultIdentity.resolve(document));
            if (!liveResult) {
                throw Base::RuntimeError("The Path Compound identity changed");
            }
            return std::pair {
                liveResult,
                resolveExactObjects(document, sourceIdentities),
            };
        };
        const auto resultCommand = getObjectCmd(result);
        doCommand(Doc, "%s.Group = [%s]", resultCommand.c_str(), sourceCommands.str().c_str());
        std::tie(result, sources) = resolveCommandObjects();
        doCommand(Doc, "import Path.Base.Util as PathTimeline");
        std::tie(result, sources) = resolveCommandObjects();
        doCommand(Doc, "PathTimeline.markTimelineOperation(%s)", resultCommand.c_str());
        std::tie(result, sources) = resolveCommandObjects();
        documentIdentity.resolve().recompute();
        std::tie(result, sources) = resolveCommandObjects();
        if (!result || result->Group.getValues() != sources || !result->isValid()) {
            abortCommand();
            Base::Console().error("The Path Compound could not be created\n");
            return;
        }
        App::DocumentTimeline::ensure(documentIdentity.resolve())
            ->finalizeProvisionalOperationBlock(result, {result});
    }
    catch (...) {
        abortCommand();
        throw;
    }
    commitCommand();
    updateActive();
}

bool CmdPathCompound::isActive()
{
    return canStartCAMMutation() && hasUsablePathSelection();
}

// Path Shape
// #####################################################################################################


DEF_STD_CMD_A(CmdPathShape)

CmdPathShape::CmdPathShape()
    : Command("CAM_Shape")
{
    sAppModule = "Path";
    sGroup = QT_TR_NOOP("CAM");
    sMenuText = QT_TR_NOOP("From Shape");
    sToolTipText = QT_TR_NOOP("Creates a toolpath from a selected shape");
    sWhatsThis = "CAM_Shape";
    sStatusTip = sToolTipText;
    sPixmap = "CAM_Shape";
}

void CmdPathShape::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!canStartCAMMutation()) {
        return;
    }
    auto* commandDocument = exactActiveDocument();
    if (!commandDocument) {
        return;
    }
    ExactDocumentIdentity documentIdentity(*commandDocument);
    const std::string commandDocumentName = commandDocument->getName();

    struct ShapeSource
    {
        App::DocumentObject* existing {nullptr};
        std::string factory;
    };
    std::vector<ShapeSource> sourceSpecs;
    for (const Gui::SelectionObject& selObj :
         getSelection().getSelectionEx(nullptr, Part::Feature::getClassTypeId())) {
        auto* pcObj = resolveSelectedPartFeature(*commandDocument, selObj);
        if (!pcObj) {
            Base::Console().error("A Path Shape cannot span documents\n");
            return;
        }
        const std::vector<std::string>& subnames = selObj.getSubNames();
        if (subnames.empty()) {
            sourceSpecs.push_back({pcObj, {}});
            continue;
        }
        for (const std::string& name : subnames) {
            if (name.compare(0, 4, "Face") && name.compare(0, 4, "Edge")) {
                Base::Console().error("Path Shape supports whole shapes, faces, and edges only\n");
                return;
            }

            std::ostringstream subname;
            subname << pcObj->getNameInDocument() << '_' << name;
            const std::string subFeatureName = getUniqueObjectName(subname.str().c_str());

            std::ostringstream factory;
            factory << "PathCommands.createSubshapeResource(" << "App.getDocument('"
                    << commandDocument->getName() << "')," << getObjectCmd(pcObj) << ",'" << name
                    << "',";
            if (!name.compare(0, 4, "Edge")) {
                factory << "'Wires'";
            }
            else {
                factory << "None";
            }
            factory << ",'" << subFeatureName << "')";
            sourceSpecs.push_back({nullptr, factory.str()});
        }
    }
    if (sourceSpecs.empty()) {
        Base::Console().error("Select at least one shape, face, or edge for a Path Shape\n");
        return;
    }

    const std::string featureName = getUniqueObjectName("PathShape");
    openCommand(QT_TRANSLATE_NOOP("Command", "Create Path Shape"));
    try {
        std::vector<std::optional<ExactObjectIdentity>> sourceIdentities(sourceSpecs.size());
        std::vector<std::size_t> resourceIndices;
        for (std::size_t index = 0; index < sourceSpecs.size(); ++index) {
            if (sourceSpecs[index].existing) {
                sourceIdentities[index].emplace(*commandDocument, *sourceSpecs[index].existing);
            }
            else {
                resourceIndices.push_back(index);
            }
        }
        const auto resolveSources = [&]() {
            auto& document = documentIdentity.resolve();
            std::vector<App::DocumentObject*> liveSources;
            liveSources.reserve(sourceIdentities.size());
            for (const auto& identity : sourceIdentities) {
                if (!identity) {
                    throw Base::RuntimeError("A Path Shape source has not been created");
                }
                liveSources.push_back(&identity->resolveUsable(document));
            }
            return liveSources;
        };
        doCommand(Doc, "import PathCommands");
        for (const auto& identity : sourceIdentities) {
            if (identity) {
                (void)identity->resolveUsable(documentIdentity.resolve());
            }
        }
        for (std::size_t index = 0; index < sourceSpecs.size(); ++index) {
            const auto& sourceSpec = sourceSpecs[index];
            if (sourceSpec.existing) {
                continue;
            }
            auto* resource = Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                documentIdentity.resolve(),
                QByteArray::fromStdString(sourceSpec.factory),
                Part::Feature::getClassTypeId()
            );
            if (!resource) {
                throw Base::RuntimeError("The Path Shape subshape factory returned no object");
            }
            auto& liveDocument = documentIdentity.resolve();
            for (const auto& identity : sourceIdentities) {
                if (identity) {
                    (void)identity->resolveUsable(liveDocument);
                }
            }
            sourceIdentities[index].emplace(liveDocument, *resource);
        }

        const QByteArray factory = QByteArray("App.getDocument('") + commandDocumentName.c_str()
            + "').addObject('Path::FeatureShape','" + featureName.c_str() + "')";
        auto* result = freecad_cast<Path::FeatureShape*>(Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            documentIdentity.resolve(),
            factory,
            Path::FeatureShape::getClassTypeId()
        ));
        auto sources = resolveSources();
        auto& liveDocument = documentIdentity.resolve();
        if (!result) {
            throw Base::RuntimeError("The Path Shape factory returned an invalid identity");
        }
        ExactObjectIdentity resultIdentity(liveDocument, *result);
        const auto resolveResult = [&]() {
            auto& document = documentIdentity.resolve();
            auto* liveResult = freecad_cast<Path::FeatureShape*>(&resultIdentity.resolve(document));
            if (!liveResult) {
                throw Base::RuntimeError("The Path Shape identity changed");
            }
            return liveResult;
        };
        const auto resultCommand = getObjectCmd(result);
        std::ostringstream sourceCommands;
        for (const auto* source : sources) {
            sourceCommands << getObjectCmd(source) << ",";
        }
        doCommand(Doc, "%s.Sources = [%s]", resultCommand.c_str(), sourceCommands.str().c_str());
        result = resolveResult();
        sources = resolveSources();
        doCommand(Doc, "import Path.Base.Util as PathTimeline");
        result = resolveResult();
        sources = resolveSources();
        doCommand(Doc, "PathTimeline.markTimelineOperation(%s)", resultCommand.c_str());
        result = resolveResult();
        sources = resolveSources();
        for (const std::size_t index : resourceIndices) {
            auto* resource = &sourceIdentities[index]->resolveUsable(documentIdentity.resolve());
            const auto resourceCommand = getObjectCmd(resource);
            doCommand(Doc, "%s.ViewObject.Visibility = False", resourceCommand.c_str());
            result = resolveResult();
            sources = resolveSources();
            doCommand(
                Doc,
                "PathTimeline.markTimelineResource(%s, %s)",
                resourceCommand.c_str(),
                resultCommand.c_str()
            );
            result = resolveResult();
            sources = resolveSources();
        }
        documentIdentity.resolve().recompute();
        result = resolveResult();
        sources = resolveSources();
        if (!result || result->Sources.getValues() != sources || !result->isValid()) {
            abortCommand();
            Base::Console().error("The Path Shape could not be created\n");
            return;
        }
        std::vector<App::DocumentObject*> block;
        block.reserve(resourceIndices.size() + 1);
        for (const std::size_t index : resourceIndices) {
            block.push_back(&sourceIdentities[index]->resolveUsable(documentIdentity.resolve()));
        }
        block.push_back(result);
        App::DocumentTimeline::ensure(documentIdentity.resolve())
            ->finalizeProvisionalOperationBlock(result, block);
    }
    catch (...) {
        abortCommand();
        throw;
    }
    commitCommand();
    updateActive();
}

bool CmdPathShape::isActive()
{
    return canStartCAMMutation() && hasUsablePartFeatureSelection(true);
}


void CreatePathCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdPathCompound());
    rcCmdMgr.addCommand(new CmdPathShape());
    rcCmdMgr.addCommand(new CmdPathArea());
    rcCmdMgr.addCommand(new CmdPathAreaWorkplane());
}
