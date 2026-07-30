/***************************************************************************
 *   Copyright (c) 2022 Peter McB                                          *
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <algorithm>
#include <cmath>
#include <set>

#include <Inventor/events/SoMouseButtonEvent.h>
#include <Inventor/nodes/SoCamera.h>
#include <Inventor/nodes/SoEventCallback.h>
#include <QAction>
#include <QApplication>
#include <QMessageBox>
#include <SMESHDS_Mesh.hxx>
#include <SMESH_Mesh.hxx>


#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObserver.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Exception.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/SelectionFilter.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/Utilities.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Gui/WaitCursor.h>

#include <Mod/Fem/App/FemAnalysis.h>
#include <Mod/Fem/App/FemConstraint.h>
#include <Mod/Fem/App/FemMeshObject.h>
#include <Mod/Fem/App/FemSetElementNodesObject.h>
#include <Mod/Fem/App/FemSetNodesObject.h>

#include "ActiveAnalysisObserver.h"
#include "FemSettings.h"

#ifdef FC_USE_VTK
# include <Mod/Fem/App/FemPostFilter.h>
# include <Mod/Fem/App/FemPostGroupExtension.h>
# include <Mod/Fem/App/FemPostPipeline.h>
# include <Mod/Fem/Gui/ViewProviderFemPostObject.h>
#endif


using namespace std;

//================================================================================================
//================================================================================================
// helpers
static App::Document* exactActiveFemDocument()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* guiDocument = Gui::Application::Instance->activeDocument();
    return document && guiDocument && guiDocument->getDocument() == document ? document : nullptr;
}

static bool canStartFemCommand()
{
    if (!exactActiveFemDocument() || Gui::Control().activeDialog()) {
        return false;
    }
    return std::ranges::all_of(
        App::GetApplication().getDocuments(),
        [](const App::Document* openDocument) {
            return openDocument && openDocument->getBookedTransactionID() == App::NullTransaction
                && !openDocument->hasPendingTransaction();
        }
    );
}

static void markTimelineReplacedInputs(
    App::DocumentObject* operation,
    const std::vector<App::DocumentObject*>& inputs
)
{
    auto* document = operation ? operation->getDocument() : nullptr;
    if (!document || !operation->getNameInDocument() || !document->containsObject(operation)) {
        throw Base::ValueError("A FEM replacement operation must be live in its document");
    }

    std::vector<App::DocumentObject*> exactInputs;
    for (auto* input : inputs) {
        if (!input || input == operation || input->getDocument() != document
            || !input->getNameInDocument() || !document->containsObject(input)) {
            throw Base::ValueError(
                "A FEM replaced input must be distinct and live in the operation document"
            );
        }
        if (std::ranges::find(exactInputs, input) == exactInputs.end()) {
            exactInputs.push_back(input);
        }
    }
    if (exactInputs.empty()) {
        throw Base::ValueError("A FEM replacement operation requires an exact input");
    }

    auto ensureProperty = [](App::DocumentObject* object, const char* type, const char* name) {
        auto* property = object->getPropertyByName(name);
        if (!property) {
            property = object->addDynamicProperty(
                type,
                name,
                "Timeline",
                "Document timeline replacement contract",
                App::Prop_NoRecompute,
                true,
                true
            );
        }
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
        return property;
    };
    auto* replaced = dynamic_cast<App::PropertyLinkListHidden*>(ensureProperty(
        operation,
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName
    ));
    auto* role = dynamic_cast<App::PropertyString*>(
        ensureProperty(operation, "App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    if (!replaced || !role) {
        throw Base::TypeError("FEM replacement timeline metadata has incompatible property types");
    }
    if (auto* property = operation->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(property);
        if (!owner || owner->getValue()) {
            throw Base::TypeError("A FEM replacement operation cannot retain resource-owner metadata");
        }
    }
    auto canonicalizeOptionalProperty = [](App::Property* property) {
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
    };
    if (auto* property = operation->getPropertyByName(App::DocumentTimeline::EditorPropertyName)) {
        if (!dynamic_cast<App::PropertyLinkHidden*>(property)) {
            throw Base::TypeError("FEM timeline editor metadata has an incompatible property type");
        }
        canonicalizeOptionalProperty(property);
    }
    if (auto* property = operation->getPropertyByName(App::DocumentTimeline::EditCommandPropertyName)) {
        if (!dynamic_cast<App::PropertyString*>(property)) {
            throw Base::TypeError(
                "FEM timeline edit-command metadata has an incompatible property type"
            );
        }
        canonicalizeOptionalProperty(property);
    }

    replaced->setValues(exactInputs);
    role->setValue(App::DocumentTimeline::OperationRole);
}

static Fem::FemAnalysis* activeAnalysisInActiveDocument()
{
    if (!canStartFemCommand() || !FemGui::ActiveAnalysisObserver::instance()->hasActiveObject()) {
        return nullptr;
    }
    Fem::FemAnalysis* analysis = FemGui::ActiveAnalysisObserver::instance()->getActiveObject();
    App::Document* document = App::GetApplication().getActiveDocument();
    return analysis && analysis->getDocument() == document && analysis->isAttachedToDocument()
        ? analysis
        : nullptr;
}

static bool belongsToActiveFemDocument(const App::DocumentObject* object)
{
    App::Document* document = exactActiveFemDocument();
    return object && document && object->getDocument() == document && object->isAttachedToDocument()
        && document->getObject(object->getNameInDocument()) == object;
}

static bool getConstraintPrerequisits(Fem::FemAnalysis** Analysis)
{
    *Analysis = activeAnalysisInActiveDocument();
    if (!*Analysis) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No active Analysis"),
            QObject::tr("Create or activate an analysis in the active document.")
        );
        return true;
    }

    // return with no error
    return false;
}

class FemCommandRollbackGuard
{
public:
    explicit FemCommandRollbackGuard(Gui::Command* command)
        : command(command)
    {}

    ~FemCommandRollbackGuard()
    {
        if (command && command->transactionID() != App::NullTransaction) {
            command->abortCommand();
        }
    }

private:
    Gui::Command* command;
};

class FemTransactionGuard
{
public:
    explicit FemTransactionGuard(int transactionId)
        : transactionId(transactionId)
    {}

    ~FemTransactionGuard()
    {
        if (transactionId != App::NullTransaction) {
            Gui::Command::abortCommand(transactionId);
        }
    }

    void commit()
    {
        if (transactionId != App::NullTransaction) {
            Gui::Command::commitCommand(transactionId);
            transactionId = App::NullTransaction;
        }
    }

private:
    int transactionId;
};

class ExactFemObject
{
public:
    ExactFemObject() = default;

    explicit ExactFemObject(App::DocumentObject* object)
        : document(object ? object->getDocument() : nullptr)
        , objectId(object ? object->getID() : -1)
        , pythonReference(object ? Gui::Command::getObjectCmd(object) : std::string {})
    {}

    App::DocumentObject* get() const
    {
        App::DocumentObject* object = document && objectId >= 0 ? document->getObjectByID(objectId)
                                                                : nullptr;
        return object && object->getDocument() == document && object->isAttachedToDocument()
                && object->getNameInDocument() && document->containsObject(object)
            ? object
            : nullptr;
    }

    bool empty() const
    {
        return get() == nullptr;
    }

    App::Document* getDocument() const
    {
        App::DocumentObject* object = get();
        return object ? object->getDocument() : nullptr;
    }

    const char* c_str() const
    {
        if (!get()) {
            throw Base::RuntimeError("An exact FEM command object no longer exists");
        }
        return pythonReference.c_str();
    }

private:
    App::Document* document = nullptr;
    long objectId = -1;
    std::string pythonReference;
};

static void removeExactFemObject(const ExactFemObject& exactObject)
{
    App::DocumentObject* object = exactObject.get();
    App::Document* document = object ? object->getDocument() : nullptr;
    const char* name = object ? object->getNameInDocument() : nullptr;
    if (document && name) {
        document->removeObject(name);
    }
}

static ExactFemObject createFemObject(App::Document* document, const char* typeId, const std::string& name)
{
    if (!document) {
        return {};
    }
    const std::string documentReference = App::DocumentT(document).getDocumentPython();
    QByteArray expression(documentReference.c_str());
    expression += ".addObject('";
    expression += typeId;
    expression += "','";
    expression += name.c_str();
    expression += "')";
    App::DocumentObject* object = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        expression,
        Base::Type::fromName(typeId)
    );
    ExactFemObject exactObject(object);
    return exactObject;
}

static ExactFemObject beginFemObjectCreation(
    Gui::Command* command,
    App::Document* document,
    std::string transactionName,
    const char* typeId,
    const std::string& name
)
{
    if (!command || !document
        || command->openCommand(document, std::move(transactionName)) == App::NullTransaction) {
        return {};
    }
    ExactFemObject object = createFemObject(document, typeId, name);
    if (object.empty()) {
        command->abortCommand();
    }
    return object;
}

static bool addFemObjectToAnalysis(const ExactFemObject& exactAnalysis, const ExactFemObject& exactObject)
{
    auto* analysis = dynamic_cast<Fem::FemAnalysis*>(exactAnalysis.get());
    App::DocumentObject* object = exactObject.get();
    if (!analysis || !object || object->getDocument() != analysis->getDocument()) {
        return false;
    }
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.addObject(%s)",
        exactAnalysis.c_str(),
        exactObject.c_str()
    );
    analysis = dynamic_cast<Fem::FemAnalysis*>(exactAnalysis.get());
    object = exactObject.get();
    if (!analysis || !object) {
        return false;
    }
    const auto parents = object->getInList();
    const auto analysisParentCount
        = std::ranges::count_if(parents, [](const App::DocumentObject* parent) {
              return parent && parent->isDerivedFrom<Fem::FemAnalysis>();
          });
    if (analysisParentCount != 1 || std::ranges::find(parents, analysis) == parents.end()) {
        return false;
    }
    return true;
}

static ExactFemObject beginFemAnalysisObjectCreation(
    Gui::Command* command,
    Fem::FemAnalysis* analysis,
    std::string transactionName,
    const char* typeId,
    const std::string& name
)
{
    if (!analysis) {
        return {};
    }
    const ExactFemObject exactAnalysis(analysis);
    ExactFemObject object = beginFemObjectCreation(
        command,
        analysis->getDocument(),
        std::move(transactionName),
        typeId,
        name
    );
    if (object.empty() || !addFemObjectToAnalysis(exactAnalysis, object)) {
        command->abortCommand();
        return {};
    }
    return object;
}

static bool startFemObjectEditor(Gui::Command* command, const ExactFemObject& exactObject)
{
    App::DocumentObject* object = exactObject.get();
    App::Document* document = object ? object->getDocument() : nullptr;
    if (!command || !document) {
        if (command) {
            command->abortCommand();
        }
        return false;
    }
    Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
    Gui::ViewProvider* view = object ? Gui::Application::Instance->getViewProvider(object) : nullptr;
    const char* objectName = object ? object->getNameInDocument() : nullptr;
    if (!guiDocument || !view || !objectName) {
        command->abortCommand();
        return false;
    }
    Gui::Command::doCommand(
        Gui::Command::Gui,
        "Gui.getDocument('%s').setEdit('%s')",
        document->getName(),
        objectName
    );
    object = exactObject.get();
    view = object ? Gui::Application::Instance->getViewProvider(object) : nullptr;
    if (!object || !view || guiDocument->getInEdit() != view) {
        command->abortCommand();
        return false;
    }
    // The task dialog now owns the document transaction.  Prevent exception
    // cleanup in the invoking command from aborting a successfully opened
    // editor.
    command->resetTransactionID();
    return true;
}

// OvG: Visibility automation show parts and hide meshes on activation of a constraint
static std::string gethideMeshShowPartStr(const App::Document* document, std::string showConstr = "")
{
    if (!document) {
        return {};
    }
    const std::string documentExpression = "App.getDocument('" + std::string(document->getName())
        + "')";
    return "for amesh in " + documentExpression + ".Objects:\n\
    if \""
        + showConstr + "\" == amesh.Name:\n\
        amesh.ViewObject.Visibility = True\n\
    elif \"Mesh\" in amesh.TypeId:\n\
        aparttoshow = amesh.Name.replace(\"_Mesh\",\"\")\n\
        for apart in "
        + documentExpression + ".Objects:\n\
            if aparttoshow == apart.Name:\n\
                apart.ViewObject.Visibility = True\n\
        amesh.ViewObject.Visibility = False\n";
}

static std::string getSelectedNodes(Gui::View3DInventorViewer* view)
{
    Gui::SelectionRole role;
    std::vector<SbVec2f> clPoly = view->getGLPolygon(&role);
    if (clPoly.size() < 3) {
        return {};
    }
    if (clPoly.front() != clPoly.back()) {
        clPoly.push_back(clPoly.front());
    }

    SoCamera* cam = view->getSoRenderManager()->getCamera();
    SbViewVolume vv = cam->getViewVolume();
    Gui::ViewVolumeProjection proj(vv);
    Base::Polygon2d polygon;
    for (auto it : clPoly) {
        polygon.Add(Base::Vector2d(it[0], it[1]));
    }

    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Fem::FemMeshObject::getClassTypeId()
    );
    if (docObj.size() != 1) {
        return {};
    }

    const SMESHDS_Mesh* data
        = static_cast<Fem::FemMeshObject*>(docObj[0])->FemMesh.getValue().getSMesh()->GetMeshDS();

    SMDS_NodeIteratorPtr aNodeIter = data->nodesIterator();
    Base::Vector3f pt2d;
    std::set<int> IntSet;

    while (aNodeIter->more()) {
        const SMDS_MeshNode* aNode = aNodeIter->next();
        Base::Vector3f vec(aNode->X(), aNode->Y(), aNode->Z());
        pt2d = proj(vec);
        if (polygon.Contains(Base::Vector2d(pt2d.x, pt2d.y))) {
            IntSet.insert(aNode->GetID());
        }
    }

    std::stringstream set;

    set << "[";
    for (auto it = IntSet.cbegin(); it != IntSet.cend(); ++it) {
        if (it == IntSet.begin()) {
            set << *it;
        }
        else {
            set << "," << *it;
        }
    }
    set << "]";

    return set.str();
}


//================================================================================================
//================================================================================================
// commands Part, Analysis, Solver

//================================================================================================
/* ATM no gui command implemented in workbench.cpp, user does it in single steps
DEF_STD_CMD_A(CmdFemAddPart)

CmdFemAddPart::CmdFemAddPart()
  : Command("FEM_FemAddPart")
{
    sAppModule      = "Fem";
    sGroup          = QT_TR_NOOP("Fem");
    sMenuText       = QT_TR_NOOP("Add Part to Analysis");
    sToolTipText    = QT_TR_NOOP("Adds a part to the analysis");
    sWhatsThis      = "FEM_FemAddPart";
    sStatusTip      = sToolTipText;
    sPixmap         = "fem-add-fem-mesh";
}

void CmdFemAddPart::activated(int)
{
#ifndef FCWithNetgen
    QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
            QObject::tr("Your FreeCAD is built without NETGEN support. Meshing will not work…"));
    return;
#endif

    std::vector<Gui::SelectionObject> selection = getSelection().getSelectionEx();

    if (selection.size() != 1) {
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong selection"),
            QObject::tr("Select an edge, face, or body. Only one body is allowed."));
        return;
    }

    if (!selection[0].isObjectTypeOf(Part::Feature::getClassTypeId())){
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Wrong object type"),
            QObject::tr("Fillet works only on parts"));
        return;
    }

    Part::Feature *base = static_cast<Part::Feature*>(selection[0].getObject());

    std::string AnalysisName = getUniqueObjectName("FemAnalysis");
    std::string MeshName = getUniqueObjectName((std::string(base->getNameInDocument())
+"_Mesh").c_str());

    openCommand(QT_TRANSLATE_NOOP("Command", "Create FEM analysis"));
    doCommand(Doc,"App.activeDocument().addObject('Fem::FemAnalysis','%s')",AnalysisName.c_str());
    doCommand(Doc,"App.activeDocument().addObject('Fem::FemMeshShapeNetgenObject','%s')",MeshName.c_str());
    doCommand(Doc,"App.activeDocument().ActiveObject.Shape =
App.activeDocument().%s",base->getNameInDocument());
    doCommand(Doc,"App.activeDocument().%s.addObject(App.activeDocument().%s)",AnalysisName.c_str(),MeshName.c_str());
    addModule(Gui,"FemGui");
    doCommand(Gui,"FemGui.setActiveAnalysis(App.activeDocument().%s)",AnalysisName.c_str());
    commitCommand();

    updateActive();
}

bool CmdFemAddPart::isActive(void)
{
    if (Gui::Control().activeDialog())
        return false;
    return Gui::Selection().countObjectsOfType<Part::Feature>(type) > 0;
}
*/


