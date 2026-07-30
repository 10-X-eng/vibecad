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


#include <QApplication>
#include <QMessageBox>
#include <QTimer>
#include <QTreeWidget>
#include <algorithm>
#include <exception>
#include <map>
#include <optional>
#include <ranges>
#include <sstream>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <Precision.hxx>
#include <ShapeAnalysis_FreeBounds.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Iterator.hxx>
#include <TopTools_HSequenceOfShape.hxx>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Link.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Macro.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionFilter.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/ViewProvider.h>
#include <Gui/WaitCursor.h>
#include <Mod/Part/App/PartFeatures.h>
#include <Mod/Part/App/BodyBase.h>

#include "TaskSweep.h"
#include "ModelingSelection.h"
#include "ui_TaskSweep.h"


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

struct ExactSelectionIdentity
{
    ExactObjectIdentity object;
    std::vector<std::string> subElements;
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

App::PropertyLinkSubList& profileLinksProperty(Part::Sweep& sweep)
{
    auto* property = dynamic_cast<App::PropertyLinkSubList*>(sweep.getPropertyByName("ProfileLinks"));
    if (!property) {
        throw Base::RuntimeError("The Sweep ProfileLinks property is unavailable");
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

bool isSweepProfileUnchecked(App::DocumentObject* object, const std::string& subElement)
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

bool isSweepProfile(App::DocumentObject* object, const std::string& subElement) noexcept
{
    try {
        return isSweepProfileUnchecked(object, subElement);
    }
    catch (...) {
        return false;
    }
}

bool isSweepPathValidUnchecked(
    const App::DocumentObject* path,
    const std::vector<std::string>& subElements
)
{
    if (!path) {
        return false;
    }

    TopoDS_Shape pathShape;
    const Part::TopoShape& shape = Part::Feature::getTopoShape(
        path,
        Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
    );
    if (shape.isNull()) {
        return false;
    }
    if (!subElements.empty()) {
        try {
            BRepBuilderAPI_MakeWire wire;
            for (const auto& subElement : subElements) {
                wire.Add(TopoDS::Edge(shape.getSubShape(subElement.c_str())));
            }
            pathShape = wire.Wire();
        }
        catch (...) {
            return false;
        }
    }
    else if (shape.getShape().ShapeType() == TopAbs_EDGE) {
        pathShape = shape.getShape();
    }
    else if (shape.getShape().ShapeType() == TopAbs_WIRE) {
        BRepBuilderAPI_MakeWire wire(TopoDS::Wire(shape.getShape()));
        pathShape = wire.Wire();
    }
    else if (shape.getShape().ShapeType() == TopAbs_COMPOUND) {
        try {
            TopoDS_Iterator iterator(shape.getShape());
            for (; iterator.More(); iterator.Next()) {
                if (iterator.Value().ShapeType() != TopAbs_EDGE
                    && iterator.Value().ShapeType() != TopAbs_WIRE) {
                    return false;
                }
            }
            Handle(TopTools_HSequenceOfShape) edges = new TopTools_HSequenceOfShape();
            Handle(TopTools_HSequenceOfShape) wires = new TopTools_HSequenceOfShape();
            for (TopExp_Explorer explorer(shape.getShape(), TopAbs_EDGE); explorer.More();
                 explorer.Next()) {
                edges->Append(explorer.Current());
            }
            ShapeAnalysis_FreeBounds::ConnectEdgesToWires(
                edges,
                Precision::Confusion(),
                Standard_True,
                wires
            );
            if (wires->Length() != 1) {
                return false;
            }
            pathShape = wires->Value(1);
        }
        catch (...) {
            return false;
        }
    }

    return !pathShape.IsNull();
}

bool isSweepPathValid(
    const App::DocumentObject* path,
    const std::vector<std::string>& subElements
) noexcept
{
    try {
        return isSweepPathValidUnchecked(path, subElements);
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

std::string pythonLinkSub(const App::DocumentObject* object, const std::vector<std::string>& subElements)
{
    std::ostringstream stream;
    stream << '(' << pythonObjectReference(object) << ",[";
    for (std::size_t index = 0; index < subElements.size(); ++index) {
        if (index) {
            stream << ',';
        }
        stream << pythonString(subElements[index]);
    }
    stream << "])";
    return stream.str();
}

void recordAcceptedSweep(
    const Part::Sweep& sweep,
    const std::vector<App::DocumentObject*>& profiles,
    const std::vector<std::string>& profileSubElements,
    bool hasProfileSubElement,
    App::DocumentObject* path,
    const std::vector<std::string>& pathSubElements,
    App::DocumentObject* parent
)
{
    if (!Gui::Application::Instance || !Gui::Application::Instance->macroManager()) {
        return;
    }
    auto* manager = Gui::Application::Instance->macroManager();
    const auto* document = sweep.getDocument();
    manager->addLine(Gui::MacroManager::App, "import Part");
    const std::string documentRef = "App.getDocument(" + pythonString(document->getName()) + ")";
    manager->addLine(Gui::MacroManager::App, ("__vibecad_sweep_doc = " + documentRef).c_str());
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep = __vibecad_sweep_doc.addObject('Part::Sweep',"
         + pythonString(sweep.getNameInDocument()) + ")")
            .c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Sections = " + pythonObjectList(profiles)).c_str()
    );
    if (hasProfileSubElement) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_sweep.ProfileLinks = " + pythonProfileLinks(profiles, profileSubElements)).c_str()
        );
    }
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Spine = " + pythonLinkSub(path, pathSubElements)).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Solid = " + std::string(sweep.Solid.getValue() ? "True" : "False")).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Frenet = " + std::string(sweep.Frenet.getValue() ? "True" : "False")).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Transition = " + pythonString(sweep.Transition.getValueAsString())).c_str()
    );
    manager->addLine(
        Gui::MacroManager::App,
        ("__vibecad_sweep.Linearize = " + std::string(sweep.Linearize.getValue() ? "True" : "False"))
            .c_str()
    );
    if (parent) {
        manager->addLine(
            Gui::MacroManager::App,
            ("__vibecad_sweep_parent = " + pythonObjectReference(parent)).c_str()
        );
        manager->addLine(Gui::MacroManager::App, "__vibecad_sweep_parent.addObject(__vibecad_sweep)");
        manager->addLine(
            Gui::MacroManager::App,
            "if hasattr(__vibecad_sweep_parent, 'Tip'): "
            "__vibecad_sweep_parent.Tip = __vibecad_sweep"
        );
    }
    manager->addLine(Gui::MacroManager::App, "__vibecad_sweep_doc.recompute()");
    manager->addLine(
        Gui::MacroManager::App,
        parent ? "del __vibecad_sweep_parent, __vibecad_sweep, __vibecad_sweep_doc"
               : "del __vibecad_sweep, __vibecad_sweep_doc"
    );
}

}  // namespace

