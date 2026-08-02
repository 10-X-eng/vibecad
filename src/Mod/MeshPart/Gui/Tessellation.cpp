// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <sstream>

#include <QMessageBox>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Stream.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/ViewProvider.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ParametricMeshFilter.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/Gui/ViewProvider.h>

#include "../App/FeatureMeshPartOperations.h"
#include "Tessellation.h"
#include "ui_Tessellation.h"


using namespace MeshPartGui;

class Tessellation::SelectionState
{
public:
    struct Target
    {
        Target(App::DocumentObject* object, std::string subName)
            : object(object)
            , subName(std::move(subName))
        {}

        App::DocumentObjectWeakPtrT object;
        std::string subName;
        Part::TopoShape sourceShape;
    };

    explicit SelectionState(App::Document* targetDocument)
        : document(targetDocument)
    {
        if (!targetDocument) {
            return;
        }
        for (const auto& selected : Gui::Selection().getSelection("*", Gui::ResolveMode::NoResolve)) {
            if (selected.pObject && selected.pObject->getDocument() == targetDocument
                && MeshGui::isNativeMeshInputActive(selected.pObject)) {
                targets.emplace_back(selected.pObject, selected.SubName);
            }
        }
    }

    App::DocumentWeakPtrT document;
    std::vector<Target> targets;
};

/* TRANSLATOR MeshPartGui::Tessellation */

Tessellation::Tessellation(QWidget* parent)
    : QWidget(parent)
    , selectionState(std::make_unique<SelectionState>(App::GetApplication().getActiveDocument()))
    , ui(new Ui_Tessellation)
{
    ui->setupUi(this);
    gmsh = new Mesh2ShapeGmsh(this);
    setupConnections();

    ui->stackedWidget->addTab(gmsh, tr("Gmsh"));

    ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Mesh/Meshing/Standard"
    );
    double value = ui->spinSurfaceDeviation->value().getValue();
    value = handle->GetFloat("LinearDeflection", value);
    double angle = ui->spinAngularDeviation->value().getValue();
    angle = handle->GetFloat("AngularDeflection", angle);
    bool relative = ui->relativeDeviation->isChecked();
    relative = handle->GetBool("RelativeLinearDeflection", relative);
    ui->relativeDeviation->setChecked(relative);

    ui->spinSurfaceDeviation->setMaximum(std::numeric_limits<int>::max());
    ui->spinSurfaceDeviation->setValue(value);
    ui->spinAngularDeviation->setValue(angle);

    ui->spinMaximumEdgeLength->setRange(0, std::numeric_limits<int>::max());

    ui->comboFineness->setCurrentIndex(2);
    onComboFinenessCurrentIndexChanged(2);

#if !defined(HAVE_MEFISTO)
    ui->stackedWidget->setTabEnabled(Mefisto, false);
#endif
#if !defined(HAVE_NETGEN)
    ui->stackedWidget->setTabEnabled(Netgen, false);
#endif

    Gui::Command::doCommand(Gui::Command::Doc, "import Mesh, Part, PartGui");
    try {
        Gui::Command::doCommand(Gui::Command::Doc, "import MeshPart");
    }
    catch (...) {
        ui->stackedWidget->setTabEnabled(Mefisto, false);
        ui->stackedWidget->setTabEnabled(Netgen, false);
    }
}

Tessellation::~Tessellation() = default;

void Tessellation::setupConnections()
{
    connect(gmsh, &Mesh2ShapeGmsh::processed, this, &Tessellation::gmshProcessed);
    connect(
        ui->estimateMaximumEdgeLength,
        &QPushButton::clicked,
        this,
        &Tessellation::onEstimateMaximumEdgeLengthClicked
    );
    connect(
        ui->comboFineness,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        &Tessellation::onComboFinenessCurrentIndexChanged
    );
    connect(ui->checkSecondOrder, &QCheckBox::toggled, this, &Tessellation::onCheckSecondOrderToggled);
    connect(ui->checkQuadDominated, &QCheckBox::toggled, this, &Tessellation::onCheckQuadDominatedToggled);
}

void Tessellation::meshingMethod(int id)
{
    ui->stackedWidget->setCurrentIndex(id);
}

