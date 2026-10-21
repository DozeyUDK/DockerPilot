import assert from 'node:assert/strict'
import test from 'node:test'
import { isLoopbackHost } from './loopback-host.mjs'

test('Vite accepts numeric IPv4 and IPv6 loopback addresses', () => {
  for (const host of ['localhost', '127.0.0.1', '127.0.0.2', '127.255.255.254', '::1', '0:0:0:0:0:0:0:1']) {
    assert.equal(isLoopbackHost(host), true, host)
  }
})

test('Vite refuses non-loopback addresses and invalid addresses', () => {
  for (const host of ['0.0.0.0', '::', '192.168.1.2', '8.8.8.8', 'fe80::1', '::ffff:127.0.0.1', 'localhost.evil.tld', '127.0.0.256', '']) {
    assert.equal(isLoopbackHost(host), false, host)
  }
})
