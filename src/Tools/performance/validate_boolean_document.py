"""Read-only geometry check of a generated workload after packaged GUI round-trip."""
import json
import math
import os
from pathlib import Path

import FreeCAD as App
import Part


source = Path(os.environ['VIBECAD_VALIDATE_DOCUMENT']).resolve()
expected = json.loads(Path(os.environ['VIBECAD_EXPECTED_GEOMETRY']).read_text(encoding='utf-8'))
document = App.openDocument(str(source))
try:
    if len(document.Objects) != expected['objects']:
        raise RuntimeError('Object count changed during round-trip')
    for name, shape in expected['expected_results'].items():
        obj = document.getObject(name)
        if obj is None or obj.Shape.isNull() or len(obj.Shape.Solids) != 1:
            raise RuntimeError(f'{name} did not retain one solid')
        if not math.isclose(obj.Shape.Volume, shape['volume'], rel_tol=1e-8):
            raise RuntimeError(f'{name} volume changed during round-trip')
        if len(obj.Shape.Faces) != shape['faces']:
            raise RuntimeError(f'{name} face count changed during round-trip')
    print('VIBECAD_GEOMETRY_ROUNDTRIP ' + json.dumps({
        'ok': True, 'objects': len(document.Objects),
        'validated_solids': len(expected['expected_results']),
    }), flush=True)
finally:
    App.closeDocument(document.Name)