void Tessellation::onComboFinenessCurrentIndexChanged(int index)
{
    // NOLINTBEGIN
    if (index == 5) {
        ui->doubleGrading->setEnabled(true);
        ui->spinEdgeElements->setEnabled(true);
        ui->spinCurvatureElements->setEnabled(true);
    }
    else {
        ui->doubleGrading->setEnabled(false);
        ui->spinEdgeElements->setEnabled(false);
        ui->spinCurvatureElements->setEnabled(false);
    }

    switch (index) {
        case VeryCoarse:
            ui->doubleGrading->setValue(0.7);
            ui->spinEdgeElements->setValue(0.3);
            ui->spinCurvatureElements->setValue(1.0);
            break;
        case Coarse:
            ui->doubleGrading->setValue(0.5);
            ui->spinEdgeElements->setValue(0.5);
            ui->spinCurvatureElements->setValue(1.5);
            break;
        case Moderate:
            ui->doubleGrading->setValue(0.3);
            ui->spinEdgeElements->setValue(1.0);
            ui->spinCurvatureElements->setValue(2.0);
            break;
        case Fine:
            ui->doubleGrading->setValue(0.2);
            ui->spinEdgeElements->setValue(2.0);
            ui->spinCurvatureElements->setValue(3.0);
            break;
        case VeryFine:
            ui->doubleGrading->setValue(0.1);
            ui->spinEdgeElements->setValue(3.0);
            ui->spinCurvatureElements->setValue(5.0);
            break;
        default:
            break;
    }
    // NOLINTEND
}

void Tessellation::onCheckSecondOrderToggled(bool on)
{
    if (on) {
        ui->checkQuadDominated->setChecked(false);
    }
}

void Tessellation::onCheckQuadDominatedToggled(bool on)
{
    if (on) {
        ui->checkSecondOrder->setChecked(false);
    }
}

void Tessellation::gmshProcessed()
{
    bool doClose = !ui->checkBoxDontQuit->isChecked();
    if (doClose) {
        Gui::Control().reject();
    }
}

void Tessellation::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        int index = ui->comboFineness->currentIndex();
        ui->retranslateUi(this);
        ui->comboFineness->setCurrentIndex(index);
    }
    QWidget::changeEvent(e);
}

void Tessellation::onEstimateMaximumEdgeLengthClicked()
{
    App::Document* targetDocument = selectionState ? *selectionState->document : nullptr;
    if (!targetDocument) {
        return;
    }

    Gui::Document* targetGui = Gui::Application::Instance->getDocument(targetDocument);
    if (!targetGui) {
        return;
    }

    double edgeLen = 0;
    for (const auto& target : selectionState->targets) {
        auto* object = target.object.get<App::DocumentObject>();
        if (!object || object->getDocument() != targetDocument
            || !MeshGui::isNativeMeshInputActive(object)) {
            continue;
        }
        auto shape = Part::Feature::getTopoShape(
            object,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
            target.subName.c_str()
        );
        if (shape.hasSubShape(TopAbs_FACE)) {
            Base::BoundBox3d bbox = shape.getBoundBox();
            edgeLen = std::max<double>(edgeLen, bbox.LengthX());
            edgeLen = std::max<double>(edgeLen, bbox.LengthY());
            edgeLen = std::max<double>(edgeLen, bbox.LengthZ());
        }
    }

    ui->spinMaximumEdgeLength->setValue(edgeLen / 10);  // NOLINT
}

