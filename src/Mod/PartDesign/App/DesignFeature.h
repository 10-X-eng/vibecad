// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string_view>
#include <vector>

#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/PropertyUnits.h>
#include <App/SuppressibleExtension.h>
#include <Mod/Part/App/PropertyTopoShapeList.h>
#include <Mod/Part/App/PartFeature.h>

#include "Feature.h"
#include "FeatureChamfer.h"
#include "FeatureDraft.h"
#include "FeatureFillet.h"
#include "FeatureHelix.h"
#include "FeatureHole.h"
#include "FeatureLoft.h"
#include "FeaturePad.h"
#include "FeaturePipe.h"
#include "FeaturePrimitive.h"
#include "FeatureRevolution.h"
#include "FeatureThickness.h"

namespace PartDesign
{

class Body;

/** UUID sentinel used when a publication points to a legacy non-state tip. */
inline constexpr const char* NoDesignBodyStateId = "00000000-0000-4000-8000-000000000000";

class PartDesignExport DesignOperation
{
public:
    virtual ~DesignOperation() = default;

    virtual const Part::PropertyTopoShapeList& designOutputShapes() const = 0;
    virtual const App::PropertyStringList& designOutputBodyIds() const = 0;
    virtual const App::PropertyBoolList& designOutputPresence() const = 0;
    virtual const App::PropertyIntegerList& designOutputPreviousInputIndices() const = 0;

    /** Compatibility view of the output Body identities. */
    virtual const App::PropertyStringList& designTargetBodyIds() const = 0;
};

/**
 * Shared persistent contract implemented by every Design-global operation.
 *
 * This mixin is deliberately not an App::DocumentObject base. Concrete
 * operation types retain their native Pad/Revolution parameter and geometry
 * implementations while exposing one uniform target/output contract to Body
 * states, the GUI, History, and VibeScript.
 */
class PartDesignExport DesignOperationProperties: public DesignOperation
{
public:
    App::PropertyUUID OperationId;
    App::PropertyUUID DesignId;
    App::PropertyInteger DesignSchemaVersion;
    App::PropertyEnumeration ResultOperation;
    App::PropertyLinkList InputStates;
    App::PropertyStringList InputBodyIds;
    App::PropertyPlacementList InputFrames;
    App::PropertyStringList OutputBodyIds;
    App::PropertyPlacementList OutputFrames;
    App::PropertyIntegerList OutputPreviousInputIndices;
    App::PropertyBoolList OutputPresence;
    App::PropertyStringList OutputComponentIds;

    /** Compatibility mirrors retained for existing callers and saved files. */
    App::PropertyStringList TargetBodyIds;
    App::PropertyPlacementList TargetFrames;
    App::PropertyString DestinationComponentId;

    Part::PropertyTopoShapeList OutputShapes;

    const Part::PropertyTopoShapeList& designOutputShapes() const override
    {
        return OutputShapes;
    }
    const App::PropertyStringList& designOutputBodyIds() const override
    {
        return OutputBodyIds;
    }
    const App::PropertyBoolList& designOutputPresence() const override
    {
        return OutputPresence;
    }
    const App::PropertyIntegerList& designOutputPreviousInputIndices() const override
    {
        return OutputPreviousInputIndices;
    }
    const App::PropertyStringList& designTargetBodyIds() const override
    {
        return TargetBodyIds;
    }

    bool designPortsTouched() const
    {
        return InputStates.isTouched() || InputBodyIds.isTouched() || InputFrames.isTouched()
            || OutputBodyIds.isTouched() || OutputFrames.isTouched()
            || OutputPreviousInputIndices.isTouched() || OutputComponentIds.isTouched()
            || TargetBodyIds.isTouched() || TargetFrames.isTouched()
            || DestinationComponentId.isTouched();
    }

    static const char* ResultOperationEnums[];

