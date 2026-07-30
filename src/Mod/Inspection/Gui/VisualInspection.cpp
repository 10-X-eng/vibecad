// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <string_view>
#include <unordered_set>

#include <QByteArray>
#include <QVariant>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Macro.h>
#include <Gui/MainWindow.h>
#include <Gui/PrefWidgets.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Mod/Inspection/App/InspectionFeature.h>
#include <Mod/Inspection/App/InspectionSource.h>
#include <Mod/Part/App/BodyBase.h>

#include "VisualInspection.h"
#include "ui_VisualInspection.h"

using namespace InspectionGui;

namespace
{
constexpr int ObjectNameRole = Qt::UserRole;
constexpr int ObjectIdRole = Qt::UserRole + 1;

App::Property* ensureTimelineProperty(
    App::DocumentObject& object,
    const char* type,
    const char* name,
    const char* description
)
{
    auto* property = object.getPropertyByName(name);
    if (!property) {
        property = object.addDynamicProperty(
            type,
            name,
            "Timeline",
            description,
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
    return property;
}

void markTimelineReplacedInputs(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& inputs
)
{
    if (inputs.empty()) {
        return;
    }
    auto* document = operation.getDocument();
    if (!document || !document->containsObject(&operation)) {
        throw Base::ValueError("A Visual Inspection operation must be live in its document");
    }

    std::vector<App::DocumentObject*> exactInputs;
    exactInputs.reserve(inputs.size());
    for (auto* input : inputs) {
        if (!input || input == &operation || input->getDocument() != document
            || !document->containsObject(input)) {
            throw Base::ValueError("A Visual Inspection replaced input must be a distinct live "
                                   "object in the operation document");
        }
        if (std::ranges::find(exactInputs, input) == exactInputs.end()) {
            exactInputs.push_back(input);
        }
    }

    auto* replacedInputs = dynamic_cast<App::PropertyLinkListHidden*>(ensureTimelineProperty(
        operation,
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName,
        "Visible source objects hidden by Visual Inspection"
    ));
    if (!replacedInputs) {
        throw Base::TypeError("Visual Inspection replaced-input metadata has an incompatible type");
    }
    replacedInputs->setValues(exactInputs);
}

std::string pythonString(const std::string& value)
{
    std::string result {"'"};
    for (const char character : value) {
        switch (character) {
            case '\\':
                result += "\\\\";
                break;
            case '\'':
                result += "\\'";
                break;
            case '\n':
                result += "\\n";
                break;
            case '\r':
                result += "\\r";
                break;
            case '\t':
                result += "\\t";
                break;
            default:
                result += character;
                break;
        }
    }
    result += '\'';
    return result;
}

struct AcceptedInspection
{
    std::string featureName;
    std::string actualName;
};

struct ExactInspectionInput
{
    App::DocumentObject* object;
    std::string name;
    long objectId;
};

void recordAcceptedVisualInspection(
    const App::Document& document,
    const std::string& groupName,
    const std::vector<AcceptedInspection>& inspections,
    const std::vector<App::DocumentObject*>& nominals,
    const std::vector<App::DocumentObject*>& hidden,
    const std::vector<App::DocumentObject*>& replacedInputs,
    double searchRadius,
    double thickness
)
{
    if (!Gui::Application::Instance || !Gui::Application::Instance->macroManager()) {
        return;
    }
    auto* manager = Gui::Application::Instance->macroManager();
    const std::string documentName = pythonString(document.getName());
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_inspection_doc = App.getDocument(" + documentName + ")").c_str()
    );
    manager->addLine(Gui::MacroManager::App, "__vibecad_inspection_resources = []");
    manager->addLine(
        Gui::MacroManager::App,
        "__vibecad_inspection_doc.openTransaction('Visual Inspection')"
    );

    std::ostringstream number;
    number << std::setprecision(std::numeric_limits<double>::max_digits10);
    const auto propertyNumber = [&number](double value) {
        number.str(std::string());
        number.clear();
        number << std::setprecision(std::numeric_limits<double>::max_digits10) << value;
        return number.str();
    };
    std::string nominalList {"["};
    for (std::size_t index = 0; index < nominals.size(); ++index) {
        if (index) {
            nominalList += ',';
        }
        nominalList += "__vibecad_inspection_doc.getObject(";
        nominalList += pythonString(nominals[index]->getNameInDocument());
        nominalList += ')';
    }
    nominalList += ']';

    std::string replacedInputList {"["};
    for (std::size_t index = 0; index < replacedInputs.size(); ++index) {
        if (index) {
            replacedInputList += ',';
        }
        replacedInputList += "__vibecad_inspection_doc.getObject(";
        replacedInputList += pythonString(replacedInputs[index]->getNameInDocument());
        replacedInputList += ')';
    }
    replacedInputList += ']';

    for (const auto& accepted : inspections) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection = "
             "__vibecad_inspection_doc.addObject("
             "'Inspection::Feature',"
             + pythonString(accepted.featureName) + ")")
                .c_str()
        );
        manager->addLine(
            Gui::MacroManager::App,
            "__vibecad_inspection_resources.append("
            "__vibecad_inspection)"
        );
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection.Actual = "
             "__vibecad_inspection_doc.getObject("
             + pythonString(accepted.actualName) + ")")
                .c_str()
        );
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection.Nominals = " + nominalList).c_str()
        );
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection.SearchRadius = " + propertyNumber(searchRadius)).c_str()
        );
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection.Thickness = " + propertyNumber(thickness)).c_str()
        );
    }

    // Replay creates implementation resources first and the durable operation
    // root last so the exact atomic publisher receives one complete graph.
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_inspection_group = "
         "__vibecad_inspection_doc.addObject('Inspection::Group',"
         + pythonString(groupName) + ")")
            .c_str()
    );
    if (!replacedInputs.empty()) {
        manager->addLine(
            Gui::MacroManager::App,
            "__vibecad_inspection_group.addProperty("
            "'App::PropertyLinkListHidden','VibeCADTimelineReplacedInputs',"
            "'Timeline','Visible source objects hidden by Visual Inspection',"
            "attr=16,hidden=True,locked=True)"
        );
        manager->addLine(
            Gui::MacroManager::App,
            "__vibecad_inspection_group.setPropertyStatus("
            "'VibeCADTimelineReplacedInputs',"
            "('Hidden','LockDynamic','NoRecompute'))"
        );
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_inspection_group.VibeCADTimelineReplacedInputs = " + replacedInputList).c_str()
        );
    }
    manager->addLine(
        Gui::MacroManager::App,
        "for __vibecad_inspection in __vibecad_inspection_resources:\n"
        "    __vibecad_inspection_group.addObject(__vibecad_inspection)"
    );
    manager->addLine(
        Gui::MacroManager::App,
        "__vibecad_inspection_doc.publishProvisionalTimelineOperationBlock("
        "__vibecad_inspection_group, __vibecad_inspection_resources)"
    );
    manager->addLine(Gui::MacroManager::App, "__vibecad_inspection_doc.recompute()");

    const std::string guiDocument = "Gui.getDocument(" + documentName + ")";
    for (auto* object : hidden) {
        manager->addLine(
            // Replacement visibility is part of the accepted operation's
            // durable replay contract. Record it with the application
            // commands so macro preferences cannot omit it as presentation-
            // only GUI noise.
            Gui::MacroManager::App,
            (guiDocument + ".getObject(" + pythonString(object->getNameInDocument())
             + ").Visibility = False")
                .c_str()
        );
    }
    manager->addLine(Gui::MacroManager::App, "__vibecad_inspection_doc.commitTransaction()");
    manager->addLine(
        Gui::MacroManager::App,
        "del __vibecad_inspection, __vibecad_inspection_group, "
        "__vibecad_inspection_resources, __vibecad_inspection_doc"
    );
}
}  // namespace

