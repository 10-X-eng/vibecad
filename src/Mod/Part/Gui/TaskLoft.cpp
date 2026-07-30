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


#include <QMessageBox>
#include <QTreeWidget>
#include <algorithm>
#include <exception>
#include <map>
#include <sstream>
#include <Precision.hxx>
#include <ShapeAnalysis_FreeBounds.hxx>
#include <Standard_Failure.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Iterator.hxx>
#include <TopTools_HSequenceOfShape.hxx>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Macro.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>

#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/PartFeatures.h>

#include "TaskLoft.h"
#include "ModelingSelection.h"
#include "ui_TaskLoft.h"


using namespace PartGui;

namespace
{
constexpr int ObjectRole = Qt::UserRole;
constexpr int SubElementRole = Qt::UserRole + 1;

struct ExactDocumentIdentity
{
    App::Document* address = nullptr;
    std::string name;
    std::string uid;
};

struct ExactObjectIdentity
{
    ExactDocumentIdentity document;
    const App::DocumentObject* address = nullptr;
    long id = -1;
    std::string name;
};

ExactDocumentIdentity exactDocumentIdentity(App::Document* document)
{
    if (!document) {
        return {};
    }
    return {
        document,
        document->getName(),
        document->Uid.getValueStr(),
    };
}

ExactObjectIdentity exactObjectIdentity(const App::DocumentObject* object)
{
    if (!object || !object->isAttachedToDocument() || !object->getNameInDocument()) {
        return {};
    }
    return {
        exactDocumentIdentity(object->getDocument()),
        object,
        object->getID(),
        object->getNameInDocument(),
    };
}

App::Document* resolveExactDocument(const ExactDocumentIdentity& identity) noexcept
{
    if (!identity.address || identity.name.empty() || identity.uid.empty()) {
        return nullptr;
    }
    try {
        auto* document = App::GetApplication().getDocument(identity.name.c_str());
        return document == identity.address && document->Uid.getValueStr() == identity.uid ? document
                                                                                           : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

App::DocumentObject* resolveExactObject(const ExactObjectIdentity& identity) noexcept
{
    auto* document = resolveExactDocument(identity.document);
    auto* object = document && identity.id >= 0 ? document->getObjectByID(identity.id) : nullptr;
    return object && object == identity.address && object->getNameInDocument()
            && identity.name == object->getNameInDocument()
            && document->getObject(identity.name.c_str()) == object
        ? object
        : nullptr;
}

App::PropertyLinkSubList& profileLinksProperty(Part::Loft& loft)
{
    auto* property = dynamic_cast<App::PropertyLinkSubList*>(loft.getPropertyByName("ProfileLinks"));
    if (!property) {
        throw Base::RuntimeError("The Loft ProfileLinks property is unavailable");
    }
    return *property;
}

Part::TopoShape profileShape(App::DocumentObject* object, const std::string& subElement)
{
    auto options = Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform;
    if (!subElement.empty()) {
        options |= Part::ShapeOption::NeedSubElement;
    }
    return Part::Feature::getTopoShape(
        object,
        options,
        subElement.empty() ? nullptr : subElement.c_str()
    );
}

bool isLoftProfileUnchecked(App::DocumentObject* object, const std::string& subElement)
{
    const auto shape = profileShape(object, subElement);
    if (shape.isNull() || !shape.isValid() || shape.getShape().Infinite()) {
        return false;
    }
    TopoDS_Shape candidate = shape.getShape();
    if (candidate.ShapeType() == TopAbs_COMPOUND) {
        Handle(TopTools_HSequenceOfShape) edges = new TopTools_HSequenceOfShape();
        Handle(TopTools_HSequenceOfShape) wires = new TopTools_HSequenceOfShape();
        TopoDS_Shape onlyChild;
        int childCount = 0;
        for (TopoDS_Iterator iterator(candidate); iterator.More(); iterator.Next()) {
            if (iterator.Value().IsNull()) {
                continue;
            }
            onlyChild = iterator.Value();
            ++childCount;
            if (onlyChild.ShapeType() == TopAbs_EDGE) {
                edges->Append(onlyChild);
            }
        }
        if (childCount == 1) {
            candidate = onlyChild;
        }
        else if (childCount > 0 && edges->Length() == childCount) {
            ShapeAnalysis_FreeBounds::ConnectEdgesToWires(
                edges,
                Precision::Confusion(),
                Standard_False,
                wires
            );
            if (wires->Length() == 1) {
                candidate = wires->Value(1);
            }
        }
    }
    const auto type = candidate.ShapeType();
    return type == TopAbs_FACE || type == TopAbs_WIRE || type == TopAbs_EDGE || type == TopAbs_VERTEX;
}

bool isLoftProfile(App::DocumentObject* object, const std::string& subElement) noexcept
{
    try {
        return isLoftProfileUnchecked(object, subElement);
    }
    catch (...) {
        return false;
    }
}

QTreeWidgetItem* makeProfileItem(
    App::DocumentObject* object,
    const std::string& subElement,
    Gui::Document* guiDocument
)
{
    const QString objectLabel = QString::fromUtf8(object->Label.getValue());
    const QString label = subElement.empty()
        ? objectLabel
        : QStringLiteral("%1 (%2)").arg(objectLabel, QString::fromStdString(subElement));
    auto* item = new QTreeWidgetItem();
    item->setText(0, label);
    item->setToolTip(0, label);
    item->setData(0, ObjectRole, QString::fromLatin1(object->getNameInDocument()));
    item->setData(0, SubElementRole, QString::fromStdString(subElement));
    if (auto* viewProvider = guiDocument->getViewProvider(object)) {
        item->setIcon(0, viewProvider->getIcon());
    }
    return item;
}

std::string pythonString(const std::string& value)
{
    return "'" + Base::Tools::escapeEncodeString(value) + "'";
}

std::string pythonObjectReference(const App::DocumentObject* object)
{
    if (!object || !object->getDocument() || !object->getNameInDocument()) {
        return "None";
    }
    return "App.getDocument(" + pythonString(object->getDocument()->getName()) + ").getObject("
        + pythonString(object->getNameInDocument()) + ")";
}

std::string pythonObjectList(const std::vector<App::DocumentObject*>& objects)
{
    std::ostringstream stream;
    stream << '[';
    for (std::size_t index = 0; index < objects.size(); ++index) {
        if (index) {
            stream << ',';
        }
        stream << pythonObjectReference(objects[index]);
    }
    stream << ']';
    return stream.str();
}

std::string pythonProfileLinks(
    const std::vector<App::DocumentObject*>& objects,
    const std::vector<std::string>& subElements
)
{
    std::ostringstream stream;
    stream << '[';
    for (std::size_t index = 0; index < objects.size(); ++index) {
        if (index) {
            stream << ',';
        }
        stream << '(' << pythonObjectReference(objects[index]) << ",["
               << pythonString(subElements[index]) << "])";
    }
    stream << ']';
    return stream.str();
}

void recordAcceptedLoft(
    const Part::Loft& loft,
    const std::vector<App::DocumentObject*>& profiles,
    const std::vector<std::string>& subElements,
    bool hasSubElement,
    App::DocumentObject* parent
)
{
    if (!Gui::Application::Instance || !Gui::Application::Instance->macroManager()) {
        return;
    }
    auto* manager = Gui::Application::Instance->macroManager();
    const auto* document = loft.getDocument();
    manager->addLine(Gui::MacroManager::App, "import Part");
    const std::string documentRef = "App.getDocument(" + pythonString(document->getName()) + ")";
    manager->addLine(Gui::MacroManager::App, ("__vibecad_loft_doc = " + documentRef).c_str());
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft = __vibecad_loft_doc.addObject('Part::Loft',"
         + pythonString(loft.getNameInDocument()) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.Sections = " + pythonObjectList(profiles)).c_str()
    );
    if (hasSubElement) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_loft.ProfileLinks = " + pythonProfileLinks(profiles, subElements)).c_str()
        );
    }
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.Solid = " + std::string(loft.Solid.getValue() ? "True" : "False")).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.Ruled = " + std::string(loft.Ruled.getValue() ? "True" : "False")).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.Closed = " + std::string(loft.Closed.getValue() ? "True" : "False")).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.MaxDegree = " + std::to_string(loft.MaxDegree.getValue())).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_loft.Linearize = " + std::string(loft.Linearize.getValue() ? "True" : "False")).c_str()
    );
    if (parent) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_loft_parent = " + pythonObjectReference(parent)).c_str()
        );
        manager->addLine(Gui::MacroManager::App, "__vibecad_loft_parent.addObject(__vibecad_loft)");
        manager->addLine(
            Gui::MacroManager::App,
            "if hasattr(__vibecad_loft_parent, 'Tip'): "
            "__vibecad_loft_parent.Tip = __vibecad_loft"
        );
    }
    manager->addLine(Gui::MacroManager::App, "__vibecad_loft_doc.recompute()");
    manager->addLine(
        Gui::MacroManager::App,
        parent
            ? "del __vibecad_loft_parent, __vibecad_loft, __vibecad_loft_doc"
            : "del __vibecad_loft, __vibecad_loft_doc"
    );
}

}  // namespace

