import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyPreset,
  formSnapshot,
  isSnapshotCurrent,
  orderedStages,
  validatePipelineForm,
} from './pipelineWorkbench.mjs'

const base = {
  type: 'gitlab', project_name: 'demo', docker_image: 'demo:latest', dockerfile: './Dockerfile',
  stages: ['test', 'build'], test_commands: 'pytest -q', smoke_test_url: '', smoke_test_retries: 10,
  runner_tags: 'custom', agent: 'custom-agent', credentials_id: 'custom-creds',
  enable_environments: true, enable_rollback_job: true,
}

test('presets replace CI settings while preserving project and provider choices', () => {
  const result = applyPreset({ ...base, runner_tags: 'custom' }, 'python')
  assert.equal(result.project_name, 'demo')
  assert.equal(result.runner_tags, 'custom')
  assert.equal(result.agent, 'custom-agent')
  assert.equal(result.credentials_id, 'custom-creds')
  assert.deepEqual(result.stages, ['build', 'test', 'scan'])
  assert.match(result.test_commands, /pytest/)
  assert.equal('label' in result, false)
})

test('switching delivery to Python resets delivery knobs and does not alias preset arrays', () => {
  const delivery = applyPreset(base, 'delivery')
  const python = applyPreset(delivery, 'python')
  assert.deepEqual(python.stages, ['build', 'test', 'scan'])
  assert.equal(python.enable_environments, false)
  assert.equal(python.enable_rollback_job, false)
  delivery.stages.push('deploy')
  assert.deepEqual(python.stages, ['build', 'test', 'scan'])
})

test('ordered stage overview always uses canonical order', () => {
  assert.deepEqual(orderedStages(['smoke', 'build']).map(({ stage, selected }) => [stage, selected]), [
    ['build', true], ['test', false], ['scan', false], ['deploy', false], ['smoke', true],
  ])
})

test('validation catches required values and stage dependencies', () => {
  const errors = validatePipelineForm({ ...base, project_name: ' ', stages: ['smoke'], smoke_test_url: 'ftp://bad', smoke_test_retries: 61 })
  assert.ok(errors.project_name)
  assert.ok(errors.stages)
  assert.ok(errors.smoke_test_url)
  assert.ok(errors.smoke_test_retries)
})

test('validation catches empty stages and test commands', () => {
  const errors = validatePipelineForm({ ...base, stages: [], test_commands: '' })
  assert.ok(errors.stages)
  assert.equal(validatePipelineForm({ ...base, stages: ['test'], test_commands: '  ' }).test_commands !== undefined, true)
})

test('validation accepts complete HTTP URLs and rejects malformed URLs', () => {
  assert.equal(validatePipelineForm({ ...base, stages: ['smoke', 'deploy'], smoke_test_url: 'https://example.com/health' }).smoke_test_url, undefined)
  for (const url of ['example.com/health', 'https://', 'http://?bad', 'ftp://example.com']) {
    assert.ok(validatePipelineForm({ ...base, stages: ['smoke', 'deploy'], smoke_test_url: url }).smoke_test_url)
  }
})

test('GitLab environment placeholder requires multi-environment mode', () => {
  const form = { ...base, stages: ['smoke', 'deploy'], smoke_test_url: 'https://{env}.example.com/health', type: 'gitlab' }
  assert.equal(validatePipelineForm({ ...form, enable_environments: true }).smoke_test_url, undefined)
  assert.ok(validatePipelineForm({ ...form, enable_environments: false }).smoke_test_url)
  assert.ok(validatePipelineForm({ ...form, type: 'jenkins' }).smoke_test_url)
})

test('retry validation accepts integer values and rejects coercible or out of range values', () => {
  for (const value of [1, 60, '1', '60', '10']) {
    assert.equal(validatePipelineForm({ ...base, stages: ['smoke', 'deploy'], smoke_test_url: 'https://example.com', smoke_test_retries: value }).smoke_test_retries, undefined)
  }
  for (const value of [0, 61, true, false, 1.5, '1.0', '1e1', '', ' 1', '01']) {
    assert.ok(validatePipelineForm({ ...base, stages: ['smoke', 'deploy'], smoke_test_url: 'https://example.com', smoke_test_retries: value }).smoke_test_retries)
  }
})

test('snapshot changes when any form value changes', () => {
  const snapshot = formSnapshot(base)
  assert.equal(formSnapshot(base), snapshot)
  assert.notEqual(formSnapshot({ ...base, deploy_strategy: 'canary' }), snapshot)
  assert.equal(isSnapshotCurrent(base, snapshot), true)
  assert.equal(isSnapshotCurrent({ ...base, nested: { changed: true } }, snapshot), false)
  assert.equal(isSnapshotCurrent({ ...base, deploy_strategy: 'canary' }, snapshot), false)
  assert.equal(isSnapshotCurrent({ ...base }, formSnapshot({ ...base, deploy_strategy: 'canary' })), false)
  assert.equal(isSnapshotCurrent({ b: 2, a: 1 }, formSnapshot({ a: 1, b: 2 })), true)
  assert.equal(isSnapshotCurrent(base, null), false)
  assert.equal(isSnapshotCurrent(base, undefined), false)
})
