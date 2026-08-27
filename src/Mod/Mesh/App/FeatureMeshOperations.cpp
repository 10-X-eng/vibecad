// SPDX-License-Identifier: LGPL-2.1-or-later

#include "FeatureMeshOperations.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <iterator>
#include <limits>
#include <list>
#include <memory>
#include <ranges>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include <QProcess>
#include <QStringList>

#include <App/Application.h>
#include <App/ComplexGeoData.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeature.h>
#include <Base/Converter.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/Matrix.h>
#include <Base/Stream.h>
#include <Base/Tools.h>
#include <Base/ViewProj.h>

#include "Core/Algorithm.h"
#include "Core/Decimation.h"
#include "Core/MeshIO.h"
#include "Core/Smoothing.h"
#include "Core/Triangulation.h"

namespace
{

bool sameMeshState(const Mesh::MeshObject& first, const Mesh::MeshObject& second)
{
    if (first.getTransform() != second.getTransform()
        || first.countSegments() != second.countSegments()) {
        return false;
    }

    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    if (firstKernel.GetPoints() != secondKernel.GetPoints()) {
        return false;
    }
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    if (firstFacets.size() != secondFacets.size()
        || !std::ranges::equal(
            firstFacets,
            secondFacets,
            [](const MeshCore::MeshFacet& left, const MeshCore::MeshFacet& right) {
                return left._aulPoints[0] == right._aulPoints[0]
                    && left._aulPoints[1] == right._aulPoints[1]
                    && left._aulPoints[2] == right._aulPoints[2];
            }
        )) {
        return false;
    }

    for (unsigned long index = 0; index < first.countSegments(); ++index) {
        if (first.getSegment(index).getIndices() != second.getSegment(index).getIndices()) {
            return false;
        }
    }
    return true;
}

bool sameMeshTopology(
    const Mesh::MeshObject& first,
    const Mesh::MeshObject& second
)
{
    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    if (firstKernel.CountPoints() != secondKernel.CountPoints()) {
        return false;
    }
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    return firstFacets.size() == secondFacets.size()
        && std::ranges::equal(
            firstFacets,
            secondFacets,
            [](const MeshCore::MeshFacet& left,
               const MeshCore::MeshFacet& right) {
                return left._aulPoints[0] == right._aulPoints[0]
                    && left._aulPoints[1] == right._aulPoints[1]
                    && left._aulPoints[2] == right._aulPoints[2];
            }
        );
}

std::vector<Mesh::FacetIndex> checkedFacetIndices(
    const App::PropertyIntegerList& property,
    unsigned long facetCount
)
{
    std::vector<Mesh::FacetIndex> indices;
    indices.reserve(property.getSize());
    for (long value : property.getValues()) {
        if (value < 0 || static_cast<unsigned long>(value) >= facetCount) {
            throw Base::ValueError("A stored facet index is outside the linked source mesh");
        }
        indices.push_back(static_cast<Mesh::FacetIndex>(value));
    }
    std::ranges::sort(indices);
    indices.erase(std::unique(indices.begin(), indices.end()), indices.end());
    return indices;
}

class TemporaryGmshFiles
{
public:
    TemporaryGmshFiles()
    {
        const std::string prefix =
            App::Application::getTempFileName();
        input = prefix + "vibecad-remesh-input.stl";
        project = prefix + "vibecad-remesh.geo";
        output = prefix + "vibecad-remesh-output.stl";
    }

    ~TemporaryGmshFiles()
    {
        Base::FileInfo(input).deleteFile();
        Base::FileInfo(project).deleteFile();
        Base::FileInfo(output).deleteFile();
    }

    std::string input;
    std::string project;
    std::string output;
};

std::string gmshQuotedPath(const std::string& path)
{
    std::string escaped;
    escaped.reserve(path.size());
    for (char character : path) {
        if (character == '\\' || character == '"') {
            escaped.push_back('\\');
        }
        escaped.push_back(character);
    }
    return escaped;
}

Mesh::MeshObject runGmshRemesh(
    const Mesh::MeshObject& source,
    int algorithm,
    double minimumSize,
    double maximumSize,
    double surfaceAngle,
    const std::string& executable,
    int timeoutSeconds
)
{
    TemporaryGmshFiles files;
    Base::ofstream input(
        Base::FileInfo(files.input),
        std::ios::out | std::ios::binary
    );
    if (!input.is_open()) {
        throw Base::RuntimeError(
            "The Gmsh input mesh could not be created"
        );
    }
    MeshCore::MeshOutput(source.getKernel()).SaveBinarySTL(input);
    input.close();

    Base::ofstream project(
        Base::FileInfo(files.project),
        std::ios::out
    );
    if (!project.is_open()) {
        throw Base::RuntimeError(
            "The Gmsh remesh project could not be created"
        );
    }
    const double effectiveMaximum =
        maximumSize > 0.0 ? maximumSize : 1.0e22;
    project
        << "If(GMSH_MAJOR_VERSION < 4)\n"
        << "  Error(\"Gmsh 4 or later is required\");\n"
        << "  Exit;\n"
        << "EndIf\n"
        << "Merge \"" << gmshQuotedPath(files.input) << "\";\n"
        << "Mesh.Algorithm = " << algorithm << ";\n"
        << "Mesh.CharacteristicLengthMax = " << effectiveMaximum
        << ";\n"
        << "Mesh.CharacteristicLengthMin = " << minimumSize << ";\n"
        << "ClassifySurfaces{" << surfaceAngle
        << " * Pi/180, 1, 0};\n"
        << "CreateGeometry;\n"
        << "Surface Loop(1) = Surface{:};\n"
        << "Volume(1) = {1};\n";
    project.close();

    QProcess process;
    process.setProgram(
        QString::fromUtf8(
            executable.empty() ? "gmsh" : executable.c_str()
        )
    );
    process.setArguments(
        {
            QStringLiteral("-"),
            QStringLiteral("-bin"),
            QStringLiteral("-2"),
            QString::fromUtf8(files.project.c_str()),
            QStringLiteral("-o"),
            QString::fromUtf8(files.output.c_str()),
        }
    );
    process.start();
    if (!process.waitForStarted(10000)) {
        throw Base::RuntimeError(
            "Gmsh could not be started"
        );
    }
    const int timeoutMilliseconds =
        timeoutSeconds > std::numeric_limits<int>::max() / 1000
        ? std::numeric_limits<int>::max()
        : timeoutSeconds * 1000;
    if (!process.waitForFinished(timeoutMilliseconds)) {
        process.kill();
        process.waitForFinished(1000);
        throw Base::RuntimeError(
            "Gmsh timed out before producing a remesh"
        );
    }
    if (process.exitStatus() != QProcess::NormalExit
        || process.exitCode() != 0) {
        std::string detail =
            process.readAllStandardError().toStdString();
        if (detail.size() > 1000) {
            detail.resize(1000);
        }
        throw Base::RuntimeError(
            std::string("Gmsh remeshing failed")
            + (detail.empty() ? "" : ": " + detail)
        );
    }

    Mesh::MeshObject result;
    MeshCore::MeshInput meshInput(result.getKernel());
    Base::ifstream output(
        Base::FileInfo(files.output),
        std::ios::in | std::ios::binary
    );
    if (!output.is_open()) {
        throw Base::RuntimeError(
            "Gmsh did not create a readable remesh"
        );
    }
    meshInput.LoadBinarySTL(output);
    output.close();
    result.harmonizeNormals();
    if (result.countFacets() == 0) {
        throw Base::RuntimeError(
            "Gmsh produced an empty remesh"
        );
    }
    result.setTransform(source.getTransform());
    return result;
}

}  // namespace

