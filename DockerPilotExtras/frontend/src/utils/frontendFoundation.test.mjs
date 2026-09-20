import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const readSource = relativePath => readFile(new URL(relativePath, import.meta.url), 'utf8')

test('theme tokens provide default dark and explicit light palettes with legacy aliases', async () => {
  const tokens = await readSource('../styles/tokens.css')

  assert.match(tokens, /:root,\s*:root\[data-theme="dark"\]/)
  assert.match(tokens, /:root\[data-theme="light"\]/)

  for (const alias of [
    '--bg-primary',
    '--bg-secondary',
    '--bg-tertiary',
    '--text-primary',
    '--text-secondary',
    '--border-color',
    '--card-bg',
    '--input-bg',
    '--input-border',
  ]) {
    assert.match(tokens, new RegExp(`${alias}:\\s*var\\(`))
  }
})

test('application shell keeps responsive, focus, and reduced-motion contracts', async () => {
  const shell = await readSource('../styles/shell.css')

  assert.match(shell, /@media \(max-width: 48rem\)/)
  assert.match(shell, /@media \(max-width: 28rem\)/)
  assert.match(shell, /:focus-visible/)
  assert.match(shell, /@media \(prefers-reduced-motion: reduce\)/)
  assert.doesNotMatch(shell, /#[0-9a-f]{3,8}\b|rgba?\(/i)
})
