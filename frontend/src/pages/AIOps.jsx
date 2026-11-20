import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Brain, FlaskConical, GitMerge, Info, Layers, Lightbulb, ListChecks, Sparkles } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import {
  ConfirmationDialog, EmptyState, ErrorState, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, Segmented,
  Select, Tabs,
} from '../components/ds'
import DataTable from '../components/DataTable'
import { AnomalyCard, CauseBox, IncidentStatus, RecommendationCard } from '../components/domain'
import { useToast } from '../components/Toast'
import { SeverityBadge, fmtDateTime, fmtTime, fmtVal } from '../components/ui'

const CATEGORIES = [
  { value: 'all', label: 'All', metrics: null },
  { value: 'cpu', label: 'CPU', metrics: ['cpu_pct'] },
  { value: 'memory', label: 'Memory', metrics: ['mem_pct'] },
  { value: 'network', label: 'Network', metrics: ['latency_ms', 'packet_loss_pct'] },
  { value: 'application', label: 'Application', metrics: ['app_response_ms'] },
  { value: 'security', label: 'Security', metrics: ['auth_failures'] },
  { value: 'disk', label: 'Disk', metrics: ['disk_pct'] },
]
const SEV_RANK = { critical: 0, warning: 1, info: 2 }

/** 'matched N/M rule signals' — the only strength indicator we show (no ML confidence exists). */
export function ruleMatch(incident, rules) {
  const rule = rules?.find((r) => r.id === incident?.rule_id)
  if (!rule || !rule.requires?.length) return null
  const labels = new Set((incident.signals || []).map((s) => s.label))
  const matched = rule.requires.filter((l) => labels.has(l)).length
  return { matched, total: rule.requires.length, rule }
}

function MatchChip({ incident, rules }) {
  const m = ruleMatch(incident, rules)
  if (!m) return null
  return <span className="chip" title={`Rule ${m.rule.id} requires: ${m.rule.requires.join(' + ')}`}>matched {m.matched}/{m.total} rule signals</span>
}

function MethodBanner({ method }) {
  return (
    <div className="mb-4 flex items-start gap-3 rounded-lg border border-ai/30 bg-ai/5 p-3 text-xs" role="note">
      <Info size={15} className="mt-0.5 shrink-0 text-ai" aria-hidden />
      <div>
        <span className="font-semibold text-ai">How this works: </span>
        explainable statistics only — {method?.detector || 'rolling robust z-score (median/MAD)'} plus ordered rule-based correlation.
        No machine-learning model, no LLM and no confidence score are used. Causes are <b>hypotheses</b>; the only strength
        indicator shown is how many of a rule's required signals were observed.
      </div>
    </div>
  )
}