namespace Mesh
{

PROPERTY_SOURCE(Mesh::MeshFromGeometry, Mesh::Feature)

MeshFromGeometry::MeshFromGeometry()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(
        Source,
        (nullptr),
        "Tessellation",
        App::Prop_None,
        "Linked geometric object to tessellate"
    );
    ADD_PROPERTY_TYPE(
        Tolerance,
        (0.1),
        "Tessellation",
        App::Prop_None,
        "Maximum geometric tessellation tolerance"
    );
    Source.setScope(App::LinkScope::Global);
}

bool MeshFromGeometry::isSuppressed() const
{
    if (suppressibleExt.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(getDocument());
    return timeline && !timeline->isOperationActive(this);
}

short MeshFromGeometry::mustExecute() const
{
    auto* source =
        freecad_cast<App::GeoFeature*>(Source.getValue());
    const auto* geometry =
        source ? source->getPropertyOfGeometry() : nullptr;
    if (Source.isTouched() || Tolerance.isTouched()
        || suppressibleExt.Suppressed.isTouched()
        || (source && source->isTouched())
        || (geometry && geometry->isTouched())
        || (source && source->Placement.isTouched())) {
        return 1;
    }
    return Mesh::Feature::mustExecute();
}

App::DocumentObjectExecReturn* MeshFromGeometry::execute()
{
    if (isSuppressed()) {
        Mesh.setValue(MeshObject());
        return App::DocumentObject::StdReturn;
    }

    auto* source =
        freecad_cast<App::GeoFeature*>(Source.getValue());
    if (!source || source == this || !getDocument()
        || source->getDocument() != getDocument()
        || !source->getNameInDocument()
        || !getDocument()->containsObject(source)
        || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(
            source
        )) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "Source must link to usable geometry in this document"
        );
    }

    const auto* geometry = source->getPropertyOfGeometry();
    const auto* data =
        geometry ? geometry->getComplexData() : nullptr;
    const double tolerance = Tolerance.getValue();
    if (!data) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "Source does not provide tessellatable geometry"
        );
    }
    if (!std::isfinite(tolerance) || tolerance <= 0.0) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "Tolerance must be finite and greater than zero"
        );
    }

    try {
        std::vector<Base::Vector3d> points;
        std::vector<Data::ComplexGeoData::Facet> topology;
        data->getFaces(points, topology, tolerance);
        // ComplexGeoData::getFaces() returns points with the geometry's own
        // transform already applied. Apply only the containing object's
        // placement here; applying getGlobalPlacement() directly would apply
        // the source Placement twice.
        const Base::Matrix4D parentTransform =
            App::GeoFeature::getGlobalPlacement(source).toMatrix()
            * source->Placement.getValue().inverse().toMatrix();
        for (auto& point : points) {
            point = parentTransform * point;
        }
        MeshObject result;
        result.setFacets(topology, points);
        if (result.countFacets() == 0) {
            throw Base::RuntimeError(
                "Geometry tessellation produced no facets"
            );
        }
        // Result Placement is a real, independently editable input. Source
        // placement changes update the baked source geometry without erasing
        // the user's downstream positioning of this mesh result.
        result.setTransform(Placement.getValue().toMatrix());
        Mesh.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
}

PROPERTY_SOURCE(Mesh::Merge, Mesh::Feature)

Merge::Merge()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(
        Sources,
        (),
        "Merge",
        App::Prop_None,
        "Ordered source meshes combined by this operation"
    );
    ADD_PROPERTY_TYPE(
        AcceptedResult,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Authenticated detached merge result retained across History changes"
    );
    ADD_PROPERTY_TYPE(
        AcceptedSourceRevisions,
        (),
        "Internal",
        App::Prop_Hidden,
        "Geometry revisions of the sources accepted by the detached merge"
    );
    ADD_PROPERTY_TYPE(
        AcceptedSourcePlacements,
        (),
        "Internal",
        App::Prop_Hidden,
        "Placements of the sources accepted by the detached merge"
    );
    ADD_PROPERTY_TYPE(
        AcceptedSourcesStale,
        (false),
        "Internal",
        App::Prop_Hidden,
        "Whether a linked source changed after the detached merge was accepted"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Merge",
        App::Prop_None,
        "Recompute the merge when a linked source changes"
    );
}

short Merge::mustExecute() const
{
    if (Sources.isTouched() || AcceptedResult.isTouched()
        || AcceptedSourceRevisions.isTouched() || AcceptedSourcePlacements.isTouched()
        || AcceptedSourcesStale.isTouched() || UpdateFromSource.isTouched()
        || suppressibleExt.Suppressed.isTouched()) {
        return 1;
    }
    for (auto* source : Sources.getValues()) {
        auto* sourceMesh = source ? source->getPropertyByName("Mesh") : nullptr;
        if ((source && source->isTouched()) || (sourceMesh && sourceMesh->isTouched())) {
            return 1;
        }
    }
    return Mesh::Feature::mustExecute();
}

