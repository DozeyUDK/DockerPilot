import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { nextModalFocusIndex } from './modalFocus.mjs'

const readSource = relativePath => readFile(new URL(relativePath, import.meta.url), 'utf8')

test('modal focus navigation wraps in both directions', () => {
  assert.equal(nextModalFocusIndex(0, 3, false), 1)
  assert.equal(nextModalFocusIndex(2, 3, false), 0)
  assert.equal(nextModalFocusIndex(2, 3, true), 1)
  assert.equal(nextModalFocusIndex(0, 3, true), 2)
  assert.equal(nextModalFocusIndex(-1, 3, false), 0)
  assert.equal(nextModalFocusIndex(-1, 3, true), 2)
  assert.equal(nextModalFocusIndex(0, 0, false), -1)
})

test('shared modal keeps the accessible dismissal and focus contracts', async () => {
  const modal = await readSource('../components/Modal.jsx')

  assert.match(modal, /role="dialog"/)
  assert.match(modal, /aria-modal="true"/)
  assert.match(modal, /aria-labelledby=/)
  assert.match(modal, /event\.key === 'Escape'/)
  assert.match(modal, /previousFocusRef\.current\?\.focus/)
  assert.match(modal, /document\.body\.style\.overflow = 'hidden'/)
  assert.match(modal, /event\.target !== event\.currentTarget/)
})

test('Pipelines uses the shared modal for both browsers', async () => {
  const pipelines = await readSource('../pages/Pipelines.jsx')

  assert.match(pipelines, /<Modal[\s\S]*title="Browse files"/)
  assert.match(pipelines, /<Modal[\s\S]*title="Browse Docker images"/)
  assert.doesNotMatch(pipelines, /File Browser Modal/)
  assert.doesNotMatch(pipelines, /Docker Images Browser Modal/)
})