//================================================================================================
//================================================================================================
// commands Constraints

//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintBearing)

CmdFemConstraintBearing::CmdFemConstraintBearing()
    : Command("FEM_ConstraintBearing")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Bearing Constraint");
    sToolTipText = QT_TR_NOOP("Creates a bearing constraint");
    sWhatsThis = "FEM_ConstraintBearing";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintBearing";
}

void CmdFemConstraintBearing::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("ConstraintBearing");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make bearing constraint"),
        "Fem::ConstraintBearing",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintBearing::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintContact)

CmdFemConstraintContact::CmdFemConstraintContact()
    : Command("FEM_ConstraintContact")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Contact Constraint");
    sToolTipText = QT_TR_NOOP("Creates a contact constraint between faces");
    sWhatsThis = "FEM_ConstraintContact";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintContact";
}

void CmdFemConstraintContact::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Contact");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make contact constraint on a face"),
        "Fem::ConstraintContact",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(
        Doc,
        "%s.Slope = \"1e6 GPa/m\"",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(Doc, "%s.Adjust = 0.0",
              featureReference.c_str());  // OvG: set default equal to 0
    doCommand(Doc, "%s.Friction = False",
              featureReference.c_str());  // OvG: set default equal to 0
    doCommand(
        Doc,
        "%s.FrictionCoefficient = 0.0",
        featureReference.c_str()
    );  // OvG: set default equal to 0
    doCommand(
        Doc,
        "%s.StickSlope = \"1e4 GPa/m\"",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintContact::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintDisplacement)

CmdFemConstraintDisplacement::CmdFemConstraintDisplacement()
    : Command("FEM_ConstraintDisplacement")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Displacement Boundary Condition");
    sToolTipText = QT_TR_NOOP("Creates a displacement boundary condition for a geometric entity");
    sWhatsThis = "FEM_ConstraintDisplacement";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintDisplacement";
}

void CmdFemConstraintDisplacement::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Displacement");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make displacement boundary condition on face"),
        "Fem::ConstraintDisplacement",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    // OvG: set initial scale to 1
    doCommand(Doc, "%s.Scale = 1", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintDisplacement::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintFixed)

CmdFemConstraintFixed::CmdFemConstraintFixed()
    : Command("FEM_ConstraintFixed")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Fixed Boundary Condition");
    sToolTipText = QT_TR_NOOP("Creates a fixed boundary condition for a geometric entity");
    sWhatsThis = "FEM_ConstraintFixed";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintFixed";
}

void CmdFemConstraintFixed::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Fixed");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make fixed boundary condition for geometry"),
        "Fem::ConstraintFixed",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    // OvG: set initial scale to 1
    doCommand(Doc, "%s.Scale = 1", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintFixed::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintRigidBody)

CmdFemConstraintRigidBody::CmdFemConstraintRigidBody()
    : Command("FEM_ConstraintRigidBody")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Rigid Body Constraint");
    sToolTipText = QT_TR_NOOP("Creates a rigid body constraint for a geometric entity");
    sWhatsThis = "FEM_ConstraintRigidBody";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintRigidBody";
}

void CmdFemConstraintRigidBody::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("RigidBody");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make rigid body constraint"),
        "Fem::ConstraintRigidBody",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    doCommand(
        Doc,
        "%s",
        gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str()
    );  // OvG: Hide meshes and show parts

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintRigidBody::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintFluidBoundary)

CmdFemConstraintFluidBoundary::CmdFemConstraintFluidBoundary()
    : Command("FEM_ConstraintFluidBoundary")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Fluid Boundary Condition");
    sToolTipText = QT_TR_NOOP(
        "Create fluid boundary condition on face entity for Computional Fluid Dynamics"
    );
    sWhatsThis = "FEM_ConstraintFluidBoundary";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintFluidBoundary";
}

void CmdFemConstraintFluidBoundary::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("ConstraintFluidBoundary");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Create fluid boundary condition"),
        "Fem::ConstraintFluidBoundary",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1
    // BoundaryValue is already the default value, zero is acceptable

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());
    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintFluidBoundary::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintForce)

CmdFemConstraintForce::CmdFemConstraintForce()
    : Command("FEM_ConstraintForce")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Force Load");
    sToolTipText = QT_TR_NOOP("Creates a force load applied to a geometric entity");
    sWhatsThis = "FEM_ConstraintForce";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintForce";
}

void CmdFemConstraintForce::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Force");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make force load on geometry"),
        "Fem::ConstraintForce",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Force = \"1 N\"",
              featureReference.c_str());  // OvG: set default to 1 N
    doCommand(Doc, "%s.Reversed = False",
              featureReference.c_str());  // OvG: set default to False
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintForce::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintGear)

CmdFemConstraintGear::CmdFemConstraintGear()
    : Command("FEM_ConstraintGear")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Gear Constraint");
    sToolTipText = QT_TR_NOOP("Creates a gear constraint");
    sWhatsThis = "FEM_ConstraintGear";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintGear";
}

void CmdFemConstraintGear::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);
    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("ConstraintGear");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make gear constraint"),
        "Fem::ConstraintGear",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Diameter = 100.0", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintGear::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintHeatflux)

CmdFemConstraintHeatflux::CmdFemConstraintHeatflux()
    : Command("FEM_ConstraintHeatflux")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Heat Flux Load");
    sToolTipText = QT_TR_NOOP("Creates a heat flux load acting on a face");
    sWhatsThis = "FEM_ConstraintHeatflux";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintHeatflux";
}

void CmdFemConstraintHeatflux::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("HeatFlux");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make heat flux load on face"),
        "Fem::ConstraintHeatflux",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.ConstraintType = \"Flux\"", featureReference.c_str());
    doCommand(
        Doc,
        "%s.AmbientTemp = 300.0",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(
        Doc,
        "%s.FilmCoef = 10.0",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(
        Doc,
        "%s.Emissivity = 1.0",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument()).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintHeatflux::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintInitialTemperature)

CmdFemConstraintInitialTemperature::CmdFemConstraintInitialTemperature()
    : Command("FEM_ConstraintInitialTemperature")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Initial Temperature");
    sToolTipText = QT_TR_NOOP("Creates an initial temperature acting on a body");
    sWhatsThis = "FEM_ConstraintInitialTemperature";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintInitialTemperature";
}

void CmdFemConstraintInitialTemperature::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("InitialTemperature");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make initial temperature condition on body"),
        "Fem::ConstraintInitialTemperature",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument()).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintInitialTemperature::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintPlaneRotation)

CmdFemConstraintPlaneRotation::CmdFemConstraintPlaneRotation()
    : Command("FEM_ConstraintPlaneRotation")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Plane Multi-Point Constraint");
    sToolTipText = QT_TR_NOOP("Creates a plane multi-point constraint for a face");
    sWhatsThis = "FEM_ConstraintPlaneRotation";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintPlaneRotation";
}

void CmdFemConstraintPlaneRotation::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("PlaneRotation");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make plane multi-point constraint on face"),
        "Fem::ConstraintPlaneRotation",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintPlaneRotation::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintPressure)

CmdFemConstraintPressure::CmdFemConstraintPressure()
    : Command("FEM_ConstraintPressure")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Pressure Load");
    sToolTipText = QT_TR_NOOP("Creates a pressure load acting on a face");
    sWhatsThis = "FEM_ConstraintPressure";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintPressure";
}