class LoftWidget::Private
{
public:
    Ui_TaskLoft ui;
    ExactDocumentIdentity document;
    std::map<const QTreeWidgetItem*, ExactObjectIdentity> profileIdentities;
    Part::Loft* acceptedResult = nullptr;
    Private() = default;
    ~Private() = default;
};

/* TRANSLATOR PartGui::LoftWidget */

LoftWidget::LoftWidget(QWidget* parent)
    : d(new Private())
{
    Q_UNUSED(parent);

    d->ui.setupUi(this);
    d->ui.selector->setAvailableLabel(tr("Available profiles"));
    d->ui.selector->setSelectedLabel(tr("Selected profiles"));

    // clang-format off
    connect(d->ui.selector->availableTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &LoftWidget::onCurrentItemChanged);
    connect(d->ui.selector->selectedTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &LoftWidget::onCurrentItemChanged);
    // clang-format on

    findShapes();
}

LoftWidget::~LoftWidget()
{
    delete d;
}

void LoftWidget::findShapes()
{
    App::Document* activeDoc = App::GetApplication().getActiveDocument();
    if (!activeDoc) {
        return;
    }
    Gui::Document* activeGui = Gui::Application::Instance->getDocument(activeDoc);
    if (!activeGui) {
        return;
    }
    d->document = exactDocumentIdentity(activeDoc);
    const auto addProfileItem =
        [this,
         activeGui](QTreeWidget* tree, App::DocumentObject* object, const std::string& subElement) {
            auto* item = makeProfileItem(object, subElement, activeGui);
            d->profileIdentities.emplace(item, exactObjectIdentity(object));
            tree->addTopLevelItem(item);
        };

    // Launch-time selection is the ordered profile role. A Body row is
    // projected to its Tip, while App::Link occurrences remain occurrences.
    auto launchSelection = PartGui::getModelingShapeSelection(activeDoc->getName());
    for (auto& selected : launchSelection) {
        auto* object = selected.getObject();
        if (!object) {
            continue;
        }
        const auto& subElements = selected.getSubNames();
        if (subElements.empty()) {
            if (isLoftProfile(object, {})) {
                addProfileItem(d->ui.selector->selectedTreeWidget(), object, {});
            }
            continue;
        }
        for (const auto& subElement : subElements) {
            if (isLoftProfile(object, subElement)) {
                addProfileItem(d->ui.selector->selectedTreeWidget(), object, subElement);
            }
        }
    }

    const auto objs = PartGui::resolveModelingObjects(
        activeDoc->getObjectsOfType<App::DocumentObject>()
    );

    for (auto obj : objs) {
        if (!isLoftProfile(obj, {})) {
            continue;
        }
        addProfileItem(d->ui.selector->availableTreeWidget(), obj, {});
    }
}