std::vector<App::DocumentObject*> VisualInspection::candidateObjects(App::Document* document)
{
    std::vector<App::DocumentObject*> candidates;
    if (!document) {
        return candidates;
    }

    std::unordered_set<App::DocumentObject*> seen;

    for (auto* object : document->getObjects()) {
        App::DocumentObject* candidate = object;
        if (auto* body = freecad_cast<Part::BodyBase*>(object)) {
            // The Body is the stable public result object. Inspection reads
            // its current Tip shape at execution time, so the reference
            // remains legal and follows future history changes.
            candidate = body;
        }
        else if (Part::BodyBase::findBodyOf(object)) {
            // Body-owned features and origin geometry are private history,
            // already represented by their owning Body.
            continue;
        }

        if (!candidate || !seen.insert(candidate).second) {
            continue;
        }
        Inspection::ResolvedSource source;
        if (Inspection::resolveSource(candidate, document, source)) {
            candidates.push_back(candidate);
        }
    }
    return candidates;
}

namespace InspectionGui
{
class SingleSelectionItem: public QTreeWidgetItem
{
public:
    explicit SingleSelectionItem(QTreeWidget* parent)
        : QTreeWidgetItem(parent)
        , _compItem(nullptr)
    {}

    explicit SingleSelectionItem(QTreeWidgetItem* parent)
        : QTreeWidgetItem(parent)
        , _compItem(nullptr)
    {}