    /**
     * Return whether this operation accepts the requested result semantic.
     *
     * Profile-generating operations accept New Body, Join, Cut, and Intersect
     * by default. Operations with narrower engineering semantics override this
     * contract so neither the GUI nor an API caller can reinterpret them.
     */
    virtual bool supportsDesignResultOperation(std::string_view resultOperation) const;
};

/**
 * Upgrade a restored schema-1 pointwise operation to independent input and
 * output ports. New operations already use the current schema.
 */
PartDesignExport void ensureDesignOperationPortSchema(App::DocumentObject& operation);

/**
 * Persistent subelement selections for an operation which edits existing
 * Bodies in place.
 *
 * TargetElements is a flattened list and TargetElementOffsets partitions it
 * into one ordered group per TargetBodyIds entry. The representation never
 * links to a Body, publication, or mutable tip object.
 */
class PartDesignExport DesignSubelementOperationProperties
{
public:
    virtual ~DesignSubelementOperationProperties() = default;

    App::PropertyIntegerList TargetElementOffsets;
    App::PropertyStringList TargetElements;

    std::vector<std::vector<std::string>> targetElementGroups() const;
    void setTargetElementGroups(const std::vector<std::vector<std::string>>& groups);
};

/**
 * A Design-global extrusion tool with explicit multi-Body inputs and outputs.
 *
 * The object is the one user-visible History operation. It is deliberately
 * outside every Body. InputStates points only to exact prior result features;
 * TargetBodyIds is the parallel stable identity list. Per-Body
 * DesignBodyState resources publish OutputShapes through one stable
 * DesignBodyPublication in each Body.
 */
class PartDesignExport DesignExtrude: public Pad, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignExtrude);

public:
    DesignExtrude();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderPad";
    }

protected:
    TopoShape getExtrusionContextBaseShape() const override;
};

/**
 * Design-global revolution with the same explicit Body-result contract as
 * DesignExtrude.
 */
class PartDesignExport DesignRevolve: public Revolution, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignRevolve);

public:
    DesignRevolve();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderRevolution";
    }

protected:
    TopoShape getRevolutionContextBaseShape() const override;
};

/** Design-global loft with explicit result Bodies. */
class PartDesignExport DesignLoft: public AdditiveLoft, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignLoft);

public:
    DesignLoft();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderLoft";
    }
};

/** Design-global sweep with explicit result Bodies. */
class PartDesignExport DesignSweep: public AdditivePipe, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignSweep);

public:
    DesignSweep();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderPipe";
    }
};

/** Design-global helical sweep with explicit result Bodies. */
class PartDesignExport DesignHelix: public AdditiveHelix, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignHelix);

public:
    DesignHelix();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderHelix";
    }
};

/** Parametric primitive operations using the same explicit Body-result ports. */
class PartDesignExport DesignBox: public Box, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignBox);

public:
    DesignBox();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignCylinder: public Cylinder, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignCylinder);

public:
    DesignCylinder();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignSphere: public Sphere, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignSphere);

public:
    DesignSphere();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignCone: public Cone, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignCone);

public:
    DesignCone();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignEllipsoid: public Ellipsoid, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignEllipsoid);

public:
    DesignEllipsoid();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignTorus: public Torus, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignTorus);

public:
    DesignTorus();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignPrism: public Prism, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignPrism);

public:
    DesignPrism();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignWedge: public Wedge, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignWedge);

public:
    DesignWedge();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

class PartDesignExport DesignTube: public Tube, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignTube);

public:
    DesignTube();
    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

/**
 * Copy one exact Body state into one independently identified Body.
 *
 * Clone is a global History operation. Its source is the immutable state in
 * InputStates; it never links to a Body container, mutable Tip, publication,
 * or Body-owned BaseFeature chain.
 */
class PartDesignExport DesignClone: public Feature, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignClone);

public:
    DesignClone();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Scale one or more exact Body states around one Design-space center.
 *
 * Scale is an in-place global History operation. It advances every explicitly
 * selected Body identity atomically and never creates a duplicate Part::Scale
 * object, hides an input presentation, or links to a mutable Body container.
 */
