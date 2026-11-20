import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, Database, Gauge, Server, Timer, Zap } from 'lucide-react'
import { api, session } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import { CHART, useTheme } from '../services/theme'
import { EmptyState, ErrorState, KV, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, StatusIndicator, Tabs } from '../components/ds'
import DataTable from '../components/DataTable'
import { Card, fmtDateTime } from '../components/ui'

const fmtBytes = (b) => (b == null ? '—' : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${(b / 1e3).toFixed(0)} kB`)
const fmtMs = (v) => (v == null ? '—' : `${v.toFixed(v < 10 ? 1 : 0)} ms`)
const fmtUptime = (s) => (s == null ? '—' : s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m ${s % 60}s`)

export default function Performance() {
  const [tab, setTab] = useState('platform')
  return (
    <div>
      <PageHeader title="Performance"
        description="Two different things, measured separately: the health of the NEXORA platform itself, and the end-to-end SLOs of customer telemetry flowing through it. Every number is computed from recorded timestamps — nothing is hard-coded." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'platform', label: 'NEXORA platform' }, { value: 'slo', label: 'Customer telemetry SLOs' }]} />
      <div className="mt-4">{tab === 'platform' ? <Platform /> : <Slo />}</div>
    </div>
  )
}

function Platform() {
  const [theme] = useTheme()
  const c = CHART[theme] || CHART.dark
  const d = usePoll((signal) => api('/platform/health', { signal, expect: 'object' }), 5000)
  if (d.error && !d.data) return <ErrorState error={d.error} onRetry={d.refresh} />
  if (!d.data) return <LoadingSkeleton rows={6} h={40} />
  const p = d.data
  const vol = (p.ingestion.per_minute || []).map((m) => ({ ...m, label: m.minute.slice(11, 16) }))
  const e5 = p.api.error_rate_5xx * 100
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">{p.scope_note}</p>
        <RefreshButton onClick={d.refresh} loading={d.loading} />
      </div>
      <Reveal className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Backend availability" value={p.availability.status === 'up' ? 'Up' : 'Down'} tone={p.availability.status === 'up' ? 'ok' : 'crit'} Icon={Server}
          hint={`uptime ${fmtUptime(p.availability.uptime_s)} · TLS`}>
          <div className="mt-1"><StatusIndicator state={p.availability.status === 'up' ? 'ok' : 'crit'} label="responding" pulse /></div>
        </MetricCard>
        <MetricCard label="API p95 latency" value={p.api.p95_ms} decimals={1} suffix=" ms" Icon={Timer} hint={`p50 ${fmtMs(p.api.p50_ms)} · ${p.api.requests} req / 15 min`} />
        <MetricCard label="5xx error rate" value={e5} decimals={2} suffix="%" tone={e5 > 1 ? 'crit' : 'ok'} Icon={Zap} hint={`4xx ${(p.api.error_rate_4xx * 100).toFixed(2)}% (incl. denied requests)`} />
        <MetricCard label="Processing latency p95" value={p.ingestion.processing_latency_p95_s} decimals={2} suffix=" s" Icon={Activity} hint={`window end → stored · ${p.ingestion.samples} events / 15 min`} />
        <MetricCard label="Collector link p95" value={p.collector_link.heartbeat_transport_p95_ms} decimals={1} suffix=" ms" Icon={Gauge} hint={`heartbeat transport · ${p.collector_link.samples} samples`} />
        <MetricCard label="Database ping" value={p.database.ping_ms} decimals={2} suffix=" ms" Icon={Database} hint={`${p.database.engine} · ${fmtBytes(p.database.size_bytes)}`} />
      </Reveal>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Ingestion volume" subtitle="Approved events stored per minute for the selected tenant (last 30 min, UTC)">
          {!vol.length ? <EmptyState title="No events in the last 30 minutes" /> : (
            <div style={{ height: 200 }} role="img" aria-label="Ingested events per minute">
              <ResponsiveContainer>
                <BarChart data={vol} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                  <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: c.axis, fontSize: 10 }} stroke={c.grid} minTickGap={20} />
                  <YAxis tick={{ fill: c.axis, fontSize: 10 }} stroke={c.grid} allowDecimals={false} width={40} />
                  <Tooltip cursor={{ fill: 'color-mix(in srgb, var(--color-text) 6%, transparent)' }} contentStyle={{ background: c.tipBg, border: `1px solid ${c.tipBorder}`, borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: c.axis }} itemStyle={{ color: c.tipText }} formatter={(v) => [v, 'events']} labelFormatter={(l) => `${l}Z`} />
                  <Bar dataKey="events" fill={c.series[0]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>)}
        </Card>
        <Card title="API requests by area" subtitle={p.api.note}>
          <DataTable rows={p.api.by_group} rowKey={(r) => r.group} dense empty="No requests yet" initialSort={{ key: 'requests', dir: 'desc' }}
            columns={[{ key: 'group', header: 'Area', render: (r) => <span className="mono">/{r.group}</span> },
              { key: 'requests', header: 'Requests', align: 'right' },
              { key: 'p50_ms', header: 'p50', align: 'right', render: (r) => fmtMs(r.p50_ms) },
              { key: 'p95_ms', header: 'p95', align: 'right', render: (r) => fmtMs(r.p95_ms) },
              { key: 'errors_4xx', header: '4xx', align: 'right' },
              { key: 'errors_5xx', header: '5xx', align: 'right', render: (r) => <span className={r.errors_5xx ? 'text-crit' : ''}>{r.errors_5xx}</span> }]} />
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Database health">
          <KV cols={3} items={[['Engine', p.database.engine], ['Ping', fmtMs(p.database.ping_ms)], ['File size', fmtBytes(p.database.size_bytes)], ['WAL size', fmtBytes(p.database.wal_bytes)],
            ['Maintenance last run', fmtDateTime(p.availability.maintenance_last_run)], ['Maintenance duration', fmtMs(p.availability.maintenance_last_ms)]]} />
          <div className="label mb-1 mt-4">Table sizes</div>
          {p.database.row_counts ? (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{Object.entries(p.database.row_counts).map(([k, v]) => (
              <div key={k} className="rounded-lg border border-line p-2"><div className="label">{k.replace('_', ' ')}</div><div className="font-semibold tabular-nums">{v.toLocaleString()}</div></div>))}</div>
          ) : <p className="text-xs text-muted">Cross-tenant table sizes are visible to the platform operator only{session.user?.role === 'tenant_admin' ? ' — you are signed in as a tenant admin.' : '.'}</p>}
        </Card>
        <Card title="Availability & latency details">
          <KV cols={2} items={[['Started', fmtDateTime(p.availability.started_at)], ['Uptime', fmtUptime(p.availability.uptime_s)],
            ['Requests since start', p.api.requests_since_start?.toLocaleString()], ['API p50 / p95 (15 min)', `${fmtMs(p.api.p50_ms)} / ${fmtMs(p.api.p95_ms)}`],
            ['Processing latency p50 / p95', `${p.ingestion.processing_latency_p50_s ?? '—'} s / ${p.ingestion.processing_latency_p95_s ?? '—'} s`],
            ['Heartbeat transport p50 / p95', `${fmtMs(p.collector_link.heartbeat_transport_p50_ms)} / ${fmtMs(p.collector_link.heartbeat_transport_p95_ms)}`]]} />
          <p className="mt-3 text-xs text-muted">{p.collector_link.note}. Latency figures are measured in-process since the SaaS process started; a restart resets the API window.</p>
        </Card>
      </div>
    </div>
  )
}

