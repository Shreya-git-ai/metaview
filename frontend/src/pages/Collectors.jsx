import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, AlertTriangle, CheckCircle2, ExternalLink, LayoutGrid, List, Server, WifiOff } from 'lucide-react'
import { api, LOCAL_CONSOLE_URL } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import { CHART, useTheme } from '../services/theme'
import {
  Drawer, EmptyState, ErrorState, KV, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, SearchInput,
  Segmented, Select, StatusIndicator, Tabs,
} from '../components/ds'
import DataTable from '../components/DataTable'
import { CollectorStatusCard, SourceHealthRow } from '../components/domain'
import { Card, HealthBadge, ago, fmtDateTime, fmtTime } from '../components/ui'

const STATE = { online: 'ok', degraded: 'warn', offline: 'crit', registered: 'idle' }
const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString())

export default function Collectors() {
  const [params, setParams] = useSearchParams()
  const selected = params.get('collector')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('all')
  const [view, setView] = useState('cards')
  const fleet = usePoll((signal) => api('/fleet', { signal, expect: 'object' }), 3000)
  const s = fleet.data?.summary
  const rows = useMemo(() => (fleet.data?.collectors || []).filter((c) =>
    (status === 'all' || c.fleet_status === status) &&
    (!q || `${c.id} ${c.tenant_id} ${c.region} ${c.version}`.toLowerCase().includes(q.toLowerCase()))), [fleet.data, q, status])
  const open = (id) => setParams(id ? { collector: id } : {})

  return (
    <div>
      <PageHeader title="Collector Fleet"
        description="Tier-1 collectors registered for this tenant. Heartbeat every 5 s; a collector is offline after 30 s of silence. Buffer and processing figures are as reported in each collector's last heartbeat."
        actions={<RefreshButton onClick={fleet.refresh} loading={fleet.loading} />} />
      <Reveal className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Registered" value={s?.total} Icon={Server} onClick={() => setStatus('all')} />
        <MetricCard label="Online (healthy)" value={s?.healthy} tone="ok" Icon={CheckCircle2} onClick={() => setStatus('online')} />
        <MetricCard label="Degraded" value={s?.degraded} tone={s?.degraded ? 'warn' : undefined} Icon={AlertTriangle} onClick={() => setStatus('degraded')}
          hint="late heartbeat, retrying/rejected payloads, config drift or high agent CPU" />
        <MetricCard label="Offline" value={s?.offline} tone={s?.offline ? 'crit' : 'ok'} Icon={WifiOff} pulse={s?.offline > 0} onClick={() => setStatus('offline')} />
      </Reveal>

      <Card title="Collectors" subtitle={`${rows.length} shown`}
        actions={<>
          <SearchInput value={q} onChange={setQ} placeholder="Search id, region, version…" label="Search collectors" />
          <Select value={status} onChange={setStatus} label="Status" options={[
            { value: 'all', label: 'All' }, { value: 'online', label: 'Online' }, { value: 'degraded', label: 'Degraded' },
            { value: 'offline', label: 'Offline' }, { value: 'registered', label: 'Registered' }]} />
          <Segmented label="View" value={view} onChange={setView} options={[{ value: 'cards', label: <LayoutGrid size={13} aria-label="Cards" /> }, { value: 'table', label: <List size={13} aria-label="Table" /> }]} />
        </>}>
        {fleet.error && !fleet.data ? <ErrorState error={fleet.error} onRetry={fleet.refresh} />
          : !fleet.data ? <LoadingSkeleton rows={3} h={70} />
            : !rows.length ? <EmptyState title="No collectors match">Adjust the search or status filter.</EmptyState>
              : view === 'cards' ? (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{rows.map((c) => <CollectorStatusCard key={c.id} c={c} onClick={() => open(c.id)} />)}</div>
              ) : (
                <DataTable rows={rows} rowKey={(r) => r.id} onRowClick={(r) => open(r.id)} selectedKey={selected}
                  columns={[
                    { key: 'id', header: 'Collector', render: (r) => <span className="mono text-xs">{r.id}</span> },
                    { key: 'fleet_status', header: 'Status', render: (r) => <StatusIndicator state={STATE[r.fleet_status]} label={r.fleet_status} /> },
                    { key: 'tenant_id', header: 'Tenant', render: (r) => <span className="text-xs">{r.tenant_name || r.tenant_id}</span> },
                    { key: 'region', header: 'Region' },
                    { key: 'heartbeat_age_s', header: 'Last heartbeat', render: (r) => <span className={r.heartbeat_age_s > 15 ? 'text-warn' : ''}>{ago(r.heartbeat_age_s)}</span> },
                    { key: 'version', header: 'Version' },
                    { key: 'ingest', header: 'Ingest', sortValue: (r) => r.ingestion?.events_per_min, render: (r) => `${r.ingestion?.events_per_min ?? '—'}/min · ${r.ingestion?.status}` },
                    { key: 'agent', header: 'Agent CPU / mem', sortValue: (r) => r.agent_cpu_pct, render: (r) => `${r.agent_cpu_pct?.toFixed(1) ?? '—'}% / ${r.agent_mem_mb?.toFixed(0) ?? '—'} MB` },
                    { key: 'retry', header: 'Buffer (pending/retrying)', sortValue: (r) => r.queue?.retrying, render: (r) => <span className={r.queue?.retrying ? 'text-warn' : ''}>{r.queue?.pending}/{r.queue?.retrying}</span> },
                    { key: 'sources', header: 'Sources', align: 'right' },
                  ]} />
              )}
        {fleet.data && rows.some((c) => c.fleet_status === 'offline') && <p className="mt-3 text-xs text-warn">Offline collectors show their LAST reported buffer and processing values — treat them as stale.</p>}
      </Card>

      <Drawer open={!!selected} onClose={() => open(null)} width={720} title={selected ? `Collector ${selected}` : ''}
        subtitle="Facts reported by the collector or measured by the SaaS — nothing inferred"
        actions={selected && <a className="btn" href={`${LOCAL_CONSOLE_URL}/${selected}`} target="_blank" rel="noreferrer"><ExternalLink size={14} />Local Console (Tier 1)</a>}>
        {selected && <CollectorDetail key={selected} id={selected} row={fleet.data?.collectors?.find((c) => c.id === selected)} />}
      </Drawer>
    </div>
  )
}