class PartDesignExport DesignScale: public Feature, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignScale);

public:
    DesignScale();

    App::PropertyBool Uniform;
    App::PropertyFloat UniformScale;
    App::PropertyFloat XScale;
    App::PropertyFloat YScale;
    App::PropertyFloat ZScale;
    App::PropertyVector Center;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Shared source contract for Design-global mirrors and patterns.
 *
 * Feature mode repeats the unsigned tool geometry of one earlier Design
 * operation, preserving that operation's additive or subtractive semantic.
 * Body mode copies one exact prior Body state into independently identified
 * output Bodies. Neither mode stores a Body container, publication, mutable
 * Tip, or legacy Body-owned feature list.
 */
class PartDesignExport DesignPatternProperties
{
public:
    virtual ~DesignPatternProperties() = default;

    App::PropertyEnumeration PatternSource;
    App::PropertyLink SourceOperation;

    static const char* PatternSourceEnums[];

    virtual Part::TopoShape refineDesignPatternShape(const Part::TopoShape& shape) const = 0;

    bool patternPropertiesTouched() const
    {
        return PatternSource.isTouched() || SourceOperation.isTouched();
    }
};

/** Mirror an earlier feature contribution or exact Body state. */
class PartDesignExport DesignMirror: public FeatureRefine,
                                     public DesignOperationProperties,
                                     public DesignPatternProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignMirror);

public:
    DesignMirror();

    App::PropertyLinkSub PlaneReference;
    App::PropertyPlacement PlaneReferenceFrame;
    App::PropertyVector PlaneOrigin;
    App::PropertyVector PlaneNormal;
    App::PropertyInteger GeneratedOccurrenceCount;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;
    Part::TopoShape refineDesignPatternShape(const Part::TopoShape& shape) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/** Repeat an earlier feature contribution or exact Body state along a line. */
class PartDesignExport DesignLinearPattern: public FeatureRefine,
                                            public DesignOperationProperties,
                                            public DesignPatternProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignLinearPattern);

public:
    DesignLinearPattern();

    App::PropertyLinkSub DirectionReference;
    App::PropertyPlacement DirectionReferenceFrame;
    App::PropertyVector Direction;
    App::PropertyLength Spacing;
    App::PropertyInteger Occurrences;
    App::PropertyBool Centered;
    App::PropertyInteger GeneratedOccurrenceCount;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;
    Part::TopoShape refineDesignPatternShape(const Part::TopoShape& shape) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/** Repeat an earlier feature contribution or exact Body state around an axis. */
class PartDesignExport DesignCircularPattern: public FeatureRefine,
                                              public DesignOperationProperties,
                                              public DesignPatternProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignCircularPattern);

public:
    DesignCircularPattern();

    App::PropertyLinkSub AxisReference;
    App::PropertyPlacement AxisReferenceFrame;
    App::PropertyVector AxisOrigin;
    App::PropertyVector AxisDirection;
    App::PropertyAngle Angle;
    App::PropertyInteger Occurrences;
    App::PropertyBool Reversed;
    App::PropertyInteger GeneratedOccurrenceCount;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;
    Part::TopoShape refineDesignPatternShape(const Part::TopoShape& shape) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Design-global hole operation.
 *
 * One reusable point/circle sketch defines every hole location. The mature
 * native Hole geometry engine builds the cutters once; the Design operation
 * then applies them atomically to every explicit Body input. Hole is always a
 * Cut and never assigns a temporary BaseFeature or active Body.
 */
class PartDesignExport DesignHole: public Hole, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignHole);

public:
    DesignHole();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

protected:
    double getHoleThroughAllLength() const override;
};

/**
 * Design-global fillet. The inherited legacy Base link remains empty and
 * hidden; exact input states and selected edges are represented by the
 * Design operation contracts instead.
 */
