// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2020 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <QPushButton>
#include <QMessageBox>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Mesh/App/Core/Algorithm.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/App/Core/Segmentation.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ViewProvider.h>

#include "OperationSupport.h"
#include "SegmentationManual.h"
#include "ui_SegmentationManual.h"


using namespace ReverseEngineeringGui;

class SegmentationManual::Private
{
public:
    explicit Private(App::Document* target)
        : document(target)
    {}

    void findGeometry(
        int minFaces,
        double tolerance,
        std::function<MeshCore::AbstractSurfaceFit*(
            const std::vector<Base::Vector3f>&,
            const std::vector<Base::Vector3f>&
        )> fitFunction
    );

    App::DocumentWeakPtrT document;
};

SegmentationManual::SegmentationManual(QWidget* parent, Qt::WindowFlags fl)
    : SegmentationManual(App::GetApplication().getActiveDocument(), parent, fl)
{}

SegmentationManual::SegmentationManual(App::Document* document, QWidget* parent, Qt::WindowFlags fl)
    : QWidget(parent, fl)
    , ui(new Ui_SegmentationManual)
    , d(std::make_unique<Private>(document))
{
    ui->setupUi(this);
    setupConnections();
    ui->spSelectComp->setRange(1, std::numeric_limits<int>::max());
    ui->spSelectComp->setValue(10);

    Gui::Selection().clearSelection();
    meshSel.setCheckOnlyVisibleTriangles(ui->visibleTriangles->isChecked());
    meshSel.setCheckOnlyPointToUserTriangles(ui->screenTriangles->isChecked());
    meshSel.setEnabledViewerSelection(false);
}

SegmentationManual::~SegmentationManual() = default;

void SegmentationManual::setupConnections()
{
    connect(ui->selectRegion, &QPushButton::clicked, this, &SegmentationManual::onSelectRegionClicked);
    connect(ui->selectAll, &QPushButton::clicked, this, &SegmentationManual::onSelectAllClicked);
    connect(
        ui->selectComponents,
        &QPushButton::clicked,
        this,
        &SegmentationManual::onSelectComponentsClicked
    );
    connect(ui->selectTriangle, &QPushButton::clicked, this, &SegmentationManual::onSelectTriangleClicked);
    connect(ui->deselectAll, &QPushButton::clicked, this, &SegmentationManual::onDeselectAllClicked);
    connect(ui->visibleTriangles, &QCheckBox::toggled, this, &SegmentationManual::onVisibleTrianglesToggled);
    connect(ui->screenTriangles, &QCheckBox::toggled, this, &SegmentationManual::onScreenTrianglesToggled);
    connect(ui->cbSelectComp, &QCheckBox::toggled, this, &SegmentationManual::onSelectCompToggled);
    connect(ui->planeDetect, &QPushButton::clicked, this, &SegmentationManual::onPlaneDetectClicked);
    connect(ui->cylinderDetect, &QPushButton::clicked, this, &SegmentationManual::onCylinderDetectClicked);
    connect(ui->sphereDetect, &QPushButton::clicked, this, &SegmentationManual::onSphereDetectClicked);
}

void SegmentationManual::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QWidget::changeEvent(e);
}

void SegmentationManual::onSelectRegionClicked()
{
    meshSel.startSelection();
}

void SegmentationManual::onSelectAllClicked()
{
    // select the complete meshes
    meshSel.fullSelection();
}

void SegmentationManual::onDeselectAllClicked()
{
    // deselect all meshes
    meshSel.clearSelection();
}

void SegmentationManual::onSelectComponentsClicked()
{
    // select components up to a certain size
    int size = ui->spSelectComp->value();
    meshSel.selectComponent(size);
}

void SegmentationManual::onVisibleTrianglesToggled(bool on)
{
    meshSel.setCheckOnlyVisibleTriangles(on);
}

void SegmentationManual::onScreenTrianglesToggled(bool on)
{
    meshSel.setCheckOnlyPointToUserTriangles(on);
}

void SegmentationManual::onSelectCompToggled(bool on)
{
    meshSel.setAddComponentOnClick(on);
}

