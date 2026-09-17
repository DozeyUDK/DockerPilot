"""Pipeline request validation without Docker or server side effects."""

import importlib.util
import os
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


def resources(data, tmp_path, generator_class=None):
    generator = load_module('utils/pipeline_generator.py', 'pipeline_test_generator')
    module = load_module('backend/resources/pipeline.py', 'pipeline_test_resources')
    save = Mock()
    classes = module.create_pipeline_resources(
        Resource=object,
        app=SimpleNamespace(config={'PIPELINES_DIR': tmp_path, 'CONFIG_DIR': tmp_path}),
        request=SimpleNamespace(get_json=lambda **kwargs: data),
        datetime_cls=datetime,
        PipelineGenerator=generator_class or generator.PipelineGenerator,
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


@pytest.mark.parametrize('filename,pipeline_type', [
    ('.gitlab-ci.yml', 'gitlab'),
    ('Jenkinsfile', 'jenkins'),
    ('pipeline.yml', 'generic'),
])
def test_saved_pipeline_round_trip(filename, pipeline_type, tmp_path):
    content = f'# {pipeline_type}\nstages: [build]\n'
    classes, _ = resources({'filename': filename, 'content': content}, tmp_path)
    saved = classes[1]().post()
    assert saved['success'] is True
    assert saved['filename'] == filename
    assert saved['path'] == filename
    assert not Path(saved['path']).is_absolute()

    payload = classes[7]().get(filename)
    assert payload['success'] is True
    assert payload['pipeline']['filename'] == filename
    assert payload['pipeline']['type'] == pipeline_type
    assert payload['pipeline']['content'] == content
    assert payload['pipeline']['size_bytes'] == len(content.encode())
    assert len(payload['pipeline']['sha256']) == 64
    assert payload['pipeline']['modified_at'].endswith('Z')


def test_pipeline_library_lists_only_supported_regular_files(tmp_path):
    (tmp_path / '.gitlab-ci.yml').write_text('stages: [build]\n', encoding='utf-8')
    (tmp_path / 'Jenkinsfile').write_text('pipeline {}\n', encoding='utf-8')
    (tmp_path / 'notes.txt').write_text('secret', encoding='utf-8')
    (tmp_path / 'pipeline.yml').mkdir()

    classes, _ = resources({}, tmp_path)
    payload = classes[7]().get()
    assert [item['filename'] for item in payload['pipelines']] == [
        '.gitlab-ci.yml', 'Jenkinsfile'
    ]
    assert all('content' not in item for item in payload['pipelines'])


@pytest.mark.parametrize('filename', [
    '../outside.yml', 'subdir/pipeline.yml', r'..\outside.yml', '/etc/passwd',
    'custom.yml', 'pipeline.yaml', '', None,
])
def test_pipeline_save_rejects_unsupported_or_unsafe_names(filename, tmp_path):
    classes, _ = resources({'filename': filename, 'content': 'stages: []'}, tmp_path)
    payload, status = classes[1]().post()
    assert status == 400
    assert 'Unsupported pipeline filename' in payload['error']
    assert not (tmp_path.parent / 'outside.yml').exists()


def test_pipeline_save_defaults_to_generic_filename(tmp_path):
    classes, _ = resources({'content': 'stages: []\n'}, tmp_path)
    payload = classes[1]().post()
    assert payload['filename'] == 'pipeline.yml'
    assert (tmp_path / 'pipeline.yml').read_text(encoding='utf-8') == 'stages: []\n'


def test_pipeline_library_rejects_missing_symlink_large_and_invalid_utf8(tmp_path):
    classes, _ = resources({}, tmp_path)
    missing, status = classes[7]().get('Jenkinsfile')
    assert status == 404
    assert missing['error'] == 'Pipeline not found.'

    outside = tmp_path.parent / 'outside-pipeline.yml'
    outside.write_text('outside', encoding='utf-8')
    link = tmp_path / 'pipeline.yml'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable on this platform')
    _, status = classes[7]().get('pipeline.yml')
    assert status == 404
    link.unlink()

    (tmp_path / 'pipeline.yml').write_bytes(b'x' * (1024 * 1024 + 1))
    _, status = classes[7]().get('pipeline.yml')
    assert status == 413
    (tmp_path / 'pipeline.yml').write_bytes(b'\xff\xfe')
    _, status = classes[7]().get('pipeline.yml')
    assert status == 422


def test_pipeline_save_rejects_symlink_and_oversized_content(tmp_path):
    outside = tmp_path.parent / 'outside-save.yml'
    outside.write_text('unchanged', encoding='utf-8')
    target = tmp_path / 'pipeline.yml'
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable on this platform')
    classes, _ = resources({'content': 'replacement'}, tmp_path)
    payload, status = classes[1]().post()
    assert status == 400
    assert outside.read_text(encoding='utf-8') == 'unchanged'
    target.unlink()

    classes, _ = resources({'content': 'x' * (1024 * 1024 + 1)}, tmp_path)
    payload, status = classes[1]().post()
    assert status == 413
    assert not target.exists()


def test_pipeline_library_rejects_fifo_without_blocking(tmp_path):
    if not hasattr(os, 'mkfifo'):
        pytest.skip('FIFO files are unavailable on this platform')
    fifo = tmp_path / 'pipeline.yml'
    os.mkfifo(fifo)
    classes, _ = resources({}, tmp_path)

    payload, status = classes[7]().get('pipeline.yml')
    assert status == 404
    assert payload['error'] == 'Pipeline not found.'
    assert classes[7]().get()['pipelines'] == []


def test_pipeline_integration_validates_artifact_before_deployment_writes(tmp_path):
    outside = tmp_path.parent / 'outside-integration.yml'
    outside.write_text('unchanged', encoding='utf-8')
    try:
        (tmp_path / '.gitlab-ci.yml').symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable on this platform')

    classes, save_deployment = resources({}, tmp_path)
    payload, status = classes[3]().post()
    assert status == 400
    assert 'symlinks' in payload['error']
    save_deployment.assert_not_called()
    assert outside.read_text(encoding='utf-8') == 'unchanged'


def test_pipeline_integration_rejects_large_artifact_before_deployment_writes(tmp_path):
    generator = load_module('utils/pipeline_generator.py', 'large_pipeline_test_generator')

    class LargePipelineGenerator(generator.PipelineGenerator):
        @staticmethod
        def generate_gitlab_pipeline(**kwargs):
            return 'x' * (1024 * 1024 + 1)

    classes, save_deployment = resources({}, tmp_path, LargePipelineGenerator)
    payload, status = classes[3]().post()
    assert status == 413
    assert '1 MiB' in payload['error']
    save_deployment.assert_not_called()
    assert not (tmp_path / '.gitlab-ci.yml').exists()


def test_pipeline_save_limit_is_measured_in_utf8_bytes(tmp_path):
    exact_limit = 'ą' * (512 * 1024)
    classes, _ = resources({'content': exact_limit}, tmp_path)
    assert classes[1]().post()['success'] is True

    classes, _ = resources({'content': exact_limit + 'x'}, tmp_path)
    _, status = classes[1]().post()
    assert status == 413
    assert (tmp_path / 'pipeline.yml').read_text(encoding='utf-8') == exact_limit


def test_pipeline_save_replace_failure_preserves_existing_file(monkeypatch, tmp_path):
    target = tmp_path / 'pipeline.yml'
    target.write_text('existing', encoding='utf-8')

    def fail_replace(source, destination):
        raise OSError('synthetic replace failure')

    monkeypatch.setattr(os, 'replace', fail_replace)
    classes, _ = resources({'content': 'replacement'}, tmp_path)
    payload, status = classes[1]().post()
    assert status == 500
    assert 'synthetic replace failure' in payload['error']
    assert target.read_text(encoding='utf-8') == 'existing'
    assert list(tmp_path.glob('.pipeline-*')) == []
