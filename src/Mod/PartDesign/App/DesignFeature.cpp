// SPDX-License-Identifier: LGPL-2.1-or-later

#include "DesignFeature.h"

#include <BRepAlgo.hxx>
#include <BRepAlgoAPI_Splitter.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBndLib.hxx>
#include <BRepClass3d_SolidClassifier.hxx>
#include <BRepClass3d_SolidExplorer.hxx>
#include <BRepGProp.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GProp_GProps.hxx>
#include <Geom_Curve.hxx>
#include <Geom_Line.hxx>
#include <Geom_Plane.hxx>
#include <GeomAPI_IntSS.hxx>
#include <Precision.hxx>
#include <ShapeFix_ShapeTolerance.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopExp.hxx>
#include <TopoDS.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Lin.hxx>
#include <gp_Pln.hxx>
#include <gp_Trsf.hxx>

#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <numbers>
#include <optional>
#include <ranges>
#include <sstream>
#include <string_view>
#include <unordered_map>
#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/Datums.h>
#include <App/GeoFeature.h>
#include <App/GeoFeatureGroupExtension.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Base/Uuid.h>
#include <Mod/Part/App/SignalException.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/Tools.h>
#include <Mod/Part/App/TopoShapeOpCode.h>

#include "Body.h"
#include "DatumLine.h"
#include "DatumPlane.h"

using namespace PartDesign;

namespace
{

constexpr long currentDesignSchemaVersion = 2;

Part::TopoShape shapeInDesignCoordinates(const Part::Feature& feature)
{
    Part::TopoShape shape = feature.Shape.getShape();
    if (shape.isNull()) {
        return shape;
    }

    const Base::Placement local = feature.Placement.getValue();
    const Base::Placement container = App::GeoFeature::getGlobalPlacement(&feature) * local.inverse();
    // Bake only the containing Component/Body transform into a copy. A
    // Part::Feature already stores its own Placement in Shape; merely changing
    // TopoShape::Placement here would be overwritten by Part::Feature's
    // Placement synchronisation on the next property notification.
    shape.transformShape(container.toMatrix(), true, true);
    return shape;
}

Part::TopoShape transformedShape(const Part::TopoShape& shape, const Base::Placement& placement)
{
    if (shape.isNull()) {
        return shape;
    }

    Part::TopoShape transformed = shape;
    transformed.transformShape(placement.toMatrix(), true, true);
    return transformed;
}

Part::TopoShape shapeInBodyStateCoordinates(const Part::Feature& feature)
{
    // New DesignBodyState shapes are stored in the persistent Body-local
    // frame. Legacy Body-owned Part features already expose their shape in
    // that same containing-Body frame. Neither representation includes the
    // Component/occurrence placement.
    return feature.Shape.getShape();
}

App::DocumentObjectExecReturn* outputError(const std::string& message)
{
    return new App::DocumentObjectExecReturn(message);
}

Part::TopoShape exactSingleInputContextShape(
    const App::DocumentObject& controller,
    const DesignOperationProperties& operation,
    std::string_view termination
)
{
    const auto& inputs = operation.InputStates.getValues();
    const auto& frames = operation.InputFrames.getValues();
    if (inputs.size() != 1 || frames.size() != 1) {
        throw Base::ValueError(
            std::string(termination)
            + " requires exactly one explicit target Body because its extent depends on that "
              "Body's prior state"
        );
    }
    auto* input = freecad_cast<Part::Feature*>(inputs.front());
    if (!input || input == &controller || input->getDocument() != controller.getDocument()) {
        throw Base::ValueError(
            std::string(termination) + " lost its exact prior Body state"
        );
    }
    Part::TopoShape shape = shapeInBodyStateCoordinates(*input);
    if (shape.isNull() || !shape.hasSubShape(TopAbs_SOLID)) {
        throw Base::ValueError(
            std::string(termination) + " requires a solid prior Body state"
        );
    }
    return transformedShape(shape, frames.front());
}

Part::TopoShape featureToolInDesignCoordinates(const PartDesign::FeatureAddSub& feature);

void bindDesignIdentity(App::DocumentObject& object, App::PropertyUUID& designId)
{
    auto* document = object.getDocument();
    if (!document || document->testStatus(App::Document::Restoring)) {
        return;
    }
    if (auto* timeline = App::DocumentTimeline::ensure(document)) {
        designId.setValue(timeline->DesignId.getValue());
    }
}

Body* bodyWithIdentity(const App::Document& document, const std::string& identity)
{
    Body* result = nullptr;
    for (auto* body : document.getObjectsOfType<Body>()) {
        if (!body || body->VibeCADBodyId.getValueStr() != identity) {
            continue;
        }
        if (result) {
            return nullptr;
        }
        result = body;
    }
    return result;
}

Base::Placement designReferenceFrame(const App::DocumentObject* reference)
{
    if (!reference) {
        return {};
    }

    if (const auto* state = freecad_cast<const DesignBodyState*>(reference)) {
        if (auto* body = reference->getDocument()
                ? bodyWithIdentity(*reference->getDocument(), state->BodyId.getValueStr())
                : nullptr) {
            return App::GeoFeature::getGlobalPlacement(body);
        }
    }

    if (auto* body = freecad_cast<Body*>(const_cast<App::DocumentObject*>(reference))) {
        return App::GeoFeature::getGlobalPlacement(body);
    }
    if (auto* body = Body::findBodyOf(reference)) {
        return App::GeoFeature::getGlobalPlacement(body);
    }

    if (const auto* feature = freecad_cast<const App::GeoFeature*>(reference)) {
        return App::GeoFeature::getGlobalPlacement(feature) * feature->Placement.getValue().inverse();
    }
    return {};
}

struct DesignAxis
{
    gp_Pnt origin;
    gp_Dir direction;
};

enum class DesignReferenceKind
{
    MirrorPlane,
    LinearDirection,
    RotationAxis,
};

DesignAxis transformAxis(DesignAxis axis, const Base::Placement& placement)
{
    const gp_Trsf transform = Part::TopoShape::convert(placement.toMatrix());
    axis.origin.Transform(transform);
    axis.direction.Transform(transform);
    return axis;
}

std::string firstReferenceSubelement(const App::PropertyLinkSub& reference)
{
    const auto subelements = reference.getSubValues();
    return subelements.empty() ? std::string() : subelements.front();
}

Part::TopoShape designReferenceShape(const App::PropertyLinkSub& reference, const Base::Placement& frame)
{
    const auto* feature = freecad_cast<const Part::Feature*>(reference.getValue());
    if (!feature || feature->Shape.getShape().isNull()) {
        return {};
    }

    Part::TopoShape shape = transformedShape(feature->Shape.getShape(), frame);
    const std::string subelement = firstReferenceSubelement(reference);
    if (subelement.empty()) {
        return shape;
    }
    const TopoDS_Shape subshape = shape.getSubShape(subelement.c_str());
    return subshape.IsNull() ? Part::TopoShape() : Part::TopoShape(subshape);
}

DesignAxis sketchAxis(
    const Part::Part2DObject& sketch,
    const std::string& subelement,
    const Base::Placement& frame
)
{
    Base::Axis axis;
    if (subelement.empty() || subelement == "N_Axis") {
        axis = sketch.getAxis(Part::Part2DObject::N_Axis);
    }
    else if (subelement == "H_Axis") {
        axis = sketch.getAxis(Part::Part2DObject::H_Axis);
    }
    else if (subelement == "V_Axis") {
        axis = sketch.getAxis(Part::Part2DObject::V_Axis);
    }
    else if (subelement.starts_with("Axis")) {
        const int axisIndex = std::atoi(subelement.substr(4).c_str());
        if (axisIndex < 0 || axisIndex >= sketch.getAxisCount()) {
            throw Base::ValueError("The selected sketch axis no longer exists");
        }
        axis = sketch.getAxis(axisIndex);
    }
    else {
        throw Base::ValueError("Select a sketch axis, line, circular edge, or planar face");
    }

    const Base::Placement placement = frame * sketch.Placement.getValue();
    return transformAxis(
        {
            gp_Pnt(axis.getBase().x, axis.getBase().y, axis.getBase().z),
            gp_Dir(axis.getDirection().x, axis.getDirection().y, axis.getDirection().z),
        },
        placement
    );
}

DesignAxis resolveDesignAxisReference(
    const App::PropertyLinkSub& reference,
    const Base::Placement& frame,
    DesignReferenceKind kind
)
{
    auto* object = reference.getValue();
    if (!object) {
        throw Base::ValueError("No geometric reference is selected");
    }

    const std::string subelement = firstReferenceSubelement(reference);
    if (const auto* sketch = freecad_cast<const Part::Part2DObject*>(object)) {
        if (kind == DesignReferenceKind::MirrorPlane && !subelement.empty()
            && subelement != "N_Axis") {
            throw Base::TypeError("Mirror requires the sketch plane, not an in-plane sketch axis");
        }
        return sketchAxis(*sketch, subelement, frame);
    }
    if (const auto* line = freecad_cast<const App::Line*>(object)) {
        if (kind == DesignReferenceKind::MirrorPlane) {
            throw Base::TypeError("Mirror requires a datum plane, sketch plane, or planar face");
        }
        const Base::Vector3d base = line->getBasePoint();
        const Base::Vector3d direction = line->getDirection();
        return transformAxis(
            {
                gp_Pnt(base.x, base.y, base.z),
                gp_Dir(direction.x, direction.y, direction.z),
            },
            frame
        );
    }
    if (const auto* plane = freecad_cast<const App::Plane*>(object)) {
        if (kind != DesignReferenceKind::MirrorPlane) {
            throw Base::TypeError("A plane cannot define a line direction or rotation axis");
        }
        const Base::Vector3d base = plane->getBasePoint();
        const Base::Vector3d direction = plane->getDirection();
        return transformAxis(
            {
                gp_Pnt(base.x, base.y, base.z),
                gp_Dir(direction.x, direction.y, direction.z),
            },
            frame
        );
    }

    const Part::TopoShape shape = designReferenceShape(reference, frame);
    if (shape.isNull()) {
        throw Base::ValueError("The selected geometric reference has no usable shape");
    }
    const TopoDS_Shape topologicalShape = shape.getShape();
    if (topologicalShape.ShapeType() == TopAbs_EDGE) {
        if (kind == DesignReferenceKind::MirrorPlane) {
            throw Base::TypeError("Mirror requires a datum plane, sketch plane, or planar face");
        }
        BRepAdaptor_Curve curve(TopoDS::Edge(topologicalShape));
        if (curve.GetType() == GeomAbs_Line) {
            const gp_Lin line = curve.Line();
            return {line.Location(), line.Direction()};
        }
        if (curve.GetType() == GeomAbs_Circle) {
            if (kind == DesignReferenceKind::LinearDirection) {
                throw Base::TypeError("Linear Pattern direction requires a straight edge");
            }
            const gp_Circ circle = curve.Circle();
            return {
                circle.Location(),
                circle.Axis().Direction(),
            };
        }
        throw Base::TypeError(
            kind == DesignReferenceKind::LinearDirection
                ? "Linear Pattern direction requires a straight edge"
                : "Circular Pattern axis requires a straight or circular edge"
        );
    }
    if (kind == DesignReferenceKind::MirrorPlane && topologicalShape.ShapeType() == TopAbs_FACE) {
        BRepAdaptor_Surface surface(TopoDS::Face(topologicalShape));
        if (surface.GetType() != GeomAbs_Plane) {
            throw Base::TypeError("The selected mirror face must be planar");
        }
        const gp_Pln plane = surface.Plane();
        return {
            plane.Location(),
            plane.Axis().Direction(),
        };
    }
    throw Base::TypeError(
        kind == DesignReferenceKind::MirrorPlane
            ? "Select a datum plane, sketch plane, or planar face"
            : kind == DesignReferenceKind::LinearDirection
            ? "Select a datum axis, sketch axis, or straight edge"
            : "Select a datum axis, sketch axis, straight edge, or circular edge"
    );
}

Base::Placement draftReferenceToTarget(
    const Base::Placement& referenceFrame,
    const Base::Placement& targetFrame
)
{
    return targetFrame.inverse() * referenceFrame;
}

gp_Dir transformedDirection(const Base::Vector3d& direction, const Base::Placement& placement)
{
    Base::Vector3d transformed = placement.getRotation().multVec(direction);
    if (transformed.Sqr() <= Precision::SquareConfusion()) {
        throw Base::ValueError("The selected pull direction has zero length");
    }
    return gp_Dir(transformed.x, transformed.y, transformed.z);
}

gp_Pln transformedPlane(const gp_Pln& plane, const Base::Placement& placement)
{
    gp_Pln transformed = plane;
    transformed.Transform(Part::Tools::fromPlacement(placement).Transformation());
    return transformed;
}

Part::TopoShape draftReferenceShape(
    const Part::Feature& feature,
    const Base::Placement& referenceFrame,
    const Base::Placement& targetFrame
)
{
    return transformedShape(
        feature.Shape.getShape(),
        draftReferenceToTarget(referenceFrame, targetFrame)
    );
}

std::string requiredReferenceSubelement(const App::PropertyLinkSub& property, const char* referenceName)
{
    const auto subelements = property.getSubValues();
    if (subelements.size() != 1 || subelements.front().empty()) {
        throw Base::ValueError(std::string(referenceName) + " requires exactly one subelement");
    }
    return subelements.front();
}

std::optional<gp_Dir> resolveDraftPullDirection(
    const DesignDraft& draft,
    const Base::Placement& targetFrame
)
{
    auto* reference = draft.PullDirection.getValue();
    if (!reference) {
        return std::nullopt;
    }
    const Base::Placement referenceToTarget
        = draftReferenceToTarget(draft.PullDirectionFrame.getValue(), targetFrame);

    if (const auto* line = freecad_cast<const PartDesign::Line*>(reference)) {
        return transformedDirection(line->getDirection(), referenceToTarget);
    }
    if (const auto* line = freecad_cast<const App::Line*>(reference)) {
        return transformedDirection(line->getDirection(), referenceToTarget);
    }
    const auto* feature = freecad_cast<const Part::Feature*>(reference);
    if (!feature) {
        throw Base::TypeError("Pull direction must reference a datum axis or linear edge");
    }

    const std::string subelement = requiredReferenceSubelement(draft.PullDirection, "Pull direction");
    Part::TopoShape shape
        = draftReferenceShape(*feature, draft.PullDirectionFrame.getValue(), targetFrame);
    Part::TopoShape selected = shape.getSubTopoShape(subelement.c_str());
    if (selected.isNull() || selected.shapeType() != TopAbs_EDGE) {
        throw Base::TypeError("Pull direction must reference one linear edge");
    }
    BRepAdaptor_Curve curve(TopoDS::Edge(selected.getShape()));
    if (curve.GetType() != GeomAbs_Line) {
        throw Base::TypeError("Pull direction reference edge must be linear");
    }
    return curve.Line().Direction();
}

gp_Pln inferDraftNeutralPlane(const Part::TopoShape& selectedFace)
{
    if (selectedFace.isNull() || selectedFace.shapeType() != TopAbs_FACE) {
        throw Base::TypeError("A neutral plane can only be inferred from a selected face");
    }

    TopTools_IndexedMapOfShape edges;
    TopExp::MapShapes(selectedFace.getShape(), TopAbs_EDGE, edges);
    for (int index = 1; index <= edges.Extent(); ++index) {
        BRepAdaptor_Curve curve(TopoDS::Edge(edges(index)));
        const gp_Pnt start = curve.Value(curve.FirstParameter());
        const gp_Pnt end = curve.Value(curve.LastParameter());

        if (curve.IsClosed()) {
            if (curve.GetType() == GeomAbs_Circle) {
                return gp_Pln(start, curve.Circle().Axis().Direction());
            }
            continue;
        }
        if (curve.GetType() != GeomAbs_Line) {
            continue;
        }

        const gp_Pnt midpoint = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2.0);
        Handle(Geom_Plane) auxiliary = new Geom_Plane(
            midpoint,
            gp_Dir(end.X() - start.X(), end.Y() - start.Y(), end.Z() - start.Z())
        );
        BRepAdaptor_Surface surface(TopoDS::Face(selectedFace.getShape()), Standard_False);
        GeomAPI_IntSS intersection(auxiliary, surface.Surface().Surface(), Precision::Confusion());
        if (!intersection.IsDone() || intersection.NbLines() < 1) {
            continue;
        }
        const Handle(Geom_Curve)& intersectionCurve = intersection.Line(1);
        if (!intersectionCurve->IsKind(STANDARD_TYPE(Geom_Line))) {
            continue;
        }
        const Handle(Geom_Line) line = Handle(Geom_Line)::DownCast(intersectionCurve);
        return gp_Pln(midpoint, line->Lin().Direction());
    }

    throw Base::RuntimeError(
        "No neutral plane was selected and none can be inferred from the first drafted face"
    );
}

gp_Pln resolveDraftNeutralPlane(
    const DesignDraft& draft,
    const Part::TopoShape& selectedFace,
    const std::optional<gp_Dir>& pullDirection,
    const Base::Placement& targetFrame
)
{
    auto* reference = draft.NeutralPlane.getValue();
    if (!reference) {
        return inferDraftNeutralPlane(selectedFace);
    }

    const Base::Placement referenceToTarget
        = draftReferenceToTarget(draft.NeutralPlaneFrame.getValue(), targetFrame);
    if (const auto* plane = freecad_cast<const PartDesign::Plane*>(reference)) {
        const Base::Vector3d point = plane->Placement.getValue().getPosition();
        Base::Vector3d normal = Base::Vector3d::UnitZ;
        plane->Placement.getValue().getRotation().multVec(normal, normal);
        return transformedPlane(
            gp_Pln(gp_Pnt(point.x, point.y, point.z), gp_Dir(normal.x, normal.y, normal.z)),
            referenceToTarget
        );
    }
    if (const auto* plane = freecad_cast<const App::Plane*>(reference)) {
        const Base::Vector3d point = plane->getBasePoint();
        const Base::Vector3d normal = plane->getDirection();
        return transformedPlane(
            gp_Pln(gp_Pnt(point.x, point.y, point.z), gp_Dir(normal.x, normal.y, normal.z)),
            referenceToTarget
        );
    }
    if (reference->isDerivedFrom<Part::Part2DObject>()) {
        const auto* plane = freecad_cast<const App::GeoFeature*>(reference);
        const Base::Placement placement = plane->Placement.getValue();
        Base::Vector3d normal = Base::Vector3d::UnitZ;
        placement.getRotation().multVec(normal, normal);
        const Base::Vector3d point = placement.getPosition();
        return transformedPlane(
            gp_Pln(gp_Pnt(point.x, point.y, point.z), gp_Dir(normal.x, normal.y, normal.z)),
            referenceToTarget
        );
    }

    const auto* feature = freecad_cast<const Part::Feature*>(reference);
    if (!feature) {
        throw Base::TypeError(
            "Neutral plane must reference a datum plane, planar sketch, face, or linear edge"
        );
    }
    const std::string subelement = requiredReferenceSubelement(draft.NeutralPlane, "Neutral plane");
    Part::TopoShape shape
        = draftReferenceShape(*feature, draft.NeutralPlaneFrame.getValue(), targetFrame);
    Part::TopoShape selected = shape.getSubTopoShape(subelement.c_str());
    if (selected.isNull()) {
        throw Base::ValueError("The neutral-plane reference no longer exists");
    }
    if (selected.shapeType() == TopAbs_FACE) {
        BRepAdaptor_Surface surface(TopoDS::Face(selected.getShape()));
        if (surface.GetType() != GeomAbs_Plane) {
            throw Base::TypeError("Neutral-plane reference face must be planar");
        }
        return surface.Plane();
    }
    if (selected.shapeType() != TopAbs_EDGE || !pullDirection) {
        throw Base::TypeError("A neutral-plane edge requires an explicit pull direction");
    }

    BRepAdaptor_Curve curve(TopoDS::Edge(selected.getShape()));
    if (curve.GetType() != GeomAbs_Line) {
        throw Base::TypeError("Neutral-plane reference edge must be linear");
    }
    const double angle = curve.Line().Angle(
        gp_Lin(curve.Value(curve.FirstParameter()), *pullDirection)
    );
    if (std::fabs(angle - std::numbers::pi / 2) > Precision::Angular()) {
        throw Base::ValueError("Neutral-plane reference edge must be normal to the pull direction");
    }
    return gp_Pln(curve.Value(curve.FirstParameter()), *pullDirection);
}

