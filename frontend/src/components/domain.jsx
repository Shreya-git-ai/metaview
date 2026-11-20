// Domain components shared across pages. All render backend data passed in as props.
import { motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { Activity, FileCheck2, FlaskConical, Lightbulb, Server, ShieldCheck, Sparkles } from 'lucide-react'
import DataTable from './DataTable'
import { StatusIndicator } from './ds'
import { HealthBadge, SeverityBadge, fmtDateTime, fmtTime, fmtVal, ago } from './ui'

export const STATUS_LABEL = { open: 'Open', acknowledged: 'Acknowledged', resolved: 'Resolved' }

export function IncidentStatus({ status }) {
  const tone = status === 'resolved' ? 'ok' : status === 'acknowledged' ? 'info' : 'warn'
  return <StatusIndicator state={tone} label={STATUS_LABEL[status] || status} pulse={status === 'open'} />
}

export function IncidentCard({ incident: i, to, onClick, selected }) {
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="mono text-xs text-muted">{i.id}{i.rule_id ? ` · ${i.rule_id}` : ''}</span>
        <SeverityBadge severity={i.severity} pulse={i.status !== 'resolved'} />
      </div>
      <div className="mt-1 text-sm font-medium">{i.title}</div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <IncidentStatus status={i.status} />
        <span>{i.signals?.length || 0} signal(s)</span>
        <span>{i.source_ids?.length || 0} source(s)</span>
        <span>updated {fmtTime(i.updated_at)}</span>
      </div>
    </>
  )
  const cls = `card-hover block w-full rounded-lg border p-3 text-left ${selected ? 'border-ai bg-ai/10' : 'border-line'}`
  return (
    <motion.div layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mb-2">
      {to ? <Link to={to} className={cls}>{body}</Link> : <button onClick={onClick} className={cls}>{body}</button>}
    </motion.div>
  )
}

const TL_COLOR = { anomaly: 'var(--color-ai)', incident_opened: 'var(--color-warn)', incident_resolved: 'var(--color-good)',
  incident_acknowledged: 'var(--color-info)', incident_reopened: 'var(--color-warn)', remediation: 'var(--color-accent)' }
export function IncidentTimeline({ items }) {
  if (!items?.length) return <p className="text-sm text-muted">No timeline entries.</p>
  return (
    <ol className="relative ml-2 border-l border-line">
      {items.map((t, idx) => (
        <motion.li key={idx} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(idx * 0.03, 0.4) }}
          className="relative mb-3 pl-4 text-xs">
          <span className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-panel" style={{ background: TL_COLOR[t.type] || 'var(--color-muted)' }} />
          <div className="flex flex-wrap items-center gap-2"><span className="mono text-muted">{fmtTime(t.at)}</span><span className="chip uppercase">{t.type.replace(/_/g, ' ')}</span></div>
          <div className="mt-0.5">{t.text}</div>
        </motion.li>
      ))}
    </ol>
  )
}

export function AnomalyCard({ anomaly: a }) {
  return (
    <motion.div layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mb-2 rounded-lg border border-line p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-text">{a.label} · <Link className="mono hover:underline" to={`/infrastructure/${a.source_id}`}>{a.source_id}</Link></span>
        <SeverityBadge severity={a.severity} />
      </div>
      <div className="mt-1 grid grid-cols-3 gap-2">
        <div><div className="label">Expected</div><div>{fmtVal(a.expected_low, a.unit)}–{fmtVal(a.expected_high, a.unit)}</div></div>
        <div><div className="label">Observed / peak</div><div className="font-semibold text-crit">{fmtVal(a.observed, a.unit)} / {fmtVal(a.peak, a.unit)}</div></div>
        <div><div className="label">Robust z</div><div>{a.robust_z?.toFixed(1)}</div></div>
      </div>
      <div className="mt-1 text-muted">{a.reason}</div>
      <div className="mt-1 text-muted">{fmtDateTime(a.detected_at)} · {a.status}{a.incident_id && <> · <Link className="mono hover:underline" to={`/incidents?incident=${a.incident_id}`}>{a.incident_id}</Link></>}</div>
    </motion.div>
  )
}

