import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const APP_SOURCE = new URL('../App.jsx', import.meta.url)

test('page modules remain lazy route boundaries', async () => {
  const source = await readFile(APP_SOURCE, 'utf8')
  const pageNames = ['Login', 'Pipelines', 'Environments', 'Status', 'SecureDeploy']

  for (const pageName of pageNames) {
    assert.match(source, new RegExp(`const ${pageName} = lazy\\(\\(\\) => import\\('./pages/${pageName}'\\)\\)`))
    assert.doesNotMatch(source, new RegExp(`import ${pageName} from './pages/${pageName}'`))
  }
})

test('authenticated and login route boundaries expose accessible loading fallbacks', async () => {
  const source = await readFile(APP_SOURCE, 'utf8')

  assert.equal((source.match(/<Suspense fallback=/g) || []).length, 2)
  assert.equal((source.match(/<RouteErrorBoundary resetKey=/g) || []).length, 2)
  assert.match(source, /role="status"/)
  assert.match(source, /aria-live="polite"/)
  assert.match(source, /aria-busy="true"/)
})

test('pipeline highlighting loads only the languages rendered by the workbench', async () => {
  const source = await readFile(new URL('../pages/Pipelines.jsx', import.meta.url), 'utf8')

  assert.match(source, /PrismLight as SyntaxHighlighter/)
  assert.match(source, /registerLanguage\('groovy', groovy\)/)
  assert.match(source, /registerLanguage\('yaml', yaml\)/)
  assert.doesNotMatch(source, /Prism as SyntaxHighlighter/)
})