bool stateMatchesBody(const App::DocumentObject* input, const Body& body, const std::string& bodyId)
{
    if (const auto* state = freecad_cast<const DesignBodyState*>(input)) {
        return state->BodyId.getValueStr() == bodyId;
    }

    // Legacy Body-owned features remain valid exact input states during the
    // additive migration. New Design operations never create this ownership.
    return body.hasObject(input);
}

App::DocumentObjectExecReturn* computeSuppressedOutputs(
    Part::Feature& controller,
    DesignOperationProperties& operation
)
{
    try {
        ensureDesignOperationPortSchema(controller);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const auto& inputs = operation.InputStates.getValues();
    const auto& inputBodyIds = operation.InputBodyIds.getValues();
    const auto& inputFrames = operation.InputFrames.getValues();
    const auto& outputBodyIds = operation.OutputBodyIds.getValues();
    const auto& outputFrames = operation.OutputFrames.getValues();
    const auto& previousIndices = operation.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = operation.OutputComponentIds.getValues();
    if (inputs.size() != inputBodyIds.size() || inputs.size() != inputFrames.size()
        || outputBodyIds.empty() || outputFrames.size() != outputBodyIds.size()
        || previousIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError("The suppressed operation has inconsistent input or output ports");
    }

    std::vector<Part::TopoShape> outputs;
    outputs.reserve(outputBodyIds.size());
    boost::dynamic_bitset<> outputPresence(outputBodyIds.size());
    for (std::size_t index = 0; index < outputBodyIds.size(); ++index) {
        const long previousIndex = previousIndices[index];
        if (previousIndex < 0) {
            if (previousIndex != -1) {
                return outputError("An operation-created output has an invalid input mapping");
            }
            outputs.emplace_back();
            continue;
        }
        if (static_cast<std::size_t>(previousIndex) >= inputs.size()
            || inputBodyIds[previousIndex] != outputBodyIds[index]) {
            return outputError("A suppressed output does not advance its matching input Body");
        }
        auto* feature = freecad_cast<Part::Feature*>(inputs[previousIndex]);
        if (!feature || feature == &controller || feature->getDocument() != controller.getDocument()) {
            return outputError("A suppressed operation lost an exact prior Body state");
        }
        const auto* designState = freecad_cast<const DesignBodyState*>(feature);
        const bool present = !designState || designState->Present.getValue();
        if (present) {
            outputs.push_back(shapeInBodyStateCoordinates(*feature));
        }
        else {
            outputs.emplace_back();
        }
        outputPresence[index] = present;
    }
    operation.OutputShapes.setValues(outputs);
    operation.OutputPresence.setValues(outputPresence);
    return App::DocumentObject::StdReturn;
}

App::DocumentObjectExecReturn* computeOutputShapes(
    Part::Feature& toolFeature,
    DesignOperationProperties& operation,
    double fuzzyTolerance
)
{
    try {
        ensureDesignOperationPortSchema(toolFeature);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const auto& inputs = operation.InputStates.getValues();
    const auto& inputBodyIds = operation.InputBodyIds.getValues();
    const auto& inputFrames = operation.InputFrames.getValues();
    const auto& outputBodyIds = operation.OutputBodyIds.getValues();
    const auto& outputFrames = operation.OutputFrames.getValues();
    const auto& previousInputIndices = operation.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = operation.OutputComponentIds.getValues();
    const std::string_view resultOperation = operation.ResultOperation.getValueAsString();
    auto* additive = freecad_cast<PartDesign::FeatureAddSub*>(&toolFeature);
    Part::TopoShape tool = additive ? featureToolInDesignCoordinates(*additive)
                                    : shapeInDesignCoordinates(toolFeature);

    // The operation is a History/controller object, never a second rendered
    // result. Body-state resources publish its outputs in their local frames.
    toolFeature.Shape.setValue(Part::TopoShape());
    if (tool.isNull()) {
        return outputError("The operation did not generate a valid tool solid");
    }

    if (inputs.size() != inputBodyIds.size() || inputs.size() != inputFrames.size()
        || outputBodyIds.empty() || outputFrames.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError("The operation has inconsistent input or output ports");
    }

    std::unordered_set<std::string> uniqueBodyIds;
    for (const auto& bodyId : outputBodyIds) {
        if (bodyId.empty() || !uniqueBodyIds.insert(bodyId).second) {
            return outputError("Every output Body must have one distinct persistent identity");
        }
    }

    if (resultOperation == "New Body") {
        if (outputBodyIds.size() != 1 || !inputs.empty() || previousInputIndices.front() != -1) {
            return outputError("New Body requires no inputs and one created output Body");
        }
        if (!tool.hasSubShape(TopAbs_SOLID) || tool.countSubShapes(TopAbs_SOLID) != 1) {
            return outputError("New Body requires exactly one solid result; select one "
                               "connected profile region or use a multi-result operation");
        }
        operation.OutputShapes.setValues({transformedShape(tool, outputFrames.front().inverse())});
        boost::dynamic_bitset<> outputPresence(1);
        outputPresence.set();
        operation.OutputPresence.setValues(outputPresence);
        return App::DocumentObject::StdReturn;
    }

    if (inputs.empty() || inputs.size() != outputBodyIds.size()) {
        return outputError("Join, Cut, and Intersect require one exact prior state for every "
                           "output Body");
    }
    for (std::size_t index = 0; index < inputs.size(); ++index) {
        if (previousInputIndices[index] != static_cast<long>(index)
            || inputBodyIds[index] != outputBodyIds[index]
            || inputFrames[index] != outputFrames[index] || !outputComponentIds[index].empty()) {
            return outputError(
                "This pointwise operation requires matching input and output Body ports"
            );
        }
    }

    const char* opcode = nullptr;
    if (resultOperation == "Join") {
        opcode = Part::OpCodes::Fuse;
    }
    else if (resultOperation == "Cut") {
        opcode = Part::OpCodes::Cut;
    }
    else if (resultOperation == "Intersect") {
        opcode = Part::OpCodes::Common;
    }
    else {
        return outputError("The operation has an unsupported result mode");
    }

    std::unordered_set<App::DocumentObject*> uniqueInputs;
    std::vector<Part::TopoShape> outputs;
    outputs.reserve(inputs.size());
    try {
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            auto* input = freecad_cast<Part::Feature*>(inputs[index]);
            auto* body = toolFeature.getDocument()
                ? bodyWithIdentity(*toolFeature.getDocument(), outputBodyIds[index])
                : nullptr;
            if (!input || input == &toolFeature || input->getDocument() != toolFeature.getDocument()
                || !uniqueInputs.insert(input).second || !body
                || !stateMatchesBody(input, *body, outputBodyIds[index])) {
                return outputError("The operation lost an exact prior state for one target Body");
            }

            Part::TopoShape base = shapeInBodyStateCoordinates(*input);
            if (base.isNull()) {
                return outputError("A target Body has no valid prior solid state");
            }
            const Part::TopoShape localTool = transformedShape(tool, outputFrames[index].inverse());

            bool intersects = false;
            for (const auto& toolSolid : localTool.getSubTopoShapes(TopAbs_SOLID)) {
                Part::TopoShape overlap;
                overlap.makeElementBoolean(
                    Part::OpCodes::Common,
                    {base, toolSolid},
                    nullptr,
                    fuzzyTolerance
                );
                if (!overlap.isNull() && overlap.hasSubShape(TopAbs_SOLID)) {
                    intersects = true;
                    break;
                }
            }
            if (!intersects) {
                return outputError(
                    std::string("The operation does not intersect selected Body '")
                    + body->Label.getValue() + "'"
                );
            }

            Part::TopoShape output;
            output.makeElementBoolean(opcode, {base, localTool}, nullptr, fuzzyTolerance);
            if (output.isNull() || !output.hasSubShape(TopAbs_SOLID)) {
                return outputError("The operation produced an empty or non-solid Body result");
            }
            if (output.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError(
                    "The operation would split one Body into multiple solids; "
                    "use a Split operation so every result receives a stable Body identity"
                );
            }
            outputs.push_back(output);
        }
    }
    catch (const Standard_Failure& error) {
        return outputError(std::string("The multi-Body operation failed: ") + error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("The multi-Body operation failed: ") + error.what());
    }

    operation.OutputShapes.setValues(outputs);
    boost::dynamic_bitset<> outputPresence(outputBodyIds.size());
    outputPresence.set();
    operation.OutputPresence.setValues(outputPresence);
    return App::DocumentObject::StdReturn;
}

Part::TopoShape featureToolInDesignCoordinates(const PartDesign::FeatureAddSub& feature)
{
    Part::TopoShape tool = feature.AddSubShape.getShape();
    if (tool.isNull()) {
        return tool;
    }
    // AddSubShape is the operation's local unsigned tool, unlike Shape whose
    // placement is synchronized by Part::Feature. Bake the complete global
    // feature placement exactly once before applying Design-space pattern
    // transforms.
    tool.transformShape(App::GeoFeature::getGlobalPlacement(&feature).toMatrix(), true, true);
    return tool;
}

App::DocumentObjectExecReturn* computeDesignBodyCopies(
    PartDesign::Feature& controller,
    DesignOperationProperties& operation,
    const std::vector<gp_Trsf>& copies,
    int& generatedCopyCount,
    std::string_view operationName
)
{
    controller.Shape.setValue(Part::TopoShape());
    generatedCopyCount = 0;
    if (copies.empty()) {
        return outputError(std::string(operationName) + " requires at least one generated copy");
    }

    try {
        ensureDesignOperationPortSchema(controller);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const auto& inputs = operation.InputStates.getValues();
    const auto& inputBodyIds = operation.InputBodyIds.getValues();
    const auto& inputFrames = operation.InputFrames.getValues();
    const auto& outputBodyIds = operation.OutputBodyIds.getValues();
    const auto& outputFrames = operation.OutputFrames.getValues();
    const auto& previousInputIndices = operation.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = operation.OutputComponentIds.getValues();
    if (std::string_view(operation.ResultOperation.getValueAsString()) != "New Bodies"
        || inputs.size() != 1 || inputBodyIds.size() != 1 || inputFrames.size() != 1
        || outputBodyIds.size() != copies.size() || outputFrames.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError(
            std::string(operationName) + " has inconsistent exact-source or output ports"
        );
    }

    auto* source = freecad_cast<Part::Feature*>(inputs.front());
    const auto* sourceState = freecad_cast<const DesignBodyState*>(source);
    if (!source || source == &controller || source->getDocument() != controller.getDocument()
        || inputBodyIds.front().empty()
        || (sourceState
            && (!sourceState->Present.getValue()
                || sourceState->BodyId.getValueStr() != inputBodyIds.front()))) {
        return outputError(std::string(operationName) + " lost its exact present source Body state");
    }

    try {
        Part::TopoShape sourceShape = shapeInBodyStateCoordinates(*source);
        if (sourceShape.isNull() || sourceShape.countSubShapes(TopAbs_SOLID) != 1) {
            return outputError(std::string(operationName) + " source must be exactly one solid");
        }
        sourceShape = transformedShape(sourceShape, inputFrames.front());

        std::vector<Part::TopoShape> outputs;
        std::vector<Part::TopoShape> preview;
        outputs.reserve(copies.size());
        preview.reserve(copies.size());
        std::unordered_set<std::string> uniqueBodyIds;
        for (std::size_t index = 0; index < copies.size(); ++index) {
            if (outputBodyIds[index].empty() || outputBodyIds[index] == inputBodyIds.front()
                || !uniqueBodyIds.insert(outputBodyIds[index]).second
                || previousInputIndices[index] != -1) {
                return outputError(
                    std::string(operationName) + " requires one distinct created Body identity per copy"
                );
            }
            Part::TopoShape copy = sourceShape.makeElementTransform(
                copies[index],
                Data::indexSuffix(static_cast<int>(index + 1)).c_str(),
                Part::CopyType::copy
            );
            if (copy.isNull() || copy.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError(
                    std::string(operationName) + " produced a copy that is not one valid solid"
                );
            }
            preview.push_back(copy);
            outputs.push_back(transformedShape(copy, outputFrames[index].inverse()));
        }
        controller.PreviewShape.setValue(Part::TopoShape().makeElementCompound(preview));
        operation.OutputShapes.setValues(outputs);
        boost::dynamic_bitset<> presence(outputs.size());
        presence.set();
        operation.OutputPresence.setValues(presence);
        generatedCopyCount = static_cast<int>(outputs.size());
        return App::DocumentObject::StdReturn;
    }
    catch (const Standard_Failure& error) {
        return outputError(std::string(operationName) + " failed: " + error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        return outputError(std::string(operationName) + " failed: " + error.what());
    }
}

App::DocumentObjectExecReturn* computeDesignPatternOutputs(
    PartDesign::FeatureRefine& controller,
    DesignOperationProperties& operation,
    DesignPatternProperties& pattern,
    const std::vector<gp_Trsf>& copies,
    int& generatedOccurrenceCount
)
{
    controller.Shape.setValue(Part::TopoShape());
    generatedOccurrenceCount = 0;
    if (copies.empty()) {
        return outputError("A Pattern requires at least one generated occurrence");
    }

    try {
        ensureDesignOperationPortSchema(controller);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const auto& inputs = operation.InputStates.getValues();
    const auto& inputBodyIds = operation.InputBodyIds.getValues();
    const auto& inputFrames = operation.InputFrames.getValues();
    const auto& outputBodyIds = operation.OutputBodyIds.getValues();
    const auto& outputFrames = operation.OutputFrames.getValues();
    const auto& previousInputIndices = operation.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = operation.OutputComponentIds.getValues();
    const std::string_view sourceMode = pattern.PatternSource.getValueAsString();
    const std::string_view resultMode = operation.ResultOperation.getValueAsString();

    if (inputs.size() != inputBodyIds.size() || inputs.size() != inputFrames.size()
        || outputBodyIds.empty() || outputFrames.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError("The Pattern has inconsistent input or output ports");
    }

    try {
        if (sourceMode == "Body") {
            if (pattern.SourceOperation.getValue()) {
                return outputError("A Body Pattern cannot retain a feature-source link");
            }
            return computeDesignBodyCopies(
                controller,
                operation,
                copies,
                generatedOccurrenceCount,
                "Body Pattern"
            );
        }

        if (sourceMode != "Feature") {
            return outputError("Pattern source must be Feature or Body");
        }

        auto* sourceObject = pattern.SourceOperation.getValue();
        auto* sourceFeature = freecad_cast<PartDesign::FeatureAddSub*>(sourceObject);
        auto* sourceOperation = dynamic_cast<DesignOperationProperties*>(sourceObject);
        if (!sourceFeature || !sourceOperation || sourceObject == &controller
            || sourceObject->getDocument() != controller.getDocument()) {
            return outputError("A Feature Pattern requires one earlier Design feature "
                               "with reusable tool geometry");
        }
        const std::string_view sourceResult = sourceOperation->ResultOperation.getValueAsString();
        const std::string_view expectedResult = sourceResult == "Cut" ? "Cut"
            : (sourceResult == "New Body" || sourceResult == "Join")  ? "Join"
                                                                      : std::string_view();
        if (expectedResult.empty() || resultMode != expectedResult) {
            return outputError("Feature Pattern supports additive and subtractive source "
                               "features and must preserve the source operation semantic");
        }
        if (inputs.empty() || inputs.size() != outputBodyIds.size()) {
            return outputError("A Feature Pattern requires one exact prior state for every "
                               "target Body");
        }
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            if (previousInputIndices[index] != static_cast<long>(index)
                || inputBodyIds[index] != outputBodyIds[index]
                || inputFrames[index] != outputFrames[index] || !outputComponentIds[index].empty()) {
                return outputError("A Feature Pattern output must advance its matching "
                                   "input Body");
            }
        }

        Part::TopoShape tool = featureToolInDesignCoordinates(*sourceFeature);
        if (tool.isNull() || !tool.hasSubShape(TopAbs_SOLID)) {
            return outputError("The selected source feature has no reusable solid tool");
        }
        std::vector<Part::TopoShape> transformedTools;
        transformedTools.reserve(copies.size());
        for (std::size_t index = 0; index < copies.size(); ++index) {
            transformedTools.push_back(tool.makeElementTransform(
                copies[index],
                Data::indexSuffix(static_cast<int>(index + 1)).c_str(),
                Part::CopyType::copy
            ));
        }
        controller.PreviewShape.setValue(Part::TopoShape().makeElementCompound(transformedTools));

        const char* opcode = resultMode == "Cut" ? Part::OpCodes::Cut : Part::OpCodes::Fuse;
        std::vector<Part::TopoShape> outputs;
        outputs.reserve(inputs.size());
        std::unordered_set<App::DocumentObject*> uniqueInputs;
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            auto* input = freecad_cast<Part::Feature*>(inputs[index]);
            auto* body = controller.getDocument()
                ? bodyWithIdentity(*controller.getDocument(), outputBodyIds[index])
                : nullptr;
            if (!input || input == &controller || !uniqueInputs.insert(input).second || !body
                || !stateMatchesBody(input, *body, outputBodyIds[index])) {
                return outputError("The Feature Pattern lost an exact target Body state");
            }

            Part::TopoShape base = shapeInBodyStateCoordinates(*input);
            if (base.isNull() || base.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError("A Feature Pattern target must be exactly one solid");
            }

            std::vector<Part::TopoShape> operands;
            operands.reserve(transformedTools.size() + 1);
            operands.push_back(base);
            for (const auto& transformedTool : transformedTools) {
                operands.push_back(transformedShape(transformedTool, outputFrames[index].inverse()));
            }

            Part::TopoShape output;
            output.makeElementBoolean(opcode, operands, nullptr, controller.FuzzyTolerance.getValue());
            output = pattern.refineDesignPatternShape(output);
            if (output.isNull() || output.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError(
                    resultMode == "Cut"
                        ? "The patterned cuts do not leave one valid target solid"
                        : "The patterned additions are disconnected; use a Body "
                          "Pattern when the occurrences should remain separate Bodies"
                );
            }
            outputs.push_back(output);
        }
        operation.OutputShapes.setValues(outputs);
        boost::dynamic_bitset<> presence(outputs.size());
        presence.set();
        operation.OutputPresence.setValues(presence);
        generatedOccurrenceCount = static_cast<int>(copies.size());
        return App::DocumentObject::StdReturn;
    }
    catch (const Standard_Failure& error) {
        return outputError(std::string("Pattern geometry failed: ") + error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("Pattern geometry failed: ") + error.what());
    }
}

App::DocumentObjectExecReturn* computeDesignCombineOutputs(
    DesignCombine& combine,
    const std::function<Part::TopoShape(const Part::TopoShape&)>& refine
)
{
    combine.Shape.setValue(Part::TopoShape());

    try {
        ensureDesignOperationPortSchema(combine);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const auto& inputs = combine.InputStates.getValues();
    const auto& inputBodyIds = combine.InputBodyIds.getValues();
    const auto& inputFrames = combine.InputFrames.getValues();
    const auto& outputBodyIds = combine.OutputBodyIds.getValues();
    const auto& outputFrames = combine.OutputFrames.getValues();
    const auto& previousInputIndices = combine.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = combine.OutputComponentIds.getValues();
    const std::string resultBodyId = combine.ResultBodyId.getValueStr();
    const std::string_view resultOperation = combine.ResultOperation.getValueAsString();
    const bool keepTools = combine.KeepTools.getValue();

    if (resultOperation != "Join" && resultOperation != "Cut" && resultOperation != "Intersect") {
        return outputError("Combine operation must be Join, Cut, or Intersect");
    }
    if (inputs.size() < 2 || inputs.size() != inputBodyIds.size()
        || inputs.size() != inputFrames.size() || resultBodyId.empty()
        || inputBodyIds.front() != resultBodyId) {
        return outputError("Combine requires one explicit result Body and at least one "
                           "distinct tool Body");
    }

    const std::size_t expectedOutputCount = keepTools ? 1 : inputs.size();
    if (outputBodyIds.size() != expectedOutputCount || outputFrames.size() != expectedOutputCount
        || previousInputIndices.size() != expectedOutputCount
        || outputComponentIds.size() != expectedOutputCount) {
        return outputError("Combine has inconsistent saved input or output ports");
    }
    for (std::size_t index = 0; index < expectedOutputCount; ++index) {
        if (outputBodyIds[index] != inputBodyIds[index] || outputFrames[index] != inputFrames[index]
            || previousInputIndices[index] != static_cast<long>(index)
            || !outputComponentIds[index].empty()) {
            return outputError("Combine outputs do not advance their exact matching input "
                               "Body states");
        }
    }

    std::unordered_set<App::DocumentObject*> uniqueInputs;
    std::unordered_set<std::string> uniqueBodyIds;
    std::vector<Part::TopoShape> shapesInResultFrame;
    shapesInResultFrame.reserve(inputs.size());
    const Base::Placement resultFrame = inputFrames.front();
    try {
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            auto* input = freecad_cast<Part::Feature*>(inputs[index]);
            auto* body = combine.getDocument()
                ? bodyWithIdentity(*combine.getDocument(), inputBodyIds[index])
                : nullptr;
            const auto* state = freecad_cast<const DesignBodyState*>(input);
            if (!input || input == &combine || input->getDocument() != combine.getDocument()
                || !uniqueInputs.insert(input).second || inputBodyIds[index].empty()
                || !uniqueBodyIds.insert(inputBodyIds[index]).second || !body
                || !stateMatchesBody(input, *body, inputBodyIds[index])
                || (state && !state->Present.getValue())) {
                return outputError("Combine lost one exact present input Body state");
            }

            Part::TopoShape local = shapeInBodyStateCoordinates(*input);
            if (local.isNull() || !local.hasSubShape(TopAbs_SOLID)
                || local.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError("Every Combine input Body must contain exactly one solid");
            }
            Part::TopoShape inDesignCoordinates = transformedShape(local, inputFrames[index]);
            shapesInResultFrame.push_back(transformedShape(inDesignCoordinates, resultFrame.inverse())
            );
        }
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string("Combine failed while transforming input Bodies: ") + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(
            std::string("Combine failed while transforming input Bodies: ") + error.what()
        );
    }
    catch (...) {
        return outputError("Combine failed while transforming input Bodies with an unknown "
                           "geometry-kernel exception");
    }

    Part::TopoShape result;
    try {
        if (resultOperation == "Join") {
            result.makeElementBoolean(
                Part::OpCodes::Fuse,
                shapesInResultFrame,
                nullptr,
                combine.FuzzyTolerance.getValue()
            );
        }
        else if (resultOperation == "Cut") {
            for (std::size_t index = 1; index < shapesInResultFrame.size(); ++index) {
                Part::TopoShape overlap;
                overlap.makeElementBoolean(
                    Part::OpCodes::Common,
                    {
                        shapesInResultFrame.front(),
                        shapesInResultFrame[index],
                    },
                    nullptr,
                    combine.FuzzyTolerance.getValue()
                );
                if (overlap.isNull() || !overlap.hasSubShape(TopAbs_SOLID)) {
                    return outputError("Every explicit Cut tool Body must intersect the "
                                       "result Body");
                }
            }
            result.makeElementBoolean(
                Part::OpCodes::Cut,
                shapesInResultFrame,
                nullptr,
                combine.FuzzyTolerance.getValue()
            );
        }
        else {
            result = shapesInResultFrame.front();
            for (std::size_t index = 1; index < shapesInResultFrame.size(); ++index) {
                Part::TopoShape intersection;
                intersection.makeElementBoolean(
                    Part::OpCodes::Common,
                    {result, shapesInResultFrame[index]},
                    nullptr,
                    combine.FuzzyTolerance.getValue()
                );
                result = std::move(intersection);
            }
        }
        result = refine(result);
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string("Combine failed in the geometry kernel: ") + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("Combine failed: ") + error.what());
    }
    catch (...) {
        return outputError("Combine failed with an unknown geometry-kernel exception");
    }

    if (result.isNull() || !result.hasSubShape(TopAbs_SOLID)) {
        return outputError("Combine produced no solid result");
    }
    if (result.countSubShapes(TopAbs_SOLID) != 1) {
        return outputError("Combine produced more than one solid; use Split so every result "
                           "receives a stable Body identity");
    }
    if (!result.isValid()) {
        return outputError("Combine produced invalid solid geometry");
    }

    std::vector<Part::TopoShape> outputs;
    outputs.reserve(expectedOutputCount);
    outputs.push_back(result);
    boost::dynamic_bitset<> outputPresence(expectedOutputCount);
    outputPresence.set(0);
    if (!keepTools) {
        outputs.resize(expectedOutputCount);
    }

    try {
        combine.OutputShapes.setValues(outputs);
        combine.OutputPresence.setValues(outputPresence);
        combine.PreviewShape.setValue(transformedShape(result, resultFrame));
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string("Combine failed while publishing its atomic outputs: ")
            + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(
            std::string("Combine failed while publishing its atomic outputs: ") + error.what()
        );
    }
    catch (...) {
        return outputError("Combine failed while publishing its atomic outputs with an "
                           "unknown geometry-kernel exception");
    }
    return App::DocumentObject::StdReturn;
}

struct EvaluatedDesignSplit
{
    Base::Placement sourceFrame;
    std::vector<Part::TopoShape> regions;
};

bool isStrictlyInside(const Part::TopoShape& solid, const Base::Vector3d& point)
{
    if (solid.isNull()) {
        return false;
    }
    const BRepClass3d_SolidClassifier classifier(
        solid.getShape(),
        gp_Pnt(point.x, point.y, point.z),
        Precision::Confusion()
    );
    return classifier.State() == TopAbs_IN;
}

double shapeVolume(const Part::TopoShape& shape)
{
    GProp_GProps properties;
    BRepGProp::VolumeProperties(shape.getShape(), properties);
    return std::abs(properties.Mass());
}

Base::Vector3d strictInteriorWitness(const Part::TopoShape& solid)
{
    GProp_GProps properties;
    BRepGProp::VolumeProperties(solid.getShape(), properties);
    if (std::abs(properties.Mass()) > Precision::Confusion()) {
        const gp_Pnt center = properties.CentreOfMass();
        const Base::Vector3d candidate(center.X(), center.Y(), center.Z());
        if (isStrictlyInside(solid, candidate)) {
            return candidate;
        }
    }

    Bnd_Box bounds;
    BRepBndLib::Add(solid.getShape(), bounds);
    if (!bounds.IsVoid() && !bounds.IsOpen()) {
        Standard_Real xMinimum;
        Standard_Real yMinimum;
        Standard_Real zMinimum;
        Standard_Real xMaximum;
        Standard_Real yMaximum;
        Standard_Real zMaximum;
        bounds.Get(xMinimum, yMinimum, zMinimum, xMaximum, yMaximum, zMaximum);
        const Base::Vector3d center(
            (xMinimum + xMaximum) * 0.5,
            (yMinimum + yMaximum) * 0.5,
            (zMinimum + zMaximum) * 0.5
        );
        if (isStrictlyInside(solid, center)) {
            return center;
        }

        const double diagonal = std::sqrt(
            std::pow(xMaximum - xMinimum, 2.0) + std::pow(yMaximum - yMinimum, 2.0)
            + std::pow(zMaximum - zMinimum, 2.0)
        );
        const double initialOffset = std::max(Precision::Confusion() * 100.0, diagonal * 1.0e-9);
        const double maximumOffset = std::max(initialOffset, diagonal * 0.1);

        for (TopExp_Explorer explorer(solid.getShape(), TopAbs_FACE); explorer.More();
             explorer.Next()) {
            gp_Pnt point;
            gp_Vec firstDerivative;
            gp_Vec secondDerivative;
            Standard_Real u = 0.0;
            Standard_Real v = 0.0;
            Standard_Real parameter = 0.5;
            if (!BRepClass3d_SolidExplorer::FindAPointInTheFace(
                    TopoDS::Face(explorer.Current()),
                    point,
                    u,
                    v,
                    parameter,
                    firstDerivative,
                    secondDerivative
                )) {
                continue;
            }
            gp_Vec normal = firstDerivative.Crossed(secondDerivative);
            if (normal.SquareMagnitude() <= std::numeric_limits<double>::epsilon()) {
                continue;
            }
            normal.Normalize();

            for (double offset = initialOffset; offset <= maximumOffset; offset *= 10.0) {
                for (const double direction : {-1.0, 1.0}) {
                    const gp_Pnt candidatePoint = point.Translated(
                        normal.Multiplied(direction * offset)
                    );
                    const Base::Vector3d candidate(
                        candidatePoint.X(),
                        candidatePoint.Y(),
                        candidatePoint.Z()
                    );
                    if (isStrictlyInside(solid, candidate)) {
                        return candidate;
                    }
                }
                if (offset == 0.0) {
                    break;
                }
            }
        }
    }

    throw Base::CADKernelError("Split could not establish a strict interior identity point for one "
                               "resulting solid");
}

Part::TopoShape resolveDesignSplitter(
    const App::PropertyLinkSubList::SubSet& reference,
    const Base::Placement& splitterFrame,
    const Base::Placement& sourceFrame
)
{
    auto* feature = freecad_cast<Part::Feature*>(reference.first);
    if (!feature || freecad_cast<DesignBodyPublication*>(feature)) {
        throw Base::TypeError("Every Split definition must be an exact modeling feature, face, "
                              "surface, shell, or solid");
    }

    const Part::TopoShape definition = feature->Shape.getShape();
    if (definition.isNull()) {
        throw Base::ValueError("A Split definition has no geometry at this History position");
    }

    std::vector<Part::TopoShape> selected;
    const bool wholeShape = reference.second.empty()
        || (reference.second.size() == 1 && reference.second.front().empty());
    if (wholeShape) {
        selected.push_back(definition);
    }
    else {
        selected.reserve(reference.second.size());
        std::unordered_set<std::string> uniqueReferences;
        for (const auto& subname : reference.second) {
            if (subname.empty() || !uniqueReferences.insert(subname).second) {
                throw Base::ValueError("Split definitions contain an empty or duplicate "
                                       "subelement reference");
            }
            Part::TopoShape subshape = definition.getSubTopoShape(subname.c_str(), true);
            if (subshape.isNull()) {
                throw Base::ValueError(
                    "Split can no longer resolve selected subelement '" + subname + "'"
                );
            }
            selected.push_back(std::move(subshape));
        }
    }

    Part::TopoShape splitter;
    if (selected.size() == 1) {
        splitter = selected.front();
    }
    else {
        splitter.makeElementCompound(
            selected,
            Part::OpCodes::Compound,
            Part::TopoShape::SingleShapeCompoundCreationPolicy::returnShape
        );
    }
    splitter = transformedShape(splitter, splitterFrame);
    return transformedShape(splitter, sourceFrame.inverse());
}

EvaluatedDesignSplit evaluateDesignSplit(
    const DesignSplit& split,
    const std::function<Part::TopoShape(const Part::TopoShape&)>& refine
)
{
    const auto& inputs = split.InputStates.getValues();
    const auto& inputBodyIds = split.InputBodyIds.getValues();
    const auto& inputFrames = split.InputFrames.getValues();
    const std::string sourceBodyId = split.SourceBodyId.getValueStr();
    const auto splitterReferences = split.Splitters.getSubListValues();
    const auto splitterFrames = split.SplitterFrames.getValues();

    if (!split.getDocument() || inputs.empty() || inputs.size() != inputBodyIds.size()
        || inputs.size() != inputFrames.size() || sourceBodyId.empty()
        || inputBodyIds.front() != sourceBodyId) {
        throw Base::ValueError("Split requires one exact source Body state and consistent saved "
                               "input ports");
    }
    if (splitterReferences.empty() || splitterReferences.size() != splitterFrames.size()) {
        throw Base::ValueError("Split requires at least one exact splitting definition with a "
                               "saved coordinate frame");
    }

    auto* source = freecad_cast<Part::Feature*>(inputs.front());
    auto* sourceBody = bodyWithIdentity(*split.getDocument(), sourceBodyId);
    const auto* sourceState = freecad_cast<const DesignBodyState*>(source);
    if (!source || source == &split || source->getDocument() != split.getDocument() || !sourceBody
        || !stateMatchesBody(source, *sourceBody, sourceBodyId)
        || (sourceState && !sourceState->Present.getValue())) {
        throw Base::ValueError("Split lost its exact present source Body state");
    }

    const Part::TopoShape sourceShape = shapeInBodyStateCoordinates(*source);
    if (sourceShape.isNull() || sourceShape.countSubShapes(TopAbs_SOLID) != 1) {
        throw Base::ValueError("Split requires one source Body containing exactly one solid");
    }

    std::vector<Part::TopoShape> sources {sourceShape};
    TopTools_ListOfShape arguments;
    TopTools_ListOfShape tools;
    arguments.Append(sourceShape.getShape());
    for (std::size_t index = 0; index < splitterReferences.size(); ++index) {
        auto* object = splitterReferences[index].first;
        if (!object || object == &split || object->getDocument() != split.getDocument()) {
            throw Base::ValueError("A Split definition is missing or belongs to another document");
        }
        Part::TopoShape splitter = resolveDesignSplitter(
            splitterReferences[index],
            splitterFrames[index],
            inputFrames.front()
        );
        if (splitter.isNull()) {
            throw Base::ValueError("A Split definition resolved to empty geometry");
        }
        tools.Append(splitter.getShape());
        sources.push_back(std::move(splitter));
    }

    BRepAlgoAPI_Splitter maker;
    maker.SetArguments(arguments);
    maker.SetTools(tools);
    maker.SetRunParallel(true);
    maker.SetNonDestructive(true);
    if (split.FuzzyTolerance.getValue() > 0.0) {
        maker.SetFuzzyValue(split.FuzzyTolerance.getValue());
    }
    maker.Build();
    if (!maker.IsDone() || maker.Shape().IsNull()) {
        throw Base::CADKernelError("Split failed in the geometry kernel");
    }

    Part::TopoShape mapped(0, sourceShape.Hasher);
    mapped.makeShapeWithElementMap(maker.Shape(), Part::MapperMaker(maker), sources, Part::OpCodes::Split);
    auto regions = mapped.getSubTopoShapes(TopAbs_SOLID);
    if (regions.size() < 2) {
        throw Base::ValueError("The selected definitions do not divide the source Body into two "
                               "or more solids");
    }

    double regionVolume = 0.0;
    for (auto& region : regions) {
        region = refine(region);
        if (region.isNull() || region.countSubShapes(TopAbs_SOLID) != 1 || !region.isValid()) {
            throw Base::CADKernelError("Split produced an empty, multi-solid, or invalid region");
        }
        regionVolume += shapeVolume(region);
    }
    const double sourceVolume = shapeVolume(sourceShape);
    const double volumeTolerance = std::max(Precision::Confusion(), sourceVolume * 1.0e-8);
    if (sourceVolume <= Precision::Confusion()
        || std::abs(regionVolume - sourceVolume) > volumeTolerance) {
        throw Base::CADKernelError("Split regions do not exactly partition the source Body volume");
    }

    return {inputFrames.front(), std::move(regions)};
}

App::DocumentObjectExecReturn* computeDesignSplitOutputs(
    DesignSplit& split,
    const std::function<Part::TopoShape(const Part::TopoShape&)>& refine
)
{
    split.Shape.setValue(Part::TopoShape());
    split.PreviewShape.setValue(Part::TopoShape());

    try {
        ensureDesignOperationPortSchema(split);
        if (std::string_view(split.ResultOperation.getValueAsString()) != "Split") {
            return outputError("A Design Split must use the Split result mode");
        }
        if (!split.RetainedRegionChosen.getValue()) {
            return outputError("Choose which Split region keeps the source Body identity");
        }

        EvaluatedDesignSplit evaluated = evaluateDesignSplit(split, refine);
        const auto witnesses = split.RegionWitnesses.getValues();
        const auto& outputBodyIds = split.OutputBodyIds.getValues();
        const auto& outputFrames = split.OutputFrames.getValues();
        const auto& previousIndices = split.OutputPreviousInputIndices.getValues();
        const auto& outputComponentIds = split.OutputComponentIds.getValues();
        if (witnesses.size() != evaluated.regions.size()
            || outputBodyIds.size() != evaluated.regions.size()
            || outputFrames.size() != evaluated.regions.size()
            || previousIndices.size() != evaluated.regions.size()
            || outputComponentIds.size() != evaluated.regions.size()) {
            return outputError("Split output identities and strict region witnesses are "
                               "inconsistent");
        }

        std::vector<Part::TopoShape> outputs;
        outputs.reserve(witnesses.size());
        std::vector<bool> claimed(evaluated.regions.size(), false);
        for (const auto& witness : witnesses) {
            std::size_t match = evaluated.regions.size();
            for (std::size_t regionIndex = 0; regionIndex < evaluated.regions.size(); ++regionIndex) {
                if (!isStrictlyInside(evaluated.regions[regionIndex], witness)) {
                    continue;
                }
                if (match != evaluated.regions.size()) {
                    return outputError("A saved Split identity point belongs to more than one "
                                       "regenerated region");
                }
                match = regionIndex;
            }
            if (match == evaluated.regions.size() || claimed[match]) {
                return outputError("Split topology changed across a saved region identity; "
                                   "edit the Split and explicitly reassign its result Bodies");
            }
            claimed[match] = true;
            outputs.push_back(evaluated.regions[match]);
        }
        if (std::ranges::any_of(claimed, [](bool value) { return !value; })) {
            return outputError("Split regenerated an unassigned region; edit the Split and "
                               "explicitly assign every result Body");
        }

        boost::dynamic_bitset<> presence(outputs.size());
        presence.set();
        split.OutputShapes.setValues(outputs);
        split.OutputPresence.setValues(presence);

        std::vector<Part::TopoShape> preview;
        preview.reserve(outputs.size());
        for (const auto& output : outputs) {
            preview.push_back(transformedShape(output, evaluated.sourceFrame));
        }
        split.PreviewShape.setValue(Part::TopoShape().makeElementCompound(preview));
        return App::DocumentObject::StdReturn;
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string("Split failed in the geometry kernel: ") + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("Split failed: ") + error.what());
    }
    catch (const std::exception& error) {
        return outputError(std::string("Split failed: ") + error.what());
    }
    catch (...) {
        return outputError("Split failed with an unknown geometry-kernel exception");
    }
}

