import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createScopedRequestGuard,
  runScopedRequest,
  scopeMatchesKey,
} from './scopedRequests.mjs'

const deferred = () => {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

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

test('CLI response and finalizer are ignored after switching servers', async () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')
  const pending = deferred()
  const events = []

  const execution = runScopedRequest({
    guard,
    channel: 'cli',
    scope: localScope,
    execute: () => pending.promise,
    onStart: () => events.push('start'),
    onSuccess: () => events.push('success'),
    onFinally: () => events.push('finally'),
  })

  guard.beginScope('remote-a')
  pending.resolve({ output: 'old server' })

  assert.deepEqual(await execution, { status: 'ignored' })
  assert.deepEqual(events, ['start'])
})

test('CLI error from the previous server cannot contaminate the active scope', async () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')
  const pending = deferred()
  const events = []

  const execution = runScopedRequest({
    guard,
    channel: 'cli',
    scope: localScope,
    execute: () => pending.promise,
    onStart: () => events.push('start'),
    onError: () => events.push('error'),
    onFinally: () => events.push('finally'),
  })

  guard.beginScope('remote-a')
  pending.reject(new Error('old server failed'))

  assert.deepEqual(await execution, { status: 'ignored' })
  assert.deepEqual(events, ['start'])
})

test('newer CLI execution owns completion callbacks in the same server scope', async () => {
  const guard = createScopedRequestGuard()
  const scope = guard.beginScope('remote-a')
  const first = deferred()
  const second = deferred()
  const events = []

  const firstExecution = runScopedRequest({
    guard,
    channel: 'cli',
    scope,
    execute: () => first.promise,
    onSuccess: () => events.push('first:success'),
    onFinally: () => events.push('first:finally'),
  })
  const secondExecution = runScopedRequest({
    guard,
    channel: 'cli',
    scope,
    execute: () => second.promise,
    onSuccess: value => events.push(`second:${value.output}`),
    onFinally: () => events.push('second:finally'),
  })

  first.resolve({ output: 'stale' })
  second.resolve({ output: 'current' })

  assert.deepEqual(await firstExecution, { status: 'ignored' })
  assert.deepEqual(await secondExecution, {
    status: 'success',
    value: { output: 'current' },
  })
  assert.deepEqual(events, ['second:current', 'second:finally'])
})

test('file browser response and finalizer are ignored after switching servers', async () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')
  const pending = deferred()
  const events = []

  const browse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope: localScope,
    execute: () => pending.promise,
    onStart: () => events.push('loading'),
    onSuccess: value => events.push(value.currentPath),
    onFinally: () => events.push('settled'),
  })

  guard.beginScope('remote-a')
  pending.resolve({ currentPath: '/old-server' })

  assert.deepEqual(await browse, { status: 'ignored' })
  assert.deepEqual(events, ['loading'])
})

test('file browser error from the previous server is ignored', async () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')
  const pending = deferred()
  const events = []

  const browse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope: localScope,
    execute: () => pending.promise,
    onError: () => events.push('error'),
    onFinally: () => events.push('settled'),
  })

  guard.beginScope('remote-a')
  pending.reject(new Error('old browser request failed'))

  assert.deepEqual(await browse, { status: 'ignored' })
  assert.deepEqual(events, [])
})

test('latest file browser navigation owns results within the active server', async () => {
  const guard = createScopedRequestGuard()
  const scope = guard.beginScope('remote-a')
  const parent = deferred()
  const child = deferred()
  const events = []

  const parentBrowse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope,
    execute: () => parent.promise,
    onSuccess: value => events.push(value.currentPath),
    onFinally: () => events.push('parent:settled'),
  })
  const childBrowse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope,
    execute: () => child.promise,
    onSuccess: value => events.push(value.currentPath),
    onFinally: () => events.push('child:settled'),
  })

  child.resolve({ currentPath: '/srv/app' })
  parent.resolve({ currentPath: '/srv' })

  assert.deepEqual(await childBrowse, {
    status: 'success',
    value: { currentPath: '/srv/app' },
  })
  assert.deepEqual(await parentBrowse, { status: 'ignored' })
  assert.deepEqual(events, ['/srv/app', 'child:settled'])
})

test('closing the file browser invalidates only its request channel', () => {
  const guard = createScopedRequestGuard()
  const scope = guard.beginScope('remote-a')
  const browser = guard.beginRequest('file-browser', scope)
  const cli = guard.beginRequest('cli', scope)

  guard.invalidateChannel('file-browser', scope)

  assert.equal(guard.isCurrent(browser), false)
  assert.equal(guard.isCurrent(cli), true)
})

test('scope key comparison hides old browser state before the next effect', () => {
  const guard = createScopedRequestGuard()
  const localScope = guard.beginScope('local')

  assert.equal(scopeMatchesKey(localScope, 'local'), true)
  assert.equal(scopeMatchesKey(localScope, 'remote-a'), false)
  assert.equal(scopeMatchesKey(null, 'remote-a'), false)
})

test('closed file browser suppresses late success, error, and finalizers', async () => {
  const guard = createScopedRequestGuard()
  const scope = guard.beginScope('remote-a')
  const success = deferred()
  const failure = deferred()
  const events = []

  const successfulBrowse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope,
    execute: () => success.promise,
    onSuccess: () => events.push('success'),
    onFinally: () => events.push('success:settled'),
  })
  guard.invalidateChannel('file-browser', scope)
  success.resolve({ currentPath: '/late-success' })
  assert.deepEqual(await successfulBrowse, { status: 'ignored' })

  const failedBrowse = runScopedRequest({
    guard,
    channel: 'file-browser',
    scope,
    execute: () => failure.promise,
    onError: () => events.push('error'),
    onFinally: () => events.push('error:settled'),
  })
  guard.invalidateChannel('file-browser', scope)
  failure.reject(new Error('late failure'))
  assert.deepEqual(await failedBrowse, { status: 'ignored' })

  assert.deepEqual(events, [])
})