void SegmentationManual::Private::findGeometry(
    int minFaces,
    double tolerance,
    std::function<MeshCore::AbstractSurfaceFit*(
        const std::vector<Base::Vector3f>&,
        const std::vector<Base::Vector3f>&
    )> fitFunction
)
{
    auto* target = *document;
    auto* guiDocument = target ? Gui::Application::Instance->getDocument(target) : nullptr;
    if (!target || !guiDocument || App::GetApplication().getActiveDocument() != target) {
        return;
    }

    for (auto* source : target->getObjectsOfType<Mesh::Feature>()) {
        if (!MeshGui::isNativeMeshInputActive(source)) {
            continue;
        }
        auto* viewProvider = dynamic_cast<MeshGui::ViewProviderMesh*>(
            guiDocument->getViewProvider(source)
        );
        const Mesh::MeshObject& mesh = source->Mesh.getValue();
        if (!viewProvider || !mesh.hasSelectedFacets()) {
            continue;
        }

        const MeshCore::MeshKernel& kernel = mesh.getKernel();
        std::vector<MeshCore::FacetIndex> facets;
        mesh.getFacetsFromSelection(facets);
        const auto vertices = mesh.getPointsFromFacets(facets);
        const auto coordinates = kernel.GetPoints(vertices);

        std::vector<Base::Vector3f> points(coordinates.begin(), coordinates.end());
        const auto normals = kernel.GetFacetNormals(facets);
        std::unique_ptr<MeshCore::AbstractSurfaceFit> surface(fitFunction(points, normals));
        if (!surface) {
            continue;
        }

        MeshCore::MeshSegmentAlgorithm finder(kernel);
        std::vector<MeshCore::MeshSurfaceSegmentPtr> segments;
        segments.emplace_back(
            std::make_shared<MeshCore::MeshDistanceGenericSurfaceFitSegment>(
                surface.release(),
                kernel,
                minFaces,
                tolerance
            )
        );
        finder.FindSegments(segments);
        for (const auto& segmenter : segments) {
            for (const auto& segment : segmenter->GetSegments()) {
                viewProvider->addSelection(segment);
            }
        }
    }
}

void SegmentationManual::onPlaneDetectClicked()
{
    auto func = [=](const std::vector<Base::Vector3f>& points,
                    const std::vector<Base::Vector3f>& normal) -> MeshCore::AbstractSurfaceFit* {
        Q_UNUSED(normal)

        MeshCore::PlaneFit fit;
        fit.AddPoints(points);
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base = fit.GetBase();
            Base::Vector3f axis = fit.GetNormal();
            return new MeshCore::PlaneSurfaceFit(base, axis);
        }

        return nullptr;
    };
    d->findGeometry(ui->numPln->value(), ui->tolPln->value(), func);
}

void SegmentationManual::onCylinderDetectClicked()
{
    auto func = [=](const std::vector<Base::Vector3f>& points,
                    const std::vector<Base::Vector3f>& normal) -> MeshCore::AbstractSurfaceFit* {
        Q_UNUSED(normal)

        MeshCore::CylinderFit fit;
        fit.AddPoints(points);
        if (!normal.empty()) {
            Base::Vector3f base = fit.GetGravity();
            Base::Vector3f axis = fit.GetInitialAxisFromNormals(normal);
            fit.SetInitialValues(base, axis);
        }
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base = fit.GetBase();
            Base::Vector3f axis = fit.GetAxis();
            float radius = fit.GetRadius();
            return new MeshCore::CylinderSurfaceFit(base, axis, radius);
        }

        return nullptr;
    };
    d->findGeometry(ui->numCyl->value(), ui->tolCyl->value(), func);
}

void SegmentationManual::onSphereDetectClicked()
{
    auto func = [=](const std::vector<Base::Vector3f>& points,
                    const std::vector<Base::Vector3f>& normal) -> MeshCore::AbstractSurfaceFit* {
        Q_UNUSED(normal)

        MeshCore::SphereFit fit;
        fit.AddPoints(points);
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            Base::Vector3f base = fit.GetCenter();
            float radius = fit.GetRadius();
            return new MeshCore::SphereSurfaceFit(base, radius);
        }

        return nullptr;
    };
    d->findGeometry(ui->numSph->value(), ui->tolSph->value(), func);
}