function SloCard({ title, target, value, unit = 's', samples, note, extra }) {
  const ok = value == null ? null : value <= target
  return (
    <div className="card card-hover p-4">
      <div className="flex items-center justify-between gap-2"><div className="label">{title}</div>
        {ok != null && <StatusIndicator state={ok ? 'ok' : 'crit'} label={ok ? 'within target' : 'outside target'} />}</div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className={`text-2xl font-semibold tabular-nums ${ok == null ? 'text-muted' : ok ? 'text-good' : 'text-crit'}`}>{value == null ? 'no data' : `${value.toFixed(2)} ${unit}`}</span>
        <span className="text-xs text-muted">target ≤ {target} {unit}</span>
      </div>
      <div className="mt-1 text-xs text-muted">{samples} sample(s){extra ? ` · ${extra}` : ''}</div>
      <div className="mt-2 text-[11px] text-muted">{note}</div>
    </div>
  )
}

function Slo() {
  const d = usePoll((signal) => api('/performance?minutes=15', { signal, expect: 'object' }), 5000)
  if (d.error && !d.data) return <ErrorState error={d.error} onRetry={d.refresh} />
  if (!d.data) return <LoadingSkeleton rows={4} h={60} />
  const p = d.data
  const c2i = p.collection_to_ingest_s, c2d = p.collection_to_dashboard_s, b2a = p.breach_to_alert_s, off = p.offline_detection_s, del = p.delivery
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">Customer telemetry path (collector → SaaS → dashboard) over the last {p.window_minutes} min. Collector and SaaS share this machine's clock.</p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <SloCard title="Collection → dashboard (p95)" target={10} value={c2d.p95} samples={c2d.samples} note={c2d.note} extra={c2d.p50 != null ? `p50 ${c2d.p50.toFixed(2)}s` : ''} />
        <SloCard title="Collection → SaaS ingest (p95)" target={10} value={c2i.p95} samples={c2i.samples} note={c2i.note}
          extra={`${c2i.buffered_events} buffered${c2i.buffered_max_delay_s ? `, max ${c2i.buffered_max_delay_s.toFixed(0)}s` : ''}`} />
        <SloCard title="Threshold breach → alert (p95)" target={15} value={b2a.p95} samples={b2a.samples} note={b2a.note} extra={b2a.max != null ? `max ${b2a.max.toFixed(2)}s` : ''} />
        <SloCard title="Collector offline detection (last)" target={60} value={off.last} samples={off.samples} note={off.note} extra={off.max != null ? `max ${off.max.toFixed(1)}s` : ''} />
      </div>
      <Card title="Post-outage delivery" subtitle="Zero lost records and zero duplicates — verified by reconciliation from the collector">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[['Accepted', del.accepted_total], ['Duplicates rejected (idempotent)', del.duplicates_rejected_total], ['Rejected (schema/identity)', del.rejected_total], ['Stored events', del.stored_events], ['Duplicates stored', del.duplicates_stored]].map(([k, v]) => (
            <div key={k} className="rounded-lg border border-line p-3"><div className="label">{k}</div><div className="mt-1 text-xl font-semibold tabular-nums">{v.toLocaleString()}</div></div>))}
        </div>
        <div className="label mb-2 mt-4">Reconciliations (collector's delivered ids vs SaaS store)</div>
        <DataTable rows={del.reconciliations} rowKey={(r) => r.at + r.collector_id} dense empty="No reconciliations yet" emptyHint="Run “Reconcile delivery” in the Tier-1 Local Console after an outage."
          columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
            { key: 'collector_id', header: 'Collector', render: (r) => <span className="mono">{r.collector_id}</span> },
            { key: 'checked', header: 'Checked', align: 'right' }, { key: 'present', header: 'Present', align: 'right' },
            { key: 'missing', header: 'Missing (lost)', align: 'right', render: (r) => <span className={`font-semibold ${r.missing ? 'text-crit' : 'text-good'}`}>{r.missing}</span> }]} />
      </Card>
    </div>
  )
}
