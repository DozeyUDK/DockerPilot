import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const readPage = name => readFile(new URL(`../pages/${name}.jsx`, import.meta.url), 'utf8')

const assertEveryLabelTargetsAControl = (source) => {
  const labelTargets = [...source.matchAll(/<label\s+htmlFor="([^"]+)"/g)].map(match => match[1])
  const controlIds = new Set([...source.matchAll(/<(?:input|select|textarea)[^>]+id="([^"]+)"/g)].map(match => match[1]))

  assert.ok(labelTargets.length > 0)
  assert.equal(new Set(labelTargets).size, labelTargets.length)
  for (const target of labelTargets) {
    assert.ok(controlIds.has(target), `label target ${target} must identify a form control`)
  }
}

test('login fields remain labelled and expose busy and error states', async () => {
  const source = await readPage('Login')

  assertEveryLabelTargetsAControl(source)
  assert.doesNotMatch(source, /style=/)
  assert.match(source, /role="alert"/)
  assert.match(source, /aria-busy=\{loading\}/)
})

test('secure deploy fields remain labelled without weakening preview-only signals', async () => {
  const source = await readPage('SecureDeploy')

  assertEveryLabelTargetsAControl(source)
  assert.doesNotMatch(source, /style=/)
  assert.match(source, /aria-current=\{step === number \? 'step'/)
  assert.match(source, /PREVIEW ONLY — NO CHANGES WILL BE APPLIED/)
  assert.match(source, /Verify with broker \(dry-run\)/)
})
