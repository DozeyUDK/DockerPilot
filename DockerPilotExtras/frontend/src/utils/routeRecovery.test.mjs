import test from 'node:test'
import assert from 'node:assert/strict'

import {
  failedRouteState,
  reloadCurrentPage,
  shouldResetRouteError,
} from './routeRecovery.mjs'

test('route failures enter a recoverable state without exposing error details', () => {
  assert.deepEqual(failedRouteState(new Error('chunk URL with sensitive query')), { hasError: true })
})

test('route error resets only when navigation changes the boundary key', () => {
  assert.equal(shouldResetRouteError(true, '/status', '/environments'), true)
  assert.equal(shouldResetRouteError(true, '/status', '/status'), false)
  assert.equal(shouldResetRouteError(false, '/status', '/environments'), false)
})

test('route recovery delegates exactly one full-page reload', () => {
  let reloads = 0
  reloadCurrentPage(() => { reloads += 1 })
  assert.equal(reloads, 1)
  assert.throws(() => reloadCurrentPage(null), /reload must be a function/)
})
