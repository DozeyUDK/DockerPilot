import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { ICONS } from '../icons.mjs'

const catalog = JSON.parse(readFileSync(new URL('../../../../icons/catalog.json', import.meta.url), 'utf8'))

test('generated GUI glyphs agree with shared Nerd Fonts catalog', () => {
  assert.equal(Object.keys(ICONS).length, Object.keys(catalog.icons).length)
  for (const [name, entry] of Object.entries(catalog.icons)) {
    assert.equal(ICONS[name], String.fromCodePoint(Number.parseInt(entry.codepoint, 16)), name)
  }
})

test('all standalone status indicators are mapped', () => {
  for (const key of ['success', 'error', 'warning', 'info', 'loading', 'docker', 'folder']) {
    assert.equal(typeof ICONS[key], 'string', key)
    assert.equal([...ICONS[key]].length, 1, key)
  }
})
