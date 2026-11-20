import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { CheckCheck, CheckCircle2, Clock, Eye, RotateCcw, Siren, TriangleAlert } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import {
  ConfirmationDialog, Drawer, EmptyState, ErrorState, KV, LoadingSkeleton, MetricCard, PageHeader, RefreshButton,
  Reveal, SearchInput, Segmented, Select,
} from '../components/ds'
import { CauseBox, IncidentCard, IncidentStatus, IncidentTimeline, RecommendationCard } from '../components/domain'
import { useToast } from '../components/Toast'
import { HealthBadge, SeverityBadge, fmtDateTime, fmtTime, fmtVal } from '../components/ui'

const METRIC_META = {
  cpu_pct: ['CPU', '%'], mem_pct: ['Memory', '%'], disk_pct: ['Disk', '%'], latency_ms: ['Latency', 'ms'],
  packet_loss_pct: ['Packet loss', '%'], app_response_ms: ['App response', 'ms'], auth_failures: ['Failed logins', ''],
}

function fmtDuration(s) {
  if (s == null) return '—'
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

export default function Incidents() {
  const [params, setParams] = useSearchParams()
  const selected = params.get('incident')
  const [status, setStatus] = useState('active')
  const [severity, setSeverity] = useState('')
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim())
  const qs = `/aiops/incidents?status=${status}&limit=200${severity ? `&severity=${severity}` : ''}${q ? `&q=${encodeURIComponent(q)}` : ''}`
  const list = usePoll((signal) => api(qs, { signal, expect: 'array' }), 4000, [qs])
  const stats = usePoll((signal) => api('/aiops/incidents/stats', { signal, expect: 'object' }), 5000)
  const s = stats.data

  const select = (id) => setParams(id ? { incident: id } : {})
  const refreshAll = () => { list.refresh(); stats.refresh() }

  return (
    <div>
      <PageHeader title="Incident Management" description="Related anomalies grouped by correlation rules. Acknowledge, resolve and reopen — every action is persisted and audited."
        actions={<RefreshButton onClick={refreshAll} loading={list.loading && !!list.data} />} />
      <Reveal className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Open" value={s?.open} tone={s?.open ? 'warn' : 'ok'} Icon={Siren} onClick={() => setStatus('open')} />
        <MetricCard label="Acknowledged" value={s?.acknowledged} tone="info" Icon={Eye} onClick={() => setStatus('acknowledged')} />
        <MetricCard label="Critical (active)" value={s?.critical_active} tone={s?.critical_active ? 'crit' : 'ok'} Icon={TriangleAlert}
          pulse={s?.critical_active > 0} onClick={() => { setStatus('active'); setSeverity('critical') }} />
        <MetricCard label="Warning (active)" value={s?.warning_active} tone={s?.warning_active ? 'warn' : 'ok'} onClick={() => { setStatus('active'); setSeverity('warning') }} />
        <MetricCard label="Resolved" value={s?.resolved} tone="ok" Icon={CheckCircle2} onClick={() => setStatus('resolved')} />
        <MetricCard label="Mean time to resolve" value={fmtDuration(s?.mean_time_to_resolve_s)} Icon={Clock} hint="resolved incidents" />
      </Reveal>

      <section className="card p-4" aria-label="Incident list">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <Segmented label="Status" value={status} onChange={setStatus}
            options={[{ value: 'active', label: 'Active' }, { value: 'open', label: 'Open' }, { value: 'acknowledged', label: 'Acknowledged' },
              { value: 'resolved', label: 'Resolved' }, { value: 'all', label: 'All' }]} />
          <Select label="Severity" value={severity} onChange={setSeverity}
            options={[{ value: '', label: 'All severities' }, { value: 'critical', label: 'Critical' }, { value: 'warning', label: 'Warning' }, { value: 'info', label: 'Info' }]} />
          <div className="ml-auto w-full sm:w-72"><SearchInput value={search} onChange={setSearch} placeholder="Search id, title, rule, source…" label="Search incidents" /></div>
        </div>
        <ErrorState error={list.error} onRetry={list.refresh} />
        {list.loading && !list.data ? <LoadingSkeleton rows={4} h={64} /> : !list.data?.length ? (
          <EmptyState title="No incidents match" Icon={Siren}>Change the filters, or trigger a scenario in the Local Console.</EmptyState>
        ) : (
          <div className="grid gap-x-3 lg:grid-cols-2 2xl:grid-cols-3">
            <AnimatePresence initial={false}>
              {list.data.map((i) => <IncidentCard key={i.id} incident={i} selected={selected === i.id} onClick={() => select(i.id)} />)}
            </AnimatePresence>
          </div>
        )}
      </section>

      <Drawer open={!!selected} onClose={() => select(null)} width={760} title={selected ? `Incident ${selected}` : ''}
        subtitle="Correlated signals, possible cause, recommendations and timeline">
        {selected && <IncidentDetail key={selected} id={selected} onChanged={refreshAll} />}
      </Drawer>
    </div>
  )
}

