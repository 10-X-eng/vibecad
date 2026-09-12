# SPDX-License-Identifier: LGPL-2.1-or-later

"""Definition validity must not depend on an arbitrary serialized byte quota."""

import importlib
import json

import pytest

from vibescript_domain_api import DomainValue
from vibescript_domain_worker import _payload


ENCODERS = ("fem", "meshpart", "cam", "inspection", "reverse_engineering",
            "robot", "techdraw", "points")


def test_shared_output_accepts_large_nested_definition():
    child = DomainValue("part", "box", "solid", (1, 2, 3), {"label": "x" * 4096})
    value = DomainValue("part", "compound", "compound", (tuple([child] * 300),), {})
    expected = value.to_payload()
    assert len(json.dumps(expected)) > 1_000_000
    assert _payload(value) == expected
    assert _payload(expected, serialized=True) == expected


@pytest.mark.parametrize("domain", ENCODERS)
def test_domain_encoder_accepts_large_definition(domain):
    worker = importlib.import_module(f"vibescript_{domain}_worker")
    value = {"definition": "x" * (4 * 1024 * 1024 + 1)}
    assert json.loads(worker._encoded(value)) == value


@pytest.mark.parametrize("domain", ENCODERS)
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_domain_encoder_still_rejects_invalid_json(domain, invalid):
    worker = importlib.import_module(f"vibescript_{domain}_worker")
    with pytest.raises(RuntimeError, match="JSON"):
        worker._encoded({"invalid": invalid})


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_shared_output_still_rejects_invalid_json(invalid):
    with pytest.raises((ValueError, TypeError)):
        _payload(DomainValue("part", "box", "solid", (invalid,), {}))


@pytest.mark.parametrize("domain", ["fem", "cam", "techdraw"])
def test_explicit_native_readback_limit_is_preserved(domain):
    worker = importlib.import_module(f"vibescript_{domain}_worker")
    with pytest.raises(RuntimeError, match="exceeds 10 JSON bytes"):
        worker._encoded({"readback": "x" * 20}, limit=10)


def test_mesh_revalidation_accepts_definition_above_old_ceiling():
    from vibescript_domain_api import create_domain_api
    from VibeCADVibeScriptDomains import get_vibescript_pack
    from vibescript_mesh_worker import validate_mesh_definition

    pack = get_vibescript_pack("MeshWorkbench")
    api = create_domain_api(pack.domain, pack.api_exports, pack.output_types)
    triangles = [[[i + 0.123456789, 0, 0], [i + 0.123456789, 1, 0],
                  [i + 0.123456789, 0, 1]] for i in range(15_000)]
    value = api.mesh(triangles)
    assert len(json.dumps(value.to_payload())) > 1_000_000
    assert validate_mesh_definition(value, require_domain_value=True) == value.to_payload()