void CmdFemConstraintPressure::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Pressure");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make pressure load on face"),
        "Fem::ConstraintPressure",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(
        Doc,
        "%s.Pressure = 0.1",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(Doc, "%s.Reversed = False",
              featureReference.c_str());  // OvG: set default to False
    // OvG: set initial scale to 1
    doCommand(Doc, "%s.Scale = 1", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintPressure::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintSpring)

CmdFemConstraintSpring::CmdFemConstraintSpring()
    : Command("FEM_ConstraintSpring")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Spring Boundary Condition");
    sToolTipText = QT_TR_NOOP("Creates a spring boundary condition on a face");
    sWhatsThis = "FEM_ConstraintSpring";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintSpring";
}

void CmdFemConstraintSpring::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Spring");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make Spring Constraint"),
        "Fem::ConstraintSpring",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(
        Doc,
        "%s.NormalStiffness = 1.0",
        featureReference.c_str()
    );  // OvG: set default not equal to 0
    doCommand(
        Doc,
        "%s.TangentialStiffness = 0.0",
        featureReference.c_str()
    );  // OvG: set default to False
    // OvG: set initial scale to 1
    doCommand(Doc, "%s.Scale = 1", featureReference.c_str());
    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintSpring::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintPulley)

CmdFemConstraintPulley::CmdFemConstraintPulley()
    : Command("FEM_ConstraintPulley")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Pulley Constraint");
    sToolTipText = QT_TR_NOOP("Creates a pulley constraint");
    sWhatsThis = "FEM_ConstraintPulley";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintPulley";
}

void CmdFemConstraintPulley::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("ConstraintPulley");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make pulley constraint"),
        "Fem::ConstraintPulley",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Diameter = 300.0", featureReference.c_str());
    doCommand(Doc, "%s.OtherDiameter = 100.0", featureReference.c_str());
    doCommand(Doc, "%s.CenterDistance = 500.0", featureReference.c_str());
    doCommand(Doc, "%s.Force = 100.0", featureReference.c_str());
    doCommand(Doc, "%s.TensionForce = 100.0", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintPulley::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintTemperature)

CmdFemConstraintTemperature::CmdFemConstraintTemperature()
    : Command("FEM_ConstraintTemperature")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Temperature Boundary Condition");
    sToolTipText = QT_TR_NOOP("Creates a temperature/concentrated heat flux load acting on a face");
    sWhatsThis = "FEM_ConstraintTemperature";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintTemperature";
}

void CmdFemConstraintTemperature::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Temperature");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make temperature boundary condition on face"),
        "Fem::ConstraintTemperature",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1",
              featureReference.c_str());  // OvG: set initial scale to 1

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument()).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintTemperature::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemConstraintTransform)

CmdFemConstraintTransform::CmdFemConstraintTransform()
    : Command("FEM_ConstraintTransform")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Local Coordinate System");
    sToolTipText = QT_TR_NOOP("Creates a local coordinate system on a face");
    sWhatsThis = "FEM_ConstraintTransform";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_ConstraintTransform";
}

void CmdFemConstraintTransform::activated(int)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }
    FemCommandRollbackGuard rollback(this);

    std::string FeatName = Analysis->getDocument()->getUniqueObjectName("Transform");

    const ExactFemObject featureReference = beginFemAnalysisObjectCreation(
        this,
        Analysis,
        QT_TRANSLATE_NOOP("Command", "Make local coordinate system on face"),
        "Fem::ConstraintTransform",
        FeatName
    );
    if (featureReference.empty()) {
        return;
    }
    doCommand(Doc, "%s.Scale = 1", featureReference.c_str());

    // OvG: Hide meshes and show parts
    doCommand(Doc, "%s", gethideMeshShowPartStr(featureReference.getDocument(), FeatName).c_str());

    updateActive();

    startFemObjectEditor(this, featureReference);
}

bool CmdFemConstraintTransform::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//================================================================================================
//================================================================================================
// commands mesh

//================================================================================================
DEF_STD_CMD_A(CmdFemDefineNodesSet)

static void DefineNodesCallback(void* ud, SoEventCallback* n)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }

    // show the wait cursor because this could take quite some time
    Gui::WaitCursor wc;

    // When this callback function is invoked we must in either case leave the edit mode
    Gui::View3DInventorViewer* view = static_cast<Gui::View3DInventorViewer*>(n->getUserData());
    view->setEditing(false);
    view->removeEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineNodesCallback, ud);
    n->setHandled();

    std::string str = getSelectedNodes(view);
    if (!str.empty()) {
        const ExactFemObject exactAnalysis(Analysis);
        App::Document* document = Analysis->getDocument();
        const int tid = Gui::Command::openActiveDocumentCommand(
            QT_TRANSLATE_NOOP("Command", "Place robot")
        );
        if (tid == App::NullTransaction) {
            return;
        }
        FemTransactionGuard transaction(tid);
        const std::string name = document->getUniqueObjectName("NodeSet");
        const ExactFemObject nodeSet = createFemObject(document, "Fem::FemSetNodesObject", name);
        if (nodeSet.empty()) {
            return;
        }
        Gui::Command::doCommand(Gui::Command::Doc, "%s.Nodes = %s", nodeSet.c_str(), str.c_str());
        if (nodeSet.empty() || !addFemObjectToAnalysis(exactAnalysis, nodeSet)) {
            return;
        }

        transaction.commit();
    }
}


CmdFemDefineNodesSet::CmdFemDefineNodesSet()
    : Command("FEM_DefineNodesSet")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Node Set by Polygon");
    sToolTipText = QT_TR_NOOP("Creates a node set by polygon selection");
    sWhatsThis = "FEM_DefineNodesSet";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_CreateNodesSet";
}

void CmdFemDefineNodesSet::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Fem::FemMeshObject::getClassTypeId()
    );

    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = getActiveGuiDocument();
            Gui::MDIView* view = doc->getActiveView();
            if (view->isDerivedFrom<Gui::View3DInventor>()) {
                Gui::View3DInventorViewer* viewer = ((Gui::View3DInventor*)view)->getViewer();
                viewer->setEditing(true);
                viewer->startSelection(Gui::View3DInventorViewer::Clip);
                viewer->addEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineNodesCallback);
            }
            else {
                return;
            }
        }

        // Gui::ViewProvider* pVP = getActiveGuiDocument()->getViewProvider(*it);
        // if (pVP->isVisible())
        //     pVP->startEditing();
    }
}

bool CmdFemDefineNodesSet::isActive()
{
    if (!canStartFemCommand()) {
        return false;
    }

    // Check for the selected mesh feature (all Mesh types)
    if (getSelection().countObjectsOfType<Fem::FemMeshObject>() != 1) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemCreateNodesSet)

CmdFemCreateNodesSet::CmdFemCreateNodesSet()
    : Command("FEM_CreateNodesSet")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Nodes Set");
    sToolTipText = QT_TR_NOOP("Creates a FEM mesh nodes set");
    sWhatsThis = "FEM_CreateNodesSet";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_CreateNodesSet";
}

void CmdFemCreateNodesSet::activated(int)
{
    Gui::SelectionFilter ObjectFilter("SELECT Fem::FemSetNodesObject COUNT 1");
    Gui::SelectionFilter FemMeshFilter("SELECT Fem::FemMeshObject COUNT 1");

    if (ObjectFilter.match()) {
        Fem::FemSetNodesObject* NodesObj = static_cast<Fem::FemSetNodesObject*>(
            ObjectFilter.Result[0][0].getObject()
        );
        if (!belongsToActiveFemDocument(NodesObj)) {
            return;
        }
        FemCommandRollbackGuard rollback(this);
        if (openCommand(NodesObj->getDocument(), QT_TRANSLATE_NOOP("Command", "Edit nodes set"))
            == App::NullTransaction) {
            return;
        }
        startFemObjectEditor(this, ExactFemObject(NodesObj));
    }
    else if (FemMeshFilter.match()) {
        FemCommandRollbackGuard rollback(this);
        Fem::FemMeshObject* MeshObj = static_cast<Fem::FemMeshObject*>(
            FemMeshFilter.Result[0][0].getObject()
        );
        App::Document* document = exactActiveFemDocument();
        if (!document || MeshObj->getDocument() != document || !MeshObj->isAttachedToDocument()) {
            return;
        }
        const ExactFemObject exactMesh(MeshObj);

        const std::string FeatName = document->getUniqueObjectName("NodesSet");
        if (openCommand(document, QT_TRANSLATE_NOOP("Command", "Create nodes set"))
            == App::NullTransaction) {
            return;
        }
        const ExactFemObject nodeSet = createFemObject(document, "Fem::FemSetNodesObject", FeatName);
        if (nodeSet.empty()) {
            abortCommand();
            return;
        }
        doCommand(Gui, "%s.FemMesh = %s", nodeSet.c_str(), exactMesh.c_str());
        MeshObj = dynamic_cast<Fem::FemMeshObject*>(exactMesh.get());
        auto* exactSet = dynamic_cast<Fem::FemSetNodesObject*>(nodeSet.get());
        if (!MeshObj || !exactSet || exactSet->FemMesh.getValue() != MeshObj) {
            abortCommand();
            return;
        }
        startFemObjectEditor(this, nodeSet);
    }
    else {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemCreateNodesSet", "Wrong selection"),
            qApp->translate("CmdFemCreateNodesSet", "Select a single FEM mesh or nodes set.")
        );
    }
}

bool CmdFemCreateNodesSet::isActive()
{
    return canStartFemCommand() && hasActiveDocument();
}

//===========================================================================
// start of Erase Elements code
//===========================================================================

DEF_STD_CMD_A(CmdFemDefineElementsSet);

static void DefineElementsCallback(void* ud, SoEventCallback* n)
{
    Fem::FemAnalysis* Analysis;

    if (getConstraintPrerequisits(&Analysis)) {
        return;
    }

    // show the wait cursor because this could take quite some time
    Gui::WaitCursor wc;

    // When this callback function is invoked we must in either case leave the edit mode
    Gui::View3DInventorViewer* view = static_cast<Gui::View3DInventorViewer*>(n->getUserData());
    view->setEditing(false);
    view->removeEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineElementsCallback, ud);
    n->setHandled();

    std::string str = getSelectedNodes(view);
    if (!str.empty()) {
        const ExactFemObject exactAnalysis(Analysis);
        App::Document* document = Analysis->getDocument();
        const int tid = Gui::Command::openActiveDocumentCommand(
            QT_TRANSLATE_NOOP("Command", "Place robot")
        );
        if (tid == App::NullTransaction) {
            return;
        }
        FemTransactionGuard transaction(tid);
        const std::string name = document->getUniqueObjectName("ElementSet");
        const ExactFemObject elementSet
            = createFemObject(document, "Fem::FemSetElementNodesObject", name);
        if (elementSet.empty()) {
            return;
        }
        Gui::Command::doCommand(Gui::Command::Doc, "%s.Nodes = %s", elementSet.c_str(), str.c_str());
        if (elementSet.empty() || !addFemObjectToAnalysis(exactAnalysis, elementSet)) {
            return;
        }

        transaction.commit();
    }
}

CmdFemDefineElementsSet::CmdFemDefineElementsSet()
    : Command("FEM_DefineElementsSet")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Element Set From Polygon");
    sToolTipText = QT_TR_NOOP("Creates a collection of elements selected by a polygon");
    sWhatsThis = "FEM_DefineElementsSet";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_CreateElementsSet";
}

void CmdFemDefineElementsSet::activated(int)
{
    std::vector<App::DocumentObject*> docObj = Gui::Selection().getObjectsOfType(
        Fem::FemMeshObject::getClassTypeId()
    );

    for (std::vector<App::DocumentObject*>::iterator it = docObj.begin(); it != docObj.end(); ++it) {
        if (it == docObj.begin()) {
            Gui::Document* doc = getActiveGuiDocument();
            Gui::MDIView* view = doc->getActiveView();
            if (view->isDerivedFrom<Gui::View3DInventor>()) {
                Gui::View3DInventorViewer* viewer = ((Gui::View3DInventor*)view)->getViewer();
                viewer->setEditing(true);
                viewer->startSelection(Gui::View3DInventorViewer::Clip);
                viewer->addEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineElementsCallback);
            }
            else {
                return;
            }
        }
    }
}