bool Tessellation::accept()
{
    std::list<App::SubObjectT> shapeObjects;
    App::Document* targetDocument = selectionState ? *selectionState->document : nullptr;
    if (!targetDocument) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("The document selected for meshing is no longer open.")
        );
        return false;
    }

    Gui::Document* targetGui = Gui::Application::Instance->getDocument(targetDocument);
    if (!targetGui) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("The document selected for meshing is no longer open.")
        );
        return false;
    }

    this->document = QString::fromUtf8(targetDocument->getName());
    if (!MeshGui::hasCleanNativeMutationBoundary(targetDocument)) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("Finish the current document operation before meshing.")
        );
        return false;
    }

    bool bodyWithNoTip = false;
    bool partWithNoFace = false;
    bool missingSelection = false;
    bool inactiveSelection = false;
    bool invalidSelection = false;
    for (const auto& target : selectionState->targets) {
        auto* object = target.object.get<App::DocumentObject>();
        if (!object || object->getDocument() != targetDocument) {
            missingSelection = true;
            continue;
        }
        if (!MeshGui::isNativeMeshInputActive(object)) {
            inactiveSelection = true;
            continue;
        }
        auto shape = Part::Feature::getTopoShape(
            object,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
            target.subName.c_str()
        );
        if (shape.hasSubShape(TopAbs_FACE)) {
            shapeObjects.emplace_back(object, target.subName.c_str());
        }
        else {
            invalidSelection = true;
            if (object->isDerivedFrom<Part::Feature>()) {
                partWithNoFace = true;
            }
            if (auto body = dynamic_cast<Part::BodyBase*>(object)) {
                if (!body->Tip.getValue()) {
                    bodyWithNoTip = true;
                }
            }
        }
    }

    if (missingSelection) {
        QMessageBox::critical(this, windowTitle(), tr("A shape selected for meshing no longer exists."));
        return false;
    }

    if (inactiveSelection) {
        QMessageBox::critical(
            this,
            windowTitle(),
            tr("A shape selected for meshing is no longer active in History.")
        );
        return false;
    }

    if (invalidSelection || shapeObjects.empty()) {
        if (bodyWithNoTip) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Error: body without a tip selected.\n"
                   "Either set the tip of the body or select a different shape.")
            );
        }
        else if (partWithNoFace) {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Error: shape without faces selected.\n"
                   "Select a different shape.")
            );
        }
        else {
            QMessageBox::critical(
                this,
                windowTitle(),
                tr("Every selected object must contain faces that can be meshed.")
            );
        }
        return false;
    }

    bool doClose = !ui->checkBoxDontQuit->isChecked();
    int method = ui->stackedWidget->currentIndex();

    // For Gmsh the workflow is very different because it uses an executable
    // and therefore things are asynchronous
    if (method == Gmsh) {
        gmsh->process(targetDocument, shapeObjects);
        return false;
    }

    return processAndCommit(method, targetDocument, shapeObjects) && doClose;
}

void Tessellation::reject()
{
    if (gmsh) {
        gmsh->reject();
    }
}

void Tessellation::process(int method, App::Document* doc, const std::list<App::SubObjectT>& shapeObjects)
{
    (void)processAndCommit(method, doc, shapeObjects);
}

bool Tessellation::processAndCommit(
    int method,
    App::Document* doc,
    const std::list<App::SubObjectT>& shapeObjects
)
{
    try {
        if (!doc || shapeObjects.empty() || !MeshGui::hasCleanNativeMutationBoundary(doc)) {
            return false;
        }
        for (const auto& info : shapeObjects) {
            auto* object = info.getObject();
            if (!object || object->getDocument() != doc || !MeshGui::isNativeMeshInputActive(object)) {
                return false;
            }
        }

        Gui::WaitCursor wc;

        saveParameters(method);

        Gui::ExactTransaction transaction(*doc, "Meshing");
        std::vector<App::DocumentObject*> sources;
        std::vector<App::DocumentObject*> outputs;
        std::vector<std::pair<App::DocumentObject*, MeshPart::MeshFromShape*>> configuredResults;
        sources.reserve(shapeObjects.size());
        outputs.reserve(shapeObjects.size());
        configuredResults.reserve(shapeObjects.size());
        for (auto& info : shapeObjects) {
            auto obj = info.getObject();
            if (!obj || obj->getDocument() != doc || !MeshGui::isNativeMeshInputActive(obj)) {
                throw Base::RuntimeError("A shape selected for meshing no longer exists");
            }
            auto sobj = obj->getSubObject(info.getSubName().c_str());
            if (!sobj) {
                throw Base::RuntimeError("A shape selected for meshing no longer exists");
            }
            sobj = sobj->getLinkedObject(true);
            if (!sobj || !MeshGui::isNativeMeshInputActive(sobj)) {
                throw Base::RuntimeError("A linked shape selected for meshing no longer exists");
            }

            auto* result = doc->addObject<MeshPart::MeshFromShape>("Mesh");
            const std::string subName = info.getSubName();
            if (subName.empty()) {
                result->Source.setValue(obj);
            }
            else {
                result->Source.setValue(obj, std::vector<std::string> {subName});
            }
            result->Method.setValue(method);
            if (method == Standard) {
                result->LinearDeflection.setValue(ui->spinSurfaceDeviation->value().getValue());
                result->AngularDeflection.setValue(
                    Base::toRadians<double>(ui->spinAngularDeviation->value().getValue())
                );
                result->Relative.setValue(ui->relativeDeviation->isChecked());
                result->Segments.setValue(ui->meshShapeColors->isChecked());
            }
            else if (method == Mefisto) {
                result->MaximumEdgeLength.setValue(
                    ui->spinMaximumEdgeLength->isEnabled()
                        ? ui->spinMaximumEdgeLength->value().getValue()
                        : 0.0
                );
            }
            else if (method == Netgen) {
                result->Fineness.setValue(ui->comboFineness->currentIndex());
                result->GrowthRate.setValue(ui->doubleGrading->value());
                result->SegmentsPerEdge.setValue(ui->spinEdgeElements->value());
                result->SegmentsPerRadius.setValue(ui->spinCurvatureElements->value());
                result->SecondOrder.setValue(ui->checkSecondOrder->isChecked());
                result->Optimize.setValue(ui->checkOptimizeSurface->isChecked());
                result->QuadDominated.setValue(ui->checkQuadDominated->isChecked());
            }
            result->Label.setValue(sobj->Label.getStrValue() + " (Meshed)");
            sources.push_back(obj);
            outputs.push_back(result);
            configuredResults.emplace_back(sobj, result);
        }
        doc->recompute();
        for (const auto& [source, result] : configuredResults) {
            if (!result || result->Mesh.getValue().countFacets() == 0 || result->isError()) {
                throw Base::RuntimeError(
                    result && result->isError() ? result->getStatusString()
                                                : "Meshing produced an empty mesh"
                );
            }
            setFaceColors(method, doc, source, result);
        }
        MeshGui::createSourcePreservingOutputGroup(
            *doc,
            sources,
            outputs,
            "MeshedShapes",
            "Meshed Shapes",
            "Mesh from shape"
        );
        if (!transaction.commit()) {
            return false;
        }
        return true;
    }
    catch (const Base::Exception& e) {
        Base::Console().error(e.what());
        return false;
    }
    catch (...) {
        Base::Console().error("Meshing failed because of an unknown error\n");
        return false;
    }
}