    ~SingleSelectionItem() override = default;

    SingleSelectionItem* getCompetitiveItem() const
    {
        return _compItem;
    }

    void setCompetitiveItem(SingleSelectionItem* item)
    {
        _compItem = item;
    }

private:
    SingleSelectionItem* _compItem;
};
}  // namespace InspectionGui

/* TRANSLATOR InspectionGui::DlgVisualInspectionImp */

/**
 *  Constructs a VisualInspection as a child of 'parent', with the
 *  name 'name' and widget flags set to 'f'.
 */
VisualInspection::VisualInspection(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , ui(new Ui_VisualInspection)
{
    ui->setupUi(this);
    connect(ui->treeWidgetActual, &QTreeWidget::itemClicked, this, &VisualInspection::onActivateItem);
    connect(ui->treeWidgetNominal, &QTreeWidget::itemClicked, this, &VisualInspection::onActivateItem);
    connect(
        ui->buttonBox,
        &QDialogButtonBox::helpRequested,
        Gui::getMainWindow(),
        &Gui::MainWindow::whatsThis
    );

    // FIXME: Not used yet
    ui->textLabel2->hide();
    ui->thickness->hide();
    ui->searchRadius->setUnit(Base::Unit::Length);
    ui->searchRadius->setRange(0, std::numeric_limits<double>::max());
    ui->thickness->setUnit(Base::Unit::Length);
    ui->thickness->setRange(0, std::numeric_limits<double>::max());

    App::Document* doc = App::GetApplication().getActiveDocument();
    // disable Ok button and enable of at least one item in each view is on
    buttonOk = ui->buttonBox->button(QDialogButtonBox::Ok);
    buttonOk->setDisabled(true);

    if (!doc) {
        ui->treeWidgetActual->setDisabled(true);
        ui->treeWidgetNominal->setDisabled(true);
        return;
    }
    targetDocumentName = doc->getName();
    targetDocumentUid = doc->Uid.getValueStr();
    targetDocumentAddress = doc;

    Gui::Document* gui = Gui::Application::Instance->getDocument(doc);
    if (!gui) {
        ui->treeWidgetActual->setDisabled(true);
        ui->treeWidgetNominal->setDisabled(true);
        return;
    }

    for (auto* object : candidateObjects(doc)) {
        Gui::ViewProvider* view = gui->getViewProvider(object);
        QIcon px = view ? view->getIcon() : QIcon();
        SingleSelectionItem* item1 = new SingleSelectionItem(ui->treeWidgetActual);
        item1->setText(0, QString::fromUtf8(object->Label.getValue()));
        item1->setData(0, ObjectNameRole, QString::fromLatin1(object->getNameInDocument()));
        item1->setData(0, ObjectIdRole, QVariant::fromValue<qlonglong>(object->getID()));
        item1->setCheckState(0, Qt::Unchecked);
        item1->setIcon(0, px);

        SingleSelectionItem* item2 = new SingleSelectionItem(ui->treeWidgetNominal);
        item2->setText(0, QString::fromUtf8(object->Label.getValue()));
        item2->setData(0, ObjectNameRole, QString::fromLatin1(object->getNameInDocument()));
        item2->setData(0, ObjectIdRole, QVariant::fromValue<qlonglong>(object->getID()));
        item2->setCheckState(0, Qt::Unchecked);
        item2->setIcon(0, px);

        item1->setCompetitiveItem(item2);
        item2->setCompetitiveItem(item1);
    }

    loadSettings();
}

/*
 *  Destroys the object and frees any allocated resources
 */
VisualInspection::~VisualInspection()
{
    // no need to delete child widgets, Qt does it all for us
    delete ui;
}

void VisualInspection::loadSettings()
{
    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Inspection/Inspection"
    );

    double searchDistance = ui->searchRadius->value().getValue();
    searchDistance = handle->GetFloat("SearchDistance", searchDistance);
    ui->searchRadius->setValue(searchDistance);

    double thickness = ui->thickness->value().getValue();
    thickness = handle->GetFloat("Thickness", thickness);
    ui->thickness->setValue(thickness);
}

