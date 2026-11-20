import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { RotateCcw, Save } from 'lucide-react'
import { api, session } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import { ConfirmationDialog, ErrorState, LoadingSkeleton, PageHeader, SearchInput, Select, Tabs } from '../components/ds'
import DataTable from '../components/DataTable'
import { AuditTable } from '../components/domain'
import { Card, HealthBadge, fmtDateTime } from '../components/ui'
import { useToast } from '../components/Toast'

const BANDS = [
  { key: 'excellent', label: 'Excellent', color: 'var(--color-good)' },
  { key: 'good', label: 'Good', color: 'var(--color-accent)' },
  { key: 'fair', label: 'Fair', color: 'var(--color-warn)' },
  { key: 'poor', label: 'Poor', color: 'var(--color-crit)' },
]
const AUDIT_FILTERS = [
  { value: '', label: 'All actions' },
  { value: 'login', label: 'User sign-in' }, { value: 'login_failed', label: 'Failed sign-in' },
  { value: 'collector_registered', label: 'Collector registered' },
  { value: 'incident_opened', label: 'Incident opened' }, { value: 'incident_acknowledged', label: 'Incident acknowledged' },
  { value: 'incident_resolved', label: 'Incident resolved' }, { value: 'incident_reopened', label: 'Incident reopened' },
  { value: 'threshold_updated', label: 'Threshold changed' },
  { value: 'remediation_executed', label: 'Remediation executed' }, { value: 'remediation_reverted', label: 'Remediation reverted' },
  { value: 'collector_offline', label: 'Collector offline' }, { value: 'collector_online', label: 'Collector online' },
  { value: 'cross_tenant_denied', label: 'Cross-tenant denied' },
]

export default function SettingsPage() {
  const [tab, setTab] = useState('thresholds')
  const user = session.user
  return (
    <div>
      <PageHeader title="Settings & Audit"
        description={`Per-tenant configuration and the audit trail. Changes are persisted by the backend and audited${user?.tenant_id ? ` for ${user.tenant_id}` : ''}.`} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'thresholds', label: 'Health thresholds' }, { value: 'transitions', label: 'Health transitions' }, { value: 'audit', label: 'Audit log' }]} />
      <div className="mt-4">
        {tab === 'thresholds' && <Thresholds />}
        {tab === 'transitions' && <Transitions />}
        {tab === 'audit' && <Audit />}
      </div>
    </div>
  )
}

function Thresholds() {
  const th = usePoll((signal) => api('/settings/thresholds', { signal, expect: 'array' }), 0)
  if (th.error && !th.data) return <ErrorState error={th.error} onRetry={th.refresh} />
  if (!th.data) return <LoadingSkeleton rows={6} h={60} />
  return (
    <Card title="Four-state health thresholds" subtitle="value < Excellent limit → Excellent · < Good limit → Good · < Fair limit → Fair · otherwise Poor. The worst metric decides a source's health.">
      <div className="space-y-3">{th.data.map((t) => <ThresholdRow key={t.metric} t={t} onSaved={th.refresh} />)}</div>
    </Card>
  )
}

