import test from 'node:test'
import assert from 'node:assert/strict'

import { SECURE_DEPLOY_STEPS } from './secureDeployForm.mjs'

test('secure deploy steps are sequential, unique, and visibly named', () => {
  assert.deepEqual(SECURE_DEPLOY_STEPS.map(step => step.number), [1, 2, 3, 4, 5, 6, 7])
  assert.equal(new Set(SECURE_DEPLOY_STEPS.map(step => step.label)).size, 7)
  assert.ok(SECURE_DEPLOY_STEPS.every(step => step.label.trim().length > 0))
})