std::vector<Part::TopoShape> evaluateDesignSeparate(
    const DesignSeparate& separate,
    const std::function<Part::TopoShape(const Part::TopoShape&)>& refine
)
{
    auto* source = freecad_cast<Part::Feature*>(separate.Source.getValue());
    if (!source || source == &separate || source->getDocument() != separate.getDocument()
        || freecad_cast<DesignBodyState*>(source) || freecad_cast<DesignBodyPublication*>(source)
        || App::GeoFeatureGroupExtension::getGroupOfObject(source)) {
        throw Base::ValueError("Separate requires one earlier reusable Design-root definition");
    }

    Part::TopoShape sourceShape = shapeInDesignCoordinates(*source);
    if (sourceShape.isNull()) {
        throw Base::ValueError("The Separate source has no geometry at this History position");
    }

    auto solids = sourceShape.getSubTopoShapes(TopAbs_SOLID);
    if (solids.size() < 2) {
        throw Base::ValueError("Separate requires one definition containing at least two solids");
    }

    for (auto& solid : solids) {
        solid = refine(solid);
        if (solid.isNull() || solid.countSubShapes(TopAbs_SOLID) != 1 || !solid.isValid()) {
            throw Base::CADKernelError("Separate produced an empty, multi-solid, or invalid Body");
        }
    }
    return solids;
}

