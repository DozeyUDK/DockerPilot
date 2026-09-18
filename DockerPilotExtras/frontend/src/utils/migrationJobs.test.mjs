import test from 'node:test'
import assert from 'node:assert/strict'
import {
  canCancelMigration,
  createMigrationPoller,
  isTerminalMigration,
  selectRestorableMigration,
} from './migrationJobs.mjs'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function manualScheduler() {
  const pending = []
  return {
    pending,
    schedule(callback) {
      pending.push(callback)
      return callback
    },
    clearSchedule(callback) {
      const index = pending.indexOf(callback)
      if (index >= 0) pending.splice(index, 1)
    },
  }
}

test('migration lifecycle uses status rather than presentation stage', () => {
  for (const status of ['completed', 'failed', 'cancelled']) {
    assert.equal(isTerminalMigration({ status, stage: 'running' }), true)
  }
  for (const status of ['queued', 'running', 'cancelling', 'finalizing']) {
    assert.equal(isTerminalMigration({ status, stage: 'completed' }), false)
  }
  assert.equal(canCancelMigration({ status: 'queued' }), true)
  assert.equal(canCancelMigration({ status: 'running' }), true)
  assert.equal(canCancelMigration({ status: 'cancelling' }), false)
  assert.equal(canCancelMigration({ status: 'finalizing' }), false)
})

test('reload recovery selects the oldest active migration for the selected source', () => {
  const jobs = [
    {
      id: 'other-source',
      container_name: 'api',
      status: 'running',
      created_at: '2026-09-18T09:00:00Z',
      metadata: { source_server_id: 'remote' },
    },
    {
      id: 'newer-local',
      container_name: 'worker',
      status: 'queued',
      created_at: '2026-09-18T09:02:00Z',
      metadata: { source_server_id: 'local' },
    },
    {
      id: 'completed-local',
      container_name: 'old',
      status: 'completed',
      created_at: '2026-09-18T08:00:00Z',
      metadata: { source_server_id: 'local' },
    },
    {
      id: 'older-local',
      container_name: 'web',
      status: 'running',
      created_at: '2026-09-18T09:01:00Z',
      metadata: { source_server_id: 'local' },
    },
  ]

  assert.equal(selectRestorableMigration(jobs, 'local')?.id, 'older-local')
  assert.equal(selectRestorableMigration(jobs, 'missing'), null)
  assert.equal(selectRestorableMigration(null, 'local'), null)
})

test('poller schedules only after the current request settles', async () => {
  const request = deferred()
  const scheduler = manualScheduler()
  const snapshots = []
  let calls = 0
  const poller = createMigrationPoller({
    migrationId: 'job-1',
    fetchJob: () => {
      calls += 1
      return request.promise
    },
    onSnapshot: job => snapshots.push(job),
    onTerminal: () => assert.fail('job must remain active'),
    onError: () => true,
    schedule: scheduler.schedule,
    clearSchedule: scheduler.clearSchedule,
  })

  const started = poller.start()
  assert.equal(calls, 1)
  assert.equal(scheduler.pending.length, 0)

  request.resolve({ id: 'job-1', status: 'running' })
  await started

  assert.equal(snapshots.length, 1)
  assert.equal(scheduler.pending.length, 1)
  poller.stop()
})

test('terminal snapshot stops without another timeout', async () => {
  const scheduler = manualScheduler()
  const terminal = []
  const poller = createMigrationPoller({
    migrationId: 'job-1',
    fetchJob: async () => ({ id: 'job-1', status: 'completed' }),
    onSnapshot: () => {},
    onTerminal: job => terminal.push(job),
    onError: () => true,
    schedule: scheduler.schedule,
    clearSchedule: scheduler.clearSchedule,
  })

  await poller.start()

  assert.equal(terminal.length, 1)
  assert.equal(poller.isRunning(), false)
  assert.equal(scheduler.pending.length, 0)
})

test('late response after stop cannot update a newer UI session', async () => {
  const request = deferred()
  const scheduler = manualScheduler()
  const snapshots = []
  const poller = createMigrationPoller({
    migrationId: 'old-job',
    fetchJob: () => request.promise,
    onSnapshot: job => snapshots.push(job),
    onTerminal: () => {},
    onError: () => true,
    schedule: scheduler.schedule,
    clearSchedule: scheduler.clearSchedule,
  })

  const started = poller.start()
  poller.stop()
  request.resolve({ id: 'old-job', status: 'running' })
  await started

  assert.deepEqual(snapshots, [])
  assert.equal(scheduler.pending.length, 0)
})

test('transient failures retry recursively and a rejected error policy stops', async () => {
  const scheduler = manualScheduler()
  const failures = []
  const poller = createMigrationPoller({
    migrationId: 'job-1',
    fetchJob: async () => { throw new Error('temporary') },
    onSnapshot: () => {},
    onTerminal: () => {},
    onError: (_error, state) => {
      failures.push(state.consecutiveFailures)
      return state.consecutiveFailures < 2
    },
    schedule: scheduler.schedule,
    clearSchedule: scheduler.clearSchedule,
  })

  await poller.start()
  assert.deepEqual(failures, [1])
  assert.equal(scheduler.pending.length, 1)

  await scheduler.pending.shift()()
  assert.deepEqual(failures, [1, 2])
  assert.equal(poller.isRunning(), false)
  assert.equal(scheduler.pending.length, 0)
})
