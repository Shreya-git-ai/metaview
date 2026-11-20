import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Server } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import ChartCard from '../components/ChartCard'
import DataTable from '../components/DataTable'
import {
  EmptyState, ErrorState, KV, LoadingSkeleton, PageHeader, RefreshButton, StatusIndicator, TimeRangeSelector,
} from '../components/ds'
import { AnomalyCard, IncidentCard } from '../components/domain'
import { HealthBadge, ago, fmtDateTime, fmtVal } from '../components/ui'

const CHARTS = [
  ['cpu_pct', 'CPU utilisation'], ['mem_pct', 'Memory utilisation'], ['disk_pct', 'Disk utilisation'],
  ['app_response_ms', 'Application response time'], ['latency_ms', 'Network latency'], ['packet_loss_pct', 'Packet loss'],
]

function Panel({ title, subtitle, children }) {
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Detail({ sourceId }) {
  const d = usePoll((signal) => api(`/sources/${encodeURIComponent(sourceId)}`, { signal, expect: 'object' }), 4000)
  if (d.error && !d.data) {
    return (
      <div className="space-y-3">
        <PageHeader title="Source not found" />
        <ErrorState error={d.error.status === 404 ? new Error(`Source ${sourceId} does not exist in your tenant.`) : d.error} onRetry={d.refresh} />
        <Link className="btn" to="/infrastructure"><ArrowLeft size={14} />Back to infrastructure</Link>
      </div>
    )
  }
  if (!d.data) return <LoadingSkeleton rows={6} h={60} />
  const s = d.data
  const th = s.thresholds || {}
  const c = s.collector
  const m = s.metrics || {}
  return (
    <div className="space-y-4">
      <PageHeader title={<span className="mono">{s.source_id}</span>}
        badge={<HealthBadge state={s.health_state} stale={s.stale} />}
        description={s.health_reason}
        actions={<><Link className="btn" to="/infrastructure"><ArrowLeft size={14} />Inventory</Link><TimeRangeSelector /><RefreshButton onClick={d.refresh} loading={d.loading} /></>} />

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="card p-4 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold">Identity & current state</h2>
          <KV cols={3} items={[
            ['Role', s.role],
            ['Collector', <span key="c" className="mono">{s.collector_id}</span>],
            ['Data age', <span key="a" className={s.stale ? 'text-warn' : ''}>{ago(s.data_age_s)}{s.stale ? ' (stale)' : ''}</span>],
            ['Last event (UTC)', fmtDateTime(s.last_event_time)],
            ['First seen (UTC)', fmtDateTime(s.first_seen)],
            ['Health changed (UTC)', fmtDateTime(s.health_changed_at)],
            ['Reported by collector', s.reported_health ? <HealthBadge key="r" state={s.reported_health} /> : '—'],
            ['Trigger', s.health_trigger || '—'],
            ['App status', m.app_up === false ? 'down' : m.app_up ? 'up' : '—'],
          ]} />
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
            {[['cpu_pct', 'CPU', '%'], ['mem_pct', 'Memory', '%'], ['disk_pct', 'Disk', '%'], ['latency_ms', 'Latency', 'ms'],
              ['packet_loss_pct', 'Loss', null], ['app_response_ms', 'App resp.', 'ms']].map(([k, label, unit]) => (
              <div key={k} className="rounded-lg border border-line p-2">
                <div className="label">{label}</div>
                <div className="mt-0.5 text-sm font-semibold tabular-nums">{k === 'packet_loss_pct' ? (m[k] != null ? `${m[k].toFixed(2)}%` : '—') : fmtVal(m[k], unit)}</div>
              </div>
            ))}
          </div>
        </section>
        <section className="card p-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Server size={15} aria-hidden />Collector</h2>
          {!c ? <EmptyState title="Collector unknown" /> : (
            <KV items={[
              ['Collector', <span key="i" className="mono">{c.id}</span>],
              ['Status', <StatusIndicator key="s" state={c.status === 'online' ? 'ok' : 'crit'} label={c.status} pulse={c.status === 'online'} />],
              ['Last heartbeat', `${ago(c.heartbeat_age_s)} · ${fmtDateTime(c.last_heartbeat_at)}`],
              ['Version', c.version],
              ['Queue pending / retrying', `${c.queue.pending} / ${c.queue.retrying}`],
              ['Config', c.config_status],
            ]} />
          )}
          {c && <Link className="btn mt-3" to={`/collectors?collector=${c.id}`}>Open in fleet</Link>}
        </section>
      </div>

      <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {CHARTS.map(([metric, title]) => (
          <ChartCard key={metric} metric={metric} title={title} sourceId={s.source_id}
            thresholds={th[metric] ? [th[metric][1], th[metric][2]] : undefined} />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Anomalies on this source" subtitle="Robust z-score against this source's own rolling baseline">
          {!s.anomalies.length ? <EmptyState title="No anomalies detected" /> : (
            <div className="max-h-[480px] overflow-y-auto pr-1">{s.anomalies.map((a) => <AnomalyCard key={a.id} anomaly={a} />)}</div>
          )}
        </Panel>
        <Panel title="Related incidents">
          {!s.incidents.length ? <EmptyState title="No incidents involve this source" /> : (
            <div className="max-h-[480px] overflow-y-auto pr-1">{s.incidents.map((i) => <IncidentCard key={i.id} incident={i} to={`/incidents?incident=${i.id}`} />)}</div>
          )}
        </Panel>
      </div>

      <Panel title="Health transition history (UTC)" subtitle="Previous → new state, triggering metric and reason">
        <DataTable rows={s.transitions} rowKey={(t) => t.at + t.new_state} dense maxHeight={360} empty="No transitions recorded"
          columns={[
            { key: 'at', header: 'At', render: (t) => <span className="mono">{fmtDateTime(t.at)}</span> },
            { key: 'change', header: 'Change', sortable: false, render: (t) => (
              <span className="flex flex-wrap items-center gap-1">{t.prev_state ? <HealthBadge state={t.prev_state} /> : '—'} → <HealthBadge state={t.new_state} /></span>) },
            { key: 'trigger_metric', header: 'Trigger' },
            { key: 'reason', header: 'Reason', sortable: false, className: 'text-muted' },
          ]} />
      </Panel>
    </div>
  )
}

export default function SourceDetail() {
  const { sourceId } = useParams()
  return <Detail key={sourceId} sourceId={sourceId} />
}
