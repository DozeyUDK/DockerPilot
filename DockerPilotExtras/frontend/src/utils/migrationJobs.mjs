export const TERMINAL_MIGRATION_STATUSES = new Set([
  'completed',
  'failed',
  'cancelled',
])

export function isTerminalMigration(job) {
  return Boolean(job && TERMINAL_MIGRATION_STATUSES.has(job.status))
}

export function canCancelMigration(job) {
  return Boolean(job && (job.status === 'queued' || job.status === 'running'))
}

export function selectRestorableMigration(jobs, sourceServerId) {
  if (!Array.isArray(jobs) || typeof sourceServerId !== 'string') return null

  const candidates = jobs.filter(job => (
    job &&
    typeof job.id === 'string' &&
    typeof job.container_name === 'string' &&
    !isTerminalMigration(job) &&
    job.metadata?.source_server_id === sourceServerId
  ))

  candidates.sort((left, right) => (
    String(left.created_at || '').localeCompare(String(right.created_at || ''))
  ))
  return candidates[0] || null
}

export function createMigrationPoller({
  migrationId,
  fetchJob,
  onSnapshot,
  onTerminal,
  onError,
  schedule = setTimeout,
  clearSchedule = clearTimeout,
  delayMs = 2000,
}) {
  if (typeof migrationId !== 'string' || migrationId.length === 0) {
    throw new TypeError('migrationId is required')
  }

  let stopped = true
  let timer = null
  let inFlight = false
  let consecutiveFailures = 0

  const poll = async () => {
    if (stopped || inFlight) return
    inFlight = true

    try {
      const job = await fetchJob(migrationId)
      if (stopped) return

      consecutiveFailures = 0
      onSnapshot(job)
      if (isTerminalMigration(job)) {
        stopped = true
        onTerminal(job)
      }
    } catch (error) {
      if (stopped) return
      consecutiveFailures += 1
      const shouldContinue = onError(error, { consecutiveFailures }) !== false
      if (!shouldContinue) stopped = true
    } finally {
      inFlight = false
      if (!stopped) timer = schedule(poll, delayMs)
    }
  }

  return {
    start() {
      if (!stopped) return Promise.resolve()
      stopped = false
      return poll()
    },
    stop() {
      stopped = true
      if (timer !== null) {
        clearSchedule(timer)
        timer = null
      }
    },
    isRunning() {
      return !stopped
    },
  }
}
