// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2012 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRep_Builder.hxx>
#include <GeomAPI_ProjectPointOnSurf.hxx>
#include <Geom_Plane.hxx>
#include <Standard_Failure.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Wire.hxx>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/ExactTransaction.h>
#include <Gui/WaitCursor.h>
#include <Mod/Mesh/App/Core/Algorithm.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/App/Core/Curvature.h>
#include <Mod/Mesh/App/Core/Segmentation.h>
#include <Mod/Mesh/App/Core/Smoothing.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Part/App/FaceMakerCheese.h>
#include <Mod/Part/App/PartFeature.h>

#include "OperationSupport.h"
#include "Segmentation.h"
#include "ui_Segmentation.h"


using namespace ReverseEngineeringGui;

Segmentation::Segmentation(Mesh::Feature* mesh, QWidget* parent, Qt::WindowFlags fl)
    : QWidget(parent, fl)
    , ui(new Ui_Segmentation)
    , myMesh(mesh)
{
    ui->setupUi(this);
    ui->numPln->setRange(1, std::numeric_limits<int>::max());
    ui->numPln->setValue(100);

    ui->checkBoxSmooth->setChecked(false);
}

Segmentation::~Segmentation() = default;

void Segmentation::accept()
{
    (void)tryAccept();
}

bool Segmentation::tryAccept()
{
    struct SegmentResult
    {
        std::vector<long> facets;
        std::string label;
    };

    auto* source = myMesh.get<Mesh::Feature>();
    auto* document = source ? source->getDocument() : nullptr;
    if (!source || !document || App::GetApplication().getActiveDocument() != document
        || !MeshGui::hasCleanNativeMutationBoundary(document)
        || !ReverseEngineeringGui::OperationSupport::isUsableSource(source, document)
        || source->Mesh.getValue().countFacets() == 0) {
        QMessageBox::warning(
            this,
            tr("Mesh Segmentation"),
            tr("The original mesh is no longer available.")
        );
        return false;
    }

    try {
        Gui::WaitCursor waitCursor;
        const bool createUnused = ui->createUnused->isChecked();
        const bool createCompound = ui->createCompound->isChecked();
        const Mesh::MeshObject& sourceMesh = source->Mesh.getValue();

        MeshCore::MeshKernel kernel = sourceMesh.getKernel();
        MeshCore::MeshAlgorithm algorithm(kernel);
        if (ui->checkBoxSmooth->isChecked()) {
            MeshCore::LaplaceSmoothing smoother(kernel);
            smoother.Smooth(ui->smoothSteps->value());
        }

        MeshCore::MeshSegmentAlgorithm finder(kernel);
        MeshCore::MeshCurvature curvature(kernel);
        curvature.ComputePerVertex();

        std::vector<MeshCore::MeshSurfaceSegmentPtr> preliminary;
        if (ui->groupBoxPln->isChecked()) {
            preliminary.emplace_back(
                std::make_shared<MeshCore::MeshCurvaturePlanarSegment>(
                    curvature.GetCurvature(),
                    ui->numPln->value(),
                    ui->curvTolPln->value()
                )
            );
        }
        finder.FindSegments(preliminary);

        std::vector<MeshCore::MeshSurfaceSegmentPtr> fittedSurfaces;
        for (const auto& candidate : preliminary) {
            if (strcmp(candidate->GetType(), "Plane") != 0) {
                continue;
            }
            for (const auto& segment : candidate->GetSegments()) {
                const auto pointIndices = kernel.GetFacetPoints(segment);
                MeshCore::PlaneFit fit;
                fit.AddPoints(kernel.GetPoints(pointIndices));
                if (fit.Fit() >= std::numeric_limits<float>::max()) {
                    continue;
                }
                fittedSurfaces.emplace_back(
                    std::make_shared<MeshCore::MeshDistanceGenericSurfaceFitSegment>(
                        new MeshCore::PlaneSurfaceFit(fit.GetBase(), fit.GetNormal()),
                        kernel,
                        ui->numPln->value(),
                        ui->distToPln->value()
                    )
                );
            }
        }
        finder.FindSegments(fittedSurfaces);

        BRep_Builder builder;
        TopoDS_Compound compound;
        builder.MakeCompound(compound);
        std::size_t compoundFaces = 0;
        std::vector<SegmentResult> acceptedSegments;
        algorithm.SetFacetFlag(MeshCore::MeshFacet::TMP0);

        for (const auto& fitted : fittedSurfaces) {
            auto planar = std::dynamic_pointer_cast<MeshCore::MeshDistanceGenericSurfaceFitSegment>(
                fitted
            );
            if (!planar) {
                continue;
            }
            const bool isPlanar = strcmp(planar->GetType(), "Plane") == 0;
            for (const auto& segment : planar->GetSegments()) {
                if (segment.empty()) {
                    continue;
                }
                algorithm.ResetFacetsFlag(segment, MeshCore::MeshFacet::TMP0);
                acceptedSegments.push_back({
                    {segment.begin(), segment.end()},
                    std::string("Segment (") + planar->GetType() + ")",
                });

                if (!createCompound || !isPlanar) {
                    continue;
                }
                std::list<std::vector<Base::Vector3f>> borders;
                algorithm.GetFacetBorders(segment, borders);
                const auto parameters = planar->Parameters();
                if (parameters.size() < 6) {
                    continue;
                }
                Handle(Geom_Plane) plane(new Geom_Plane(
                    gp_Pnt(parameters[0], parameters[1], parameters[2]),
                    gp_Dir(parameters[3], parameters[4], parameters[5])
                ));

                std::vector<TopoDS_Wire> wires;
                for (const auto& border : borders) {
                    BRepBuilderAPI_MakePolygon polygon;
                    for (auto point = border.rbegin(); point != border.rend(); ++point) {
                        const gp_Pnt original(point->x, point->y, point->z);
                        polygon.Add(GeomAPI_ProjectPointOnSurf(original, plane).NearestPoint());
                    }
                    if (polygon.IsDone()) {
                        wires.push_back(polygon.Wire());
                    }
                }
                if (wires.empty()) {
                    continue;
                }
                try {
                    const TopoDS_Shape face = Part::FaceMakerCheese::makeFace(wires);
                    if (!face.IsNull()) {
                        builder.Add(compound, face);
                        ++compoundFaces;
                    }
                }
                catch (const Standard_Failure& error) {
                    Base::Console().warning(
                        "Could not create a face for one mesh segment: %s\n",
                        error.GetMessageString()
                    );
                }
            }
        }

        if (createUnused) {
            std::vector<MeshCore::FacetIndex> unused;
            algorithm.GetFacetsFlag(unused, MeshCore::MeshFacet::TMP0);
            if (!unused.empty()) {
                acceptedSegments.push_back({
                    {unused.begin(), unused.end()},
                    "Unused Facets",
                });
            }
        }
        if (acceptedSegments.empty() && compoundFaces == 0) {
            QMessageBox::information(
                this,
                tr("Mesh Segmentation"),
                tr("The current settings did not find any mesh segments.")
            );
            return false;
        }

        Gui::ExactTransaction mutation(
            *document,
            QT_TRANSLATE_NOOP("Command", "Segment mesh by surface")
        );
        std::vector<App::DocumentObject*> outputs;
        outputs.reserve(acceptedSegments.size() + (compoundFaces > 0 ? 1U : 0U));
        for (const auto& accepted : acceptedSegments) {
            auto* segment = document->addObject<Mesh::FacetSubset>("Segment");
            segment->Label.setValue(accepted.label);
            segment->Source.setValue(source);
            segment->FacetIndices.setValues(accepted.facets);
            segment->AcceptedTopology.setValue(sourceMesh);
            segment->SelectionKind.setValue("Surface segment");
            outputs.push_back(segment);
        }
        if (compoundFaces > 0) {
            auto* faces = document->addObject<Part::Feature>("SegmentFaces");
            faces->Label.setValue("Segment Faces");
            faces->Shape.setValue(compound);
            ReverseEngineeringGui::OperationSupport::setSource(*faces, *source);
            outputs.push_back(faces);
        }

        document->recompute();
        for (auto* output : outputs) {
            if (!output || output->isError()) {
                throw Base::RuntimeError("Mesh segmentation produced an invalid result");
            }
            if (const auto* segment = freecad_cast<const Mesh::Feature*>(output);
                segment && segment->Mesh.getValue().countFacets() == 0) {
                throw Base::RuntimeError("Mesh segmentation produced an empty segment");
            }
            if (const auto* faces = freecad_cast<const Part::Feature*>(output);
                faces && faces->Shape.getValue().IsNull()) {
                throw Base::RuntimeError("Mesh segmentation produced an empty face result");
            }
        }

        ReverseEngineeringGui::OperationSupport::publishOutputGroup(
            *document,
            {source},
            outputs,
            "Segments",
            (std::string("Segments ") + source->Label.getValue()).c_str(),
            "Segment mesh by surface",
            true
        );
        document->recompute();
        ReverseEngineeringGui::OperationSupport::commit(mutation);
        return true;
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(this, tr("Mesh Segmentation"), QString::fromUtf8(error.what()));
    }
    catch (const std::exception& error) {
        QMessageBox::warning(this, tr("Mesh Segmentation"), QString::fromUtf8(error.what()));
    }
    catch (...) {
        QMessageBox::warning(this, tr("Mesh Segmentation"), tr("Mesh segmentation failed unexpectedly."));
    }
    return false;
}

void Segmentation::changeEvent(QEvent* e)
{
    if (e->type() == QEvent::LanguageChange) {
        ui->retranslateUi(this);
    }
    QWidget::changeEvent(e);
}

// ---------------------------------------

/* TRANSLATOR MeshGui::TaskRemoveComponents */

TaskSegmentation::TaskSegmentation(Mesh::Feature* mesh)
{
    if (mesh && mesh->getDocument()) {
        setDocumentName(mesh->getDocument()->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    widget = new Segmentation(mesh);
    addTaskBox(widget, false);
}

bool TaskSegmentation::accept()
{
    return widget->tryAccept();
}

#include "moc_Segmentation.cpp"
