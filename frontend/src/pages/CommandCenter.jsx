import { useMemo, useState } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { motion } from 'framer-motion'
import { AlertOctagon, Brain, Clock, Layers, Server, Siren, Sparkles } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import ChartCard from '../components/ChartCard'
import DataTable from '../components/DataTable'
import {
  EmptyState, ErrorState, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, SearchInput,
  StatusIndicator, TimeRangeSelector,
} from '../components/ds'
import { IncidentCard } from '../components/domain'
import { HEALTH, HealthBadge, SeverityBadge, ago, fmtTime, fmtVal } from '../components/ui'

// thresholds → { metric: [fairLine, poorLine] } (good_below marks entry into Fair, fair_below into Poor)
function useThresholdLines() {
  const { data } = usePoll((signal) => api('/settings/thresholds', { signal, expect: 'array' }), 0)
  return useMemo(() => Object.fromEntries((data || []).map((t) => [t.metric, [t.good_below, t.fair_below]])), [data])
}

const CHARTS = [
  ['cpu_pct', 'CPU utilisation'], ['mem_pct', 'Memory utilisation'], ['app_response_ms', 'Application response time'],
  ['latency_ms', 'Network latency'], ['packet_loss_pct', 'Packet loss'], ['disk_pct', 'Disk utilisation'],
]

function Panel({ title, subtitle, actions, children, tone }) {
  return (
    <section className={`card p-4 ${tone === 'ai' ? 'border-ai/40' : ''}`}>
      <header className="mb-3 flex items-start justify-between gap-2">
        <div><h2 className="text-sm font-semibold">{title}</h2>{subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}</div>
        {actions}
      </header>
      {children}
    </section>
  )
}

