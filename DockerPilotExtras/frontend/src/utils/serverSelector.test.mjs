import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildServerOptions,
  getNextServerOptionIndex,
  getServerOptionLabel,
} from './serverSelector.mjs'

test('server options always begin with local and preserve remote connection context', () => {
  const options = buildServerOptions([
    { id: 'edge-1', name: 'Edge', username: 'deploy', hostname: 'edge.internal', port: 22 },
  ])

  assert.deepEqual(options, [
    { id: 'local', name: 'Local', detail: 'This DockerPilot host', icon: '🏠' },
    { id: 'edge-1', name: 'Edge', label: 'Edge (edge.internal)', detail: 'deploy@edge.internal:22', icon: '🖥️' },
  ])
  assert.equal(getServerOptionLabel('edge-1', options), 'Edge (edge.internal)')
  assert.equal(getServerOptionLabel('missing', options), 'missing')
})

test('server option keyboard navigation wraps and supports boundaries', () => {
  assert.equal(getNextServerOptionIndex(0, 3, 'ArrowDown'), 1)
  assert.equal(getNextServerOptionIndex(2, 3, 'ArrowDown'), 0)
  assert.equal(getNextServerOptionIndex(0, 3, 'ArrowUp'), 2)
  assert.equal(getNextServerOptionIndex(1, 3, 'Home'), 0)
  assert.equal(getNextServerOptionIndex(1, 3, 'End'), 2)
  assert.equal(getNextServerOptionIndex(1, 3, 'Tab'), 1)
  assert.equal(getNextServerOptionIndex(0, 0, 'ArrowDown'), -1)
})