class PartDesignExport DesignFillet: public Fillet,
                                     public DesignOperationProperties,
                                     public DesignSubelementOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignFillet);

public:
    DesignFillet();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

/**
 * Design-global chamfer with the same explicit multi-Body subelement contract
 * as DesignFillet.
 */
class PartDesignExport DesignChamfer: public Chamfer,
                                      public DesignOperationProperties,
                                      public DesignSubelementOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignChamfer);

public:
    DesignChamfer();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

/**
 * Design-global shell/thickness operation.
 *
 * Selected opening faces are stored per exact Body input. The inherited Base
 * link remains empty so the operation cannot depend on a Body container,
 * publication, or mutable Tip.
 */
class PartDesignExport DesignThickness: public Thickness,
                                        public DesignOperationProperties,
                                        public DesignSubelementOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignThickness);

public:
    DesignThickness();

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

/**
 * Design-global draft operation.
 *
 * Target faces are grouped by exact Body input. Optional neutral-plane and
 * pull-direction links resolve to exact definition objects; their containing
 * frames are captured when selected so moving a Component occurrence cannot
 * silently redefine an accepted part feature.
 */
class PartDesignExport DesignDraft: public Draft,
                                    public DesignOperationProperties,
                                    public DesignSubelementOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignDraft);

public:
    DesignDraft();

    App::PropertyPlacement NeutralPlaneFrame;
    App::PropertyPlacement PullDirectionFrame;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;
};

/**
 * Design-global solid combination with one explicit result Body.
 *
 * InputStates contains the result Body first, followed by the tool Bodies.
 * When KeepTools is true, only the result Body receives a new state and the
 * tools remain read-only inputs. When it is false, every tool receives an
 * absent output state so History, suppression, undo, and reopen can restore
 * its exact persistent Body identity.
 */
class PartDesignExport DesignCombine: public FeatureRefine, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignCombine);

public:
    DesignCombine();

    /** Persistent identity of the Body which receives the Boolean result. */
    App::PropertyUUID ResultBodyId;

    /** Preserve tool Bodies instead of consuming them at this History step. */
    App::PropertyBool KeepTools;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Design-global Body split with explicit, persistent output-region identity.
 *
 * The first input is the source Body. Additional inputs are exact Body states
 * used as splitting tools; standalone faces and surfaces are stored in
 * Splitters with immutable containing-frame snapshots. RegionWitnesses is
 * parallel to the outputs. Each point must classify strictly inside exactly
 * one regenerated solid, so output Body identity is never inferred from
 * kernel order, volume, centroid, or bounding-box similarity.
 */
class PartDesignExport DesignSplit: public FeatureRefine, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignSplit);

public:
    DesignSplit();

    /** Persistent identity of the Body being split. */
    App::PropertyUUID SourceBodyId;

    /** Exact face, surface, shell, or solid definitions used to split. */
    App::PropertyLinkSubList Splitters;

    /** Saved containing frame for each Splitters entry. */
    App::PropertyPlacementList SplitterFrames;

    /** One strict interior point per output Body, in source-Body coordinates. */
    App::PropertyVectorList RegionWitnesses;

    /** False until the user explicitly chooses which region keeps SourceBodyId. */
    App::PropertyBool RetainedRegionChosen;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Design-global separation of one reusable multi-solid definition.
 *
 * Source is an earlier Design-root definition, never a Body, publication, or
 * assembly occurrence. Every source solid receives one newly created Body.
 * RegionWitnesses provide persistent geometric identity without relying on
 * OpenCascade traversal order, volume, centroid sorting, or labels.
 */
class PartDesignExport DesignSeparate: public FeatureRefine, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignSeparate);