App::DocumentObjectExecReturn* computeDesignSeparateOutputs(
    DesignSeparate& separate,
    const std::function<Part::TopoShape(const Part::TopoShape&)>& refine
)
{
    separate.Shape.setValue(Part::TopoShape());
    separate.PreviewShape.setValue(Part::TopoShape());

    try {
        ensureDesignOperationPortSchema(separate);
        const auto& inputs = separate.InputStates.getValues();
        const auto& inputBodyIds = separate.InputBodyIds.getValues();
        const auto& inputFrames = separate.InputFrames.getValues();
        const auto& outputBodyIds = separate.OutputBodyIds.getValues();
        const auto& outputFrames = separate.OutputFrames.getValues();
        const auto& previousInputIndices = separate.OutputPreviousInputIndices.getValues();
        const auto& outputComponentIds = separate.OutputComponentIds.getValues();
        const auto witnesses = separate.RegionWitnesses.getValues();

        if (std::string_view(separate.ResultOperation.getValueAsString()) != "New Bodies"
            || !inputs.empty() || !inputBodyIds.empty() || !inputFrames.empty()
            || outputBodyIds.size() < 2 || outputFrames.size() != outputBodyIds.size()
            || previousInputIndices.size() != outputBodyIds.size()
            || outputComponentIds.size() != outputBodyIds.size()
            || witnesses.size() != outputBodyIds.size()) {
            return outputError("Separate has inconsistent source or output ports");
        }

        std::unordered_set<std::string> uniqueBodyIds;
        for (std::size_t index = 0; index < outputBodyIds.size(); ++index) {
            if (outputBodyIds[index].empty() || !uniqueBodyIds.insert(outputBodyIds[index]).second
                || previousInputIndices[index] != -1) {
                return outputError("Separate requires one distinct created Body identity "
                                   "for every source solid");
            }
        }

        auto solids = evaluateDesignSeparate(separate, refine);
        if (solids.size() != witnesses.size()) {
            return outputError("Separate source solids changed; edit Separate to reconcile its "
                               "result Bodies");
        }

        std::vector<Part::TopoShape> outputs;
        std::vector<Part::TopoShape> preview;
        std::vector<bool> claimed(solids.size(), false);
        outputs.reserve(solids.size());
        preview.reserve(solids.size());
        for (std::size_t outputIndex = 0; outputIndex < witnesses.size(); ++outputIndex) {
            std::size_t match = solids.size();
            for (std::size_t solidIndex = 0; solidIndex < solids.size(); ++solidIndex) {
                if (!isStrictlyInside(solids[solidIndex], witnesses[outputIndex])) {
                    continue;
                }
                if (match != solids.size()) {
                    return outputError("A saved Separate identity point belongs to more than "
                                       "one regenerated solid");
                }
                match = solidIndex;
            }
            if (match == solids.size() || claimed[match]) {
                return outputError("Separate can no longer locate a saved Body identity; edit "
                                   "Separate to reconcile its result Bodies");
            }
            claimed[match] = true;
            preview.push_back(solids[match]);
            outputs.push_back(transformedShape(solids[match], outputFrames[outputIndex].inverse()));
        }
        if (std::ranges::any_of(claimed, [](bool value) { return !value; })) {
            return outputError("Separate source has a new solid; edit Separate to create its "
                               "result Body");
        }

        separate.OutputShapes.setValues(outputs);
        boost::dynamic_bitset<> presence(outputs.size());
        presence.set();
        separate.OutputPresence.setValues(presence);
        separate.PreviewShape.setValue(Part::TopoShape().makeElementCompound(preview));
        return App::DocumentObject::StdReturn;
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string("Separate failed in the geometry kernel: ") + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("Separate failed: ") + error.what());
    }
    catch (const std::exception& error) {
        return outputError(std::string("Separate failed: ") + error.what());
    }
    catch (...) {
        return outputError("Separate failed with an unknown geometry-kernel exception");
    }
}

using DesignDressupBuilder = std::function<
    Part::TopoShape(const Part::TopoShape&, const std::vector<Part::TopoShape>&, std::size_t, const Base::Placement&)>;

using DesignSubelementResolver = std::function<
    std::vector<Part::TopoShape>(const Part::TopoShape&, const std::vector<std::string>&)>;

App::DocumentObjectExecReturn* computeDesignDressupOutputs(
    Part::Feature& controller,
    DesignOperationProperties& operation,
    const DesignSubelementOperationProperties& selections,
    const char* operationName,
    const char* subelementName,
    const DesignSubelementResolver& resolveSubelements,
    const DesignDressupBuilder& build
)
{
    controller.Shape.setValue(Part::TopoShape());

    try {
        ensureDesignOperationPortSchema(controller);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    if (std::string_view(operation.ResultOperation.getValueAsString()) != "Modify") {
        return outputError(std::string(operationName) + " must modify explicit existing Bodies");
    }

    const auto& inputs = operation.InputStates.getValues();
    const auto& inputBodyIds = operation.InputBodyIds.getValues();
    const auto& inputFrames = operation.InputFrames.getValues();
    const auto& outputBodyIds = operation.OutputBodyIds.getValues();
    const auto& outputFrames = operation.OutputFrames.getValues();
    const auto& previousInputIndices = operation.OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = operation.OutputComponentIds.getValues();
    if (outputBodyIds.empty() || inputs.size() != inputBodyIds.size()
        || inputs.size() != inputFrames.size() || inputs.size() != outputBodyIds.size()
        || outputFrames.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError(
            std::string(operationName)
            + " requires one exact prior state and coordinate frame for every target Body"
        );
    }
    for (std::size_t index = 0; index < outputBodyIds.size(); ++index) {
        if (previousInputIndices[index] != static_cast<long>(index)
            || inputBodyIds[index] != outputBodyIds[index]
            || inputFrames[index] != outputFrames[index] || !outputComponentIds[index].empty()) {
            return outputError(
                std::string(operationName) + " requires matching input and output Body ports"
            );
        }
    }

    std::vector<std::vector<std::string>> elementGroups;
    try {
        elementGroups = selections.targetElementGroups();
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }
    if (elementGroups.size() != outputBodyIds.size()) {
        return outputError(
            std::string(operationName) + " requires one subelement selection group for every target Body"
        );
    }

    std::unordered_set<std::string> uniqueBodyIds;
    std::unordered_set<App::DocumentObject*> uniqueInputs;
    std::vector<Part::TopoShape> outputs;
    outputs.reserve(outputBodyIds.size());

    try {
        for (std::size_t index = 0; index < outputBodyIds.size(); ++index) {
            auto* input = freecad_cast<Part::Feature*>(inputs[index]);
            auto* body = controller.getDocument()
                ? bodyWithIdentity(*controller.getDocument(), outputBodyIds[index])
                : nullptr;
            if (outputBodyIds[index].empty() || !uniqueBodyIds.insert(outputBodyIds[index]).second
                || !input || input == &controller || input->getDocument() != controller.getDocument()
                || !uniqueInputs.insert(input).second || !body
                || !stateMatchesBody(input, *body, outputBodyIds[index])) {
                return outputError(
                    std::string(operationName) + " lost an exact prior state for one target Body"
                );
            }

            Part::TopoShape base = shapeInBodyStateCoordinates(*input);
            if (base.isNull() || !base.hasSubShape(TopAbs_SOLID)
                || base.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError(
                    std::string(operationName) + " requires exactly one solid in every target Body"
                );
            }

            const auto subelements = resolveSubelements(base, elementGroups[index]);
            if (subelements.empty()) {
                return outputError(
                    std::string(operationName) + " has no valid " + subelementName
                    + " for one target Body"
                );
            }

            Part::TopoShape output = build(base, subelements, index, outputFrames[index]);
            if (output.isNull() || !output.hasSubShape(TopAbs_SOLID)
                || output.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError(
                    std::string(operationName)
                    + " produced an empty, non-solid, or multi-solid Body result"
                );
            }

            TopTools_ListOfShape arguments;
            arguments.Append(base.getShape());
            if (!BRepAlgo::IsValid(arguments, output.getShape(), Standard_False, Standard_False)) {
                ShapeFix_ShapeTolerance tolerance;
                tolerance.LimitTolerance(
                    output.getShape(),
                    Precision::Confusion(),
                    Precision::Confusion(),
                    TopAbs_SHAPE
                );
            }
            outputs.push_back(std::move(output));
        }
    }
    catch (const Standard_Failure& error) {
        return outputError(
            std::string(operationName) + " failed in the geometry kernel: " + error.GetMessageString()
        );
    }
    catch (const Base::Exception& error) {
        return outputError(std::string(operationName) + " failed: " + error.what());
    }
    catch (...) {
        return outputError(
            std::string(operationName) + " failed with an unknown geometry-kernel exception"
        );
    }

    if (auto* feature = freecad_cast<Feature*>(&controller)) {
        std::vector<Part::TopoShape> previewShapes;
        previewShapes.reserve(outputs.size());
        for (std::size_t index = 0; index < outputs.size(); ++index) {
            previewShapes.push_back(transformedShape(outputs[index], outputFrames[index]));
        }
        feature->PreviewShape.setValue(Part::TopoShape().makeElementCompound(previewShapes));
    }

    // Publish only after every target has succeeded. A failed target therefore
    // cannot leave a partially advanced multi-Body operation.
    operation.OutputShapes.setValues(outputs);
    boost::dynamic_bitset<> outputPresence(outputBodyIds.size());
    outputPresence.set();
    operation.OutputPresence.setValues(outputPresence);
    return App::DocumentObject::StdReturn;
}

void touchDesignResultConsumers(
    App::DocumentObject& operation,
    bool allowDuringRestore = false
)
{
    auto* document = operation.getDocument();
    if (!document
        || (!allowDuringRestore
            && document->testStatus(App::Document::Restoring))) {
        return;
    }

    for (auto* state : designBodyStatesForOperation(&operation)) {
        if (!state) {
            continue;
        }
        state->touch();
        auto* body = bodyWithIdentity(*document, state->BodyId.getValueStr());
        auto* publication = findDesignBodyPublication(body);
        if (publication) {
            publication->touch();
        }
        if (body) {
            body->touch();
        }
    }
}

void touchDesignResults(
    App::DocumentObject& operation,
    const App::Property* property,
    const Part::PropertyTopoShapeList& outputShapes
)
{
    if (property == &outputShapes) {
        return;
    }
    touchDesignResultConsumers(operation);
}

}  // namespace

std::vector<Base::Vector3d> PartDesign::discoverDesignSplitRegionWitnesses(const DesignSplit& split)
{
    const EvaluatedDesignSplit evaluated
        = evaluateDesignSplit(split, [](const Part::TopoShape& shape) { return shape; });

    std::vector<Base::Vector3d> witnesses;
    witnesses.reserve(evaluated.regions.size());
    for (const auto& region : evaluated.regions) {
        witnesses.push_back(strictInteriorWitness(region));
    }

    // This ordering is used only to present unassigned regions in a stable
    // task-panel list. Once the user assigns identities, RegionWitnesses is
    // persisted in output-port order and no geometric sorting participates in
    // identity reconciliation.
    std::ranges::sort(witnesses, [](const Base::Vector3d& left, const Base::Vector3d& right) {
        if (left.x != right.x) {
            return left.x < right.x;
        }
        if (left.y != right.y) {
            return left.y < right.y;
        }
        return left.z < right.z;
    });
    return witnesses;
}

std::vector<Base::Vector3d> PartDesign::discoverDesignSeparateRegionWitnesses(
    const DesignSeparate& separate
)
{
    std::vector<Base::Vector3d> witnesses;
    const auto assignments = reconcileDesignSeparateRegions(separate, {});
    witnesses.reserve(assignments.size());
    for (const auto& assignment : assignments) {
        witnesses.push_back(assignment.witness);
    }
    return witnesses;
}

std::vector<DesignSeparateRegionAssignment> PartDesign::reconcileDesignSeparateRegions(
    const DesignSeparate& separate,
    const std::vector<Base::Vector3d>& previousWitnesses
)
{
    const auto solids = evaluateDesignSeparate(separate, [](const Part::TopoShape& shape) {
        return shape;
    });

    std::vector<long> previousIndexBySolid(solids.size(), -1);
    std::vector<std::size_t> solidByPreviousIndex(previousWitnesses.size(), solids.size());
    for (std::size_t previousIndex = 0; previousIndex < previousWitnesses.size(); ++previousIndex) {
        std::size_t match = solids.size();
        for (std::size_t solidIndex = 0; solidIndex < solids.size(); ++solidIndex) {
            if (!isStrictlyInside(solids[solidIndex], previousWitnesses[previousIndex])) {
                continue;
            }
            if (match != solids.size()) {
                throw Base::ValueError(
                    "A saved Separate identity point belongs to more than one current solid; "
                    "the result identity cannot be inferred safely"
                );
            }
            match = solidIndex;
        }
        if (match == solids.size()) {
            continue;
        }
        if (previousIndexBySolid[match] >= 0) {
            throw Base::ValueError(
                "A current Separate solid contains more than one saved result identity; "
                "choose the surviving Body explicitly before accepting this merge"
            );
        }
        previousIndexBySolid[match] = static_cast<long>(previousIndex);
        solidByPreviousIndex[previousIndex] = match;
    }

    std::vector<DesignSeparateRegionAssignment> assignments;
    assignments.reserve(solids.size());
    for (std::size_t previousIndex = 0; previousIndex < previousWitnesses.size(); ++previousIndex) {
        if (solidByPreviousIndex[previousIndex] == solids.size()) {
            continue;
        }
        assignments.push_back({previousWitnesses[previousIndex], static_cast<long>(previousIndex)});
    }

    std::vector<DesignSeparateRegionAssignment> added;
    for (std::size_t solidIndex = 0; solidIndex < solids.size(); ++solidIndex) {
        if (previousIndexBySolid[solidIndex] >= 0) {
            continue;
        }
        added.push_back({strictInteriorWitness(solids[solidIndex]), -1});
    }
    std::ranges::sort(
        added,
        [](const DesignSeparateRegionAssignment& left, const DesignSeparateRegionAssignment& right) {
            if (left.witness.x != right.witness.x) {
                return left.witness.x < right.witness.x;
            }
            if (left.witness.y != right.witness.y) {
                return left.witness.y < right.witness.y;
            }
            return left.witness.z < right.witness.z;
        }
    );
    assignments.insert(assignments.end(), added.begin(), added.end());
    return assignments;
}

std::vector<std::vector<std::string>> DesignSubelementOperationProperties::targetElementGroups() const
{
    const auto& offsets = TargetElementOffsets.getValues();
    const auto& elements = TargetElements.getValues();
    if (offsets.empty() || offsets.front() != 0
        || offsets.back() != static_cast<long>(elements.size())) {
        throw Base::RuntimeError("The operation has an invalid persistent subelement index");
    }

    std::vector<std::vector<std::string>> groups;
    groups.reserve(offsets.size() - 1);
    for (std::size_t index = 0; index + 1 < offsets.size(); ++index) {
        const long begin = offsets[index];
        const long end = offsets[index + 1];
        if (begin < 0 || end < begin || end > static_cast<long>(elements.size())) {
            throw Base::RuntimeError("The operation has an invalid persistent subelement range");
        }
        groups.emplace_back(elements.begin() + begin, elements.begin() + end);
    }
    return groups;
}

void DesignSubelementOperationProperties::setTargetElementGroups(
    const std::vector<std::vector<std::string>>& groups
)
{
    std::vector<long> offsets;
    std::vector<std::string> elements;
    offsets.reserve(groups.size() + 1);
    offsets.push_back(0);
    for (const auto& group : groups) {
        std::unordered_set<std::string> unique;
        for (const auto& element : group) {
            if (element.empty() || !unique.insert(element).second) {
                throw Base::ValueError(
                    "Every selected subelement must have one distinct non-empty name"
                );
            }
            elements.push_back(element);
        }
        offsets.push_back(static_cast<long>(elements.size()));
    }
    TargetElements.setValues(elements);
    TargetElementOffsets.setValues(offsets);
}

std::vector<Part::TopoShape> PartDesign::resolveDesignTargetEdges(
    const Part::TopoShape& shape,
    const std::vector<std::string>& references,
    bool useAllEdges
)
{
    if (shape.isNull()) {
        throw Part::NullShapeException("Cannot resolve edges against an empty Body state");
    }

    std::vector<Part::TopoShape> result;
    TopTools_IndexedMapOfShape uniqueEdges;
    const auto addEdge =
        [&](const TopoDS_Shape& candidate, const std::string& reference, bool requireDressable) {
            if (candidate.IsNull() || candidate.ShapeType() != TopAbs_EDGE) {
                if (requireDressable) {
                    throw Base::ValueError("Selected reference '" + reference + "' is not an edge");
                }
                return false;
            }

            const auto adjacent = shape.findAncestorsShapes(candidate, TopAbs_FACE);
            if (adjacent.size() != 2) {
                if (requireDressable) {
                    throw Base::ValueError(
                        "Selected edge '" + reference + "' does not bound exactly two faces"
                    );
                }
                return false;
            }
            if (BRep_Tool::Continuity(
                    TopoDS::Edge(candidate),
                    TopoDS::Face(adjacent.front()),
                    TopoDS::Face(adjacent.back())
                )
                != GeomAbs_C0) {
                if (requireDressable) {
                    throw Base::ValueError(
                        "Selected edge '" + reference
                        + "' is tangent-continuous and has no sharp corner to dress"
                    );
                }
                return false;
            }
            if (uniqueEdges.Contains(candidate)) {
                return true;
            }
            uniqueEdges.Add(candidate);
            result.emplace_back(candidate);
            return true;
        };

    if (useAllEdges) {
        for (const auto& edge : shape.getSubTopoShapes(TopAbs_EDGE)) {
            // "All edges" means all sharp, two-face solid edges. Smooth
            // seams and open boundaries are not dressable corners.
            addEdge(edge.getShape(), "all edges", false);
        }
        if (result.empty()) {
            throw Base::ValueError("The selected Body state has no sharp, two-face edges to dress");
        }
        return result;
    }

    for (const auto& reference : references) {
        Part::TopoShape selected;
        try {
            selected = shape.getSubTopoShape(reference.c_str());
        }
        catch (const Base::Exception& error) {
            throw Base::ValueError(
                "Cannot resolve selected reference '" + reference + "': " + error.what()
            );
        }
        if (selected.isNull()) {
            throw Base::ValueError(
                "Selected reference '" + reference + "' no longer exists on its exact Body state"
            );
        }

        if (selected.shapeType() == TopAbs_EDGE) {
            addEdge(selected.getShape(), reference, true);
            continue;
        }
        if (selected.shapeType() != TopAbs_FACE && selected.shapeType() != TopAbs_WIRE) {
            throw Base::ValueError(
                "Selected reference '" + reference + "' must be an edge, face, or wire"
            );
        }
        bool foundDressableEdge = false;
        for (TopExp_Explorer edges(selected.getShape(), TopAbs_EDGE); edges.More(); edges.Next()) {
            foundDressableEdge = addEdge(edges.Current(), reference, false) || foundDressableEdge;
        }
        if (!foundDressableEdge) {
            throw Base::ValueError(
                "Selected reference '" + reference + "' has no sharp, two-face edges to dress"
            );
        }
    }
    if (result.empty()) {
        throw Base::ValueError("No sharp, two-face edges were selected for the operation");
    }
    return result;
}

std::vector<Part::TopoShape> PartDesign::resolveDesignTargetFaces(
    const Part::TopoShape& shape,
    const std::vector<std::string>& references
)
{
    if (shape.isNull()) {
        throw Part::NullShapeException("Cannot resolve faces against an empty Body state");
    }
    if (references.empty()) {
        throw Base::ValueError("Select at least one face for the operation");
    }

    std::vector<Part::TopoShape> result;
    result.reserve(references.size());
    TopTools_IndexedMapOfShape uniqueFaces;
    for (const auto& reference : references) {
        if (reference.empty()) {
            throw Base::ValueError("A selected face reference is empty");
        }

        Part::TopoShape selected;
        try {
            selected = shape.getSubTopoShape(reference.c_str());
        }
        catch (const Base::Exception& error) {
            throw Base::ValueError(
                "Cannot resolve selected reference '" + reference + "': " + error.what()
            );
        }
        if (selected.isNull()) {
            throw Base::ValueError(
                "Selected reference '" + reference + "' no longer exists on its exact Body state"
            );
        }
        if (selected.shapeType() != TopAbs_FACE) {
            throw Base::ValueError("Selected reference '" + reference + "' is not a face");
        }
        if (shape.findAncestorsShapes(selected.getShape(), TopAbs_SOLID).size() != 1) {
            throw Base::ValueError(
                "Selected face '" + reference + "' does not belong to exactly one solid"
            );
        }
        if (uniqueFaces.Contains(selected.getShape())) {
            throw Base::ValueError("Selected face '" + reference + "' was included more than once");
        }
        uniqueFaces.Add(selected.getShape());
        result.push_back(std::move(selected));
    }
    return result;
}

