import { Link } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import { KeyRound, Lock, ShieldAlert, Siren, UserX } from 'lucide-react'
import { api } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import {
  EmptyState, ErrorState, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, TimeRangeSelector, useTimeRange,
} from '../components/ds'
import DataTable from '../components/DataTable'
import ChartCard from '../components/ChartCard'
import { AnomalyCard, IncidentCard } from '../components/domain'
import { SeverityBadge, fmtDateTime } from '../components/ui'

export default function Security() {
  const { minutes } = useTimeRange()
  const ov = usePoll((signal) => api(`/security/overview?minutes=${Math.max(5, minutes)}`, { signal, expect: 'object' }), 5000, [minutes])
  const sums = usePoll((signal) => api('/security/summaries?limit=50', { signal, expect: 'array' }), 5000)
  const d = ov.data

  return (
    <div>
      <PageHeader title="Security Centre" description="Authentication patterns summarised locally by each collector. SaaS receives counts only — never usernames, IP addresses, passwords or raw auth records."
        actions={<><TimeRangeSelector /><RefreshButton onClick={() => { ov.refresh(); sums.refresh() }} loading={ov.loading && !!ov.data} /></>} />

      <div className="mb-4 flex items-start gap-3 rounded-lg border border-accent/30 bg-accent/5 p-3 text-xs" role="note">
        <Lock size={15} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        <div><b className="text-accent">Privacy by design: </b>{d?.privacy_note || 'Usernames, IPs and raw auth records are summarised locally; only counts reach SaaS.'} Secret values removed by the collector are never stored or shown here.</div>
      </div>

      <ErrorState error={ov.error && !d ? ov.error : null} onRetry={ov.refresh} />
      <Reveal className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard label={`Failed logins (${minutes}m)`} value={d?.failed_logins_total} Icon={UserX} tone={d?.failed_logins_total > 20 ? 'warn' : undefined} hint="sum of per-window counts" />
        <MetricCard label="Burst summaries" value={d?.burst_summaries} Icon={KeyRound} tone={d?.burst_summaries ? 'warn' : 'ok'} hint="≥ 6 failures in one 3 s window" />
        <MetricCard label="Active security incidents" value={d?.active_security_incidents} Icon={Siren} tone={d?.active_security_incidents ? 'crit' : 'ok'}
          pulse={d?.active_security_incidents > 0} to="/incidents" />
        <MetricCard label="Sources with failures" value={d ? d.by_source.filter((s) => s.failed_logins > 0).length : null} Icon={ShieldAlert} hint={`of ${d?.by_source.length ?? 0} reporting`} />
      </Reveal>

      <div className="mb-4 grid gap-4 xl:grid-cols-[2fr_1fr]">
        <ChartCard metric="auth_failures" title="Failed login attempts per window" />
        <section className="card p-4" aria-label="Failed logins by source">
          <h3 className="text-sm font-semibold">By source</h3>
          <p className="mb-2 text-xs text-muted">Last {minutes} min · pseudonymous source ids</p>
          <DataTable rows={d?.by_source} loading={ov.loading} rowKey={(r) => r.source_id} dense empty="No auth data in range"
            initialSort={{ key: 'failed_logins', dir: 'desc' }}
            columns={[
              { key: 'source_id', header: 'Source', render: (r) => <Link className="mono hover:underline" to={`/infrastructure/${r.source_id}`}>{r.source_id}</Link> },
              { key: 'role', header: 'Role' },
              { key: 'failed_logins', header: 'Failed', align: 'right', render: (r) => <span className={r.failed_logins ? 'font-semibold text-warn' : ''}>{r.failed_logins}</span> },
              { key: 'max_per_window', header: 'Peak / window', align: 'right' },
            ]} />
        </section>
      </div>

      <section className="card mb-4 p-4" aria-label="Security event summaries">
        <h3 className="text-sm font-semibold">Security event timeline</h3>
        <p className="mb-2 text-xs text-muted">Burst summaries produced by the local collector (event category, severity, pseudonymous source, time, counts).</p>
        <DataTable rows={sums.data} loading={sums.loading} error={sums.error} onRetry={sums.refresh} rowKey={(r) => r.event_id} maxHeight={380} dense
          empty="No suspicious authentication bursts" emptyHint="Trigger “Repeated failed logins” in the Local Console to generate one."
          columns={[
            { key: 'event_time', header: 'Time (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.event_time)}</span> },
            { key: 'source_id', header: 'Source', render: (r) => <span className="mono">{r.source_id}</span> },
            { key: 'context_code', header: 'Category', render: (r) => <span className="chip">{r.context_code || r.category}</span> },
            { key: 'severity', header: 'Severity', render: (r) => <SeverityBadge severity={r.severity} /> },
            { key: 'failed_logins', header: 'Failed logins', align: 'right' },
            { key: 'distinct_accounts', header: 'Distinct accounts', align: 'right' },
            { key: 'distinct_sources', header: 'Distinct origins', align: 'right' },
          ]} />
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="card p-4" aria-label="Security anomalies">
          <h3 className="mb-2 text-sm font-semibold">Suspicious authentication patterns</h3>
          {ov.loading && !d ? <LoadingSkeleton rows={2} h={60} /> : !d?.anomalies.length ? (
            <EmptyState title="No anomalous failure rates" Icon={ShieldAlert}>Failed-login counts are compared with each source's rolling baseline.</EmptyState>
          ) : <AnimatePresence initial={false}>{d.anomalies.slice(0, 10).map((a) => <AnomalyCard key={a.id} anomaly={a} />)}</AnimatePresence>}
        </section>
        <section className="card p-4" aria-label="Security incident history">
          <h3 className="mb-2 text-sm font-semibold">Security incident history</h3>
          {ov.loading && !d ? <LoadingSkeleton rows={2} h={60} /> : !d?.incidents.length ? (
            <EmptyState title="No security incidents" Icon={Siren}>Incidents from rule R-SEC-01 (repeated authentication failures) appear here.</EmptyState>
          ) : <AnimatePresence initial={false}>{d.incidents.map((i) => <IncidentCard key={i.id} incident={i} to={`/incidents?incident=${i.id}`} />)}</AnimatePresence>}
        </section>
      </div>
    </div>
  )
}
