# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create two unrelated source parts for the Native CAM setup corpus."""

from __future__ import annotations

import os
from pathlib import Path

import FreeCAD as App
import Part


def _bracket() -> Part.Shape:
    shape = Part.makeBox(60.0, 40.0, 12.0)
    shape = shape.cut(
        Part.makeBox(32.0, 18.0, 7.0, App.Vector(14.0, 11.0, 6.0))
    )
    for x_mm, y_mm in ((8.0, 8.0), (52.0, 8.0), (8.0, 32.0), (52.0, 32.0)):
        shape = shape.cut(
            Part.makeCylinder(2.5, 14.0, App.Vector(x_mm, y_mm, -1.0))
        )
    return shape.removeSplitter()


def _plate() -> Part.Shape:
    origin = App.Vector(90.0, 0.0, 0.0)
    shape = Part.makeBox(70.0, 45.0, 8.0, origin)
    for x_mm, y_mm in ((100.0, 10.0), (150.0, 10.0), (100.0, 35.0), (150.0, 35.0)):
        shape = shape.cut(
            Part.makeCylinder(3.0, 10.0, App.Vector(x_mm, y_mm, -1.0))
        )
    return shape.removeSplitter()


def create_fixture(output_path: Path) -> Path:
    output = output_path.expanduser().resolve()
    if output.suffix.casefold() != ".fcstd":
        raise ValueError("The CAM benchmark fixture output must be an FCStd file.")
    output.parent.mkdir(parents=True, exist_ok=True)

    document = App.newDocument("CamIndependentSetupsSource")
    document.UndoMode = 1
    try:
        shapes = (_bracket(), _plate())
        if any(
            shape.isNull() or not shape.isValid() or len(shape.Solids) != 1
            for shape in shapes
        ):
            raise RuntimeError("A multi-setup benchmark source is not one valid solid.")
        document.openTransaction("Create independent CAM source parts")
        models = []
        for name, label, shape in (
            ("BracketModel", "Independent bracket", shapes[0]),
            ("PlateModel", "Independent hole plate", shapes[1]),
        ):
            model = document.addObject("Part::Feature", name)
            model.Label = label
            model.Shape = shape
            document.publishProvisionalTimelineOperationBlock(model, (), ())
            models.append(model)
        if document.recompute(tuple(models), True, True) is False:
            raise RuntimeError("The multi-setup CAM benchmark did not recompute.")
        document.commitTransaction()
        document.saveAs(str(output))
        Part.export(models, str(output.with_suffix(".step")))
        return output
    finally:
        App.closeDocument(document.Name)


if __name__ == "__main__":
    destination = Path(
        os.environ.get("VIBECAD_CAM_MULTI_SETUP_FIXTURE")
        or "/tmp/vibecad-cam-independent-setups.FCStd"
    )
    print(create_fixture(destination), flush=True)