PROPERTY_SOURCE(PartDesign::DesignExtrude, PartDesign::Pad)
PROPERTY_SOURCE(PartDesign::DesignRevolve, PartDesign::Revolution)
PROPERTY_SOURCE(PartDesign::DesignLoft, PartDesign::AdditiveLoft)
PROPERTY_SOURCE(PartDesign::DesignSweep, PartDesign::AdditivePipe)
PROPERTY_SOURCE(PartDesign::DesignHelix, PartDesign::AdditiveHelix)
PROPERTY_SOURCE(PartDesign::DesignBox, PartDesign::Box)
PROPERTY_SOURCE(PartDesign::DesignCylinder, PartDesign::Cylinder)
PROPERTY_SOURCE(PartDesign::DesignSphere, PartDesign::Sphere)
PROPERTY_SOURCE(PartDesign::DesignCone, PartDesign::Cone)
PROPERTY_SOURCE(PartDesign::DesignEllipsoid, PartDesign::Ellipsoid)
PROPERTY_SOURCE(PartDesign::DesignTorus, PartDesign::Torus)
PROPERTY_SOURCE(PartDesign::DesignPrism, PartDesign::Prism)
PROPERTY_SOURCE(PartDesign::DesignWedge, PartDesign::Wedge)
PROPERTY_SOURCE(PartDesign::DesignTube, PartDesign::Tube)
PROPERTY_SOURCE(PartDesign::DesignClone, PartDesign::Feature)
PROPERTY_SOURCE(PartDesign::DesignScale, PartDesign::Feature)
PROPERTY_SOURCE(PartDesign::DesignMirror, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignLinearPattern, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignCircularPattern, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignHole, PartDesign::Hole)
PROPERTY_SOURCE(PartDesign::DesignFillet, PartDesign::Fillet)
PROPERTY_SOURCE(PartDesign::DesignChamfer, PartDesign::Chamfer)
PROPERTY_SOURCE(PartDesign::DesignThickness, PartDesign::Thickness)
PROPERTY_SOURCE(PartDesign::DesignDraft, PartDesign::Draft)
PROPERTY_SOURCE(PartDesign::DesignCombine, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignSplit, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignSeparate, PartDesign::FeatureRefine)
PROPERTY_SOURCE(PartDesign::DesignScriptOperation, PartDesign::Feature)
PROPERTY_SOURCE(PartDesign::DesignGeneratedOperation, PartDesign::Feature)
PROPERTY_SOURCE(PartDesign::DesignBodyState, Part::Feature)
PROPERTY_SOURCE(PartDesign::DesignBodyPublication, PartDesign::Feature)

const char* DesignOperationProperties::ResultOperationEnums[] = {
    "New Body",
    "New Bodies",
    "Join",
    "Cut",
    "Intersect",
    "Modify",
    "Split",
    "Program Outputs",
    nullptr,
};

const char* DesignPatternProperties::PatternSourceEnums[] = {
    "Feature",
    "Body",
    nullptr,
};

bool DesignOperationProperties::supportsDesignResultOperation(std::string_view resultOperation) const
{
    if (dynamic_cast<const DesignSubelementOperationProperties*>(this)) {
        return resultOperation == "Modify";
    }
    return resultOperation == "New Body" || resultOperation == "Join" || resultOperation == "Cut"
        || resultOperation == "Intersect";
}

#define ADD_DESIGN_OPERATION_PROPERTIES() \
    do { \
        Base::Uuid operationId; \
        ADD_PROPERTY_TYPE( \
            OperationId, \
            (operationId), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly), \
            "Persistent identity of this Design History operation" \
        ); \
        Base::Uuid designId; \
        ADD_PROPERTY_TYPE( \
            DesignId, \
            (designId), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Persistent identity of the Design which owns this operation" \
        ); \
        ADD_PROPERTY_TYPE( \
            DesignSchemaVersion, \
            (currentDesignSchemaVersion), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden), \
            "Saved Design operation schema" \
        ); \
        ADD_PROPERTY_TYPE( \
            ResultOperation, \
            (0L), \
            "Operation", \
            App::Prop_None, \
            "Create a new Body or apply this operation to every explicit " \
            "target Body" \
        ); \
        ResultOperation.setEnums(ResultOperationEnums); \
        ADD_PROPERTY_TYPE( \
            InputStates, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Exact prior Body states, parallel to InputBodyIds" \
        ); \
        InputStates.setScope(App::LinkScope::Global); \
        ADD_PROPERTY_TYPE( \
            InputBodyIds, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Persistent Body identity for each exact input state" \
        ); \
        ADD_PROPERTY_TYPE( \
            InputFrames, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Exact Design-to-Body frame for each input state" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputBodyIds, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Persistent Body identity for each output state" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputFrames, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Exact Design-to-Body frame for each output state" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputPreviousInputIndices, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Input port advanced by each output; -1 creates a Body" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputPresence, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden), \
            "Whether each output Body exists at this History state" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputComponentIds, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Destination Component for each operation-created output Body" \
        ); \
        ADD_PROPERTY_TYPE( \
            TargetBodyIds, \
            (), \
            "Operation", \
            App::Prop_ReadOnly, \
            "Compatibility mirror of OutputBodyIds" \
        ); \
        ADD_PROPERTY_TYPE( \
            TargetFrames, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Compatibility mirror of OutputFrames" \
        ); \
        ADD_PROPERTY_TYPE( \
            DestinationComponentId, \
            (""), \
            "Operation", \
            App::Prop_None, \
            "Compatibility mirror for a single created output Component" \
        ); \
        ADD_PROPERTY_TYPE( \
            OutputShapes, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden), \
            "Atomic output Body-state geometry, parallel to OutputBodyIds" \
        ); \
    } while (false)

#define ADD_DESIGN_SUBELEMENT_PROPERTIES() \
    do { \
        ADD_PROPERTY_TYPE( \
            TargetElementOffsets, \
            (), \
            "VibeCAD Design", \
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden), \
            "Offsets partitioning TargetElements by target Body" \
        ); \
        TargetElementOffsets.setValues(std::vector<long> {0}); \
        ADD_PROPERTY_TYPE( \
            TargetElements, \
            (), \
            "Operation", \
            App::Prop_ReadOnly, \
            "Persistent subelement names grouped in OutputBodyIds order" \
        ); \
    } while (false)

#define ADD_DESIGN_PATTERN_PROPERTIES() \
    do { \
        ADD_PROPERTY_TYPE( \
            PatternSource, \
            (0L), \
            "Pattern", \
            App::Prop_None, \
            "Repeat one earlier Design feature or one exact Body state" \
        ); \
        PatternSource.setEnums(PatternSourceEnums); \
        ADD_PROPERTY_TYPE( \
            SourceOperation, \
            (nullptr), \
            "Pattern", \
            App::Prop_None, \
            "Earlier Design feature whose tool geometry is repeated" \
        ); \
        SourceOperation.setScope(App::LinkScope::Global); \
    } while (false)

void PartDesign::ensureDesignOperationPortSchema(App::DocumentObject& operation)
{
    auto* properties = dynamic_cast<DesignOperationProperties*>(&operation);
    if (!properties) {
        throw Base::TypeError("The object has no Design operation port contract");
    }

    const long schema = properties->DesignSchemaVersion.getValue();
    if (schema == currentDesignSchemaVersion) {
        return;
    }
    if (schema != 1) {
        throw Base::RuntimeError("The Design operation uses an unsupported saved schema");
    }

    const auto outputBodyIds = properties->TargetBodyIds.getValues();
    const auto outputFrames = properties->TargetFrames.getValues();
    const auto inputs = properties->InputStates.getValues();
    if (outputBodyIds.size() != outputFrames.size()) {
        throw Base::RuntimeError("The legacy Design operation has inconsistent target ports");
    }

    const bool createsBody = std::string_view(properties->ResultOperation.getValueAsString())
        == "New Body";
    if ((createsBody && (outputBodyIds.size() != 1 || !inputs.empty()))
        || (!createsBody && inputs.size() != outputBodyIds.size())) {
        throw Base::RuntimeError("The legacy Design operation cannot be migrated to explicit ports");
    }

    std::vector<long> previousInputIndices;
    std::vector<std::string> outputComponentIds(outputBodyIds.size());
    if (createsBody) {
        previousInputIndices.push_back(-1);
        outputComponentIds.front() = properties->DestinationComponentId.getValue();
        properties->InputBodyIds.setValues(std::vector<std::string> {});
        properties->InputFrames.setValues({});
    }
    else {
        previousInputIndices.reserve(inputs.size());
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            previousInputIndices.push_back(static_cast<long>(index));
        }
        properties->InputBodyIds.setValues(outputBodyIds);
        properties->InputFrames.setValues(outputFrames);
    }

    properties->OutputBodyIds.setValues(outputBodyIds);
    properties->OutputFrames.setValues(outputFrames);
    properties->OutputPreviousInputIndices.setValues(previousInputIndices);
    boost::dynamic_bitset<> outputPresence(outputBodyIds.size());
    outputPresence.set();
    properties->OutputPresence.setValues(outputPresence);
    properties->OutputComponentIds.setValues(outputComponentIds);
    properties->DesignSchemaVersion.setValue(currentDesignSchemaVersion);
}

DesignExtrude::DesignExtrude()
{
    // A Design operation is intentionally outside every Body and Component.
    // All of its modeling references therefore cross container boundaries.
    // Make that contract native instead of relying on a GUI-time scope
    // migration or a hidden intermediary object.
    Profile.setScope(App::LinkScope::Global);
    UpToFace.setScope(App::LinkScope::Global);
    UpToShape.setScope(App::LinkScope::Global);
    UpToFace2.setScope(App::LinkScope::Global);
    UpToShape2.setScope(App::LinkScope::Global);
    ReferenceAxis.setScope(App::LinkScope::Global);

    ADD_DESIGN_OPERATION_PROPERTIES();
}

void DesignExtrude::setupObject()
{
    Pad::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignExtrude::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Pad::mustExecute();
}

