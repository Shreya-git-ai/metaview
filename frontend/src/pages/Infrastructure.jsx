import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cpu, Database, HardDrive, Timer } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import ChartCard from '../components/ChartCard'
import DataTable from '../components/DataTable'
import {
  ErrorState, MetricCard, PageHeader, RefreshButton, Reveal, SearchInput, Segmented, Select, TimeRangeSelector,
} from '../components/ds'
import { HealthBadge, ago, fmtVal } from '../components/ui'

const METRICS = [
  { value: 'cpu_pct', label: 'CPU', unit: '%', Icon: Cpu },
  { value: 'mem_pct', label: 'Memory', unit: '%', Icon: Database },
  { value: 'disk_pct', label: 'Disk', unit: '%', Icon: HardDrive },
  { value: 'app_response_ms', label: 'App response', unit: 'ms', Icon: Timer },
]

function useThresholdLines() {
  const { data } = usePoll((signal) => api('/settings/thresholds', { signal, expect: 'array' }), 0)
  return useMemo(() => Object.fromEntries((data || []).map((t) => [t.metric, [t.good_below, t.fair_below]])), [data])
}

export default function Infrastructure() {
  const nav = useNavigate()
  const src = usePoll((signal) => api('/sources', { signal, expect: 'array' }), 3000)
  const lines = useThresholdLines()
  const [metric, setMetric] = useState('cpu_pct')
  const [q, setQ] = useState('')
  const [role, setRole] = useState('all')
  const [health, setHealth] = useState('all')

  const rows = src.data
  const roles = useMemo(() => ['all', ...new Set((rows || []).map((s) => s.role))], [rows])
  const filtered = useMemo(() => (rows || []).filter((s) => (role === 'all' || s.role === role)
    && (health === 'all' || s.health_state === health)
    && (!q || s.source_id.includes(q.toLowerCase()))), [rows, role, health, q])

  // summary from current (latest) source metrics only — no invented values
  const summary = useMemo(() => METRICS.map((m) => {
    const vals = (rows || []).filter((s) => !s.stale).map((s) => s.metrics[m.value]).filter((v) => v != null)
    return { ...m, avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null, max: vals.length ? Math.max(...vals) : null, n: vals.length }
  }), [rows])

  const sel = METRICS.find((m) => m.value === metric)
  return (
    <div className="space-y-4">
      <PageHeader title="Infrastructure Monitoring"
        description="Inventory and resource history of every source reported by your collectors (pseudonymous source ids — hostnames never leave the customer environment)."
        actions={<><TimeRangeSelector /><RefreshButton onClick={src.refresh} loading={src.loading} /></>} />
      {src.error && <ErrorState error={src.error} onRetry={src.refresh} />}

      <Reveal className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {summary.map((m) => (
          <MetricCard key={m.value} label={`${m.label} · fleet average`} Icon={m.Icon} value={m.avg}
            decimals={m.unit === '%' ? 1 : 0} suffix={m.unit === '%' ? '%' : ' ms'}
            hint={m.n ? `max ${fmtVal(m.max, m.unit)} · ${m.n} live source(s)` : 'no live sources'}
            onClick={() => setMetric(m.value)} />
        ))}
      </Reveal>

      <section className="card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="text-sm font-semibold">Metric comparison</h2>
            <p className="text-xs text-muted">All sources on one chart · dashed lines are the tenant's Fair / Poor thresholds · ◆ marks points the detector flagged as anomalous</p></div>
          <Segmented label="Metric" value={metric} onChange={setMetric} options={METRICS.map((m) => ({ value: m.value, label: m.label }))} />
        </div>
        <ChartCard key={metric} metric={metric} title={`${sel.label} — all sources`} height={300} thresholds={lines[metric]}
          onSourceClick={(sid) => sid && sid !== 'anomaly' && nav(`/infrastructure/${sid}`)} />
      </section>

      <section className="card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="text-sm font-semibold">Source inventory</h2><p className="text-xs text-muted">{filtered.length} of {rows?.length ?? 0} source(s) · click a row for details</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <Select label="Role" value={role} onChange={setRole} options={roles.map((r) => ({ value: r, label: r === 'all' ? 'All roles' : r }))} />
            <Select label="Health" value={health} onChange={setHealth}
              options={[{ value: 'all', label: 'All' }, ...['excellent', 'good', 'fair', 'poor'].map((h) => ({ value: h, label: h[0].toUpperCase() + h.slice(1) }))]} />
            <div className="w-full sm:w-56"><SearchInput value={q} onChange={setQ} placeholder="Search source id" /></div>
          </div>
        </div>
        <DataTable rows={rows ? filtered : null} loading={src.loading} error={src.error} onRetry={src.refresh}
          rowKey={(s) => s.source_id} onRowClick={(s) => nav(`/infrastructure/${s.source_id}`)}
          empty="No sources match" emptyHint="Sources appear once a collector reports metrics."
          columns={[
            { key: 'source_id', header: 'Source', render: (s) => <span className="mono text-xs">{s.source_id}</span> },
            { key: 'role', header: 'Role' },
            { key: 'collector_id', header: 'Collector', render: (s) => <span className="mono text-xs">{s.collector_id}</span> },
            { key: 'health', header: 'Health', sortValue: (s) => ['excellent', 'good', 'fair', 'poor'].indexOf(s.health_state),
              render: (s) => <HealthBadge state={s.health_state} stale={s.stale} /> },
            { key: 'cpu', header: 'CPU', align: 'right', sortValue: (s) => s.metrics.cpu_pct, render: (s) => fmtVal(s.metrics.cpu_pct, '%') },
            { key: 'mem', header: 'Memory', align: 'right', sortValue: (s) => s.metrics.mem_pct, render: (s) => fmtVal(s.metrics.mem_pct, '%') },
            { key: 'disk', header: 'Disk', align: 'right', sortValue: (s) => s.metrics.disk_pct, render: (s) => fmtVal(s.metrics.disk_pct, '%') },
            { key: 'app', header: 'App resp.', align: 'right', sortValue: (s) => s.metrics.app_response_ms, render: (s) => fmtVal(s.metrics.app_response_ms, 'ms') },
            { key: 'app_up', header: 'App', render: (s) => (s.metrics.app_up === false ? <span className="text-crit">down</span> : s.metrics.app_up ? 'up' : '—') },
            { key: 'age', header: 'Data age', sortValue: (s) => s.data_age_s, render: (s) => <span className={s.stale ? 'text-warn' : 'text-muted'}>{ago(s.data_age_s)}</span> },
            { key: 'reason', header: 'Reason', sortable: false, className: 'max-w-xs text-xs text-muted', render: (s) => s.health_reason },
          ]} />
      </section>
    </div>
  )
}