function IncidentDetail({ id, onChanged }) {
  const { push } = useToast()
  const d = usePoll((signal) => api(`/aiops/incidents/${id}`, { signal, expect: 'object' }), 4000, [id])
  const [confirm, setConfirm] = useState(null)   // { kind: 'resolve'|'reopen'|'execute'|'revert', rec? }
  const [busy, setBusy] = useState(false)
  const i = d.data

  const affected = useMemo(() => i?.metrics_at_detection || [], [i])

  async function act(kind, rec) {
    setBusy(true)
    const path = rec ? `/aiops/recommendations/${rec.id}/${kind}` : `/aiops/incidents/${id}/${kind}`
    const label = { acknowledge: 'Incident acknowledged', resolve: 'Incident resolved', reopen: 'Incident reopened',
      execute: 'Remediation executed (simulated)', revert: 'Remediation reverted' }[kind]
    try {
      await api(path, { method: 'POST' })
      push({ title: label, message: rec ? `${rec.title} — recorded in the audit log` : `${id} — recorded in the audit log`, tone: 'success' })
      await d.refresh()
      onChanged?.()
    } catch (e) {
      push({ title: 'Action failed', message: e.message, tone: 'error' })
    } finally { setBusy(false); setConfirm(null) }
  }

  if (d.error && !i) return <ErrorState error={d.error} onRetry={d.refresh} />
  if (!i) return <LoadingSkeleton rows={6} h={40} />

  return (
    <div className="space-y-5">
      <section>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-base font-semibold">{i.title}</h3>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted">
              <AnimatePresence mode="wait"><motion.span key={i.status} initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}><IncidentStatus status={i.status} /></motion.span></AnimatePresence>
              <SeverityBadge severity={i.severity} pulse={i.status !== 'resolved'} />
              <span className="mono">{i.rule_id || 'no rule'}</span><span>{i.kind === 'collector' ? 'collector incident' : 'anomaly correlation'}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy || i.status !== 'open'} onClick={() => act('acknowledge')}><Eye size={14} />Acknowledge</button>
            {i.status !== 'resolved'
              ? <button className="btn btn-primary" disabled={busy} onClick={() => setConfirm({ kind: 'resolve' })}><CheckCheck size={14} />Resolve</button>
              : <button className="btn" disabled={busy} onClick={() => setConfirm({ kind: 'reopen' })}><RotateCcw size={14} />Reopen</button>}
          </div>
        </div>
        <div className="mt-4">
          <KV cols={3} items={[
            ['Opened (detection)', fmtDateTime(i.opened_at)],
            ['Last updated', fmtDateTime(i.updated_at)],
            ['Acknowledged', i.acknowledged_at ? fmtDateTime(i.acknowledged_at) : '—'],
            ['Resolved', i.resolved_at ? fmtDateTime(i.resolved_at) : '—'],
            ['Affected sources', i.source_ids.length],
            ['Contributing signals', i.anomalies.length || i.signals.length],
          ]} />
        </div>
        {i.resolution_note && <div className="mt-3 rounded-lg border border-good/40 bg-good/10 p-3 text-xs"><b className="text-good">Resolution:</b> {i.resolution_note}</div>}
      </section>

      <CauseBox incident={i} />

      <section>
        <h4 className="label mb-2">Contributing signals (detection reason)</h4>
        {!i.anomalies.length ? (
          <p className="text-xs text-muted">{i.kind === 'collector' ? `Heartbeat silence${i.signals?.[0]?.observed != null ? ` of ${Math.round(i.signals[0].observed)}s` : ''} exceeded the offline timeout.` : 'No anomalies linked.'}</p>
        ) : (
          <div className="overflow-x-auto"><table className="tbl text-xs">
            <thead><tr><th>Signal</th><th>Source</th><th>Observed / peak</th><th>Expected (baseline)</th><th>z</th><th>Severity</th><th>Detected</th></tr></thead>
            <tbody>{i.anomalies.map((a) => (
              <tr key={a.id} title={a.reason}><td>{a.label}</td><td className="mono"><Link className="hover:underline" to={`/infrastructure/${a.source_id}`}>{a.source_id}</Link></td>
                <td>{fmtVal(a.observed, a.unit)} / <b>{fmtVal(a.peak, a.unit)}</b></td>
                <td>{fmtVal(a.expected_low, a.unit)}–{fmtVal(a.expected_high, a.unit)} <span className="text-muted">(median {fmtVal(a.baseline_median, a.unit)})</span></td>
                <td>{a.robust_z.toFixed(1)}</td><td><SeverityBadge severity={a.severity} /></td><td className="mono">{fmtTime(a.detected_at)}</td></tr>))}</tbody>
          </table></div>
        )}
      </section>

      {affected.length > 0 && (
        <section>
          <h4 className="label mb-2">Affected infrastructure · metrics at detection time</h4>
          <div className="space-y-3">
            {affected.map((src) => (
              <div key={src.source_id} className="rounded-lg border border-line p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <Link to={`/infrastructure/${src.source_id}`} className="mono font-medium hover:underline">{src.source_id}{src.role ? ` · ${src.role}` : ''}</Link>
                  {src.current_health && <span className="flex items-center gap-1 text-muted">now <HealthBadge state={src.current_health} /></span>}
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {Object.entries(src.metrics).map(([m, v]) => {
                    const [label, unit] = METRIC_META[m] || [m, '']
                    return (
                      <div key={m} className={`rounded-md p-2 text-xs ${v.anomalous ? 'bg-crit/10' : 'bg-panel-2'}`}>
                        <div className="label">{label}</div>
                        <div className={`font-semibold tabular-nums ${v.anomalous ? 'text-crit' : ''}`}>{unit ? fmtVal(v.value, unit) : Math.round(v.value)}</div>
                        <div className="text-[10px] text-muted">{fmtTime(v.event_time)}{v.z != null ? ` · z ${v.z.toFixed(1)}` : ''}{v.anomalous ? ' · anomalous' : ''}</div>
                      </div>
                    )
                  })}
                </div>
                {!Object.keys(src.metrics).length && <p className="text-xs text-muted">No metric points recorded before detection.</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h4 className="label mb-2">Recommended actions</h4>
        {!i.recommendations.length ? <p className="text-xs text-muted">No recommendations.</p> : i.recommendations.map((r) => (
          <RecommendationCard key={r.id} rec={r} busy={busy} disabled={i.status === 'resolved'}
            onExecute={(rec) => setConfirm({ kind: 'execute', rec })} onRevert={(rec) => setConfirm({ kind: 'revert', rec })} />))}
        {i.remediations?.length > 0 && (
          <div className="mt-2 text-xs text-muted">Remediation history: {i.remediations.map((r) => `${r.action_code} ${r.status} by ${r.approved_by} (${fmtTime(r.created_at)})`).join(' · ')}</div>)}
      </section>

      <section>
        <h4 className="label mb-2">Timeline (UTC)</h4>
        <IncidentTimeline items={i.timeline} />
      </section>

      <ConfirmationDialog open={!!confirm} busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => act(confirm.kind, confirm.rec)}
        tone={confirm?.kind === 'revert' ? 'danger' : 'primary'}
        title={{ resolve: 'Resolve incident', reopen: 'Reopen incident', execute: 'Approve remediation', revert: 'Revert remediation' }[confirm?.kind] || ''}
        confirmLabel={{ resolve: 'Resolve', reopen: 'Reopen', execute: 'Approve & run (simulated)', revert: 'Revert' }[confirm?.kind] || 'Confirm'}
        message={{
          resolve: `Mark ${i.id} as resolved? Its active anomalies will be cleared. The action is audited and can be undone with Reopen.`,
          reopen: `Reopen ${i.id}? A reopened incident is not auto-resolved — an operator must resolve it.`,
          execute: confirm?.rec && `${confirm.rec.title}: execution is SIMULATED in this MVP — nothing changes in customer infrastructure. Approval is recorded in the audit log and can be reverted.`,
          revert: confirm?.rec && `Revert the simulated "${confirm.rec.title}" action? This is audited.`,
        }[confirm?.kind]} />
    </div>
  )
}