void DesignExtrude::onChanged(const App::Property* property)
{
    Pad::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignExtrude::recompute()
{
    if (!Suppressed.getValue()) {
        return Pad::recompute();
    }

    Shape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

TopoShape DesignExtrude::getExtrusionContextBaseShape() const
{
    const auto needsContext = [](const App::PropertyEnumeration& type) {
        const std::string_view value = type.getValueAsString();
        return value == "UpToFirst" || value == "UpToLast";
    };
    const bool secondSide = std::string_view(SideType.getValueAsString()) == "Two sides";
    if (!needsContext(Type) && !(secondSide && needsContext(Type2))) {
        return FeatureExtrude::getExtrusionContextBaseShape();
    }
    return exactSingleInputContextShape(*this, *this, "Up to first/last extrusion");
}

App::DocumentObjectExecReturn* DesignExtrude::execute()
{
    App::DocumentObjectExecReturn* result = Pad::execute();
    if (result != App::DocumentObject::StdReturn) {
        return result;
    }
    return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
}

DesignRevolve::DesignRevolve()
{
    Profile.setScope(App::LinkScope::Global);
    UpToFace.setScope(App::LinkScope::Global);
    ReferenceAxis.setScope(App::LinkScope::Global);

    ADD_DESIGN_OPERATION_PROPERTIES();
}

void DesignRevolve::setupObject()
{
    Revolution::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignRevolve::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Revolution::mustExecute();
}

void DesignRevolve::onChanged(const App::Property* property)
{
    Revolution::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignRevolve::recompute()
{
    if (!Suppressed.getValue()) {
        return Revolution::recompute();
    }

    Shape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

TopoShape DesignRevolve::getRevolutionContextBaseShape() const
{
    const std::string_view type = Type.getValueAsString();
    if (type != "UpToFirst" && type != "UpToLast" && type != "UpToFace") {
        return Revolved::getRevolutionContextBaseShape();
    }
    return exactSingleInputContextShape(*this, *this, "Target-dependent revolution");
}

App::DocumentObjectExecReturn* DesignRevolve::execute()
{
    App::DocumentObjectExecReturn* result = Revolution::execute();
    if (result != App::DocumentObject::StdReturn) {
        return result;
    }
    return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
}

DesignLoft::DesignLoft()
{
    Profile.setScope(App::LinkScope::Global);
    Sections.setScope(App::LinkScope::Global);
    ADD_DESIGN_OPERATION_PROPERTIES();
}

void DesignLoft::setupObject()
{
    AdditiveLoft::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignLoft::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return AdditiveLoft::mustExecute();
}

void DesignLoft::onChanged(const App::Property* property)
{
    AdditiveLoft::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignLoft::recompute()
{
    if (!Suppressed.getValue()) {
        return AdditiveLoft::recompute();
    }

    Shape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignLoft::execute()
{
    auto* result = AdditiveLoft::execute();
    if (result != App::DocumentObject::StdReturn) {
        return result;
    }
    return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
}

DesignSweep::DesignSweep()
{
    Profile.setScope(App::LinkScope::Global);
    Spine.setScope(App::LinkScope::Global);
    AuxiliarySpine.setScope(App::LinkScope::Global);
    Sections.setScope(App::LinkScope::Global);
    ADD_DESIGN_OPERATION_PROPERTIES();
}

void DesignSweep::setupObject()
{
    AdditivePipe::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignSweep::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return AdditivePipe::mustExecute();
}

void DesignSweep::onChanged(const App::Property* property)
{
    AdditivePipe::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignSweep::recompute()
{
    if (!Suppressed.getValue()) {
        return AdditivePipe::recompute();
    }

    Shape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignSweep::execute()
{
    auto* result = AdditivePipe::execute();
    if (result != App::DocumentObject::StdReturn) {
        return result;
    }
    return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
}

DesignHelix::DesignHelix()
{
    Profile.setScope(App::LinkScope::Global);
    ReferenceAxis.setScope(App::LinkScope::Global);
    ADD_DESIGN_OPERATION_PROPERTIES();
}

void DesignHelix::setupObject()
{
    AdditiveHelix::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignHelix::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return AdditiveHelix::mustExecute();
}

void DesignHelix::onChanged(const App::Property* property)
{
    AdditiveHelix::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignHelix::recompute()
{
    if (!Suppressed.getValue()) {
        return AdditiveHelix::recompute();
    }

    Shape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignHelix::execute()
{
    auto* result = AdditiveHelix::execute();
    if (result != App::DocumentObject::StdReturn) {
        return result;
    }
    return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
}

#define DEFINE_DESIGN_PRIMITIVE(ClassName, BaseName) \
    ClassName::ClassName() \
    { \
        ADD_DESIGN_OPERATION_PROPERTIES(); \
        BaseFeature.setValue(nullptr); \
        BaseFeature.setStatus(App::Property::ReadOnly, true); \
        BaseFeature.setStatus(App::Property::Hidden, true); \
        AddSubShape.setStatus(App::Property::Hidden, true); \
    } \
\
    void ClassName::setupObject() \
    { \
        BaseName::setupObject(); \
        bindDesignIdentity(*this, DesignId); \
    } \
\
    short ClassName::mustExecute() const \
    { \
        if (ResultOperation.isTouched() || designPortsTouched()) { \
            return 1; \
        } \
        return BaseName::mustExecute(); \
    } \
\
    void ClassName::onChanged(const App::Property* property) \
    { \
        BaseName::onChanged(property); \
        touchDesignResults(*this, property, OutputShapes); \
    } \
\
    App::DocumentObjectExecReturn* ClassName::recompute() \
    { \
        if (!Suppressed.getValue()) { \
            return BaseName::recompute(); \
        } \
        Shape.setValue(Part::TopoShape()); \
        AddSubShape.setValue(Part::TopoShape()); \
        return computeSuppressedOutputs(*this, *this); \
    } \
\
    App::DocumentObjectExecReturn* ClassName::execute() \
    { \
        TopoDS_Shape primitive; \
        if (auto* error = buildPrimitiveShape(primitive); error != App::DocumentObject::StdReturn) { \
            return error; \
        } \
        Part::TopoShape tool; \
        tool.setShape(primitive); \
        tool.Tag = -getID(); \
        AddSubShape.setValue(tool); \
        Shape.setValue(tool); \
        return computeOutputShapes(*this, *this, FuzzyTolerance.getValue()); \
    }

DEFINE_DESIGN_PRIMITIVE(DesignBox, Box)
DEFINE_DESIGN_PRIMITIVE(DesignCylinder, Cylinder)
DEFINE_DESIGN_PRIMITIVE(DesignSphere, Sphere)
DEFINE_DESIGN_PRIMITIVE(DesignCone, Cone)
DEFINE_DESIGN_PRIMITIVE(DesignEllipsoid, Ellipsoid)
DEFINE_DESIGN_PRIMITIVE(DesignTorus, Torus)
DEFINE_DESIGN_PRIMITIVE(DesignPrism, Prism)
DEFINE_DESIGN_PRIMITIVE(DesignWedge, Wedge)
DEFINE_DESIGN_PRIMITIVE(DesignTube, Tube)

#undef DEFINE_DESIGN_PRIMITIVE

DesignClone::DesignClone()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ResultOperation.setValue("New Bodies");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    ResultOperation.setStatus(App::Property::Hidden, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
}

void DesignClone::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignClone::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Bodies";
}

short DesignClone::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignClone::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignClone::recompute()
{
    if (!Suppressed.getValue()) {
        return Feature::recompute();
    }
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignClone::execute()
{
    gp_Trsf identity;
    int generatedCopyCount = 0;
    return computeDesignBodyCopies(*this, *this, {identity}, generatedCopyCount, "Clone");
}

DesignScale::DesignScale()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ResultOperation.setValue("Modify");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        Uniform,
        (true),
        "Scale",
        App::Prop_None,
        "Use one scale factor for all Design axes"
    );
    ADD_PROPERTY_TYPE(
        UniformScale,
        (1.0),
        "Scale",
        App::Prop_None,
        "Uniform scale factor; values must be greater than zero"
    );
    ADD_PROPERTY_TYPE(
        XScale,
        (1.0),
        "Scale",
        App::Prop_None,
        "Scale factor along the Design X axis"
    );
    ADD_PROPERTY_TYPE(
        YScale,
        (1.0),
        "Scale",
        App::Prop_None,
        "Scale factor along the Design Y axis"
    );
    ADD_PROPERTY_TYPE(
        ZScale,
        (1.0),
        "Scale",
        App::Prop_None,
        "Scale factor along the Design Z axis"
    );
    ADD_PROPERTY_TYPE(
        Center,
        (Base::Vector3d()),
        "Scale",
        App::Prop_None,
        "Fixed center of scaling in Design coordinates"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
}

void DesignScale::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignScale::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "Modify";
}

short DesignScale::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || Uniform.isTouched()
        || UniformScale.isTouched() || XScale.isTouched() || YScale.isTouched()
        || ZScale.isTouched() || Center.isTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignScale::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignScale::recompute()
{
    if (!Suppressed.getValue()) {
        return Feature::recompute();
    }
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignScale::execute()
{
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());

    try {
        ensureDesignOperationPortSchema(*this);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    if (!supportsDesignResultOperation(ResultOperation.getValueAsString())) {
        return outputError("Scale must modify explicit existing Bodies");
    }

    const double xFactor = Uniform.getValue() ? UniformScale.getValue() : XScale.getValue();
    const double yFactor = Uniform.getValue() ? UniformScale.getValue() : YScale.getValue();
    const double zFactor = Uniform.getValue() ? UniformScale.getValue() : ZScale.getValue();
    if (!std::isfinite(xFactor) || !std::isfinite(yFactor) || !std::isfinite(zFactor)
        || xFactor <= Precision::Confusion() || yFactor <= Precision::Confusion()
        || zFactor <= Precision::Confusion()) {
        return outputError("Every Scale factor must be finite and greater than zero");
    }

    const Base::Vector3d center = Center.getValue();
    if (!std::isfinite(center.x) || !std::isfinite(center.y) || !std::isfinite(center.z)) {
        return outputError("Scale center coordinates must be finite");
    }

    const auto& inputs = InputStates.getValues();
    const auto& inputBodyIds = InputBodyIds.getValues();
    const auto& inputFrames = InputFrames.getValues();
    const auto& outputBodyIds = OutputBodyIds.getValues();
    const auto& outputFrames = OutputFrames.getValues();
    const auto& previousInputIndices = OutputPreviousInputIndices.getValues();
    const auto& outputComponentIds = OutputComponentIds.getValues();
    if (outputBodyIds.empty() || inputs.size() != inputBodyIds.size()
        || inputs.size() != inputFrames.size() || inputs.size() != outputBodyIds.size()
        || outputFrames.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size()
        || outputComponentIds.size() != outputBodyIds.size()) {
        return outputError(
            "Scale requires one exact prior state and coordinate frame for every target Body"
        );
    }

    Base::Matrix4D transform;
    transform.setToUnity();
    transform[0][0] = xFactor;
    transform[1][1] = yFactor;
    transform[2][2] = zFactor;
    transform[0][3] = center.x * (1.0 - xFactor);
    transform[1][3] = center.y * (1.0 - yFactor);
    transform[2][3] = center.z * (1.0 - zFactor);

    std::unordered_set<std::string> uniqueBodyIds;
    std::unordered_set<App::DocumentObject*> uniqueInputs;
    std::vector<Part::TopoShape> outputs;
    std::vector<Part::TopoShape> preview;
    outputs.reserve(outputBodyIds.size());
    preview.reserve(outputBodyIds.size());

    try {
        for (std::size_t index = 0; index < outputBodyIds.size(); ++index) {
            auto* input = freecad_cast<Part::Feature*>(inputs[index]);
            auto* body = getDocument()
                ? bodyWithIdentity(*getDocument(), outputBodyIds[index])
                : nullptr;
            if (previousInputIndices[index] != static_cast<long>(index)
                || inputBodyIds[index] != outputBodyIds[index]
                || inputFrames[index] != outputFrames[index]
                || !outputComponentIds[index].empty() || outputBodyIds[index].empty()
                || !uniqueBodyIds.insert(outputBodyIds[index]).second || !input
                || input == this || input->getDocument() != getDocument()
                || !uniqueInputs.insert(input).second || !body
                || !stateMatchesBody(input, *body, outputBodyIds[index])) {
                return outputError("Scale lost an exact prior state for one target Body");
            }

            Part::TopoShape inputShape = shapeInBodyStateCoordinates(*input);
            if (inputShape.isNull() || inputShape.countSubShapes(TopAbs_SOLID) != 1) {
                return outputError("Scale requires exactly one solid in every target Body");
            }

            inputShape = transformedShape(inputShape, inputFrames[index]);
            Part::TopoShape scaled = inputShape.makeElementTransform(
                transform,
                Part::OpCodes::Gtransform,
                Part::CheckScale::checkScale,
                Part::CopyType::copy
            );
            if (scaled.isNull() || scaled.countSubShapes(TopAbs_SOLID) != 1 || !scaled.isValid()) {
                return outputError("Scale produced an invalid or non-solid Body result");
            }

            preview.push_back(scaled);
            outputs.push_back(transformedShape(scaled, outputFrames[index].inverse()));
        }
    }
    catch (const Standard_Failure& error) {
        return outputError(std::string("Scale failed in the geometry kernel: ")
                           + error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        return outputError(std::string("Scale failed: ") + error.what());
    }

    OutputShapes.setValues(outputs);
    boost::dynamic_bitset<> presence(outputs.size());
    presence.set();
    OutputPresence.setValues(presence);
    PreviewShape.setValue(Part::TopoShape().makeElementCompound(preview));
    return App::DocumentObject::StdReturn;
}

DesignMirror::DesignMirror()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_PATTERN_PROPERTIES();
    ResultOperation.setValue("New Bodies");
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        PlaneReference,
        (nullptr),
        "Mirror",
        App::Prop_None,
        "Optional datum plane, sketch plane, or planar face"
    );
    PlaneReference.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        PlaneReferenceFrame,
        (Base::Placement()),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Containing coordinate frame captured with the mirror plane"
    );
    ADD_PROPERTY_TYPE(
        PlaneOrigin,
        (Base::Vector3d()),
        "Mirror",
        App::Prop_None,
        "Mirror-plane origin in Design coordinates when no reference is selected"
    );
    ADD_PROPERTY_TYPE(
        PlaneNormal,
        (Base::Vector3d(0.0, 0.0, 1.0)),
        "Mirror",
        App::Prop_None,
        "Mirror-plane normal in Design coordinates when no reference is selected"
    );
    ADD_PROPERTY_TYPE(
        GeneratedOccurrenceCount,
        (0),
        "Pattern diagnostics",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Output),
        "Number of generated mirrored occurrences"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
}

void DesignMirror::setupObject()
{
    FeatureRefine::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignMirror::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Bodies" || resultOperation == "Join" || resultOperation == "Cut";
}

Part::TopoShape DesignMirror::refineDesignPatternShape(const Part::TopoShape& shape) const
{
    return refineShapeIfActive(shape);
}

short DesignMirror::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || patternPropertiesTouched()
        || PlaneReference.isTouched() || PlaneReferenceFrame.isTouched() || PlaneOrigin.isTouched()
        || PlaneNormal.isTouched()) {
        return 1;
    }
    return FeatureRefine::mustExecute();
}

void DesignMirror::onChanged(const App::Property* property)
{
    FeatureRefine::onChanged(property);
    auto* document = getDocument();
    if (property == &PlaneReference && document && !document->testStatus(App::Document::Restoring)) {
        PlaneReferenceFrame.setValue(designReferenceFrame(PlaneReference.getValue()));
    }
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignMirror::recompute()
{
    if (!Suppressed.getValue()) {
        return Feature::recompute();
    }
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    GeneratedOccurrenceCount.setValue(0);
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignMirror::execute()
{
    try {
        DesignAxis plane;
        if (PlaneReference.getValue()) {
            plane = resolveDesignAxisReference(
                PlaneReference,
                PlaneReferenceFrame.getValue(),
                DesignReferenceKind::MirrorPlane
            );
        }
        else {
            const Base::Vector3d origin = PlaneOrigin.getValue();
            const Base::Vector3d normal = PlaneNormal.getValue();
            if (normal.Length() <= Precision::Confusion()) {
                return outputError("Mirror plane normal must be nonzero");
            }
            plane = {
                gp_Pnt(origin.x, origin.y, origin.z),
                gp_Dir(normal.x, normal.y, normal.z),
            };
        }

        gp_Trsf transform;
        transform.SetMirror(gp_Ax2(plane.origin, plane.direction));
        int generated = 0;
        auto* result = computeDesignPatternOutputs(*this, *this, *this, {transform}, generated);
        GeneratedOccurrenceCount.setValue(generated);
        return result;
    }
    catch (const Standard_Failure& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.what());
    }
}

DesignLinearPattern::DesignLinearPattern()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_PATTERN_PROPERTIES();
    ResultOperation.setValue("New Bodies");
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        DirectionReference,
        (nullptr),
        "Linear Pattern",
        App::Prop_None,
        "Optional datum axis, sketch axis, or straight edge"
    );
    DirectionReference.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        DirectionReferenceFrame,
        (Base::Placement()),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Containing coordinate frame captured with the pattern direction"
    );
    ADD_PROPERTY_TYPE(
        Direction,
        (Base::Vector3d(1.0, 0.0, 0.0)),
        "Linear Pattern",
        App::Prop_None,
        "Pattern direction in Design coordinates when no reference is selected"
    );
    ADD_PROPERTY_TYPE(
        Spacing,
        (10.0),
        "Linear Pattern",
        App::Prop_None,
        "Distance between adjacent occurrences"
    );
    ADD_PROPERTY_TYPE(
        Occurrences,
        (2),
        "Linear Pattern",
        App::Prop_None,
        "Total occurrences including the unchanged source"
    );
    ADD_PROPERTY_TYPE(
        Centered,
        (false),
        "Linear Pattern",
        App::Prop_None,
        "Generate alternating occurrences on both sides of the source"
    );
    ADD_PROPERTY_TYPE(
        GeneratedOccurrenceCount,
        (0),
        "Pattern diagnostics",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Output),
        "Number of generated linear occurrences"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
}

void DesignLinearPattern::setupObject()
{
    FeatureRefine::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignLinearPattern::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Bodies" || resultOperation == "Join" || resultOperation == "Cut";
}

Part::TopoShape DesignLinearPattern::refineDesignPatternShape(const Part::TopoShape& shape) const
{
    return refineShapeIfActive(shape);
}

short DesignLinearPattern::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || patternPropertiesTouched()
        || DirectionReference.isTouched() || DirectionReferenceFrame.isTouched()
        || Direction.isTouched() || Spacing.isTouched() || Occurrences.isTouched()
        || Centered.isTouched()) {
        return 1;
    }
    return FeatureRefine::mustExecute();
}

void DesignLinearPattern::onChanged(const App::Property* property)
{
    FeatureRefine::onChanged(property);
    auto* document = getDocument();
    if (property == &DirectionReference && document
        && !document->testStatus(App::Document::Restoring)) {
        DirectionReferenceFrame.setValue(designReferenceFrame(DirectionReference.getValue()));
    }
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignLinearPattern::recompute()
{
    if (!Suppressed.getValue()) {
        return Feature::recompute();
    }
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    GeneratedOccurrenceCount.setValue(0);
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignLinearPattern::execute()
{
    try {
        const int occurrences = Occurrences.getValue();
        const double spacing = Spacing.getValue();
        if (occurrences < 2) {
            return outputError("Linear Pattern requires at least two total occurrences");
        }
        if (spacing <= Precision::Confusion()) {
            return outputError("Linear Pattern spacing must be greater than zero");
        }

        gp_Dir direction;
        if (DirectionReference.getValue()) {
            direction = resolveDesignAxisReference(
                            DirectionReference,
                            DirectionReferenceFrame.getValue(),
                            DesignReferenceKind::LinearDirection
            )
                            .direction;
        }
        else {
            const Base::Vector3d value = Direction.getValue();
            if (value.Length() <= Precision::Confusion()) {
                return outputError("Linear Pattern direction must be nonzero");
            }
            direction = gp_Dir(value.x, value.y, value.z);
        }

        std::vector<gp_Trsf> copies;
        copies.reserve(static_cast<std::size_t>(occurrences - 1));
        for (int index = 1; index < occurrences; ++index) {
            double multiplier = static_cast<double>(index);
            if (Centered.getValue()) {
                multiplier = static_cast<double>((index + 1) / 2) * (index % 2 == 0 ? -1.0 : 1.0);
            }
            gp_Trsf transform;
            transform.SetTranslation(gp_Vec(direction) * (spacing * multiplier));
            copies.push_back(transform);
        }

        int generated = 0;
        auto* result = computeDesignPatternOutputs(*this, *this, *this, copies, generated);
        GeneratedOccurrenceCount.setValue(generated);
        return result;
    }
    catch (const Standard_Failure& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.what());
    }
}

DesignCircularPattern::DesignCircularPattern()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_PATTERN_PROPERTIES();
    ResultOperation.setValue("New Bodies");
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        AxisReference,
        (nullptr),
        "Circular Pattern",
        App::Prop_None,
        "Optional datum axis, sketch axis, straight edge, or circular edge"
    );
    AxisReference.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        AxisReferenceFrame,
        (Base::Placement()),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Containing coordinate frame captured with the rotation axis"
    );
    ADD_PROPERTY_TYPE(
        AxisOrigin,
        (Base::Vector3d()),
        "Circular Pattern",
        App::Prop_None,
        "Axis origin in Design coordinates when no reference is selected"
    );
    ADD_PROPERTY_TYPE(
        AxisDirection,
        (Base::Vector3d(0.0, 0.0, 1.0)),
        "Circular Pattern",
        App::Prop_None,
        "Axis direction in Design coordinates when no reference is selected"
    );
    ADD_PROPERTY_TYPE(Angle, (360.0), "Circular Pattern", App::Prop_None, "Total angular extent");
    ADD_PROPERTY_TYPE(
        Occurrences,
        (2),
        "Circular Pattern",
        App::Prop_None,
        "Total occurrences including the unchanged source"
    );
    ADD_PROPERTY_TYPE(
        Reversed,
        (false),
        "Circular Pattern",
        App::Prop_None,
        "Reverse the rotation direction"
    );
    ADD_PROPERTY_TYPE(
        GeneratedOccurrenceCount,
        (0),
        "Pattern diagnostics",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Output),
        "Number of generated circular occurrences"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
}

void DesignCircularPattern::setupObject()
{
    FeatureRefine::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignCircularPattern::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Bodies" || resultOperation == "Join" || resultOperation == "Cut";
}

Part::TopoShape DesignCircularPattern::refineDesignPatternShape(const Part::TopoShape& shape) const
{
    return refineShapeIfActive(shape);
}

short DesignCircularPattern::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || patternPropertiesTouched()
        || AxisReference.isTouched() || AxisReferenceFrame.isTouched() || AxisOrigin.isTouched()
        || AxisDirection.isTouched() || Angle.isTouched() || Occurrences.isTouched()
        || Reversed.isTouched()) {
        return 1;
    }
    return FeatureRefine::mustExecute();
}

void DesignCircularPattern::onChanged(const App::Property* property)
{
    FeatureRefine::onChanged(property);
    auto* document = getDocument();
    if (property == &AxisReference && document && !document->testStatus(App::Document::Restoring)) {
        AxisReferenceFrame.setValue(designReferenceFrame(AxisReference.getValue()));
    }
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignCircularPattern::recompute()
{
    if (!Suppressed.getValue()) {
        return Feature::recompute();
    }
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    GeneratedOccurrenceCount.setValue(0);
    return computeSuppressedOutputs(*this, *this);
}

App::DocumentObjectExecReturn* DesignCircularPattern::execute()
{
    try {
        const int occurrences = Occurrences.getValue();
        const double angleDegrees = Angle.getValue();
        if (occurrences < 2) {
            return outputError("Circular Pattern requires at least two total occurrences");
        }
        if (angleDegrees <= Precision::Angular() || angleDegrees > 360.0 + Precision::Angular()) {
            return outputError("Circular Pattern angle must be greater than zero and no "
                               "more than 360 degrees");
        }

        DesignAxis axis;
        if (AxisReference.getValue()) {
            axis = resolveDesignAxisReference(
                AxisReference,
                AxisReferenceFrame.getValue(),
                DesignReferenceKind::RotationAxis
            );
        }
        else {
            const Base::Vector3d origin = AxisOrigin.getValue();
            const Base::Vector3d direction = AxisDirection.getValue();
            if (direction.Length() <= Precision::Confusion()) {
                return outputError("Circular Pattern axis direction must be nonzero");
            }
            axis = {
                gp_Pnt(origin.x, origin.y, origin.z),
                gp_Dir(direction.x, direction.y, direction.z),
            };
        }

        const bool fullCircle = std::abs(angleDegrees - 360.0) <= Precision::Angular();
        const double stepDegrees = fullCircle ? angleDegrees / static_cast<double>(occurrences)
                                              : angleDegrees / static_cast<double>(occurrences - 1);
        const double sign = Reversed.getValue() ? -1.0 : 1.0;
        std::vector<gp_Trsf> copies;
        copies.reserve(static_cast<std::size_t>(occurrences - 1));
        for (int index = 1; index < occurrences; ++index) {
            gp_Trsf transform;
            transform.SetRotation(
                gp_Ax1(axis.origin, axis.direction),
                Base::toRadians(sign * stepDegrees * static_cast<double>(index))
            );
            copies.push_back(transform);
        }

        int generated = 0;
        auto* result = computeDesignPatternOutputs(*this, *this, *this, copies, generated);
        GeneratedOccurrenceCount.setValue(generated);
        return result;
    }
    catch (const Standard_Failure& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        GeneratedOccurrenceCount.setValue(0);
        return outputError(error.what());
    }
}

DesignHole::DesignHole()
{
    Profile.setScope(App::LinkScope::Global);
    ADD_DESIGN_OPERATION_PROPERTIES();
    ResultOperation.setValue("Cut");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    // These inherited fields encode the retired Body-tip authoring contract.
    // They remain available on legacy Hole objects, but a DesignHole cannot
    // persist modeling data in them.
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    AddSubShape.setStatus(App::Property::Hidden, true);
    Midplane.setValue(false);
    Midplane.setStatus(App::Property::ReadOnly, true);
    Midplane.setStatus(App::Property::Hidden, true);
    UpToFace.setValue(nullptr);
    UpToFace.setStatus(App::Property::ReadOnly, true);
    UpToFace.setStatus(App::Property::Hidden, true);
    UpToFace2.setValue(nullptr);
    UpToFace2.setStatus(App::Property::ReadOnly, true);
    UpToFace2.setStatus(App::Property::Hidden, true);
    UpToShape.setSubListValues({});
    UpToShape.setStatus(App::Property::ReadOnly, true);
    UpToShape.setStatus(App::Property::Hidden, true);
    UpToShape2.setSubListValues({});
    UpToShape2.setStatus(App::Property::ReadOnly, true);
    UpToShape2.setStatus(App::Property::Hidden, true);
}

void DesignHole::setupObject()
{
    Hole::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignHole::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "Cut";
}

double DesignHole::getHoleThroughAllLength() const
{
    const auto& inputs = InputStates.getValues();
    const auto& frames = InputFrames.getValues();
    if (inputs.size() != frames.size()) {
        return std::max(Depth.getValue(), 1.0);
    }

    Bnd_Box bounds;
    bool hasShape = false;
    try {
        const Part::TopoShape profile = getProfileShape(
            Part::ShapeOption::NeedSubElement | Part::ShapeOption::ResolveLink
            | Part::ShapeOption::Transform | Part::ShapeOption::DontSimplifyCompound
        );
        if (!profile.isNull()) {
            BRepBndLib::Add(profile.getShape(), bounds);
            hasShape = true;
        }
    }
    catch (const Base::Exception&) {
        // During initial task-panel construction the profile or its support can
        // be incomplete. Exact validation still occurs before publication.
    }
    catch (const Standard_Failure&) {
    }

    for (std::size_t index = 0; index < inputs.size(); ++index) {
        const auto* feature = freecad_cast<const Part::Feature*>(inputs[index]);
        if (!feature || feature == this || feature->getDocument() != getDocument()
            || feature->Shape.getShape().isNull()) {
            continue;
        }
        const Part::TopoShape shape = transformedShape(feature->Shape.getShape(), frames[index]);
        BRepBndLib::Add(shape.getShape(), bounds);
        hasShape = true;
    }

    if (!hasShape || bounds.IsVoid()) {
        return std::max(Depth.getValue(), 1.0);
    }
    bounds.SetGap(0.0);
    const double length = 2.02 * std::sqrt(bounds.SquareExtent());
    return length > Precision::Confusion() ? length : std::max(Depth.getValue(), 1.0);
}

short DesignHole::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Hole::mustExecute();
}

void DesignHole::onChanged(const App::Property* property)
{
    Hole::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignHole::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignHole::execute()
{
    if (!supportsDesignResultOperation(ResultOperation.getValueAsString())) {
        return outputError("Hole only supports the Cut result mode");
    }

    try {
        Part::TopoShape profile = getProfileShape(
            Part::ShapeOption::NeedSubElement | Part::ShapeOption::ResolveLink
            | Part::ShapeOption::Transform | Part::ShapeOption::DontSimplifyCompound
        );
        positionByPrevious();
        const TopLoc_Location featureInverse = getLocation().Inverted();
        profile.move(featureInverse);

        Base::Vector3d profileDirection = guessNormalDirection(profile);
        if (Reversed.getValue()) {
            profileDirection *= -1.0;
        }
        gp_Vec direction(profileDirection.x, profileDirection.y, profileDirection.z);
        direction.Transform(featureInverse.Transformation());

        const std::string depthMode = DepthType.getValueAsString();
        double length = 0.0;
        if (depthMode == "Dimension") {
            length = Depth.getValue();
        }
        else if (depthMode == "ThroughAll") {
            length = getHoleThroughAllLength();
            Depth.setValue(length);
        }
        else {
            return outputError("Hole has an unsupported depth specification");
        }

        Part::TopoShape cutters;
        std::vector<Part::TopoShape> individualCutters;
        auto* cutterResult = buildHoleCutters(profile, direction, length, cutters, individualCutters);
        if (cutterResult != App::DocumentObject::StdReturn) {
            return cutterResult;
        }

        AddSubShape.setValue(cutters);
        Shape.setValue(cutters);
        return computeOutputShapes(*this, *this, FuzzyTolerance.getValue());
    }
    catch (const Standard_Failure& error) {
        return outputError(error.GetMessageString());
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }
}

DesignFillet::DesignFillet()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_SUBELEMENT_PROPERTIES();
    ResultOperation.setValue("Modify");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    // These fields belong to the retired Body-tip contract. They remain only
    // because this type reuses the mature Fillet parameter/view-provider
    // implementation; new modeling data can never be stored in them.
    Base.setValue(nullptr);
    Base.setStatus(App::Property::ReadOnly, true);
    Base.setStatus(App::Property::Hidden, true);
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    AddSubShape.setStatus(App::Property::Hidden, true);
    SupportTransform.setStatus(App::Property::Hidden, true);
}

