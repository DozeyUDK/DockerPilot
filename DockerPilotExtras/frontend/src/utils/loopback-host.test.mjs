import assert from 'node:assert/strict'
import test from 'node:test'
import { isLoopbackHost, loopbackProxyTarget } from './loopback-host.mjs'

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

test('Vite targets an explicitly bound IPv4 or IPv6 loopback backend', () => {
  assert.equal(loopbackProxyTarget('127.0.0.2', '5000'), 'http://127.0.0.2:5000')
  assert.equal(loopbackProxyTarget('::1', '5001'), 'http://[::1]:5001')
  assert.equal(loopbackProxyTarget('0:0:0:0:0:0:0:1', '5050'), 'http://[0:0:0:0:0:0:0:1]:5050')
})

test('Vite defaults to IPv4 loopback and never targets external or wildcard hosts', () => {
  for (const host of [undefined, '', 'localhost', '0.0.0.0', '::', '192.168.1.2', 'example.com']) {
    assert.equal(loopbackProxyTarget(host, '5000'), 'http://127.0.0.1:5000')
  }
})
