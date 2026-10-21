import { isIP } from 'node:net'

// Match backend ipaddress.is_loopback for numeric IPv4 and IPv6 addresses.
export function isLoopbackHost(host) {
  if (host === 'localhost') return true
  const family = isIP(host)
  if (family === 4) return host.startsWith('127.')
  if (family === 6) return new URL(`http://[${host}]/`).hostname === '[::1]'
  return false
}