class SweepWidget::Private
{
public:
    Ui_TaskSweep ui;
    QString buttonText;
    ExactDocumentIdentity document;
    std::map<const QTreeWidgetItem*, ExactObjectIdentity> profileIdentities;
    std::optional<ExactSelectionIdentity> path;
    Part::Sweep* acceptedResult = nullptr;
    Private() = default;
    ~Private() = default;

    class EdgeSelection: public Gui::SelectionFilterGate
    {
    public:
        EdgeSelection()
            : Gui::SelectionFilterGate(nullPointer())
        {}
        bool allow(App::Document* /*pDoc*/, App::DocumentObject* pObj, const char* sSubName) override
        {
            if (!PartGui::isModelingObjectActive(pObj)) {
                return false;
            }
            pObj = PartGui::resolveModelingObject(pObj);
            if (!pObj) {
                return false;
            }
            if (Base::Tools::isNullOrEmpty(sSubName)) {
                // If selecting again the same edge the passed sub-element is empty. If the whole
                // shape is an edge or wire we can use it completely.
                Part::TopoShape topoShape = Part::Feature::getTopoShape(
                    pObj,
                    Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
                );
                if (topoShape.isNull()) {
                    return false;
                }
                const TopoDS_Shape shape = topoShape.getShape();
                if (!shape.IsNull()) {
                    // a single edge
                    if (shape.ShapeType() == TopAbs_EDGE) {
                        return true;
                    }
                    // a single wire
                    if (shape.ShapeType() == TopAbs_WIRE) {
                        return true;
                    }
                    // a compound of only edges or wires
                    if (shape.ShapeType() == TopAbs_COMPOUND) {
                        TopoDS_Iterator it(shape);
                        for (; it.More(); it.Next()) {
                            if (it.Value().IsNull()) {
                                return false;
                            }
                            if ((it.Value().ShapeType() != TopAbs_EDGE)
                                && (it.Value().ShapeType() != TopAbs_WIRE)) {
                                return false;
                            }
                        }

                        return true;
                    }
                }
            }
            else {
                std::string element(sSubName);
                return element.substr(0, 4) == "Edge";
            }

            return false;
        }
    };
};

