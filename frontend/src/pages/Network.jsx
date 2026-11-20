import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Activity, Gauge, Network as NetIcon, Siren } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import ChartCard from '../components/ChartCard'
import DataTable from '../components/DataTable'
import {
  EmptyState, ErrorState, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, StatusIndicator,
  TimeRangeSelector, useTimeRange,
} from '../components/ds'
import { AnomalyCard, IncidentCard } from '../components/domain'
import { HealthBadge, ago, fmtVal } from '../components/ui'

const CONN = { healthy: ['ok', 'Healthy'], degraded: ['warn', 'Degraded'], stale: ['crit', 'Stale / no data'] }
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d))

export default function Network() {
  const nav = useNavigate()
  const { minutes } = useTimeRange()
  const d = usePoll((signal) => api(`/network/overview?minutes=${minutes}`, { signal, expect: 'object' }), 4000, [minutes])
  const data = d.data

  const kpi = useMemo(() => {
    if (!data) return null
    const live = data.sources.filter((s) => !s.stale)
    const lat = live.map((s) => s.latest.latency_ms).filter((v) => v != null)
    const loss = data.sources.map((s) => s.packet_loss_pct.max).filter((v) => v != null)
    return {
      degraded: data.sources.filter((s) => s.connectivity !== 'healthy').length,
      avgLatency: lat.length ? lat.reduce((a, b) => a + b, 0) / lat.length : null,
      maxLoss: loss.length ? Math.max(...loss) : null,
      activeIncidents: data.incidents.filter((i) => i.status !== 'resolved').length,
    }
  }, [data])
  const th = data?.thresholds || {}

  return (
    <div className="space-y-4">
      <PageHeader title="Network Monitoring"
        description="Latency, packet loss and bandwidth as measured by the collectors for each source. Degradation is judged against your tenant thresholds."
        actions={<><TimeRangeSelector /><RefreshButton onClick={d.refresh} loading={d.loading} /></>} />
      {d.error && <ErrorState error={d.error} onRetry={d.refresh} />}
      {!data ? <LoadingSkeleton rows={6} h={50} /> : (
        <>
          <Reveal className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <MetricCard label="Sources degraded" value={kpi.degraded} Icon={NetIcon} tone={kpi.degraded ? 'warn' : 'ok'}
              hint={`of ${data.sources.length} source(s)`} srStatus={kpi.degraded ? 'network degradation present' : 'network healthy'} />
            <MetricCard label="Average latency (now)" value={kpi.avgLatency} decimals={0} suffix=" ms" Icon={Gauge}
              tone={kpi.avgLatency != null && th.latency_ms && kpi.avgLatency >= th.latency_ms[0] ? 'warn' : undefined}
              hint={th.latency_ms ? `Excellent below ${th.latency_ms[0]} ms` : undefined} />
            <MetricCard label={`Max packet loss (${minutes}m)`} value={kpi.maxLoss} decimals={2} suffix="%" Icon={Activity}
              tone={kpi.maxLoss != null && th.packet_loss_pct && kpi.maxLoss >= th.packet_loss_pct[1] ? 'crit' : undefined}
              hint={th.packet_loss_pct ? `Excellent below ${th.packet_loss_pct[0]}%` : undefined} />
            <MetricCard label="Network incidents active" value={kpi.activeIncidents} Icon={Siren} to="/incidents"
              tone={kpi.activeIncidents ? 'crit' : 'ok'} pulse={kpi.activeIncidents > 0} />
          </Reveal>

          <section className="card p-4">
            <h2 className="text-sm font-semibold">Connectivity by source</h2>
            <p className="mb-3 mt-0.5 text-xs text-muted">Statistics over the last {minutes} min. Click a row for the source detail.</p>
            <DataTable rows={data.sources} rowKey={(s) => s.source_id} onRowClick={(s) => nav(`/infrastructure/${s.source_id}`)}
              empty="No sources reporting"
              columns={[
                { key: 'source_id', header: 'Source', render: (s) => <span className="mono text-xs">{s.source_id}</span> },
                { key: 'role', header: 'Role' },
                { key: 'connectivity', header: 'Connectivity', sortValue: (s) => ['healthy', 'degraded', 'stale'].indexOf(s.connectivity),
                  render: (s) => { const [st, label] = CONN[s.connectivity] || ['idle', s.connectivity]; return <StatusIndicator state={st} label={label} pulse={s.connectivity !== 'healthy'} /> } },
                { key: 'lat', header: 'Latency now', align: 'right', sortValue: (s) => s.latest.latency_ms, render: (s) => fmtVal(s.latest.latency_ms, 'ms') },
                { key: 'lat_stats', header: 'Latency avg / p95 / max', sortValue: (s) => s.latency_ms.p95,
                  render: (s) => <span className="tabular-nums text-xs">{num(s.latency_ms.avg, 0)} / {num(s.latency_ms.p95, 0)} / {num(s.latency_ms.max, 0)} ms</span> },
                { key: 'loss', header: 'Loss now', align: 'right', sortValue: (s) => s.latest.packet_loss_pct,
                  render: (s) => (s.latest.packet_loss_pct != null ? `${s.latest.packet_loss_pct.toFixed(2)}%` : '—') },
                { key: 'loss_stats', header: 'Loss avg / max', sortValue: (s) => s.packet_loss_pct.max,
                  render: (s) => <span className="tabular-nums text-xs">{num(s.packet_loss_pct.avg, 2)} / {num(s.packet_loss_pct.max, 2)} %</span> },
                { key: 'bw', header: 'Bandwidth', align: 'right', sortValue: (s) => s.latest.net_mbps, render: (s) => fmtVal(s.latest.net_mbps, 'Mbps') },
                { key: 'health', header: 'Health', sortable: false, render: (s) => <HealthBadge state={s.health_state} stale={s.stale} /> },
                { key: 'age', header: 'Data age', sortValue: (s) => s.data_age_s, render: (s) => <span className={s.stale ? 'text-warn' : 'text-muted'}>{ago(s.data_age_s)}</span> },
                { key: 'deg', header: 'Degradation', sortable: false, className: 'max-w-xs text-xs',
                  render: (s) => (s.degradation.length ? <span className="text-warn">{s.degradation.join(' · ')}</span> : <span className="text-muted">none</span>) },
              ]} />
          </section>

          <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
            <ChartCard metric="latency_ms" title="Network latency" thresholds={th.latency_ms ? [th.latency_ms[1], th.latency_ms[2]] : undefined} />
            <ChartCard metric="packet_loss_pct" title="Packet loss" thresholds={th.packet_loss_pct ? [th.packet_loss_pct[1], th.packet_loss_pct[2]] : undefined} />
            <ChartCard metric="net_mbps" title="Bandwidth" showAnomalies={false} />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <section className="card p-4">
              <h2 className="text-sm font-semibold">Network anomaly history</h2>
              <p className="mb-3 mt-0.5 text-xs text-muted">Latency and packet-loss anomalies against each source's rolling baseline</p>
              {!data.anomalies.length ? <EmptyState title="No network anomalies detected" /> : (
                <div className="max-h-[480px] overflow-y-auto pr-1">{data.anomalies.map((a) => <AnomalyCard key={a.id} anomaly={a} />)}</div>
              )}
            </section>
            <section className="card p-4">
              <h2 className="text-sm font-semibold">Network-related incidents</h2>
              <p className="mb-3 mt-0.5 text-xs text-muted">Correlation rules R-NET-01 / 02 / 03</p>
              {!data.incidents.length ? <EmptyState title="No network incidents" /> : (
                <div className="max-h-[480px] overflow-y-auto pr-1">{data.incidents.map((i) => <IncidentCard key={i.id} incident={i} to={`/incidents?incident=${i.id}`} />)}</div>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  )
}
