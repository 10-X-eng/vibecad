# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

################################################################################
#                                                                              #
#   FreeCAD is free software: you can redistribute it and/or modify            #
#   it under the terms of the GNU Lesser General Public License as             #
#   published by the Free Software Foundation, either version 2.1              #
#   of the License, or (at your option) any later version.                     #
#                                                                              #
#   FreeCAD is distributed in the hope that it will be useful,                 #
#   but WITHOUT ANY WARRANTY; without even the implied warranty                #
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                    #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with FreeCAD. If not, see https://www.gnu.org/licenses       #
#                                                                              #
################################################################################

__title__ = "CAM Steep/Shallow 3D Finishing Operation"
__author__ = "FreeCAD CAM developers"
__url__ = "https://www.freecad.org"
__doc__ = "Class and implementation of the Steep/Shallow 3D finishing operation."

import math

import FreeCAD
from PySide import QtCore

import Path
import Path.Op.Base as PathOp
import Path.Op.SurfaceSupport as SurfaceSupport
import Path.Base.Generator.steep_shallow as steep_shallow
import PathScripts.PathUtils as PathUtils

# numpy is a hard dependency of the generator; import here so sampling
# can build the heightfield grid.
import numpy

# OCL is loaded lazily so the module can be imported in environments
# without OCL (for non-execute access like loading saved documents).
try:
    import ocl  # noqa: F401  (preferred name on some packagings)
except ImportError:  # pragma: no cover
    try:
        import opencamlib as ocl  # noqa: F401
    except ImportError:  # pragma: no cover
        ocl = None


translate = FreeCAD.Qt.translate


if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