void DesignFillet::setupObject()
{
    Fillet::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignFillet::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || TargetElementOffsets.isTouched()
        || TargetElements.isTouched() || UseAllEdges.isTouched()) {
        return 1;
    }
    return Fillet::mustExecute();
}

void DesignFillet::onChanged(const App::Property* property)
{
    Fillet::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignFillet::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignFillet::execute()
{
    const double radius = Radius.getValue();
    if (radius <= 0.0) {
        return outputError("Fillet radius must be greater than zero");
    }

    return computeDesignDressupOutputs(
        *this,
        *this,
        *this,
        "Fillet",
        "edges",
        [useAllEdges = UseAllEdges.getValue(
         )](const Part::TopoShape& base, const std::vector<std::string>& references) {
            return resolveDesignTargetEdges(base, references, useAllEdges);
        },
        [this,
         radius](const Part::TopoShape& base, const std::vector<Part::TopoShape>& edges, std::size_t, const Base::Placement&) {
#if defined(__GNUC__) && defined(FC_OS_LINUX)
            Part::SignalException signalGuard;
#endif
            Part::TopoShape output;
            output.makeElementFillet(base, edges, radius, radius);
            return refineShapeIfActive(output);
        }
    );
}

DesignChamfer::DesignChamfer()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_SUBELEMENT_PROPERTIES();
    ResultOperation.setValue("Modify");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    Base.setValue(nullptr);
    Base.setStatus(App::Property::ReadOnly, true);
    Base.setStatus(App::Property::Hidden, true);
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    AddSubShape.setStatus(App::Property::Hidden, true);
    SupportTransform.setStatus(App::Property::Hidden, true);
}

void DesignChamfer::setupObject()
{
    Chamfer::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignChamfer::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || TargetElementOffsets.isTouched()
        || TargetElements.isTouched() || UseAllEdges.isTouched()) {
        return 1;
    }
    return Chamfer::mustExecute();
}

void DesignChamfer::onChanged(const App::Property* property)
{
    Chamfer::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignChamfer::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignChamfer::execute()
{
    const int type = ChamferType.getValue();
    const double size = Size.getValue();
    const double secondSize = Size2.getValue();
    const double angle = Angle.getValue();
    if (size <= 0.0) {
        return outputError("Chamfer size must be greater than zero");
    }
    if (type == static_cast<int>(Part::ChamferType::twoDistances) && secondSize <= 0.0) {
        return outputError("Chamfer second size must be greater than zero");
    }
    if (type == static_cast<int>(Part::ChamferType::distanceAngle)
        && (angle <= 0.0 || angle >= 180.0)) {
        return outputError("Chamfer angle must be greater than 0 and less than 180 degrees");
    }
    if (type < static_cast<int>(Part::ChamferType::equalDistance)
        || type > static_cast<int>(Part::ChamferType::distanceAngle)) {
        return outputError("Chamfer type is invalid");
    }

    const double secondParameter = type == static_cast<int>(Part::ChamferType::distanceAngle)
        ? angle
        : secondSize;
    const auto flip = FlipDirection.getValue() ? Part::Flip::flip : Part::Flip::none;

    return computeDesignDressupOutputs(
        *this,
        *this,
        *this,
        "Chamfer",
        "edges",
        [useAllEdges = UseAllEdges.getValue(
         )](const Part::TopoShape& base, const std::vector<std::string>& references) {
            return resolveDesignTargetEdges(base, references, useAllEdges);
        },
        [this,
         type,
         size,
         secondParameter,
         flip](const Part::TopoShape& base, const std::vector<Part::TopoShape>& edges, std::size_t, const Base::Placement&) {
            Part::SignalException signalGuard;
            Part::TopoShape output;
            output.makeElementChamfer(
                base,
                edges,
                static_cast<Part::ChamferType>(type),
                size,
                secondParameter,
                nullptr,
                flip
            );
            return refineShapeIfActive(output);
        }
    );
}

DesignThickness::DesignThickness()
{
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_SUBELEMENT_PROPERTIES();
    ResultOperation.setValue("Modify");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    Base.setValue(nullptr);
    Base.setStatus(App::Property::ReadOnly, true);
    Base.setStatus(App::Property::Hidden, true);
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    AddSubShape.setStatus(App::Property::Hidden, true);
    SupportTransform.setStatus(App::Property::Hidden, true);
}

void DesignThickness::setupObject()
{
    Thickness::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignThickness::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || TargetElementOffsets.isTouched()
        || TargetElements.isTouched() || Value.isTouched() || Reversed.isTouched()
        || Intersection.isTouched() || Mode.isTouched() || Join.isTouched()) {
        return 1;
    }
    return Thickness::mustExecute();
}

void DesignThickness::onChanged(const App::Property* property)
{
    Thickness::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignThickness::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignThickness::execute()
{
    const double value = Value.getValue();
    const int mode = Mode.getValue();
    int join = Join.getValue();
    if (value <= 2.0 * Precision::Confusion()) {
        return outputError("Thickness must be greater than the geometry tolerance");
    }
    if (mode < 0 || mode > 2) {
        return outputError("Thickness mode is invalid");
    }
    if (join < 0 || join > 1) {
        return outputError("Thickness join type is invalid");
    }
    if (join == 1) {
        join = 2;
    }

    const double signedValue = Reversed.getValue() ? -value : value;
    const bool intersection = Intersection.getValue();
    return computeDesignDressupOutputs(
        *this,
        *this,
        *this,
        "Thickness",
        "faces",
        [](const Part::TopoShape& base, const std::vector<std::string>& references) {
            return resolveDesignTargetFaces(base, references);
        },
        [this,
         signedValue,
         intersection,
         mode,
         join](const Part::TopoShape& base, const std::vector<Part::TopoShape>& faces, std::size_t, const Base::Placement&) {
            Part::TopoShape output = base.makeElementThickSolid(
                faces,
                signedValue,
                Precision::Confusion(),
                intersection,
                false,
                static_cast<int16_t>(mode),
                static_cast<Part::JoinType>(join)
            );
            return refineShapeIfActive(output);
        }
    );
}

DesignDraft::DesignDraft()
{
    NeutralPlane.setScope(App::LinkScope::Global);
    PullDirection.setScope(App::LinkScope::Global);
    ADD_DESIGN_OPERATION_PROPERTIES();
    ADD_DESIGN_SUBELEMENT_PROPERTIES();
    ResultOperation.setValue("Modify");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        NeutralPlaneFrame,
        (Base::Placement()),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Containing coordinate frame captured with the neutral-plane reference"
    );
    ADD_PROPERTY_TYPE(
        PullDirectionFrame,
        (Base::Placement()),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Containing coordinate frame captured with the pull-direction reference"
    );

    Base.setValue(nullptr);
    Base.setStatus(App::Property::ReadOnly, true);
    Base.setStatus(App::Property::Hidden, true);
    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    AddSubShape.setStatus(App::Property::Hidden, true);
    SupportTransform.setStatus(App::Property::Hidden, true);
}

