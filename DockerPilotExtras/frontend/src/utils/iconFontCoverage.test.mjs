import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const globalCss = read('../index.css')
const appCss = read('../App.css')
const status = read('../pages/Status.jsx')
const environments = read('../pages/Environments.jsx')

test('native controls inherit the icon-aware font stack', () => {
  assert.match(globalCss, /button,\s*input,\s*select,\s*textarea\s*\{\s*font-family:\s*inherit\s*;/)
  assert.match(globalCss, /body\s*\{[^}]*font-family:\s*'DockerPilot Nerd Symbols'/s)
  assert.match(globalCss, /@font-face\s*\{[^}]*unicode-range:[^}]*U\+E000-F8FF/s)
})

test('code and preformatted output keep the bundled icon font before their monospace fallback', () => {
  assert.match(globalCss, /code\s*\{\s*font-family:\s*'DockerPilot Nerd Symbols'/)
  for (const value of appCss.matchAll(/font-family:\s*([^;]+);/g)) {
    assert.match(value[1], /DockerPilot Nerd Symbols/, 'App.css override: ' + value[1])
  }
  for (const [pageName, pageText] of [['Status', status], ['Environments', environments]]) {
    for (const value of pageText.matchAll(/fontFamily:\s*'([^']+)'/g)) {
      assert.ok(
        value[1] === 'inherit' || value[1].startsWith('"DockerPilot Nerd Symbols",'),
        pageName + ' inline font must preserve symbols: ' + value[1]
      )
    }
  }
})

test('icon-bearing console suggestion is inside an icon-aware font stack', () => {
  assert.match(status, /suggestionText = [^\n]*[\uE000-\uF8FF]/)
  assert.match(status, /fontFamily:\s*'"DockerPilot Nerd Symbols", "Consolas"/)
})
