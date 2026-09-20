import React, { useEffect, useMemo, useState } from 'react'
import { secureDeployAPI } from '../services/api'
import { useAuth } from '../contexts/AuthContext'
import { SECURE_DEPLOY_STEPS } from '../utils/secureDeployForm.mjs'

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
  const [totpCode, setTotpCode] = useState('')
  const [approval, setApproval] = useState(null)
  const [brokerResult, setBrokerResult] = useState(null)

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

  const approvePlan = async () => {
    if (!preview?.plan?.plan_id) return
    setBusy(true)
    setMessage(null)
    try {
      const { data } = await secureDeployAPI.approve(preview.plan.plan_id, {
        plan_sha256: preview.plan.plan_sha256,
        totp_code: totpCode
      })
      setTotpCode('')
      setApproval(data.approval)
      setMessage({ type: 'success', text: `Approved ${data.approval.approval_id}` })
    } catch (err) {
      setMessage({
        type: 'error',
        text: err?.response?.data?.error?.message || err.message || 'Approve failed'
      })
    } finally {
      setBusy(false)
    }
  }

  const verifyWithBroker = async () => {
    if (!preview?.plan?.plan_id || !approval?.approval_id) return
    setBusy(true)
    setMessage(null)
    try {
      const { data } = await secureDeployAPI.brokerDryRun(preview.plan.plan_id, {
        approval_id: approval.approval_id
      })
      setBrokerResult(data.verification)
      setMessage({ type: 'success', text: `Broker dry-run: ${data.verification?.status}` })
    } catch (err) {
      setBrokerResult(null)
      setMessage({
        type: 'error',
        text: err?.response?.data?.error?.message || err.message || 'Broker dry-run failed'
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page secure-deploy-page">
      <section className="card secure-deploy-header" aria-labelledby="secure-deploy-title">
        <div className="secure-deploy-header-copy">
          <h2 className="card-title" id="secure-deploy-title">Secure Deploy</h2>
          <p>
            Spec → Dozeyguard → firewall plan preview. No deployment changes are applied from this screen.
          </p>
        </div>
        <span className="secure-deploy-badge">{badge}</span>
      </section>

      {message && (
        <div
          className={`alert alert-${message.type === 'success' ? 'success' : 'error'}`}
          role={message.type === 'success' ? 'status' : 'alert'}
          aria-live={message.type === 'success' ? 'polite' : 'assertive'}
        >
          {message.text}
        </div>
      )}

      <nav className="card secure-deploy-toolbar" aria-label="Secure deploy steps">
        {SECURE_DEPLOY_STEPS.map(({ number, label }) => (
          <button
            key={number}
            type="button"
            className="secure-deploy-step"
            aria-current={step === number ? 'step' : undefined}
            onClick={() => setStep(number)}
          >
            {number}. {label}
          </button>
        ))}
      </nav>

      {step === 1 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-identity-title">
          <h3 className="card-title" id="secure-deploy-identity-title">Identity</h3>
          <div className="form-field">
            <label htmlFor="secure-deploy-project">Project</label>
            <input className="form-control" id="secure-deploy-project" value={spec.metadata.project} onChange={(e) => update('metadata.project', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-service">Service</label>
            <input className="form-control" id="secure-deploy-service" value={spec.metadata.service} onChange={(e) => update('metadata.service', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-owner">Owner</label>
            <input className="form-control" id="secure-deploy-owner" value={spec.metadata.owner} onChange={(e) => update('metadata.owner', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-environment">Environment</label>
            <select className="form-control" id="secure-deploy-environment" value={spec.metadata.environment} onChange={(e) => update('metadata.environment', e.target.value)}>
              <option value="dev">dev</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-image-title">
          <h3 className="card-title" id="secure-deploy-image-title">Image</h3>
          <div className="form-field">
            <label htmlFor="secure-deploy-image-reference">Reference</label>
            <input className="form-control" id="secure-deploy-image-reference" value={spec.image.reference} onChange={(e) => update('image.reference', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-image-digest">Digest</label>
            <input className="form-control" id="secure-deploy-image-digest" value={spec.image.digest} onChange={(e) => update('image.digest', e.target.value)} placeholder="sha256:..." />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-pull-policy">Pull policy</label>
            <select className="form-control" id="secure-deploy-pull-policy" value={spec.image.pull_policy} onChange={(e) => update('image.pull_policy', e.target.value)}>
              <option value="never">never</option>
              <option value="if_not_present">if_not_present</option>
              <option value="always">always</option>
            </select>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-runtime-title">
          <h3 className="card-title" id="secure-deploy-runtime-title">Runtime hardening</h3>
          <div className="form-field">
            <label htmlFor="secure-deploy-runtime-user">User UID:GID</label>
            <input className="form-control" id="secure-deploy-runtime-user" value={spec.runtime.user} onChange={(e) => update('runtime.user', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-memory-limit">Memory limit</label>
            <input className="form-control" id="secure-deploy-memory-limit" value={spec.runtime.memory_limit} onChange={(e) => update('runtime.memory_limit', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-cpu-limit">CPU limit</label>
            <input className="form-control" id="secure-deploy-cpu-limit" value={spec.runtime.cpu_limit} onChange={(e) => update('runtime.cpu_limit', e.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-healthcheck">Healthcheck test (comma-separated)</label>
            <input className="form-control" id="secure-deploy-healthcheck" value={spec.health.test.join(',')} onChange={(e) => update('health.test', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
          </div>
          <p className="form-hint">read_only and no-new-privileges are required true by Spec v1.</p>
        </section>
      )}

      {step === 4 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-network-title">
          <h3 className="card-title" id="secure-deploy-network-title">Network</h3>
          <div className="form-field">
            <label htmlFor="secure-deploy-exposure">Exposure</label>
            <select
              className="form-control"
              id="secure-deploy-exposure"
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
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-host-port">Host port</label>
            <input className="form-control" id="secure-deploy-host-port" type="number" value={spec.network.published_ports[0]?.host_port || ''} onChange={(e) => update('network.published_ports', [{ ...spec.network.published_ports[0], host_port: Number(e.target.value) }])} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-allowed-cidrs">Allowed CIDRs (comma-separated)</label>
            <input className="form-control" id="secure-deploy-allowed-cidrs" value={(spec.network.allowed_sources || []).join(',')} onChange={(e) => update('network.allowed_sources', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />
          </div>
        </section>
      )}

      {step === 5 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-storage-title">
          <h3 className="card-title" id="secure-deploy-storage-title">Storage</h3>
          <div className="form-field">
            <label htmlFor="secure-deploy-volume-name">Named volume</label>
            <input className="form-control" id="secure-deploy-volume-name" value={spec.storage.volumes[0]?.source || ''} onChange={(e) => update('storage.volumes', [{ ...spec.storage.volumes[0], source: e.target.value }])} />
          </div>
          <div className="form-field">
            <label htmlFor="secure-deploy-volume-target">Target</label>
            <input className="form-control" id="secure-deploy-volume-target" value={spec.storage.volumes[0]?.target || ''} onChange={(e) => update('storage.volumes', [{ ...spec.storage.volumes[0], target: e.target.value }])} />
          </div>
        </section>
      )}

      {step === 6 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-secrets-title">
          <h3 className="card-title" id="secure-deploy-secrets-title">Secrets (refs only)</h3>
          <p className="form-hint" id="secure-deploy-secret-hint">
            Enter logical OpenBao reference names only. Values are never accepted or stored in preview.
          </p>
          <div className="form-field">
            <label htmlFor="secure-deploy-secret-name">Secret name</label>
            <input
              className="form-control"
              id="secure-deploy-secret-name"
              aria-describedby="secure-deploy-secret-hint"
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
          </div>
        </section>
      )}

      {step === 7 && (
        <section className="card secure-deploy-panel" aria-labelledby="secure-deploy-preview-title">
          <h3 className="card-title" id="secure-deploy-preview-title">Preview</h3>
          {!preview && <p>Generate a preview to see findings, firewall actions, and hashes.</p>}
          {preview && (
            <div className="secure-deploy-preview">
              <div>
                <strong>Status:</strong> {preview.status}
                {draftId ? ` · draft ${draftId}` : ''}
              </div>
              <div>
                <strong>Plan hash (confirm before approve)</strong>
                <pre>{preview.plan?.plan_sha256 || preview.preview?.hashes?.plan_sha256}</pre>
              </div>
              <div>
                <strong>Hashes</strong>
                <pre>{JSON.stringify(preview.preview?.hashes || {}, null, 2)}</pre>
              </div>
              <div>
                <strong>Dozeyguard findings</strong>
                <pre>{JSON.stringify(preview.preview?.dozeyguard?.findings || [], null, 2)}</pre>
              </div>
              <div>
                <strong>Firewall actions</strong>
                <pre>{JSON.stringify(preview.preview?.firewall || {}, null, 2)}</pre>
              </div>
              <div>
                <strong>Rollback outline</strong>
                <pre>{JSON.stringify(preview.preview?.rollback_outline || [], null, 2)}</pre>
              </div>
              <div>
                <strong>Plan expiry</strong> {preview.plan?.expires_at}
              </div>
              {approval && (
                <div>
                  <strong>Approval</strong>
                  <pre>{JSON.stringify({
                    approval_id: approval.approval_id,
                    status: approval.status,
                    expires_at: approval.expires_at
                  }, null, 2)}</pre>
                </div>
              )}
              {brokerResult && (
                <div>
                  <strong>Broker dry-run</strong>
                  <pre>{JSON.stringify(brokerResult, null, 2)}</pre>
                </div>
              )}
              <div className="form-field">
                <label htmlFor="secure-deploy-totp">Step-up TOTP (approve / revoke)</label>
                <input
                  className="form-control"
                  id="secure-deploy-totp"
                  type="password"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                />
              </div>
            </div>
          )}
        </section>
      )}

      <div className="card secure-deploy-actions" role="group" aria-label="Secure deploy preview actions" aria-busy={busy}>
        <button type="button" className="btn form-button" disabled={busy} onClick={saveDraft}>Save draft</button>
        <button type="button" className="btn form-button" disabled={busy} onClick={validate}>Validate</button>
        <button type="button" className="btn btn-primary form-button" disabled={busy} onClick={generatePreview}>Generate preview</button>
        <button type="button" className="btn form-button" disabled={!preview?.plan} onClick={downloadPlan}>Download redacted plan</button>
        <button type="button" className="btn form-button" disabled={busy || !preview?.plan} onClick={approvePlan}>Approve plan</button>
        <button type="button" className="btn form-button" disabled={busy || !approval?.approval_id} onClick={verifyWithBroker}>Verify with broker (dry-run)</button>
      </div>
    </div>
  )
}

export default SecureDeploy