bool Merge::isSuppressed() const
{
    if (suppressibleExt.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(getDocument());
    return timeline && !timeline->isOperationActive(this);
}

bool Merge::detachedSourcesChanged() const
{
    const auto sources = Sources.getValues();
    const auto revisions = AcceptedSourceRevisions.getValues();
    const auto placements = AcceptedSourcePlacements.getValues();
    if (sources.size() != revisions.size() || sources.size() != placements.size()) {
        return true;
    }
    for (std::size_t index = 0; index < sources.size(); ++index) {
        const auto* source = dynamic_cast<const Mesh::Feature*>(sources[index]);
        if (!source || source == this || source->getDocument() != getDocument()
            || !getDocument() || !getDocument()->containsObject(source)
            || std::to_string(source->Mesh.getGeometryRevision()) != revisions[index]
            || source->Placement.getValue() != placements[index]) {
            return true;
        }
    }
    return false;
}

void Merge::onDocumentRestored()
{
    Mesh::Feature::onDocumentRestored();
    if (UpdateFromSource.getValue() || AcceptedSourcesStale.getValue()
        || AcceptedResult.getValue().countFacets() == 0) {
        return;
    }
    std::vector<std::string> revisions;
    std::vector<Base::Placement> placements;
    for (const auto* object : Sources.getValues()) {
        const auto* source = dynamic_cast<const Mesh::Feature*>(object);
        if (!source || source == this || source->getDocument() != getDocument()) {
            AcceptedSourcesStale.setValue(true);
            return;
        }
        revisions.push_back(std::to_string(source->Mesh.getGeometryRevision()));
        placements.push_back(source->Placement.getValue());
    }
    AcceptedSourceRevisions.setValues(revisions);
    AcceptedSourcePlacements.setValues(placements);
}

App::DocumentObjectExecReturn* Merge::execute()
{
    if (isSuppressed()) {
        if (UpdateFromSource.getValue() || AcceptedResult.getValue().countFacets() > 0) {
            Mesh.setValue(MeshObject());
        }
        return App::DocumentObject::StdReturn;
    }
    if (!UpdateFromSource.getValue()) {
        if (detachedSourcesChanged()) {
            AcceptedSourcesStale.setValue(true);
        }
        if (AcceptedSourcesStale.getValue()) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "A linked source changed after this background merge was accepted; rerun the merge"
            );
        }
        if (AcceptedResult.getValue().countFacets() > 0) {
            Mesh.setValue(AcceptedResult.getValue());
            return App::DocumentObject::StdReturn;
        }
        if (Mesh.getValue().countFacets() == 0) {
            return new App::DocumentObjectExecReturn(
                "The detached Mesh merge has no cached result"
            );
        }
        return App::DocumentObject::StdReturn;
    }

    const auto sources = Sources.getValues();
    if (sources.size() < 2) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn("Merge requires at least two linked source meshes");
    }

    std::set<const App::DocumentObject*> uniqueSources;
    MeshObject combined;
    for (const auto* sourceObject : sources) {
        const auto* source = dynamic_cast<const Mesh::Feature*>(sourceObject);
        if (!source || source == this || !getDocument() || source->getDocument() != getDocument()
            || !getDocument()->containsObject(source)
            || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(source)
            || !uniqueSources.insert(source).second) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "Merge sources must be distinct live mesh objects in this document"
            );
        }

        const MeshObject& sourceMesh = source->Mesh.getValue();
        if (sourceMesh.countFacets() == 0) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn("A linked merge source is empty");
        }

        MeshCore::MeshKernel kernel(sourceMesh.getKernel());
        kernel.Transform(sourceMesh.getTransform());
        const auto facetOffset = static_cast<FacetIndex>(combined.countFacets());
        combined.addMesh(kernel);

        if (sourceMesh.countSegments() == 0) {
            std::vector<FacetIndex> indices;
            indices.reserve(kernel.CountFacets());
            for (FacetIndex index = 0; index < static_cast<FacetIndex>(kernel.CountFacets());
                 ++index) {
                indices.push_back(facetOffset + index);
            }
            combined.addSegment(indices);
            continue;
        }

        for (unsigned long segmentIndex = 0; segmentIndex < sourceMesh.countSegments();
             ++segmentIndex) {
            const auto& sourceSegment = sourceMesh.getSegment(segmentIndex);
            if (sourceSegment.isEmpty()) {
                continue;
            }
            std::vector<FacetIndex> indices;
            indices.reserve(sourceSegment.getIndices().size());
            std::ranges::transform(
                sourceSegment.getIndices(),
                std::back_inserter(indices),
                [facetOffset](FacetIndex index) { return index + facetOffset; }
            );
            Segment segment(&combined, indices, false);
            segment.setName(sourceSegment.getName());
            segment.setColor(sourceSegment.getColor());
            segment.save(sourceSegment.isSaved());
            combined.addSegment(segment);
        }
    }

    if (combined.countFacets() == 0) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn("Merge produced an empty mesh");
    }
    combined.setTransform(Placement.getValue().toMatrix());
    Mesh.setValue(combined);
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE_WITH_EXTENSIONS(Mesh::OutputGroup, Mesh::Feature)

const char* OutputGroup::InputModeEnums[] = {
    "Replacement",
    "Source preserving",
    "Standalone",
    nullptr,
};

OutputGroup::OutputGroup()
{
    App::GroupExtension::initExtension(this);
    suppressibleExt.initExtension(this);
    suppressibleExt.setTimelineResultVisibleWhenSuppressed(true);

    ADD_PROPERTY_TYPE(
        Sources,
        (),
        "Operation",
        App::Prop_None,
        "Upstream mesh objects represented by this multi-output operation"
    );
    ADD_PROPERTY_TYPE(
        OperationKind,
        ("Mesh operation"),
        "Operation",
        App::Prop_ReadOnly,
        "Accepted command represented by this result group"
    );
    ADD_PROPERTY_TYPE(
        InputMode,
        (0L),
        "Operation",
        App::Prop_ReadOnly,
        "How this operation relates its physical outputs to upstream objects"
    );
    InputMode.setEnums(InputModeEnums);
    ADD_PROPERTY_TYPE(
        ExternalInputs,
        (),
        "Operation",
        App::Prop_ReadOnly,
        "Saved external input identities; reopening never reads these paths"
    );
    ADD_PROPERTY_TYPE(
        VibeCADTimelineRole,
        (App::DocumentTimeline::OperationRole),
        "Timeline",
        static_cast<App::PropertyType>(App::Prop_Hidden | App::Prop_NoRecompute),
        "Document timeline classification"
    );
    VibeCADTimelineRole.setStatus(App::Property::Hidden, true);
    VibeCADTimelineRole.setStatus(App::Property::LockDynamic, true);
    VibeCADTimelineRole.setStatus(App::Property::NoRecompute, true);
}

