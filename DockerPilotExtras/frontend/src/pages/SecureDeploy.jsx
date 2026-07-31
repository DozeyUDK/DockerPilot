import React, { useEffect, useMemo, useState } from 'react'
import { secureDeployAPI } from '../services/api'
import { useAuth } from '../contexts/AuthContext'

const EMPTY_SPEC = {
  schema_version: 1,
  metadata: {
    project: 'secure-canary',
    service: 'web',
    owner: '',
    description: '',
    environment: 'production',
    change_ticket: ''
  },
  image: {
    reference: 'docker.io/library/nginx',
    digest: 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    pull_policy: 'if_not_present',
    allow_mutable_tag: false,
    exception_ref: null
  },
  runtime: {
    user: '101:101',
    group: '101',
    read_only: true,
    no_new_privileges: true,
    init: true,
    restart_policy: 'unless-stopped',
    stop_grace_period_seconds: 10,
    cap_drop: ['ALL'],
    cap_add: [],
    pids_limit: 128,
    memory_limit: '256m',
    cpu_limit: '0.5',
    tmpfs: [],
    command: ['nginx', '-g', 'daemon off;'],
    entrypoint: ['nginx']
  },
  network: {
    exposure: 'localhost',
    published_ports: [
      {
        container_port: 80,
        host_port: 18080,
        protocol: 'tcp',
        bind_address: '127.0.0.1',
        exposure: 'localhost'
      }
    ],
    allowed_sources: [],
    networks: ['private'],
    dns: []
  },
  storage: {
    volumes: [
      {
        type: 'named_volume',
        source: 'web_data',
        target: '/var/lib/nginx',
        read_only: false,
        approved_root_ref: null
      }
    ]
  },
  secrets: {
    refs: []
  },
  health: {
    required: true,
    test: ['CMD', 'wget', '-qO-', 'http://127.0.0.1/'],
    interval_seconds: 30,
    timeout_seconds: 5,
    retries: 3,
    start_period_seconds: 10
  },
  deployment: {
    strategy: 'recreate',
    rollback_enabled: true,
    health_timeout_seconds: 120,
    max_unavailable: 1,
    lock_scope: 'project_service'
  },
  exceptions: {
    policy_refs: []
  }
}