function CollectorDetail({ id, row }) {
  const [tab, setTab] = useState('overview')
  const detail = usePoll((signal) => api(`/collectors/${id}`, { signal, expect: 'object' }), 5000, [id])
  if (detail.error && !detail.data) return <ErrorState error={detail.error} onRetry={detail.refresh} />
  if (!detail.data || !row) return <LoadingSkeleton rows={6} h={22} />
  const d = detail.data
  const stale = row.fleet_status === 'offline'
  const lp = row.local_processing || {}
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusIndicator state={STATE[row.fleet_status]} label={row.fleet_status} pulse />
        <HealthBadge state={row.health_state} />
        <span className="text-xs text-muted">{row.health_reasons?.join(' · ')}</span>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'overview', label: 'Overview' }, { value: 'heartbeats', label: 'Heartbeat timeline' },
        { value: 'history', label: 'Health history', count: d.transitions?.length }, { value: 'events', label: 'Recent events' }]} />
      {tab === 'overview' && (
        <div className="space-y-4">
          <section><div className="label mb-2">Identity</div>
            <KV cols={3} items={[['Collector id', <span className="mono" key="i">{d.id}</span>], ['Tenant', row.tenant_name ? `${row.tenant_name} (${d.tenant_id})` : d.tenant_id],
              ['Region', row.region], ['Version', d.version], ['Registered', fmtDateTime(d.registered_at)],
              ['Configuration', <span key="c"><span className={d.config_status === 'in_sync' ? 'text-good' : 'text-warn'}>{d.config_status}</span> <span className="mono text-xs text-muted">{d.config_hash}</span></span>]]} /></section>
          <section><div className="label mb-2">Connection</div>
            <KV cols={3} items={[['Transport', 'HTTPS (TLS, CA-verified) + collector token'], ['Last heartbeat', `${fmtDateTime(d.last_heartbeat_at)} (${ago(d.heartbeat_age_s)})`],
              ['Heartbeats received', fmtNum(d.heartbeats)], ['Last data received', fmtDateTime(row.ingestion?.last_event_received_at)],
              ['Ingestion', `${row.ingestion?.status} · ${row.ingestion?.events_last_5m} events in 5 min`], ['Status reason', d.status_reason]]} /></section>
          <section className={stale ? 'opacity-70' : ''}>
            <div className="label mb-2">Local buffer {stale ? <span className="text-warn">· STALE — as of last heartbeat</span> : '· as of last heartbeat'}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[['Pending', d.queue.pending], ['Retrying', d.queue.retrying, 'warn'], ['Delivered', d.queue.delivered], ['Rejected', d.queue.failed, 'crit']].map(([k, v, tone]) => (
                <div key={k} className="rounded-lg border border-line p-2"><div className="label">{k}</div><div className={`text-lg font-semibold tabular-nums ${v && tone ? `text-${tone}` : ''}`}>{fmtNum(v)}</div></div>))}
            </div>
            <p className="mt-1 text-xs text-muted">Last successful send reported: {fmtDateTime(lp.last_successful_send_at)} · agent {d.agent_cpu_pct?.toFixed(1)}% CPU / {d.agent_mem_mb?.toFixed(0)} MB · uptime {fmtNum(d.uptime_s)} s</p>
          </section>
          <section><div className="label mb-2">Local processing counters (cumulative, reported)</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {[['Raw records processed', lp.raw_records_total], ['Fields dropped', lp.fields_dropped_total], ['Secrets removed', lp.secrets_redacted_total],
                ['Identifiers pseudonymised', lp.identifiers_pseudonymised_total], ['Payloads approved', lp.payloads_approved_total], ['Policy-blocked', d.policy_blocked]].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line p-2"><div className="label">{k}</div><div className="font-semibold tabular-nums">{fmtNum(v)}</div></div>))}
            </div>
          </section>
          <section><div className="label mb-2">Sources behind this collector</div>
            {!d.sources?.length ? <EmptyState title="No sources yet" /> : d.sources.map((s) => <SourceHealthRow key={s.source_id} s={s} />)}</section>
        </div>
      )}
      {tab === 'heartbeats' && <HeartbeatTimeline id={id} />}
      {tab === 'history' && (
        <DataTable rows={d.transitions} rowKey={(r) => r.at + r.new_state} empty="No status transitions yet" dense
          columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
            { key: 'change', header: 'Change', sortable: false, render: (r) => <span className="whitespace-nowrap">{r.prev_state ? <HealthBadge state={r.prev_state} /> : '—'} → <HealthBadge state={r.new_state} /></span> },
            { key: 'reason', header: 'Reason', render: (r) => <span className="text-muted">{r.reason}</span> }]} />
      )}
      {tab === 'events' && <CollectorEvents id={id} />}
    </div>
  )
}