short OutputGroup::mustExecute() const
{
    if (Sources.isTouched() || InputMode.isTouched() || suppressibleExt.Suppressed.isTouched()) {
        return 1;
    }
    for (auto* source : Sources.getValues()) {
        auto* sourceMesh = source ? source->getPropertyByName("Mesh") : nullptr;
        if ((source && source->isTouched()) || (sourceMesh && sourceMesh->isTouched())) {
            return 1;
        }
    }
    return Mesh::Feature::mustExecute();
}

bool OutputGroup::isSuppressed() const
{
    if (suppressibleExt.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(getDocument());
    return timeline && !timeline->isOperationActive(this);
}

App::DocumentObjectExecReturn* OutputGroup::execute()
{
    if (!isSuppressed() || InputMode.getValue() != 0) {
        Mesh.setValue(MeshObject());
        return App::DocumentObject::StdReturn;
    }

    const auto* timeline = App::DocumentTimeline::get(getDocument());
    std::vector<const MeshObject*> activeSources;
    activeSources.reserve(Sources.getSize());
    for (auto* source : Sources.getValues()) {
        if (!source || (timeline && !timeline->isOperationActive(source))) {
            continue;
        }
        const auto* sourceMesh = dynamic_cast<const Mesh::PropertyMeshKernel*>(
            source->getPropertyByName("Mesh")
        );
        if (!sourceMesh) {
            return new App::DocumentObjectExecReturn(
                "A linked output-group source does not provide mesh geometry"
            );
        }
        activeSources.push_back(&sourceMesh->getValue());
    }

    if (activeSources.empty()) {
        Mesh.setValue(MeshObject());
        return App::DocumentObject::StdReturn;
    }
    if (activeSources.size() == 1) {
        Mesh.setValue(*activeSources.front());
        return App::DocumentObject::StdReturn;
    }

    MeshObject combined;
    for (const auto* source : activeSources) {
        MeshCore::MeshKernel kernel(source->getKernel());
        kernel.Transform(source->getTransform());
        const auto facetOffset = static_cast<FacetIndex>(combined.countFacets());
        combined.addMesh(kernel);

        for (unsigned long segmentIndex = 0; segmentIndex < source->countSegments(); ++segmentIndex) {
            const auto& sourceSegment = source->getSegment(segmentIndex);
            std::vector<FacetIndex> indices;
            indices.reserve(sourceSegment.getIndices().size());
            std::ranges::transform(
                sourceSegment.getIndices(),
                std::back_inserter(indices),
                [facetOffset](FacetIndex index) { return index + facetOffset; }
            );
            Segment segment(&combined, indices, false);
            segment.setName(sourceSegment.getName());
            segment.setColor(sourceSegment.getColor());
            segment.save(sourceSegment.isSaved());
            combined.addSegment(segment);
        }
    }
    Mesh.setValue(combined);
    return App::DocumentObject::StdReturn;
}

bool OutputGroup::allowObject(App::DocumentObject* object)
{
    return object && object->getDocument() == getDocument()
        && App::DocumentTimeline::timelineOwner(object) == this;
}

void OutputGroup::extensionOnChanged(const App::Property* property)
{
    if (property == &Visibility) {
        // DocumentTimeline owns controller/resource visibility. The ordinary
        // GroupExtension behavior would overwrite every child's independent
        // visibility whenever the controller eye is toggled.
        App::Extension::extensionOnChanged(property);
        return;
    }
    App::GroupExtension::extensionOnChanged(property);
}

PROPERTY_SOURCE(Mesh::Smoothing, Mesh::FixDefects)

const char* Smoothing::MethodEnums[] = {"Taubin", "Laplace", "Median", nullptr};

Smoothing::Smoothing()
{
    ADD_PROPERTY_TYPE(Method, (0L), "Smoothing", App::Prop_None, "Smoothing algorithm");
    Method.setEnums(MethodEnums);
    ADD_PROPERTY_TYPE(Iterations, (1), "Smoothing", App::Prop_None, "Number of smoothing passes");
    ADD_PROPERTY_TYPE(Lambda, (0.5F), "Smoothing", App::Prop_None, "Primary smoothing step");
    ADD_PROPERTY_TYPE(Mu, (-0.53F), "Smoothing", App::Prop_None, "Taubin inflation-control step");
    ADD_PROPERTY_TYPE(
        PointIndices,
        (),
        "Smoothing",
        App::Prop_None,
        "Optional source point indices; an empty list smooths the complete mesh"
    );
    ADD_PROPERTY_TYPE(
        SelectionSource,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Exact source topology used to resolve the selected point indices"
    );
}

short Smoothing::mustExecute() const
{
    if (Method.isTouched() || Iterations.isTouched() || Lambda.isTouched() || Mu.isTouched()
        || PointIndices.isTouched() || SelectionSource.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* Smoothing::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }
    mesh.clearFacetSelection();
    if (Iterations.getValue() < 1) {
        return new App::DocumentObjectExecReturn("Iterations must be at least one");
    }

    std::vector<PointIndex> points;
    points.reserve(PointIndices.getSize());
    if (PointIndices.getSize() > 0
        && !sameMeshTopology(mesh, SelectionSource.getValue())) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "The linked source mesh topology changed after the smoothing selection was captured; "
            "select the intended region again"
        );
    }
    for (long index : PointIndices.getValues()) {
        if (index < 0 || static_cast<unsigned long>(index) >= mesh.countPoints()) {
            return new App::DocumentObjectExecReturn(
                "A smoothing point index is outside the linked source mesh"
            );
        }
        points.push_back(static_cast<PointIndex>(index));
    }
    std::ranges::sort(points);
    points.erase(std::unique(points.begin(), points.end()), points.end());

    try {
        switch (Method.getValue()) {
            case 0: {
                MeshCore::TaubinSmoothing smoothing(mesh.getKernel());
                smoothing.SetLambda(Lambda.getValue());
                smoothing.SetMicro(Mu.getValue());
                if (points.empty()) {
                    smoothing.Smooth(Iterations.getValue());
                }
                else {
                    smoothing.SmoothPoints(Iterations.getValue(), points);
                }
                break;
            }
            case 1: {
                MeshCore::LaplaceSmoothing smoothing(mesh.getKernel());
                smoothing.SetLambda(Lambda.getValue());
                if (points.empty()) {
                    smoothing.Smooth(Iterations.getValue());
                }
                else {
                    smoothing.SmoothPoints(Iterations.getValue(), points);
                }
                break;
            }
            case 2: {
                MeshCore::MedianFilterSmoothing smoothing(mesh.getKernel());
                if (points.empty()) {
                    smoothing.Smooth(Iterations.getValue());
                }
                else {
                    smoothing.SmoothPoints(Iterations.getValue(), points);
                }
                break;
            }
            default:
                return new App::DocumentObjectExecReturn("Unknown smoothing method");
        }
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }

    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE(Mesh::Decimation, Mesh::FixDefects)

