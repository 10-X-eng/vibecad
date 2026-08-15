# SPDX-License-Identifier: LGPL-2.1-or-later

"""Headless Lead In/Out path generation shared by CAM GUI and Native mode."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from types import SimpleNamespace

import Constants
import FreeCAD as App
import Part
import Path

from Path.Base import Language as PathLanguage
from Path.Base.MachineState import MachineState
from Path.Base.Util import activeForOp, toolControllerForOp
from Path.Dressup import Utils as PathDressup
from PathScripts import PathUtils
from PySide.QtCore import QT_TRANSLATE_NOOP


translate = App.Qt.translate

HIDE_MODES = {
    "Angle": ("No Retract", "Perpendicular", "Tangent", "Vertical"),
    "Invert": (
        "No Retract",
        "ArcZ",
        "ArcZFollow",
        "LineZ",
        "LineZFollow",
        "Vertical",
        "Perpendicular",
        "Tangent",
    ),
    "Offset": ("No Retract",),
    "Radius": ("No Retract", "Vertical"),
}

EXTEND_STYLES = frozenset(("Arc", "Line", "Perpendicular", "Tangent"))
NON_ZERO_ANGLE_STYLES = frozenset(
    ("Arc", "Arc3d", "ArcZ", "ArcZFollow", "Helix", "LineZ", "LineZFollow")
)
ANGLE_STYLES = frozenset(
    (
        "Arc",
        "Line",
        "Arc3d",
        "ArcZ",
        "ArcZFollow",
        "Helix",
        "Line3d",
        "LineZ",
        "LineZFollow",
    )
)
RADIUS_STYLES = frozenset(
    (
        "Arc",
        "Line",
        "Perpendicular",
        "Tangent",
        "Arc3d",
        "ArcZ",
        "ArcZFollow",
        "Helix",
        "Line3d",
        "LineZ",
        "LineZFollow",
    )
)
INVERT_STYLES = frozenset(("Arc", "Line", "Arc3d", "Helix", "Line3d"))
OFFSET_STYLES = frozenset(
    (
        "Arc",
        "Line",
        "Perpendicular",
        "Tangent",
        "Arc3d",
        "ArcZ",
        "ArcZFollow",
        "Helix",
        "Line3d",
        "LineZ",
        "LineZFollow",
        "Vertical",
    )
)
MAX_LEAD_INPUT_COMMANDS = 50_000
MAX_LEAD_OUTPUT_COMMANDS = 500_000
MAX_LEAD_DISTANCE_MM = 1_000_000.0


@dataclass(frozen=True, slots=True)
class LeadDefinition:
    """One exact entry or exit motion; ``style=None`` disables that side."""

    style: str | None
    angle_degrees: float = 0.0
    radius_or_length_mm: float = 0.0
    invert: bool = False
    offset_mm: float = 0.0
    extend_mm: float = 0.0


def _path_with_job_center(owner, path=None):
    if isinstance(path, Path.Path):
        result = Path.Path(list(path.Commands or ()))
        result.Center = path.Center
    else:
        result = Path.Path(path) if path else Path.Path()
    job = None
    for candidate in (owner, getattr(owner, "Base", None)):
        if candidate is None:
            continue
        try:
            job = PathUtils.findParentJob(candidate)
        except (AttributeError, TypeError):
            job = None
        if job is not None:
            break
    if job is not None:
        result.Center = job.Path.Center
    return result


def _finite(value, noun):
    result = float(getattr(value, "Value", value))
    if not math.isfinite(result):
        raise ValueError(f"{noun} must be finite")
    return result


def normalize_lead_definition(definition, noun, *, clamp=False):
    """Return the canonical values consumed by the shipped generator."""

    if not isinstance(definition, LeadDefinition):
        raise TypeError(f"{noun} must be a LeadDefinition")
    if definition.style is None:
        return LeadDefinition(None)
    style = str(definition.style)
    if style not in lead_styles:
        raise ValueError(f"{noun} style is not supported")

    angle = _finite(definition.angle_degrees, f"{noun} angle")
    radius = _finite(definition.radius_or_length_mm, f"{noun} radius or length")
    offset = _finite(definition.offset_mm, f"{noun} offset")
    extend = _finite(definition.extend_mm, f"{noun} extension")
    if clamp:
        radius = max(radius, 1.0)
        minimum_angle = 0.1 if style in NON_ZERO_ANGLE_STYLES else 0.0
        maximum_angle = 180.0
        if style in ("ArcZ", "ArcZFollow"):
            maximum_angle = 179.0
        elif style == "LineZFollow":
            maximum_angle = 89.0
        angle = min(max(angle, minimum_angle), maximum_angle)

    minimum_angle = 0.1 if style in NON_ZERO_ANGLE_STYLES else 0.0
    maximum_angle = 180.0
    if style in ("ArcZ", "ArcZFollow"):
        maximum_angle = 179.0
    elif style == "LineZFollow":
        maximum_angle = 89.0
    if style in ANGLE_STYLES and not minimum_angle <= angle <= maximum_angle:
        raise ValueError(
            f"{noun} angle must be between {minimum_angle:g} and {maximum_angle:g} degrees"
        )
    if style in RADIUS_STYLES and not 0.0 < radius <= MAX_LEAD_DISTANCE_MM:
        raise ValueError(
            f"{noun} radius or length must be greater than zero and at most "
            f"{MAX_LEAD_DISTANCE_MM:g} mm"
        )
    if (
        style in OFFSET_STYLES
        and not -MAX_LEAD_DISTANCE_MM <= offset <= MAX_LEAD_DISTANCE_MM
    ):
        raise ValueError(
            f"{noun} offset must be between {-MAX_LEAD_DISTANCE_MM:g} and "
            f"{MAX_LEAD_DISTANCE_MM:g} mm"
        )
    if style in EXTEND_STYLES and not 0.0 <= extend <= MAX_LEAD_DISTANCE_MM:
        raise ValueError(
            f"{noun} extension must be between zero and {MAX_LEAD_DISTANCE_MM:g} mm"
        )
    return LeadDefinition(
        style=style,
        angle_degrees=angle if style in ANGLE_STYLES else 0.0,
        radius_or_length_mm=radius if style in RADIUS_STYLES else 0.0,
        invert=bool(definition.invert) if style in INVERT_STYLES else False,
        offset_mm=offset if style in OFFSET_STYLES else 0.0,
        extend_mm=extend if style in EXTEND_STYLES else 0.0,
    )


def lead_definition_from_object(obj, suffix, *, clamp=True):
    enabled = bool(getattr(obj, f"Lead{suffix}"))
    if not enabled:
        return LeadDefinition(None)
    definition = LeadDefinition(
        style=str(getattr(obj, f"Style{suffix}")),
        angle_degrees=_finite(getattr(obj, f"Angle{suffix}"), f"Lead-{suffix} angle"),
        radius_or_length_mm=_finite(
            getattr(obj, f"Radius{suffix}"),
            f"Lead-{suffix} radius or length",
        ),
        invert=bool(getattr(obj, f"Invert{suffix}")),
        offset_mm=_finite(getattr(obj, f"Offset{suffix}"), f"Lead-{suffix} offset"),
        extend_mm=_finite(
            getattr(obj, f"ExtendLead{suffix}"), f"Lead-{suffix} extension"
        ),
    )
    return normalize_lead_definition(definition, f"Lead-{suffix}", clamp=clamp)


lead_styles = (
    # common options first
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Arc"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Line"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Perpendicular"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Tangent"),
    # additional options, alphabetical order
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Arc3d"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "ArcZ"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "ArcZFollow"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Helix"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Line3d"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "LineZ"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "LineZFollow"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "No Retract"),
    QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Vertical"),
)


class ObjectDressup:
    def __init__(self, obj, base):
        obj.addProperty(
            "App::PropertyLink",
            "Base",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The base toolpath to modify"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "LeadIn",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Modify lead in to toolpath"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "LeadOut",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Modify lead out from toolpath"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "RetractThreshold",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Set distance which will attempts to avoid unnecessary retractions",
            ),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "StyleIn",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "The style of motion into the toolpath"),
        )
        obj.StyleIn = lead_styles
        obj.addProperty(
            "App::PropertyEnumeration",
            "StyleOut",
            "Path",
            QT_TRANSLATE_NOOP(
                "App::Property", "The style of motion out of the toolpath"
            ),
        )
        obj.StyleOut = lead_styles
        obj.addProperty(
            "App::PropertyBool",
            "RapidPlunge",
            "Path",
            QT_TRANSLATE_NOOP("App::Property", "Perform plunges with G0"),
        )
        obj.addProperty(
            "App::PropertyAngle",
            "AngleIn",
            "Path Lead-in",
            QT_TRANSLATE_NOOP("App::Property", "Angle of the Lead-In (1..90)"),
        )
        obj.addProperty(
            "App::PropertyAngle",
            "AngleOut",
            "Path Lead-out",
            QT_TRANSLATE_NOOP("App::Property", "Angle of the Lead-Out (1..90)"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "RadiusIn",
            "Path Lead-in",
            QT_TRANSLATE_NOOP("App::Property", "Determine length of the Lead-In"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "RadiusOut",
            "Path Lead-out",
            QT_TRANSLATE_NOOP("App::Property", "Determine length of the Lead-Out"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "InvertIn",
            "Path Lead-in",
            QT_TRANSLATE_NOOP("App::Property", "Invert Lead-In direction"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "InvertOut",
            "Path Lead-out",
            QT_TRANSLATE_NOOP("App::Property", "Invert Lead-Out direction"),
        )
        obj.addProperty(
            "App::PropertyDistance",
            "OffsetIn",
            "Path Lead-in",
            QT_TRANSLATE_NOOP("App::Property", "Move start point"),
        )
        obj.addProperty(
            "App::PropertyDistance",
            "OffsetOut",
            "Path Lead-out",
            QT_TRANSLATE_NOOP("App::Property", "Move end point"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "ExtendLeadIn",
            "Path Lead-in",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Extends Lead-in distance\nOnly for styles: Arc, Line, Perpendicular and Tangent",
            ),
        )
        obj.addProperty(
            "App::PropertyLength",
            "ExtendLeadOut",
            "Path Lead-out",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Extends Lead-out distance\nOnly for styles: Arc, Line, Perpendicular and Tangent",
            ),
        )
        self.obj = obj
        obj.Proxy = self
        obj.Base = base

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, obj, prop):
        if prop == "Path" and obj.ViewObject:
            obj.ViewObject.signalChangeIcon()

    def setup(self, obj):
        obj.LeadIn = True
        obj.LeadOut = True
        obj.AngleIn = 45
        obj.AngleOut = 45
        obj.InvertIn = False
        obj.InvertOut = False
        obj.RapidPlunge = False
        obj.StyleIn = "Arc"
        obj.StyleOut = "Arc"

        baseOp = PathDressup.baseOp(obj.Base)
        if baseOp and getattr(baseOp, "ToolController", None):
            expr = f"{baseOp.Name}.ToolController.Tool.Diameter.Value/2*1.5"
            obj.setExpression("RadiusIn", expr)
            obj.setExpression("RadiusOut", expr)
        else:
            obj.RadiusIn = 10
            obj.RadiusOut = 10

    @staticmethod
    def updateEditorModes(obj):
        obj.setEditorMode(
            "ExtendLeadIn",
            0 if obj.StyleIn in EXTEND_STYLES else 2,
        )
        obj.setEditorMode(
            "ExtendLeadOut",
            0 if obj.StyleOut in EXTEND_STYLES else 2,
        )
        for property_name, hidden_styles in HIDE_MODES.items():
            obj.setEditorMode(
                property_name + "In",
                2 if obj.StyleIn in hidden_styles else 0,
            )
            obj.setEditorMode(
                property_name + "Out",
                2 if obj.StyleOut in hidden_styles else 0,
            )

    def execute(self, obj):
        if not activeForOp(obj):
            obj.Path = _path_with_job_center(obj)
            return
        if not obj.Base:
            obj.Path = _path_with_job_center(obj)
            return
        if not obj.Base.isDerivedFrom("Path::Feature"):
            obj.Path = _path_with_job_center(obj)
            return
        if not obj.Base.Path:
            obj.Path = _path_with_job_center(obj)
            return
        self.updateEditorModes(obj)
        try:
            path, metadata = generatePathWithMetadata(
                obj.Base,
                lead_in=lead_definition_from_object(obj, "In"),
                lead_out=lead_definition_from_object(obj, "Out"),
                retract_threshold_mm=_finite(
                    obj.RetractThreshold,
                    "Lead In/Out retract threshold",
                ),
                rapid_plunge=bool(obj.RapidPlunge),
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            obj.Path = _path_with_job_center(obj)
            Path.Log.warning(
                translate(
                    "CAM_DressupLeadInOut",
                    "Can not generate Lead In/Out path: %s",
                )
                % str(exc)
            )
            return
        self.lastGenerationStats = metadata
        obj.Path = path

    def onDocumentRestored(self, obj):
        """onDocumentRestored(obj) ... Called automatically when document is restored."""
        styleOn = styleOff = None
        if hasattr(obj, "StyleOn"):
            # Replace StyleOn by StyleIn
            styleOn = obj.StyleOn
            obj.addProperty(
                "App::PropertyEnumeration",
                "StyleIn",
                "Path",
                QT_TRANSLATE_NOOP(
                    "App::Property", "The style of motion into the toolpath"
                ),
            )
            obj.StyleIn = lead_styles
            obj.removeProperty("StyleOn")
            # Set previous value if possible
            if styleOn in lead_styles:
                obj.StyleIn = styleOn
            elif styleOn == "Arc":
                obj.StyleIn = "Arc"
                obj.AngleIn = 90
        if hasattr(obj, "StyleOff"):
            # Replace StyleOff by StyleOut
            styleOff = obj.StyleOff
            obj.addProperty(
                "App::PropertyEnumeration",
                "StyleOut",
                "Path",
                QT_TRANSLATE_NOOP(
                    "App::Property", "The style of motion out of the toolpath"
                ),
            )
            obj.StyleOut = lead_styles
            obj.removeProperty("StyleOff")
            # Set previous value if possible
            if styleOff in lead_styles:
                obj.StyleOut = styleOff
            elif styleOff == "Arc":
                obj.StyleOut = "Arc"
                obj.AngleOut = 90

        if not hasattr(obj, "AngleIn"):
            obj.addProperty(
                "App::PropertyAngle",
                "AngleIn",
                "Path Lead-in",
                QT_TRANSLATE_NOOP("App::Property", "Angle of the Lead-In (1..90)"),
            )
            obj.AngleIn = 90
        if not hasattr(obj, "AngleOut"):
            obj.addProperty(
                "App::PropertyAngle",
                "AngleOut",
                "Path Lead-out",
                QT_TRANSLATE_NOOP("App::Property", "Angle of the Lead-Out (1..90)"),
            )
            obj.AngleOut = 90

        if styleOn:
            if styleOn == "Arc":
                obj.StyleIn = "Arc"
                obj.AngleIn = 90

        if styleOff:
            if styleOff == "Arc":
                obj.StyleOut = "Arc"
                obj.AngleOut = 90

        for prop in ("Length", "LengthIn"):
            if hasattr(obj, prop):
                obj.renameProperty(prop, "RadiusIn")
                break

        if hasattr(obj, "LengthOut"):
            obj.renameProperty("LengthOut", "RadiusOut")

        if hasattr(obj, "PercentageRadiusIn") or hasattr(obj, "PercentageRadiusOut"):
            baseOp = PathDressup.baseOp(obj.Base)
            if hasattr(obj, "PercentageRadiusIn"):
                obj.addProperty(
                    "App::PropertyLength",
                    "RadiusIn",
                    "Path Lead-in",
                    QT_TRANSLATE_NOOP(
                        "App::Property", "Determine length of the Lead-In"
                    ),
                )
                if baseOp and getattr(baseOp, "ToolController", None):
                    valIn = obj.PercentageRadiusIn / 100
                    exprIn = (
                        f"{baseOp.Name}.ToolController.Tool.Diameter.Value/2*{valIn}"
                    )
                    obj.setExpression("RadiusIn", exprIn)
                else:
                    obj.RadiusIn = 10
                obj.removeProperty("PercentageRadiusIn")

            if hasattr(obj, "PercentageRadiusOut"):
                obj.addProperty(
                    "App::PropertyLength",
                    "RadiusOut",
                    "Path Lead-out",
                    QT_TRANSLATE_NOOP(
                        "App::Property", "Determine length of the Lead-Out"
                    ),
                )
                if baseOp and getattr(baseOp, "ToolController", None):
                    valOut = obj.PercentageRadiusOut / 100
                    exprOut = (
                        f"{baseOp.Name}.ToolController.Tool.Diameter.Value/2*{valOut}"
                    )
                    obj.setExpression("RadiusOut", exprOut)
                else:
                    obj.RadiusOut = 10
                obj.removeProperty("PercentageRadiusOut")

        if hasattr(obj, "IncludeLayers"):
            obj.removeProperty("IncludeLayers")

        if not hasattr(obj, "InvertIn"):
            obj.addProperty(
                "App::PropertyBool",
                "InvertIn",
                "Path Lead-in",
                QT_TRANSLATE_NOOP("App::Property", "Invert Lead-In direction"),
            )
        if not hasattr(obj, "InvertOut"):
            obj.addProperty(
                "App::PropertyBool",
                "InvertOut",
                "Path Lead-out",
                QT_TRANSLATE_NOOP("App::Property", "Invert Lead-Out direction"),
            )
        if not hasattr(obj, "OffsetIn"):
            obj.addProperty(
                "App::PropertyDistance",
                "OffsetIn",
                "Path Lead-in",
                QT_TRANSLATE_NOOP("App::Property", "Move start point"),
            )
        if not hasattr(obj, "OffsetOut"):
            obj.addProperty(
                "App::PropertyDistance",
                "OffsetOut",
                "Path Lead-out",
                QT_TRANSLATE_NOOP("App::Property", "Move end point"),
            )
        if not hasattr(obj, "RetractThreshold"):
            obj.addProperty(
                "App::PropertyLength",
                "RetractThreshold",
                "Path",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Set distance which will attempts to avoid unnecessary retractions",
                ),
            )
        if hasattr(obj, "KeepToolDown"):
            if obj.KeepToolDown:
                obj.RetractThreshold = 999999
            obj.removeProperty("KeepToolDown")
        if not hasattr(obj, "ExtendLeadIn"):
            obj.addProperty(
                "App::PropertyLength",
                "ExtendLeadIn",
                "Path Lead-in",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Extends Lead-in distance"
                    "\nOnly for styles: Arc, Line, Perpendicular and Tangent",
                ),
            )
            extLeadInMode = (
                0 if obj.StyleIn in ("Arc", "Line", "Perpendicular", "Tangent") else 2
            )
            obj.setEditorMode("ExtendLeadIn", extLeadInMode)
        if not hasattr(obj, "ExtendLeadOut"):
            obj.addProperty(
                "App::PropertyLength",
                "ExtendLeadOut",
                "Path Lead-out",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Extends Lead-out distance"
                    "\nOnly for styles: Arc, Line, Perpendicular and Tangent",
                ),
            )
            extLeadOutMode = (
                0 if obj.StyleOut in ("Arc", "Line", "Perpendicular", "Tangent") else 2
            )
            obj.setEditorMode("ExtendLeadOut", extLeadOutMode)

        # Ensure correct initial visibility of fields after defaults are set
        for k, v in HIDE_MODES.items():
            obj.setEditorMode(k + "In", 2 if obj.StyleIn in v else 0)
            obj.setEditorMode(k + "Out", 2 if obj.StyleOut in v else 0)

    def getPathParams(self, obj):
        self.horizFeed = 0
        self.vertFeed = 0
        self.entranceFeed = 0
        self.exitFeed = 0
        self.clearanceHeight = None
        self.safeHeight = None

        baseOp = PathDressup.baseOp(obj.Base)

        if getattr(baseOp, "Side", None) in ("Inside", "Outside"):
            self.side = baseOp.Side
        else:
            self.side = "Inside"

        if getattr(baseOp, "Direction", None) in ("CW", "CCW"):
            self.direction = baseOp.Direction
        else:
            self.direction = "CCW"

        toolController = toolControllerForOp(obj)
        if toolController:
            self.horizFeed = toolController.HorizFeed.Value
            self.vertFeed = toolController.VertFeed.Value
            if hasattr(toolController, "LeadInFeed"):
                self.entranceFeed = toolController.LeadInFeed.Value
            if hasattr(toolController, "LeadOutFeed"):
                self.exitFeed = toolController.LeadOutFeed.Value

        if hasattr(baseOp, "ClearanceHeight"):
            self.clearanceHeight = baseOp.ClearanceHeight.Value

        if hasattr(baseOp, "SafeHeight"):
            self.safeHeight = baseOp.SafeHeight.Value

        if (
            self.clearanceHeight is None
            or self.safeHeight is None
            or not self.horizFeed
            or not self.vertFeed
        ):

            def _isVertical(currentposition, cmd):
                x = cmd.Parameters["X"] if "X" in cmd.Parameters else currentposition.x
                y = cmd.Parameters["Y"] if "Y" in cmd.Parameters else currentposition.y
                z = cmd.Parameters["Z"] if "Z" in cmd.Parameters else currentposition.z
                endpoint = App.Vector(x, y, z)
                if Path.Geom.pointsCoincide(currentposition, endpoint):
                    return True
                return Path.Geom.isVertical(Part.makeLine(currentposition, endpoint))

            machine = MachineState()
            rapidZ = []
            for cmd in baseOp.Path.Commands:
                if cmd.Name not in Constants.GCODE_MOVE_ALL:
                    continue
                if _isVertical(machine.getPosition(), cmd):
                    if cmd.Name in Constants.GCODE_MOVE_RAPID and len(rapidZ) < 2:
                        machine.addCommand(cmd)
                        rapidZ.append(machine.getPosition().z)
                        continue
                    if (
                        cmd.Name in Constants.GCODE_MOVE_MILL
                        and not self.vertFeed
                        and "F" in cmd.Parameters
                    ):
                        machine.addCommand(cmd)
                        self.vertFeed = machine.getState()["F"]
                        continue
                elif (
                    cmd.Name in Constants.GCODE_MOVE_MILL
                    and not self.horizFeed
                    and "F" in cmd.Parameters
                ):
                    machine.addCommand(cmd)
                    self.horizFeed = machine.getState()["F"]
                    continue
                if len(rapidZ) >= 2 and self.horizFeed and self.vertFeed:
                    break
                machine.addCommand(cmd)

            if len(rapidZ) >= 2:
                if self.clearanceHeight is None:
                    self.clearanceHeight = rapidZ[0]
                if self.safeHeight is None:
                    self.safeHeight = rapidZ[1]

        if hasattr(baseOp, "StartDepth"):
            self.startDepth = baseOp.StartDepth.Value
        else:
            self.startDepth = self.safeHeight

        if not self.entranceFeed:
            self.entranceFeed = self.vertFeed
        if not self.exitFeed:
            self.exitFeed = self.horizFeed

        self.clearanceHeightOut = self.clearanceHeight
        if hasattr(baseOp, "ClearanceHeightOut"):
            self.clearanceHeightOut = baseOp.ClearanceHeightOut.Value

        return self.clearanceHeight is not None and self.safeHeight is not None

    # Get direction for lead-in/lead-out in XY plane
    def getLeadDir(self, invert=False):
        output = math.pi / 2
        side = self.side
        direction = self.direction
        if (side == "Inside" and direction == "CW") or (
            side == "Outside" and direction == "CCW"
        ):
            output = -output
        if invert:
            output = -output
        if self.invertAlt:
            output = -output

        return output

    # Get direction of original path
    def getArcPathDir(self, cmdName):
        # only CW/CCW and G2/G3 matters
        direction = self.direction
        output = math.pi / 2
        if direction == "CW":
            output = -output

        if cmdName in Constants.GCODE_MOVE_CW and direction == "CCW":
            output = -output
        elif cmdName in Constants.GCODE_MOVE_CCW and direction == "CW":
            output = -output

        return output

    # Create safety movements to start point
    def getTravelStart(self, obj, pos, first, outInstrPrev):
        commands = []
        posPrev = outInstrPrev.positionEnd() if outInstrPrev else App.Vector()
        posPrevXY = App.Vector(posPrev.x, posPrev.y, 0)
        posXY = App.Vector(pos.x, pos.y, 0)
        distance = posPrevXY.distanceToPoint(posXY)

        if first or (distance > obj.RetractThreshold):
            # move to clearance height
            commands.append(
                PathLanguage.MoveStraight(None, "G0", {"Z": self.clearanceHeight})
            )

            # move to mill position at clearance height
            commands.append(
                PathLanguage.MoveStraight(None, "G0", {"X": pos.x, "Y": pos.y})
            )

            # move vertical down to mill position
            if obj.RapidPlunge:
                # move to mill position rapidly
                commands.append(PathLanguage.MoveStraight(None, "G0", {"Z": pos.z}))
            else:
                # move to mill position in two steps
                commands.append(
                    PathLanguage.MoveStraight(None, "G0", {"Z": self.safeHeight})
                )
                commands.append(
                    PathLanguage.MoveStraight(
                        None, "G1", {"Z": pos.z, "F": self.vertFeed}
                    )
                )

        else:
            # move to next mill position by short path
            if obj.RapidPlunge:
                commands.append(
                    PathLanguage.MoveStraight(
                        None, "G0", {"X": pos.x, "Y": pos.y, "Z": pos.z}
                    )
                )
            else:
                commands.append(
                    PathLanguage.MoveStraight(
                        None,
                        "G1",
                        {"X": pos.x, "Y": pos.y, "Z": pos.z, "F": self.vertFeed},
                    )
                )

        return commands

    # Create commands with movements to clearance height
    def getTravelEnd(self):
        commands = []
        z = self.clearanceHeightOut
        commands.append(PathLanguage.MoveStraight(None, "G0", {"Z": z}))

        return commands

    # Create vector object from angle
    def angleToVector(self, angle):
        return App.Vector(math.cos(angle), math.sin(angle), 0)

    # Create arc in XY plane with automatic detection G2|G3
    def createArcMove(self, begin, end, offset, invert, feedRate):
        param = {
            "X": end.x,
            "Y": end.y,
            "Z": end.z,
            "I": offset.x,
            "J": offset.y,
            "F": feedRate,
        }
        if self.getLeadDir(invert) > 0:
            command = PathLanguage.MoveArcCCW(begin, "G3", param)
        else:
            command = PathLanguage.MoveArcCW(begin, "G2", param)

        return command

    # Create arc in XY plane with manually set G2|G3
    def createArcMoveN(self, begin, end, offset, cmdName, feedRate):
        param = {"X": end.x, "Y": end.y, "I": offset.x, "J": offset.y, "F": feedRate}
        if cmdName in Constants.GCODE_MOVE_CW:
            command = PathLanguage.MoveArcCW(begin, cmdName, param)
        else:
            command = PathLanguage.MoveArcCCW(begin, cmdName, param)

        return command

    # Create line movement G1
    def createStraightMove(self, begin, end, feedRate):
        param = {"X": end.x, "Y": end.y, "Z": end.z, "F": feedRate}
        command = PathLanguage.MoveStraight(begin, "G1", param)

        return command

    # Get optimal step angle for iteration ArcZ
    def getStepAngleArcZ(self, angle, radius, div=None):
        if div:
            stepAngle = angle / div
        else:
            stepLength = self.tolerance * 10
            stepAngle = stepLength / radius
        stepAngle = angle / math.ceil(round(angle / stepAngle, 6))

        return stepAngle

    def _guardOutputCount(self, count):
        limit = int(getattr(self, "_max_output_commands", MAX_LEAD_OUTPUT_COMMANDS))
        if count > limit:
            raise ValueError(f"Lead In/Out would generate more than {limit} commands")

    def _guardDiscretization(self, edges):
        step = self.tolerance * 10.0
        if step <= 0.0:
            raise ValueError("Lead In/Out discretization step must be positive")
        estimated = sum(
            max(2, int(math.ceil(float(edge.Length) / step)) + 1) for edge in edges
        )
        self._guardOutputCount(estimated)

    # Get commands from original path for follow lead in
    def getCommandsFollowIn(self, obj, distance):
        cmds = []
        offset = obj.OffsetIn.Value
        if distance + offset > 0:
            cmds.extend(
                [copy.deepcopy(cmd) for cmd in self.extendTravelIn(distance + offset)]
            )
            if offset > 0:
                cmds = self.cutTravelEnd(cmds, offset)
        if offset < 0:
            # need process as closed profile even if profile is open
            cmds.extend(
                [copy.deepcopy(cmd) for cmd in self.extendTravelOut(abs(offset), True)]
            )
            if distance + offset < 0:
                cmds = self.cutTravelBegin(cmds, abs(distance + offset))

        return cmds

    # Get commands from original path for follow lead out
    def getCommandsFollowOut(self, obj, distance):
        offset = obj.OffsetOut.Value
        cmds = []
        if offset < 0:
            # need process as closed profile even if profile is open
            cmds.extend(
                [copy.deepcopy(cmd) for cmd in self.extendTravelIn(abs(offset), True)]
            )
            if distance + offset < 0:
                cmds = self.cutTravelEnd(cmds, abs(distance + offset))
        if distance + offset > 0:
            cmds.extend(
                [copy.deepcopy(cmd) for cmd in self.extendTravelOut(distance + offset)]
            )
            if offset > 0:
                cmds = self.cutTravelBegin(cmds, offset)

        return cmds

    # Create lead-in line which follows profile
    def createLineZFollowIn(self, obj, begin, end, feedRate):
        z = begin.z
        length = begin.distanceToPoint(end)
        angle = math.asin((begin.z - end.z) / length)
        distance = length * math.cos(angle)
        cmds = self.getCommandsFollowIn(obj, distance)
        for i, cmd in enumerate(cmds):
            distance -= cmd.pathLength()
            cmd.begin.z = z
            if i == len(cmds) - 1:  # last command
                # forcing end position to exclude precision error
                z = end.z
            else:
                z = end.z + math.tan(angle) * distance
            cmd.param["Z"] = z
        return cmds

    # Create lead-out line which follows profile
    def createLineZFollowOut(self, obj, begin, end, feedRate):
        z = begin.z
        length = begin.distanceToPoint(end)
        angle = math.asin((end.z - begin.z) / length)
        distance = length * math.cos(angle)
        cmds = self.getCommandsFollowOut(obj, distance)
        dist = 0
        for i, cmd in enumerate(cmds):
            dist += cmd.pathLength()
            cmd.begin.z = z
            if i == len(cmds) - 1:  # last command
                # forcing end position to exclude precision error
                z = end.z
            else:
                z = begin.z + math.tan(angle) * dist
            cmd.param["Z"] = z
        return cmds

    # Create vertical arc with move Down by line segments
    def createArcZIn(self, begin, end, radius, feedRate):
        commands = []
        angle = math.acos((radius - begin.z + end.z) / radius)  # start angle
        stepAngle = self.getStepAngleArcZ(angle, radius, div=20)
        angle -= stepAngle
        iterBegin = copy.copy(begin)  # start point of short segment
        v = end - begin
        n = math.hypot(v.x, v.y)
        u = v / n
        while angle > 0 and not Path.Geom.isRoughly(angle, 0):
            distance = n - radius * math.sin(angle)
            iterEnd = begin + u * distance
            iterEnd.z = end.z + radius * (1 - math.cos(angle))
            param = {"X": iterEnd.x, "Y": iterEnd.y, "Z": iterEnd.z, "F": feedRate}
            commands.append(PathLanguage.MoveStraight(iterBegin, "G1", param))
            iterBegin = copy.copy(iterEnd)
            angle -= stepAngle

        # last move to end point
        param = {"X": end.x, "Y": end.y, "Z": end.z, "F": feedRate}
        commands.append(PathLanguage.MoveStraight(iterBegin, "G1", param))

        return commands

    # Create vertical arc with move Up by line segments
    def createArcZOut(self, begin, end, radius, feedRate):
        commands = []
        angleMax = math.acos((radius - end.z + begin.z) / radius)  # finish angle
        stepAngle = self.getStepAngleArcZ(angleMax, radius, div=20)
        iterBegin = copy.copy(begin)  # start point of short segment
        v = end - begin
        n = math.hypot(v.x, v.y)
        u = v / n
        angle = stepAngle  # start angle
        while angle < angleMax and not Path.Geom.isRoughly(angle, angleMax):
            distance = radius * math.sin(angle)
            iterEnd = begin + u * distance
            iterEnd.z = begin.z + radius * (1 - math.cos(angle))
            param = {"X": iterEnd.x, "Y": iterEnd.y, "Z": iterEnd.z, "F": feedRate}
            commands.append(PathLanguage.MoveStraight(iterBegin, "G1", param))
            iterBegin = copy.copy(iterEnd)
            angle += stepAngle

        # last move to end point
        param = {"X": end.x, "Y": end.y, "Z": end.z, "F": feedRate}
        commands.append(PathLanguage.MoveStraight(iterBegin, "G1", param))

        return commands

    # Create vertical arc with move Down and follow profile
    def createArcZFollowIn(self, obj, begin, end, radius, feedRate):
        p1 = App.Vector(begin.x, begin.y, 0)
        p2 = App.Vector(end.x, end.y, 0)
        distance = p1.distanceToPoint(p2)
        cmds = self.getCommandsFollowIn(obj, distance)
        edges = [
            Path.Geom.edgeForCmd(cmd.toCommand(), cmd.positionBegin()) for cmd in cmds
        ]
        self._guardDiscretization(edges)
        wire = Part.Wire(Part.__sortEdges__(edges))
        points = []
        for edge in wire.Edges:
            # skip first point of each edge
            points.extend(edge.discretize(Distance=self.tolerance * 10)[1:])
        points.insert(0, wire.OrderedVertexes[0].Point)

        prevZ = begin.z
        commands = []
        for i, p in enumerate(points):
            distance -= p.distanceToPoint(points[i + 1])
            nextZ = end.z + radius - math.sqrt(radius**2 - distance**2)
            param = {
                "X": points[i + 1].x,
                "Y": points[i + 1].y,
                "Z": nextZ,
                "F": feedRate,
            }
            commands.append(
                PathLanguage.MoveStraight(App.Vector(p.x, p.y, prevZ), "G1", param)
            )
            if i == len(points) - 2:
                break
            prevZ = nextZ

        return commands

    # Create vertical arc with move Down and follow profile
    def createArcZFollowOut(self, obj, begin, end, radius, feedRate):
        p1 = App.Vector(begin.x, begin.y, 0)
        p2 = App.Vector(end.x, end.y, 0)
        distance = p1.distanceToPoint(p2)
        cmds = self.getCommandsFollowOut(obj, distance)
        edges = [
            Path.Geom.edgeForCmd(cmd.toCommand(), cmd.positionBegin()) for cmd in cmds
        ]
        self._guardDiscretization(edges)
        wire = Part.Wire(Part.__sortEdges__(edges))
        points = []
        for edge in wire.Edges:
            # skip first point of each edge
            points.extend(edge.discretize(Distance=self.tolerance * 10)[1:])
        points.insert(0, wire.OrderedVertexes[0].Point)
        prevZ = begin.z
        commands = []
        dist = 0
        for i, p in enumerate(points):
            dist += p.distanceToPoint(points[i + 1])
            nextZ = begin.z + radius - math.sqrt(radius**2 - dist**2)
            param = {
                "X": points[i + 1].x,
                "Y": points[i + 1].y,
                "Z": nextZ,
                "F": feedRate,
            }
            commands.append(
                PathLanguage.MoveStraight(App.Vector(p.x, p.y, prevZ), "G1", param)
            )
            if i == len(points) - 2:
                break
            prevZ = nextZ
        return commands

    def getLeadStart(self, obj, move, first, inInstrPrev, outInstrPrev):
        #    tangent  begin      move
        #    <----_-----x-------------------x
        #       /       |
        #     /         | normal
        #    |          |
        #    x          v

        lead = []
        begin = move.positionBegin()
        beginZ = move.positionBegin().z  # do not change this variable below

        if not obj.LeadIn and obj.LeadOut:
            # can not skip leadin if leadout
            # override style to get correct move to next step down
            styleIn = "Vertical"
        else:
            styleIn = obj.StyleIn

        if styleIn not in ("No Retract", "Vertical"):
            if styleIn == "Perpendicular":
                angleIn = math.pi / 2
            elif styleIn == "Tangent":
                angleIn = 0
            else:
                angleIn = math.radians(obj.AngleIn.Value)

            length = obj.RadiusIn.Value
            angleTangent = move.anglesOfTangents()[0]
            normalMax = (
                self.angleToVector(angleTangent + self.getLeadDir(obj.InvertIn))
                * length
            )

            # Here you can find description of the calculations
            # https://forum.freecad.org/viewtopic.php?t=97641

            # prepend "Arc" style lead-in - arc in XY
            # Arc3d the same as Arc, but increased Z start point
            if styleIn in ("Arc", "Arc3d", "Helix"):
                # tangent and normal vectors in XY plane
                arcRadius = length
                tangentLength = math.sin(angleIn) * arcRadius
                normalLength = arcRadius * (1 - math.cos(angleIn))
                tangent = -self.angleToVector(angleTangent) * tangentLength
                normal = (
                    self.angleToVector(angleTangent + self.getLeadDir(obj.InvertIn))
                    * normalLength
                )
                arcBegin = begin + tangent + normal
                arcCenter = begin + normalMax
                arcOffset = arcCenter - arcBegin
                lead.append(
                    self.createArcMove(
                        arcBegin, begin, arcOffset, obj.InvertIn, self.entranceFeed
                    )
                )
                if obj.ExtendLeadIn and styleIn == "Arc":
                    extAngleTangent = lead[-1].anglesOfTangents()[0]
                    extTangent = (
                        -self.angleToVector(extAngleTangent) * obj.ExtendLeadIn.Value
                    )
                    arcBegin = lead[-1].positionBegin()
                    extBegin = arcBegin + extTangent
                    lead.insert(
                        0,
                        self.createStraightMove(extBegin, arcBegin, self.entranceFeed),
                    )

            # prepend "Line" style lead-in - line in XY
            # Line3d the same as Line, but increased Z start point
            elif styleIn in ("Line", "Line3d", "Perpendicular", "Tangent"):
                if styleIn in ("Line", "Perpendicular", "Tangent"):
                    length += obj.ExtendLeadIn.Value
                # tangent and normal vectors in XY plane
                tangentLength = math.cos(angleIn) * length
                normalLength = math.sin(angleIn) * length
                tangent = -self.angleToVector(angleTangent) * tangentLength
                normal = (
                    self.angleToVector(angleTangent + self.getLeadDir(obj.InvertIn))
                    * normalLength
                )
                lineBegin = begin + tangent + normal
                lead.append(
                    self.createStraightMove(lineBegin, begin, self.entranceFeed)
                )

            # prepend "LineZ" style lead-in - vertical inclined line
            # Should be applied only on straight Path segment
            elif styleIn in ("LineZ", "LineZFollow"):
                # tangent vector in XY plane
                # normal vector is vertical
                normalLengthMax = self.safeHeight - begin.z
                normalLength = math.sin(angleIn) * length
                # do not exceed Normal vector max length
                normalLength = min(normalLength, normalLengthMax)
                tangentLength = normalLength / math.tan(angleIn)
                tangent = -self.angleToVector(angleTangent) * tangentLength
                normal = App.Vector(0, 0, normalLength)
                lineBegin = begin + tangent + normal
                if styleIn == "LineZ":
                    lead.append(
                        self.createStraightMove(lineBegin, begin, self.entranceFeed)
                    )
                else:
                    lead.extend(
                        self.createLineZFollowIn(
                            obj, lineBegin, begin, self.entranceFeed
                        )
                    )

            # prepend "ArcZ" style lead-in - vertical Arc
            # Should be applied only on straight Path segment or open profile
            elif styleIn in ("ArcZ", "ArcZFollow"):
                # tangent vector in XY plane
                # normal vector is vertical
                arcRadius = length
                normalLengthMax = self.safeHeight - begin.z
                normalLength = arcRadius * (1 - math.cos(angleIn))
                if normalLength > normalLengthMax:
                    # do not exceed Normal vector max length
                    normalLength = normalLengthMax
                    # recalculate angle for limited normal length
                    angleIn = math.acos((arcRadius - normalLength) / arcRadius)
                tangentLength = arcRadius * math.sin(angleIn)
                tangent = -self.angleToVector(angleTangent) * tangentLength
                normal = App.Vector(0, 0, normalLength)
                arcBegin = begin + tangent + normal
                if styleIn == "ArcZ":
                    lead.extend(
                        self.createArcZIn(arcBegin, begin, arcRadius, self.entranceFeed)
                    )
                else:
                    lead.extend(
                        self.createArcZFollowIn(
                            obj, arcBegin, begin, arcRadius, self.entranceFeed
                        )
                    )

            # replace 'begin' position with first lead-in command
            begin = lead[0].positionBegin()

            if styleIn in ("Arc3d", "Line3d"):
                # up Z start point for Arc3d and Line3d
                if inInstrPrev and inInstrPrev.z() > begin.z:
                    begin.z = inInstrPrev.z()
                else:
                    begin.z = self.startDepth
                lead[0].setPositionBegin(begin)

            elif styleIn == "Helix":
                # change Z for current helix lead-in
                posPrevZ = None
                if outInstrPrev:
                    posPrevZ = outInstrPrev.positionEnd().z
                if posPrevZ is not None and posPrevZ > beginZ:
                    halfStepZ = (posPrevZ - beginZ) / 2
                    begin.z += halfStepZ
                else:
                    begin.z = self.startDepth

        if obj.StyleOut == "Helix" and outInstrPrev:
            """change Z for previous helix lead-out
            Unable to do it in getLeadEnd(), due to lack of
            existing information about next moves while creating Lead-out"""
            posPrevZ = outInstrPrev.positionEnd().z
            if posPrevZ > beginZ:
                """previous profile upper than this
                mean processing one stepdown profile"""
                halfStepZ = (posPrevZ - beginZ) / 2
                outInstrPrev.param["Z"] = posPrevZ - halfStepZ

        # get complete start travel moves
        if styleIn != "No Retract":
            travelToStart = self.getTravelStart(obj, begin, first, outInstrPrev)
        else:
            # exclude any lead-in commands
            param = {"X": begin.x, "Y": begin.y, "Z": begin.z, "F": self.entranceFeed}
            travelToStart = [PathLanguage.MoveStraight(None, "G1", param)]

        lead = travelToStart + lead

        return lead

    def getLeadEnd(self, obj, move, last, outInstrPrev):
        #            move       end   tangent
        #    x-------------------x-----_---->
        #                        |       \
        #                 normal |         \
        #                        |          |
        #                        v          x

        lead = []
        end = move.positionEnd()

        if obj.StyleOut not in ("No Retract", "Vertical"):
            if obj.StyleOut == "Perpendicular":
                angleOut = math.pi / 2
            elif obj.StyleOut == "Tangent":
                angleOut = 0
            else:
                angleOut = math.radians(obj.AngleOut.Value)

            length = obj.RadiusOut.Value
            angleTangent = move.anglesOfTangents()[1]
            normalMax = (
                self.angleToVector(angleTangent + self.getLeadDir(obj.InvertOut))
                * length
            )

            # Here you can find description of the calculations
            # https://forum.freecad.org/viewtopic.php?t=97641

            # append "Arc" style lead-out - arc in XY
            # Arc3d the same as Arc, but increased Z start point
            if obj.StyleOut in ("Arc", "Arc3d", "Helix"):
                # tangent and normal vectors in XY plane
                arcRadius = length
                tangentLength = math.sin(angleOut) * arcRadius
                normalLength = arcRadius * (1 - math.cos(angleOut))
                tangent = self.angleToVector(angleTangent) * tangentLength
                normal = (
                    self.angleToVector(angleTangent + self.getLeadDir(obj.InvertOut))
                    * normalLength
                )
                arcEnd = end + tangent + normal
                lead.append(
                    self.createArcMove(
                        end, arcEnd, normalMax, obj.InvertOut, self.exitFeed
                    )
                )
                if obj.ExtendLeadOut and obj.StyleOut == "Arc":
                    extAngleTangent = lead[-1].anglesOfTangents()[1]
                    extTangent = (
                        self.angleToVector(extAngleTangent) * obj.ExtendLeadOut.Value
                    )
                    arcEnd = lead[-1].positionEnd()
                    extEnd = arcEnd + extTangent
                    lead.append(self.createStraightMove(arcEnd, extEnd, self.exitFeed))

            # append "Line" style lead-out
            # Line3d the same as Line, but increased Z start point
            elif obj.StyleOut in ("Line", "Line3d", "Perpendicular", "Tangent"):
                if obj.StyleOut in ("Line", "Perpendicular", "Tangent"):
                    length += obj.ExtendLeadOut.Value
                # tangent and normal vectors in XY plane
                tangentLength = math.cos(angleOut) * length
                normalLength = math.sin(angleOut) * length
                tangent = self.angleToVector(angleTangent) * tangentLength
                normal = (
                    self.angleToVector(angleTangent + self.getLeadDir(obj.InvertOut))
                    * normalLength
                )
                lineEnd = end + tangent + normal
                lead.append(self.createStraightMove(end, lineEnd, self.exitFeed))

            # append "LineZ" style lead-out - vertical inclined line
            # Should be apply only on straight Path segment
            elif obj.StyleOut in ("LineZ", "LineZFollow"):
                # tangent vector in XY plane
                # normal vector is vertical
                normalLengthMax = self.startDepth - end.z
                normalLength = math.sin(angleOut) * length
                # do not exceed Normal vector max length
                normalLength = min(normalLength, normalLengthMax)
                tangentLength = normalLength / math.tan(angleOut)
                tangent = self.angleToVector(angleTangent) * tangentLength
                normal = App.Vector(0, 0, normalLength)
                lineEnd = end + tangent + normal
                if obj.StyleOut == "LineZ":
                    lead.append(self.createStraightMove(end, lineEnd, self.exitFeed))
                else:
                    lead.extend(
                        self.createLineZFollowOut(obj, end, lineEnd, self.exitFeed)
                    )

            # prepend "ArcZ" style lead-out - vertical Arc
            # Should be apply only on straight Path segment
            elif obj.StyleOut in ("ArcZ", "ArcZFollow"):
                # tangent vector in XY plane
                # normal vector is vertical
                arcRadius = length
                normalLengthMax = self.safeHeight - end.z
                normalLength = arcRadius * (1 - math.cos(angleOut))
                if normalLength > normalLengthMax:
                    # do not exceed Normal vector max length
                    normalLength = normalLengthMax
                    # recalculate angle for limited normal length
                    angleOut = math.acos((arcRadius - normalLength) / arcRadius)
                tangentLength = arcRadius * math.sin(angleOut)
                tangent = self.angleToVector(angleTangent) * tangentLength
                normal = App.Vector(0, 0, normalLength)
                arcEnd = end + tangent + normal
                if obj.StyleOut == "ArcZ":
                    lead.extend(
                        self.createArcZOut(end, arcEnd, arcRadius, self.exitFeed)
                    )
                else:
                    lead.extend(
                        self.createArcZFollowOut(
                            obj, end, arcEnd, arcRadius, self.exitFeed
                        )
                    )

        if obj.StyleOut in ("Arc3d", "Line3d"):
            # Up Z end point for Arc3d and Line3d
            if outInstrPrev and outInstrPrev.positionBegin().z > end.z:
                lead[-1].param["Z"] = outInstrPrev.positionBegin().z
            else:
                lead[-1].param["Z"] = self.startDepth

        # append travel moves to clearance height after finishing all profiles
        if last and obj.StyleOut != "No Retract":
            lead += self.getTravelEnd()

        return lead

    # Check command
    def isCuttingMove(self, instr):
        result = instr.isMove() and not instr.isRapid() and not instr.isPlunge()
        return result

    # Get direction of non cut movements
    def getMoveDir(self, instr):
        if instr.positionBegin().z > instr.positionEnd().z:
            return "Down"
        elif instr.positionBegin().z < instr.positionEnd().z:
            return "Up"
        elif instr.pathLength() != 0:
            return "Hor"
        else:
            # move command without change position
            return None

    # Get last index of mill command in whole Path
    def findLastCuttingMoveIndex(self):
        for i in range(len(self.source) - 1, -1, -1):
            if self.isCuttingMove(self.source[i]):
                return i

        return None

    # Get finish index of mill command for one profile
    def findLastCutMultiProfileIndex(self):
        startIndex = self.firstMillIndex
        self.profileLength = 0
        if startIndex >= len(self.source):
            return len(self.source) - 1
        startPoint = self.source[startIndex].positionBegin()
        for i in range(startIndex, len(self.source)):
            if not self.isCuttingMove(self.source[i]):
                return i - 1
            self.profileLength += self.source[i].pathLength()
            if Path.Geom.pointsCoincide(startPoint, self.source[i].positionEnd()):
                # Workaround for several closed paths without retraction
                return i

        return i

    def isProfileClosed(self):
        start = self.firstMillIndex
        end = self.lastMillIndex
        startPoint = self.source[start].positionBegin()
        endPoint = self.source[end].positionEnd()

        if Path.Geom.pointsCoincide(startPoint, endPoint):
            return True
        else:
            return False

    # Increase travel length from 'begin', take commands from profile 'end'
    def extendTravelIn(self, length, forceClosed=None):
        start = self.firstMillIndex
        end = self.lastMillIndex
        closedProfile = forceClosed if forceClosed is not None else self.closedProfile
        if closedProfile:
            # closed profile
            # get extra commands from end of the closed profile
            if self.profileLength <= 0.0:
                raise ValueError("Lead In/Out closed profile has zero path length")
            ratio = int(length / self.profileLength)  # number extra repeats
            self._guardOutputCount((ratio + 1) * (end - start + 1))
            length = length - ratio * self.profileLength
            measuredLength = 0
            commands = []
            for i, instr in enumerate(reversed(self.source[start : end + 1])):
                instrLength = instr.pathLength()
                if Path.Geom.isRoughly(measuredLength + instrLength, length):
                    # get needed length without needing to cut last command
                    commands = self.source[end - i : end + 1]
                    break
                elif measuredLength + instrLength > length:
                    # measured length exceeds needed length and needs cut command
                    commands = self.source[end - i + 1 : end + 1]
                    newLength = length - measuredLength
                    newInstr = self.cutInstrBegin(instr, newLength)
                    commands.insert(0, newInstr)
                    break
                measuredLength += instrLength

            return commands + self.source[start : end + 1] * ratio

        else:
            # open profile
            # extend first move
            instr = self.source[start]
            newLength = length + instr.pathLength()
            newInstr = self.cutInstrBegin(instr, newLength)
            newInstr.setPositionEnd(instr.positionBegin())
            return [newInstr]

    # Increase travel length from 'end', take commands from profile 'start'
    def extendTravelOut(self, length, forceClosed=None):
        start = self.firstMillIndex
        end = self.lastMillIndex
        closedProfile = forceClosed if forceClosed is not None else self.closedProfile
        if closedProfile:
            # closed profile
            # get extra commands from begin of the closed profile
            if self.profileLength <= 0.0:
                raise ValueError("Lead In/Out closed profile has zero path length")
            ratio = int(length / self.profileLength)  # number extra repeats
            self._guardOutputCount((ratio + 1) * (end - start + 1))
            length = length - ratio * self.profileLength
            measuredLength = 0
            commands = []
            for i, instr in enumerate(self.source[start : end + 1]):
                instrLength = instr.pathLength()
                if Path.Geom.isRoughly(measuredLength + instrLength, length):
                    # get needed length without needing to cut last command
                    commands = self.source[start : start + i + 1]
                    break
                elif measuredLength + instrLength > length:
                    # measured length exceeds needed length and needs cut command
                    commands = self.source[start : start + i]
                    newLength = length - measuredLength
                    newInstr = self.cutInstrEnd(instr, newLength)
                    commands.append(newInstr)
                    break
                measuredLength += instrLength

            return self.source[start : end + 1] * ratio + commands

        else:
            # open profile
            # extend last move
            instr = self.source[end]
            newLength = length + instr.pathLength()
            newInstr = self.cutInstrEnd(instr, newLength)
            newInstr = self.cutInstrBegin(newInstr, length)
            return [newInstr]

    # Cut travel end by distance (negative overtravel out)
    def cutTravelEnd(self, commands, cutLength):
        measuredLength = 0
        for i, instr in enumerate(reversed(commands)):
            if instr.positionBegin() is None:
                # workaround if cut whole profile by negative offset
                cmds = commands[:-i]
                newInstr = self.cutInstrEnd(commands[-i], 0.1)
                cmds.append(newInstr)
                return cmds

            instrLength = instr.pathLength()
            measuredLength += instrLength
            if Path.Geom.isRoughly(measuredLength, cutLength):
                # get needed length and not need to cut any command
                return commands[: -i - 1]

            elif measuredLength > cutLength:
                # measured length exceed needed cut length and need cut command
                cmds = commands[: -i - 1]
                newLength = measuredLength - cutLength
                newInstr = self.cutInstrEnd(instr, newLength)
                cmds.append(newInstr)
                return cmds

        Path.Log.warning(translate("CAM", "Exceeded length in cutTravelEnd"))
        return []

    # Cut travel from begin by distance
    def cutTravelBegin(self, commands, cutLength):
        measuredLength = 0
        for i, instr in enumerate(commands):
            instrLength = instr.pathLength()
            measuredLength += instrLength
            if Path.Geom.isRoughly(measuredLength, cutLength):
                # get needed length and not need to cut any command
                return commands[i + 1 :]

            elif measuredLength > cutLength:
                # measured length exceed needed cut length and need cut command
                cmds = commands[i + 1 :]
                newLength = measuredLength - cutLength
                newInstr = self.cutInstrBegin(instr, newLength)
                cmds.insert(0, newInstr)
                return cmds

        Path.Log.warning(translate("CAM", "Exceeded length in cutTravelBegin"))
        return []

    # Change end point of instruction
    def cutInstrEnd(self, instr, newLength):
        command = None
        # Cut straight move from begin
        if instr.isStraight():
            begin = instr.positionBegin()
            end = instr.positionEnd()
            v = end - begin
            n = math.hypot(v.x, v.y)
            u = v / n
            cutEnd = begin + u * newLength
            command = self.createStraightMove(begin, cutEnd, self.horizFeed)

        # Cut arc move from begin
        elif instr.isArc():
            cmdName = instr.cmd
            angleTangent = instr.anglesOfTangents()[0]
            arcBegin = instr.positionBegin()
            arcOffset = App.Vector(instr.i(), instr.j(), instr.k())
            arcRadius = instr.arcRadius()
            arcAngle = newLength / arcRadius
            tangentLength = math.sin(arcAngle) * arcRadius
            normalLength = arcRadius * (1 - math.cos(arcAngle))
            tangent = self.angleToVector(angleTangent) * tangentLength
            normal = (
                self.angleToVector(angleTangent + self.getArcPathDir(cmdName))
                * normalLength
            )
            arcEnd = arcBegin + tangent + normal
            command = self.createArcMoveN(
                arcBegin, arcEnd, arcOffset, cmdName, self.horizFeed
            )

        return command

    # Change start point of instruction
    def cutInstrBegin(self, instr, newLength):
        # Cut straignt move from begin
        if instr.isStraight():
            begin = instr.positionBegin()
            end = instr.positionEnd()
            v = end - begin
            n = math.hypot(v.x, v.y)
            u = v / n
            newBegin = end - u * newLength
            command = self.createStraightMove(newBegin, end, self.horizFeed)
            return command

        # Cut arc move from begin
        elif instr.isArc():
            cmdName = instr.cmd
            angleTangent = instr.anglesOfTangents()[1]
            arcEnd = instr.positionEnd()
            arcCenter = instr.xyCenter()
            arcRadius = instr.arcRadius()
            arcAngle = newLength / arcRadius
            tangentLength = math.sin(arcAngle) * arcRadius
            normalLength = arcRadius * (1 - math.cos(arcAngle))
            tangent = -self.angleToVector(angleTangent) * tangentLength
            normal = (
                self.angleToVector(angleTangent + self.getArcPathDir(cmdName))
                * normalLength
            )
            arcBegin = arcEnd + tangent + normal
            arcOffset = arcCenter - arcBegin
            command = self.createArcMoveN(
                arcBegin, arcEnd, arcOffset, cmdName, self.horizFeed
            )
            return command

        return None

    def generateLeadInOutCurve(self, obj):
        source = PathLanguage.Maneuver.FromPath(
            PathUtils.getPathWithPlacement(obj.Base)
        ).instr
        self.source = source
        maneuver = PathLanguage.Maneuver()

        # Knowing whether a given instruction is the first cutting move is easy,
        # we just use a flag and set it to false afterwards. To find the last
        # cutting move we need to search the list in reverse order.

        commands = []
        first = True  # prepare first move at clearance height
        self.firstMillIndex = None  # Index start mill instruction for one profile
        self.lastMillIndex = None  # Index end mill instruction for one profile
        self.lastCuttingMoveIndex = self.findLastCuttingMoveIndex()
        self.closedProfile = None
        inInstrPrev = None  # for RetractThreshold
        outInstrPrev = None  # for RetractThreshold
        measuredLength = 0  # for negative OffsetIn
        skipCounter = 0  # for negative OffsetIn
        moveDir = None

        # Process all instructions
        for i, instr in enumerate(source):
            # Process without mill instruction
            if not self.isCuttingMove(instr):
                if not instr.isMove():
                    # non-move instruction gets added verbatim
                    commands.append(instr)
                else:
                    moveDir = self.getMoveDir(instr)
                    if (not obj.LeadIn and not obj.LeadOut) and (
                        moveDir in ("Down", "Hor") or first
                    ):
                        # keep original Lead-in movements
                        commands.append(instr)
                    if not obj.LeadOut and moveDir == "Up" and not first:
                        # keep original Lead-out movements
                        commands.append(instr)
                # skip travel and plunge moves if LeadInOut will be process
                # travel moves will be added in getLeadStart and getLeadEnd
                self._guardOutputCount(len(commands))
                continue

            # measuring length for one profile
            if self.isCuttingMove(instr):
                measuredLength += instr.pathLength()

            # Process Lead-In
            if first or not self.isCuttingMove(source[i - 1 - skipCounter]):
                if obj.LeadIn or obj.LeadOut:
                    # can not skip leadin if leadout

                    self.firstMillIndex = (
                        i if self.firstMillIndex is None else self.firstMillIndex
                    )
                    self.lastMillIndex = (
                        self.findLastCutMultiProfileIndex()
                        if self.lastMillIndex is None
                        else self.lastMillIndex
                    )

                    self.closedProfile = self.isProfileClosed()
                    stats = getattr(self, "_generation_stats", None)
                    if stats is not None:
                        stats["profile_count"] += 1
                        if self.closedProfile:
                            stats["closed_profile_count"] += 1
                        else:
                            stats["open_profile_count"] += 1
                        if obj.LeadIn:
                            stats["lead_in_profile_count"] += 1
                        if obj.LeadOut:
                            stats["lead_out_profile_count"] += 1

                    overtravelIn = None
                    if obj.OffsetIn.Value < 0 and obj.StyleIn != "No Retract":
                        # Process negative Offset Lead-In (cut travel from begin)
                        if measuredLength <= abs(obj.OffsetIn.Value):
                            # skip mill instruction
                            skipCounter += 1  # count skipped instructions
                            continue
                        else:
                            skipCounter = 0
                            # cut mill instruction
                            newLength = measuredLength - abs(obj.OffsetIn.Value)
                            instr = self.cutInstrBegin(instr, newLength)

                    elif obj.OffsetIn.Value > 0 and obj.StyleIn != "No Retract":
                        # Process positive offset Lead-In (overtravel)
                        overtravelIn = self.extendTravelIn(obj.OffsetIn.Value)
                    if overtravelIn:
                        commands.extend(
                            self.getLeadStart(
                                obj, overtravelIn[0], first, inInstrPrev, outInstrPrev
                            )
                        )
                        commands.extend(overtravelIn)
                    else:
                        commands.extend(
                            self.getLeadStart(
                                obj, instr, first, inInstrPrev, outInstrPrev
                            )
                        )
                    inInstrPrev = commands[-1]
                    self._guardOutputCount(len(commands))
                first = False

            # Add mill instruction
            commands.append(instr)
            self._guardOutputCount(len(commands))

            # Process Lead-Out
            last = bool(i == self.lastCuttingMoveIndex)
            if last or not self.isCuttingMove(source[i + 1]):
                if obj.LeadOut:
                    # Process negative Offset Lead-Out (cut travel from end)
                    if obj.OffsetOut.Value < 0 and obj.StyleOut != "No Retract":
                        commands = self.cutTravelEnd(commands, abs(obj.OffsetOut.Value))

                    # Process positive Offset Lead-Out (overtravel)
                    elif obj.OffsetOut.Value > 0 and obj.StyleOut != "No Retract":
                        overtravelOut = self.extendTravelOut(obj.OffsetOut.Value)
                        if overtravelOut:
                            commands.extend(overtravelOut)

                    # add lead end and travel moves
                    leadEndInstr = self.getLeadEnd(
                        obj, commands[-1], last, outInstrPrev
                    )
                    commands.extend(leadEndInstr)
                    self._guardOutputCount(len(commands))

                    # Last mill position to check RetractThreshold
                    if leadEndInstr:
                        outInstrPrev = leadEndInstr[-1]
                    else:
                        outInstrPrev = instr

                # preparing for the next profile
                measuredLength = 0
                self.firstMillIndex = None
                self.lastMillIndex = None
                self.invertAlt = (
                    not self.invertAlt if getattr(obj, "InvertAlt", None) else False
                )

            # Workaround for several closed paths without retraction
            if not Path.Geom.isRoughly(instr.positionBegin().z, instr.positionEnd().z):
                # reset firstMillIndex, if move not in XY plane
                self.firstMillIndex = i + 1
            if self.lastMillIndex and i >= self.lastMillIndex:
                # lastMillIndex not correct any more, find new
                self.lastMillIndex = self.findLastCutMultiProfileIndex()
            if self.lastMillIndex and Path.Geom.pointsCoincide(
                instr.positionBegin(), source[self.lastMillIndex].positionEnd()
            ):
                # get firstMillIndex for last closed path
                self.firstMillIndex = i
                self.closedProfile = True

            self._guardOutputCount(len(commands))

        maneuver.addInstructions(commands)
        return maneuver.toPath()


class _QuantityValue:
    __slots__ = ("Value",)

    def __init__(self, value):
        self.Value = float(value)

    def __bool__(self):
        return bool(self.Value)


def _generation_object(base, lead_in, lead_out, retract_threshold, rapid_plunge):
    return SimpleNamespace(
        Base=base,
        LeadIn=lead_in.style is not None,
        LeadOut=lead_out.style is not None,
        StyleIn=lead_in.style or "Vertical",
        StyleOut=lead_out.style or "Vertical",
        AngleIn=_QuantityValue(lead_in.angle_degrees),
        AngleOut=_QuantityValue(lead_out.angle_degrees),
        RadiusIn=_QuantityValue(lead_in.radius_or_length_mm),
        RadiusOut=_QuantityValue(lead_out.radius_or_length_mm),
        InvertIn=lead_in.invert,
        InvertOut=lead_out.invert,
        OffsetIn=_QuantityValue(lead_in.offset_mm),
        OffsetOut=_QuantityValue(lead_out.offset_mm),
        ExtendLeadIn=_QuantityValue(lead_in.extend_mm),
        ExtendLeadOut=_QuantityValue(lead_out.extend_mm),
        RetractThreshold=float(retract_threshold),
        RapidPlunge=rapid_plunge,
    )


def hasUsableMachineParameters(base):
    """Return whether the base can supply safe heights and positive feeds."""

    commands = tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ())
    if not commands or len(commands) > MAX_LEAD_INPUT_COMMANDS:
        return False
    settings = SimpleNamespace(Base=base)
    proxy = object.__new__(ObjectDressup)
    try:
        if not proxy.getPathParams(settings):
            return False
        values = (
            proxy.horizFeed,
            proxy.vertFeed,
            proxy.entranceFeed,
            proxy.exitFeed,
            proxy.clearanceHeight,
            proxy.safeHeight,
            proxy.startDepth,
            proxy.clearanceHeightOut,
        )
        return (
            all(math.isfinite(float(value)) for value in values)
            and proxy.horizFeed > 0.0
            and proxy.vertFeed > 0.0
            and proxy.entranceFeed > 0.0
            and proxy.exitFeed > 0.0
            and proxy.clearanceHeight >= proxy.safeHeight
        )
    except (ArithmeticError, AttributeError, TypeError, ValueError):
        return False


def generatePathWithMetadata(
    base,
    *,
    lead_in,
    lead_out,
    retract_threshold_mm,
    rapid_plunge,
    max_output_commands=MAX_LEAD_OUTPUT_COMMANDS,
):
    """Generate Lead In/Out motion without document or task-panel mutation."""

    normalized_in = normalize_lead_definition(lead_in, "Lead-in")
    normalized_out = normalize_lead_definition(lead_out, "Lead-out")
    retract_threshold = _finite(
        retract_threshold_mm,
        "Lead In/Out retract threshold",
    )
    if not 0.0 <= retract_threshold <= MAX_LEAD_DISTANCE_MM:
        raise ValueError(
            "Lead In/Out retract threshold must be between zero and "
            f"{MAX_LEAD_DISTANCE_MM:g} mm"
        )
    if not isinstance(rapid_plunge, bool):
        raise TypeError("Lead In/Out rapid_plunge must be a boolean")
    if (
        not isinstance(max_output_commands, int)
        or isinstance(max_output_commands, bool)
        or not 1 <= max_output_commands <= MAX_LEAD_OUTPUT_COMMANDS
    ):
        raise ValueError(
            "Lead In/Out max_output_commands must be a positive integer no greater "
            f"than {MAX_LEAD_OUTPUT_COMMANDS}"
        )
    if base is None or not base.isDerivedFrom("Path::Feature"):
        raise ValueError("Lead In/Out base must be one Path feature")
    placed = _path_with_job_center(base, PathUtils.getPathWithPlacement(base))
    source_commands = tuple(placed.Commands or ())
    if not source_commands:
        raise ValueError("Lead In/Out base path is empty")
    if len(source_commands) > MAX_LEAD_INPUT_COMMANDS:
        raise ValueError(
            f"Lead In/Out base has {len(source_commands)} commands; its interactive "
            f"limit is {MAX_LEAD_INPUT_COMMANDS}"
        )

    stats = {
        "source_command_count": len(source_commands),
        "output_command_count": len(source_commands),
        "profile_count": 0,
        "closed_profile_count": 0,
        "open_profile_count": 0,
        "lead_in_profile_count": 0,
        "lead_out_profile_count": 0,
    }
    if normalized_in.style is None and normalized_out.style is None:
        return placed, stats

    settings = _generation_object(
        base,
        normalized_in,
        normalized_out,
        retract_threshold,
        rapid_plunge,
    )
    proxy = object.__new__(ObjectDressup)
    proxy.invertAlt = False
    proxy._generation_stats = stats
    proxy._max_output_commands = max_output_commands
    job = PathUtils.findParentJob(base)
    tolerance = (
        _finite(job.GeometryTolerance, "Lead In/Out geometry tolerance")
        if job is not None
        else 0.01
    )
    if tolerance <= 0.0:
        raise ValueError("Lead In/Out geometry tolerance must be greater than zero")
    proxy.tolerance = tolerance
    if not proxy.getPathParams(settings):
        raise ValueError(
            "Lead In/Out could not determine clearance and safe heights from the base path"
        )
    numeric_machine_values = (
        proxy.horizFeed,
        proxy.vertFeed,
        proxy.entranceFeed,
        proxy.exitFeed,
        proxy.clearanceHeight,
        proxy.safeHeight,
        proxy.startDepth,
        proxy.clearanceHeightOut,
    )
    if not all(math.isfinite(float(value)) for value in numeric_machine_values):
        raise ValueError("Lead In/Out machine heights and feeds must be finite")
    if proxy.horizFeed <= 0.0 or proxy.vertFeed <= 0.0:
        raise ValueError("Lead In/Out requires positive horizontal and vertical feeds")
    if proxy.entranceFeed <= 0.0 or proxy.exitFeed <= 0.0:
        raise ValueError("Lead In/Out requires positive entry and exit feeds")
    if proxy.clearanceHeight < proxy.safeHeight:
        raise ValueError("Lead In/Out clearance height must not be below safe height")

    result = _path_with_job_center(base, proxy.generateLeadInOutCurve(settings))
    output_commands = tuple(result.Commands or ())
    if len(output_commands) > max_output_commands:
        raise ValueError(
            f"Lead In/Out would generate {len(output_commands)} commands; the safety "
            f"limit is {max_output_commands}"
        )
    stats["output_command_count"] = len(output_commands)
    return result, dict(stats)
