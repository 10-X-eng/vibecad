# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create the reusable source-only three-axis CAM bracket benchmark."""

from __future__ import annotations

import os
from pathlib import Path

import FreeCAD as App
import Part


def create_fixture(output_path: Path) -> Path:
    output = output_path.expanduser().resolve()
    if output.suffix.casefold() != ".fcstd":
        raise ValueError("The CAM benchmark fixture output must be an FCStd file.")
    output.parent.mkdir(parents=True, exist_ok=True)

    document = App.newDocument("CamBracketBenchmarkSource")
    document.UndoMode = 1
    try:
        shape = Part.makeBox(60.0, 40.0, 12.0)
        shape = shape.cut(
            Part.makeBox(
                32.0,
                18.0,
                7.0,
                App.Vector(14.0, 11.0, 6.0),
            )
        )
        for x_mm, y_mm in ((8.0, 8.0), (52.0, 8.0), (8.0, 32.0), (52.0, 32.0)):
            shape = shape.cut(
                Part.makeCylinder(
                    2.5,
                    14.0,
                    App.Vector(x_mm, y_mm, -1.0),
                )
            )
        shape = shape.removeSplitter()
        if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
            raise RuntimeError("The CAM bracket benchmark is not one valid solid.")

        document.openTransaction("Create CAM bracket benchmark source")
        model = document.addObject("Part::Feature", "BracketModel")
        model.Label = "Three-axis bracket model"
        model.Shape = shape
        document.publishProvisionalTimelineOperationBlock(model, (), ())
        if document.recompute((model,), True, True) is False:
            raise RuntimeError("The CAM bracket benchmark did not recompute.")
        document.commitTransaction()
        document.saveAs(str(output))
        shape.exportStep(str(output.with_suffix(".step")))
        return output
    finally:
        App.closeDocument(document.Name)


if __name__ == "__main__":
    destination = Path(
        os.environ.get("VIBECAD_CAM_BENCHMARK_FIXTURE")
        or "/tmp/vibecad-cam-bracket-source.FCStd"
    )
    print(create_fixture(destination), flush=True)