void Tessellation::saveParameters(int method)
{
    if (method == Standard) {
        ParameterGrp::handle handle = App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Mesh/Meshing/Standard"
        );
        double value = ui->spinSurfaceDeviation->value().getValue();
        handle->SetFloat("LinearDeflection", value);
        double angle = ui->spinAngularDeviation->value().getValue();
        handle->SetFloat("AngularDeflection", angle);
        bool relative = ui->relativeDeviation->isChecked();
        handle->SetBool("RelativeLinearDeflection", relative);
    }
}

void Tessellation::setFaceColors(int method, App::Document* doc, App::DocumentObject* obj)
{
    auto* result = doc ? freecad_cast<Mesh::Feature*>(doc->getActiveObject()) : nullptr;
    setFaceColors(method, doc, obj, result);
}

void Tessellation::setFaceColors(
    int method,
    App::Document* doc,
    App::DocumentObject* obj,
    Mesh::Feature* result
)
{
    // if Standard mesher is used and face colors should be applied
    if (method == Standard && doc && obj && result) {
        if (ui->meshShapeColors->isChecked()) {
            Gui::ViewProvider* vpm = Gui::Application::Instance->getViewProvider(result);
            auto vpmesh = dynamic_cast<MeshGui::ViewProviderMesh*>(vpm);

            auto svp = freecad_cast<PartGui::ViewProviderPartExt*>(
                Gui::Application::Instance->getViewProvider(obj)
            );
            if (vpmesh && svp) {
                std::vector<Base::Color> diff_col = svp->ShapeAppearance.getDiffuseColors();
                if (ui->groupsFaceColors->isChecked()) {
                    diff_col = getUniqueColors(diff_col);
                }

                vpmesh->highlightSegments(diff_col);
                addFaceColors(vpmesh->getObject<Mesh::Feature>(), diff_col);
            }
        }
    }
}

void Tessellation::addFaceColors(Mesh::Feature* mesh, const std::vector<Base::Color>& colorPerSegm)
{
    const Mesh::MeshObject& kernel = mesh->Mesh.getValue();
    unsigned long numSegm = kernel.countSegments();
    if (numSegm > 0 && numSegm == colorPerSegm.size()) {
        unsigned long uCtFacets = kernel.countFacets();
        std::vector<Base::Color> colorPerFace(uCtFacets);
        for (unsigned long i = 0; i < numSegm; i++) {
            Base::Color segmColor = colorPerSegm[i];
            std::vector<Mesh::FacetIndex> segm = kernel.getSegment(i).getIndices();
            for (Mesh::FacetIndex it : segm) {
                colorPerFace[it] = segmColor;
            }
        }

        auto typeId = App::PropertyColorList::getClassTypeId();
        if (auto prop = dynamic_cast<App::PropertyColorList*>(
                mesh->addDynamicProperty(typeId.getName(), "FaceColors")
            )) {
            prop->setValues(colorPerFace);
        }
    }
}