function ThresholdRow({ t, onSaved }) {
  const { push } = useToast()
  const init = { excellent_below: t.excellent_below, good_below: t.good_below, fair_below: t.fair_below }
  const [v, setV] = useState(init)
  const [saving, setSaving] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const dirty = v.excellent_below !== t.excellent_below || v.good_below !== t.good_below || v.fair_below !== t.fair_below
  const invalid = !(v.excellent_below >= 0 && v.excellent_below < v.good_below && v.good_below < v.fair_below)
  const scaleMax = useMemo(() => {
    const m = Math.max(v.fair_below * 1.25, 1)
    return t.unit === '%' && t.metric !== 'packet_loss_pct' ? Math.min(100, Math.max(m, v.fair_below + 5)) : m
  }, [v.fair_below, t.unit, t.metric])
  const bounds = [0, v.excellent_below, v.good_below, v.fair_below, scaleMax]

  async function save(values = v) {
    setSaving(true)
    try {
      await api('/settings/thresholds', { method: 'PUT', body: { metric: t.metric, ...values } })
      push({ title: `${t.label} thresholds saved`, message: `Excellent < ${values.excellent_below} · Good < ${values.good_below} · Fair < ${values.fair_below} ${t.unit}`, tone: 'success' })
      onSaved()
    } catch (e) {
      push({ title: 'Could not save thresholds', message: e.message, tone: 'error' })
    } finally { setSaving(false) }
  }
  const input = (k, label) => (
    <label className="flex flex-col gap-1 text-xs text-muted">{label}
      <input type="number" step="any" min="0" value={v[k]} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })}
        className="input w-24" aria-invalid={invalid} />
    </label>
  )
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-[160px]"><div className="text-sm font-semibold">{t.label}</div><div className="text-xs text-muted">{t.metric} · {t.unit} · default {t.default?.join(' / ')}</div></div>
        <div className="flex flex-wrap items-end gap-2">
          {input('excellent_below', 'Excellent below')}{input('good_below', 'Good below')}{input('fair_below', 'Fair below')}
          <button className="btn btn-primary" disabled={!dirty || invalid || saving} onClick={() => save()}><Save size={13} />Save</button>
          <button className="btn" disabled={saving || !t.default} onClick={() => setConfirmReset(true)} title="Reset to default"><RotateCcw size={13} /><span className="sr-only">Reset to default</span></button>
        </div>
      </div>
      <div className="mt-3 flex h-3 w-full gap-[2px] overflow-hidden rounded-full" role="img"
        aria-label={`${t.label}: Excellent below ${v.excellent_below}, Good below ${v.good_below}, Fair below ${v.fair_below}, Poor above`}>
        {BANDS.map((b, i) => (
          <motion.div key={b.key} title={`${b.label}: ${bounds[i]}–${i === 3 ? '∞' : bounds[i + 1]} ${t.unit}`} style={{ background: b.color }}
            animate={{ width: `${Math.max(0, ((Math.min(bounds[i + 1], scaleMax) - bounds[i]) / scaleMax) * 100)}%` }} transition={{ duration: 0.3 }} />
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted">
        {BANDS.map((b, i) => <span key={b.key} className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: b.color }} />{b.label} {i === 3 ? `≥ ${bounds[3]}` : `${bounds[i]}–${bounds[i + 1]}`}</span>)}
      </div>
      {invalid && <p role="alert" className="mt-2 text-xs text-crit">Limits must be increasing: 0 ≤ Excellent &lt; Good &lt; Fair.</p>}
      <ConfirmationDialog open={confirmReset} title={`Reset ${t.label} thresholds?`}
        message={`Restore the defaults (${t.default?.join(' / ')} ${t.unit}). Health classification changes immediately for this tenant and the change is audited.`}
        confirmLabel="Reset to default" busy={saving} onCancel={() => setConfirmReset(false)}
        onConfirm={async () => {
          const [a, b, c] = t.default
          const values = { excellent_below: a, good_below: b, fair_below: c }
          setV(values); await save(values); setConfirmReset(false)
        }} />
    </div>
  )
}

function Transitions() {
  const [entity, setEntity] = useState('')
  const tr = usePoll((signal) => api('/health/transitions?limit=200', { signal, expect: 'array' }), 5000)
  const rows = (tr.data || []).filter((r) => !entity || r.entity_type === entity)
  return (
    <Card title="Health-state transitions (UTC)" subtitle="Previous → new state, triggering metric and reason for every change"
      actions={<Select value={entity} onChange={setEntity} label="Entity" options={[{ value: '', label: 'All' }, { value: 'source', label: 'Sources' }, { value: 'collector', label: 'Collectors' }]} />}>
      <DataTable rows={tr.data ? rows : null} loading={tr.loading} error={tr.error} onRetry={tr.refresh} rowKey={(r) => r.id} dense maxHeight={560}
        empty="No transitions recorded yet"
        columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
          { key: 'entity_id', header: 'Entity', render: (r) => <span className="mono">{r.entity_type}: {r.entity_id}</span> },
          { key: 'change', header: 'Change', sortable: false, render: (r) => <span className="whitespace-nowrap">{r.prev_state ? <HealthBadge state={r.prev_state} /> : '—'} → <HealthBadge state={r.new_state} /></span> },
          { key: 'trigger_metric', header: 'Trigger' },
          { key: 'reason', header: 'Reason', render: (r) => <span className="text-muted">{r.reason}</span> }]} />
    </Card>
  )
}

function Audit() {
  const [action, setAction] = useState('')
  const [q, setQ] = useState('')
  const path = `/audit?limit=200${action ? `&action=${action}` : ''}`
  const au = usePoll((signal) => api(path, { signal, expect: 'array' }), 5000, [path])
  const rows = au.data ? au.data.filter((r) => !q || `${r.actor} ${r.action} ${r.target} ${JSON.stringify(r.detail)}`.toLowerCase().includes(q.toLowerCase())) : null
  return (
    <Card title="Audit log" subtitle="User actions, collector registrations, incident events, configuration changes and denied access"
      actions={<><SearchInput value={q} onChange={setQ} placeholder="Actor, target, detail…" label="Search audit log" /><Select value={action} onChange={setAction} label="Action" options={AUDIT_FILTERS} /></>}>
      <AuditTable rows={rows} loading={au.loading} error={au.error} maxHeight={560} />
    </Card>
  )
}
