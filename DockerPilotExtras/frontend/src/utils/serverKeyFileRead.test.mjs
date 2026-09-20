import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { isCurrentKeyFileRead } from './serverKeyFileRead.mjs'

test('only the active reader from the current form session may publish a key', () => {
  const activeReader = {}
  assert.equal(isCurrentKeyFileRead(activeReader, activeReader, 4, 4), true)
  assert.equal(isCurrentKeyFileRead({}, activeReader, 4, 4), false)
  assert.equal(isCurrentKeyFileRead(activeReader, activeReader, 3, 4), false)
})

test('server form invalidates and aborts pending key reads before reset', async () => {
  const source = await readFile(new URL('../pages/Environments.jsx', import.meta.url), 'utf8')

  assert.match(source, /const invalidateServerKeyRead = \(\) =>/)
  assert.match(source, /serverKeyReaderRef\.current\?\.abort\(\)/)
  assert.match(source, /const resetServerForm = \(\) => \{\s*invalidateServerKeyRead\(\)/)
  assert.match(source, /isCurrentKeyFileRead\(/)
  assert.match(source, /setServerForm\(previous => \(\{ \.\.\.previous, private_key:/)
  assert.match(source, /const handlePrivateKeyChange = event => \{\s*invalidateServerKeyRead\(\)/)
  assert.match(source, /onChange=\{handlePrivateKeyChange\}/)
  assert.match(source, /const handleServerAuthTypeChange = event => \{\s*invalidateServerKeyRead\(\)/)
  assert.match(source, /onChange=\{handleServerAuthTypeChange\}/)
})