export default function CommandCenter() {
  const { overview } = useOutletContext()
  const nav = useNavigate()
  const ov = overview.data
  const lines = useThresholdLines()
  const [stateFilter, setStateFilter] = useState(null)
  const [q, setQ] = useState('')
  const events = usePoll((signal) => api('/events/recent?limit=12', { signal, expect: 'array' }), 5000)

  const sources = useMemo(() => (ov?.sources || []).filter((s) =>
    (!stateFilter || s.health_state === stateFilter)
    && (!q || s.source_id.includes(q.toLowerCase()) || s.role.includes(q.toLowerCase()))), [ov, stateFilter, q])

  if (!ov) {
    return (
      <div>
        <PageHeader title="Command Center" description="Live operational overview of every monitored source." />
        {overview.error ? <ErrorState error={overview.error} onRetry={overview.refresh} /> : <LoadingSkeleton rows={6} h={60} />}
      </div>
    )
  }
  const k = ov.kpis
  const dist = ov.health.distribution
  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1
  const age = ov.freshness.age_s
  const fresh = age != null && age < 15

  return (
    <div className="space-y-4">
      <PageHeader title="Command Center"
        description="Live metadata received from authenticated local collectors. All figures come from the NEXORA SaaS API."
        actions={<><TimeRangeSelector /><RefreshButton onClick={overview.refresh} loading={overview.loading} /></>} />

      {overview.error && <ErrorState error={overview.error} onRetry={overview.refresh} />}

      <Reveal className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <motion.div variants={{ initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 } }} className="col-span-2 md:col-span-1">
          <Link to="/infrastructure" className="card card-hover block h-full p-4">
            <div className="label">Overall health</div>
            <div className="mt-2"><HealthBadge state={ov.health.state} size="lg" /></div>
            <div className="mt-2 line-clamp-2 text-xs text-muted" title={ov.health.reason}>{ov.health.reason}</div>
          </Link>
        </motion.div>
        <MetricCard label="Monitored sources" value={k.monitored_sources} Icon={Layers} to="/infrastructure"
          hint={`${ov.health.stale_sources} stale`} />
        <MetricCard label="Collectors online" value={`${k.collectors_online}/${k.collectors_total}`} Icon={Server} to="/collectors"
          tone={k.collectors_offline ? 'crit' : 'ok'} hint={k.collectors_offline ? `${k.collectors_offline} offline` : 'all connected'}
          srStatus={k.collectors_offline ? 'some collectors offline' : 'all collectors online'} />
        <MetricCard label="Active incidents" value={k.active_incidents} Icon={Siren} to="/incidents" tone={k.active_incidents ? 'warn' : 'ok'} />
        <MetricCard label="Critical alerts" value={k.critical_alerts} Icon={AlertOctagon} to="/incidents" tone={k.critical_alerts ? 'crit' : 'ok'}
          pulse={k.critical_alerts > 0} srStatus={k.critical_alerts ? 'critical alerts active' : 'no critical alerts'} />
        <MetricCard label="Active anomalies" value={k.active_anomalies} Icon={Brain} to="/aiops" tone="ai" />
      </Reveal>

      <Panel title="Health distribution & source inventory"
        subtitle="Four-state SaaS classification with configurable thresholds. Stale telemetry is downgraded, never shown as current. Click a state to filter."
        actions={<span className="flex items-center gap-2 text-xs text-muted"><Clock size={13} aria-hidden />
          <StatusIndicator state={fresh ? 'ok' : 'warn'} pulse={fresh} label={`Latest telemetry ${age != null ? `${age.toFixed(0)}s old` : 'n/a'}`} /></span>}>
        <div className="mb-3 flex h-3 w-full gap-[2px] overflow-hidden rounded-full bg-panel-2" aria-hidden>
          {Object.entries(dist).filter(([, n]) => n).map(([s, n]) => (
            <motion.div key={s} title={`${HEALTH[s].label}: ${n}`} initial={{ width: 0 }} animate={{ width: `${(n / total) * 100}%` }}
              transition={{ duration: 0.5, ease: 'easeOut' }} style={{ background: HEALTH[s].color }} />
          ))}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Filter sources by health state">
          {Object.entries(dist).map(([s, n]) => (
            <button key={s} onClick={() => setStateFilter(stateFilter === s ? null : s)} aria-pressed={stateFilter === s}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors ${stateFilter === s ? 'border-accent bg-accent/10 text-text' : 'border-line text-muted hover:text-text'}`}>
              <span className="h-2 w-2 rounded-full" style={{ background: HEALTH[s].color }} aria-hidden />{HEALTH[s].label}<b className="text-text">{n}</b>
            </button>
          ))}
          {stateFilter && <button className="text-xs text-accent hover:underline" onClick={() => setStateFilter(null)}>Clear filter</button>}
          <div className="ml-auto w-full sm:w-64"><SearchInput value={q} onChange={setQ} placeholder="Filter by source id or role" /></div>
        </div>
        <DataTable rows={sources} rowKey={(s) => s.source_id} onRowClick={(s) => nav(`/infrastructure/${s.source_id}`)}
          empty={stateFilter || q ? 'No sources match the filter' : 'No sources reporting yet'}
          columns={[
            { key: 'source_id', header: 'Source', render: (s) => <span className="mono text-xs">{s.source_id}</span> },
            { key: 'role', header: 'Role' },
            { key: 'health_state', header: 'Health', sortValue: (s) => ['excellent', 'good', 'fair', 'poor'].indexOf(s.health_state),
              render: (s) => <HealthBadge state={s.health_state} stale={s.stale} /> },
            { key: 'cpu', header: 'CPU', align: 'right', sortValue: (s) => s.metrics.cpu_pct, render: (s) => fmtVal(s.metrics.cpu_pct, '%') },
            { key: 'mem', header: 'Mem', align: 'right', sortValue: (s) => s.metrics.mem_pct, render: (s) => fmtVal(s.metrics.mem_pct, '%') },
            { key: 'disk', header: 'Disk', align: 'right', sortValue: (s) => s.metrics.disk_pct, render: (s) => fmtVal(s.metrics.disk_pct, '%') },
            { key: 'lat', header: 'Latency', align: 'right', sortValue: (s) => s.metrics.latency_ms, render: (s) => fmtVal(s.metrics.latency_ms, 'ms') },
            { key: 'loss', header: 'Loss', align: 'right', sortValue: (s) => s.metrics.packet_loss_pct,
              render: (s) => (s.metrics.packet_loss_pct != null ? `${s.metrics.packet_loss_pct.toFixed(2)}%` : '—') },
            { key: 'app', header: 'App resp.', align: 'right', sortValue: (s) => s.metrics.app_response_ms, render: (s) => fmtVal(s.metrics.app_response_ms, 'ms') },
            { key: 'age', header: 'Data age', sortValue: (s) => s.data_age_s, render: (s) => <span className={s.stale ? 'text-warn' : 'text-muted'}>{ago(s.data_age_s)}</span> },
            { key: 'reason', header: 'Reason', sortable: false, className: 'max-w-xs text-xs text-muted', render: (s) => s.health_reason },
          ]} />
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {CHARTS.map(([m, t]) => <ChartCard key={m} metric={m} title={t} thresholds={lines[m]} />)}
      </div>

      <div className="grid gap-3 xl:grid-cols-3">
        <Panel title="Recent incidents" actions={<Link className="btn" to="/incidents">All incidents</Link>}>
          {!ov.incidents.length ? <EmptyState title="No active incidents">Incidents open automatically when correlated anomalies are detected.</EmptyState>
            : ov.incidents.map((i) => <IncidentCard key={i.id} incident={i} to={`/incidents?incident=${i.id}`} />)}
        </Panel>
        <Panel title="AI recommendations" subtitle="Explainable — each quotes its evidence and rule" tone="ai"
          actions={<Link className="btn" to="/aiops">AIOps</Link>}>
          {!ov.recommendations.length ? <EmptyState title="No open recommendations" Icon={Sparkles} /> : ov.recommendations.map((r) => (
            <div key={r.id} className="mb-2 rounded-lg border border-ai/30 bg-ai/5 p-3">
              <div className="flex items-center gap-2 text-sm font-medium"><Sparkles size={14} className="text-ai" aria-hidden />{r.title}</div>
              <div className="mt-1 text-xs text-muted">{r.rationale}</div>
              <div className="mt-1 text-[11px] text-muted"><Link className="mono hover:underline" to={`/incidents?incident=${r.incident_id}`}>{r.incident_id}</Link>{r.requires_approval && ' · requires approval (simulated)'}</div>
            </div>
          ))}
        </Panel>
        <Panel title="Recent operational events" subtitle="Health transitions, anomalies and audited actions">
          {events.error && !events.data && <ErrorState error={events.error} onRetry={events.refresh} />}
          {!events.data ? <LoadingSkeleton rows={5} /> : !events.data.length ? <EmptyState title="No events yet" /> : (
            <ul className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
              {events.data.map((e, idx) => (
                <motion.li key={`${e.at}-${idx}`} layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="border-b border-line/60 pb-2 text-xs last:border-0">
                  <div className="flex items-center justify-between gap-2"><span className="mono text-muted">{fmtTime(e.at)}</span><SeverityBadge severity={e.severity} /></div>
                  <div className="mt-0.5 break-words">{e.text}</div>
                </motion.li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  )
}
