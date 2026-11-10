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

test('HA-iX industrial shell uses flat surfaces, accessible navigation, and both palettes', async () => {
  const tokens = await readSource('../styles/tokens.css')
  const shell = await readSource('../styles/shell.css')
  const navigation = await readSource('../components/AppNavigation.jsx')
  const app = await readSource('../App.jsx')
  const forms = await readSource('../styles/forms.css')
  const content = await readSource('../App.css')
  const environments = await readSource('../pages/Environments.jsx')

  assert.match(tokens, /--color-canvas:\s*#0b1116/)
  assert.match(tokens, /--color-surface:\s*#111820/)
  assert.match(tokens, /--color-border:\s*#29343c/)
  assert.match(tokens, /--color-accent:\s*#00cccc/)
  assert.match(tokens, /--color-accent:\s*#008b8b/)
  assert.match(tokens, /--radius-sm:\s*2px/)
  assert.match(shell, /grid-template-areas:/)
  assert.match(shell, /grid-template-columns:\s*4\.75rem minmax\(0, 1fr\)/)
  assert.match(shell, /\.app-sidebar/)
  assert.match(navigation, /<aside className="app-sidebar"/)
  assert.match(navigation, /aria-current=\{active \? 'page' : undefined\}/)
  assert.match(navigation, /aria-label=\{item\.label\}/)
  assert.match(navigation, /<ServerSelector \/>/)
  assert.match(app, /className="App app-authenticated"/)

  // Decorative gradients and luminous glow no longer obscure operational data.
  for (const source of [shell, forms, content, environments]) {
    assert.doesNotMatch(source, /(?:linear|radial)-gradient\(/i)
  }
  assert.doesNotMatch(environments, /0 0 20px \$\{env\.color\}/)
})