export default function AIOps() {
  const [tab, setTab] = useState('anomalies')
  const method = usePoll((signal) => api('/aiops/method', { signal, expect: 'object' }), 0)
  const anomalies = usePoll((signal) => api('/aiops/anomalies?limit=200', { signal, expect: 'array' }), 4000)
  const incidents = usePoll((signal) => api('/aiops/incidents?status=all&limit=200', { signal, expect: 'array' }), 4000)
  const recs = usePoll((signal) => api('/aiops/recommendations', { signal, expect: 'array' }), 5000)
  const rules = method.data?.correlation_rules

  const activeAnoms = (anomalies.data || []).filter((a) => a.status === 'active').length
  const openIncs = (incidents.data || []).filter((i) => i.status !== 'resolved')
  const proposed = (recs.data || []).filter((r) => r.status === 'proposed' && openIncs.some((i) => i.id === r.incident_id)).length

  const refreshAll = () => { anomalies.refresh(); incidents.refresh(); recs.refresh() }
  const tabs = [
    { value: 'anomalies', label: 'Anomaly Detection', count: activeAnoms },
    { value: 'correlation', label: 'Event Correlation', count: openIncs.filter((i) => i.kind === 'anomaly').length },
    { value: 'rootcause', label: 'Root Cause Assistance' },
    { value: 'recommendations', label: 'Recommendations', count: proposed },
    { value: 'baselines', label: 'Baselines' },
    { value: 'method', label: 'Method' },
  ]

  return (
    <div>
      <PageHeader title="AIOps Intelligence" description="Anomalies against learned baselines, correlated into incidents with explainable possible causes and recommended actions."
        badge={<span className="chip text-ai"><Brain size={11} aria-hidden />explainable</span>}
        actions={<RefreshButton onClick={refreshAll} loading={anomalies.loading && !!anomalies.data} />} />
      <MethodBanner method={method.data} />
      <Reveal className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard label="Active anomalies" value={activeAnoms} tone={activeAnoms ? 'ai' : 'ok'} Icon={Brain} hint={`${anomalies.data?.length ?? 0} detected in history`} />
        <MetricCard label="Correlated incidents" value={openIncs.filter((i) => i.kind === 'anomaly').length} tone="warn" Icon={GitMerge} hint="open or acknowledged" />
        <MetricCard label="Critical incidents" value={openIncs.filter((i) => i.severity === 'critical').length} tone={openIncs.some((i) => i.severity === 'critical') ? 'crit' : 'ok'}
          Icon={Layers} pulse={openIncs.some((i) => i.severity === 'critical')} />
        <MetricCard label="Open recommendations" value={proposed} tone="ai" Icon={Sparkles} hint="for active incidents" />
      </Reveal>
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      <div className="mt-4">
        <AnimatePresence mode="wait">
          <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}>
            {tab === 'anomalies' && <AnomalyTab q={anomalies} />}
            {tab === 'correlation' && <CorrelationTab q={incidents} rules={rules} />}
            {tab === 'rootcause' && <RootCauseTab incidents={incidents} rules={rules} />}
            {tab === 'recommendations' && <RecommendationsTab recs={recs} incidents={incidents} />}
            {tab === 'baselines' && <BaselinesTab />}
            {tab === 'method' && <MethodTab q={method} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Anomaly detection
function AnomalyTab({ q }) {
  const [cat, setCat] = useState('all')
  const [state, setState] = useState('active')
  const metrics = CATEGORIES.find((c) => c.value === cat)?.metrics
  const list = (q.data || []).filter((a) => (!metrics || metrics.includes(a.metric)) && (state === 'all' || a.status === state))
  const counts = useMemo(() => {
    const out = {}
    for (const c of CATEGORIES) out[c.value] = (q.data || []).filter((a) => a.status === 'active' && (!c.metrics || c.metrics.includes(a.metric))).length
    return out
  }, [q.data])
  return (
    <section aria-label="Anomaly detection">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Segmented label="Anomaly category" value={cat} onChange={setCat}
          options={CATEGORIES.map((c) => ({ value: c.value, label: counts[c.value] ? `${c.label} · ${counts[c.value]}` : c.label }))} />
        <Segmented label="Anomaly state" value={state} onChange={setState}
          options={[{ value: 'active', label: 'Active' }, { value: 'cleared', label: 'Cleared' }, { value: 'all', label: 'History' }]} />
      </div>
      <ErrorState error={q.error} onRetry={q.refresh} />
      {q.loading && !q.data ? <LoadingSkeleton rows={4} h={70} /> : !list.length ? (
        <EmptyState title={state === 'active' ? 'No active anomalies' : 'No anomalies in this view'} Icon={Brain}>
          Baselines need ~60 s (20 points) per source before detection starts. Trigger a scenario in the Local Console to see detections.
        </EmptyState>
      ) : (
        <div className="grid gap-x-3 md:grid-cols-2 2xl:grid-cols-3">
          <AnimatePresence initial={false}>
            {list.slice(0, 60).map((a) => <AnomalyCard key={a.id} anomaly={a} />)}
          </AnimatePresence>
        </div>
      )}
      {list.length > 60 && <p className="mt-2 text-xs text-muted">Showing 60 of {list.length}.</p>}
    </section>
  )
}

// ---------------------------------------------------------------- Event correlation
function CorrelationTab({ q, rules }) {
  const [scope, setScope] = useState('active')
  const list = (q.data || []).filter((i) => i.kind === 'anomaly' && (scope === 'all' || i.status !== 'resolved'))
  if (q.error && !q.data) return <ErrorState error={q.error} onRetry={q.refresh} />
  if (q.loading && !q.data) return <LoadingSkeleton rows={3} h={120} />
  return (
    <section aria-label="Event correlation">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted">Anomalies on the same pseudonymous source — or network signals on the same collector — within 120 s are grouped into one incident.</p>
        <Segmented label="Correlation scope" value={scope} onChange={setScope} options={[{ value: 'active', label: 'Active' }, { value: 'all', label: 'All' }]} />
      </div>
      {!list.length ? <EmptyState title="No correlated incidents" Icon={GitMerge}>Related anomalies will be grouped here as they are detected.</EmptyState> : (
        <div className="space-y-3">
          <AnimatePresence initial={false}>
            {list.map((i) => {
              const times = (i.signals || []).map((s) => s.detected_at).filter(Boolean).sort()
              const first = times[0], last = times[times.length - 1]
              const spanS = first && last ? Math.round((new Date(last) - new Date(first)) / 1000) : null
              return (
                <motion.article key={i.id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="card p-4">
                  <header className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={`/incidents?incident=${i.id}`} className="mono text-xs text-muted hover:underline">{i.id}</Link>
                      <h3 className="text-sm font-semibold">{i.title}</h3>
                      <MatchChip incident={i} rules={rules} />
                    </div>
                    <div className="flex items-center gap-3"><IncidentStatus status={i.status} /><SeverityBadge severity={i.severity} pulse={i.status !== 'resolved'} /></div>
                  </header>
                  <div className="mt-3 grid gap-3 text-xs sm:grid-cols-3">
                    <div><div className="label">Common affected source{i.source_ids.length > 1 ? 's' : ''}</div>
                      <div className="mt-0.5 flex flex-wrap gap-1">{i.source_ids.map((s) => <Link key={s} to={`/infrastructure/${s}`} className="chip mono hover:text-text">{s}</Link>)}</div></div>
                    <div><div className="label">Time-window relationship</div>
                      <div className="mt-0.5">{first ? `${fmtTime(first)} → ${fmtTime(last)}` : '—'}{spanS != null && <span className="text-muted"> · {spanS}s apart (window 120 s)</span>}</div></div>
                    <div><div className="label">Correlation rule</div><div className="mt-0.5 mono">{i.rule_id || 'none matched'}</div></div>
                  </div>
                  <div className="mt-3 overflow-x-auto">
                    <table className="tbl text-xs">
                      <thead><tr><th>Signal</th><th>Source</th><th>Detected</th><th>Peak</th><th>Expected</th><th>z</th><th>Severity</th></tr></thead>
                      <tbody>{(i.signals || []).map((s) => (
                        <tr key={`${s.metric}-${s.source_id}`}><td>{s.label}</td><td className="mono">{s.source_id}</td><td className="mono">{fmtTime(s.detected_at)}</td>
                          <td className="font-semibold">{s.peak != null ? fmtVal(s.peak, unitOf(s.metric)) : fmtVal(s.observed, 's')}</td>
                          <td>{s.expected_low != null ? `${fmtVal(s.expected_low, unitOf(s.metric))}–${fmtVal(s.expected_high, unitOf(s.metric))}` : '—'}</td>
                          <td>{s.robust_z != null ? s.robust_z.toFixed(1) : '—'}</td><td><SeverityBadge severity={s.severity} /></td></tr>))}</tbody>
                    </table>
                  </div>
                  {i.explanation && <p className="mt-3 text-xs text-muted"><span className="font-semibold text-text">Why grouped: </span>{i.explanation}</p>}
                </motion.article>
              )
            })}
          </AnimatePresence>
        </div>
      )}
    </section>
  )
}

function unitOf(metric) {
  return { cpu_pct: '%', mem_pct: '%', disk_pct: '%', packet_loss_pct: '%', latency_ms: 'ms', app_response_ms: 'ms' }[metric]
}

// ---------------------------------------------------------------- Root cause assistance
function RootCauseTab({ incidents, rules }) {
  const recs = usePoll((signal) => api('/aiops/recommendations', { signal, expect: 'array' }), 6000)
  const ranked = useMemo(() => (incidents.data || []).filter((i) => i.probable_cause).sort((a, b) =>
    (a.status === 'resolved') - (b.status === 'resolved') || (SEV_RANK[a.severity] ?? 3) - (SEV_RANK[b.severity] ?? 3) ||
    (b.updated_at || '').localeCompare(a.updated_at || '')).slice(0, 20), [incidents.data])
  if (incidents.error && !incidents.data) return <ErrorState error={incidents.error} onRetry={incidents.refresh} />
  if (incidents.loading && !incidents.data) return <LoadingSkeleton rows={3} h={140} />
  if (!ranked.length) return <EmptyState title="No possible causes yet" Icon={Lightbulb}>Possible causes appear once anomalies are correlated into an incident.</EmptyState>
  return (
    <section aria-label="Root cause assistance" className="space-y-3">
      <p className="text-xs text-muted">Ranked: active incidents first, then by severity. Every cause below is a <b>possible cause (hypothesis)</b> inferred from correlated signals — NEXORA never marks a cause as confirmed; confirmation is an operator decision.</p>
      {ranked.map((i, idx) => {
        const steps = (recs.data || []).filter((r) => r.incident_id === i.id && r.kind === 'investigate')
        return (
          <motion.article key={i.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(idx * 0.04, 0.3) }} className="card p-4">
            <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-ai/15 text-xs font-bold text-ai" aria-label={`Rank ${idx + 1}`}>{idx + 1}</span>
                <Link to={`/incidents?incident=${i.id}`} className="mono text-xs text-muted hover:underline">{i.id}</Link>
                <h3 className="text-sm font-semibold">{i.title}</h3>
                <MatchChip incident={i} rules={rules} />
              </div>
              <div className="flex items-center gap-3"><IncidentStatus status={i.status} /><SeverityBadge severity={i.severity} /></div>
            </header>
            <CauseBox incident={i} />
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <div>
                <div className="label mb-1">Supporting evidence (related telemetry)</div>
                <ul className="space-y-1 text-xs">
                  {(i.signals || []).map((s) => (
                    <li key={`${s.metric}-${s.source_id}`} className="flex flex-wrap gap-x-2">
                      <span className="font-medium">{s.label}</span>
                      <Link to={`/infrastructure/${s.source_id}`} className="mono text-muted hover:underline">{s.source_id}</Link>
                      {s.peak != null && <span>peak <b>{fmtVal(s.peak, unitOf(s.metric))}</b> vs expected ≤ {fmtVal(s.expected_high, unitOf(s.metric))}</span>}
                      {s.robust_z != null && <span className="text-muted">z={s.robust_z.toFixed(1)}</span>}
                    </li>))}
                </ul>
              </div>
              <div>
                <div className="label mb-1 flex items-center gap-1"><ListChecks size={12} aria-hidden />Recommended investigation steps</div>
                {!steps.length ? <p className="text-xs text-muted">No investigation steps for this rule.</p> : (
                  <ol className="list-decimal space-y-1 pl-5 text-xs">{steps.map((r) => <li key={r.id}><span className="font-medium">{r.title}</span><div className="text-muted">{r.rationale}</div></li>)}</ol>)}
              </div>
            </div>
          </motion.article>
        )
      })}
    </section>
  )
}

// ---------------------------------------------------------------- Recommendations
function RecommendationsTab({ recs, incidents }) {
  const { push } = useToast()
  const [kind, setKind] = useState('all')
  const [status, setStatus] = useState('active')
  const [confirm, setConfirm] = useState(null) // { rec, action }
  const [busy, setBusy] = useState(false)
  const incMap = useMemo(() => Object.fromEntries((incidents.data || []).map((i) => [i.id, i])), [incidents.data])
  const list = (recs.data || []).filter((r) => {
    const inc = incMap[r.incident_id]
    if (kind !== 'all' && r.kind !== kind) return false
    if (status === 'active') return inc && inc.status !== 'resolved'
    if (status === 'executed') return r.status === 'executed' || r.status === 'reverted'
    return true
  }).sort((a, b) => (SEV_RANK[incMap[a.incident_id]?.severity] ?? 3) - (SEV_RANK[incMap[b.incident_id]?.severity] ?? 3))

  async function run() {
    const { rec, action } = confirm
    setBusy(true)
    try {
      await api(`/aiops/recommendations/${rec.id}/${action}`, { method: 'POST' })
      push({ title: action === 'execute' ? 'Remediation executed (simulated)' : 'Remediation reverted', message: `${rec.title} · ${rec.incident_id} — recorded in the audit log`, tone: 'success' })
      recs.refresh()
    } catch (e) {
      push({ title: 'Action failed', message: e.message, tone: 'error' })
    } finally { setBusy(false); setConfirm(null) }
  }

  return (
    <section aria-label="AI recommendations">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Segmented label="Recommendation scope" value={status} onChange={setStatus}
          options={[{ value: 'active', label: 'Active incidents' }, { value: 'executed', label: 'Executed / reverted' }, { value: 'all', label: 'All' }]} />
        <Select label="Kind" value={kind} onChange={setKind} options={[{ value: 'all', label: 'All kinds' }, { value: 'investigate', label: 'Investigate' }, { value: 'remediate', label: 'Remediate' }]} />
      </div>
      <ErrorState error={recs.error} onRetry={recs.refresh} />
      {recs.loading && !recs.data ? <LoadingSkeleton rows={3} h={70} /> : !list.length ? <EmptyState title="No recommendations in this view" Icon={Sparkles} /> : (
        <div className="grid gap-x-3 xl:grid-cols-2">
          {list.map((r) => {
            const inc = incMap[r.incident_id]
            return (
              <motion.div key={r.id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <div className="mb-1 flex items-center gap-2 text-[11px] text-muted">
                  {inc && <SeverityBadge severity={inc.severity} />}<span>{inc?.title || r.incident_id}</span>{inc && <span>· {inc.status}</span>}
                  <span className="ml-auto">{fmtDateTime(r.created_at)}</span>
                </div>
                <RecommendationCard rec={r} busy={busy} disabled={inc?.status === 'resolved'}
                  onExecute={(rec) => setConfirm({ rec, action: 'execute' })} onRevert={(rec) => setConfirm({ rec, action: 'revert' })} />
              </motion.div>
            )
          })}
        </div>
      )}
      <ConfirmationDialog open={!!confirm} busy={busy} onCancel={() => setConfirm(null)} onConfirm={run}
        title={confirm?.action === 'execute' ? 'Approve remediation' : 'Revert remediation'}
        confirmLabel={confirm?.action === 'execute' ? 'Approve & run (simulated)' : 'Revert'}
        tone={confirm?.action === 'revert' ? 'danger' : 'primary'}
        message={confirm && (
          <div className="space-y-2">
            <p><b className="text-text">{confirm.rec.title}</b> for incident <span className="mono">{confirm.rec.incident_id}</span>.</p>
            <p className="flex items-start gap-2"><FlaskConical size={14} className="mt-0.5 shrink-0 text-warn" />Execution is SIMULATED in this MVP — no change is made to customer infrastructure. Your approval is recorded in the audit log and can be reverted.</p>
          </div>)} />
    </section>
  )
}

// ---------------------------------------------------------------- Baselines & method
function BaselinesTab() {
  const q = usePoll((signal) => api('/aiops/baselines', { signal, expect: 'array' }), 6000)
  return (
    <section className="card p-4" aria-label="Baselines">
      <h3 className="text-sm font-semibold">Current baselines</h3>
      <p className="mb-3 text-xs text-muted">Rolling median ± 3·σ (σ = 1.4826·MAD, floored per metric) over the last 60 non-anomalous points.</p>
      <DataTable rows={q.data} loading={q.loading} error={q.error} onRetry={q.refresh} rowKey={(b) => b.source_id + b.metric} maxHeight={560} dense
        empty="No baselines yet" emptyHint="Baselines form after collectors send their first data."
        columns={[
          { key: 'source_id', header: 'Source', render: (b) => <Link className="mono hover:underline" to={`/infrastructure/${b.source_id}`}>{b.source_id}</Link> },
          { key: 'label', header: 'Metric' },
          { key: 'median', header: 'Median', align: 'right', render: (b) => fmtVal(b.median, b.unit) },
          { key: 'expected_high', header: 'Expected range', render: (b) => `${fmtVal(b.expected_low, b.unit)} – ${fmtVal(b.expected_high, b.unit)}` },
          { key: 'latest', header: 'Latest', align: 'right', render: (b) => fmtVal(b.latest, b.unit) },
          { key: 'latest_z', header: 'Latest z', align: 'right', render: (b) => <span className={b.latest_z >= 3 ? 'font-semibold text-crit' : ''}>{b.latest_z != null ? b.latest_z.toFixed(1) : '—'}</span> },
          { key: 'points', header: 'Points', align: 'right', render: (b) => <>{b.points}{b.warming_up && <span className="ml-1 text-warn">warming up</span>}</> },
        ]} />
    </section>
  )
}

function MethodTab({ q }) {
  if (q.error) return <ErrorState error={q.error} onRetry={q.refresh} />
  if (!q.data) return <LoadingSkeleton rows={4} />
  const m = q.data
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="card p-4">
        <h3 className="text-sm font-semibold">Detection method</h3>
        <p className="mt-2 text-sm">{m.detector}</p>
        <p className="mt-2 text-sm text-ai">{m.statement}</p>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div><dt className="label">Warm-up points</dt><dd>{m.rules.warmup_points}</dd></div>
          <div><dt className="label">Immediate threshold</dt><dd>z ≥ {m.rules.z_immediate}</dd></div>
          <div><dt className="label">Sustained threshold</dt><dd>z ≥ {m.rules.z_sustained_2_points} on 2 consecutive points</dd></div>
        </dl>
        <div className="label mb-1 mt-3">Minimum absolute deviation</div>
        <div className="flex flex-wrap gap-1">{Object.entries(m.rules.min_absolute_deviation).map(([k, v]) => <span key={k} className="chip mono">{k}: {v}</span>)}</div>
        <p className="mt-3 text-xs text-muted">{m.correlation}</p>
      </section>
      <section className="card p-4">
        <h3 className="mb-2 text-sm font-semibold">Correlation rules (evaluated in order)</h3>
        <ol className="space-y-2">
          {m.correlation_rules.map((r) => (
            <li key={r.id} className="border-b border-line/60 pb-2 text-sm last:border-0">
              <div><span className="mono text-xs text-ai">{r.id}</span> · <b>{r.title}</b></div>
              <div className="text-xs text-muted">requires {r.requires.join(' + ')} → {r.probable_cause}</div>
              <div className="mt-1 flex flex-wrap gap-1">{r.actions.map((a) => <span key={a} className="chip mono">{a}</span>)}</div>
            </li>))}
        </ol>
      </section>
    </div>
  )
}
