"""Pipeline request validation without Docker or server side effects."""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml


def load_module(relative, name):
    path = Path(__file__).resolve().parents[1] / 'DockerPilotExtras' / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resources(data, tmp_path):
    generator = load_module('utils/pipeline_generator.py', 'pipeline_test_generator')
    module = load_module('backend/resources/pipeline.py', 'pipeline_test_resources')
    save = Mock()
    classes = module.create_pipeline_resources(
        Resource=object,
        app=SimpleNamespace(config={'PIPELINES_DIR': tmp_path, 'CONFIG_DIR': tmp_path}),
        request=SimpleNamespace(get_json=lambda **kwargs: data),
        datetime_cls=datetime,
        PipelineGenerator=generator.PipelineGenerator,
        parse_env_vars=generator.parse_env_vars,
        generate_deployment_config_for_environment=Mock(),
        save_deployment_config=save,
        append_deployment_history_data=Mock(),
        get_deployment_history_data=Mock(),
    )
    return classes, save


@pytest.mark.parametrize('resource_index', [0, 3], ids=['generate', 'integrate'])
@pytest.mark.parametrize('data,field', [
    (None, 'body'), ([], 'body'), ('text', 'body'),
    ({'type': 'unknown'}, 'type'),
    ({'stages': []}, 'stages'),
    ({'stages': 'build'}, 'stages'),
    ({'stages': ['build', 'unknown']}, 'stages'),
    ({'stages': [None]}, 'stages'),
    ({'project_name': '  '}, 'project_name'),
    ({'docker_image': None}, 'docker_image'),
    ({'dockerfile': []}, 'dockerfile'),
    ({'env_vars': {}}, 'env_vars'),
    ({'test_commands': ''}, 'test_commands'),
    ({'test_commands': [None]}, 'test_commands'),
    ({'enable_environments': 'false'}, 'enable_environments'),
    ({'stages': ['smoke'], 'smoke_test_url': 'https://example.com'}, 'stages'),
    ({'stages': ['deploy', 'smoke'], 'smoke_test_url': 'file:///etc/passwd'}, 'smoke_test_url'),
    ({'stages': ['deploy', 'smoke'], 'smoke_test_url': 'https://['}, 'smoke_test_url'),
    ({'stages': ['deploy', 'smoke'], 'smoke_test_url': None}, 'smoke_test_url'),
    ({'type': 'jenkins', 'stages': ['deploy', 'smoke'], 'smoke_test_url': 'https://{env}.example.com'}, 'smoke_test_url'),
])
def test_invalid_requests_return_field_errors_without_writes(data, field, resource_index, tmp_path):
    classes, save = resources(data, tmp_path)
    payload, status = classes[resource_index]().post()
    assert status == 400
    assert field in payload['fields']
    save.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('retries', [0, 61, -1, True, 2.5, '1.5', None, 'many', '9' * 5000])
def test_invalid_smoke_retries(retries, tmp_path):
    classes, _ = resources({'stages': ['deploy', 'smoke'],
                            'smoke_test_url': 'https://example.com/health',
                            'smoke_test_retries': retries}, tmp_path)
    payload, status = classes[0]().post()
    assert status == 400
    assert 'smoke_test_retries' in payload['fields']


def test_gitlab_canonical_stage_order_and_custom_commands(tmp_path):
    classes, save = resources({'stages': ['scan', 'test', 'build', 'test'],
                               'test_commands': ['pytest -q']}, tmp_path)
    result = classes[0]().post()
    parsed = yaml.safe_load(result['content'])
    assert result['filename'] == '.gitlab-ci.yml'
    assert parsed['stages'] == ['build', 'test', 'scan']
    assert 'deploy' not in parsed and 'deploy:dev' not in parsed
    assert any('pytest -q' in command for command in parsed['test']['script'])
    save.assert_not_called()


def test_default_generation_still_supported(tmp_path):
    classes, _ = resources({}, tmp_path)
    assert classes[0]().post()['success'] is True


def test_valid_jenkins_ci_only(tmp_path):
    classes, _ = resources({'type': 'jenkins', 'stages': ['build', 'test', 'scan'],
                            'test_commands': 'pytest -q\npython -m compileall src'}, tmp_path)
    result = classes[0]().post()
    assert result['filename'] == 'Jenkinsfile'
    assert "stage('Deploy" not in result['content']
    assert 'pytest -q' in result['content']


def test_valid_gitlab_smoke_template_and_string_retries(tmp_path):
    classes, _ = resources({'stages': ['smoke', 'deploy', 'build'],
                            'smoke_test_url': 'https://{env}.example.com/health',
                            'smoke_test_retries': '3'}, tmp_path)
    result = classes[0]().post()
    parsed = yaml.safe_load(result['content'])
    assert parsed['stages'] == ['build', 'deploy', 'smoke']
    assert 'SMOKE_RETRIES=3' in parsed['smoke:dev']['script']
    assert 'SMOKE_URL="https://dev.example.com/health"' in parsed['smoke:dev']['script']