void DesignDraft::setupObject()
{
    Draft::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignDraft::mustExecute() const
{
    if (ResultOperation.isTouched() || designPortsTouched() || TargetElementOffsets.isTouched()
        || TargetElements.isTouched() || Angle.isTouched() || NeutralPlane.isTouched()
        || NeutralPlaneFrame.isTouched() || PullDirection.isTouched()
        || PullDirectionFrame.isTouched() || Reversed.isTouched()) {
        return 1;
    }
    return Draft::mustExecute();
}

void DesignDraft::onChanged(const App::Property* property)
{
    Draft::onChanged(property);
    auto* document = getDocument();
    if (document && !document->testStatus(App::Document::Restoring)) {
        if (property == &NeutralPlane) {
            NeutralPlaneFrame.setValue(designReferenceFrame(NeutralPlane.getValue()));
        }
        else if (property == &PullDirection) {
            PullDirectionFrame.setValue(designReferenceFrame(PullDirection.getValue()));
        }
    }
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignDraft::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignDraft::execute()
{
    const double angle = Base::toRadians(Angle.getValue());
    if (angle <= Precision::Angular() || angle >= std::numbers::pi / 2) {
        return outputError("Draft angle must be greater than zero and less than 90 degrees");
    }
    const double signedAngle = Reversed.getValue() ? -angle : angle;
    std::optional<DraftComputeProps> firstComputed;

    App::DocumentObjectExecReturn* result = computeDesignDressupOutputs(
        *this,
        *this,
        *this,
        "Draft",
        "faces",
        [](const Part::TopoShape& base, const std::vector<std::string>& references) {
            return resolveDesignTargetFaces(base, references);
        },
        [this, signedAngle, &firstComputed](
            const Part::TopoShape& base,
            const std::vector<Part::TopoShape>& faces,
            std::size_t,
            const Base::Placement& targetFrame
        ) {
            for (const auto& face : faces) {
                BRepAdaptor_Surface surface(TopoDS::Face(face.getShape()));
                if (surface.GetType() != GeomAbs_Plane && surface.GetType() != GeomAbs_Cylinder
                    && surface.GetType() != GeomAbs_Cone) {
                    throw Base::TypeError("Draft supports planar, cylindrical, and conical faces");
                }
            }

            std::optional<gp_Dir> pullDirection = resolveDraftPullDirection(*this, targetFrame);
            const gp_Pln neutralPlane
                = resolveDraftNeutralPlane(*this, faces.front(), pullDirection, targetFrame);
            if (!pullDirection) {
                pullDirection = neutralPlane.Axis().Direction();
            }

            Part::TopoShape output
                = base.makeElementDraft(faces, *pullDirection, signedAngle, neutralPlane, false);
            if (!firstComputed) {
                firstComputed = DraftComputeProps {
                    *pullDirection,
                    neutralPlane,
                };
            }
            return refineShapeIfActive(output);
        }
    );
    if (result == App::DocumentObject::StdReturn && firstComputed) {
        setLastComputedProps(*firstComputed);
    }
    return result;
}

DesignCombine::DesignCombine()
{
    ADD_DESIGN_OPERATION_PROPERTIES();

    ResultOperation.setValue("Join");
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    Base::Uuid resultBodyId;
    ADD_PROPERTY_TYPE(
        ResultBodyId,
        (resultBodyId),
        "Operation",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of the Body which receives the combined solid"
    );
    ADD_PROPERTY_TYPE(
        KeepTools,
        (false),
        "Operation",
        App::Prop_None,
        "Keep tool Bodies present after the combination"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    SuppressedShape.setStatus(App::Property::Hidden, true);
}

void DesignCombine::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignCombine::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "Join" || resultOperation == "Cut" || resultOperation == "Intersect";
}

short DesignCombine::mustExecute() const
{
    if (ResultOperation.isTouched() || ResultBodyId.isTouched() || KeepTools.isTouched()
        || Refine.isTouched() || FuzzyTolerance.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignCombine::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignCombine::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        PreviewShape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignCombine::execute()
{
    return computeDesignCombineOutputs(*this, [this](const Part::TopoShape& shape) {
        return refineShapeIfActive(shape);
    });
}

DesignSplit::DesignSplit()
{
    ADD_DESIGN_OPERATION_PROPERTIES();

    ResultOperation.setValue("Split");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    Base::Uuid sourceBodyId;
    ADD_PROPERTY_TYPE(
        SourceBodyId,
        (sourceBodyId),
        "Operation",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of the source Body being divided"
    );
    ADD_PROPERTY_TYPE(
        Splitters,
        (nullptr, nullptr),
        "Operation",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Exact faces, surfaces, shells, or solids which divide the source Body"
    );
    Splitters.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        SplitterFrames,
        (),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Saved containing coordinate frame for every Split definition"
    );
    ADD_PROPERTY_TYPE(
        RegionWitnesses,
        (),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Strict interior identity point for every Split output Body"
    );
    ADD_PROPERTY_TYPE(
        RetainedRegionChosen,
        (false),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Whether the user explicitly chose which region retains the source Body identity"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    SuppressedShape.setStatus(App::Property::Hidden, true);
}

void DesignSplit::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignSplit::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "Split";
}

short DesignSplit::mustExecute() const
{
    if (SourceBodyId.isTouched() || Splitters.isTouched() || SplitterFrames.isTouched()
        || RegionWitnesses.isTouched() || RetainedRegionChosen.isTouched() || Refine.isTouched()
        || FuzzyTolerance.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignSplit::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignSplit::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        PreviewShape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignSplit::execute()
{
    return computeDesignSplitOutputs(*this, [this](const Part::TopoShape& shape) {
        return refineShapeIfActive(shape);
    });
}

DesignSeparate::DesignSeparate()
{
    ADD_DESIGN_OPERATION_PROPERTIES();

    ResultOperation.setValue("New Bodies");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        Source,
        (nullptr),
        "Operation",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Earlier reusable Design definition whose solids become Bodies"
    );
    Source.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        RegionWitnesses,
        (),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Strict interior Design-space identity point for every output Body"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    SuppressedShape.setStatus(App::Property::Hidden, true);
}

void DesignSeparate::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

bool DesignSeparate::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Bodies";
}

short DesignSeparate::mustExecute() const
{
    if (Source.isTouched() || RegionWitnesses.isTouched() || Refine.isTouched()
        || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignSeparate::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

App::DocumentObjectExecReturn* DesignSeparate::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        PreviewShape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignSeparate::execute()
{
    return computeDesignSeparateOutputs(*this, [this](const Part::TopoShape& shape) {
        return refineShapeIfActive(shape);
    });
}

DesignScriptOperation::DesignScriptOperation()
{
    ADD_DESIGN_OPERATION_PROPERTIES();

    ResultOperation.setValue("Program Outputs");
    ResultOperation.setStatus(App::Property::ReadOnly, true);
    DestinationComponentId.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        ProgramId,
        (""),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Stable identity of the VibeScript program"
    );
    ADD_PROPERTY_TYPE(
        ProgramRevision,
        (""),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Accepted VibeScript source revision"
    );
    ADD_PROPERTY_TYPE(
        ProgramObjectName,
        (""),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Saved name of the portable VibeScript program metadata object"
    );
    ADD_PROPERTY_TYPE(
        ProgramOutputKeys,
        (),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Stable source-level identity of every published program output"
    );
    ADD_PROPERTY_TYPE(
        ProgramOutputTypes,
        (),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Published topology type parallel to ProgramOutputKeys"
    );
    ADD_PROPERTY_TYPE(
        ScriptOutputKeys,
        (),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Stable source-level identity of each output Body"
    );
    ADD_PROPERTY_TYPE(
        ScriptOutputLabels,
        (),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Human-facing label of each output Body"
    );
    ADD_PROPERTY_TYPE(
        AcceptedShapes,
        (),
        "VibeScript",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Isolated and validated geometry for each accepted output"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    SuppressedShape.setStatus(App::Property::Hidden, true);
}

bool DesignScriptOperation::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "Program Outputs";
}

void DesignScriptOperation::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignScriptOperation::mustExecute() const
{
    if (ProgramId.isTouched() || ProgramRevision.isTouched() || ProgramObjectName.isTouched()
        || ProgramOutputKeys.isTouched() || ProgramOutputTypes.isTouched()
        || ScriptOutputKeys.isTouched() || ScriptOutputLabels.isTouched()
        || AcceptedShapes.isTouched() || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignScriptOperation::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    touchDesignResults(*this, property, OutputShapes);
}

void DesignScriptOperation::onDocumentRestored()
{
    Feature::onDocumentRestored();

    // DesignScriptOperation first shipped with only Body-output metadata.
    // Preserve those saved documents by deriving the all-output catalog from
    // the exact legacy list. New documents always persist both properties.
    const auto bodyKeys = ScriptOutputKeys.getValues();
    if (ProgramOutputKeys.getValues().empty() && !bodyKeys.empty()) {
        ProgramOutputKeys.setValues(bodyKeys);
        ProgramOutputTypes.setValues(std::vector<std::string>(bodyKeys.size(), "solid"));
    }
}

App::DocumentObjectExecReturn* DesignScriptOperation::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        PreviewShape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignScriptOperation::execute()
{
    const auto programKeys = ProgramOutputKeys.getValues();
    const auto programTypes = ProgramOutputTypes.getValues();
    const auto keys = ScriptOutputKeys.getValues();
    const auto labels = ScriptOutputLabels.getValues();
    const auto accepted = AcceptedShapes.getValues();
    const auto bodyIds = OutputBodyIds.getValues();
    const auto previousInputIndices = OutputPreviousInputIndices.getValues();
    if (ProgramId.getStrValue().empty() || ProgramRevision.getStrValue().empty()
        || programKeys.empty() || programTypes.size() != programKeys.size()
        || labels.size() != keys.size() || accepted.size() != keys.size()
        || bodyIds.size() != keys.size() || previousInputIndices.size() != keys.size()) {
        return outputError("A VibeScript operation has inconsistent accepted output metadata");
    }

    std::unordered_map<std::string, std::string> programOutputs;
    for (std::size_t index = 0; index < programKeys.size(); ++index) {
        if (programKeys[index].empty() || programTypes[index].empty()
            || !programOutputs.emplace(programKeys[index], programTypes[index]).second) {
            return outputError(
                "Every published VibeScript output requires one distinct key and topology type"
            );
        }
    }

    std::unordered_set<std::string> uniqueKeys;
    std::unordered_set<std::string> uniqueBodyIds;
    for (std::size_t index = 0; index < keys.size(); ++index) {
        const auto& shape = accepted[index];
        if (keys[index].empty() || !uniqueKeys.insert(keys[index]).second
            || !programOutputs.contains(keys[index]) || programOutputs[keys[index]] != "solid"
            || bodyIds[index].empty() || !uniqueBodyIds.insert(bodyIds[index]).second) {
            return outputError(
                "Every VibeScript output requires one distinct stable key and Body identity"
            );
        }
        if (shape.isNull() || !shape.hasSubShape(TopAbs_SOLID)
            || shape.countSubShapes(TopAbs_SOLID) != 1) {
            return outputError("Every VibeScript Body output must contain exactly one solid");
        }
    }

    boost::dynamic_bitset<> presence(keys.size());
    presence.set();
    OutputShapes.setValues(accepted);
    OutputPresence.setValues(presence);
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    return App::DocumentObject::StdReturn;
}

DesignGeneratedOperation::DesignGeneratedOperation()
{
    ADD_DESIGN_OPERATION_PROPERTIES();

    ResultOperation.setValue("New Body");
    ResultOperation.setStatus(App::Property::ReadOnly, true);

    ADD_PROPERTY_TYPE(
        Generator,
        (nullptr),
        "Generated Feature",
        App::Prop_None,
        "Design-internal native feature which parametrically generates this result"
    );
    Generator.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        GeneratorKind,
        (""),
        "Generated Feature",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Stable semantic kind of the native generator"
    );
    ADD_PROPERTY_TYPE(
        OutputLabel,
        (""),
        "Generated Feature",
        App::Prop_None,
        "Human-facing label of the generated Body"
    );

    BaseFeature.setValue(nullptr);
    BaseFeature.setStatus(App::Property::ReadOnly, true);
    BaseFeature.setStatus(App::Property::Hidden, true);
    SuppressedShape.setStatus(App::Property::Hidden, true);
}

bool DesignGeneratedOperation::supportsDesignResultOperation(std::string_view resultOperation) const
{
    return resultOperation == "New Body" || resultOperation == "Modify";
}

void DesignGeneratedOperation::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignGeneratedOperation::mustExecute() const
{
    if (Generator.isTouched() || GeneratorKind.isTouched() || OutputLabel.isTouched()
        || designPortsTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

void DesignGeneratedOperation::onChanged(const App::Property* property)
{
    Feature::onChanged(property);
    if (property == &OutputShapes) {
        // A linked FeaturePython generator can execute during restore or a
        // dependency recompute without changing the Generator link itself.
        // Its newly published shape must invalidate the retained Body state.
        touchDesignResultConsumers(*this, true);
    }
    else {
        touchDesignResults(*this, property, OutputShapes);
    }
}

App::DocumentObjectExecReturn* DesignGeneratedOperation::recompute()
{
    if (Suppressed.getValue()) {
        Shape.setValue(Part::TopoShape());
        PreviewShape.setValue(Part::TopoShape());
        return computeSuppressedOutputs(*this, *this);
    }
    return Feature::recompute();
}

App::DocumentObjectExecReturn* DesignGeneratedOperation::execute()
{
    auto* generator = freecad_cast<Part::Feature*>(Generator.getValue());
    const auto resultOperation = std::string_view(ResultOperation.getValueAsString());
    const auto inputs = InputStates.getValues();
    const auto inputBodyIds = InputBodyIds.getValues();
    const auto inputFrames = InputFrames.getValues();
    const auto bodyIds = OutputBodyIds.getValues();
    const auto savedOutputFrames = OutputFrames.getValues();
    const auto previousInputIndices = OutputPreviousInputIndices.getValues();
    const bool createsBody = resultOperation == "New Body";
    const bool modifiesBody = resultOperation == "Modify";
    const bool validNewBodyPorts = createsBody && inputs.empty() && inputBodyIds.empty()
        && inputFrames.empty() && bodyIds.size() == 1 && savedOutputFrames.size() == 1
        && previousInputIndices.size() == 1 && previousInputIndices.front() == -1;
    const bool validModifyPorts = modifiesBody && inputs.size() == 1 && inputBodyIds.size() == 1
        && inputFrames.size() == 1 && previousInputIndices.size() == 1
        && previousInputIndices.front() == 0 && bodyIds.size() == 1 && savedOutputFrames.size() == 1
        && inputBodyIds.front() == bodyIds.front()
        && inputFrames.front() == savedOutputFrames.front();
    if (GeneratorKind.getStrValue().empty() || OutputLabel.getStrValue().empty() || !generator
        || generator == this || generator->getDocument() != getDocument() || bodyIds.size() != 1
        || savedOutputFrames.size() != 1 || (!validNewBodyPorts && !validModifyPorts)) {
        return outputError(
            "A generated Design operation has inconsistent generator or output "
            "metadata"
        );
    }

    Part::TopoShape generated;
    if (createsBody) {
        const Base::Placement generatedFrame = App::GeoFeature::getGlobalPlacement(generator);
        OutputFrames.setValues({generatedFrame});
        // TargetFrames is the schema-v1 compatibility mirror of OutputFrames.
        // Keep the two ports atomic when a generated feature changes its frame;
        // validateDesign() deliberately rejects a partially updated operation.
        TargetFrames.setValues({generatedFrame});
        // A generator may publish a one-solid compound whose child solid has
        // its own TopLoc_Location (modeled Fasteners do this after recompute).
        // Bake the complete generator shape into Design coordinates and then
        // express it in the retained Body frame. Resetting the extracted
        // solid's placement would discard that child location and make the
        // result change across recompute/undo/abort cycles.
        generated = transformedShape(
            shapeInDesignCoordinates(*generator),
            generatedFrame.inverse()
        );
    }
    else {
        // A promoted legacy generator remains at Design scope in its exact
        // global placement. Existing Body identity and frame do not move, so
        // express the generated solid in that Body's saved local coordinates.
        generated = transformedShape(
            shapeInDesignCoordinates(*generator),
            savedOutputFrames.front().inverse()
        );
        TargetFrames.setValues(savedOutputFrames);
    }
    if (generated.isNull() || !generated.hasSubShape(TopAbs_SOLID)
        || generated.countSubShapes(TopAbs_SOLID) != 1) {
        return outputError("A native Design generator must produce exactly one solid");
    }
    // Both paths above already express the result in the retained Body frame.
    // The named-subshape cache intentionally stores topology independently of
    // compound ancestry, so use OCCT's explorer for the solid's cumulative
    // parent/child location and retain the mapped element table separately.
    Part::TopoShape mappedSolid = generated.getSubTopoShape(TopAbs_SOLID, 1);
    TopExp_Explorer solidExplorer(generated.getShape(), TopAbs_SOLID);
    if (!solidExplorer.More()) {
        return outputError("A native Design generator lost its one solid result");
    }
    mappedSolid.setShape(solidExplorer.Current(), false);
    generated = mappedSolid;
    if (generated.getPlacement() != Base::Placement()) {
        // Part::Feature forces an assigned Shape's top-level transform to its
        // Placement while recomputing. Bake the extracted child's rigid
        // location into an unlocated copy so that synchronization cannot erase
        // it. TopoShape::bakeInTransform() uses a general geometric transform;
        // that needlessly converts curved BREP and can change its mass
        // properties even for a pure placement.
        const Base::Placement childPlacement = generated.getPlacement();
        generated.setPlacement(Base::Placement());
        generated.transformShape(childPlacement.toMatrix(), true, true);
    }

    boost::dynamic_bitset<> presence(1);
    presence.set();
    OutputShapes.setValues({generated});
    OutputPresence.setValues(presence);
    Shape.setValue(Part::TopoShape());
    PreviewShape.setValue(Part::TopoShape());
    return App::DocumentObject::StdReturn;
}

#undef ADD_DESIGN_SUBELEMENT_PROPERTIES
#undef ADD_DESIGN_PATTERN_PROPERTIES
#undef ADD_DESIGN_OPERATION_PROPERTIES

DesignBodyState::DesignBodyState()
{
    // Timeline resources normally stop recomputing when their owner is
    // suppressed. A Body state is the explicit bypass output of its owner, so
    // it must still execute and copy OutputPresence/OutputShapes. The state
    // itself is never independently suppressible.
    App::SuppressibleExtension::initExtension(this);
    Suppressed.setValue(false);
    Suppressed.setStatus(App::Property::ReadOnly, true);
    Suppressed.setStatus(App::Property::Hidden, true);

    ADD_PROPERTY_TYPE(
        Operation,
        (nullptr),
        "VibeCAD Design",
        App::Prop_None,
        "Design-global History operation which produces this Body state"
    );
    Operation.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        OutputIndex,
        (0),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Index of this Body state in the operation output set"
    );

    Base::Uuid designId;
    ADD_PROPERTY_TYPE(
        DesignId,
        (designId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the Design which owns this Body state"
    );
    Base::Uuid operationId;
    ADD_PROPERTY_TYPE(
        OperationId,
        (operationId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the operation producing this Body state"
    );
    Base::Uuid bodyId;
    ADD_PROPERTY_TYPE(
        BodyId,
        (bodyId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of the Body advanced by this state"
    );
    Base::Uuid stateId;
    ADD_PROPERTY_TYPE(
        BodyStateId,
        (stateId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of this exact Body state"
    );
    ADD_PROPERTY_TYPE(
        PreviousState,
        (nullptr),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Exact preceding Design-owned state of the same Body"
    );
    PreviousState.setScope(App::LinkScope::Global);
    ADD_PROPERTY_TYPE(
        Present,
        (true),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden),
        "Whether this Body exists at this exact History state"
    );
}

void DesignBodyState::setupObject()
{
    Part::Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignBodyState::mustExecute() const
{
    if (Operation.isTouched() || OutputIndex.isTouched() || PreviousState.isTouched()) {
        return 1;
    }
    const auto* operation = dynamic_cast<const DesignOperation*>(Operation.getValue());
    const int index = OutputIndex.getValue();
    if (operation && index >= 0) {
        const auto& outputs = operation->designOutputShapes().getValues();
        const auto& presence = operation->designOutputPresence().getValues();
        const auto outputIndex = static_cast<std::size_t>(index);
        if (outputIndex < outputs.size() && outputIndex < presence.size()) {
            const bool expectedPresent = presence[outputIndex];
            const auto& expectedShape = outputs[outputIndex];
            const auto& currentShape = Shape.getShape();
            if (Present.getValue() != expectedPresent
                || (expectedPresent
                    && (currentShape.isNull()
                        || !currentShape.getShape().IsPartner(
                            expectedShape.getShape()
                        )))
                || (!expectedPresent && !currentShape.isNull())) {
                return 1;
            }
        }
    }
    return Part::Feature::mustExecute();
}

App::DocumentObjectExecReturn* DesignBodyState::execute()
{
    auto* operationObject = Operation.getValue();
    if (!operationObject) {
        if (PreviousState.getValue()) {
            return outputError("An initial Body state cannot reference a previous state");
        }
        if (BodyId.getValueStr().empty() || !getDocument()
            || !bodyWithIdentity(*getDocument(), BodyId.getValueStr())) {
            return outputError("An initial Body state has no persistent target Body");
        }
        Present.setValue(true);
        const auto& initialShape = Shape.getShape();
        if (initialShape.isNull() || !initialShape.hasSubShape(TopAbs_SOLID)
            || initialShape.countSubShapes(TopAbs_SOLID) != 1) {
            return outputError("An initial Body state must contain exactly one solid");
        }
        return App::DocumentObject::StdReturn;
    }

    auto* operation = dynamic_cast<DesignOperation*>(operationObject);
    if (!operation || operationObject->getDocument() != getDocument()) {
        return outputError("This Body state lost its Design-global operation");
    }

    try {
        ensureDesignOperationPortSchema(*operationObject);
    }
    catch (const Base::Exception& error) {
        return outputError(error.what());
    }

    const int index = OutputIndex.getValue();
    const auto& outputs = operation->designOutputShapes().getValues();
    const auto& outputBodyIds = operation->designOutputBodyIds().getValues();
    const auto& outputPresence = operation->designOutputPresence().getValues();
    const auto& previousInputIndices = operation->designOutputPreviousInputIndices().getValues();
    if (outputs.size() != outputBodyIds.size() || outputPresence.size() != outputBodyIds.size()
        || previousInputIndices.size() != outputBodyIds.size() || index < 0
        || static_cast<std::size_t>(index) >= outputBodyIds.size()) {
        return outputError("This Body state has no matching output from its Design operation");
    }
    const auto outputIndex = static_cast<std::size_t>(index);
    if (outputBodyIds[outputIndex] != BodyId.getValueStr()) {
        return outputError("This Body state no longer matches its operation output identity");
    }

    if (!getDocument() || !bodyWithIdentity(*getDocument(), BodyId.getValueStr())) {
        return outputError("This Body state lost its persistent target Body identity");
    }

    auto* operationProperties = dynamic_cast<DesignOperationProperties*>(operation);
    if (!operationProperties) {
        return outputError("This Body state operation has no persistent input contract");
    }
    if (OperationId.getValueStr() != operationProperties->OperationId.getValueStr()) {
        return outputError("This Body state no longer matches its operation identity");
    }
    const auto& inputs = operationProperties->InputStates.getValues();
    const auto& inputBodyIds = operationProperties->InputBodyIds.getValues();
    if (inputs.size() != inputBodyIds.size()) {
        return outputError("This Body state's operation has inconsistent input ports");
    }

    const long previousInputIndex = previousInputIndices[outputIndex];
    if (previousInputIndex == -1) {
        if (PreviousState.getValue()) {
            return outputError("An operation-created Body state cannot reference a prior state");
        }
    }
    else if (previousInputIndex < 0 || static_cast<std::size_t>(previousInputIndex) >= inputs.size()
             || inputBodyIds[static_cast<std::size_t>(previousInputIndex)] != BodyId.getValueStr()
             || PreviousState.getValue() != inputs[static_cast<std::size_t>(previousInputIndex)]) {
        return outputError(
            "This Body state no longer references its output port's exact prior state"
        );
    }

    const bool present = outputPresence[outputIndex];
    const auto& output = outputs[outputIndex];
    if (present
        && (output.isNull() || !output.hasSubShape(TopAbs_SOLID)
            || output.countSubShapes(TopAbs_SOLID) != 1)) {
        return outputError("A present Body state must contain exactly one solid");
    }
    if (!present && !output.isNull()) {
        return outputError("An absent Body state cannot contain rendered geometry");
    }

    Present.setValue(present);
    Shape.setValue(output);
    return App::DocumentObject::StdReturn;
}

DesignBodyPublication::DesignBodyPublication()
{
    ADD_PROPERTY_TYPE(
        CurrentState,
        (nullptr),
        "VibeCAD Design",
        App::Prop_None,
        "Newest Design-owned state published by this Body"
    );
    CurrentState.setScope(App::LinkScope::Global);

    Base::Uuid designId;
    ADD_PROPERTY_TYPE(
        DesignId,
        (designId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the Design which owns this Body publication"
    );
    Base::Uuid bodyId;
    ADD_PROPERTY_TYPE(
        BodyId,
        (bodyId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of the Body rendered by this publication"
    );
    Base::Uuid bodyStateId;
    bodyStateId.setValue(NoDesignBodyStateId);
    ADD_PROPERTY_TYPE(
        BodyStateId,
        (bodyStateId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_Output | App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the newest Body state published here"
    );
}

void DesignBodyPublication::setupObject()
{
    Feature::setupObject();
    bindDesignIdentity(*this, DesignId);
}

short DesignBodyPublication::mustExecute() const
{
    if (CurrentState.isTouched() || BodyId.isTouched()) {
        return 1;
    }
    return Feature::mustExecute();
}

App::DocumentObjectExecReturn* DesignBodyPublication::execute()
{
    auto* body = getFeatureBody();
    if (!body || body->VibeCADBodyId.getValueStr() != BodyId.getValueStr()) {
        return outputError("This publication is not contained by its persistent target Body");
    }

    auto* currentFeature = freecad_cast<Part::Feature*>(CurrentState.getValue());
    if (!currentFeature || currentFeature->getDocument() != getDocument()) {
        return outputError("This Body publication has no live current state");
    }
    if (auto* currentState = freecad_cast<DesignBodyState*>(currentFeature)) {
        BodyStateId.setValue(currentState->BodyStateId.getValue());
    }
    else {
        // A retained legacy/imported exact state has no DesignBodyState UUID.
        // Clear the publication mirror when an edited multi-output operation
        // stops advancing this Body and the tip is rerouted to that state.
        BodyStateId.setValue(NoDesignBodyStateId);
    }

    auto* state = freecad_cast<DesignBodyState*>(currentFeature);
    std::unordered_set<DesignBodyState*> visited;
    while (state) {
        if (state->getDocument() != getDocument()
            || state->BodyId.getValueStr() != BodyId.getValueStr() || !visited.insert(state).second) {
            return outputError("This Body publication has an invalid state chain");
        }

        auto* operation = state->Operation.getValue();
        if (!operation || App::DocumentTimeline::isObjectUsableAtCurrentPosition(operation)) {
            break;
        }
        state = freecad_cast<DesignBodyState*>(state->PreviousState.getValue());
    }

    if (state) {
        currentFeature = state;
    }
    else if (auto* designCurrent = freecad_cast<DesignBodyState*>(CurrentState.getValue())) {
        currentFeature = freecad_cast<Part::Feature*>(designCurrent->PreviousState.getValue());
        while (auto* previousDesign = freecad_cast<DesignBodyState*>(currentFeature)) {
            auto* producer = previousDesign->Operation.getValue();
            if (!producer || App::DocumentTimeline::isObjectUsableAtCurrentPosition(producer)) {
                break;
            }
            currentFeature = freecad_cast<Part::Feature*>(previousDesign->PreviousState.getValue());
        }
    }
    if (!currentFeature) {
        Shape.setValue(Part::TopoShape());
        return App::DocumentObject::StdReturn;
    }
    if (const auto* currentState = freecad_cast<const DesignBodyState*>(currentFeature);
        currentState && !currentState->Present.getValue()) {
        Shape.setValue(Part::TopoShape());
        return App::DocumentObject::StdReturn;
    }
    if (currentFeature->Shape.getShape().isNull()) {
        return outputError("This Body publication's active state has no valid shape");
    }

    Shape.setValue(shapeInBodyStateCoordinates(*currentFeature));
    return App::DocumentObject::StdReturn;
}

DesignBodyPublication* PartDesign::findDesignBodyPublication(Body* body) noexcept
{
    if (!body) {
        return nullptr;
    }

    DesignBodyPublication* publication = nullptr;
    for (auto* member : body->Group.getValues()) {
        auto* candidate = freecad_cast<DesignBodyPublication*>(member);
        if (!candidate) {
            continue;
        }
        if (publication) {
            return nullptr;
        }
        publication = candidate;
    }
    return publication;
}

const DesignBodyPublication* PartDesign::findDesignBodyPublication(const Body* body) noexcept
{
    return findDesignBodyPublication(const_cast<Body*>(body));
}

Part::Feature* PartDesign::designBodyStateBefore(Body* body, const App::DocumentObject* operation) noexcept
{
    try {
        if (!body || !body->getDocument()) {
            return nullptr;
        }

        Part::Feature* state = nullptr;
        if (auto* publication = findDesignBodyPublication(body)) {
            state = freecad_cast<Part::Feature*>(publication->CurrentState.getValue());
        }
        else {
            state = freecad_cast<Part::Feature*>(body->Tip.getValue());
        }
        if (!operation) {
            std::unordered_set<const App::DocumentObject*> activeVisited;
            while (auto* designState = freecad_cast<DesignBodyState*>(state)) {
                if (!activeVisited.insert(designState).second) {
                    return nullptr;
                }
                auto* producer = designState->Operation.getValue();
                if (!producer || App::DocumentTimeline::isObjectUsableAtCurrentPosition(producer)) {
                    return designState->Present.getValue() ? designState : nullptr;
                }
                state = freecad_cast<Part::Feature*>(designState->PreviousState.getValue());
            }
            return state;
        }
        if (operation->getDocument() != body->getDocument()) {
            return nullptr;
        }

        const auto* timeline = App::DocumentTimeline::get(body->getDocument());
        const auto& history = timeline ? timeline->Operations.getValues()
                                       : std::vector<App::DocumentObject*> {};
        const auto operationPosition = std::find(history.begin(), history.end(), operation);

        std::unordered_set<const App::DocumentObject*> visited;
        while (state && visited.insert(state).second) {
            if (auto* designState = freecad_cast<DesignBodyState*>(state)) {
                auto* producer = designState->Operation.getValue();
                if (producer == operation) {
                    auto* previous = freecad_cast<Part::Feature*>(
                        designState->PreviousState.getValue()
                    );
                    const auto* previousDesign = freecad_cast<const DesignBodyState*>(previous);
                    return previousDesign && !previousDesign->Present.getValue() ? nullptr : previous;
                }
                if (operationPosition != history.end() && producer) {
                    const auto producerPosition = std::find(history.begin(), history.end(), producer);
                    if (producerPosition != history.end() && producerPosition >= operationPosition) {
                        state = freecad_cast<Part::Feature*>(designState->PreviousState.getValue());
                        continue;
                    }
                }
                return designState->Present.getValue() ? designState : nullptr;
            }

            if (operationPosition != history.end()) {
                const auto statePosition = std::find(history.begin(), history.end(), state);
                if (statePosition != history.end() && statePosition >= operationPosition) {
                    state = freecad_cast<Part::Feature*>(body->getPrevResultFeature(state));
                    continue;
                }
            }
            return state;
        }
    }
    catch (...) {
    }
    return nullptr;
}

const Part::Feature* PartDesign::designBodyStateBefore(
    const Body* body,
    const App::DocumentObject* operation
) noexcept
{
    return designBodyStateBefore(const_cast<Body*>(body), operation);
}

std::vector<DesignBodyState*> PartDesign::designBodyStatesForOperation(App::DocumentObject* operation)
{
    std::vector<DesignBodyState*> states;
    if (!operation || !operation->getDocument()) {
        return states;
    }
    for (auto* state : operation->getDocument()->getObjectsOfType<DesignBodyState>()) {
        if (state && state->Operation.getValue() == operation) {
            states.push_back(state);
        }
    }
    std::ranges::sort(states, [](const auto* left, const auto* right) {
        if (left->OutputIndex.getValue() != right->OutputIndex.getValue()) {
            return left->OutputIndex.getValue() < right->OutputIndex.getValue();
        }
        return left->getID() < right->getID();
    });
    return states;
}