class ObjectSteepShallow(PathOp.ObjectOp):
    """Steep/shallow 3-D finishing operation.

    Classifies the model surface by local slope against SlopeThreshold:
    steep regions receive constant-Z contour (waterline-style) passes;
    shallow regions receive surface-following parallel passes. One
    operation, both strategies, blended at the classification boundary
    by BoundaryOverlap.
    """

    def opFeatures(self, obj):
        return (
            PathOp.FeatureTool
            | PathOp.FeatureDepths
            | PathOp.FeatureHeights
            | PathOp.FeatureStepDown
            | PathOp.FeatureCoolant
        )

    def initOperation(self, obj):
        self.propertiesReady = False
        self.initOpProperties(obj)

    def opOnDocumentRestored(self, obj):
        self.propertiesReady = False
        self.initOpProperties(obj, warn=True)

    def initOpProperties(self, obj, warn=False):
        Path.Log.track()
        self.addNewProps = list()
        for prtyp, nm, grp, tt in self.opPropertyDefinitions():
            if not hasattr(obj, nm):
                obj.addProperty(prtyp, nm, grp, tt)
                self.addNewProps.append(nm)

        if len(self.addNewProps) > 0:
            ENUMS = self.propertyEnumerations()
            for n in ENUMS:
                if n[0] in self.addNewProps:
                    setattr(obj, n[0], n[1])
            if warn:
                msg = translate("CAM_SteepShallow", "New property added to")
                msg += ' "{}": {}'.format(obj.Label, self.addNewProps) + ". "
                msg += translate("CAM_SteepShallow", "Check default value(s).")
                FreeCAD.Console.PrintWarning(msg + "\n")

        self.propertiesReady = True

    def opPropertyDefinitions(self):
        return [
            (
                "App::PropertyAngle",
                "SlopeThreshold",
                "SteepShallow",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Slope angle (degrees) separating steep from shallow "
                    "regions. Steep regions get constant-Z contour passes; "
                    "shallow regions get surface-following parallel passes.",
                ),
            ),
            (
                "App::PropertyDistance",
                "StepOver",
                "SteepShallow",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Lateral spacing between surface-following passes in shallow regions.",
                ),
            ),
            (
                "App::PropertyDistance",
                "BoundaryOverlap",
                "SteepShallow",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Distance the shallow passes extend past the steep/shallow "
                    "boundary so the two strategies overlap without leaving an "
                    "unmachined stripe.",
                ),
            ),
            (
                "App::PropertyEnumeration",
                "CutMode",
                "SteepShallow",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property", "Climb or Conventional cutting direction."
                ),
            ),
            (
                "App::PropertyDistance",
                "SampleInterval",
                "SteepShallow",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Grid spacing of the drop-cutter surface sampling. Smaller "
                    "values follow the surface more accurately but compute "
                    "more slowly.",
                ),
            ),
            (
                "App::PropertyBool",
                "UseRestMachining",
                "Rest Machining",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Only cut material a previous, larger tool could not reach. "
                    "Passes are restricted to regions the reference tool left "
                    "uncut (inside corners and tight fillets).",
                ),
            ),
            (
                "App::PropertyDistance",
                "RestReferenceToolDiameter",
                "Rest Machining",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Diameter of the previous (larger) tool used to identify "
                    "leftover material for rest machining.",
                ),
            ),
            (
                "App::PropertyDistance",
                "LinearDeflection",
                "Mesh",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Tessellation linear deflection. Smaller = finer mesh.",
                ),
            ),
            (
                "App::PropertyDistance",
                "AngularDeflection",
                "Mesh",
                QtCore.QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Tessellation angular deflection. Smaller = finer mesh.",
                ),
            ),
        ]

    @classmethod
    def propertyEnumerations(cls, dataType="data"):
        Path.Log.track()
        enums = {
            "CutMode": [
                (translate("CAM_SteepShallow", "Climb"), "Climb"),
                (translate("CAM_SteepShallow", "Conventional"), "Conventional"),
            ],
        }
        if dataType == "raw":
            return enums
        data = list()
        idx = 0 if dataType == "translated" else 1
        for k in enums:
            data.append((k, [tup[idx] for tup in enums[k]]))
        return data

    def opPropertyDefaults(self, obj, job):
        return {
            "SlopeThreshold": 45.0,
            "StepOver": 1.0,
            "BoundaryOverlap": 0.5,
            "CutMode": "Climb",
            "SampleInterval": 1.0,
            "UseRestMachining": False,
            "RestReferenceToolDiameter": 12.0,
            "LinearDeflection": 0.1,
            "AngularDeflection": 0.524,
        }

    def opSetDefaultValues(self, obj, job):
        Path.Log.track()
        defaults = self.opPropertyDefaults(obj, job)
        for name, value in defaults.items():
            try:
                setattr(obj, name, value)
            except Exception as e:
                Path.Log.warning(
                    "SteepShallow: failed to set default for {}: {}".format(name, e)
                )

    # ------------------------------------------------------------------
    # Heightfield sampling
    # ------------------------------------------------------------------

    @staticmethod
    def _drop_floor(stl):
        """A Z value safely below the model so 'no contact' is detectable."""
        bb = stl.bb
        span = max(
            abs(bb.maxpt.x - bb.minpt.x),
            abs(bb.maxpt.y - bb.minpt.y),
            abs(bb.maxpt.z - bb.minpt.z),
        )
        return bb.minpt.z - max(span * 10.0, 1000.0)

    def _sample_heightfield(self, stl, cutter, xs, ys):
        """Drop-cutter sample the CL heightfield over the (xs, ys) grid.

        Returns a numpy array of shape (len(xs), len(ys)); NaN where the
        cutter found no surface contact.
        """
        floor = self._drop_floor(stl)
        bdc = ocl.BatchDropCutter()
        bdc.setSTL(stl)
        bdc.setCutter(cutter)
        for x in xs:
            for y in ys:
                bdc.appendPoint(ocl.CLPoint(float(x), float(y), floor))
        bdc.run()
        results = bdc.getCLPoints()

        n_x = len(xs)
        n_y = len(ys)
        z = numpy.full((n_x, n_y), float("nan"), dtype=float)
        for idx, p in enumerate(results):
            if idx >= n_x * n_y:
                break
            i = idx // n_y
            j = idx % n_y
            if p.z > floor + 1e-9:
                z[i, j] = p.z
        return z

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def opExecute(self, obj):
        Path.Log.track()
        if ocl is None:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow",
                    "Steep/Shallow requires OpenCamLib (OCL), which is not available.",
                )
            )
            return

        job = PathUtils.findParentJob(obj)
        if job is None:
            Path.Log.error(
                translate("CAM_SteepShallow", "Steep/Shallow: no parent Job.")
            )
            return

        models = job.Model.Group if hasattr(job.Model, "Group") else []
        if not models:
            Path.Log.error(
                translate("CAM_SteepShallow", "Steep/Shallow: Job has no model.")
            )
            return
        model = models[0]

        sample_interval = float(obj.SampleInterval.Value)
        if sample_interval <= 0.0:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow",
                    "Steep/Shallow: SampleInterval must be positive.",
                )
            )
            return

        step_over = float(obj.StepOver.Value)
        if step_over <= 0.0:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow", "Steep/Shallow: StepOver must be positive."
                )
            )
            return

        step_down = float(obj.StepDown.Value)
        if step_down <= 0.0:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow", "Steep/Shallow: StepDown must be positive."
                )
            )
            return

        rest_reference_diameter = None
        if obj.UseRestMachining:
            rest_reference_diameter = float(obj.RestReferenceToolDiameter.Value)
            if rest_reference_diameter <= 0.0:
                Path.Log.error(
                    translate(
                        "CAM_SteepShallow",
                        "Steep/Shallow: RestReferenceToolDiameter must be positive.",
                    )
                )
                return
            if rest_reference_diameter <= float(self.radius) * 2.0:
                Path.Log.warning(
                    translate(
                        "CAM_SteepShallow",
                        "Steep/Shallow: rest machining reference tool is not larger "
                        "than the current tool; little or no material will remain "
                        "to cut.",
                    )
                )

        # Tessellate the model for OCL.
        try:
            stl = SurfaceSupport._makeSTL(model, obj, ocl)
        except Exception as e:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow", "Steep/Shallow: failed to tessellate model:"
                )
                + " {}".format(e)
            )
            return

        # Build the OCL cutter from the tool controller.
        oclt = SurfaceSupport.OCL_Tool(ocl, obj, safe=False)
        oclt.setFromTool(self.tool) if hasattr(oclt, "setFromTool") else None
        cutter = oclt.getOclTool() if hasattr(oclt, "getOclTool") else None
        if not cutter:
            # Fallback to a flat cutter sized to the tool diameter.
            cutter = ocl.CylCutter(max(self.radius * 2.0, 0.5), 50.0)

        # Sampling grid: model bounding box expanded by the tool radius
        # so the cutter can roll off vertical walls at the model edge.
        shape = model.Shape
        bb = shape.BoundBox
        margin = float(self.radius)
        x_min = bb.XMin - margin
        x_max = bb.XMax + margin
        y_min = bb.YMin - margin
        y_max = bb.YMax + margin

        n_x = max(2, int(math.ceil((x_max - x_min) / sample_interval)) + 1)
        n_y = max(2, int(math.ceil((y_max - y_min) / sample_interval)) + 1)
        xs = numpy.linspace(x_min, x_max, n_x)
        ys = numpy.linspace(y_min, y_max, n_y)

        try:
            heightfield = self._sample_heightfield(stl, cutter, xs, ys)
        except Exception as e:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow", "Steep/Shallow: drop-cutter sampling failed:"
                )
                + " {}".format(e)
            )
            return

        # Depth handling: no-contact samples and everything below
        # FinalDepth are clamped to FinalDepth so the cut never exceeds
        # the operation's depth limit.
        final_depth = float(obj.FinalDepth.Value)
        heightfield = numpy.where(numpy.isfinite(heightfield), heightfield, final_depth)
        heightfield = numpy.maximum(heightfield, final_depth)

        # SafeHeight must clear the sampled surface; lift it if the CL
        # heights (surface + cutter geometry) exceed the configured value.
        safe_height = float(obj.SafeHeight.Value)
        z_max = float(heightfield.max())
        if safe_height < z_max:
            Path.Log.warning(
                "Steep/Shallow: SafeHeight {:.3f} is below the sampled surface "
                "maximum {:.3f}; raising rapids to clear it.".format(safe_height, z_max)
            )
            safe_height = z_max + 1.0

        # Feed rates: the generator requires positive feeds. Fall back
        # to a conservative default when the tool controller has none
        # configured, so a missing feed setting degrades gracefully
        # instead of producing an empty path.
        horiz_feed = float(self.horizFeed)
        vert_feed = float(self.vertFeed)
        if horiz_feed <= 0.0 or vert_feed <= 0.0:
            Path.Log.warning(
                "Steep/Shallow: tool controller has no feed rates configured; "
                "using 100 mm/min. Set feeds on the tool controller."
            )
            horiz_feed = horiz_feed if horiz_feed > 0.0 else 100.0
            vert_feed = vert_feed if vert_feed > 0.0 else 100.0

        try:
            commands = steep_shallow.generate(
                heightfield,
                xs,
                ys,
                slope_threshold=float(obj.SlopeThreshold.Value),
                stepdown=step_down,
                stepover=step_over,
                safe_height=safe_height,
                horiz_feed=horiz_feed,
                vert_feed=vert_feed,
                boundary_overlap=float(obj.BoundaryOverlap.Value),
                direction=str(obj.CutMode),
                rest_reference_diameter=rest_reference_diameter,
            )
        except (TypeError, ValueError) as e:
            Path.Log.error(
                translate(
                    "CAM_SteepShallow", "Steep/Shallow: toolpath generation failed:"
                )
                + " {}".format(e)
            )
            return

        for cmd in commands:
            self.commandlist.append(cmd)


def SetupProperties():
    """Property names the Setup Sheet may persist defaults for."""
    return [
        "SlopeThreshold",
        "StepOver",
        "BoundaryOverlap",
        "CutMode",
        "SampleInterval",
        "UseRestMachining",
        "RestReferenceToolDiameter",
        "LinearDeflection",
        "AngularDeflection",
    ]


def Create(name, obj=None, parentJob=None, toolController=None):
    """Factory used by the Op-Gui SetupOperation."""
    obj = PathOp.createOperationObject(name, obj, parentJob)
    obj.Proxy = ObjectSteepShallow(
        obj,
        name,
        parentJob,
        toolController=toolController,
    )
    return obj