Decimation::Decimation()
{
    static const App::PropertyFloatConstraint::Constraints reductionConstraints {
        0.0,
        100.0,
        0.1,
    };
    ADD_PROPERTY_TYPE(
        UseTargetFacetCount,
        (false),
        "Decimation",
        App::Prop_None,
        "Use TargetFacetCount instead of percentage reduction"
    );
    ADD_PROPERTY_TYPE(
        TargetFacetCount,
        (1),
        "Decimation",
        App::Prop_None,
        "Requested maximum number of output facets"
    );
    ADD_PROPERTY_TYPE(Tolerance, (0.0F), "Decimation", App::Prop_None, "Geometric decimation tolerance");
    ADD_PROPERTY_TYPE(
        Reduction,
        (50.0F),
        "Decimation",
        App::Prop_None,
        "Percentage of source facets to remove"
    );
    Reduction.setConstraints(&reductionConstraints);
}

short Decimation::mustExecute() const
{
    if (UseTargetFacetCount.isTouched() || TargetFacetCount.isTouched() || Tolerance.isTouched()
        || Reduction.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* Decimation::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }
    if (mesh.countFacets() == 0) {
        return new App::DocumentObjectExecReturn("The linked source mesh is empty");
    }

    try {
        if (UseTargetFacetCount.getValue()) {
            const int target = TargetFacetCount.getValue();
            if (target < 1 || static_cast<unsigned long>(target) >= mesh.countFacets()) {
                return new App::DocumentObjectExecReturn(
                    "TargetFacetCount must be smaller than the source and at least one"
                );
            }
            mesh.decimate(target);
        }
        else {
            const float reduction = static_cast<float>(Reduction.getValue() / 100.0);
            if (!(reduction > 0.0F && reduction < 1.0F)) {
                return new App::DocumentObjectExecReturn(
                    "Reduction must be greater than zero and less than 100 percent"
                );
            }
            mesh.decimate(static_cast<float>(Tolerance.getValue()), reduction);
        }
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }
    if (mesh.countFacets() == 0) {
        return new App::DocumentObjectExecReturn("Decimation produced an empty mesh");
    }

    Mesh.setValue(MeshObject(mesh.getKernel(), mesh.getTransform()));
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE(Mesh::Scale, Mesh::FixDefects)

Scale::Scale()
{
    static const App::PropertyFloatConstraint::Constraints constraints {
        0.0,
        std::numeric_limits<double>::max(),
        0.01,
    };
    ADD_PROPERTY_TYPE(Factor, (1.0F), "Scale", App::Prop_None, "Uniform mesh scale factor");
    Factor.setConstraints(&constraints);
}

short Scale::mustExecute() const
{
    if (Factor.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* Scale::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }

    const double factor = Factor.getValue();
    if (!(factor > 0.0) || !std::isfinite(factor)) {
        return new App::DocumentObjectExecReturn("Factor must be a finite value greater than zero");
    }
    const double maximumCoordinate = std::numeric_limits<float>::max();
    if (!std::ranges::all_of(
            mesh.getKernel().GetPoints(),
            [factor, maximumCoordinate](const MeshCore::MeshPoint& point) {
                return std::ranges::all_of(
                    std::array<double, 3> {point.x, point.y, point.z},
                    [factor, maximumCoordinate](double coordinate) {
                        const double scaled = coordinate * factor;
                        return std::isfinite(scaled) && std::abs(scaled) <= maximumCoordinate;
                    }
                );
            }
        )) {
        return new App::DocumentObjectExecReturn("Factor would create invalid mesh coordinates");
    }

    Base::Matrix4D transform;
    transform.scale(factor, factor, factor);
    mesh.getKernel().Transform(transform);
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE(Mesh::TrimByPlane, Mesh::FixDefects)

const char* TrimByPlane::SideEnums[] = {"Below", "Above", nullptr};

TrimByPlane::TrimByPlane()
{
    ADD_PROPERTY_TYPE(Plane, (nullptr), "Trim", App::Prop_None, "Linked trimming plane");
    ADD_PROPERTY_TYPE(Side, (0L), "Trim", App::Prop_None, "Side of the plane to retain");
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Trim",
        App::Prop_None,
        "Rebuild the trim when the linked source or plane changes"
    );
    Side.setEnums(SideEnums);
}

short TrimByPlane::mustExecute() const
{
    if (Plane.isTouched() || Side.isTouched() || UpdateFromSource.isTouched()
        || (Plane.getValue() && Plane.getValue()->isTouched())) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* TrimByPlane::execute()
{
    if (isSuppressed()) {
        if (UpdateFromSource.getValue()) {
            MeshObject source;
            if (auto* error = loadSourceMesh(source)) {
                return error;
            }
            Mesh.setValue(source);
        }
        return App::DocumentObject::StdReturn;
    }
    if (!UpdateFromSource.getValue()) {
        if (Mesh.getValue().countFacets() == 0) {
            return new App::DocumentObjectExecReturn(
                "The detached plane trim has no cached mesh result"
            );
        }
        return App::DocumentObject::StdReturn;
    }

    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }

    auto* plane = Plane.getValue();
    auto* placement = plane
        ? dynamic_cast<App::PropertyPlacement*>(plane->getPropertyByName("Placement"))
        : nullptr;
    if (!plane || plane->getDocument() != getDocument() || !placement) {
        return new App::DocumentObjectExecReturn(
            "Plane must link to an object in this document with a Placement property"
        );
    }

    Base::Vector3d normal(0.0, 0.0, 1.0);
    placement->getValue().getRotation().multVec(normal, normal);
    Base::Vector3f base = Base::convertTo<Base::Vector3f>(placement->getValue().getPosition());
    Base::Vector3f direction = Base::convertTo<Base::Vector3f>(normal);
    if (Side.getValue() == 1) {
        direction = -direction;
    }
    mesh.trimByPlane(base, direction);
    if (mesh.countFacets() == 0) {
        return new App::DocumentObjectExecReturn(
            "The linked plane leaves no mesh geometry on the selected side"
        );
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE(Mesh::PolygonEdit, Mesh::FixDefects)

const char* PolygonEdit::ActionEnums[] = {"Cut Facets", "Trim Facets", nullptr};
const char* PolygonEdit::RegionEnums[] = {"Inside", "Outside", nullptr};

PolygonEdit::PolygonEdit()
{
    ADD_PROPERTY_TYPE(
        Polygon,
        (),
        "Polygon",
        App::Prop_None,
        "Ordered polygon vertices in document coordinates"
    );
    ADD_PROPERTY_TYPE(
        Action,
        (0L),
        "Polygon",
        App::Prop_None,
        "Remove complete facets or clip intersected facets"
    );
    Action.setEnums(ActionEnums);
    ADD_PROPERTY_TYPE(
        Region,
        (0L),
        "Polygon",
        App::Prop_None,
        "Projected polygon region to remove"
    );
    Region.setEnums(RegionEnums);
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Polygon",
        App::Prop_None,
        "Rebuild the edit when the linked source changes"
    );
}

short PolygonEdit::mustExecute() const
{
    if (Polygon.isTouched() || Action.isTouched() || Region.isTouched()
        || UpdateFromSource.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* PolygonEdit::execute()
{
    if (isSuppressed()) {
        if (UpdateFromSource.getValue()) {
            MeshObject source;
            if (auto* error = loadSourceMesh(source)) {
                return error;
            }
            Mesh.setValue(source);
        }
        return App::DocumentObject::StdReturn;
    }
    if (!UpdateFromSource.getValue()) {
        if (Mesh.getValue().countFacets() == 0) {
            return new App::DocumentObjectExecReturn(
                "The detached polygon edit has no cached mesh result"
            );
        }
        return App::DocumentObject::StdReturn;
    }

    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }

    try {
        const MeshObject original = mesh;
        const auto vertices = Polygon.getValues();
        if (vertices.size() < 3) {
            return new App::DocumentObjectExecReturn(
                "Polygon must contain at least three model-space vertices"
            );
        }
        std::vector<Base::Vector3f> polygon;
        polygon.reserve(vertices.size());
        for (const auto& vertex : vertices) {
            if (!std::isfinite(vertex.x) || !std::isfinite(vertex.y)
                || !std::isfinite(vertex.z)) {
                return new App::DocumentObjectExecReturn(
                    "Polygon vertices must contain finite coordinates"
                );
            }
            polygon.push_back(Base::convertTo<Base::Vector3f>(vertex));
        }

        MeshCore::FlatTriangulator triangulator;
        triangulator.SetPolygon(polygon);
        Base::Matrix4D inverse = triangulator.GetTransformToFitPlane();
        Base::Matrix4D projectionMatrix = inverse;
        projectionMatrix.inverseOrthogonal();
        polygon = triangulator.ProjectToFitPlane();

        Base::Polygon2d polygon2d;
        for (const auto& vertex : polygon) {
            polygon2d.Add(Base::Vector2d(vertex.x, vertex.y));
        }
        const auto region = Region.getValue() == 0 ? MeshObject::INNER : MeshObject::OUTER;
        if (Action.getValue() == 0) {
            Base::ViewProjMatrix projection(projectionMatrix);
            mesh.cut(polygon2d, projection, region);
        }
        else {
            Base::ViewOrthoProjMatrix projection(projectionMatrix);
            mesh.trim(polygon2d, projection, region);
        }
        if (mesh.countFacets() == 0) {
            return new App::DocumentObjectExecReturn(
                "The polygon operation would remove the entire source mesh"
            );
        }
        if (sameMeshState(mesh, original)) {
            return new App::DocumentObjectExecReturn(
                "The polygon operation does not change the source mesh"
            );
        }
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const std::exception& error) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
}

PROPERTY_SOURCE(Mesh::FacetEdit, Mesh::FixDefects)

const char* FacetEdit::ActionEnums[] = {"Add Triangle", "Remove Facets", "Fill Hole", nullptr};

FacetEdit::FacetEdit()
{
    ADD_PROPERTY_TYPE(Action, (0L), "Edit", App::Prop_None, "Topology edit to replay");
    Action.setEnums(ActionEnums);
    ADD_PROPERTY_TYPE(
        Indices,
        (),
        "Edit",
        App::Prop_None,
        "Point indices for Add Triangle or facet indices for Remove Facets"
    );
    ADD_PROPERTY_TYPE(
        SeedFacet,
        (0),
        "Edit",
        App::Prop_None,
        "Facet adjacent to the boundary used by Fill Hole"
    );
    static const App::PropertyIntegerConstraint::Constraints levelConstraints {0, 10, 1};
    ADD_PROPERTY_TYPE(Level, (2), "Edit", App::Prop_None, "Interactive hole-fill refinement level");
    Level.setConstraints(&levelConstraints);
    ADD_PROPERTY_TYPE(
        AcceptedSource,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Exact source topology used to resolve the stored edit indices"
    );
}

short FacetEdit::mustExecute() const
{
    if (Action.isTouched() || Indices.isTouched() || SeedFacet.isTouched() || Level.isTouched()
        || AcceptedSource.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* FacetEdit::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }
    if (!sameMeshTopology(mesh, AcceptedSource.getValue())) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "The linked source mesh topology changed after this indexed edit was captured; "
            "select the intended topology again"
        );
    }

    try {
        if (Action.getValue() == 0) {
            const auto& values = Indices.getValues();
            if (values.size() != 3) {
                return new App::DocumentObjectExecReturn(
                    "Add Triangle requires exactly three source point indices"
                );
            }
            MeshCore::MeshFacet facet;
            for (int corner = 0; corner < 3; ++corner) {
                const long index = values[corner];
                if (index < 0 || static_cast<unsigned long>(index) >= mesh.countPoints()) {
                    return new App::DocumentObjectExecReturn(
                        "An Add Triangle point index is outside the linked source mesh"
                    );
                }
                facet._aulPoints[corner] = static_cast<PointIndex>(index);
            }
            const unsigned long before = mesh.countFacets();
            mesh.addFacets(std::vector<MeshCore::MeshFacet> {facet}, true);
            if (mesh.countFacets() != before + 1) {
                return new App::DocumentObjectExecReturn(
                    "The triangle would create invalid or non-manifold topology"
                );
            }
        }
        else if (Action.getValue() == 1) {
            auto indices = checkedFacetIndices(Indices, mesh.countFacets());
            if (indices.empty()) {
                return new App::DocumentObjectExecReturn(
                    "Remove Facets requires at least one source facet index"
                );
            }
            mesh.deleteFacets(indices);
        }
        else if (Action.getValue() == 2) {
            const long seed = SeedFacet.getValue();
            if (seed < 0 || static_cast<unsigned long>(seed) >= mesh.countFacets()) {
                return new App::DocumentObjectExecReturn(
                    "SeedFacet is outside the linked source mesh"
                );
            }
            std::list<PointIndex> border;
            MeshCore::MeshRefPointToFacets pointToFacets(mesh.getKernel());
            MeshCore::MeshAlgorithm algorithm(mesh.getKernel());
            algorithm.GetFacetBorder(static_cast<FacetIndex>(seed), border);
            std::vector<PointIndex> boundary(border.begin(), border.end());
            std::list<std::vector<PointIndex>> boundaries {boundary};
            algorithm.SplitBoundaryLoops(boundaries);

            std::vector<MeshCore::MeshFacet> newFacets;
            std::vector<Base::Vector3f> newPoints;
            unsigned long nextPoint = mesh.countPoints();
            for (auto loop : boundaries) {
                if (loop.size() < 3) {
                    continue;
                }
                MeshCore::MeshFacetArray faces;
                MeshCore::MeshPointArray points;
                MeshCore::QuasiDelaunayTriangulator triangulator;
                triangulator.SetVerifier(new MeshCore::TriangulationVerifierV2);
                if (!algorithm.FillupHole(loop, triangulator, faces, points, Level.getValue(), &pointToFacets)) {
                    continue;
                }
                if (loop.front() == loop.back()) {
                    loop.pop_back();
                }
                const unsigned long boundaryPoints = loop.size();
                for (auto point = points.begin() + boundaryPoints; point != points.end(); ++point) {
                    loop.push_back(nextPoint++);
                    newPoints.push_back(*point);
                }
                for (auto facet : faces) {
                    facet._aulPoints[0] = loop[facet._aulPoints[0]];
                    facet._aulPoints[1] = loop[facet._aulPoints[1]];
                    facet._aulPoints[2] = loop[facet._aulPoints[2]];
                    newFacets.push_back(facet);
                }
            }
            if (newFacets.empty()) {
                return new App::DocumentObjectExecReturn(
                    "The selected facet is not adjacent to a fillable hole"
                );
            }
            const unsigned long before = mesh.countFacets();
            mesh.addFacets(newFacets, newPoints, true);
            if (mesh.countFacets() != before + newFacets.size()) {
                return new App::DocumentObjectExecReturn(
                    "The hole fill would create invalid topology"
                );
            }
        }
        else {
            return new App::DocumentObjectExecReturn("Unknown facet edit action");
        }
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }

    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

PROPERTY_SOURCE(Mesh::FacetSubset, Mesh::FixDefects)

FacetSubset::FacetSubset()
{
    ADD_PROPERTY_TYPE(
        FacetIndices,
        (),
        "Selection",
        App::Prop_None,
        "Exact source facet indices retained by this result"
    );
    ADD_PROPERTY_TYPE(
        AcceptedTopology,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Source point and facet connectivity used to validate the retained "
        "facet indices"
    );
    ADD_PROPERTY_TYPE(
        AcceptedResult,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Authenticated detached facet subset retained across History changes"
    );
    ADD_PROPERTY_TYPE(
        AcceptedSourceRevision,
        (""),
        "Internal",
        App::Prop_Hidden,
        "Geometry revision of the source accepted by the detached subset"
    );
    ADD_PROPERTY_TYPE(
        AcceptedSourceStale,
        (false),
        "Internal",
        App::Prop_Hidden,
        "Whether source geometry changed after the detached subset was accepted"
    );
    ADD_PROPERTY_TYPE(
        SelectionKind,
        ("Facet subset"),
        "Selection",
        App::Prop_ReadOnly,
        "Meaning of the accepted facet selection"
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Selection",
        App::Prop_None,
        "Rebuild this subset when the linked source changes"
    );
}

short FacetSubset::mustExecute() const
{
    if (FacetIndices.isTouched() || AcceptedTopology.isTouched()
        || AcceptedResult.isTouched() || AcceptedSourceRevision.isTouched()
        || AcceptedSourceStale.isTouched() || UpdateFromSource.isTouched()
        || SelectionKind.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

void FacetSubset::onDocumentRestored()
{
    Mesh::FixDefects::onDocumentRestored();
    if (UpdateFromSource.getValue() || AcceptedSourceStale.getValue()
        || AcceptedResult.getValue().countFacets() == 0) {
        return;
    }
    const auto* source = dynamic_cast<const Mesh::Feature*>(Source.getValue());
    if (!source || source == this || source->getDocument() != getDocument()) {
        AcceptedSourceStale.setValue(true);
        return;
    }
    AcceptedSourceRevision.setValue(
        std::to_string(source->Mesh.getGeometryRevision())
    );
}

App::DocumentObjectExecReturn* FacetSubset::execute()
{
    if (!UpdateFromSource.getValue()) {
        const auto* source = dynamic_cast<const Mesh::Feature*>(Source.getValue());
        if (!source || source == this || !getDocument()
            || source->getDocument() != getDocument()
            || !getDocument()->containsObject(source)) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "The detached Mesh facet subset has no live source"
            );
        }
        if (std::to_string(source->Mesh.getGeometryRevision())
            != AcceptedSourceRevision.getValue()) {
            AcceptedSourceStale.setValue(true);
        }
        if (AcceptedSourceStale.getValue()) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "The linked source geometry changed after this background facet subset "
                "was accepted; rerun the segmentation"
            );
        }
        if (AcceptedResult.getValue().countFacets() == 0) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "The detached Mesh facet subset has no cached result"
            );
        }
        MeshObject restored(AcceptedResult.getValue());
        restored.setTransform(source->Placement.getValue().toMatrix());
        Mesh.setValue(restored);
        return App::DocumentObject::StdReturn;
    }
    MeshObject source;
    if (auto* error = loadSourceMesh(source)) {
        Mesh.setValue(MeshObject());
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(source);
        return App::DocumentObject::StdReturn;
    }
    if (!sameMeshTopology(source, AcceptedTopology.getValue())) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "The linked source mesh topology changed after this facet "
            "selection was accepted; select the intended facets again"
        );
    }

    try {
        const auto indices =
            checkedFacetIndices(FacetIndices, source.countFacets());
        if (indices.empty()) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "A facet subset requires at least one source facet"
            );
        }

        std::unique_ptr<MeshObject> result(
            source.meshFromSegment(indices)
        );
        if (!result || result->countFacets() == 0) {
            Mesh.setValue(MeshObject());
            return new App::DocumentObjectExecReturn(
                "The accepted facet subset produced an empty mesh"
            );
        }

        std::unordered_map<FacetIndex, FacetIndex> remap;
        remap.reserve(indices.size());
        for (FacetIndex index = 0;
             index < static_cast<FacetIndex>(indices.size());
             ++index) {
            remap.emplace(indices[index], index);
        }
        for (unsigned long segmentIndex = 0;
             segmentIndex < source.countSegments();
             ++segmentIndex) {
            const auto& sourceSegment =
                source.getSegment(segmentIndex);
            std::vector<FacetIndex> segmentIndices;
            for (FacetIndex sourceIndex :
                 sourceSegment.getIndices()) {
                if (const auto found = remap.find(sourceIndex);
                    found != remap.end()) {
                    segmentIndices.push_back(found->second);
                }
            }
            if (segmentIndices.empty()) {
                continue;
            }
            Segment segment(result.get(), segmentIndices, false);
            segment.setName(sourceSegment.getName());
            segment.setColor(sourceSegment.getColor());
            segment.save(sourceSegment.isSaved());
            result->addSegment(segment);
        }

        Mesh.setValue(*result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
}

