import { isIP } from 'node:net'

// Match backend ipaddress.is_loopback for numeric IPv4 and IPv6 addresses.
export function isLoopbackHost(host) {
  if (host === 'localhost') return true
  const family = isIP(host)
  if (family === 4) return host.startsWith('127.')
  if (family === 6) return new URL(`http://[${host}]/`).hostname === '[::1]'
  return false
}

/**
 * Mirror the backend's explicit numeric loopback bind when proxying /api.
 * A wildcard/non-loopback backend bind is still reachable via IPv4 loopback;
 * never proxy developer API traffic to a remote host.
 */
export function loopbackProxyTarget(bindHost, port) {
  const configured = (bindHost || '127.0.0.1').trim()
  const host = isLoopbackHost(configured) && configured !== 'localhost'
    ? configured
    : '127.0.0.1'
  const authority = isIP(host) === 6 ? `[${host}]` : host
  return `http://${authority}:${port}`
}
