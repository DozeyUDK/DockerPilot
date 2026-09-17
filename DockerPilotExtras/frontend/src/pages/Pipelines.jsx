import React, { useState, useEffect, useRef } from 'react'
import { pipelineAPI, dockerAPI, fileBrowserAPI } from '../services/api'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useTheme } from '../contexts/ThemeContext'
import { PIPELINE_PRESETS, applyPreset, formSnapshot, isSnapshotCurrent, orderedStages, validatePipelineForm } from '../utils/pipelineWorkbench.mjs'
import {
  formatPipelineModifiedAt,
  formatPipelineSize,
  isSavedPipelineDetailCurrent,
  normalizeSavedPipelines,
  pipelineDownloadDescriptor,
  pipelineLanguage,
  reconcileSelectedPipeline,
} from '../utils/savedPipelineLibrary.mjs'
import '../App.css'

function Pipelines() {
  const { theme } = useTheme()
  const [formData, setFormData] = useState({
    type: 'gitlab',
    project_name: '',
    docker_image: 'myapp:latest',
    dockerfile: './Dockerfile',
    stages: ['build', 'test', 'scan'],
    env_vars: 'ENV=production',
    test_commands: 'npm test\nnpm run lint',
    deploy_strategy: 'rolling',
    image_tag_strategy: 'branch-sha',
    enable_environments: false,
    enable_rollback_job: false,
    scan_severity: 'HIGH,CRITICAL',
    scan_fail_on_findings: true,
    smoke_test_url: '',
    smoke_test_retries: 10,
    runner_tags: 'docker,linux',
    use_cache: true,
    agent: 'any',
    credentials_id: 'docker-credentials'
  })
  
  const [generatedPipeline, setGeneratedPipeline] = useState('')
  const [filename, setFilename] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [validationErrors, setValidationErrors] = useState({})
  const generationRequest = useRef(0)
  const [generatedType, setGeneratedType] = useState('gitlab')
  const [generatedSnapshot, setGeneratedSnapshot] = useState(null)
  const [dockerImages, setDockerImages] = useState([])
  const [dockerImagesFull, setDockerImagesFull] = useState([])
  const [dockerfiles, setDockerfiles] = useState([])
  const [dockerfilesFull, setDockerfilesFull] = useState([])
  const [loadingImages, setLoadingImages] = useState(false)
  const [loadingDockerfiles, setLoadingDockerfiles] = useState(false)
  const [imageSearch, setImageSearch] = useState('')
  const [dockerfileSearch, setDockerfileSearch] = useState('')
  const [showFileBrowser, setShowFileBrowser] = useState(false)
  const [showImageBrowser, setShowImageBrowser] = useState(false)
  const [browserPath, setBrowserPath] = useState('')
  const [browserItems, setBrowserItems] = useState([])
  const [loadingBrowser, setLoadingBrowser] = useState(false)
  const [savedPipelines, setSavedPipelines] = useState([])
  const [selectedPipeline, setSelectedPipeline] = useState(null)
  const [loadingSavedPipelines, setLoadingSavedPipelines] = useState(false)
  const [loadingSavedPipeline, setLoadingSavedPipeline] = useState(false)
  const [savedPipelineError, setSavedPipelineError] = useState('')
  const savedListRequest = useRef(0)
  const savedDetailRequest = useRef(0)
  const savedPipelinesRef = useRef([])

  useEffect(() => {
    loadDockerImages()
    loadDockerfiles()
    loadSavedPipelines()
  }, [])

  const loadSavedPipelines = async () => {
    const requestId = ++savedListRequest.current
    ++savedDetailRequest.current
    setLoadingSavedPipelines(true)
    setLoadingSavedPipeline(false)
    setSavedPipelineError('')
    try {
      const response = await pipelineAPI.saved()
      if (requestId !== savedListRequest.current) return
      const pipelines = normalizeSavedPipelines(response.data?.pipelines)
      savedPipelinesRef.current = pipelines
      setSavedPipelines(pipelines)
      setSelectedPipeline(current => reconcileSelectedPipeline(current, pipelines))
    } catch (error) {
      if (requestId !== savedListRequest.current) return
      setSavedPipelineError(error.response?.data?.error || 'Could not load saved pipelines')
    } finally {
      if (requestId === savedListRequest.current) setLoadingSavedPipelines(false)
    }
  }

  const openSavedPipeline = async (savedFilename) => {
    const requestId = ++savedDetailRequest.current
    setLoadingSavedPipeline(true)
    setSavedPipelineError('')
    try {
      const response = await pipelineAPI.readSaved(savedFilename)
      if (requestId !== savedDetailRequest.current) return
      const pipeline = response.data?.pipeline
      const metadata = normalizeSavedPipelines([pipeline])[0]
      if (!metadata || !isSavedPipelineDetailCurrent(pipeline, savedPipelinesRef.current)) {
        throw new TypeError('Invalid saved pipeline response')
      }
      setSelectedPipeline(pipeline)
    } catch (error) {
      if (requestId !== savedDetailRequest.current) return
      setSavedPipelineError(error.response?.data?.error || 'Could not open saved pipeline')
    } finally {
      if (requestId === savedDetailRequest.current) setLoadingSavedPipeline(false)
    }
  }

  const loadDockerImages = async () => {
    setLoadingImages(true)
    try {
      const response = await dockerAPI.images()
      if (response.data.success) {
        setDockerImages(response.data.images || [])
        setDockerImagesFull(response.data.images_full || [])
      }
    } catch (error) {
      console.error('Error loading Docker images:', error)
    } finally {
      setLoadingImages(false)
    }
  }

  const openImageBrowser = () => {
    setShowImageBrowser(true)
    loadDockerImages()
  }

  const selectImageFromBrowser = (imageName) => {
    setFormData(prev => ({ ...prev, docker_image: imageName }))
    setShowImageBrowser(false)
  }

  const loadDockerfiles = async () => {
    setLoadingDockerfiles(true)
    try {
      const response = await dockerAPI.dockerfiles()
      if (response.data.success) {
        setDockerfiles(response.data.dockerfiles || [])
        setDockerfilesFull(response.data.dockerfiles_full || [])
      }
    } catch (error) {
      console.error('Error loading Dockerfiles:', error)
    } finally {
      setLoadingDockerfiles(false)
    }
  }

  const loadFileBrowser = async (path = '') => {
    setLoadingBrowser(true)
    try {
      const response = await fileBrowserAPI.browse(path)
      if (response.data.success) {
        setBrowserPath(response.data.current_path)
        setBrowserItems(response.data.items || [])
      }
    } catch (error) {
      console.error('Error loading file browser:', error)
    } finally {
      setLoadingBrowser(false)
    }
  }

  const openFileBrowser = () => {
    setShowFileBrowser(true)
    loadFileBrowser()
  }

  const selectFileFromBrowser = (filePath) => {
    // Convert full path to relative if possible
    const currentDir = window.location.pathname.includes('/') ? './' : '.'
    setFormData(prev => ({ ...prev, dockerfile: filePath }))
    setShowFileBrowser(false)
  }

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }))
  }

  const filteredImages = dockerImagesFull.filter(img => 
    img.name.toLowerCase().includes(imageSearch.toLowerCase()) ||
    img.repository.toLowerCase().includes(imageSearch.toLowerCase()) ||
    img.tag.toLowerCase().includes(imageSearch.toLowerCase())
  )

  const filteredDockerfiles = dockerfilesFull.filter(df => 
    df.relative.toLowerCase().includes(dockerfileSearch.toLowerCase()) ||
    df.full.toLowerCase().includes(dockerfileSearch.toLowerCase())
  )

  const handleStageChange = (stage) => {
    setFormData(prev => ({
      ...prev,
      stages: prev.stages.includes(stage)
        ? prev.stages.filter(s => s !== stage)
        : [...prev.stages, stage]
    }))
  }

  const handlePreset = (presetName) => {
    setFormData(prev => applyPreset(prev, presetName))
    setValidationErrors({})
    setMessage({ type: 'success', text: `${PIPELINE_PRESETS[presetName].label} preset applied.` })
  }

  const handleGenerate = async () => {
    const errors = validatePipelineForm(formData)
    setValidationErrors(errors)
    if (Object.keys(errors).length > 0) {
      setMessage({ type: 'error', text: 'Fix the highlighted fields before generating.' })
      return
    }
    setLoading(true)
    setMessage(null)
    const requestSnapshot = formSnapshot(formData)
    const requestId = ++generationRequest.current
    
    try {
      const response = await pipelineAPI.generate(formData)
      if (requestId !== generationRequest.current) return
      if (response.data.success) {
        setGeneratedPipeline(response.data.content)
        setFilename(response.data.filename)
        setGeneratedType(response.data.type || formData.type)
        setGeneratedSnapshot(requestSnapshot)
        setMessage({ type: 'success', text: 'Preview generated for the submitted configuration.' })
      }
    } catch (error) {
      if (requestId !== generationRequest.current) return
      setValidationErrors(error.response?.data?.fields || {})
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error generating pipeline' 
      })
    } finally {
      if (requestId === generationRequest.current) setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!previewIsCurrent) {
      setMessage({ type: 'error', text: 'Generate pipeline first' })
      return
    }

    try {
      const response = await pipelineAPI.save({
        content: generatedPipeline,
        filename: filename
      })
      if (response.data.success) {
        setMessage({ type: 'success', text: `Pipeline saved: ${response.data.path}` })
        loadSavedPipelines()
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error saving' 
      })
    }
  }

  const handleDownload = () => {
    if (!previewIsCurrent) return
    
    const blob = new Blob([generatedPipeline], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleSavedDownload = () => {
    if (!selectedPipeline) return
    const download = pipelineDownloadDescriptor(selectedPipeline)
    const blob = new Blob([download.content], { type: download.mimeType })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = download.filename
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    URL.revokeObjectURL(url)
  }

  const previewMatchesForm = Boolean(generatedPipeline && isSnapshotCurrent(formData, generatedSnapshot))
  const previewIsCurrent = previewMatchesForm && !loading

  return (
    <div>
      <h2>CI/CD Pipeline Generator</h2>
      
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="two-column">
        {/* Left: Configuration Form */}
        <div className="card">
          <h3 className="card-title">Pipeline Configuration</h3>

          <div className="form-group">
            <label>Start with a preset:</label>
            <div className="btn-group">
              {Object.entries(PIPELINE_PRESETS).map(([name, preset]) => (
                <button key={name} type="button" className="btn btn-secondary" onClick={() => handlePreset(name)}>
                  {preset.label}
                </button>
              ))}
            </div>
            <small style={{ color: 'var(--text-secondary)' }}>Node.js and Python presets run build, test, and scan. Delivery adds deploy and smoke checks. Presets keep your project, image, Dockerfile, and provider choices.</small>
            <small style={{ color: 'var(--text-secondary)', display: 'block', marginTop: '0.25rem' }}>Delivery requires Docker and DockerPilot on the runner; test tools must be present in the image.</small>
          </div>

          <div className="form-group">
            <label>Stage overview:</label>
            <div className="pipeline-stage-overview">
              {orderedStages(formData.stages).map(({ stage, selected }, index) => (
                <span key={stage} className={selected ? 'stage-chip stage-chip-active' : 'stage-chip'}>
                  {index + 1}. {stage}{selected ? ' ✓' : ''}
                </span>
              ))}
            </div>
            <small style={{ color: 'var(--text-secondary)' }}>Selected stages run in this order.</small>
          </div>
          
          <div className="form-group">
            <label>Pipeline type:</label>
            <select name="type" value={formData.type} onChange={handleChange}>
              <option value="gitlab">GitLab CI</option>
              <option value="jenkins">Jenkins</option>
            </select>
          </div>

          <div className="form-group">
            <label>Project name:</label>
            <input
              type="text"
              name="project_name"
              value={formData.project_name}
              onChange={handleChange}
              placeholder="myapp"
            />
            {validationErrors.project_name && <small className="field-error">{validationErrors.project_name}</small>}
          </div>

          <div className="form-group">
            <label>
              Docker Image:
              <button 
                type="button"
                onClick={loadDockerImages}
                disabled={loadingImages}
                style={{ 
                  marginLeft: '0.5rem', 
                  padding: '0.25rem 0.5rem', 
                  fontSize: '0.85rem',
                  cursor: loadingImages ? 'not-allowed' : 'pointer'
                }}
                title="Refresh image list"
              >
                {loadingImages ? '⏳' : '🔄'}
              </button>
              <button 
                type="button"
                onClick={openImageBrowser}
                style={{ 
                  marginLeft: '0.5rem', 
                  padding: '0.25rem 0.5rem', 
                  fontSize: '0.85rem',
                  cursor: 'pointer'
                }}
                title="Browse Docker images"
              >
                🐳
              </button>
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type="text"
                name="docker_image"
                value={formData.docker_image}
                onChange={(e) => {
                  handleChange(e)
                  setImageSearch(e.target.value)
                }}
                onFocus={() => setImageSearch(formData.docker_image)}
                onBlur={() => setTimeout(() => setImageSearch(''), 200)}
                placeholder="Select or enter an image..."
                list="docker-images-list"
                style={{ width: '100%' }}
              />
              {imageSearch && filteredImages.length > 0 && (
                <div style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  right: 0,
                  backgroundColor: 'white',
                  border: '1px solid #ccc',
                  borderRadius: '4px',
                  maxHeight: '200px',
                  overflowY: 'auto',
                  zIndex: 1000,
                  boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                }}>
                  {filteredImages.slice(0, 20).map((img, idx) => (
                    <div
                      key={idx}
                      onClick={() => {
                        setFormData(prev => ({ ...prev, docker_image: img.name }))
                                            setImageSearch('')
                      }}
                      style={{
                        padding: '0.5rem',
                        cursor: 'pointer',
                        borderBottom: '1px solid #eee'
                      }}
                      onMouseEnter={(e) => e.target.style.backgroundColor = '#f0f0f0'}
                      onMouseLeave={(e) => e.target.style.backgroundColor = 'white'}
                      title={`ID: ${img.id || 'N/A'}, Size: ${img.size || 'N/A'}`}
                    >
                      <div style={{ fontWeight: 'bold' }}>{img.name}</div>
                      {img.size && (
                        <div style={{ fontSize: '0.85rem', color: '#666' }}>
                          {img.size} {img.id && `• ID: ${img.id}`}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            {dockerImages.length > 0 && (
              <small style={{ color: '#666', fontSize: '0.85rem' }}>
                {dockerImages.length} images available
              </small>
            )}
            {validationErrors.docker_image && <small className="field-error">{validationErrors.docker_image}</small>}
          </div>

          <div className="form-group">
            <label>
              Dockerfile path:
              <button 
                type="button"
                onClick={loadDockerfiles}
                disabled={loadingDockerfiles}
                style={{ 
                  marginLeft: '0.5rem', 
                  padding: '0.25rem 0.5rem', 
                  fontSize: '0.85rem',
                  cursor: loadingDockerfiles ? 'not-allowed' : 'pointer'
                }}
                title="Refresh Dockerfile list"
              >
                {loadingDockerfiles ? '⏳' : '🔄'}
              </button>
              <button 
                type="button"
                onClick={openFileBrowser}
                style={{ 
                  marginLeft: '0.5rem', 
                  padding: '0.25rem 0.5rem', 
                  fontSize: '0.85rem',
                  cursor: 'pointer'
                }}
                title="Browse files"
              >
                📁
              </button>
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type="text"
                name="dockerfile"
                value={formData.dockerfile}
                onChange={(e) => {
                  handleChange(e)
                  setDockerfileSearch(e.target.value)
                }}
                onFocus={() => setDockerfileSearch(formData.dockerfile)}
                onBlur={() => setTimeout(() => setDockerfileSearch(''), 200)}
                placeholder="Select or enter a path..."
                list="dockerfiles-list"
                style={{ width: '100%' }}
              />
              {dockerfileSearch && filteredDockerfiles.length > 0 && (
                <div style={{
                  position: 'absolute',
                  top: '100%',
                  left: 0,
                  right: 0,
                  backgroundColor: 'white',
                  border: '1px solid #ccc',
                  borderRadius: '4px',
                  maxHeight: '200px',
                  overflowY: 'auto',
                  zIndex: 1000,
                  boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                }}>
                  {filteredDockerfiles.slice(0, 20).map((df, idx) => (
                    <div
                      key={idx}
                      onClick={() => {
                        setFormData(prev => ({ ...prev, dockerfile: df.relative }))
                                            setDockerfileSearch('')
                      }}
                      style={{
                        padding: '0.5rem',
                        cursor: 'pointer',
                        borderBottom: '1px solid #eee'
                      }}
                      onMouseEnter={(e) => e.target.style.backgroundColor = '#f0f0f0'}
                      onMouseLeave={(e) => e.target.style.backgroundColor = 'white'}
                      title={df.full}
                    >
                      <div style={{ fontWeight: 'bold' }}>{df.relative}</div>
                      <div style={{ fontSize: '0.85rem', color: '#666' }}>{df.full}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {dockerfiles.length > 0 && (
              <small style={{ color: '#666', fontSize: '0.85rem' }}>
                {dockerfiles.length} Dockerfiles found
              </small>
            )}
            {validationErrors.dockerfile && <small className="field-error">{validationErrors.dockerfile}</small>}
          </div>

          <div className="form-group">
            <label>Build stages:</label>
            <div className="checkbox-group">
              {['build', 'test', 'scan', 'deploy', 'smoke'].map(stage => (
                <div key={stage} className="checkbox-item">
                  <input
                    type="checkbox"
                    id={`stage-${stage}`}
                    checked={formData.stages.includes(stage)}
                    onChange={() => handleStageChange(stage)}
                  />
                  <label htmlFor={`stage-${stage}`}>{stage.charAt(0).toUpperCase() + stage.slice(1)}</label>
                </div>
              ))}
            </div>
            {validationErrors.stages && <small className="field-error">{validationErrors.stages}</small>}
          </div>

          <div className="form-group">
            <label>Environment variables:</label>
            <textarea
              name="env_vars"
              value={formData.env_vars}
              onChange={handleChange}
              rows="3"
            />
          </div>

          {formData.stages.includes('test') && (
            <div className="form-group">
              <label>Container test commands (one per line):</label>
              <textarea
                name="test_commands"
                value={formData.test_commands}
                onChange={handleChange}
                rows="3"
                placeholder="npm test&#10;npm run lint"
              />
              {validationErrors.test_commands && <small className="field-error">{validationErrors.test_commands}</small>}
            </div>
          )}

          <div className="form-group">
            <label>Deployment strategy:</label>
            <select name="deploy_strategy" value={formData.deploy_strategy} onChange={handleChange}>
              <option value="rolling">Rolling</option>
              <option value="blue-green">Blue-Green</option>
              <option value="canary">Canary</option>
            </select>
          </div>

          <div className="form-group">
            <label>Image tag strategy:</label>
            <select name="image_tag_strategy" value={formData.image_tag_strategy} onChange={handleChange}>
              <option value="branch-sha">branch-sha (recommended)</option>
              <option value="sha">commit-sha</option>
              <option value="tag-or-sha">git-tag or sha</option>
              <option value="latest">latest</option>
              <option value="static">use tag from docker image field</option>
            </select>
          </div>

          <div className="form-group">
            <div className="checkbox-item">
              <input
                type="checkbox"
                id="enable_environments"
                name="enable_environments"
                checked={formData.enable_environments}
                onChange={handleChange}
              />
              <label htmlFor="enable_environments">Multi-environment deploy flow (DEV → STAGING → PROD)</label>
            </div>
          </div>

          <div className="form-group">
            <div className="checkbox-item">
              <input
                type="checkbox"
                id="enable_rollback_job"
                name="enable_rollback_job"
                checked={formData.enable_rollback_job}
                onChange={handleChange}
              />
              <label htmlFor="enable_rollback_job">Generate manual rollback job</label>
            </div>
          </div>

          {formData.stages.includes('scan') && (
            <div className="form-group">
              <label>Security scan severity:</label>
              <select name="scan_severity" value={formData.scan_severity} onChange={handleChange}>
                <option value="HIGH,CRITICAL">HIGH,CRITICAL</option>
                <option value="MEDIUM,HIGH,CRITICAL">MEDIUM,HIGH,CRITICAL</option>
                <option value="CRITICAL">CRITICAL only</option>
              </select>
              <div className="checkbox-item" style={{ marginTop: '0.5rem' }}>
                <input
                  type="checkbox"
                  id="scan_fail_on_findings"
                  name="scan_fail_on_findings"
                  checked={formData.scan_fail_on_findings}
                  onChange={handleChange}
                />
                <label htmlFor="scan_fail_on_findings">Fail pipeline when vulnerabilities are found</label>
              </div>
            </div>
          )}

          {formData.stages.includes('smoke') && (
            <div className="form-group">
              <label>Smoke test URL (supports {'{env}'} placeholder):</label>
              <input
                type="text"
                name="smoke_test_url"
                value={formData.smoke_test_url}
                onChange={handleChange}
                placeholder="https://dev.example.com/health or https://{env}.example.com/health"
              />
              {validationErrors.smoke_test_url && <small className="field-error">{validationErrors.smoke_test_url}</small>}
              <label style={{ marginTop: '0.5rem' }}>Smoke retries:</label>
              <input
                type="number"
                min="1"
                max="60"
                name="smoke_test_retries"
                value={formData.smoke_test_retries}
                onChange={handleChange}
              />
              {validationErrors.smoke_test_retries && <small className="field-error">{validationErrors.smoke_test_retries}</small>}
            </div>
          )}

          {formData.type === 'gitlab' && (
            <>
              <div className="form-group">
                <label>Runner tags:</label>
                <input
                  type="text"
                  name="runner_tags"
                  value={formData.runner_tags}
                  onChange={handleChange}
                />
              </div>
              <div className="form-group">
                <div className="checkbox-item">
                  <input
                    type="checkbox"
                    id="use_cache"
                    name="use_cache"
                    checked={formData.use_cache}
                    onChange={handleChange}
                  />
                  <label htmlFor="use_cache">Use cache</label>
                </div>
              </div>
            </>
          )}

          {formData.type === 'jenkins' && (
            <>
              <div className="form-group">
                <label>Agent:</label>
                <input
                  type="text"
                  name="agent"
                  value={formData.agent}
                  onChange={handleChange}
                />
              </div>
              <div className="form-group">
                <label>Credentials ID:</label>
                <input
                  type="text"
                  name="credentials_id"
                  value={formData.credentials_id}
                  onChange={handleChange}
                />
              </div>
            </>
          )}

          <div className="btn-group">
            <button className="btn btn-primary" onClick={handleGenerate} disabled={loading}>
              {loading ? 'Generating...' : 'Generate Pipeline'}
            </button>
          </div>
        </div>

        {/* Right: Preview */}
        <div className="card">
          <h3 className="card-title">Pipeline Preview</h3>
          <div className="btn-group" style={{ marginBottom: '1rem' }}>
            <button 
              className="btn btn-secondary" 
              onClick={handleSave}
              disabled={!previewIsCurrent}
            >
              Save
            </button>
            <button 
              className="btn btn-success" 
              onClick={handleDownload}
              disabled={!previewIsCurrent}
            >
              Download
            </button>
          </div>
          
          {generatedPipeline && !previewMatchesForm && (
            <div className="alert alert-warning">Configuration changed. Generate again to refresh this preview.</div>
          )}
          {loading && <div className="spinner"></div>}
          
          {generatedPipeline && (
            <>
            <small style={{ color: 'var(--text-secondary)', display: 'block', marginBottom: '0.5rem' }}>
              Preview syntax: {generatedType === 'gitlab' ? 'GitLab CI (YAML)' : 'Jenkins (Groovy)'}
            </small>
            <SyntaxHighlighter
              language={generatedType === 'gitlab' ? 'yaml' : 'groovy'}
              style={vscDarkPlus}
              customStyle={{ borderRadius: '4px' }}
            >
              {generatedPipeline}
            </SyntaxHighlighter>
            </>
          )}
          
          {!generatedPipeline && !loading && (
            <p style={{ color: '#666', textAlign: 'center', padding: '2rem' }}>
              Generate a pipeline to see the preview
            </p>
          )}
        </div>
      </div>

      <section className="card saved-pipelines-card" aria-labelledby="saved-pipelines-title">
        <div className="saved-pipelines-header">
          <div>
            <h3 id="saved-pipelines-title" className="card-title">Saved Pipelines</h3>
            <p className="saved-pipelines-description">Open or download generated pipeline artifacts. Saved files are read-only here.</p>
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={loadSavedPipelines}
            disabled={loadingSavedPipelines}
          >
            {loadingSavedPipelines ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>

        {savedPipelineError && <div className="alert alert-error">{savedPipelineError}</div>}

        <div className="saved-pipelines-layout">
          <div className="saved-pipeline-list" aria-live="polite">
            {loadingSavedPipelines && savedPipelines.length === 0 && <div className="spinner"></div>}
            {!loadingSavedPipelines && savedPipelines.length === 0 && (
              <p className="saved-pipelines-empty">No saved pipelines yet. Generate and save one above.</p>
            )}
            {savedPipelines.map(pipeline => (
              <div
                key={pipeline.filename}
                className={`saved-pipeline-row${selectedPipeline?.filename === pipeline.filename ? ' selected' : ''}`}
              >
                <div className="saved-pipeline-summary">
                  <strong>{pipeline.filename}</strong>
                  <span>{pipeline.type} · {formatPipelineSize(pipeline.size_bytes)}</span>
                  <span>Modified {formatPipelineModifiedAt(pipeline.modified_at)}</span>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => openSavedPipeline(pipeline.filename)}
                  disabled={loadingSavedPipelines}
                >
                  Open
                </button>
              </div>
            ))}
          </div>

          <div className="saved-pipeline-detail">
            {loadingSavedPipeline && <div className="spinner"></div>}
            {!loadingSavedPipeline && !selectedPipeline && (
              <p className="saved-pipelines-empty">Select a saved pipeline to preview it.</p>
            )}
            {!loadingSavedPipeline && selectedPipeline && (
              <>
                <div className="saved-pipeline-detail-header">
                  <div>
                    <strong>{selectedPipeline.filename}</strong>
                    <span>{formatPipelineSize(selectedPipeline.size_bytes)}</span>
                  </div>
                  <button type="button" className="btn btn-success" onClick={handleSavedDownload}>Download</button>
                </div>
                <SyntaxHighlighter
                  language={pipelineLanguage(selectedPipeline.type)}
                  style={vscDarkPlus}
                  customStyle={{ borderRadius: '4px', maxHeight: '420px' }}
                >
                  {selectedPipeline.content}
                </SyntaxHighlighter>
              </>
            )}
          </div>
        </div>
      </section>

      {/* File Browser Modal */}
      {showFileBrowser && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }} onClick={() => setShowFileBrowser(false)}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '1.5rem',
            maxWidth: '600px',
            maxHeight: '80vh',
            width: '90%',
            overflow: 'auto',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ color: 'var(--text-primary)' }}>Browse files</h3>
              <button 
                onClick={() => setShowFileBrowser(false)}
                style={{ 
                  background: 'none', 
                  border: 'none', 
                  fontSize: '1.5rem', 
                  cursor: 'pointer',
                  padding: '0 0.5rem',
                  color: 'var(--text-primary)'
                }}
              >
                ×
              </button>
            </div>
            
            <div style={{ marginBottom: '1rem' }}>
              <div style={{ 
                display: 'flex', 
                gap: '0.5rem', 
                alignItems: 'center',
                marginBottom: '0.5rem'
              }}>
                <button 
                  onClick={() => {
                    const parentPath = browserPath.split('/').slice(0, -1).join('/') || '/'
                    loadFileBrowser(parentPath)
                  }}
                  disabled={!browserPath || browserPath === '/' || browserPath.split('/').length <= 1}
                  style={{ padding: '0.25rem 0.5rem' }}
                >
                  ↑ Back
                </button>
                <input
                  type="text"
                  value={browserPath}
                  onChange={(e) => setBrowserPath(e.target.value)}
                  onKeyPress={(e) => {
                    if (e.key === 'Enter') {
                      loadFileBrowser(browserPath)
                    }
                  }}
                  style={{ 
                    flex: 1, 
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px'
                  }}
                  placeholder="Enter path..."
                />
                <button 
                  onClick={() => loadFileBrowser(browserPath)}
                  style={{ padding: '0.5rem 1rem' }}
                >
                  Go
                </button>
              </div>
            </div>

            {loadingBrowser ? (
              <div style={{ textAlign: 'center', padding: '2rem' }}>Loading...</div>
            ) : (
              <div style={{ 
                border: '1px solid var(--border-color)', 
                borderRadius: '4px',
                maxHeight: '400px',
                overflowY: 'auto',
                backgroundColor: 'var(--bg-tertiary)'
              }}>
                {browserItems.length === 0 ? (
                  <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                    Empty directory
                  </div>
                ) : (
                  browserItems.map((item, idx) => (
                    <div
                      key={idx}
                      onClick={() => {
                        if (item.is_dir) {
                          loadFileBrowser(item.path)
                        } else if (item.name.toLowerCase().includes('dockerfile')) {
                          selectFileFromBrowser(item.path)
                        }
                      }}
                      style={{
                        padding: '0.75rem',
                        cursor: item.is_dir || item.name.toLowerCase().includes('dockerfile') ? 'pointer' : 'default',
                        borderBottom: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        backgroundColor: item.name.toLowerCase().includes('dockerfile') 
                          ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.2)' : '#e8f5e9')
                          : 'var(--card-bg)',
                        color: 'var(--text-primary)'
                      }}
                      onMouseEnter={(e) => {
                        if (item.is_dir || item.name.toLowerCase().includes('dockerfile')) {
                          e.target.style.backgroundColor = 'var(--bg-tertiary)'
                        }
                      }}
                      onMouseLeave={(e) => {
                        e.target.style.backgroundColor = item.name.toLowerCase().includes('dockerfile') 
                          ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.2)' : '#e8f5e9')
                          : 'var(--card-bg)'
                      }}
                    >
                      <span style={{ fontSize: '1.2rem' }}>
                        {item.is_dir ? '📁' : item.name.toLowerCase().includes('dockerfile') ? '🐳' : '📄'}
                      </span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: item.is_dir ? 'bold' : 'normal' }}>
                          {item.name}
                        </div>
                        {item.is_file && (
                          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                            {(item.size / 1024).toFixed(2)} KB
                          </div>
                        )}
                      </div>
                      {item.name.toLowerCase().includes('dockerfile') && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            selectFileFromBrowser(item.path)
                          }}
                          style={{
                            padding: '0.25rem 0.5rem',
                            backgroundColor: '#007bff',
                            color: 'white',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: 'pointer'
                          }}
                        >
                          Select
                        </button>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Docker Images Browser Modal */}
      {showImageBrowser && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }} onClick={() => setShowImageBrowser(false)}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '1.5rem',
            maxWidth: '700px',
            maxHeight: '80vh',
            width: '90%',
            overflow: 'auto',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ color: 'var(--text-primary)' }}>Browse Docker images</h3>
              <button 
                onClick={() => setShowImageBrowser(false)}
                style={{ 
                  background: 'none', 
                  border: 'none', 
                  fontSize: '1.5rem', 
                  cursor: 'pointer',
                  padding: '0 0.5rem',
                  color: 'var(--text-primary)'
                }}
              >
                ×
              </button>
            </div>

            <div style={{ marginBottom: '1rem' }}>
              <input
                type="text"
                placeholder="Search images..."
                value={imageSearch}
                onChange={(e) => setImageSearch(e.target.value)}
                style={{ 
                  width: '100%', 
                  padding: '0.5rem',
                  backgroundColor: 'var(--input-bg)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--input-border)',
                  borderRadius: '4px'
                }}
              />
            </div>

            {loadingImages ? (
              <div style={{ textAlign: 'center', padding: '2rem' }}>Loading images...</div>
            ) : (
              <div style={{ 
                border: '1px solid var(--border-color)', 
                borderRadius: '4px',
                maxHeight: '400px',
                overflowY: 'auto',
                backgroundColor: 'var(--bg-tertiary)'
              }}>
                {filteredImages.length === 0 ? (
                  <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                    {imageSearch ? 'No images found matching the search' : 'No Docker images'}
                  </div>
                ) : (
                  filteredImages.map((img, idx) => (
                    <div
                      key={idx}
                      onClick={() => selectImageFromBrowser(img.name)}
                      style={{
                        padding: '1rem',
                        cursor: 'pointer',
                        borderBottom: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '1rem',
                        backgroundColor: 'var(--card-bg)',
                        color: 'var(--text-primary)'
                      }}
                      onMouseEnter={(e) => e.target.style.backgroundColor = 'var(--bg-tertiary)'}
                      onMouseLeave={(e) => e.target.style.backgroundColor = 'var(--card-bg)'}
                    >
                      <span style={{ fontSize: '2rem' }}>🐳</span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 'bold', fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                          {img.name}
                        </div>
                        <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                          {img.id && <span>ID: <code style={{ backgroundColor: 'var(--bg-tertiary)', padding: '0.2rem 0.4rem', borderRadius: '3px' }}>{img.id}</code></span>}
                          {img.size && <span>Size: <strong>{img.size}</strong></span>}
                          {img.created && <span>Created: {img.created.split(' ')[0]}</span>}
                        </div>
                        {img.repository !== '<none>' && img.tag !== '<none>' && (
                          <div style={{ fontSize: '0.85rem', color: '#999', marginTop: '0.25rem' }}>
                            Repository: {img.repository} • Tag: {img.tag}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          selectImageFromBrowser(img.name)
                        }}
                        style={{
                          padding: '0.5rem 1rem',
                          backgroundColor: '#007bff',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          fontWeight: 'bold'
                        }}
                      >
                        Select
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default Pipelines