std::vector<Base::Color> Tessellation::getUniqueColors(const std::vector<Base::Color>& colors) const
{
    // unique colors
    std::set<uint32_t> col_set;
    for (const auto& it : colors) {
        col_set.insert(it.getPackedValue());
    }

    std::vector<Base::Color> unique;
    unique.reserve(col_set.size());
    for (const auto& it : col_set) {
        unique.emplace_back(it);
    }
    return unique;
}

QString Tessellation::getMeshingParameters(int method, App::DocumentObject* obj) const
{
    QString param;
    if (method == Standard) {
        param = getStandardParameters(obj);
    }
    else if (method == Mefisto) {
        param = getMefistoParameters();
    }
    else if (method == Netgen) {
        param = getNetgenParameters();
    }

    return param;
}

QString Tessellation::getStandardParameters(App::DocumentObject* obj) const
{
    double devFace = ui->spinSurfaceDeviation->value().getValue();
    double devAngle = ui->spinAngularDeviation->value().getValue();
    devAngle = Base::toRadians<double>(devAngle);
    bool relative = ui->relativeDeviation->isChecked();

    QString param;
    param = QStringLiteral("Shape=__shape__, "
                           "LinearDeflection=%1, "
                           "AngularDeflection=%2, "
                           "Relative=%3")
                .arg(devFace)
                .arg(devAngle)
                .arg(relative ? QStringLiteral("True") : QStringLiteral("False"));
    if (ui->meshShapeColors->isChecked()) {
        param += QStringLiteral(",Segments=True");
    }

    auto svp = freecad_cast<PartGui::ViewProviderPartExt*>(
        Gui::Application::Instance->getViewProvider(obj)
    );
    if (ui->groupsFaceColors->isChecked() && svp) {
        // TODO: currently, we can only retrieve part feature
        // color. The problem is that if the feature is linked,
        // there are potentially many places where the color can
        // get overridden.
        //
        // With topo naming feature merged, it will be possible to
        // infer more accurate colors from just the shape names,
        // with static function,
        //
        // PartGui::ViewProviderPartExt::getShapeColors().
        //
        param += QStringLiteral(",GroupColors=Gui.getDocument('%1').getObject('%2').DiffuseColor")
                     .arg(
                         QString::fromUtf8(obj->getDocument()->getName()),
                         QString::fromUtf8(obj->getNameInDocument())
                     );
    }

    return param;
}

QString Tessellation::getMefistoParameters() const
{
    double maxEdge = ui->spinMaximumEdgeLength->value().getValue();
    if (!ui->spinMaximumEdgeLength->isEnabled()) {
        maxEdge = 0;
    }
    return QStringLiteral("Shape=__shape__,MaxLength=%1").arg(maxEdge);
}

QString Tessellation::getNetgenParameters() const
{
    QString param;
    int fineness = ui->comboFineness->currentIndex();
    double growthRate = ui->doubleGrading->value();
    double nbSegPerEdge = ui->spinEdgeElements->value();
    double nbSegPerRadius = ui->spinCurvatureElements->value();
    bool secondOrder = ui->checkSecondOrder->isChecked();
    bool optimize = ui->checkOptimizeSurface->isChecked();
    bool allowquad = ui->checkQuadDominated->isChecked();
    if (fineness <= int(VeryFine)) {
        param = QStringLiteral("Shape=__shape__,"
                               "Fineness=%1,SecondOrder=%2,Optimize=%3,AllowQuad=%4")
                    .arg(fineness)
                    .arg(secondOrder ? 1 : 0)
                    .arg(optimize ? 1 : 0)
                    .arg(allowquad ? 1 : 0);
    }
    else {
        param = QStringLiteral("Shape=__shape__,"
                               "GrowthRate=%1,SegPerEdge=%2,SegPerRadius=%3,SecondOrder=%4,"
                               "Optimize=%5,AllowQuad=%6")
                    .arg(growthRate)
                    .arg(nbSegPerEdge)
                    .arg(nbSegPerRadius)
                    .arg(secondOrder ? 1 : 0)
                    .arg(optimize ? 1 : 0)
                    .arg(allowquad ? 1 : 0);
    }

    return param;
}

// ---------------------------------------

