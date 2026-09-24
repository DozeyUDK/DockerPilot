export const failedRouteState = () => ({ hasError: true })

export const shouldResetRouteError = (hasError, previousKey, nextKey) => (
  Boolean(hasError) && previousKey !== nextKey
)

export const reloadCurrentPage = (reload) => {
  if (typeof reload !== 'function') {
    throw new TypeError('reload must be a function')
  }
  reload()
}
