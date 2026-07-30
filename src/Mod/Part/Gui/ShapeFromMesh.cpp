// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2021 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <sstream>

#include <QMessageBox>

#include <App/Application.h>
#include <App/ComplexGeoData.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <App/DocumentTimeline.h>
#include <App/GroupExtension.h>
#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/UnitsApi.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/WaitCursor.h>
#include <Mod/Part/App/PartFeature.h>

#include "ModelingSelection.h"
#include "ShapeFromMesh.h"
#include "ui_ShapeFromMesh.h"


using namespace PartGui;

namespace
{

bool hasMeshFacets(const App::DocumentObject* object)
{
    const auto* property = object ? object->getPropertyByName("Mesh") : nullptr;
    const auto* geometryProperty = property && property->isDerivedFrom<App::PropertyComplexGeoData>()
        ? static_cast<const App::PropertyComplexGeoData*>(property)
        : nullptr;
    const Data::ComplexGeoData* geometry = geometryProperty ? geometryProperty->getComplexData()
                                                            : nullptr;
    if (!geometry) {
        return false;
    }

    std::vector<Base::Vector3d> points;
    std::vector<Data::ComplexGeoData::Facet> facets;
    geometry->getFaces(points, facets, 0.0);
    return !facets.empty();
}

App::Property* ensureTimelineProperty(
    App::DocumentObject& object,
    const char* typeName,
    const char* propertyName,
    const char* description
)
{
    auto* property = object.getPropertyByName(propertyName);
    if (!property) {
        property = object.addDynamicProperty(
            typeName,
            propertyName,
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

App::DocumentObject* createMultiResultController(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs
)
{
    if (outputs.size() < 2) {
        throw Base::ValueError("A mesh conversion controller requires several outputs");
    }

    const std::string name = document.getUniqueObjectName("ShapeFromMeshResults");
    auto* controller = document.addObject("App::DocumentObjectGroup", name.c_str());
    auto* sourceProperty = controller ? dynamic_cast<App::PropertyLinkList*>(ensureTimelineProperty(
                                            *controller,
                                            "App::PropertyLinkList",
                                            "Sources",
                                            "Meshes converted by this operation"
                                        ))
                                      : nullptr;
    auto* operationKind = controller ? dynamic_cast<App::PropertyString*>(ensureTimelineProperty(
                                           *controller,
                                           "App::PropertyString",
                                           "OperationKind",
                                           "Native operation which produced these results"
                                       ))
                                     : nullptr;
    auto* group = controller ? controller->getExtensionByType<App::GroupExtension>() : nullptr;
    if (!controller || !sourceProperty || !operationKind || !group) {
        throw Base::RuntimeError("The multi-result mesh conversion controller is unavailable");
    }

    controller->Label.setValue("Converted Mesh Shapes");
    sourceProperty->setValues(sources);
    operationKind->setValue("Convert mesh to shape");
    for (auto* output : outputs) {
        if (!output || output->getDocument() != &document) {
            throw Base::ValueError("A mesh conversion output must belong to its document");
        }
    }
    const auto added = group->addObjects(outputs);
    if (added.size() != outputs.size()) {
        throw Base::RuntimeError("The mesh conversion controller could not own every result");
    }
    return controller;
}

}  // namespace

class ShapeFromMesh::SelectionState
{
public:
    explicit SelectionState(App::Document* targetDocument)
        : document(targetDocument)
    {
        if (!targetDocument) {
            return;
        }
        Base::Type meshType = Base::Type::fromName("Mesh::Feature");
        for (auto* object : Gui::Selection().getObjectsOfType(meshType)) {
            if (object && object->getDocument() == targetDocument
                && PartGui::isModelingObjectActive(object)) {
                meshes.emplace_back(object);
            }
        }
    }

    App::DocumentWeakPtrT document;
    std::vector<App::DocumentObjectWeakPtrT> meshes;
};

ShapeFromMesh::ShapeFromMesh(QWidget* parent, Qt::WindowFlags fl)
    : QDialog(parent, fl)
    , selectionState(std::make_unique<SelectionState>(App::GetApplication().getActiveDocument()))
    , ui(new Ui_ShapeFromMesh)
{
    ui->setupUi(this);
    ui->groupBoxSew->setChecked(false);

    double STD_OCC_TOLERANCE = 1e-6;

    int decimals = Base::UnitsApi::getDecimals();
    double tolerance_from_decimals = pow(10., -decimals);

    double minimal_tolerance = tolerance_from_decimals < STD_OCC_TOLERANCE ? STD_OCC_TOLERANCE
                                                                           : tolerance_from_decimals;
    ui->doubleSpinBox->setRange(minimal_tolerance, 10.0);
    ui->doubleSpinBox->setValue(0.1);
    ui->doubleSpinBox->setSingleStep(0.1);
    ui->doubleSpinBox->setDecimals(decimals);
}

ShapeFromMesh::~ShapeFromMesh() = default;

bool ShapeFromMesh::perform()
{
    double tolerance = ui->doubleSpinBox->value();
    bool sewShape = ui->groupBoxSew->isChecked();

    Gui::WaitCursor wc;

    std::vector<App::DocumentObject*> meshes;
    App::Document* document = selectionState ? *selectionState->document : nullptr;
    if (!document || selectionState->meshes.empty()
        || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction()) {
        return false;
    }
    meshes.reserve(selectionState->meshes.size());
    for (const auto& target : selectionState->meshes) {
        auto* mesh = target.get<App::DocumentObject>();
        if (!mesh || mesh->getDocument() != document
            || !PartGui::isModelingObjectActive(mesh)
            || !hasMeshFacets(mesh)) {
            return false;
        }
        meshes.push_back(mesh);
    }

    try {
        PartGui::ModelingTaskAttempt attempt(*document, QT_TRANSLATE_NOOP("Command", "Convert mesh"));
        Gui::doCommandT(Gui::Command::Doc, "import Part");
        std::vector<App::DocumentObject*> outputs;
        outputs.reserve(meshes.size());
        for (auto* mesh : meshes) {
            std::string name = document->getUniqueObjectName(mesh->getNameInDocument());

            const QString factory
                = QStringLiteral("App.getDocument('%1').addObject("
                                 "'Part::Feature','%2')")
                      .arg(QString::fromLatin1(document->getName()), QString::fromStdString(name));
            auto* result = Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *document,
                factory.toUtf8(),
                Part::Feature::getClassTypeId()
            );
            std::string partObject = App::DocumentObjectT(result).getObjectPython();
            std::string meshObject = App::DocumentObjectT(mesh).getObjectPython();

            Gui::doCommandT(Gui::Command::Doc, "__shape__ = Part.Shape()");
            Gui::doCommandT(
                Gui::Command::Doc,
                "__shape__.makeShapeFromMesh(%s.Mesh.Topology, %f, %s)",
                meshObject,
                tolerance,
                (sewShape ? "True" : "False")
            );
            Gui::doCommandT(Gui::Command::Doc, partObject + ".Shape = __shape__");
            Gui::doCommandT(Gui::Command::Doc, partObject + ".purgeTouched()");
            Gui::doCommandT(Gui::Command::Doc, "del __shape__");

            auto* partResult = freecad_cast<Part::Feature*>(result);
            if (!partResult || partResult->Shape.getShape().isNull()
                || !partResult->Shape.getShape().isValid()) {
                throw Base::RuntimeError("Mesh conversion produced an empty shape");
            }
            outputs.push_back(result);
        }
        App::DocumentObject* operation = nullptr;
        if (outputs.size() == 1) {
            attempt.trackCreatedObject(*outputs.front());
            attempt.keepResultAtDocumentRoot(*outputs.front());
            operation = outputs.front();
            auto* timeline = App::DocumentTimeline::get(document);
            if (!timeline) {
                throw Base::RuntimeError("Mesh conversion has no native document timeline");
            }
            timeline->publishProvisionalOperationBlock(outputs.front(), {});
        }
        else if (outputs.size() > 1) {
            auto* controller = createMultiResultController(*document, meshes, outputs);
            attempt.trackCreatedObject(*controller);
            attempt.keepResultAtDocumentRoot(*controller);
            operation = controller;
            auto* timeline = App::DocumentTimeline::get(document);
            if (!timeline) {
                throw Base::RuntimeError("Mesh conversion has no native document timeline");
            }
            timeline->publishProvisionalOperationBlock(controller, outputs);
        }
        if (!operation) {
            throw Base::RuntimeError("Mesh conversion produced no result");
        }

        std::vector<App::DocumentObject*> replacedMeshes;
        replacedMeshes.reserve(meshes.size());
        for (auto* mesh : meshes) {
            if (mesh->Visibility.getValue()) {
                replacedMeshes.push_back(mesh);
            }
        }
        if (!replacedMeshes.empty()) {
            attempt.trackReplacedInputs(*operation, replacedMeshes);
            for (auto* mesh : replacedMeshes) {
                FCMD_OBJ_HIDE(mesh);
            }
        }
        document->recompute();
        attempt.commit();
        return true;
    }
    catch (const Base::Exception& error) {
        error.reportException();
        QMessageBox::warning(this, tr("Shape From Mesh"), QString::fromUtf8(error.what()));
        return false;
    }
    catch (...) {
        Base::Console().error("Mesh conversion failed because of an unknown error\n");
        QMessageBox::warning(
            this,
            tr("Shape From Mesh"),
            tr("The selected mesh could not be converted to a valid shape.")
        );
        return false;
    }
}

void ShapeFromMesh::accept()
{
    if (perform()) {
        QDialog::accept();
    }
}

#include "moc_ShapeFromMesh.cpp"
