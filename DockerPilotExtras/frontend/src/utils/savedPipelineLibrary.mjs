const PIPELINE_FILENAMES = new Set(['.gitlab-ci.yml', 'Jenkinsfile', 'pipeline.yml'])

export function normalizeSavedPipelines(value) {
  if (!Array.isArray(value)) return []

  return value.filter((item) => (
    item &&
    PIPELINE_FILENAMES.has(item.filename) &&
    typeof item.type === 'string' &&
    Number.isInteger(item.size_bytes) &&
    item.size_bytes >= 0 &&
    typeof item.modified_at === 'string' &&
    typeof item.sha256 === 'string'
  ))
}

export function pipelineLanguage(type) {
  return type === 'jenkins' ? 'groovy' : 'yaml'
}

export function formatPipelineSize(sizeBytes) {
  if (!Number.isFinite(sizeBytes) || sizeBytes < 0) return 'Unknown size'
  if (sizeBytes < 1024) return `${sizeBytes} B`
  return `${(sizeBytes / 1024).toFixed(1)} KiB`
}

export function formatPipelineModifiedAt(value) {
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime()) ? 'Unknown date' : timestamp.toLocaleString()
}

export function pipelineDownloadDescriptor(pipeline) {
  if (!pipeline || !PIPELINE_FILENAMES.has(pipeline.filename) || typeof pipeline.content !== 'string') {
    throw new TypeError('Invalid saved pipeline')
  }

  return {
    content: pipeline.content,
    filename: pipeline.filename,
    mimeType: 'text/plain;charset=utf-8',
  }
}

export function reconcileSelectedPipeline(selectedPipeline, savedPipelines) {
  if (!selectedPipeline) return null
  const currentMetadata = normalizeSavedPipelines(savedPipelines).find(
    item => item.filename === selectedPipeline.filename,
  )
  return currentMetadata?.sha256 === selectedPipeline.sha256 ? selectedPipeline : null
}

export function isSavedPipelineDetailCurrent(pipeline, savedPipelines) {
  if (!pipeline || typeof pipeline.content !== 'string') return false
  const currentMetadata = normalizeSavedPipelines(savedPipelines).find(
    item => item.filename === pipeline.filename,
  )
  return Boolean(currentMetadata && currentMetadata.sha256 === pipeline.sha256)
}