export function RecommendationCard({ rec: r, onExecute, onRevert, busy, disabled }) {
  const remediate = r.kind === 'remediate'
  return (
    <div className={`mb-2 rounded-lg border p-3 ${remediate ? 'border-warn/40' : 'border-ai/30 bg-ai/5'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
            {remediate ? <FlaskConical size={14} className="text-warn" aria-hidden /> : <Sparkles size={14} className="text-ai" aria-hidden />}{r.title}
            <span className="chip uppercase">{r.kind}</span>
            {r.status !== 'proposed' && <span className="chip uppercase text-accent">{r.status}</span>}
          </div>
          <div className="mt-1 text-xs text-muted">{r.rationale}</div>
          {r.incident_id && <div className="mt-1 text-[11px] text-muted">Incident <Link className="mono hover:underline" to={`/incidents?incident=${r.incident_id}`}>{r.incident_id}</Link></div>}
        </div>
        {remediate && onExecute && (r.status === 'executed'
          ? <button className="btn shrink-0" disabled={busy} onClick={() => onRevert(r)}>Revert</button>
          : <button className="btn shrink-0" disabled={busy || disabled} onClick={() => onExecute(r)}><ShieldCheck size={14} />Approve &amp; run (simulated)</button>)}
      </div>
    </div>
  )
}

export function CauseBox({ incident: i }) {
  if (!i.probable_cause) return null
  return (
    <div className="rounded-lg border border-ai/40 bg-ai/10 p-3">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ai"><Lightbulb size={14} aria-hidden />Possible cause — hypothesis, not confirmed · rule {i.rule_id}</div>
      <div className="mt-1 text-sm">{i.probable_cause}</div>
      {i.explanation && <div className="mt-2 text-xs text-muted">{i.explanation}</div>}
    </div>
  )
}

export function CollectorStatusCard({ c, onClick }) {
  const state = { online: 'ok', degraded: 'warn', offline: 'crit', registered: 'idle' }[c.fleet_status] || 'idle'
  return (
    <button onClick={onClick} className="card card-hover w-full p-4 text-left">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold"><Server size={15} aria-hidden /><span className="mono">{c.id}</span></span>
        <StatusIndicator state={state} label={c.fleet_status} pulse={c.fleet_status === 'online' || c.fleet_status === 'offline'} />
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
        <div><div className="label">Heartbeat</div><div className={c.heartbeat_age_s > 15 ? 'text-warn' : ''}>{ago(c.heartbeat_age_s)}</div></div>
        <div><div className="label">Ingest</div><div>{c.ingestion?.events_per_min ?? '—'}/min</div></div>
        <div><div className="label">Region</div><div>{c.region}</div></div>
      </div>
      <div className="mt-2 text-[11px] text-muted">{c.health_reasons?.join(' · ')}</div>
    </button>
  )
}

export function EgressRecordCard({ e, onClick, selected }) {
  const p = e.payload?.processing
  return (
    <button onClick={onClick} className={`card-hover mb-2 block w-full rounded-lg border p-3 text-left text-xs ${selected ? 'border-accent bg-accent/5' : 'border-line'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="mono">{e.event_id.slice(0, 8)}… · {e.category}</span>
        <span className="flex items-center gap-1 text-good"><FileCheck2 size={12} aria-hidden />received</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-muted">
        <span>{e.collector_id}</span><span>{fmtTime(e.received_at)}</span><span>{e.payload_bytes} B</span>
        {p && <span className={p.secrets_redacted ? 'text-local' : ''}>{p.secrets_redacted} secret(s) removed locally</span>}
      </div>
    </button>
  )
}

export function AuditTable({ rows, loading, error, maxHeight = 420 }) {
  return (
    <DataTable rows={rows} loading={loading} error={error} rowKey={(r) => r.id} maxHeight={maxHeight} dense
      empty="No audit records"
      columns={[
        { key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
        { key: 'actor', header: 'Actor' },
        { key: 'action', header: 'Action', render: (r) => <span className="chip">{r.action}</span> },
        { key: 'target', header: 'Target', render: (r) => <span className="mono">{r.target || '—'}</span> },
        { key: 'detail', header: 'Detail', sortable: false, render: (r) => <span className="text-muted">{Object.keys(r.detail || {}).length ? JSON.stringify(r.detail).slice(0, 90) : ''}</span> },
      ]} />
  )
}

export function SourceHealthRow({ s }) {
  return (
    <div className="flex items-center justify-between border-b border-line/60 py-1.5 text-sm last:border-0">
      <Link to={`/infrastructure/${s.source_id}`} className="mono text-xs hover:underline">{s.source_id} · {s.role}</Link>
      <HealthBadge state={s.health_state} stale={s.stale} />
    </div>
  )
}

export const icons = { Activity }