PROPERTY_SOURCE(Mesh::StoredEdit, Mesh::FixDefects)

StoredEdit::StoredEdit()
{
    ADD_PROPERTY_TYPE(
        AcceptedSource,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Exact source mesh against which this result was accepted"
    );
    ADD_PROPERTY_TYPE(
        AcceptedResult,
        (MeshObject()),
        "Internal",
        App::Prop_Hidden,
        "Exact accepted operation result"
    );
    ADD_PROPERTY_TYPE(
        EditKind,
        ("Mesh edit"),
        "Edit",
        App::Prop_ReadOnly,
        "Operation that produced the accepted result"
    );
}

short StoredEdit::mustExecute() const
{
    if (AcceptedSource.isTouched() || AcceptedResult.isTouched() || EditKind.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* StoredEdit::execute()
{
    MeshObject source;
    if (auto* error = loadSourceMesh(source)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(source);
        return App::DocumentObject::StdReturn;
    }
    if (!sameMeshState(source, AcceptedSource.getValue())) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(
            "The linked source mesh changed after this accepted edit; rerun the operation"
        );
    }
    Mesh.setValue(AcceptedResult.getValue());
    return App::DocumentObject::StdReturn;
}


PROPERTY_SOURCE(Mesh::GmshRemesh, Mesh::FixDefects)

GmshRemesh::GmshRemesh()
{
    ADD_PROPERTY_TYPE(
        Algorithm,
        (2),
        "Gmsh",
        App::Prop_None,
        "Gmsh two-dimensional meshing algorithm identifier"
    );
    ADD_PROPERTY_TYPE(
        MinimumElementSize,
        (0.0),
        "Gmsh",
        App::Prop_None,
        "Minimum remeshed element size"
    );
    ADD_PROPERTY_TYPE(
        MaximumElementSize,
        (0.0),
        "Gmsh",
        App::Prop_None,
        "Maximum remeshed element size; zero leaves it unbounded"
    );
    ADD_PROPERTY_TYPE(
        SurfaceAngle,
        (40.0),
        "Gmsh",
        App::Prop_None,
        "Angle used to classify discrete source surfaces"
    );
    ADD_PROPERTY_TYPE(
        Executable,
        ("gmsh"),
        "Gmsh",
        App::Prop_None,
        "Gmsh executable used for every recompute"
    );
    ADD_PROPERTY_TYPE(
        TimeoutSeconds,
        (600),
        "Gmsh",
        App::Prop_None,
        "Maximum time allowed for a Gmsh recompute"
    );
    static const App::PropertyIntegerConstraint::Constraints timeoutRange = {
        1,
        86400,
        1,
    };
    TimeoutSeconds.setConstraints(&timeoutRange);
    ADD_PROPERTY_TYPE(
        CachedSource,
        (MeshObject()),
        "Gmsh Cache",
        static_cast<App::PropertyType>(
            App::Prop_Hidden | App::Prop_NoRecompute
        ),
        "Exact source state corresponding to the accepted remesh cache"
    );
    ADD_PROPERTY_TYPE(
        CachedResult,
        (MeshObject()),
        "Gmsh Cache",
        static_cast<App::PropertyType>(
            App::Prop_Hidden | App::Prop_NoRecompute
        ),
        "Accepted Gmsh remesh reused until source or settings change"
    );
    CachedSource.setStatus(App::Property::Hidden, true);
    CachedSource.setStatus(App::Property::NoRecompute, true);
    CachedResult.setStatus(App::Property::Hidden, true);
    CachedResult.setStatus(App::Property::NoRecompute, true);
}

short GmshRemesh::mustExecute() const
{
    if (Algorithm.isTouched() || MinimumElementSize.isTouched()
        || MaximumElementSize.isTouched() || SurfaceAngle.isTouched()
        || Executable.isTouched() || TimeoutSeconds.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* GmshRemesh::execute()
{
    MeshObject source;
    if (auto* error = loadSourceMesh(source)) {
        Mesh.setValue(MeshObject());
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(source);
        return App::DocumentObject::StdReturn;
    }

    try {
        const double minimumSize = MinimumElementSize.getValue();
        const double maximumSize = MaximumElementSize.getValue();
        const double angle = SurfaceAngle.getValue();
        if (!std::isfinite(minimumSize) || minimumSize < 0.0
            || !std::isfinite(maximumSize) || maximumSize < 0.0
            || (maximumSize > 0.0 && minimumSize > maximumSize)) {
            throw Base::ValueError(
                "Gmsh element sizes must be finite, non-negative, and "
                "ordered minimum-to-maximum"
            );
        }
        if (!std::isfinite(angle) || angle <= 0.0
            || angle >= 180.0) {
            throw Base::ValueError(
                "SurfaceAngle must be between zero and 180 degrees"
            );
        }

        const bool settingsTouched = Algorithm.isTouched()
            || MinimumElementSize.isTouched()
            || MaximumElementSize.isTouched()
            || SurfaceAngle.isTouched() || Executable.isTouched()
            || TimeoutSeconds.isTouched();
        MeshObject result;
        if (!settingsTouched
            && CachedResult.getValue().countFacets() > 0
            && sameMeshState(source, CachedSource.getValue())) {
            result = CachedResult.getValue();
        }
        else {
            result = runGmshRemesh(
                source,
                Algorithm.getValue(),
                minimumSize,
                maximumSize,
                angle,
                Executable.getValue(),
                TimeoutSeconds.getValue()
            );
            CachedSource.setValue(source);
            CachedResult.setValue(result);
        }
        if (result.countFacets() == 0) {
            throw Base::RuntimeError(
                "Gmsh produced an empty remesh"
            );
        }
        Mesh.setValue(result);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Mesh.setValue(MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
}

}  // namespace Mesh