class Mesh2ShapeGmsh::Private
{
public:
    struct Target
    {
        Target(App::DocumentObject* object, std::string subName)
            : object(object)
            , subName(std::move(subName))
        {}

        App::DocumentObjectWeakPtrT object;
        std::string subName;
        Part::TopoShape sourceShape;
    };

    struct CompletedMesh
    {
        CompletedMesh(
            std::string label,
            const MeshCore::MeshKernel& kernel,
            App::DocumentObject* object,
            std::string subName,
            const Part::TopoShape& sourceShape
        )
            : label(std::move(label))
            , kernel(kernel)
            , object(object)
            , subName(std::move(subName))
            , sourceShape(sourceShape)
        {}

        std::string label;
        MeshCore::MeshKernel kernel;
        App::DocumentObjectWeakPtrT object;
        std::string subName;
        Part::TopoShape sourceShape;
    };

    std::string label;
    std::vector<Target> targets;
    std::unique_ptr<App::DocumentWeakPtrT> doc;
    std::string cadFile;
    std::string stlFile;
    std::string geoFile;
    std::vector<CompletedMesh> completedMeshes;
};

Mesh2ShapeGmsh::Mesh2ShapeGmsh(QWidget* parent, Qt::WindowFlags fl)
    : GmshWidget(parent, fl)
    , d(new Private())
{
    d->cadFile = App::Application::getTempFileName() + "mesh.brep";
    d->stlFile = App::Application::getTempFileName() + "mesh.stl";
    d->geoFile = App::Application::getTempFileName() + "mesh.geo";
}

Mesh2ShapeGmsh::~Mesh2ShapeGmsh()
{
    Base::FileInfo(d->stlFile).deleteFile();
    Base::FileInfo(d->geoFile).deleteFile();
    Base::FileInfo(d->cadFile).deleteFile();
}

void Mesh2ShapeGmsh::process(App::Document* doc, const std::list<App::SubObjectT>& objs)
{
    d->doc = std::make_unique<App::DocumentWeakPtrT>(doc);
    d->targets.clear();
    d->targets.reserve(objs.size());
    for (const auto& target : objs) {
        auto* object = target.getObject();
        if (!object || object->getDocument() != doc || !MeshGui::isNativeMeshInputActive(object)) {
            d->targets.clear();
            d->doc.reset();
            return;
        }
        d->targets.emplace_back(object, target.getSubName());
    }
    d->completedMeshes.clear();
    if (!d->targets.empty()) {
        accept();
    }
}

void Mesh2ShapeGmsh::reject()
{
    MeshGui::GmshWidget::reject();
    Base::FileInfo(d->cadFile).deleteFile();
    Base::FileInfo(d->stlFile).deleteFile();
    Base::FileInfo(d->geoFile).deleteFile();
    d->targets.clear();
    d->completedMeshes.clear();
    d->doc.reset();
}

