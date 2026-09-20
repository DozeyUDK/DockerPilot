export const PRIMARY_NAV_ITEMS = Object.freeze([
  Object.freeze({ path: '/', label: 'CI/CD Pipelines' }),
  Object.freeze({ path: '/environments', label: 'Environments' }),
  Object.freeze({ path: '/status', label: 'Status' }),
  Object.freeze({ path: '/secure-deploy', label: 'Secure Deploy' }),
])

export const isNavItemActive = (pathname, itemPath) => {
  if (typeof pathname !== 'string' || typeof itemPath !== 'string') {
    return false
  }

  if (itemPath === '/') {
    return pathname === '/'
  }

  return pathname === itemPath || pathname.startsWith(`${itemPath}/`)
}