bool CmdFemDefineElementsSet::isActive()
{
    if (!canStartFemCommand()) {
        return false;
    }

    // Check for the selected mesh feature (all Mesh types)
    if (getSelection().countObjectsOfType<Fem::FemMeshObject>() != 1) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }

    return false;
}

//================================================================================================
DEF_STD_CMD_A(CmdFemCreateElementsSet);

CmdFemCreateElementsSet::CmdFemCreateElementsSet()
    : Command("FEM_CreateElementsSet")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Erase Elements");
    sToolTipText = QT_TR_NOOP("Creates a FEM mesh elements set");
    sWhatsThis = "FEM_CreateElementsSet";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_CreateElementsSet";
}

void CmdFemCreateElementsSet::activated(int)
{
    Gui::SelectionFilter ObjectFilter("SELECT Fem::FemSetElementNodesObject COUNT 1");
    Gui::SelectionFilter FemMeshFilter("SELECT Fem::FemMeshObject COUNT 1");

    if (ObjectFilter.match()) {
        Fem::FemSetElementNodesObject* NodesObj = static_cast<Fem::FemSetElementNodesObject*>(
            ObjectFilter.Result[0][0].getObject()
        );
        if (!belongsToActiveFemDocument(NodesObj)) {
            return;
        }
        FemCommandRollbackGuard rollback(this);
        if (openCommand(NodesObj->getDocument(), QT_TRANSLATE_NOOP("Command", "Edit Elements set"))
            == App::NullTransaction) {
            return;
        }
        startFemObjectEditor(this, ExactFemObject(NodesObj));
    }
    // start
    else if (FemMeshFilter.match()) {
        FemCommandRollbackGuard rollback(this);
        Fem::FemMeshObject* MeshObj = static_cast<Fem::FemMeshObject*>(
            FemMeshFilter.Result[0][0].getObject()
        );
        App::Document* document = exactActiveFemDocument();
        if (!document || MeshObj->getDocument() != document || !MeshObj->isAttachedToDocument()) {
            return;
        }
        const ExactFemObject exactMesh(MeshObj);

        std::string elementsName = Fem::FemSetElementNodesObject::getElementName();
        std::string uniqueElementsName = document->getUniqueObjectName(elementsName.c_str());

        if (openCommand(document, QT_TRANSLATE_NOOP("Command", "Create Elements set"))
            == App::NullTransaction) {
            return;
        }
        const ExactFemObject elementSet
            = createFemObject(document, "Fem::FemSetElementNodesObject", uniqueElementsName);
        if (elementSet.empty()) {
            abortCommand();
            return;
        }
        doCommand(Gui, "%s.FemMesh = %s", elementSet.c_str(), exactMesh.c_str());
        MeshObj = dynamic_cast<Fem::FemMeshObject*>(exactMesh.get());
        auto* exactSet = dynamic_cast<Fem::FemSetElementNodesObject*>(elementSet.get());
        if (!MeshObj || !exactSet || exactSet->FemMesh.getValue() != MeshObj) {
            abortCommand();
            return;
        }
        startFemObjectEditor(this, elementSet);
    }
    else {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemCreateElementsSet", "Wrong selection"),
            qApp->translate("CmdFemCreateNodesSet", "Select a single FEM Mesh.")
        );
    }
}

bool CmdFemCreateElementsSet::isActive()
{
    return canStartFemCommand() && hasActiveDocument();
}
//===========================================================================
// end of Erase Elements code
//===========================================================================

//===========================================================================
// FEM_CompEmConstraints (dropdown toolbar button for electromagnetic constraints)
//===========================================================================

DEF_STD_CMD_ACL(CmdFemCompEmConstraints)

CmdFemCompEmConstraints::CmdFemCompEmConstraints()
    : Command("FEM_CompEmConstraints")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Electromagnetic Boundary Conditions");
    sToolTipText = QT_TR_NOOP("Electromagnetic boundary conditions");
    sWhatsThis = "FEM_CompEmConstraints";
    sStatusTip = sToolTipText;
}

void CmdFemCompEmConstraints::activated(int iMsg)
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    if (iMsg == 0) {
        rcCmdMgr.runCommandByName("FEM_ConstraintElectromagnetic");
    }
    else if (iMsg == 1) {
        rcCmdMgr.runCommandByName("FEM_ConstraintCurrentDensity");
    }
    else if (iMsg == 2) {
        rcCmdMgr.runCommandByName("FEM_ConstraintMagnetization");
    }
    else if (iMsg == 3) {
        rcCmdMgr.runCommandByName("FEM_ConstraintElectricChargeDensity");
    }
    else {
        return;
    }

    // Since the default icon is reset when enabling/disabling the command we have
    // to explicitly set the icon of the used command.
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    assert(iMsg < a.size());
    pcAction->setIcon(a[iMsg]->icon());
}

Gui::Action* CmdFemCompEmConstraints::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* cmd0 = pcAction->addAction(QString());
    cmd0->setObjectName(QStringLiteral("FEM_ConstraintElectromagnetic"));
    cmd0->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_ConstraintElectromagnetic"));
    QAction* cmd1 = pcAction->addAction(QString());
    cmd1->setObjectName(QStringLiteral("FEM_ConstraintCurrentDensity"));
    cmd1->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_ConstraintCurrentDensity"));
    QAction* cmd2 = pcAction->addAction(QString());
    cmd2->setObjectName(QStringLiteral("FEM_ConstraintMagnetization"));
    cmd2->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_ConstraintMagnetization"));
    QAction* cmd3 = pcAction->addAction(QString());
    cmd3->setObjectName(QStringLiteral("FEM_ConstraintElectricChargeDensity"));
    cmd3->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_ConstraintElectricChargeDensity"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(cmd0->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdFemCompEmConstraints::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }

    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    Gui::Command* ConstraintElectromagnetic = rcCmdMgr.getCommandByName(
        "FEM_ConstraintElectromagnetic"
    );
    if (ConstraintElectromagnetic) {
        QAction* cmd0 = a[0];
        cmd0->setText(
            QApplication::translate(
                "FEM_ConstraintElectromagnetic",
                ConstraintElectromagnetic->getMenuText()
            )
        );
        cmd0->setToolTip(
            QApplication::translate(
                "FEM_ConstraintElectromagnetic",
                ConstraintElectromagnetic->getToolTipText()
            )
        );
        cmd0->setStatusTip(
            QApplication::translate(
                "FEM_ConstraintElectromagnetic",
                ConstraintElectromagnetic->getStatusTip()
            )
        );
    }

    Gui::Command* ConstraintCurrentDensity = rcCmdMgr.getCommandByName("FEM_ConstraintCurrentDensity");
    if (ConstraintCurrentDensity) {
        QAction* cmd1 = a[1];
        cmd1->setText(
            QApplication::translate(
                "FEM_ConstraintCurrentDensity",
                ConstraintCurrentDensity->getMenuText()
            )
        );
        cmd1->setToolTip(
            QApplication::translate(
                "FEM_ConstraintCurrentDensity",
                ConstraintCurrentDensity->getToolTipText()
            )
        );
        cmd1->setStatusTip(
            QApplication::translate(
                "FEM_ConstraintCurrentDensity",
                ConstraintCurrentDensity->getStatusTip()
            )
        );
    }

    Gui::Command* ConstraintMagnetization = rcCmdMgr.getCommandByName("FEM_ConstraintMagnetization");
    if (ConstraintMagnetization) {
        QAction* cmd2 = a[2];
        cmd2->setText(
            QApplication::translate("FEM_ConstraintMagnetization", ConstraintMagnetization->getMenuText())
        );
        cmd2->setToolTip(
            QApplication::translate(
                "FEM_ConstraintMagnetization",
                ConstraintMagnetization->getToolTipText()
            )
        );
        cmd2->setStatusTip(
            QApplication::translate(
                "FEM_ConstraintMagnetization",
                ConstraintMagnetization->getStatusTip()
            )
        );
    }

    Gui::Command* ConstraintElectricChargeDensity = rcCmdMgr.getCommandByName(
        "FEM_ConstraintElectricChargeDensity"
    );
    if (ConstraintElectricChargeDensity) {
        QAction* cmd3 = a[3];
        cmd3->setText(
            QApplication::translate(
                "FEM_ConstraintElectricChargeDensity",
                ConstraintElectricChargeDensity->getMenuText()
            )
        );
        cmd3->setToolTip(
            QApplication::translate(
                "FEM_ConstraintElectricChargeDensity",
                ConstraintElectricChargeDensity->getToolTipText()
            )
        );
        cmd3->setStatusTip(
            QApplication::translate(
                "FEM_ConstraintElectricChargeDensity",
                ConstraintElectricChargeDensity->getStatusTip()
            )
        );
    }
}

bool CmdFemCompEmConstraints::isActive()
{
    return activeAnalysisInActiveDocument() != nullptr;
}


//===========================================================================
// FEM_CompEmEquations (dropdown toolbar button for electromagnetic equations)
//===========================================================================

DEF_STD_CMD_ACL(CmdFemCompEmEquations)

CmdFemCompEmEquations::CmdFemCompEmEquations()
    : Command("FEM_CompEmEquations")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Electromagnetic Equations");
    sToolTipText = QT_TR_NOOP("Electromagnetic equations for the Elmer solver");
    sWhatsThis = "FEM_CompEmEquations";
    sStatusTip = sToolTipText;
}

void CmdFemCompEmEquations::activated(int iMsg)
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    if (iMsg == 0) {
        rcCmdMgr.runCommandByName("FEM_EquationElectrostatic");
    }
    else if (iMsg == 1) {
        rcCmdMgr.runCommandByName("FEM_EquationElectricforce");
    }
    else if (iMsg == 2) {
        rcCmdMgr.runCommandByName("FEM_EquationMagnetodynamic");
    }
    else if (iMsg == 3) {
        rcCmdMgr.runCommandByName("FEM_EquationMagnetodynamic2D");
    }
    else if (iMsg == 4) {
        rcCmdMgr.runCommandByName("FEM_EquationStaticCurrent");
    }
    else {
        return;
    }

    // Since the default icon is reset when enabling/disabling the command we have
    // to explicitly set the icon of the used command.
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    assert(iMsg < a.size());
    pcAction->setIcon(a[iMsg]->icon());
}

