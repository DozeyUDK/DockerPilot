import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = relativePath => readFile(new URL(relativePath, import.meta.url), 'utf8')

test('mobile server menu stays within the viewport content area', async () => {
  const css = await source('../styles/shell.css')
  const mobile = css.split('@media (max-width: 48rem) {')[1]
  assert.ok(mobile, 'tablet/mobile layout must exist')
  const menu = mobile.match(/\.server-selector-menu\s*\{([^}]+)\}/)?.[1]
  assert.ok(menu, 'mobile menu override must exist')
  for (const rule of [
    /position:\s*fixed;/,
    /top:\s*calc\(4rem\s*\+\s*var\(--space-1\)\);/,
    /left:\s*calc\(3\.5rem\s*\+\s*var\(--space-2\)\);/,
    /right:\s*var\(--space-2\);/,
    /width:\s*auto;/,
    /min-width:\s*0;/,
    /max-width:\s*none;/,
    /overflow-y:\s*auto;/,
  ]) {
    assert.match(menu, rule)
  }

  const railWidth = 3.5 * 16
  const margin = 0.5 * 16
  for (const width of [320, 375, 390, 768]) {
    const menuLeft = railWidth + margin
    const menuRight = width - margin
    assert.ok(menuLeft >= railWidth, 'menu must never overlap sidebar')
    assert.ok(menuRight <= width, 'menu must never extend beyond the viewport')
    assert.ok(menuRight - menuLeft > 0, 'menu must have positive usable width')
  }
})

test('accent-coloured inline controls use on-accent text rather than white', async () => {
  const status = await source('../pages/Status.jsx')
  const environments = await source('../pages/Environments.jsx')
  const tokens = await source('../styles/tokens.css')

  for (const file of [status, environments]) {
    assert.doesNotMatch(file, /backgroundColor:\s*'var\(--color-accent\)',\s*color:\s*'white'/)
  }
  assert.match(status, /backgroundColor:\s*'var\(--color-accent\)',\s*color:\s*'var\(--color-on-accent\)'/)
  assert.match(environments, /backgroundColor:\s*loadingStatus\s*\?[^\n]+,[\s\n]*color:\s*loadingStatus\s*\?\s*'white'\s*:\s*'var\(--color-on-accent\)'/)
  assert.match(environments, /backgroundColor:\s*\(!serverForm\.name[^\n]+,[\s\n]*color:\s*\(!serverForm\.name[^\n]+\s*:\s*'var\(--color-on-accent\)'/)
  assert.match(tokens, /--color-on-accent:\s*#06171b;/)
})

test('dark primary accent and on-accent text have readable contrast', async () => {
  const tokens = await source('../styles/tokens.css')
  const dark = tokens.split(':root[data-theme="light"]')[0]
  const extract = name => {
    const value = dark.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`))?.[1]
    assert.ok(value, `missing ${name}`)
    return value
  }
  const luminance = color => {
    const channel = index => {
      const c = Number.parseInt(color.slice(index, index + 2), 16) / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    }
    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
  }
  const first = luminance(extract('color-accent'))
  const second = luminance(extract('color-on-accent'))
  const contrast = (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
  assert.ok(contrast >= 4.5, `expected at least 4.5:1, got ${contrast.toFixed(2)}:1`)
})