function HeartbeatTimeline({ id }) {
  const [minutes, setMinutes] = useState(30)
  const [theme] = useTheme()
  const c = CHART[theme] || CHART.dark
  const hb = usePoll((signal) => api(`/collectors/${id}/heartbeats?minutes=${minutes}`, { signal, expect: 'object' }), 5000, [id, minutes])
  const data = hb.data?.heartbeats || []
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">Expected every {hb.data?.expected_interval_s ?? 5} s · offline after {hb.data?.offline_after_s ?? 30} s · {data.length} heartbeats</p>
        <Segmented label="Window" value={minutes} onChange={setMinutes} options={[{ value: 15, label: '15m' }, { value: 30, label: '30m' }, { value: 60, label: '1h' }, { value: 120, label: '2h' }]} />
      </div>
      {hb.error && !hb.data ? <ErrorState error={hb.error} /> : !hb.data ? <div className="skeleton" style={{ height: 180 }} />
        : !data.length ? <EmptyState title="No heartbeats in this window" Icon={Activity} /> : (
          <div className="grid gap-3">
            {[['gap_s', 'Interval between heartbeats (s)'], ['transport_ms', 'Heartbeat transport latency (ms)'], ['queue_retrying', 'Payloads retrying (reported)']].map(([key, label], i) => (
              <div key={key} className="rounded-lg border border-line p-2">
                <div className="label mb-1">{label}</div>
                <div style={{ height: 110 }} role="img" aria-label={label}>
                  <ResponsiveContainer>
                    <LineChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                      <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
                      <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} tick={{ fill: c.axis, fontSize: 10 }} stroke={c.grid}
                        tickFormatter={(t) => new Date(t).toISOString().slice(11, 16)} minTickGap={40} />
                      <YAxis tick={{ fill: c.axis, fontSize: 10 }} stroke={c.grid} width={44} allowDecimals={key !== 'queue_retrying'} />
                      <Tooltip contentStyle={{ background: c.tipBg, border: `1px solid ${c.tipBorder}`, borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: c.axis }} itemStyle={{ color: c.tipText }} labelFormatter={(t) => new Date(t).toISOString().slice(11, 19) + 'Z'} />
                      <Line dataKey={key} name={label} stroke={c.series[i % c.series.length]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            ))}
          </div>
        )}
      <div>
        <div className="label mb-1">Gaps &gt; 15 s</div>
        {!hb.data?.gaps?.length ? <p className="text-xs text-muted">No heartbeat gaps in this window.</p> : (
          <ul className="space-y-1 text-xs">{hb.data.gaps.map((g) => <li key={g.from} className="flex items-center gap-2 text-warn"><AlertTriangle size={12} />{fmtTime(g.from)} → {fmtTime(g.to)} · silent for {g.seconds}s</li>)}</ul>)}
      </div>
    </div>
  )
}

