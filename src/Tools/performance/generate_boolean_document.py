"""Generate a disposable independent-branch CAD workload, never a customer file."""
import json
import math
import os
from pathlib import Path
import time

import FreeCAD as App
import Part

output = Path(os.environ['VIBECAD_GENERATED_DOCUMENT']).resolve()
if output.exists():
    raise RuntimeError('Refusing to replace an existing generated document')
output.parent.mkdir(parents=True, exist_ok=True)
started = time.perf_counter()
doc = App.newDocument('ParallelBoolean600')
expected = {}
for index in range(200):
    length = 10 + index % 5
    width = 12 + index % 7
    height = 14 + index % 3
    x, y = (index % 20) * 25, (index // 20) * 30
    box = doc.addObject('Part::Box', f'Blank{index:03d}')
    box.Length, box.Width, box.Height = length, width, height
    box.Placement.Base = App.Vector(x, y, 0)
    cylinder = doc.addObject('Part::Cylinder', f'Bore{index:03d}')
    cylinder.Radius, cylinder.Height = 2, height + 2
    cylinder.Placement.Base = App.Vector(x + length / 2, y + width / 2, -1)
    result = doc.addObject('Part::Cut', f'Result{index:03d}')
    result.Base, result.Tool = box, cylinder
    box.Visibility, cylinder.Visibility = False, False
    expected[result.Name] = height * (length * width - math.pi * 4)

before = time.perf_counter()
cpu_before = os.times()
doc.recompute()
recompute_seconds = time.perf_counter() - before
cpu_after = os.times()
recompute_cpu_seconds = (cpu_after.user + cpu_after.system
                         - cpu_before.user - cpu_before.system)
actual = {}
for name, volume in expected.items():
    obj = doc.getObject(name)
    if obj.Shape.isNull() or len(obj.Shape.Solids) != 1:
        raise RuntimeError(f'{name} did not produce one solid')
    if not math.isclose(obj.Shape.Volume, volume, rel_tol=1e-8):
        raise RuntimeError(f'{name} volume mismatch: {obj.Shape.Volume} != {volume}')
    actual[name] = {'volume': obj.Shape.Volume, 'faces': len(obj.Shape.Faces)}
doc.saveAs(str(output))
report = {
    'objects': len(doc.Objects), 'independent_branches': len(expected),
    'recompute_seconds': recompute_seconds,
    'recompute_cpu_seconds': recompute_cpu_seconds,
    'average_cpu_cores': recompute_cpu_seconds / recompute_seconds,
    'total_seconds': time.perf_counter() - started,
    'runtime': dict(App.hostRuntimeStatus()), 'expected_results': actual,
}
output.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('VIBECAD_GENERATED_WORKLOAD ' + json.dumps({key: value for key, value in report.items()
                                               if key != 'expected_results'}), flush=True)
App.closeDocument(doc.Name)