void VisualInspection::saveSettings()
{
    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Inspection/Inspection"
    );
    double searchDistance = ui->searchRadius->value().getValue();
    handle->SetFloat("SearchDistance", searchDistance);

    double thickness = ui->thickness->value().getValue();
    handle->SetFloat("Thickness", thickness);
}

void VisualInspection::onActivateItem(QTreeWidgetItem* item)
{
    if (item) {
        SingleSelectionItem* sel = static_cast<SingleSelectionItem*>(item);
        SingleSelectionItem* cmp = sel->getCompetitiveItem();
        if (cmp && cmp->checkState(0) == Qt::Checked) {
            cmp->setCheckState(0, Qt::Unchecked);
        }
    }

    bool ok = false;
    for (QTreeWidgetItemIterator it(ui->treeWidgetActual); *it; ++it) {
        SingleSelectionItem* sel = (SingleSelectionItem*)*it;
        if (sel->checkState(0) == Qt::Checked) {
            ok = true;
            break;
        }
    }

    if (ok) {
        ok = false;
        for (QTreeWidgetItemIterator it(ui->treeWidgetNominal); *it; ++it) {
            SingleSelectionItem* sel = (SingleSelectionItem*)*it;
            if (sel->checkState(0) == Qt::Checked) {
                ok = true;
                break;
            }
        }
    }

    buttonOk->setEnabled(ok);
}