function SecureDeploy() {
  const { username, refreshStatus } = useAuth()
  const [step, setStep] = useState(1)
  const [spec, setSpec] = useState(() => ({
    ...EMPTY_SPEC,
    metadata: { ...EMPTY_SPEC.metadata, owner: username || '' }
  }))
  const [message, setMessage] = useState(null)
  const [preview, setPreview] = useState(null)
  const [draftId, setDraftId] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    refreshStatus?.()
  }, [refreshStatus])

  const badge = useMemo(
    () => 'PREVIEW ONLY — NO CHANGES WILL BE APPLIED',
    []
  )

  const update = (path, value) => {
    setSpec((prev) => {
      const next = JSON.parse(JSON.stringify(prev))
      const parts = path.split('.')
      let cursor = next
      for (let i = 0; i < parts.length - 1; i += 1) {
        cursor = cursor[parts[i]]
      }
      cursor[parts[parts.length - 1]] = value
      return next
    })
  }

  const saveDraft = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const { data } = await secureDeployAPI.createDraft(spec)
      setDraftId(data.draft_id)
      setMessage({ type: 'success', text: `Draft saved: ${data.draft_id}` })
    } catch (err) {
      setMessage({
        type: 'error',
        text: err?.response?.data?.error?.message || err.message || 'Draft failed'
      })
    } finally {
      setBusy(false)
    }
  }

  const validate = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const { data } = await secureDeployAPI.validate(spec)
      setMessage({ type: 'success', text: `Valid. spec_sha256=${data.spec_sha256}` })
    } catch (err) {
      setMessage({
        type: 'error',
        text: err?.response?.data?.error?.message || err.message || 'Validation failed'
      })
    } finally {
      setBusy(false)
    }
  }

  const generatePreview = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const { data } = await secureDeployAPI.plan(spec)
      setPreview(data)
      setMessage({
        type: data.status === 'ready' ? 'success' : 'error',
        text: `Preview status: ${data.status}`
      })
      setStep(7)
    } catch (err) {
      setPreview(null)
      setMessage({
        type: 'error',
        text: err?.response?.data?.error?.message || err.message || 'Plan failed'
      })
    } finally {
      setBusy(false)
    }
  }

  const downloadPlan = () => {
    if (!preview?.plan) return
    const blob = new Blob([JSON.stringify(preview.plan, null, 2)], {
      type: 'application/json'
    })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${preview.plan.plan_id || 'secure-deploy-plan'}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="page">
      <div className="card" style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center' }}>
          <div>
            <h2 className="card-title" style={{ marginBottom: '0.35rem' }}>Secure Deploy</h2>
            <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
              Spec → Dozeyguard → firewall plan preview. No apply/approve in this release.
            </p>
          </div>
          <span
            style={{
              background: '#b45309',
              color: 'white',
              padding: '0.45rem 0.75rem',
              borderRadius: '4px',
              fontWeight: 700,
              whiteSpace: 'nowrap'
            }}
          >
            {badge}
          </span>
        </div>
      </div>

      {message && (
        <div className={`alert alert-${message.type === 'success' ? 'success' : 'error'}`} style={{ marginBottom: '1rem' }}>
          {message.text}
        </div>
      )}

      <div className="card" style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
            <button
              key={n}
              type="button"
              className={step === n ? 'btn btn-primary' : 'btn'}
              onClick={() => setStep(n)}
            >
              Step {n}
            </button>
          ))}
        </div>
      </div>

      {step === 1 && (
        <div className="card">
          <h3 className="card-title">Identity</h3>
          <label>Project<input value={spec.metadata.project} onChange={(e) => update('metadata.project', e.target.value)} /></label>
          <label>Service<input value={spec.metadata.service} onChange={(e) => update('metadata.service', e.target.value)} /></label>
          <label>Owner<input value={spec.metadata.owner} onChange={(e) => update('metadata.owner', e.target.value)} /></label>
          <label>
            Environment
            <select value={spec.metadata.environment} onChange={(e) => update('metadata.environment', e.target.value)}>
              <option value="dev">dev</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
          </label>
        </div>
      )}

      {step === 2 && (
        <div className="card">
          <h3 className="card-title">Image</h3>
          <label>Reference<input value={spec.image.reference} onChange={(e) => update('image.reference', e.target.value)} /></label>
          <label>Digest<input value={spec.image.digest} onChange={(e) => update('image.digest', e.target.value)} placeholder="sha256:..." /></label>
          <label>
            Pull policy
            <select value={spec.image.pull_policy} onChange={(e) => update('image.pull_policy', e.target.value)}>
              <option value="never">never</option>
              <option value="if_not_present">if_not_present</option>
              <option value="always">always</option>
            </select>
          </label>
        </div>
      )}

      {step === 3 && (
        <div className="card">
          <h3 className="card-title">Runtime hardening</h3>
          <label>User UID:GID<input value={spec.runtime.user} onChange={(e) => update('runtime.user', e.target.value)} /></label>
          <label>Memory limit<input value={spec.runtime.memory_limit} onChange={(e) => update('runtime.memory_limit', e.target.value)} /></label>
          <label>CPU limit<input value={spec.runtime.cpu_limit} onChange={(e) => update('runtime.cpu_limit', e.target.value)} /></label>
          <label>Healthcheck test (comma-separated)<input value={spec.health.test.join(',')} onChange={(e) => update('health.test', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} /></label>
          <p style={{ color: 'var(--text-secondary)' }}>read_only and no-new-privileges are required true by Spec v1.</p>
        </div>
      )}

      {step === 4 && (
        <div className="card">
          <h3 className="card-title">Network</h3>
          <label>
            Exposure
            <select
              value={spec.network.exposure}
              onChange={(e) => {
                update('network.exposure', e.target.value)
                if (spec.network.published_ports[0]) {
                  update('network.published_ports', [
                    { ...spec.network.published_ports[0], exposure: e.target.value }
                  ])
                }
              }}
            >
              <option value="none">none</option>
              <option value="localhost">localhost</option>
              <option value="lan_allowlist">lan_allowlist</option>
              <option value="zerotier_allowlist">zerotier_allowlist</option>
              <option value="public_via_existing_proxy">public_via_existing_proxy</option>
            </select>
          </label>
          <label>Host port<input type="number" value={spec.network.published_ports[0]?.host_port || ''} onChange={(e) => update('network.published_ports', [{ ...spec.network.published_ports[0], host_port: Number(e.target.value) }])} /></label>
          <label>Allowed CIDRs (comma-separated)<input value={(spec.network.allowed_sources || []).join(',')} onChange={(e) => update('network.allowed_sources', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} /></label>
        </div>
      )}

      {step === 5 && (
        <div className="card">
          <h3 className="card-title">Storage</h3>
          <label>Named volume<input value={spec.storage.volumes[0]?.source || ''} onChange={(e) => update('storage.volumes', [{ ...spec.storage.volumes[0], source: e.target.value }])} /></label>
          <label>Target<input value={spec.storage.volumes[0]?.target || ''} onChange={(e) => update('storage.volumes', [{ ...spec.storage.volumes[0], target: e.target.value }])} /></label>
        </div>
      )}

      {step === 6 && (
        <div className="card">
          <h3 className="card-title">Secrets (refs only)</h3>
          <p style={{ color: 'var(--text-secondary)' }}>
            Enter logical OpenBao reference names only. Values are never accepted or stored in preview.
          </p>
          <label>
            Secret name
            <input
              value={spec.secrets.refs[0]?.name || ''}
              onChange={(e) =>
                update('secrets.refs', e.target.value
                  ? [{
                      name: e.target.value,
                      provider: 'openbao',
                      manifest_service: `${spec.metadata.project}-${spec.metadata.service}`,
                      target: '/run/secrets/app',
                      injection: 'file',
                      environment_key: null
                    }]
                  : [])
              }
            />
          </label>
        </div>
      )}

      {step === 7 && (
        <div className="card">
          <h3 className="card-title">Preview</h3>
          {!preview && <p>Generate a preview to see findings, firewall actions, and hashes.</p>}
          {preview && (
            <div style={{ display: 'grid', gap: '1rem' }}>
              <div>
                <strong>Status:</strong> {preview.status}
                {draftId ? ` · draft ${draftId}` : ''}
              </div>
              <div>
                <strong>Hashes</strong>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(preview.preview?.hashes || {}, null, 2)}</pre>
              </div>
              <div>
                <strong>Dozeyguard findings</strong>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(preview.preview?.dozeyguard?.findings || [], null, 2)}</pre>
              </div>
              <div>
                <strong>Firewall actions</strong>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(preview.preview?.firewall || {}, null, 2)}</pre>
              </div>
              <div>
                <strong>Rollback outline</strong>
                <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(preview.preview?.rollback_outline || [], null, 2)}</pre>
              </div>
              <div>
                <strong>Plan expiry</strong> {preview.plan?.expires_at}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        <button type="button" className="btn" disabled={busy} onClick={saveDraft}>Save draft</button>
        <button type="button" className="btn" disabled={busy} onClick={validate}>Validate</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={generatePreview}>Generate preview</button>
        <button type="button" className="btn" disabled={!preview?.plan} onClick={downloadPlan}>Download redacted plan</button>
      </div>
    </div>
  )
}

export default SecureDeploy