bool LoftWidget::accept()
{
    d->acceptedResult = nullptr;
    int count = d->ui.selector->selectedTreeWidget()->topLevelItemCount();
    if (count < 2) {
        QMessageBox::critical(
            this,
            tr("Too Few Elements"),
            tr("At least 2 vertices, edges, wires, or faces are required.")
        );
        return false;
    }

    auto* appDocument = resolveExactDocument(d->document);
    auto* guiDocument = appDocument && Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(appDocument)
        : nullptr;
    if (!appDocument || !guiDocument) {
        QMessageBox::warning(this, tr("Input error"), tr("The document is no longer available."));
        return false;
    }

    std::vector<App::DocumentObject*> profiles;
    std::vector<std::string> subElements;
    bool hasSubElement = false;
    profiles.reserve(count);
    subElements.reserve(count);
    for (int i = 0; i < count; i++) {
        auto* item = d->ui.selector->selectedTreeWidget()->topLevelItem(i);
        const auto subElement = item->data(0, SubElementRole).toString().toStdString();
        const auto identity = d->profileIdentities.find(item);
        auto* object = identity != d->profileIdentities.end() ? resolveExactObject(identity->second)
                                                              : nullptr;
        if (!object || object->getDocument() != appDocument
            || !PartGui::isModelingObjectActive(object) || !isLoftProfile(object, subElement)) {
            QMessageBox::critical(
                this,
                tr("Invalid Profile"),
                tr("Every loft profile must still resolve to a valid vertex, edge, wire, or face.")
            );
            return false;
        }
        profiles.push_back(object);
        subElements.push_back(subElement);
        hasSubElement = hasSubElement || !subElement.empty();
    }

    std::vector<const App::DocumentObject*> operands(
        profiles.begin(),
        profiles.end()
    );
    const auto resultOwner =
        PartGui::inferModelingOperandOwner(*appDocument, operands);
    std::vector<App::DocumentObject*> replacedPresentations;
    std::vector<App::DocumentObject*> objectsToHide;
    const auto appendVisible = [](std::vector<App::DocumentObject*>& objects,
                                  App::DocumentObject* object) {
        if (object && object->Visibility.getValue()
            && std::ranges::find(objects, object) == objects.end()) {
            objects.push_back(object);
        }
    };
    for (auto* profile : profiles) {
        appendVisible(objectsToHide, profile);
        if (resultOwner.ownership
            == PartGui::ModelingResultOwnership::DocumentRoot) {
            auto* presentation =
                PartGui::resolveModelingPresentationObject(profile);
            appendVisible(replacedPresentations, presentation);
            appendVisible(objectsToHide, presentation);
        }
    }

    try {
        ModelingTaskAttempt attempt(*appDocument, "Loft");
        auto* loft = appDocument->addObject<Part::Loft>("Loft");
        if (!loft) {
            throw Base::RuntimeError("Could not create the loft feature");
        }
        attempt.trackCreatedObject(*loft);
        if (resultOwner.ownership
            == PartGui::ModelingResultOwnership::Body) {
            attempt.targetResultBody(*loft, *resultOwner.body);
        }
        else {
            attempt.keepResultAtDocumentRoot(*loft);
        }
        if (!replacedPresentations.empty()) {
            attempt.trackReplacedInputs(
                *loft,
                replacedPresentations
            );
        }
        loft->Sections.setValues(profiles);
        if (hasSubElement) {
            profileLinksProperty(*loft).setValues(
                std::vector<App::DocumentObject*>(profiles),
                std::vector<std::string>(subElements)
            );
        }
        loft->Solid.setValue(d->ui.checkSolid->isChecked());
        loft->Ruled.setValue(d->ui.checkRuledSurface->isChecked());
        loft->Closed.setValue(d->ui.checkClosed->isChecked());
        appDocument->recompute();

        const auto shape = loft->Shape.getShape();
        if (!loft->isValid() || shape.isNull() || !shape.isValid()) {
            const auto status = loft->getStatusString();
            throw Base::RuntimeError(
                status && *status ? status : "Loft did not produce valid geometry"
            );
        }
        if (loft->Solid.getValue() && shape.countSubShapes(TopAbs_SOLID) == 0) {
            throw Base::RuntimeError("Loft was asked for a solid but did not produce one");
        }
        recordAcceptedLoft(
            *loft,
            profiles,
            subElements,
            hasSubElement,
            resultOwner.body
        );
        for (auto* object : objectsToHide) {
            Gui::cmdAppObjectHide(object);
        }
        attempt.commit();
        d->acceptedResult = loft;
    }
    catch (const Base::Exception& e) {
        d->acceptedResult = nullptr;
        QMessageBox::warning(
            this,
            tr("Input error"),
            QCoreApplication::translate("Exception", e.what())
        );
        return false;
    }
    catch (const Standard_Failure& e) {
        d->acceptedResult = nullptr;
        QMessageBox::warning(this, tr("Input error"), QString::fromUtf8(e.GetMessageString()));
        return false;
    }
    catch (const std::exception& e) {
        d->acceptedResult = nullptr;
        QMessageBox::warning(this, tr("Input error"), QString::fromUtf8(e.what()));
        return false;
    }
    catch (...) {
        d->acceptedResult = nullptr;
        QMessageBox::warning(this, tr("Input error"), tr("Unexpected loft failure."));
        return false;
    }

    return true;
}

