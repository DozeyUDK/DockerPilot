import test from 'node:test'
import assert from 'node:assert/strict'

import { PRIMARY_NAV_ITEMS, isNavItemActive } from './navigation.mjs'

test('primary navigation exposes unique absolute paths and useful labels', () => {
  const paths = PRIMARY_NAV_ITEMS.map(item => item.path)

  assert.equal(new Set(paths).size, paths.length)
  for (const item of PRIMARY_NAV_ITEMS) {
    assert.match(item.path, /^\//)
    assert.ok(item.label.trim().length > 0)
  }
})

test('root navigation item matches only the application root', () => {
  assert.equal(isNavItemActive('/', '/'), true)
  assert.equal(isNavItemActive('/status', '/'), false)
})

test('section navigation items match nested routes on segment boundaries', () => {
  assert.equal(isNavItemActive('/status', '/status'), true)
  assert.equal(isNavItemActive('/status/containers', '/status'), true)
  assert.equal(isNavItemActive('/status-other', '/status'), false)
})

test('invalid navigation inputs are never active', () => {
  assert.equal(isNavItemActive(null, '/status'), false)
  assert.equal(isNavItemActive('/status', null), false)
})