public:
    DesignSeparate();

    /** Exact earlier reusable definition containing two or more solids. */
    App::PropertyLink Source;

    /** One strict interior Design-space point per output Body. */
    App::PropertyVectorList RegionWitnesses;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * One accepted VibeScript program in Design-global History.
 *
 * VibeScript source is the parametric definition. AcceptedShapes is the
 * isolated worker's validated result snapshot; ScriptOutputKeys is the stable
 * semantic identity used to retain each output Body across source edits.
 * The operation owns no Body and never reconstructs a hidden native
 * Body/Tip/BaseFeature chain.
 */
class PartDesignExport DesignScriptOperation: public Feature, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignScriptOperation);

public:
    DesignScriptOperation();

    /** Stable VibeScript program identity and accepted revision. */
    App::PropertyString ProgramId;
    App::PropertyString ProgramRevision;

    /** Portable program metadata object name; deliberately not a graph link. */
    App::PropertyString ProgramObjectName;

    /** Every source-level program output, including non-Body publications. */
    App::PropertyStringList ProgramOutputKeys;
    App::PropertyStringList ProgramOutputTypes;

    /** Stable source-level output identities and human-facing labels. */
    App::PropertyStringList ScriptOutputKeys;
    App::PropertyStringList ScriptOutputLabels;

    /** Isolated and validated output geometry, parallel to ScriptOutputKeys. */
    Part::PropertyTopoShapeList AcceptedShapes;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void onDocumentRestored() override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignScriptOperation";
    }
};

/**
 * One Design-global operation backed by a native parametric generator.
 *
 * Generator is a one-way dependency on a hidden Design-internal Part feature.
 * The generator never links back to this operation, so the saved dependency
 * graph remains acyclic.  The operation publishes the generator's current
 * solid through the same stable Body-state contract as every other Design
 * operation.
 */
class PartDesignExport DesignGeneratedOperation: public Feature, public DesignOperationProperties
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignGeneratedOperation);

public:
    DesignGeneratedOperation();

    /** Native generator implementation and its durable semantic kind. */
    App::PropertyLink Generator;
    App::PropertyString GeneratorKind;

    /** Human-facing label applied to the one stable output Body. */
    App::PropertyString OutputLabel;

    bool supportsDesignResultOperation(std::string_view resultOperation) const override;

    App::DocumentObjectExecReturn* recompute() override;
    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void onChanged(const App::Property* property) override;
    void setupObject() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProviderDesignOperation";
    }
};

/**
 * Compute strict interior region witnesses for the currently configured Split
 * definition. The returned points are ordered geometrically only for the
 * initial task-panel presentation; no Body identity is assigned by this
 * function.
 */
PartDesignExport std::vector<Base::Vector3d> discoverDesignSplitRegionWitnesses(const DesignSplit& split
);

/**
 * Compute strict interior Design-space witnesses for a Separate operation's
 * current source solids. The returned order is only an initial presentation
 * order; accepted identity is retained exclusively by saved witness points.
 */
PartDesignExport std::vector<Base::Vector3d> discoverDesignSeparateRegionWitnesses(
    const DesignSeparate& separate
);

/**
 * One exact current Separate result and the saved result identity it retains.
 *
 * previousWitnessIndex is -1 only when the current source contains a genuinely
 * new solid. Existing identities are matched solely by strict containment of
 * their saved interior witness; labels, order, mass properties, and proximity
 * never participate.
 */
struct PartDesignExport DesignSeparateRegionAssignment
{
    Base::Vector3d witness;
    long previousWitnessIndex {-1};
};

/**
 * Reconcile a Separate operation's current source solids against its accepted
 * result witnesses.
 *
 * Preserved results are returned in their previous output order. New results
 * follow in stable geometric order. Missing previous results are deliberately
 * absent from the return value so the central operation-resource reconciler
 * can retire them with its normal downstream-consumer checks. Ambiguous
 * containment is rejected rather than guessed.
 */
PartDesignExport std::vector<DesignSeparateRegionAssignment> reconcileDesignSeparateRegions(
    const DesignSeparate& separate,
    const std::vector<Base::Vector3d>& previousWitnesses
);

