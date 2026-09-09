# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assembly validation uses the existing native isolation boundary."""

import json
from pathlib import Path

import pytest

import VibeCADHostIsolation as isolation
import VibeCADVibeScriptDomainRuntime as runtime


def test_adapter_does_not_validate_on_host_interpreter(tmp_path, monkeypatch):
    pack = runtime.contracts.get_vibescript_pack('AssemblyWorkbench')
    prepared = dict(pack=pack, staging=tmp_path, expected_outputs=[],
                    resolved_references=[], worker_request={}, memory_limit_bytes=123456)
    expected = {'ok': True, 'outputs': [], 'assembly_members': []}
    calls = []

    def host_validation(*args):
        raise AssertionError('CPU validation ran on the GUI interpreter')

    def execute(**kwargs):
        calls.append(kwargs)
        request = json.loads((Path(kwargs['staging']) / 'host-validation-input.json').read_text())
        assert request['prepared']['workbench'] == 'AssemblyWorkbench'
        assert request['execution'] == {'ok': True}
        (Path(kwargs['staging']) / 'host-validation-result.json').write_text(json.dumps({
            'request_id': request['request_id'], 'result': expected,
        }))
        return {'started': True, 'returncode': 0}

    monkeypatch.setattr(runtime, 'validate_candidate', host_validation)
    monkeypatch.setattr(isolation, 'execute_staged_script', execute)
    assert runtime.AssemblyDomainAdapter(pack).validate_result(prepared, {'ok': True}) == expected
    assert len(calls) == 1
    assert calls[0]['cpu_slots'] == 1
    assert calls[0]['memory_limit_bytes'] == 123456


def test_session_validation_propagates_cancellation(tmp_path, monkeypatch):
    import VibeCADSession as session

    pack = runtime.contracts.get_vibescript_pack('AssemblyWorkbench')
    prepared = dict(pack=pack, staging=tmp_path, tool_name='vibescript.build_program')
    cancelled = lambda: True

    def execute(**kwargs):
        assert kwargs['cancellation_check'] is cancelled
        return {'started': True, 'returncode': -1, 'cancelled': True}

    monkeypatch.setattr(isolation, 'execute_staged_script', execute)
    with pytest.raises(runtime.DomainRuntimeFailure) as error:
        session._validate_domain_candidate(
            runtime.AssemblyDomainAdapter(pack), prepared, {'ok': True}, cancelled)
    assert error.value.payload['cancelled'] is True
    assert error.value.payload['failure_code'] == 'RUN_CANCELLED'


def test_session_validation_preserves_existing_adapter_contract():
    import VibeCADSession as session

    class ExistingAdapter:
        def validate_result(self, prepared, execution):
            return prepared, execution

    prepared, execution = {}, {'ok': True}
    assert session._validate_domain_candidate(
        ExistingAdapter(), prepared, execution, None) == (prepared, execution)


@pytest.mark.parametrize('response,exception,message', [
    ({'request_id': 'old-request', 'result': {'ok': True}}, ValueError, 'different request'),
    ({'result': {'ok': False}}, ValueError, 'no validated result'),
    ({'error': {'type': 'ValueError', 'message': 'bad pose'}}, ValueError, 'bad pose'),
    ({'error': {'type': 'TypeError', 'message': 'bad definition'}}, TypeError, 'bad definition'),
])
def test_isolated_validation_rejects_bad_results(tmp_path, monkeypatch, response, exception, message):
    pack = runtime.contracts.get_vibescript_pack('AssemblyWorkbench')

    def execute(**kwargs):
        request = json.loads((tmp_path / 'host-validation-input.json').read_text())
        (tmp_path / 'host-validation-result.json').write_text(json.dumps(
            {'request_id': request['request_id'], **response}))
        return {'started': True, 'returncode': 0}

    monkeypatch.setattr(isolation, 'execute_staged_script', execute)
    with pytest.raises(exception, match=message):
        runtime.AssemblyDomainAdapter(pack).validate_result(
            dict(pack=pack, staging=tmp_path), {'ok': True})


def test_worker_failure_cannot_accept_previous_result(tmp_path, monkeypatch):
    pack = runtime.contracts.get_vibescript_pack('AssemblyWorkbench')
    (tmp_path / 'host-validation-result.json').write_text(json.dumps({'result': {'ok': True}}))
    monkeypatch.setattr(isolation, 'execute_staged_script', lambda **kwargs:
                        {'started': True, 'returncode': 1, 'error': 'worker stopped'})
    with pytest.raises(RuntimeError, match='worker stopped'):
        runtime.AssemblyDomainAdapter(pack).validate_result(
            dict(pack=pack, staging=tmp_path), {'ok': True})