void VisualInspection::accept()
{
    onActivateItem(nullptr);
    if (!buttonOk->isEnabled()) {
        return;
    }

    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(targetDocumentName.c_str());
    }
    catch (...) {
        return;
    }
    auto* guiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    if (!document || targetDocumentUid.empty() || document != targetDocumentAddress
        || document->Uid.getValueStr() != targetDocumentUid || !guiDocument
        || App::GetApplication().getActiveDocument() != document
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()) {
        Base::Console().warning("Visual Inspection was not applied because its document "
                                "transaction is no longer clean.\n");
        return;
    }

    std::vector<App::DocumentObject*> nominalObjects;
    std::vector<App::DocumentObject*> actualObjects;
    const auto resolveItemObject = [document](const QTreeWidgetItem& item) {
        bool validId = false;
        const qlonglong objectId = item.data(0, ObjectIdRole).toLongLong(&validId);
        const QByteArray objectName = item.data(0, ObjectNameRole).toString().toLatin1();
        if (!validId || objectId <= 0 || objectName.isEmpty()) {
            return static_cast<App::DocumentObject*>(nullptr);
        }
        auto* object = document->getObjectByID(static_cast<long>(objectId));
        return object && object->getNameInDocument() && objectName == object->getNameInDocument()
                && document->containsObject(object)
                && document->getObject(objectName.constData()) == object
                && Inspection::isSourceUsable(object, document)
            ? object
            : nullptr;
    };
    for (QTreeWidgetItemIterator it(ui->treeWidgetNominal); *it; ++it) {
        auto* item = static_cast<SingleSelectionItem*>(*it);
        if (item->checkState(0) != Qt::Checked) {
            continue;
        }
        auto* object = resolveItemObject(*item);
        if (!object) {
            return;
        }
        nominalObjects.push_back(object);
    }
    for (QTreeWidgetItemIterator it(ui->treeWidgetActual); *it; ++it) {
        auto* item = static_cast<SingleSelectionItem*>(*it);
        if (item->checkState(0) != Qt::Checked) {
            continue;
        }
        auto* object = resolveItemObject(*item);
        if (!object) {
            return;
        }
        actualObjects.push_back(object);
    }
    if (nominalObjects.empty() || actualObjects.empty()) {
        return;
    }

    const double searchRadius = ui->searchRadius->value().getValue();
    const double thickness = ui->thickness->value().getValue();
    std::string createdGroupName;
    std::vector<AcceptedInspection> acceptedInspections;
    std::vector<App::DocumentObject*> timelineResources;
    std::vector<App::DocumentObject*> hiddenObjects;
    std::vector<ExactInspectionInput> hiddenIdentities;
    std::vector<App::DocumentObject*> replacedInputs;
    std::unique_ptr<Gui::ExactTransaction> transaction;
    auto rollback = [&]() {
        if (transaction) {
            (void)transaction->abort();
        }
    };
    try {
        transaction = std::make_unique<Gui::ExactTransaction>(*document, "Visual Inspection");

        auto* group = freecad_cast<App::DocumentObjectGroup*>(
            document->addObject("Inspection::Group", "Inspection")
        );
        if (!group) {
            throw Base::RuntimeError("Could not create the Visual Inspection group");
        }
        createdGroupName = group->getNameInDocument();

        for (auto* actual : actualObjects) {
            const std::string featureName = std::string(actual->getNameInDocument()) + "_Inspect";
            auto* inspection = freecad_cast<Inspection::Feature*>(
                document->addObject("Inspection::Feature", featureName.c_str())
            );
            if (!inspection) {
                throw Base::RuntimeError("Could not create a Visual Inspection result");
            }
            group->addObject(inspection);
            timelineResources.push_back(inspection);
            inspection->Actual.setValue(actual);
            inspection->Nominals.setValues(nominalObjects);
            inspection->SearchRadius.setValue(searchRadius);
            inspection->Thickness.setValue(thickness);
            acceptedInspections.push_back({
                inspection->getNameInDocument(),
                actual->getNameInDocument(),
            });
        }

        document->recompute();
        std::unordered_set<App::DocumentObject*> hidden;
        hidden.insert(actualObjects.begin(), actualObjects.end());
        hidden.insert(nominalObjects.begin(), nominalObjects.end());
        hiddenObjects.assign(hidden.begin(), hidden.end());
        std::ranges::sort(hiddenObjects, {}, [](const App::DocumentObject* object) {
            return std::string_view(object->getNameInDocument());
        });
        hiddenIdentities.reserve(hiddenObjects.size());
        for (auto* object : hiddenObjects) {
            hiddenIdentities.push_back({
                object,
                object->getNameInDocument(),
                object->getID(),
            });
            if (auto* view = Gui::Application::Instance
                                 ->getViewProvider<Gui::ViewProviderDocumentObject>(object)) {
                if (view->Visibility.getValue()) {
                    replacedInputs.push_back(object);
                }
            }
        }
        markTimelineReplacedInputs(*group, replacedInputs);

        auto* timeline = App::DocumentTimeline::get(document);
        if (!timeline) {
            throw Base::RuntimeError("Visual Inspection could not access the document history");
        }
        timeline->publishProvisionalOperationBlock(group, timelineResources);

        // Semantic publication verifies that every operation which predates
        // this transaction still has its accepted state.  Record the exact
        // visible inputs above, publish while that pre-creation state is
        // intact, then apply this operation's replacement visibility in the
        // same transaction.
        hiddenObjects.clear();
        hiddenObjects.reserve(hiddenIdentities.size());
        for (const auto& identity : hiddenIdentities) {
            auto* object = document->getObjectByID(identity.objectId);
            if (object != identity.object || !object->getNameInDocument()
                || identity.name != object->getNameInDocument() || !document->containsObject(object)) {
                throw Base::RuntimeError("A Visual Inspection input changed exact identity "
                                         "during history publication");
            }
            hiddenObjects.push_back(object);
            if (auto* view = Gui::Application::Instance
                                 ->getViewProvider<Gui::ViewProviderDocumentObject>(object)) {
                view->Visibility.setValue(false);
            }
        }

        if (!transaction->commit()) {
            throw Base::RuntimeError("Could not commit the Visual Inspection transaction");
        }
        transaction.reset();
    }
    catch (const Base::Exception& error) {
        rollback();
        Base::Console().error("Visual Inspection failed: %s\n", error.what());
        return;
    }
    catch (const std::exception& error) {
        rollback();
        Base::Console().error("Visual Inspection failed: %s\n", error.what());
        return;
    }
    catch (...) {
        rollback();
        Base::Console().error("Visual Inspection failed with an unknown error.\n");
        return;
    }

    try {
        // Recording is supplementary and happens only after the exact
        // transaction is durable. A macro-output failure cannot invalidate
        // accepted inspection geometry.
        recordAcceptedVisualInspection(
            *document,
            createdGroupName,
            acceptedInspections,
            nominalObjects,
            hiddenObjects,
            replacedInputs,
            searchRadius,
            thickness
        );
    }
    catch (const std::exception& error) {
        Base::Console().warning(
            "Visual Inspection was accepted, but its macro record "
            "failed: %s\n",
            error.what()
        );
    }
    catch (...) {
        Base::Console().warning("Visual Inspection was accepted, but its macro record failed.\n");
    }
    saveSettings();
    QDialog::accept();
}

#include "moc_VisualInspection.cpp"