/* TRANSLATOR PartGui::SweepWidget */

SweepWidget::SweepWidget(QWidget* parent)
    : d(new Private())
{
    Q_UNUSED(parent);

    d->ui.setupUi(this);
    d->ui.selector->setAvailableLabel(tr("Available profiles"));
    d->ui.selector->setSelectedLabel(tr("Selected profiles"));
    d->ui.labelPath->clear();

    // clang-format off
    connect(d->ui.buttonPath, &QPushButton::toggled,
            this, &SweepWidget::onButtonPathToggled);
    connect(d->ui.selector->availableTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &SweepWidget::onCurrentItemChanged);
    connect(d->ui.selector->selectedTreeWidget(), &QTreeWidget::currentItemChanged,
            this, &SweepWidget::onCurrentItemChanged);
    // clang-format on

    findShapes();
}

SweepWidget::~SweepWidget()
{
    delete d;
    Gui::Selection().rmvSelectionGate();
}

void SweepWidget::findShapes()
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

    // Launch-time selection supplies profiles only. The path is assigned only
    // by the explicit Sweep Path action and is stored independently.
    auto launchSelection = PartGui::getModelingShapeSelection(activeDoc->getName());
    for (auto& selected : launchSelection) {
        auto* object = selected.getObject();
        if (!object) {
            continue;
        }
        const auto& subElements = selected.getSubNames();
        if (subElements.empty()) {
            if (isSweepProfile(object, {})) {
                addProfileItem(d->ui.selector->selectedTreeWidget(), object, {});
            }
            continue;
        }
        for (const auto& subElement : subElements) {
            if (isSweepProfile(object, subElement)) {
                addProfileItem(d->ui.selector->selectedTreeWidget(), object, subElement);
            }
        }
    }

    const auto objs = PartGui::resolveModelingObjects(
        activeDoc->getObjectsOfType<App::DocumentObject>()
    );

    for (auto obj : objs) {
        if (!isSweepProfile(obj, {})) {
            continue;
        }
        addProfileItem(d->ui.selector->availableTreeWidget(), obj, {});
    }
}

bool SweepWidget::isPathValid(const Gui::SelectionObject& sel) const
{
    return isSweepPathValid(sel.getObject(), sel.getSubNames());
}