Part::Loft* LoftWidget::lastAcceptedResult() const noexcept
{
    return d->acceptedResult;
}

bool LoftWidget::reject()
{
    return true;
}

void LoftWidget::onCurrentItemChanged(QTreeWidgetItem* current, QTreeWidgetItem* previous)
{
    if (previous) {
        const auto identity = d->profileIdentities.find(previous);
        if (identity != d->profileIdentities.end() && resolveExactObject(identity->second)) {
            Gui::Selection().rmvSelection(d->document.name.c_str(), identity->second.name.c_str());
        }
    }
    if (current) {
        const auto identity = d->profileIdentities.find(current);
        if (identity != d->profileIdentities.end() && resolveExactObject(identity->second)) {
            Gui::Selection().addSelection(d->document.name.c_str(), identity->second.name.c_str());
        }
    }
}

void LoftWidget::changeEvent(QEvent* e)
{
    QWidget::changeEvent(e);
    if (e->type() == QEvent::LanguageChange) {
        d->ui.retranslateUi(this);
        d->ui.selector->setAvailableLabel(tr("Vertex/Edge/Wire/Face"));
        d->ui.selector->setSelectedLabel(tr("Loft"));
    }
}


/* TRANSLATOR PartGui::TaskLoft */

TaskLoft::TaskLoft()
{
    widget = new LoftWidget();
    addTaskBox(Gui::BitmapFactory().pixmap("Part_Loft"), widget);
}

TaskLoft::~TaskLoft() = default;

void TaskLoft::open()
{}

void TaskLoft::clicked(int)
{}

bool TaskLoft::accept()
{
    if (!widget->accept()) {
        return false;
    }
    markCommandInteractionStateDurable({widget->lastAcceptedResult()});
    return true;
}

bool TaskLoft::reject()
{
    return widget->reject();
}

#include "moc_TaskLoft.cpp"