Gui::Action* CmdFemCompEmEquations::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* cmd0 = pcAction->addAction(QString());
    cmd0->setObjectName(QStringLiteral("FEM_EquationElectrostatic"));
    cmd0->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationElectrostatic"));
    QAction* cmd1 = pcAction->addAction(QString());
    cmd1->setObjectName(QStringLiteral("FEM_EquationElectricforce"));
    cmd1->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationElectricforce"));
    QAction* cmd2 = pcAction->addAction(QString());
    cmd2->setObjectName(QStringLiteral("FEM_EquationMagnetodynamic"));
    cmd2->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationMagnetodynamic"));
    QAction* cmd3 = pcAction->addAction(QString());
    cmd3->setObjectName(QStringLiteral("FEM_EquationMagnetodynamic2D"));
    cmd3->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationMagnetodynamic2D"));
    QAction* cmd4 = pcAction->addAction(QString());
    cmd4->setObjectName(QStringLiteral("FEM_EquationStaticCurrent"));
    cmd4->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationStaticCurrent"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(cmd0->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdFemCompEmEquations::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }

    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    Gui::Command* EquationElectrostatic = rcCmdMgr.getCommandByName("FEM_EquationElectrostatic");
    if (EquationElectrostatic) {
        QAction* cmd0 = a[0];
        cmd0->setText(
            QApplication::translate("FEM_EquationElectrostatic", EquationElectrostatic->getMenuText())
        );
        cmd0->setToolTip(
            QApplication::translate("FEM_EquationElectrostatic", EquationElectrostatic->getToolTipText())
        );
        cmd0->setStatusTip(
            QApplication::translate("FEM_EquationElectrostatic", EquationElectrostatic->getStatusTip())
        );
    }

    Gui::Command* EquationElectricforce = rcCmdMgr.getCommandByName("FEM_EquationElectricforce");
    if (EquationElectricforce) {
        QAction* cmd1 = a[1];
        cmd1->setText(
            QApplication::translate("FEM_EquationElectricforce", EquationElectricforce->getMenuText())
        );
        cmd1->setToolTip(
            QApplication::translate("FEM_EquationElectricforce", EquationElectricforce->getToolTipText())
        );
        cmd1->setStatusTip(
            QApplication::translate("FEM_EquationElectricforce", EquationElectricforce->getStatusTip())
        );
    }

    Gui::Command* EquationMagnetodynamic = rcCmdMgr.getCommandByName("FEM_EquationMagnetodynamic");
    if (EquationMagnetodynamic) {
        QAction* cmd2 = a[2];
        cmd2->setText(
            QApplication::translate("FEM_EquationMagnetodynamic", EquationMagnetodynamic->getMenuText())
        );
        cmd2->setToolTip(
            QApplication::translate(
                "FEM_EquationMagnetodynamic",
                EquationMagnetodynamic->getToolTipText()
            )
        );
        cmd2->setStatusTip(
            QApplication::translate("FEM_EquationMagnetodynamic", EquationMagnetodynamic->getStatusTip())
        );
    }

    Gui::Command* EquationMagnetodynamic2D = rcCmdMgr.getCommandByName("FEM_EquationMagnetodynamic2D");
    if (EquationMagnetodynamic2D) {
        QAction* cmd3 = a[3];
        cmd3->setText(
            QApplication::translate(
                "FEM_EquationMagnetodynamic2D",
                EquationMagnetodynamic2D->getMenuText()
            )
        );
        cmd3->setToolTip(
            QApplication::translate(
                "FEM_EquationMagnetodynamic2D",
                EquationMagnetodynamic2D->getToolTipText()
            )
        );
        cmd3->setStatusTip(
            QApplication::translate(
                "FEM_EquationMagnetodynamic2D",
                EquationMagnetodynamic2D->getStatusTip()
            )
        );
    }

    Gui::Command* EquationStaticCurrent = rcCmdMgr.getCommandByName("FEM_EquationStaticCurrent");
    if (EquationStaticCurrent) {
        QAction* cmd4 = a[4];
        cmd4->setText(
            QApplication::translate("FEM_EquationStaticCurrent", EquationStaticCurrent->getMenuText())
        );
        cmd4->setToolTip(
            QApplication::translate("FEM_EquationStaticCurrent", EquationStaticCurrent->getToolTipText())
        );
        cmd4->setStatusTip(
            QApplication::translate("FEM_EquationStaticCurrent", EquationStaticCurrent->getStatusTip())
        );
    }
}

bool CmdFemCompEmEquations::isActive()
{
    if (!activeAnalysisInActiveDocument()) {
        return false;
    }
    Gui::Command* child = Gui::Application::Instance->commandManager().getCommandByName(
        "FEM_EquationElectrostatic"
    );
    return child && child->isActive();
}


//===========================================================================
// FEM_CompMechEquations (dropdown toolbar button for mechanical equations)
//===========================================================================

DEF_STD_CMD_ACL(CmdFemCompMechEquations)

CmdFemCompMechEquations::CmdFemCompMechEquations()
    : Command("FEM_CompMechEquations")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Mechanical Equations");
    sToolTipText = QT_TR_NOOP("Mechanical equations for the Elmer solver");
    sWhatsThis = "FEM_CompMechEquations";
    sStatusTip = sToolTipText;
}

void CmdFemCompMechEquations::activated(int iMsg)
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    if (iMsg == 0) {
        rcCmdMgr.runCommandByName("FEM_EquationElasticity");
    }
    else if (iMsg == 1) {
        rcCmdMgr.runCommandByName("FEM_EquationDeformation");
    }
    else {
        return;
    }

    // Since the default icon is reset when enabling/disabling the command we have
    // to explicitly set the icon of the used command.
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    assert(iMsg < a.size());
    pcAction->setIcon(a[iMsg]->icon());
}

Gui::Action* CmdFemCompMechEquations::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* cmd0 = pcAction->addAction(QString());
    cmd0->setObjectName(QStringLiteral("FEM_EquationElasticity"));
    cmd0->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationElasticity"));
    QAction* cmd1 = pcAction->addAction(QString());
    cmd1->setObjectName(QStringLiteral("FEM_EquationDeformation"));
    cmd1->setIcon(Gui::BitmapFactory().iconFromTheme("FEM_EquationDeformation"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(cmd0->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdFemCompMechEquations::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }

    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    Gui::Command* EquationElasticity = rcCmdMgr.getCommandByName("FEM_EquationElasticity");
    if (EquationElasticity) {
        QAction* cmd1 = a[0];
        cmd1->setText(
            QApplication::translate("FEM_EquationElasticity", EquationElasticity->getMenuText())
        );
        cmd1->setToolTip(
            QApplication::translate("FEM_EquationElasticity", EquationElasticity->getToolTipText())
        );
        cmd1->setStatusTip(
            QApplication::translate("FEM_EquationElasticity", EquationElasticity->getStatusTip())
        );
    }

    Gui::Command* EquationDeformation = rcCmdMgr.getCommandByName("FEM_EquationDeformation");
    if (EquationDeformation) {
        QAction* cmd0 = a[1];
        cmd0->setText(
            QApplication::translate("FEM_EquationDeformation", EquationDeformation->getMenuText())
        );
        cmd0->setToolTip(
            QApplication::translate("FEM_EquationDeformation", EquationDeformation->getToolTipText())
        );
        cmd0->setStatusTip(
            QApplication::translate("FEM_EquationDeformation", EquationDeformation->getStatusTip())
        );
    }
}

bool CmdFemCompMechEquations::isActive()
{
    if (!activeAnalysisInActiveDocument()) {
        return false;
    }
    Gui::Command* child = Gui::Application::Instance->commandManager().getCommandByName(
        "FEM_EquationElasticity"
    );
    return child && child->isActive();
}


//================================================================================================
//================================================================================================
// commands vtk post processing

#ifdef FC_USE_VTK

//================================================================================================
// helper vtk post processing

namespace
{
Fem::FemPostObject* selectedPostObject()
{
    const auto selection = Gui::Selection().getSelection();
    if (selection.size() != 1) {
        return nullptr;
    }
    auto* object = dynamic_cast<Fem::FemPostObject*>(selection.front().pObject);
    return belongsToActiveFemDocument(object) ? object : nullptr;
}

Fem::FemPostPipeline* postPipelineForObject(App::DocumentObject* object)
{
    if (!object || !object->isAttachedToDocument()) {
        return nullptr;
    }
    if (auto* pipeline = dynamic_cast<Fem::FemPostPipeline*>(object)) {
        return pipeline;
    }

    App::Document* document = object->getDocument();
    std::vector<App::DocumentObject*> pending {object};
    std::set<App::DocumentObject*> visited;
    std::set<Fem::FemPostPipeline*> pipelines;
    while (!pending.empty()) {
        App::DocumentObject* current = pending.back();
        pending.pop_back();
        if (!current || current->getDocument() != document || !visited.insert(current).second) {
            continue;
        }
        for (App::DocumentObject* parent : current->getInList()) {
            if (!parent || parent->getDocument() != document) {
                continue;
            }
            if (auto* pipeline = dynamic_cast<Fem::FemPostPipeline*>(parent)) {
                pipelines.insert(pipeline);
            }
            else {
                pending.push_back(parent);
            }
        }
    }
    return pipelines.size() == 1 ? *pipelines.begin() : nullptr;
}

Fem::FemPostPipeline* editedPostFunctionPipeline()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    Gui::Document* guiDocument = Gui::Application::Instance->activeDocument();
    if (!document || !guiDocument || guiDocument->getDocument() != document
        || !Gui::Control().activeDialog() || document->getBookedTransactionID() == App::NullTransaction
        || !document->hasPendingTransaction()) {
        return nullptr;
    }

    auto* editor = dynamic_cast<Gui::ViewProviderDocumentObject*>(guiDocument->getInEdit());
    App::DocumentObject* object = editor ? editor->getObject() : nullptr;
    if (!belongsToActiveFemDocument(object)
        || (!object->isDerivedFrom<Fem::FemPostClipFilter>()
            && !object->isDerivedFrom<Fem::FemPostCutFilter>())) {
        return nullptr;
    }
    return postPipelineForObject(object);
}

Fem::FemPostPipeline* explicitPostPipeline()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    if (!document) {
        return nullptr;
    }

    if (auto* pipeline = editedPostFunctionPipeline()) {
        return pipeline;
    }

    if (Fem::FemPostObject* selected = selectedPostObject()) {
        if (auto* pipeline = postPipelineForObject(selected)) {
            return pipeline;
        }
    }

    const auto pipelines = document->getObjectsOfType<Fem::FemPostPipeline>();
    return pipelines.size() == 1 ? pipelines.front() : nullptr;
}

bool canUsePostFunctionCommand()
{
    if (canStartFemCommand()) {
        return explicitPostPipeline() != nullptr;
    }
    return editedPostFunctionPipeline() != nullptr;
}
}  // namespace

void setupFilter(Gui::Command* cmd, std::string Name)
{
    FemCommandRollbackGuard rollback(cmd);

    // In the isActive() functions it is already assured that the filters are
    // only active on allowed objects
    // For the case the clip filter is set by Python code, we check that the input
    // is a post object and issue an error if not.

    Fem::FemPostObject* selObject = selectedPostObject();
    if (!canStartFemCommand() || !selObject) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("setupFilter", "Select one post-processing object in the active document."),
            qApp->translate("setupFilter", "The filter could not be created.")
        );
        return;
    }

    Fem::FemPostPipeline* pipeline = postPipelineForObject(selObject);
    if (!pipeline) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("setupFilter", "Ambiguous post-processing pipeline"),
            qApp->translate("setupFilter", "Select an object that belongs to exactly one pipeline.")
        );
        return;
    }

    App::Document* document = selObject->getDocument();
    const bool sourceWasVisible = selObject->Visibility.getValue();

    std::string FeatName = document->getUniqueObjectName(Name.c_str());

    // at first we must determine the pipeline of the selection object
    // (which can be a pipeline itself)
    App::DocumentObject* group = nullptr;
    if (selObject->hasExtension(Fem::FemPostGroupExtension::getExtensionClassTypeId())) {
        group = selObject;
    }
    else {
        group = Fem::FemPostGroupExtension::getGroupOfObject(selObject);
        if (!group || group->getDocument() != document || !group->isDerivedFrom<Fem::FemPostObject>()
            || postPipelineForObject(group) != pipeline) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                qApp->translate("setupFilter", "Error: Object not in a post processing group"),
                qApp->translate(
                    "setupFilter",
                    "The filter could not be set up: Object not in a post processing group."
                )
            );
            return;
        }
    }

    // create the object and add it to the pipeline
    const ExactFemObject exactGroup(group);
    const ExactFemObject exactPipeline(pipeline);
    const ExactFemObject exactSource(selObject);

    const int transactionId = cmd->openCommand(document, QT_TRANSLATE_NOOP("Command", "Create filter"));
    if (transactionId == App::NullTransaction) {
        return;
    }
    const std::string filterType = "Fem::FemPost" + Name + "Filter";
    const ExactFemObject exactFilter = createFemObject(document, filterType.c_str(), FeatName);
    if (exactFilter.empty()) {
        cmd->abortCommand();
        return;
    }
    // add it as subobject to the pipeline
    cmd->doCommand(Gui::Command::Doc, "%s.addObject(%s)", exactGroup.c_str(), exactFilter.c_str());

    // set display to assure the user sees the new object
    cmd->doCommand(Gui::Command::Doc, "%s.ViewObject.DisplayMode = \"Surface\"", exactFilter.c_str());
    // Set SelectionStyle to BoundBox because the idea is that the user gets the useful result
    // from the colors. The default would be to highlight the shape but then the colors are changed
    // by every highlighting leading to confusions for the user.
    cmd->doCommand(Gui::Command::Doc, "%s.ViewObject.SelectionStyle = \"BoundBox\"", exactFilter.c_str());

    auto* femFilter = dynamic_cast<Fem::FemPostFilter*>(exactFilter.get());
    App::DocumentObject* exactGroupObject = exactGroup.get();
    auto* exactPipelineObject = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    const auto filterParents = femFilter ? femFilter->getInList()
                                         : std::vector<App::DocumentObject*> {};
    if (!femFilter || !exactGroupObject || !exactPipelineObject
        || std::ranges::find(filterParents, exactGroupObject) == filterParents.end()
        || postPipelineForObject(femFilter) != exactPipelineObject) {
        cmd->abortCommand();
        return;
    }

    auto* exactSourceObject = dynamic_cast<Fem::FemPostObject*>(exactSource.get());
    auto* selObjectView = dynamic_cast<FemGui::ViewProviderFemPostObject*>(
        exactSourceObject ? Gui::Application::Instance->getViewProvider(exactSourceObject) : nullptr
    );
    if (!selObjectView) {
        cmd->abortCommand();
        return;
    }
    // use none field color from base filter
    Base::color_traits<Base::Color> ct {selObjectView->NoneFieldColor.getValue()};
    cmd->doCommand(
        Gui::Command::Doc,
        "%s.ViewObject.NoneFieldColor = (%d, %d, %d)",
        exactFilter.c_str(),
        ct.red(),
        ct.green(),
        ct.blue()
    );
    // TODO: FIX
    /*
    auto selObjectView = static_cast<FemGui::ViewProviderFemPostObject*>(
        Gui::Application::Instance->getViewProvider(selObject));
    cmd->doCommand(Gui::Command::Doc,
                   "App.activeDocument().ActiveObject.ViewObject.Field = \"%s\"",
                   selObjectView->Field.getValueAsString());
    cmd->doCommand(Gui::Command::Doc,
                   "App.activeDocument().ActiveObject.ViewObject.VectorMode = \"%s\"",
                   selObjectView->VectorMode.getValueAsString());
    */

    // hide selected filter
    femFilter = dynamic_cast<Fem::FemPostFilter*>(exactFilter.get());
    if (!femFilter) {
        cmd->abortCommand();
        return;
    }
    const bool hidesSource = !femFilter->isDerivedFrom<Fem::FemPostDataAlongLineFilter>()
        && !femFilter->isDerivedFrom<Fem::FemPostDataAtPointFilter>();
    if (hidesSource) {
        femFilter = dynamic_cast<Fem::FemPostFilter*>(exactFilter.get());
        exactSourceObject = dynamic_cast<Fem::FemPostObject*>(exactSource.get());
        if (!femFilter || !exactSourceObject) {
            cmd->abortCommand();
            return;
        }
        if (sourceWasVisible) {
            markTimelineReplacedInputs(femFilter, {exactSourceObject});
        }
        cmd->doCommand(Gui::Command::Doc, "%s.ViewObject.Visibility = False", exactSource.c_str());
    }

    // show active filter
    cmd->doCommand(Gui::Command::Doc, "%s.ViewObject.Visibility = True", exactFilter.c_str());
    if (exactFilter.empty() || exactSource.empty() || exactPipeline.empty() || exactGroup.empty()) {
        cmd->abortCommand();
        return;
    }

    cmd->updateActive();
    // open the dialog to edit the filter
    startFemObjectEditor(cmd, exactFilter);
}


