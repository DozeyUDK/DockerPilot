export function createScopedRequestGuard() {
  let scopeSequence = 0
  let requestSequence = 0
  let currentScope = null
  let latestByChannel = new Map()

  const scopeIsCurrent = scope => Boolean(
    scope && currentScope && scope.id === currentScope.id,
  )

  return {
    beginScope(scopeKey) {
      currentScope = Object.freeze({
        id: ++scopeSequence,
        key: String(scopeKey ?? ''),
      })
      latestByChannel = new Map()
      return currentScope
    },

    invalidateScope(scope) {
      if (!scopeIsCurrent(scope)) return
      currentScope = null
      latestByChannel = new Map()
    },

    beginRequest(channel, scope = currentScope) {
      if (!scopeIsCurrent(scope)) {
        return Object.freeze({
          scopeId: scope?.id ?? null,
          channel,
          requestId: null,
        })
      }

      const requestId = ++requestSequence
      latestByChannel.set(channel, requestId)
      return Object.freeze({ scopeId: scope.id, channel, requestId })
    },

    isCurrent(request) {
      return Boolean(
        request &&
        currentScope &&
        request.scopeId === currentScope.id &&
        request.requestId !== null &&
        latestByChannel.get(request.channel) === request.requestId,
      )
    },
  }
}

export async function runScopedRequest({
  guard,
  channel,
  scope,
  execute,
  onStart,
  onSuccess,
  onError,
  onFinally,
}) {
  const request = guard.beginRequest(channel, scope)
  if (!guard.isCurrent(request)) return { status: 'ignored' }

  onStart?.(request)
  try {
    const value = await execute()
    if (!guard.isCurrent(request)) return { status: 'ignored' }
    onSuccess?.(value, request)
    return { status: 'success', value }
  } catch (error) {
    if (!guard.isCurrent(request)) return { status: 'ignored' }
    onError?.(error, request)
    return { status: 'error', error }
  } finally {
    if (guard.isCurrent(request)) onFinally?.(request)
  }
}
