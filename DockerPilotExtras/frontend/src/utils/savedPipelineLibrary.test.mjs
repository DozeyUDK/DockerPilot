import test from 'node:test'
import assert from 'node:assert/strict'
import {
  formatPipelineSize,
  formatPipelineModifiedAt,
  normalizeSavedPipelines,
  isSavedPipelineDetailCurrent,
  pipelineDownloadDescriptor,
  pipelineLanguage,
  reconcileSelectedPipeline,
} from './savedPipelineLibrary.mjs'

const metadata = {
  filename: 'pipeline.yml',
  type: 'generic',
  size_bytes: 14,
  modified_at: '2026-09-17T10:00:00Z',
  sha256: 'a'.repeat(64),
}

test('saved pipeline metadata accepts only complete allowlisted records', () => {
  assert.deepEqual(normalizeSavedPipelines([
    metadata,
    { ...metadata, filename: '../pipeline.yml' },
    { ...metadata, size_bytes: -1 },
    null,
  ]), [metadata])
  assert.deepEqual(normalizeSavedPipelines(null), [])
})

test('pipeline presentation maps Jenkins to Groovy and other supported types to YAML', () => {
  assert.equal(pipelineLanguage('jenkins'), 'groovy')
  assert.equal(pipelineLanguage('gitlab'), 'yaml')
  assert.equal(pipelineLanguage('generic'), 'yaml')
})

test('pipeline sizes are readable without hiding the byte boundary', () => {
  assert.equal(formatPipelineSize(0), '0 B')
  assert.equal(formatPipelineSize(1023), '1023 B')
  assert.equal(formatPipelineSize(1024), '1.0 KiB')
  assert.equal(formatPipelineSize(-1), 'Unknown size')
})

test('modified timestamps have a safe fallback', () => {
  assert.notEqual(formatPipelineModifiedAt('2026-09-17T10:00:00Z'), 'Unknown date')
  assert.equal(formatPipelineModifiedAt('not-a-date'), 'Unknown date')
})

test('download descriptor preserves UTF-8 content and rejects unsafe names', () => {
  assert.deepEqual(pipelineDownloadDescriptor({ ...metadata, content: 'name: zażółć\n' }), {
    content: 'name: zażółć\n',
    filename: 'pipeline.yml',
    mimeType: 'text/plain;charset=utf-8',
  })
  assert.throws(
    () => pipelineDownloadDescriptor({ ...metadata, filename: '../pipeline.yml', content: 'bad' }),
    /Invalid saved pipeline/,
  )
})

test('selected detail is retained only while its list hash is current', () => {
  const detail = { ...metadata, content: 'stages: []\n' }
  assert.equal(reconcileSelectedPipeline(detail, [metadata]), detail)
  assert.equal(isSavedPipelineDetailCurrent(detail, [metadata]), true)

  const overwritten = { ...metadata, sha256: 'b'.repeat(64) }
  assert.equal(reconcileSelectedPipeline(detail, [overwritten]), null)
  assert.equal(isSavedPipelineDetailCurrent(detail, [overwritten]), false)
  assert.equal(reconcileSelectedPipeline(detail, []), null)
})