bool Mesh2ShapeGmsh::writeProject(QString& inpFile, QString& outFile)
{
    App::Document* document = d->doc ? **d->doc : nullptr;
    if (!d->targets.empty()) {
        auto& target = d->targets.front();
        App::DocumentObject* part = target.object.get<App::DocumentObject>();
        if (part && part->getDocument() == document && MeshGui::isNativeMeshInputActive(part)) {
            Part::TopoShape shape = Part::Feature::getTopoShape(
                part,
                Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
                target.subName.c_str()
            );
            if (!shape.hasSubShape(TopAbs_FACE)) {
                d->targets.clear();
                d->completedMeshes.clear();
                Base::Console().error("A shape selected for Gmsh no longer has any faces\n");
                return false;
            }
            target.sourceShape = shape;
            shape.exportBrep(d->cadFile.c_str());
            d->label = part->Label.getStrValue() + " (Meshed)";

            // Parameters
            int algorithm = meshingAlgorithm();
            double maxSize = getMaxSize();
            if (maxSize == 0.0) {
                maxSize = 1.0e22;
            }
            double minSize = getMinSize();

            // Gmsh geo file
            Base::FileInfo geo(d->geoFile);
            Base::ofstream geoOut(geo, std::ios::out);
            if (!geoOut.is_open()) {
                Base::Console().error("The Gmsh project file could not be created\n");
                d->targets.clear();
                d->completedMeshes.clear();
                return false;
            }
            geoOut << "// geo file for meshing with Gmsh meshing software created by FreeCAD\n"
                   << "// open brep geometry\n"
                   << "Merge \"" << d->cadFile << "\";\n\n"
                   << "// Characteristic Length\n"
                   << "// no boundary layer settings for this mesh\n"
                   << "// min, max Characteristic Length\n"
                   << "Mesh.CharacteristicLengthMax = " << maxSize << ";\n"
                   << "Mesh.CharacteristicLengthMin = " << minSize << ";\n\n"
                   << "// optimize the mesh\n"
                   << "Mesh.Optimize = 1;\n"
                   << "Mesh.OptimizeNetgen = 0;\n"
                   << "// High-order meshes optimization (0=none, 1=optimization, "
                      "2=elastic+optimization, 3=elastic, 4=fast curving)\n"
                   << "Mesh.HighOrderOptimize = 0;\n\n"
                   << "// mesh order\n"
                   << "Mesh.ElementOrder = 2;\n"
                   << "// Second order nodes are created by linear interpolation instead by "
                      "curvilinear\n"
                   << "Mesh.SecondOrderLinear = 1;\n\n"
                   << "// mesh algorithm, only a few algorithms are usable with 3D boundary layer "
                      "generation\n"
                   << "// 2D mesh algorithm (1=MeshAdapt, 2=Automatic, 5=Delaunay, 6=Frontal, "
                      "7=BAMG, 8=DelQuad, 9=Packing of Parallelograms, 11=Quasi-structured Quad)\n"
                   << "Mesh.Algorithm = " << algorithm << ";\n"
                   << "// 3D mesh algorithm (1=Delaunay, 2=New Delaunay, 4=Frontal, 7=MMG3D, "
                      "9=R-tree, 10=HTX)\n"
                   << "Mesh.Algorithm3D = 1;\n\n"
                   << "// meshing\n"
                   << "// set geometrical tolerance (also used for merging nodes)\n"
                   << "Geometry.Tolerance = 1e-06;\n"
                   << "Mesh  2;\n"
                   << "Coherence Mesh; // Remove duplicate vertices\n";
            geoOut.close();

            inpFile = QString::fromUtf8(d->geoFile.c_str());
            outFile = QString::fromUtf8(d->stlFile.c_str());

            return true;
        }
        d->targets.clear();
        d->completedMeshes.clear();
        Base::Console().error("A shape selected for Gmsh no longer exists\n");
        return false;
    }
    else {
        bool committed = false;
        if (document && !d->completedMeshes.empty()) {
            bool sourcesCurrent = true;
            for (const auto& completed : d->completedMeshes) {
                auto* object = completed.object.get<App::DocumentObject>();
                if (!object || object->getDocument() != document
                    || !MeshGui::isNativeMeshInputActive(object)) {
                    sourcesCurrent = false;
                    break;
                }
                const Part::TopoShape current = Part::Feature::getTopoShape(
                    object,
                    Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
                    completed.subName.c_str()
                );
                if (!current.hasSubShape(TopAbs_FACE) || !(current == completed.sourceShape)) {
                    sourcesCurrent = false;
                    break;
                }
            }
            if (!sourcesCurrent) {
                Base::Console().warning("Gmsh results were discarded because a selected "
                                        "shape changed while meshing\n");
            }
            else if (!MeshGui::hasCleanNativeMutationBoundary(document)) {
                Base::Console().warning("Gmsh results were not applied because another "
                                        "document operation is in progress\n");
            }
            else {
                try {
                    Gui::ExactTransaction transaction(*document, "Meshing");
                    std::vector<App::DocumentObject*> sources;
                    std::vector<App::DocumentObject*> outputs;
                    sources.reserve(d->completedMeshes.size());
                    outputs.reserve(d->completedMeshes.size());
                    for (const auto& completed : d->completedMeshes) {
                        auto* source = completed.object.get<App::DocumentObject>();
                        auto* feature = document->addObject<MeshPart::MeshFromShape>("Mesh");
                        feature->Label.setValue(completed.label);
                        if (completed.subName.empty()) {
                            feature->Source.setValue(source);
                        }
                        else {
                            feature->Source.setValue(
                                source,
                                std::vector<std::string> {completed.subName}
                            );
                        }
                        feature->Method.setValue(
                            static_cast<long>(MeshPart::MeshFromShape::MeshingMethod::Gmsh)
                        );
                        feature->GmshAlgorithm.setValue(meshingAlgorithm());
                        feature->GmshMinimumSize.setValue(getMinSize());
                        feature->GmshMaximumSize.setValue(getMaxSize());
                        feature->GmshGeometryTolerance.setValue(1.0e-6);
                        feature->GmshElementOrder.setValue(2);
                        feature->GmshOptimize.setValue(true);
                        feature->GmshExecutable.setValue(executablePath().toStdString());
                        std::ostringstream sourceBrep;
                        completed.sourceShape.exportBrep(sourceBrep);
                        Mesh::MeshObject accepted(completed.kernel);
                        feature->CachedGmshSourceBrep.setValue(sourceBrep.str());
                        feature->CachedGmshResult.setValue(accepted);
                        feature->Mesh.setValue(accepted);
                        feature->purgeTouched();
                        sources.push_back(source);
                        outputs.push_back(feature);
                    }
                    MeshGui::createSourcePreservingOutputGroup(
                        *document,
                        sources,
                        outputs,
                        "MeshedShapes",
                        "Meshed Shapes",
                        "Mesh from shape"
                    );
                    if (!transaction.commit()) {
                        Base::Console().error("Gmsh meshes could not be committed\n");
                    }
                    else {
                        committed = true;
                    }
                }
                catch (const Base::Exception& error) {
                    Base::Console().error("Gmsh meshes were not applied: %s\n", error.what());
                }
                catch (...) {
                    Base::Console().error("Gmsh meshes were not applied because of an "
                                          "unknown error\n");
                }
            }
        }
        d->completedMeshes.clear();
        if (committed) {
            Q_EMIT processed();
        }
    }

    return false;
}