bool SweepWidget::accept()
{
    d->acceptedResult = nullptr;
    if (d->ui.buttonPath->isChecked()) {
        return false;
    }
    int count = d->ui.selector->selectedTreeWidget()->topLevelItemCount();
    if (count < 1) {
        QMessageBox::critical(this, tr("Too Few Elements"), tr("At least one edge or wire is required."));
        return false;
    }
    if (!d->path) {
        QMessageBox::critical(
            this,
            tr("Missing Sweep Path"),
            tr("Use Sweep Path to assign one connected edge or wire.")
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

    auto* pathObject = resolveExactObject(d->path->object);
    const auto& pathSubElements = d->path->subElements;
    if (!pathObject || pathObject->getDocument() != appDocument
        || !PartGui::isModelingObjectActive(pathObject)
        || !isSweepPathValid(pathObject, pathSubElements)) {
        QMessageBox::critical(
            this,
            tr("Invalid Sweep Path"),
            tr("The assigned sweep path is no longer the same active model geometry.")
        );
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
            || !PartGui::isModelingObjectActive(object) || !isSweepProfile(object, subElement)) {
            QMessageBox::critical(
                this,
                tr("Invalid Profile"),
                tr("Every sweep profile must still resolve to valid geometry.")
            );
            return false;
        }
        if (object == pathObject
            && (subElement.empty() || pathSubElements.empty()
                || std::ranges::find(pathSubElements, subElement) != pathSubElements.end())) {
            QMessageBox::critical(
                this,
                tr("Conflicting Sweep Roles"),
                tr("The same geometry cannot be both a sweep profile and its path.")
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
    if (std::ranges::find(operands, pathObject) == operands.end()) {
        operands.push_back(pathObject);
    }
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
    for (auto* operand : operands) {
        appendVisible(
            objectsToHide,
            const_cast<App::DocumentObject*>(operand)
        );
        if (resultOwner.ownership
            == PartGui::ModelingResultOwnership::DocumentRoot) {
            auto* presentation = const_cast<App::DocumentObject*>(
                PartGui::resolveModelingPresentationObject(operand)
            );
            appendVisible(replacedPresentations, presentation);
            appendVisible(objectsToHide, presentation);
        }
    }

    try {
        Gui::WaitCursor wc;
        ModelingTaskAttempt attempt(*appDocument, "Sweep");
        auto* sweep = appDocument->addObject<Part::Sweep>("Sweep");
        if (!sweep) {
            throw Base::RuntimeError("Could not create the sweep feature");
        }
        attempt.trackCreatedObject(*sweep);
        if (resultOwner.ownership
            == PartGui::ModelingResultOwnership::Body) {
            attempt.targetResultBody(*sweep, *resultOwner.body);
        }
        else {
            attempt.keepResultAtDocumentRoot(*sweep);
        }
        if (!replacedPresentations.empty()) {
            attempt.trackReplacedInputs(
                *sweep,
                replacedPresentations
            );
        }
        sweep->Sections.setValues(profiles);
        if (hasSubElement) {
            profileLinksProperty(*sweep).setValues(
                std::vector<App::DocumentObject*>(profiles),
                std::vector<std::string>(subElements)
            );
        }
        sweep->Spine.setValue(pathObject, std::vector<std::string>(pathSubElements));
        sweep->Solid.setValue(d->ui.checkSolid->isChecked());
        sweep->Frenet.setValue(d->ui.checkFrenet->isChecked());
        appDocument->recompute();

        const auto shape = sweep->Shape.getShape();
        if (!sweep->isValid() || shape.isNull() || !shape.isValid()) {
            const auto status = sweep->getStatusString();
            throw Base::RuntimeError(
                status && *status ? status : "Sweep did not produce valid geometry"
            );
        }
        if (sweep->Solid.getValue() && shape.countSubShapes(TopAbs_SOLID) == 0) {
            throw Base::RuntimeError("Sweep was asked for a solid but did not produce one");
        }
        recordAcceptedSweep(
            *sweep,
            profiles,
            subElements,
            hasSubElement,
            pathObject,
            pathSubElements,
            resultOwner.body
        );
        for (auto* object : objectsToHide) {
            Gui::cmdAppObjectHide(object);
        }
        attempt.commit();
        d->acceptedResult = sweep;
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
        QMessageBox::warning(this, tr("Input error"), tr("Unexpected sweep failure."));
        return false;
    }

    return true;
}

Part::Sweep* SweepWidget::lastAcceptedResult() const noexcept
{
    return d->acceptedResult;
}

bool SweepWidget::reject()
{
    return true;
}

void SweepWidget::onCurrentItemChanged(QTreeWidgetItem* current, QTreeWidgetItem* previous)
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

void SweepWidget::onButtonPathToggled(bool on)
{
    if (on) {
        QList<QWidget*> c = this->findChildren<QWidget*>();
        for (auto it : c) {
            it->setEnabled(false);
        }
        d->buttonText = d->ui.buttonPath->text();
        d->ui.buttonPath->setText(tr("Done"));
        d->ui.buttonPath->setEnabled(true);
        d->ui.labelPath->setText(
            tr("Select one or more connected edges in the 3D view and press 'Done'")
        );
        d->ui.labelPath->setEnabled(true);

        Gui::Selection().clearSelection();
        Gui::Selection().addSelectionGate(new Private::EdgeSelection());
    }
    else {
        QList<QWidget*> c = this->findChildren<QWidget*>();
        for (auto it : c) {
            it->setEnabled(true);
        }
        d->ui.buttonPath->setText(d->buttonText);
        d->ui.labelPath->clear();
        Gui::Selection().rmvSelectionGate();

        const auto selection = PartGui::getModelingShapeSelection(d->document.name.c_str());
        if (selection.size() == 1 && isPathValid(selection.front())) {
            auto* object = selection.front().getObject();
            d->path = ExactSelectionIdentity {
                exactObjectIdentity(object),
                selection.front().getSubNames(),
            };
            QString pathText = tr("Path: %1").arg(QString::fromUtf8(object->Label.getValue()));
            if (!d->path->subElements.empty()) {
                QStringList elements;
                for (const auto& subName : d->path->subElements) {
                    elements.push_back(QString::fromStdString(subName));
                }
                pathText += QStringLiteral(" (%1)").arg(elements.join(QStringLiteral(", ")));
            }
            d->ui.labelPath->setText(pathText);
        }
        else {
            d->path.reset();
            QMessageBox::critical(
                this,
                tr("Sweep Path"),
                tr("Select one connected edge or wire through the Sweep Path action.")
            );
            Gui::Selection().clearSelection();
        }
    }
}

void SweepWidget::changeEvent(QEvent* e)
{
    QWidget::changeEvent(e);
    if (e->type() == QEvent::LanguageChange) {
        d->ui.retranslateUi(this);
        d->ui.selector->setAvailableLabel(tr("Vertex/Wire"));
        d->ui.selector->setSelectedLabel(tr("Sweep"));
    }
}


/* TRANSLATOR PartGui::TaskSweep */

TaskSweep::TaskSweep()
    : label(nullptr)
{
    widget = new SweepWidget();
    addTaskBox(Gui::BitmapFactory().pixmap("Part_Sweep"), widget);
}

TaskSweep::~TaskSweep()
{
    delete label;
}

void TaskSweep::open()
{}

void TaskSweep::clicked(int id)
{
    if (id == QDialogButtonBox::Help) {
        QString help = QApplication::translate(
            "PartGui::TaskSweep",
            "Select at least 1 profile and an edge or wire\n"
            "in the 3D view for the sweep path."
        );
        if (!label) {
            label = new Gui::StatusWidget(widget);
            label->setStatusText(help);
        }

        label->show();
        QTimer::singleShot(3000, label, &Gui::StatusWidget::hide);
    }
}

bool TaskSweep::accept()
{
    if (!widget->accept()) {
        return false;
    }
    markCommandInteractionStateDurable({widget->lastAcceptedResult()});
    return true;
}

bool TaskSweep::reject()
{
    return widget->reject();
}

#include "moc_TaskSweep.cpp"