function CollectorEvents({ id }) {
  const ev = usePoll((signal) => api(`/collectors/${id}/events`, { signal, expect: 'object' }), 5000, [id])
  if (ev.error && !ev.data) return <ErrorState error={ev.error} />
  if (!ev.data) return <LoadingSkeleton rows={4} />
  return (
    <div className="space-y-4">
      <section><div className="label mb-2">Data transmissions (SaaS ingest audit)</div>
        <DataTable rows={ev.data.transmissions} rowKey={(r) => r.at} dense empty="No transmissions yet" maxHeight={260}
          columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtTime(r.at)}</span> },
            { key: 'outcome', header: 'Outcome', render: (r) => <span className={r.outcome === 'ok' ? 'text-good' : 'text-crit'}>{r.outcome}</span> },
            { key: 'accepted', header: 'Accepted', align: 'right' }, { key: 'duplicates', header: 'Duplicates', align: 'right' },
            { key: 'rejected', header: 'Rejected', align: 'right' }, { key: 'bytes', header: 'Bytes', align: 'right' }]} /></section>
      <section><div className="label mb-2">Audit events</div>
        <DataTable rows={ev.data.audit} rowKey={(r) => r.at + r.action} dense empty="No audit events" maxHeight={260}
          columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
            { key: 'action', header: 'Action', render: (r) => <span className="chip">{r.action}</span> }, { key: 'actor', header: 'Actor' },
            { key: 'detail', header: 'Detail', sortable: false, render: (r) => <span className="text-muted">{Object.keys(r.detail || {}).length ? JSON.stringify(r.detail).slice(0, 80) : ''}</span> }]} /></section>
    </div>
  )
}
