export const CANONICAL_STAGES = ['build', 'test', 'scan', 'deploy', 'smoke']

export const PIPELINE_PRESETS = {
  node: {
    label: 'Node.js CI',
    stages: ['build', 'test', 'scan'],
    test_commands: 'npm test\nnpm run lint',
    deploy_strategy: 'rolling',
    enable_environments: false,
    enable_rollback_job: false,
    scan_severity: 'HIGH,CRITICAL',
    scan_fail_on_findings: true,
    smoke_test_url: '',
    smoke_test_retries: 10,
  },
  python: {
    label: 'Python CI',
    stages: ['build', 'test', 'scan'],
    test_commands: 'pytest -q\nruff check .',
    deploy_strategy: 'rolling',
    enable_environments: false,
    enable_rollback_job: false,
    scan_severity: 'HIGH,CRITICAL',
    scan_fail_on_findings: true,
    smoke_test_url: '',
    smoke_test_retries: 10,
  },
  delivery: {
    label: 'Delivery flow',
    stages: ['build', 'test', 'scan', 'deploy', 'smoke'],
    test_commands: 'npm test\nnpm run lint',
    deploy_strategy: 'rolling',
    enable_environments: false,
    enable_rollback_job: false,
    scan_severity: 'HIGH,CRITICAL',
    scan_fail_on_findings: true,
    smoke_test_url: '',
    smoke_test_retries: 10,
  },
}

export function applyPreset(formData, presetName) {
  const preset = PIPELINE_PRESETS[presetName]
  if (!preset) return { ...formData }
  // Keep project identity and provider specific settings chosen by the user.
  const { label: _label, ...settings } = preset
  return { ...formData, ...settings, stages: [...settings.stages] }
}

export function orderedStages(stages = []) {
  const selected = new Set(stages)
  return CANONICAL_STAGES.map((stage) => ({ stage, selected: selected.has(stage) }))
}

export function validatePipelineForm(formData) {
  const errors = {}
  if (!String(formData.project_name || '').trim()) errors.project_name = 'Project name is required.'
  if (!String(formData.docker_image || '').trim()) errors.docker_image = 'Docker image is required.'
  if (!String(formData.dockerfile || '').trim()) errors.dockerfile = 'Dockerfile path is required.'

  const stages = Array.isArray(formData.stages) ? formData.stages : []
  if (!stages.length) errors.stages = 'Select at least one build stage.'
  if (stages.includes('test') && !String(formData.test_commands || '').trim()) {
    errors.test_commands = 'Add at least one test command.'
  }
  if (stages.includes('smoke')) {
    if (!stages.includes('deploy')) errors.stages = 'Smoke tests require the deploy stage.'
    const url = String(formData.smoke_test_url || '').trim()
    let validUrl = false
    try {
      const parsed = new URL(url.replaceAll('{env}', 'dev'))
      validUrl = ['http:', 'https:'].includes(parsed.protocol) && Boolean(parsed.hostname)
    } catch {
      validUrl = false
    }
    if (!validUrl) errors.smoke_test_url = 'Smoke test URL must be a complete http:// or https:// URL.'
    if (url.includes('{env}') && !(formData.type === 'gitlab' && formData.enable_environments)) {
      errors.smoke_test_url = 'The {env} placeholder requires GitLab multi-environment deploys.'
    }
    const rawRetries = formData.smoke_test_retries
    const validRetries = (typeof rawRetries === 'number' && Number.isInteger(rawRetries) && rawRetries >= 1 && rawRetries <= 60) ||
      (typeof rawRetries === 'string' && /^(?:[1-9]|[1-5]\d|60)$/.test(rawRetries))
    if (!validRetries) {
      errors.smoke_test_retries = 'Retries must be an integer from 1 to 60.'
    }
  }
  return errors
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue)
  if (value && typeof value === 'object') {
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = stableValue(value[key])
      return result
    }, {})
  }
  return value
}

export function formSnapshot(formData) {
  return JSON.stringify(stableValue(formData))
}

export function isSnapshotCurrent(formData, snapshot) {
  return Boolean(snapshot) && formSnapshot(formData) === snapshot
}