std::string Plot()
{
    auto xAxisLabel = QCoreApplication::translate(
                          "CmdFemPostLinearizedStressesFilter",
                          "Thickness [mm]",
                          "Plot X-Axis Label"
    )
                          .toStdString();
    auto yAxisLabel = QCoreApplication::translate(
                          "CmdFemPostLinearizedStressesFilter",
                          "Stress [MPa]",
                          "Plot Y-Axis Label"
    )
                          .toStdString();
    auto titleLabel = QCoreApplication::translate(
                          "CmdFemPostLinearizedStressesFilter",
                          "Linearized Stresses",
                          "Plot title"
    )
                          .toStdString();
    auto legendEntryA = QCoreApplication::translate(
                            "CmdFemPostLinearizedStressesFilter",
                            "Membrane",
                            "Plot legend item label"
    )
                            .toStdString();
    auto legendEntryB = QCoreApplication::translate(
                            "CmdFemPostLinearizedStressesFilter",
                            "Membrane and Bending",
                            "Plot legend item label"
    )
                            .toStdString();
    auto legendEntryC = QCoreApplication::translate(
                            "CmdFemPostLinearizedStressesFilter",
                            "Total",
                            "Plot legend item label"
    )
                            .toStdString();

    std::ostringstream oss;
    oss << "t=t_coords[len(t_coords)-1]\n\
for i in range(len(t_coords)):\n\
    dum = t_coords[i]\n\
    t_coords[i] = dum - t_coords[len(t_coords)-1]*0.5\n\
m = 0\n\
for i in range(len(sValues)-1):\n\
    m = m +(t_coords[i+1] - t_coords[i])*(sValues[i+1]+sValues[i])\n\
m = (1/t)*0.5*m\n\
membrane = []\n\
for i in range(len(sValues)):\n\
    membrane.append(m)\n\
b = 0\n\
for i in range(len(sValues)-1):\n\
    d = (t_coords[i+1] - t_coords[i])\n\
    b = b + d*(-3/t**2)*(sValues[i+1]*t_coords[i+1] + sValues[i]*t_coords[i])\n\
b2 = -b\n\
bending =[]\n\
for i in range(len(t_coords)):\n\
    func = ((b2-b)/t)*t_coords[i]\n\
    bending.append(func)\n\
peak = []\n\
mb = []\n\
for i in range(len(sValues)):\n\
    peak.append(sValues[i])\n\
    mb.append(bending[i] + membrane[0])\n\
import FreeCAD\n\
from PySide import QtCore\n\
import numpy as np\n\
from matplotlib import pyplot as plt\n\
plt.figure(\""
        << titleLabel << "\")\n\
plt.plot(t_coords, membrane, \"k--\")\n\
plt.plot(t_coords, mb, \"b*-\")\n\
plt.plot(t_coords, peak, \"r-x\")\n\
plt.annotate(str(round(membrane[0],2)), xy=(t_coords[0], membrane[0]), xytext=(t_coords[0], membrane[0]))\n\
plt.annotate(str(round(mb[0],2)), xy=(t_coords[0], mb[0]), xytext=(t_coords[0], mb[0]))\n\
plt.annotate(str(round(mb[len(t_coords)-1],2)), xy=(t_coords[len(t_coords)-1], mb[len(t_coords)-1]), xytext=(t_coords[len(t_coords)-1], mb[len(t_coords)-1]))\n\
plt.annotate(str(round(peak[0],2)), xy=(t_coords[0], peak[0]), xytext=(t_coords[0], peak[0]))\n\
plt.annotate(str(round(peak[len(t_coords)-1],2)), xy=(t_coords[len(t_coords)-1], peak[len(t_coords)-1]), xytext=(t_coords[len(t_coords)-1], peak[len(t_coords)-1]))\n\
FreeCAD.Console.PrintError('membrane stress = ')\n\
FreeCAD.Console.PrintError([str(round(membrane[0],2))])\n\
FreeCAD.Console.PrintError('membrane + bending min = ')\n\
FreeCAD.Console.PrintError([str(round(mb[0],2))])\n\
FreeCAD.Console.PrintError('membrane + bending  max = ')\n\
FreeCAD.Console.PrintError([str(round(mb[len(t_coords)-1],2))])\n\
FreeCAD.Console.PrintError('Total stress min = ')\n\
FreeCAD.Console.PrintError([str(round(peak[0],2))])\n\
FreeCAD.Console.PrintError('Total stress max = ')\n\
FreeCAD.Console.PrintError([str(round(peak[len(t_coords)-1],2))])\n\
plt.ioff()\n\
plt.legend([\""
        << legendEntryA << "\", \"" << legendEntryB << "\", \"" << legendEntryC
        << "\"], loc = \"best\")\n\
plt.xlabel(\""
        << xAxisLabel << "\")\n\
plt.ylabel(\""
        << yAxisLabel << "\")\n\
plt.title(\""
        << titleLabel << "\")\n\
plt.grid()\n\
fig_manager = plt.get_current_fig_manager()\n\
fig_manager.window.setParent(FreeCADGui.getMainWindow())\n\
fig_manager.window.setWindowFlag(QtCore.Qt.Tool)\n\
plt.show()\n";
    return oss.str();
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostClipFilter)

CmdFemPostClipFilter::CmdFemPostClipFilter()
    : Command("FEM_PostFilterClipRegion")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Region Clip Filter");
    sToolTipText = QT_TR_NOOP(
        "Defines a clip filter which uses functions to define the clipped region"
    );
    sWhatsThis = "FEM_PostFilterClipRegion";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterClipRegion";
}

void CmdFemPostClipFilter::activated(int)
{
    setupFilter(this, "Clip");
}

bool CmdFemPostClipFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostCutFilter)

CmdFemPostCutFilter::CmdFemPostCutFilter()
    : Command("FEM_PostFilterCutFunction")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Function Cut Filter");
    sToolTipText = QT_TR_NOOP("Cuts the data along an implicit function");
    sWhatsThis = "FEM_PostFilterCutFunction";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterCutFunction";
}

void CmdFemPostCutFilter::activated(int)
{
    setupFilter(this, "Cut");
}

bool CmdFemPostCutFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostDataAlongLineFilter)

CmdFemPostDataAlongLineFilter::CmdFemPostDataAlongLineFilter()
    : Command("FEM_PostFilterDataAlongLine")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Line Clip Filter");
    sToolTipText = QT_TR_NOOP("Defines a clip filter which clips a field along a line");
    sWhatsThis = "FEM_PostFilterDataAlongLine";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterDataAlongLine";
}

void CmdFemPostDataAlongLineFilter::activated(int)
{
    setupFilter(this, "DataAlongLine");
}

bool CmdFemPostDataAlongLineFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostDataAtPointFilter)

CmdFemPostDataAtPointFilter::CmdFemPostDataAtPointFilter()
    : Command("FEM_PostFilterDataAtPoint")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Data at Point Clip Filter");
    sToolTipText = QT_TR_NOOP("Defines a clip filter which clips a field data at point");
    sWhatsThis = "FEM_PostFilterDataAtPoint";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterDataAtPoint";
}

void CmdFemPostDataAtPointFilter::activated(int)
{

    setupFilter(this, "DataAtPoint");
}

bool CmdFemPostDataAtPointFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
namespace
{
Fem::FemPostDataAlongLineFilter* selectedStressLineFilter()
{
    Gui::SelectionFilter filter("SELECT Fem::FemPostDataAlongLineFilter COUNT 1");
    if (!filter.match()) {
        return nullptr;
    }
    auto* selected = dynamic_cast<Fem::FemPostDataAlongLineFilter*>(filter.Result[0][0].getObject());
    return belongsToActiveFemDocument(selected) ? selected : nullptr;
}

bool isStressField(const std::string& fieldName)
{
    // These names are the stress arrays produced by FemVTKTools.cpp.
    return fieldName == "Tresca Stress" || fieldName == "von Mises Stress"
        || fieldName == "Major Principal Stress" || fieldName == "Intermediate Principal Stress"
        || fieldName == "Minor Principal Stress" || fieldName == "Stress xx component"
        || fieldName == "Stress xy component" || fieldName == "Stress xz component"
        || fieldName == "Stress yy component" || fieldName == "Stress yz component"
        || fieldName == "Stress zz component";
}
}  // namespace

DEF_STD_CMD_A(CmdFemPostLinearizedStressesFilter)

CmdFemPostLinearizedStressesFilter::CmdFemPostLinearizedStressesFilter()
    : Command("FEM_PostFilterLinearizedStresses")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Stress Linearization Plot");
    sToolTipText = QT_TR_NOOP("Defines a stress linearization plot");
    sWhatsThis = "FEM_PostFilterLinearizedStresses";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterLinearizedStresses";
}

void CmdFemPostLinearizedStressesFilter::activated(int)
{
    Fem::FemPostDataAlongLineFilter* dataAlongLine = selectedStressLineFilter();
    if (!dataAlongLine || !isStressField(dataAlongLine->PlotData.getValue())) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemPostLinearizedStressesFilter", "Wrong selection"),
            qApp->translate(
                "CmdFemPostLinearizedStressesFilter",
                "Select a clip filter which clips a stress field along a line"
            )
        );
        return;
    }

    App::DocumentObjectT object(dataAlongLine);
    const std::string objectName = object.getObjectPython();
    Gui::doCommandT(Gui::Command::Doc, "t_coords = %s.XAxisData", objectName);
    Gui::doCommandT(Gui::Command::Doc, "sValues = %s.YAxisData", objectName);
    Gui::doCommandT(Gui::Command::Doc, Plot().c_str());
}