/**
 * Resolve one target's selected edges against its exact Body-state shape.
 * Faces and wires expand to their boundary edges. Invalid or duplicate
 * references are rejected before a geometry algorithm runs.
 */
PartDesignExport std::vector<Part::TopoShape> resolveDesignTargetEdges(
    const Part::TopoShape& shape,
    const std::vector<std::string>& references,
    bool useAllEdges
);

/**
 * Resolve selected faces against one exact Body state.
 *
 * Invalid, non-face, non-solid-owned, or duplicate references are rejected
 * before a geometry algorithm runs.
 */
PartDesignExport std::vector<Part::TopoShape> resolveDesignTargetFaces(
    const Part::TopoShape& shape,
    const std::vector<std::string>& references
);

/**
 * Design-owned immutable result state for one Body.
 *
 * States live at the Design root, never inside a Body or Component. Operation
 * selects the global producer output and PreviousState records the exact
 * preceding state. This is the durable Body-state DAG.
 */
class PartDesignExport DesignBodyState: public Part::Feature, public App::SuppressibleExtension
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignBodyState);

public:
    DesignBodyState();

    App::PropertyLink Operation;
    App::PropertyInteger OutputIndex;
    App::PropertyUUID DesignId;
    App::PropertyUUID OperationId;
    App::PropertyUUID BodyId;
    App::PropertyUUID BodyStateId;
    App::PropertyLink PreviousState;
    App::PropertyBool Present;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void setupObject() override;
    void onDocumentRestored() override;

    const char* getViewProviderName() const override
    {
        return "Gui::ViewProviderDocumentObject";
    }

private:
    App::DocumentObject* restoredOperation = nullptr;
    int restoredOutputIndex = -1;
    bool restoredPresent = false;
    bool preserveRestoredShape = false;
};

/**
 * The one stable rendered result inside a physical Body.
 *
 * CurrentState points to the newest Design-owned state. The publication
 * resolves the state active at the current History position and transforms
 * Design coordinates into its Body/Component local frame. It is presentation,
 * not a modeling input.
 */
class PartDesignExport DesignBodyPublication: public Feature
{
    PROPERTY_HEADER_WITH_OVERRIDE(PartDesign::DesignBodyPublication);

public:
    DesignBodyPublication();

    App::PropertyLink CurrentState;
    App::PropertyUUID DesignId;
    App::PropertyUUID BodyId;
    App::PropertyUUID BodyStateId;

    App::DocumentObjectExecReturn* execute() override;
    short mustExecute() const override;
    void setupObject() override;
    void onDocumentRestored() override;

    const char* getViewProviderName() const override
    {
        return "PartDesignGui::ViewProvider";
    }

private:
    Part::Feature* restoredPublishedFeature = nullptr;
    bool preserveRestoredShape = false;
};

/**
 * Resolve the one stable publication owned by a Body.
 *
 * A null result means the Body is legacy/empty or malformed. More than one
 * publication is deliberately treated as malformed rather than choosing one
 * from tree order.
 */
PartDesignExport DesignBodyPublication* findDesignBodyPublication(Body* body) noexcept;
PartDesignExport const DesignBodyPublication* findDesignBodyPublication(const Body* body) noexcept;

/**
 * Return the exact Body-local state active immediately before operation.
 *
 * Passing nullptr returns the current published state. The walk follows only
 * explicit PreviousState links and persisted History order; visibility, tree
 * order, labels, and active-Body state are never consulted.
 */
PartDesignExport Part::Feature* designBodyStateBefore(
    Body* body,
    const App::DocumentObject* operation
) noexcept;
PartDesignExport const Part::Feature* designBodyStateBefore(
    const Body* body,
    const App::DocumentObject* operation
) noexcept;

/** Return every Design-owned state produced by operation, ordered by output index. */
PartDesignExport std::vector<DesignBodyState*> designBodyStatesForOperation(
    App::DocumentObject* operation
);

}  // namespace PartDesign