void SegmentationManual::createSegment()
{
    struct AcceptedSelection
    {
        Mesh::Feature* source;
        std::vector<long> selected;
        std::vector<long> remainder;
    };

    auto clearSelection = [this]() {
        meshSel.clearSelection();
    };
    auto* document = *d->document;
    if (!document || App::GetApplication().getActiveDocument() != document
        || !MeshGui::hasCleanNativeMutationBoundary(document)) {
        clearSelection();
        QMessageBox::warning(
            this,
            tr("Manual Segmentation"),
            tr("The original document is no longer available.")
        );
        return;
    }

    try {
        std::vector<AcceptedSelection> selections;
        for (auto* source : document->getObjectsOfType<Mesh::Feature>()) {
            if (!ReverseEngineeringGui::OperationSupport::isUsableSource(source, document)) {
                continue;
            }
            const Mesh::MeshObject& mesh = source->Mesh.getValue();
            if (!mesh.hasSelectedFacets()) {
                continue;
            }

            std::vector<MeshCore::FacetIndex> selectedFacets;
            mesh.getFacetsFromSelection(selectedFacets);
            std::ranges::sort(selectedFacets);
            selectedFacets.erase(
                std::unique(selectedFacets.begin(), selectedFacets.end()),
                selectedFacets.end()
            );
            if (selectedFacets.empty()) {
                continue;
            }

            std::vector<bool> selectedByIndex(mesh.countFacets(), false);
            std::vector<long> selected;
            selected.reserve(selectedFacets.size());
            for (const auto facet : selectedFacets) {
                if (facet >= mesh.countFacets()) {
                    throw Base::ValueError("A selected mesh facet is no longer available");
                }
                selectedByIndex[facet] = true;
                selected.push_back(static_cast<long>(facet));
            }

            std::vector<long> remainder;
            remainder.reserve(mesh.countFacets() - selectedFacets.size());
            for (unsigned long facet = 0; facet < mesh.countFacets(); ++facet) {
                if (!selectedByIndex[facet]) {
                    remainder.push_back(static_cast<long>(facet));
                }
            }
            selections.push_back({
                source,
                std::move(selected),
                std::move(remainder),
            });
        }
        if (selections.empty()) {
            clearSelection();
            QMessageBox::information(
                this,
                tr("Manual Segmentation"),
                tr("Select one or more mesh facets to create a segment.")
            );
            return;
        }

        const bool cutSelected = ui->checkBoxCutSegm->isChecked();
        const bool hideSelected = ui->checkBoxHideSegm->isChecked();
        Gui::ExactTransaction mutation(
            *document,
            QT_TRANSLATE_NOOP("Command", "Create manual mesh segment")
        );
        std::vector<App::DocumentObject*> sources;
        std::vector<App::DocumentObject*> outputs;
        sources.reserve(selections.size());
        outputs.reserve(selections.size() * (cutSelected ? 2U : 1U));

        for (const auto& selection : selections) {
            sources.push_back(selection.source);
            auto* segment = document->addObject<Mesh::FacetSubset>("Segment");
            segment->Label.setValue(selection.source->Label.getStrValue() + " Segment");
            segment->Source.setValue(selection.source);
            segment->FacetIndices.setValues(selection.selected);
            segment->AcceptedTopology.setValue(selection.source->Mesh.getValue());
            segment->SelectionKind.setValue("Manual selection");
            segment->Visibility.setValue(!hideSelected);
            outputs.push_back(segment);

            if (cutSelected && !selection.remainder.empty()) {
                auto* remainder = document->addObject<Mesh::FacetSubset>("SegmentRemainder");
                remainder->Label.setValue(selection.source->Label.getStrValue() + " Remainder");
                remainder->Source.setValue(selection.source);
                remainder->FacetIndices.setValues(selection.remainder);
                remainder->AcceptedTopology.setValue(selection.source->Mesh.getValue());
                remainder->SelectionKind.setValue("Manual selection remainder");
                outputs.push_back(remainder);
            }
        }

        document->recompute();
        if (std::ranges::any_of(outputs, [](const App::DocumentObject* output) {
                const auto* subset = freecad_cast<const Mesh::FacetSubset*>(output);
                return !subset || subset->isError() || subset->Mesh.getValue().countFacets() == 0;
            })) {
            throw Base::RuntimeError("Manual segmentation produced an invalid mesh result");
        }

        ReverseEngineeringGui::OperationSupport::publishOutputGroup(
            *document,
            sources,
            outputs,
            cutSelected ? "CutSegments" : "ExtractedSegments",
            cutSelected ? "Cut Mesh Segments" : "Extracted Mesh Segments",
            cutSelected ? "Cut selected mesh segments" : "Extract selected mesh segments",
            cutSelected
        );
        document->recompute();
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        clearSelection();
    }
    catch (const Base::Exception& error) {
        clearSelection();
        QMessageBox::warning(this, tr("Manual Segmentation"), QString::fromUtf8(error.what()));
    }
    catch (const std::exception& error) {
        clearSelection();
        QMessageBox::warning(this, tr("Manual Segmentation"), QString::fromUtf8(error.what()));
    }
    catch (...) {
        clearSelection();
        QMessageBox::warning(
            this,
            tr("Manual Segmentation"),
            tr("Manual segmentation failed unexpectedly.")
        );
    }
}

void SegmentationManual::onSelectTriangleClicked()
{
    meshSel.selectTriangle();
    meshSel.setAddComponentOnClick(ui->cbSelectComp->isChecked());
}

void SegmentationManual::reject()
{
    // deselect all meshes
    meshSel.clearSelection();
    meshSel.setEnabledViewerSelection(true);
}

// -------------------------------------------------

/* TRANSLATOR ReverseEngineeringGui::TaskSegmentationManual */

TaskSegmentationManual::TaskSegmentationManual()
    : TaskSegmentationManual(App::GetApplication().getActiveDocument())
{}

TaskSegmentationManual::TaskSegmentationManual(App::Document* document)
{
    if (document) {
        setDocumentName(document->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new SegmentationManual(document);
    addTaskBox(widget, false);
}

void TaskSegmentationManual::modifyStandardButtons(QDialogButtonBox* box)
{
    QPushButton* btn = box->button(QDialogButtonBox::Ok);
    btn->setText(tr("Create"));
}

bool TaskSegmentationManual::accept()
{
    return false;
}

void TaskSegmentationManual::clicked(int id)
{
    if (id == QDialogButtonBox::Ok) {
        widget->createSegment();
    }
    else if (id == QDialogButtonBox::Close) {
        widget->reject();
    }
}

#include "moc_SegmentationManual.cpp"