bool CmdFemPostLinearizedStressesFilter::isActive()
{
    Fem::FemPostDataAlongLineFilter* dataAlongLine = selectedStressLineFilter();
    return canStartFemCommand() && dataAlongLine && isStressField(dataAlongLine->PlotData.getValue());
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostScalarClipFilter)

CmdFemPostScalarClipFilter::CmdFemPostScalarClipFilter()
    : Command("FEM_PostFilterClipScalar")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Scalar Clip Filter");
    sToolTipText = QT_TR_NOOP("Defines a clip filter which clips a field with a scalar value");
    sWhatsThis = "FEM_PostFilterClipScalar";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterClipScalar";
}

void CmdFemPostScalarClipFilter::activated(int)
{
    setupFilter(this, "ScalarClip");
}

bool CmdFemPostScalarClipFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostWarpVectorFilter)

CmdFemPostWarpVectorFilter::CmdFemPostWarpVectorFilter()
    : Command("FEM_PostFilterWarp")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Warp Filter");
    sToolTipText = QT_TR_NOOP("Warps the geometry along a vector field by a certain factor");
    sWhatsThis = "FEM_PostFilterWarp";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterWarp";
}

void CmdFemPostWarpVectorFilter::activated(int)
{
    setupFilter(this, "WarpVector");
}

bool CmdFemPostWarpVectorFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostContoursFilter)

CmdFemPostContoursFilter::CmdFemPostContoursFilter()
    : Command("FEM_PostFilterContours")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Contours Filter");
    sToolTipText = QT_TR_NOOP("Defines a contours filter that displays iso contours");
    sWhatsThis = "FEM_PostFilterContours";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterContours";
}

void CmdFemPostContoursFilter::activated(int)
{
    setupFilter(this, "Contours");
}

bool CmdFemPostContoursFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostCalculatorFilter)

CmdFemPostCalculatorFilter::CmdFemPostCalculatorFilter()
    : Command("FEM_PostFilterCalculator")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Calculator Filter");
    sToolTipText = QT_TR_NOOP("Creates a new field from current data");
    sWhatsThis = "FEM_PostFilterCalculator";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostFilterCalculator";
}

void CmdFemPostCalculatorFilter::activated(int)
{
    setupFilter(this, "Calculator");
}

bool CmdFemPostCalculatorFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}


//================================================================================================
DEF_STD_CMD_ACL(CmdFemPostFunctions)

CmdFemPostFunctions::CmdFemPostFunctions()
    : Command("FEM_PostCreateFunctions")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Filter Functions");
    sToolTipText = QT_TR_NOOP("Functions for use in postprocessing filter");
    sWhatsThis = "FEM_PostCreateFunctions";
    sStatusTip = sToolTipText;
    eType = eType | ForEdit;
}

void CmdFemPostFunctions::activated(int iMsg)
{
    FemCommandRollbackGuard rollback(this);

    std::string name;
    if (iMsg == 0) {
        name = "Plane";
    }
    else if (iMsg == 1) {
        name = "Sphere";
    }
    else if (iMsg == 2) {
        name = "Cylinder";
    }
    else if (iMsg == 3) {
        name = "Box";
    }
    else {
        return;
    }

    Fem::FemPostPipeline* pipeline = explicitPostPipeline();
    if (!canUsePostFunctionCommand() || !pipeline) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemPostFunctions", "Select a post-processing pipeline"),
            qApp->translate(
                "CmdFemPostFunctions",
                "When multiple pipelines exist, select the pipeline "
                "that should own the function."
            )
        );
        return;
    }

    App::Document* document = pipeline->getDocument();
    Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
    const bool nestedEdit = guiDocument && guiDocument->getInEdit();
    int transactionId = App::NullTransaction;
    if (!nestedEdit) {
        transactionId = openCommand(document, QT_TRANSLATE_NOOP("Command", "Create function"));
        if (transactionId == App::NullTransaction) {
            return;
        }
    }

    const ExactFemObject exactPipeline(pipeline);

    // Check if the exact pipeline has a function provider and add one only
    // to that pipeline when needed.
    Fem::FemPostFunctionProvider* provider = pipeline->getFunctionProvider();
    ExactFemObject exactProvider(provider);
    bool providerCreated = false;
    if (!provider) {
        const std::string functionGroupName = document->getUniqueObjectName("Functions");
        exactProvider = createFemObject(document, "Fem::FemPostFunctionProvider", functionGroupName);
        if (exactProvider.empty()) {
            if (transactionId != App::NullTransaction) {
                abortCommand();
            }
            return;
        }
        doCommand(Doc, "%s.addObject(%s)", exactPipeline.c_str(), exactProvider.c_str());
        provider = dynamic_cast<Fem::FemPostFunctionProvider*>(exactProvider.get());
        pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
        providerCreated = provider != nullptr;
    }
    if (!provider || !pipeline) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else if (providerCreated) {
            removeExactFemObject(exactProvider);
        }
        return;
    }
    const auto& pipelineObjects = pipeline->Group.getValues();
    const auto providerCount
        = std::ranges::count_if(pipelineObjects, [](const App::DocumentObject* object) {
              return object && object->isDerivedFrom<Fem::FemPostFunctionProvider>();
          });
    if (providerCount != 1 || pipeline->getFunctionProvider() != provider
        || postPipelineForObject(provider) != pipeline) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else if (providerCreated) {
            removeExactFemObject(exactProvider);
        }
        return;
    }

    const std::string featureName = document->getUniqueObjectName(name.c_str());
    const std::string featureType = "Fem::FemPost" + name + "Function";
    const ExactFemObject exactFeature = createFemObject(document, featureType.c_str(), featureName);
    if (exactFeature.empty()) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else if (providerCreated) {
            removeExactFemObject(exactProvider);
        }
        return;
    }
    doCommand(Doc, "%s.addObject(%s)", exactProvider.c_str(), exactFeature.c_str());

    pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    provider = dynamic_cast<Fem::FemPostFunctionProvider*>(exactProvider.get());
    if (!pipeline || !provider) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else {
            removeExactFemObject(exactFeature);
            if (providerCreated) {
                removeExactFemObject(exactProvider);
            }
        }
        return;
    }
    vtkBoundingBox box = pipeline->getBoundingBox();

    // A pipeline without output data has an invalid VTK bounding box.  VTK
    // represents that state with non-finite limits, and serializing those
    // values with "%f" produces the bare Python token `inf`.  Function
    // objects are still useful before data is connected, so give every
    // function a finite, editable one-unit starting frame instead of failing
    // halfway through object creation.
    double center[3] = {0.0, 0.0, 0.0};
    double lengths[3] = {1.0, 1.0, 1.0};
    double diagonal = 1.0;
    if (box.IsValid()) {
        box.GetCenter(center);
        for (int axis = 0; axis < 3; ++axis) {
            if (!std::isfinite(center[axis])) {
                center[axis] = 0.0;
            }
            const double length = box.GetLength(axis);
            if (std::isfinite(length) && length > 0.0) {
                lengths[axis] = length;
            }
        }
        const double boxDiagonal = box.GetDiagonalLength();
        if (std::isfinite(boxDiagonal) && boxDiagonal > 0.0) {
            diagonal = boxDiagonal;
        }
    }

    App::DocumentObject* feature = exactFeature.get();
    const auto featureParents = feature ? feature->getInList() : std::vector<App::DocumentObject*> {};
    if (!feature || std::ranges::find(featureParents, provider) == featureParents.end()
        || postPipelineForObject(feature) != pipeline) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else {
            removeExactFemObject(exactFeature);
            if (providerCreated) {
                removeExactFemObject(exactProvider);
            }
        }
        return;
    }
    if (iMsg == 0) {
        doCommand(
            Doc,
            "%s.PlaneOrigin = App.Vector(%f, %f, %f)",
            exactFeature.c_str(),
            center[0],
            center[1],
            center[2]
        );
        doCommand(Gui, "%s.ViewObject.Scale = %f", exactFeature.c_str(), diagonal);
    }
    else if (iMsg == 1) {
        doCommand(
            Doc,
            "%s.SphereCenter = App.Vector(%f, %f, %f)",
            exactFeature.c_str(),
            center[0],
            center[1] + lengths[1] / 2,
            center[2] + lengths[2] / 2
        );
        doCommand(Doc, "%s.SphereRadius = %f", exactFeature.c_str(), diagonal / 2);
    }
    else if (iMsg == 2) {
        doCommand(
            Doc,
            "%s.CylinderCenter = App.Vector(%f, %f, %f)",
            exactFeature.c_str(),
            center[0],
            center[1] + lengths[1] / 2,
            center[2]
        );
        doCommand(Doc, "%s.CylinderRadius = %f", exactFeature.c_str(), diagonal / 3.6);
    }
    else if (iMsg == 3) {
        doCommand(
            Doc,
            "%s.BoxCenter = App.Vector(%f, %f, %f)",
            exactFeature.c_str(),
            center[0] + lengths[0] / 2,
            center[1] + lengths[1] / 2,
            center[2]
        );
        doCommand(Doc, "%s.BoxLength = %f", exactFeature.c_str(), lengths[0]);
        doCommand(Doc, "%s.BoxWidth = %f", exactFeature.c_str(), lengths[1]);
        doCommand(Doc, "%s.BoxHeight = %f", exactFeature.c_str(), 1.1 * lengths[2]);
    }

    if (exactFeature.empty() || exactProvider.empty() || exactPipeline.empty()) {
        if (transactionId != App::NullTransaction) {
            abortCommand();
        }
        else {
            removeExactFemObject(exactFeature);
            if (providerCreated) {
                removeExactFemObject(exactProvider);
            }
        }
        return;
    }

    this->updateActive();
    if (!nestedEdit) {
        if (!startFemObjectEditor(this, exactFeature)) {
            return;
        }
    }

    // Since the default icon is reset when enabling/disabling the command we have
    // to explicitly set the icon of the used command.
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    assert(iMsg < a.size());
    pcAction->setIcon(a[iMsg]->icon());
}

Gui::Action* CmdFemPostFunctions::createAction()
{
    Gui::ActionGroup* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    applyCommandData(this->className(), pcAction);

    QAction* cmd0 = pcAction->addAction(QString());
    cmd0->setObjectName(QStringLiteral("FEM_PostCreateFunctionPlane"));
    cmd0->setIcon(Gui::BitmapFactory().iconFromTheme("fem-post-geo-plane"));

    QAction* cmd1 = pcAction->addAction(QString());
    cmd1->setObjectName(QStringLiteral("FEM_PostCreateFunctionSphere"));
    cmd1->setIcon(Gui::BitmapFactory().iconFromTheme("fem-post-geo-sphere"));

    QAction* cmd2 = pcAction->addAction(QString());
    cmd2->setObjectName(QStringLiteral("FEM_PostCreateFunctionCylinder"));
    cmd2->setIcon(Gui::BitmapFactory().iconFromTheme("fem-post-geo-cylinder"));

    QAction* cmd3 = pcAction->addAction(QString());
    cmd3->setObjectName(QStringLiteral("FEM_PostCreateFunctionBox"));
    cmd3->setIcon(Gui::BitmapFactory().iconFromTheme("fem-post-geo-box"));

    _pcAction = pcAction;
    languageChange();

    pcAction->setIcon(cmd1->icon());
    int defaultId = 0;
    pcAction->setProperty("defaultAction", QVariant(defaultId));

    return pcAction;
}

