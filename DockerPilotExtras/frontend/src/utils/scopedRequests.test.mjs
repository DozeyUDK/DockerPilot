import test from 'node:test'
import assert from 'node:assert/strict'

import { createScopedRequestGuard } from './scopedRequests.mjs'

test('server switch invalidates every request from the previous scope', () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')
  const localStatus = guard.beginRequest('status', localScope)
  const localContainers = guard.beginRequest('containers', localScope)

  assert.equal(guard.isCurrent(localStatus), true)
  assert.equal(guard.isCurrent(localContainers), true)

  const remoteScope = guard.beginScope('remote-a')
  const remoteStatus = guard.beginRequest('status', remoteScope)

  assert.equal(guard.isCurrent(localStatus), false)
  assert.equal(guard.isCurrent(localContainers), false)
  assert.equal(guard.isCurrent(remoteStatus), true)
})

test('newer request supersedes only the same channel in the current scope', () => {
  const guard = createScopedRequestGuard()
  const scope = guard.beginScope('remote-a')
  const firstStatus = guard.beginRequest('status', scope)
  const containers = guard.beginRequest('containers', scope)
  const secondStatus = guard.beginRequest('status', scope)

  assert.equal(guard.isCurrent(firstStatus), false)
  assert.equal(guard.isCurrent(secondStatus), true)
  assert.equal(guard.isCurrent(containers), true)
})

test('stale scope cannot supersede a request in the active scope', () => {
  const guard = createScopedRequestGuard()
  const oldScope = guard.beginScope('local')
  const currentScope = guard.beginScope('remote-a')
  const currentStatus = guard.beginRequest('status', currentScope)
  const staleStatus = guard.beginRequest('status', oldScope)

  assert.equal(guard.isCurrent(staleStatus), false)
  assert.equal(guard.isCurrent(currentStatus), true)
})

test('cleanup invalidates only the matching active scope', () => {
  const guard = createScopedRequestGuard()
  const oldScope = guard.beginScope('local')
  const currentScope = guard.beginScope('remote-a')
  const currentPreflight = guard.beginRequest('preflight', currentScope)

  guard.invalidateScope(oldScope)
  assert.equal(guard.isCurrent(currentPreflight), true)

  guard.invalidateScope(currentScope)
  assert.equal(guard.isCurrent(currentPreflight), false)
})