bool Mesh2ShapeGmsh::loadOutput()
{
    App::Document* doc = d->doc ? **d->doc : nullptr;
    if (!doc || d->targets.empty()) {
        return false;
    }

    // Now read-in the mesh
    Base::FileInfo stl(d->stlFile);
    Base::FileInfo geo(d->geoFile);
    Base::FileInfo cad(d->cadFile);

    auto& target = d->targets.front();
    auto* object = target.object.get<App::DocumentObject>();
    if (!object || object->getDocument() != doc || !MeshGui::isNativeMeshInputActive(object)) {
        Base::Console().error("A shape selected for Gmsh no longer exists\n");
        d->targets.clear();
        d->completedMeshes.clear();
        stl.deleteFile();
        geo.deleteFile();
        cad.deleteFile();
        return false;
    }
    const Part::TopoShape current = Part::Feature::getTopoShape(
        object,
        Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
        target.subName.c_str()
    );
    if (!current.hasSubShape(TopAbs_FACE) || !(current == target.sourceShape)) {
        Base::Console().error("A shape selected for Gmsh changed while meshing\n");
        d->targets.clear();
        d->completedMeshes.clear();
        stl.deleteFile();
        geo.deleteFile();
        cad.deleteFile();
        return false;
    }

    Mesh::MeshObject kernel;
    try {
        MeshCore::MeshInput input(kernel.getKernel());
        Base::ifstream stlIn(stl, std::ios::in | std::ios::binary);
        if (!stlIn.is_open()) {
            throw Base::RuntimeError("Gmsh did not create a readable mesh file");
        }
        input.LoadBinarySTL(stlIn);
        stlIn.close();
        kernel.harmonizeNormals();
        if (kernel.countFacets() == 0) {
            throw Base::RuntimeError("Gmsh produced an empty mesh");
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Gmsh output was not accepted: %s\n", error.what());
        stl.deleteFile();
        geo.deleteFile();
        cad.deleteFile();
        return false;
    }
    catch (...) {
        Base::Console().error("Gmsh output was not accepted because it could not be "
                              "read\n");
        stl.deleteFile();
        geo.deleteFile();
        cad.deleteFile();
        return false;
    }
    d->completedMeshes.emplace_back(
        d->label,
        kernel.getKernel(),
        target.object.get<App::DocumentObject>(),
        target.subName,
        target.sourceShape
    );
    d->targets.erase(d->targets.begin());
    stl.deleteFile();
    geo.deleteFile();
    cad.deleteFile();

    // process next object
    accept();

    return true;
}

// ---------------------------------------

TaskTessellation::TaskTessellation()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    if (document) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new Tessellation();
    addTaskBox(widget);
}

void TaskTessellation::open()
{}

void TaskTessellation::clicked(int id)
{
    Q_UNUSED(id)
}

bool TaskTessellation::accept()
{
    return widget->accept();
}

bool TaskTessellation::reject()
{
    widget->reject();
    return true;
}

#include "moc_Tessellation.cpp"