void CmdFemPostFunctions::languageChange()
{
    Command::languageChange();

    if (!_pcAction) {
        return;
    }
    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    QAction* cmd = a[0];
    cmd->setText(QApplication::translate("CmdFemPostFunctions", "Plane"));
    cmd->setToolTip(
        QApplication::translate(
            "FEM_PostCreateFunctions",
            "Create a plane function, defined by its origin and normal"
        )
    );
    cmd->setStatusTip(cmd->toolTip());

    cmd = a[1];
    cmd->setText(QApplication::translate("CmdFemPostFunctions", "Sphere"));
    cmd->setToolTip(
        QApplication::translate(
            "FEM_PostCreateFunctions",
            "Create a sphere function, defined by its center and radius"
        )
    );
    cmd->setStatusTip(cmd->toolTip());

    cmd = a[2];
    cmd->setText(QApplication::translate("CmdFemPostFunctions", "Cylinder"));
    cmd->setToolTip(
        QApplication::translate(
            "FEM_PostCreateFunctions",
            "Create a cylinder function, defined by its center, axis and radius"
        )
    );
    cmd->setStatusTip(cmd->toolTip());

    cmd = a[3];
    cmd->setText(QApplication::translate("CmdFemPostFunctions", "Box"));
    cmd->setToolTip(
        QApplication::translate(
            "FEM_PostCreateFunctions",
            "Create a box function, defined by its center, length, width and height"
        )
    );
    cmd->setStatusTip(cmd->toolTip());
}

bool CmdFemPostFunctions::isActive()
{
    return canUsePostFunctionCommand();
}


//================================================================================================
DEF_STD_CMD_AC(CmdFemPostApllyChanges)

CmdFemPostApllyChanges::CmdFemPostApllyChanges()
    : Command("FEM_PostApplyChanges")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Apply Changes to Pipeline");
    sToolTipText = QT_TR_NOOP("Applies changes to parameters directly and not on recompute only");
    sWhatsThis = "FEM_PostApplyChanges";
    sStatusTip = sToolTipText;
    sPixmap = "view-refresh";
    eType = eType | ForEdit;
}

void CmdFemPostApllyChanges::activated(int iMsg)
{
    FemGui::FemSettings().setPostAutoRecompute(iMsg == 1);
}

bool CmdFemPostApllyChanges::isActive()
{
    // This checkable action changes only the post-processing preference.  It
    // is intentionally available while a post task owns the document
    // transaction; that is the context in which the setting is useful.
    return exactActiveFemDocument() != nullptr && getActiveGuiDocument();
}

Gui::Action* CmdFemPostApllyChanges::createAction()
{
    Gui::Action* pcAction = Command::createAction();
    pcAction->setCheckable(true);
    pcAction->setChecked(FemGui::FemSettings().getPostAutoRecompute());

    return pcAction;
}


//================================================================================================
DEF_STD_CMD_A(CmdFemPostPipelineFromResult)

CmdFemPostPipelineFromResult::CmdFemPostPipelineFromResult()
    : Command("FEM_PostPipelineFromResult")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Post Pipeline From Result");
    sToolTipText = QT_TR_NOOP("Creates a post processing pipeline from a result object");
    sWhatsThis = "FEM_PostPipelineFromResult";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostPipelineFromResult";
}

void CmdFemPostPipelineFromResult::activated(int)
{
    FemCommandRollbackGuard rollback(this);

    const auto selection = getSelection().getSelection();
    auto* result = selection.size() == 1
        ? dynamic_cast<Fem::FemResultObject*>(selection.front().pObject)
        : nullptr;
    if (!canStartFemCommand() || !belongsToActiveFemDocument(result)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemPostPipelineFromResult", "Wrong selection type"),
            qApp->translate("CmdFemPostPipelineFromResult", "Select a result object.")
        );
        return;
    }

    App::Document* document = result->getDocument();
    const bool resultWasVisible = result->Visibility.getValue();
    std::set<Fem::FemAnalysis*> parentAnalyses;
    for (App::DocumentObject* parent : result->getInList()) {
        if (auto* analysis = dynamic_cast<Fem::FemAnalysis*>(parent);
            analysis && analysis->getDocument() == document) {
            parentAnalyses.insert(analysis);
        }
    }
    if (parentAnalyses.size() > 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("CmdFemPostPipelineFromResult", "Ambiguous analysis"),
            qApp->translate(
                "CmdFemPostPipelineFromResult",
                "The selected result belongs to more than one analysis."
            )
        );
        return;
    }

    const ExactFemObject exactResult(result);
    ExactFemObject exactAnalysis;
    if (!parentAnalyses.empty()) {
        exactAnalysis = ExactFemObject(*parentAnalyses.begin());
    }
    const std::string featureName = document->getUniqueObjectName("ResultPipeline");
    const int transactionId
        = openCommand(document, QT_TRANSLATE_NOOP("Command", "Create pipeline from result"));
    if (transactionId == App::NullTransaction) {
        return;
    }

    const ExactFemObject exactPipeline = createFemObject(document, "Fem::FemPostPipeline", featureName);
    auto* pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
    Gui::ViewProvider* pipelineView = pipeline
        ? Gui::Application::Instance->getViewProvider(pipeline)
        : nullptr;
    if (!pipeline || !guiDocument || !pipelineView) {
        abortCommand();
        return;
    }
    if (!parentAnalyses.empty()) {
        auto* analysis = dynamic_cast<Fem::FemAnalysis*>(exactAnalysis.get());
        if (!analysis) {
            abortCommand();
            return;
        }
        doCommand(Doc, "%s.addObject(%s)", exactAnalysis.c_str(), exactPipeline.c_str());
        pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
        analysis = dynamic_cast<Fem::FemAnalysis*>(exactAnalysis.get());
        if (!pipeline || !analysis) {
            abortCommand();
            return;
        }
        const auto pipelineParents = pipeline->getInList();
        if (std::ranges::find(pipelineParents, analysis) == pipelineParents.end()) {
            abortCommand();
            return;
        }
    }
    pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    if (!pipeline) {
        abortCommand();
        return;
    }
    const auto pipelineParents = pipeline->getInList();
    const auto analysisParentCount
        = std::ranges::count_if(pipelineParents, [](const App::DocumentObject* parent) {
              return parent && parent->isDerivedFrom<Fem::FemAnalysis>();
          });
    if (analysisParentCount != (parentAnalyses.empty() ? 0 : 1)) {
        abortCommand();
        return;
    }

    doCommand(Doc, "%s.load(%s)", exactPipeline.c_str(), exactResult.c_str());
    doCommand(Gui, "%s.ViewObject.DisplayMode = \"Surface\"", exactPipeline.c_str());
    doCommand(Gui, "%s.ViewObject.SelectionStyle = \"BoundBox\"", exactPipeline.c_str());
    pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    result = dynamic_cast<Fem::FemResultObject*>(exactResult.get());
    if (!pipeline || !result || (!parentAnalyses.empty() && exactAnalysis.empty())) {
        abortCommand();
        return;
    }
    if (resultWasVisible) {
        markTimelineReplacedInputs(pipeline, {result});
    }
    pipeline = dynamic_cast<Fem::FemPostPipeline*>(exactPipeline.get());
    result = dynamic_cast<Fem::FemResultObject*>(exactResult.get());
    if (!pipeline || !result) {
        abortCommand();
        return;
    }
    for (App::DocumentObject* object : document->getObjects()) {
        if (object == pipeline) {
            continue;
        }
        if (Gui::ViewProvider* view = Gui::Application::Instance->getViewProvider(object)) {
            view->hide();
        }
    }
    pipelineView = Gui::Application::Instance->getViewProvider(exactPipeline.get());
    if (!pipelineView) {
        abortCommand();
        return;
    }
    pipelineView->show();
    if (exactPipeline.empty() || exactResult.empty()
        || (!parentAnalyses.empty() && exactAnalysis.empty())) {
        abortCommand();
        return;
    }
    this->updateActive();
    commitCommand();
}

bool CmdFemPostPipelineFromResult::isActive()
{
    if (!canStartFemCommand()) {
        return false;
    }

    // only activate if a result object is selected from which the pipeline can be loaded
    const auto selection = getSelection().getSelection();
    auto* result = selection.size() == 1
        ? dynamic_cast<Fem::FemResultObject*>(selection.front().pObject)
        : nullptr;
    return belongsToActiveFemDocument(result);
}

//================================================================================================
DEF_STD_CMD_A(CmdFemPostBranchFilter)

CmdFemPostBranchFilter::CmdFemPostBranchFilter()
    : Command("FEM_PostBranchFilter")
{
    sAppModule = "Fem";
    sGroup = QT_TR_NOOP("Fem");
    sMenuText = QT_TR_NOOP("Pipeline Branch");
    sToolTipText = QT_TR_NOOP("Branches the pipeline into a new path");
    sWhatsThis = "FEM_PostBranchFilter";
    sStatusTip = sToolTipText;
    sPixmap = "FEM_PostBranchFilter";
}

void CmdFemPostBranchFilter::activated(int)
{
    setupFilter(this, "Branch");
}

bool CmdFemPostBranchFilter::isActive()
{
    Fem::FemPostObject* selected = selectedPostObject();
    return canStartFemCommand() && selected && postPipelineForObject(selected);
}

#endif


//================================================================================================
//================================================================================================
void CreateFemCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    // part, analysis, solver
    // rcCmdMgr.addCommand(new CmdFemAddPart()); // not implemented as GUI menu or click icon
    // rcCmdMgr.addCommand(new CmdFemCreateAnalysis()); // Analysis is created in python
    // rcCmdMgr.addCommand(new CmdFemCreateSolver());  // Solver will be extended and created
    // in python

    // constraints
    rcCmdMgr.addCommand(new CmdFemConstraintBearing());
    rcCmdMgr.addCommand(new CmdFemConstraintContact());
    rcCmdMgr.addCommand(new CmdFemConstraintDisplacement());
    rcCmdMgr.addCommand(new CmdFemConstraintFixed());
    rcCmdMgr.addCommand(new CmdFemConstraintRigidBody());
    rcCmdMgr.addCommand(new CmdFemConstraintFluidBoundary());
    rcCmdMgr.addCommand(new CmdFemConstraintForce());
    rcCmdMgr.addCommand(new CmdFemConstraintGear());
    rcCmdMgr.addCommand(new CmdFemConstraintHeatflux());
    rcCmdMgr.addCommand(new CmdFemConstraintInitialTemperature());
    rcCmdMgr.addCommand(new CmdFemConstraintPlaneRotation());
    rcCmdMgr.addCommand(new CmdFemConstraintPressure());
    rcCmdMgr.addCommand(new CmdFemConstraintPulley());
    rcCmdMgr.addCommand(new CmdFemConstraintTemperature());
    rcCmdMgr.addCommand(new CmdFemConstraintTransform());
    rcCmdMgr.addCommand(new CmdFemConstraintSpring());
    rcCmdMgr.addCommand(new CmdFemCompEmConstraints());
    rcCmdMgr.addCommand(new CmdFemCompMechEquations());

    // mesh
    rcCmdMgr.addCommand(new CmdFemCreateNodesSet());
    rcCmdMgr.addCommand(new CmdFemDefineNodesSet());
    rcCmdMgr.addCommand(new CmdFemCreateElementsSet());
    rcCmdMgr.addCommand(new CmdFemDefineElementsSet());

    // equations
    rcCmdMgr.addCommand(new CmdFemCompEmEquations());

    // vtk post processing
#ifdef FC_USE_VTK
    rcCmdMgr.addCommand(new CmdFemPostApllyChanges);
    rcCmdMgr.addCommand(new CmdFemPostCalculatorFilter);
    rcCmdMgr.addCommand(new CmdFemPostClipFilter);
    rcCmdMgr.addCommand(new CmdFemPostContoursFilter);
    rcCmdMgr.addCommand(new CmdFemPostCutFilter);
    rcCmdMgr.addCommand(new CmdFemPostDataAlongLineFilter);
    rcCmdMgr.addCommand(new CmdFemPostDataAtPointFilter);
    rcCmdMgr.addCommand(new CmdFemPostLinearizedStressesFilter);
    rcCmdMgr.addCommand(new CmdFemPostFunctions);
    rcCmdMgr.addCommand(new CmdFemPostPipelineFromResult);
    rcCmdMgr.addCommand(new CmdFemPostBranchFilter);
    rcCmdMgr.addCommand(new CmdFemPostScalarClipFilter);
    rcCmdMgr.addCommand(new CmdFemPostWarpVectorFilter);
#endif
}
