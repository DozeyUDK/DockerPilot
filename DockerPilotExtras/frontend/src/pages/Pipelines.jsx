import React, { useState, useEffect } from 'react'
import { pipelineAPI, dockerAPI, fileBrowserAPI } from '../services/api'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useTheme } from '../contexts/ThemeContext'
import '../App.css'

function Pipelines() {
  const { theme } = useTheme()
  const [formData, setFormData] = useState({
    type: 'gitlab',
    project_name: '',
    docker_image: 'myapp:latest',
    dockerfile: './Dockerfile',
    stages: ['build', 'test', 'deploy'],
    env_vars: 'ENV=production',
    deploy_strategy: 'rolling',
    runner_tags: 'docker,linux',
    use_cache: true,
    agent: 'any',
    credentials_id: 'docker-credentials'
  })
  
  const [generatedPipeline, setGeneratedPipeline] = useState('')
  const [filename, setFilename] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
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

  useEffect(() => {
    loadDockerImages()
    loadDockerfiles()
  }, [])

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

  const handleGenerate = async () => {
    setLoading(true)
    setMessage(null)
    
    try {
      const response = await pipelineAPI.generate(formData)
      if (response.data.success) {
        setGeneratedPipeline(response.data.content)
        setFilename(response.data.filename)
        setMessage({ type: 'success', text: 'Pipeline generated successfully!' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error generating pipeline' 
      })
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!generatedPipeline) {
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
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error saving' 
      })
    }
  }

  const handleDownload = () => {
    if (!generatedPipeline) return
    
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
          </div>

          <div className="form-group">
            <label>Build stages:</label>
            <div className="checkbox-group">
              {['build', 'test', 'deploy'].map(stage => (
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

          <div className="form-group">
            <label>Deployment strategy:</label>
            <select name="deploy_strategy" value={formData.deploy_strategy} onChange={handleChange}>
              <option value="rolling">Rolling</option>
              <option value="blue-green">Blue-Green</option>
              <option value="canary">Canary</option>
            </select>
          </div>

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
              disabled={!generatedPipeline}
            >
              Save
            </button>
            <button 
              className="btn btn-success" 
              onClick={handleDownload}
              disabled={!generatedPipeline}
            >
              Download
            </button>
          </div>
          
          {loading && <div className="spinner"></div>}
          
          {generatedPipeline && (
            <SyntaxHighlighter
              language={formData.type === 'gitlab' ? 'yaml' : 'groovy'}
              style={vscDarkPlus}
              customStyle={{ borderRadius: '4px' }}
            >
              {generatedPipeline}
            </SyntaxHighlighter>
          )}
          
          {!generatedPipeline && !loading && (
            <p style={{ color: '#666', textAlign: 'center', padding: '2rem' }}>
              Generate a pipeline to see the preview
            </p>
          )}
        </div>
      </div>

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

