// Tier-1 Local Console panels. All data comes from the collector process on 127.0.0.1 (never via SaaS).
import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowRight, Ban, CheckCircle2, Clock, Cpu, Database, FileJson, Fingerprint, HeartPulse, Inbox, Play, RefreshCw,
  Repeat, Send, ShieldAlert, ShieldCheck, Wifi, WifiOff, XCircle,
} from 'lucide-react'
import { local } from './api'
import { secondsSince, useLocalPoll } from './useLocalPoll'
import { RedactedText, SecretText } from './highlight'
import DataTable from '../components/DataTable'
import { Drawer, EmptyState, ErrorState, KV, LoadingSkeleton, MetricCard, Segmented, Select, StatusIndicator } from '../components/ds'
import { useToast } from '../components/Toast'
import { Card, HealthBadge, JsonView, Spinner, ago, fmtDateTime, fmtTime, fmtVal } from '../components/ui'

export const EGRESS_STATUS = {
  delivered: { label: 'delivered', cls: 'text-good' },
  delivered_duplicate_ack: { label: 'delivered (dup ack)', cls: 'text-good' },
  queued: { label: 'queued', cls: 'text-muted' },
  sending: { label: 'sending', cls: 'text-muted' },
  retrying: { label: 'retrying', cls: 'text-warn' },
  blocked: { label: 'blocked by policy', cls: 'text-local' },
  rejected_by_saas: { label: 'rejected by SaaS', cls: 'text-crit' },
  failed: { label: 'failed', cls: 'text-crit' },
}
export const StatusText = ({ s }) => <span className={`font-medium ${(EGRESS_STATUS[s] || {}).cls || ''}`}>{(EGRESS_STATUS[s] || { label: s }).label}</span>

export function CheckChips({ checks }) {
  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(checks || {}).map(([k, v]) => (
        <span key={k} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${v === 'pass' ? 'bg-good/15 text-good' : 'bg-crit/15 text-crit'}`}>
          {v === 'pass' ? <CheckCircle2 size={11} aria-hidden /> : <XCircle size={11} aria-hidden />}{k.replace('_', ' ')}: {v}
        </span>))}
    </div>
  )
}

const FLOW = ['Collect', 'Filter', 'Redact', 'Aggregate', 'Analyse', 'Policy', 'Egress audit', 'Queue', 'TLS send']

// ================================================================== OVERVIEW
export function Overview({ s, collectorId }) {
  const agg = useLocalPoll((signal) => local(collectorId, '/aggregates', { signal }), 3000, [collectorId])
  const c = s.connectivity || {}
  const connected = c.state === 'connected' && !s.partitioned && !s.paused
  const hbAge = secondsSince(c.last_heartbeat_ok_at)
  const okAge = secondsSince(c.last_ok_at)
  const egressTotal = Object.values(s.egress || {}).reduce((a, b) => a + b, 0)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Collector process" value={s.paused ? 'Halted' : 'Running'} tone={s.paused ? 'crit' : 'ok'} Icon={Cpu}
          hint={s.paused ? 'simulated hang (scenario)' : `uptime ${s.uptime_s}s`} />
        <MetricCard label="SaaS connectivity" value={s.paused ? 'Paused' : connected ? 'Connected' : 'Unreachable'} tone={connected ? 'ok' : 'warn'}
          Icon={connected ? Wifi : WifiOff} hint={s.partitioned ? 'simulated partition' : c.last_error ? 'see Buffer & delivery' : 'TLS · CA verified'} />
        <MetricCard label="Heartbeat" value={hbAge == null ? '—' : `${Math.round(hbAge)}s ago`} tone={hbAge != null && hbAge < 12 ? 'ok' : 'warn'}
          Icon={HeartPulse} hint={`last OK ${fmtTime(c.last_heartbeat_ok_at)}`} />
        <MetricCard label="Last successful send" value={okAge == null ? '—' : `${Math.round(okAge)}s ago`} tone={okAge != null && okAge < 15 ? 'ok' : 'warn'}
          Icon={Send} hint={fmtTime(c.last_ok_at)} />
        <MetricCard label="Pending transmission" value={(s.queue?.pending || 0) + (s.queue?.retrying || 0)} tone={s.queue?.retrying ? 'warn' : 'ok'}
          Icon={Inbox} hint={`${s.queue?.retrying || 0} retrying · ${s.queue?.delivered || 0} delivered`} />
        <MetricCard label="Policy-blocked" value={s.policy_blocked || 0} tone={s.policy_blocked ? 'warn' : 'ok'} Icon={Ban} hint="never transmitted" />
      </div>

      <Card title="Local pipeline" subtitle={`Sampling every ${s.sample_interval_s}s · aggregation window ${s.window_s}s · everything left of “TLS send” stays on this machine`} tone="local">
        <div className="flex flex-wrap items-center gap-1 text-xs" aria-label="Pipeline stages">
          {FLOW.map((step, i) => (
            <span key={step} className="flex items-center gap-1">
              <span className={`rounded-md px-2 py-1 font-medium ${i < FLOW.length - 1 ? 'bg-local/15 text-local' : 'bg-accent/15 text-accent'}`}>{step}</span>
              {i < FLOW.length - 1 && <svg width="18" height="8" aria-hidden><line x1="0" y1="4" x2="18" y2="4" strokeWidth="2" className="flow" /></svg>}
            </span>
          ))}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
          {[['Raw records', s.stats?.raw_records], ['Fields dropped', s.stats?.fields_dropped], ['Secrets redacted', s.stats?.secrets_redacted],
            ['Identifiers pseudonymised', s.stats?.identifiers_pseudonymised], ['Payloads audited', egressTotal]].map(([k, v]) => (
            <div key={k} className="rounded-lg border border-line p-3"><div className="label">{k}</div>
              <div className={`mt-1 text-lg font-semibold tabular-nums ${k === 'Secrets redacted' && v ? 'text-local' : ''}`}>{v ?? 0}</div></div>))}
        </div>
      </Card>

      <Card title="Local infrastructure metrics" subtitle="Last aggregation window per host, with the collector's local health evaluation (EWMA baseline)" tone="local">
        {agg.error && !agg.data ? <ErrorState error={agg.error} /> : !agg.data ? <LoadingSkeleton rows={3} h={60} /> : !agg.data.length ? <EmptyState title="Waiting for the first window" /> : (
          <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
            {agg.data.map((a) => (
              <motion.div key={a.source_id} layout className="rounded-lg border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0"><div className="truncate text-sm font-medium text-local" title="Hostname — local only">{a.hostname_local_only}</div>
                    <div className="mono text-[11px] text-muted">→ {a.source_id} · {a.raw_records} raw records</div></div>
                  <HealthBadge state={a.local_health} />
                </div>
                <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
                  {[['CPU', fmtVal(a.metrics.cpu_pct, '%')], ['Mem', fmtVal(a.metrics.mem_pct, '%')], ['Disk', fmtVal(a.metrics.disk_pct, '%')],
                    ['Latency', fmtVal(a.metrics.latency_ms, 'ms')], ['Loss', a.metrics.packet_loss_pct != null ? `${a.metrics.packet_loss_pct.toFixed(2)}%` : '—'],
                    ['App', fmtVal(a.metrics.app_response_ms, 'ms')], ['Net', fmtVal(a.metrics.net_mbps, 'Mbps')], ['Auth fail', a.metrics.auth_failures ?? '—']].map(([k, v]) => (
                    <div key={k}><div className="label">{k}</div><div className="tabular-nums">{v}</div></div>))}
                </div>
                <div className="mt-2 text-[11px] text-muted">{a.local_reason}{Object.keys(a.local_z_scores || {}).length > 0 && <> · local z {Object.entries(a.local_z_scores).map(([k, v]) => `${k}=${v}`).join(', ')}</>}</div>
              </motion.div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Collector identity" tone="local">
          <KV cols={2} items={[['Collector', <span key="c" className="mono">{s.collector_id}</span>], ['Tenant', s.tenant_id], ['Version', s.version],
            ['Config hash', <span key="h" className="mono">{s.config_hash}</span>], ['SaaS endpoint', <span key="u" className="mono break-all">{s.saas_url}</span>],
            ['Transport', s.transport], ['Registered', s.registered ? 'yes — per-collector token stored locally' : 'not yet'],
            ['Consecutive send failures', c.consecutive_failures ?? 0]]} />
        </Card>
        <Card title="Host mapping (local only)" subtitle="Hostnames and the pseudonymisation key never leave this collector; SaaS only sees source_id" tone="local">
          <DataTable dense rows={s.hosts} rowKey={(h) => h.source_id} columns={[
            { key: 'hostname_local_only', header: 'Hostname (local)', render: (h) => <span className="mono text-local">{h.hostname_local_only}</span> },
            { key: 'role', header: 'Role' },
            { key: 'source_id', header: 'source_id (sent)', render: (h) => <span className="mono">{h.source_id}</span> },
          ]} />
        </Card>
      </div>
    </div>
  )
}

// ================================================================== RAW & REDACTION
export function RawRedaction({ collectorId }) {
  const [secretsOnly, setSecretsOnly] = useState(false)
  const [stream, setStream] = useState('all')
  const [paused, setPaused] = useState(false)
  const raw = useLocalPoll((signal) => local(collectorId, `/raw?limit=60&kind=log${secretsOnly ? '&secrets_only=true' : ''}`, { signal }), 2000, [collectorId, secretsOnly], paused)
  const red = useLocalPoll((signal) => local(collectorId, `/redactions?limit=200${secretsOnly ? '&secrets_only=true' : ''}`, { signal }), 2000, [collectorId, secretsOnly], paused)
  const ex = useLocalPoll((signal) => local(collectorId, '/filter-example', { signal }), 4000, [collectorId], paused)
  const byRaw = useMemo(() => Object.fromEntries((red.data || []).map((r) => [r.raw_id, r])), [red.data])
  const rows = (raw.data || []).filter((r) => stream === 'all' || r.stream === stream).slice(0, 40)
  const ruleCounts = useMemo(() => {
    const m = {}
    for (const r of red.data || []) for (const f of r.findings || []) m[f.rule_id] = { kind: f.kind, n: (m[f.rule_id]?.n || 0) + f.matches, rule: f.rule }
    return Object.entries(m).sort((a, b) => b[1].n - a[1].n)
  }, [red.data])

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <Card title="Raw telemetry → redaction preview" tone="local"
        subtitle="Raw log lines exactly as collected (secrets in red, identifiers underlined) and the redacted form the pipeline keeps. Raw lines never leave this machine."
        actions={<>
          <Segmented label="Stream" value={stream} onChange={setStream} options={[{ value: 'all', label: 'All' }, { value: 'app', label: 'app' }, { value: 'auth', label: 'auth' }]} />
          <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={secretsOnly} onChange={(e) => setSecretsOnly(e.target.checked)} />secrets only</label>
          <button className="btn" onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? <Play size={13} /> : <Clock size={13} />}{paused ? 'Resume' : 'Pause'}</button>
        </>}>
        {raw.error && !raw.data ? <ErrorState error={raw.error} /> : !raw.data ? <LoadingSkeleton rows={6} h={34} /> : !rows.length ? (
          <EmptyState title={secretsOnly ? 'No secret-bearing lines yet' : 'No log lines yet'}>{secretsOnly && 'Trigger “Sensitive log injection” in the Scenarios tab.'}</EmptyState>
        ) : (
          <div className="max-h-[560px] space-y-2 overflow-y-auto pr-1" aria-live="off">
            <AnimatePresence initial={false}>
              {rows.map((r) => {
                const rd = byRaw[r.id]
                return (
                  <motion.div key={r.id} layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
                    className={`rounded-lg border p-2 ${r.has_secret ? 'border-crit/40 bg-crit/5' : 'border-line'}`}>
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                      <span className="mono">{fmtTime(r.ts)}</span><span className="text-local">{r.host}</span><span className="chip">{r.stream}</span>
                      {r.has_secret && <span className="inline-flex items-center gap-1 text-crit"><ShieldAlert size={11} aria-hidden />secret detected</span>}
                    </div>
                    <div className="mt-1 grid gap-1 md:grid-cols-[1fr_auto_1fr] md:items-start">
                      <div className="mono break-all text-[11.5px]"><SecretText text={r.body?.line} /></div>
                      <ArrowRight size={14} className="hidden text-muted md:block" aria-hidden />
                      <div className="mono break-all text-[11.5px]">{rd ? <RedactedText text={rd.redacted_text} /> : <span className="text-muted">(no redaction needed)</span>}</div>
                    </div>
                    {rd?.findings?.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{rd.findings.map((f) => (
                      <span key={f.rule_id} className={`rounded px-1.5 text-[10px] ${f.kind === 'secret' ? 'bg-crit/15 text-crit' : 'bg-panel-2 text-muted'}`}>{f.rule_id} ×{f.matches}</span>))}</div>}
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </div>
        )}
      </Card>
      <div className="space-y-4">
        <Card title="Sensitive information detected" subtitle="Rule hits in the recent redaction log" tone="local">
          {!red.data ? <LoadingSkeleton rows={4} /> : !ruleCounts.length ? <EmptyState title="No findings yet" /> : (
            <ul className="space-y-1.5 text-sm">
              {ruleCounts.map(([id, v]) => (
                <li key={id} className="flex items-center justify-between gap-2">
                  <span><span className="mono text-xs">{id}</span> <span className="text-xs text-muted">{v.rule}</span></span>
                  <span className={`chip ${v.kind === 'secret' ? 'text-crit' : ''}`}>{v.kind === 'secret' ? 'removed' : 'pseudonymised'} ×{v.n}</span>
                </li>))}
            </ul>)}
        </Card>
        <Card title="Field filtering (metric record)" subtitle="Raw agent record vs the allow-listed fields kept for aggregation" tone="local">
          {!ex.data?.raw ? <LoadingSkeleton rows={3} /> : (
            <>
              <div className="label mb-1">Dropped locally</div>
              <div className="mb-2 flex flex-wrap gap-1">{ex.data.dropped_fields.map((f) => <span key={f} className="mono rounded bg-local/15 px-1.5 text-[11px] text-local">{f}</span>)}</div>
              <div className="label mb-1">Kept</div>
              <JsonView data={ex.data.filtered} />
              <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted">Show raw record (local only)</summary><div className="mt-1"><JsonView data={ex.data.raw} /></div></details>
            </>)}
        </Card>
      </div>
    </div>
  )
}

// ================================================================== EGRESS & POLICY
const EG_FILTERS = [{ value: '', label: 'All' }, { value: 'delivered', label: 'Delivered' }, { value: 'retrying', label: 'Retrying' },
  { value: 'blocked', label: 'Blocked' }, { value: 'queued', label: 'Queued' }, { value: 'rejected_by_saas', label: 'Rejected' }]

export function EgressPolicy({ collectorId }) {
  const [status, setStatus] = useState('')
  const [open, setOpen] = useState(null)
  const eg = useLocalPoll((signal) => local(collectorId, `/egress?limit=60${status ? `&status=${status}` : ''}`, { signal }), 2500, [collectorId, status])
  const blocked = useLocalPoll((signal) => local(collectorId, '/egress?limit=20&status=blocked', { signal }), 4000, [collectorId])
  const pol = useLocalPoll((signal) => local(collectorId, '/policy', { signal }), 0, [collectorId])
  const c = pol.data?.contract
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card title="Approved metadata — egress audit" tone="local" subtitle="Every payload decision: the exact canonical JSON bytes, their SHA-256, the policy result and transmission status. Click a row for the full record."
          actions={<Segmented label="Status filter" value={status} onChange={setStatus} options={EG_FILTERS} />}>
          <DataTable rows={eg.data} loading={eg.loading} error={eg.error} rowKey={(r) => r.id} onRowClick={setOpen} selectedKey={open?.id} maxHeight={520} dense
            empty="No egress records for this filter"
            columns={[
              { key: 'created_at', header: 'Created', render: (r) => <span className="mono">{fmtTime(r.created_at)}</span> },
              { key: 'event_id', header: 'Event', render: (r) => <span className="mono">{r.event_id ? `${r.event_id.slice(0, 8)}…` : '—'}</span> },
              { key: 'category', header: 'Category' },
              { key: 'source_id', header: 'Source', render: (r) => <span className="mono">{r.source_id}</span> },
              { key: 'policy_allowed', header: 'Policy', render: (r) => r.policy_allowed ? <span className="text-good">allowed</span> : <span className="text-local">blocked</span> },
              { key: 'status', header: 'Status', render: (r) => <><StatusText s={r.status} />{r.attempts > 1 && <span className="text-muted"> ({r.attempts}×)</span>}</> },
              { key: 'payload_bytes', header: 'Bytes', align: 'right' },
              { key: 'sha256', header: 'SHA-256', sortable: false, render: (r) => <span className="mono text-muted">{r.sha256.slice(0, 10)}…</span> },
            ]} />
        </Card>
        <Card title="Blocked events" subtitle="Rejected by the policy engine — never transmitted" tone="local">
          {!blocked.data ? <LoadingSkeleton rows={3} /> : !blocked.data.length ? <EmptyState title="No blocked payloads" Icon={ShieldCheck}>Trigger “Sensitive log injection” to see the simulated leaky plugin being blocked.</EmptyState> : (
            <div className="max-h-[520px] space-y-2 overflow-y-auto">
              <AnimatePresence initial={false}>
                {blocked.data.map((b) => (
                  <motion.button key={b.id} layout initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} onClick={() => setOpen(b)}
                    className="card-hover block w-full rounded-lg border border-local/40 bg-local/5 p-3 text-left">
                    <div className="flex items-center gap-2 text-sm font-medium text-local"><Ban size={14} aria-hidden />{b.event_id?.slice(0, 8)}… · {fmtTime(b.created_at)}</div>
                    <ul className="mt-1 list-disc pl-5 text-[11px] text-muted">{b.policy_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                  </motion.button>))}
              </AnimatePresence>
            </div>)}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Allow-list contract" subtitle="Shared by both tiers; unknown fields are rejected (extra=forbid), free text is not permitted" tone="local">
          {pol.error ? <ErrorState error={pol.error} /> : !c ? <LoadingSkeleton rows={4} /> : (
            <div className="space-y-3">
              {[['Payload fields', c.payload_fields], ['Metric fields', c.metric_fields], ['Processing-evidence fields', c.processing_fields || []],
                ['Context codes', c.context_codes], ['Raw metric fields kept by the filter', pol.data.raw_metric_filter_allow_list]].map(([k, v]) => (
                <div key={k}><div className="label mb-1">{k}</div>
                  <div className="flex flex-wrap gap-1">{v.map((f) => <span key={f} className="mono rounded bg-good/10 px-1.5 py-0.5 text-[11px] text-good">{f}</span>)}</div></div>))}
              <div className="text-xs text-muted">{c.free_text} · unknown fields: {c.unknown_fields}</div>
              <div><div className="label mb-1">Checks before any byte leaves</div>
                <ol className="list-decimal space-y-0.5 pl-5 text-xs">{pol.data.policy_checks.map((p) => <li key={p}>{p}</li>)}</ol></div>
            </div>)}
        </Card>
        <Card title="Redaction rules" tone="local">
          {!pol.data ? <LoadingSkeleton rows={6} /> : (
            <DataTable dense rows={pol.data.redaction_rules} rowKey={(r) => r.id} columns={[
              { key: 'id', header: 'Rule', render: (r) => <span className="mono">{r.id}</span> },
              { key: 'name', header: 'Detects' },
              { key: 'kind', header: 'Kind', render: (r) => <span className={r.kind === 'secret' ? 'text-crit' : 'text-warn'}>{r.kind}</span> },
              { key: 'action', header: 'Action', sortable: false },
            ]} />)}
        </Card>
      </div>
      <EgressDrawer record={open} onClose={() => setOpen(null)} collectorId={collectorId} />
    </div>
  )
}

export function EgressDrawer({ record: r, onClose, collectorId }) {
  const [v, setV] = useState(null)
  const [busy, setBusy] = useState(false)
  const verify = async () => {
    setBusy(true)
    try { setV(await local(collectorId, `/verify/${r.event_id}`, { method: 'POST' })) } catch (e) { setV({ error: e.message }) } finally { setBusy(false) }
  }
  return (
    <Drawer open={!!r} onClose={() => { setV(null); onClose() }} title={r ? `Egress record ${r.event_id ? r.event_id.slice(0, 8) + '…' : `#${r.id}`}` : ''}
      subtitle={r ? `${r.category} · ${r.source_id} · window ${r.window_id}` : ''}
      actions={r?.policy_allowed && r?.event_id && <button className="btn" onClick={verify} disabled={busy}>{busy ? <Spinner /> : <Fingerprint size={14} />}Verify with SaaS</button>}>
      {r && (
        <div className="space-y-4">
          <KV items={[['Event id', <span key="e" className="mono break-all">{r.event_id || '—'}</span>], ['Status', <StatusText key="s" s={r.status} />],
            ['Created', fmtDateTime(r.created_at)], ['Transmitted', fmtDateTime(r.transmitted_at)], ['Attempts', r.attempts], ['HTTP', r.http_status ?? '—'],
            ['SaaS result', r.saas_result || '—'], ['Destination', <span key="d" className="mono break-all">{r.destination}</span>]]} />
          <div><div className="label mb-1">Policy checks</div><CheckChips checks={r.policy_checks} />
            {r.policy_reasons?.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-local">{r.policy_reasons.map((x) => <li key={x}>{x}</li>)}</ul>}</div>
          {r.last_error && <div className="rounded-lg border border-warn/40 bg-warn/10 p-2 text-xs text-warn">Last error: {r.last_error}</div>}
          <div><div className="label mb-1 flex items-center gap-1"><FileJson size={12} aria-hidden />Exact payload ({r.payload_bytes} bytes)</div><JsonView data={r.payload} /></div>
          <div className="text-xs">SHA-256 <span className="mono break-all text-local">{r.sha256}</span></div>
          {v && (v.error ? <ErrorState error={new Error(v.error)} /> : (
            <div className={`rounded-lg border p-3 text-xs ${v.match ? 'border-good/40 bg-good/10' : 'border-crit/40 bg-crit/10'}`}>
              <div className={`font-semibold ${v.match ? 'text-good' : 'text-crit'}`}>{v.match ? 'SaaS copy matches byte-for-byte' : 'Hash mismatch / not stored'}</div>
              <div className="mt-1 mono break-all">SaaS sha256 {v.saas_sha256 || '—'}</div>
              <div className="mt-1">Synthetic secrets found in SaaS copy: <b>{v.synthetic_secrets_found_in_saas_copy}</b></div>
            </div>))}
        </div>
      )}
    </Drawer>
  )
}

// ================================================================== BUFFER & DELIVERY
export function BufferDelivery({ s, collectorId }) {
  const { push } = useToast()
  const [status, setStatus] = useState('')
  const [rec, setRec] = useState(null)
  const [busy, setBusy] = useState(null)
  const q = useLocalPoll((signal) => local(collectorId, `/queue?limit=60${status ? `&status=${status}` : ''}`, { signal }), 2000, [collectorId, status])
  const c = q.data?.connectivity || s.connectivity || {}
  const nextIn = q.data?.next_send_at ? Math.max(0, q.data.next_send_at - Date.now() / 1000) : 0

  const reconcile = async () => {
    setBusy('rec')
    try { const r = await local(collectorId, '/reconcile', { method: 'POST' }); setRec(r); push({ title: `Reconciliation: ${r.missing} missing`, message: `${r.present}/${r.checked} delivered ids present at SaaS`, tone: r.missing ? 'error' : 'success' }) }
    catch (e) { setRec({ error: e.message }); push({ title: 'Reconcile failed', message: e.message, tone: 'error' }) } finally { setBusy(null) }
  }
  const replay = async () => {
    setBusy('rep')
    try { const r = await local(collectorId, '/scenarios/duplicate_replay', { method: 'POST' }); const x = r.result || {}
      push({ title: 'Duplicate replay sent', message: `sent ${x.sent ?? 0} · accepted ${x.accepted ?? '—'} · duplicates ${x.duplicates ?? '—'}`, tone: x.error ? 'error' : 'info' }) }
    catch (e) { push({ title: 'Replay failed', message: e.message, tone: 'error' }) } finally { setBusy(null) }
  }
  const counts = q.data?.counts || s.queue || {}
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label="Pending" value={counts.pending || 0} Icon={Inbox} hint="approved, not yet sent" />
        <MetricCard label="Retrying" value={counts.retrying || 0} tone={counts.retrying ? 'warn' : undefined} Icon={RefreshCw} hint="buffered during outage" pulse={counts.retrying > 0} />
        <MetricCard label="Delivered" value={counts.delivered || 0} tone="ok" Icon={CheckCircle2} />
        <MetricCard label="Failed (rejected)" value={counts.failed || 0} tone={counts.failed ? 'crit' : undefined} Icon={XCircle} hint="not retried" />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card title="Local buffer (SQLite outbox — approved metadata only)" tone="local"
          subtitle="Raw logs are never buffered for transmission. Event ids are fixed at the collector so re-sends are idempotent."
          actions={<Segmented label="Queue filter" value={status} onChange={setStatus} options={[{ value: '', label: 'All' }, { value: 'pending', label: 'Pending' }, { value: 'retrying', label: 'Retrying' }, { value: 'delivered', label: 'Delivered' }, { value: 'failed', label: 'Failed' }]} />}>
          <DataTable rows={q.data?.items} loading={q.loading} error={q.error} rowKey={(r) => r.event_id} maxHeight={480} dense empty="Queue empty for this filter"
            columns={[
              { key: 'event_id', header: 'Event', render: (r) => <span className="mono">{r.event_id.slice(0, 8)}…</span> },
              { key: 'event_time', header: 'Event time', render: (r) => <span className="mono">{fmtTime(r.event_time)}</span> },
              { key: 'status', header: 'Status', render: (r) => <StatusIndicator state={{ delivered: 'ok', retrying: 'warn', failed: 'crit', pending: 'idle' }[r.status]} label={r.status} pulse={r.status === 'retrying'} /> },
              { key: 'attempts', header: 'Attempts', align: 'right' },
              { key: 'delivered_at', header: 'Delivered', render: (r) => <span className="mono">{fmtTime(r.delivered_at)}</span> },
              { key: 'saas_result', header: 'SaaS / last error', sortable: false, render: (r) => <span className="text-muted">{r.saas_result || r.last_error?.slice(0, 50) || '—'}</span> },
            ]} />
        </Card>
        <div className="space-y-4">
          <Card title="SaaS connectivity" tone="local">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium">
              {c.state === 'connected' && !s.partitioned ? <><Wifi size={16} className="text-good" />Connected</> : <><WifiOff size={16} className="text-warn" />{s.partitioned ? 'Partitioned (simulated)' : 'Unreachable — buffering locally'}</>}
            </div>
            <KV items={[['Last successful send', `${fmtTime(c.last_ok_at)} (${ago(secondsSince(c.last_ok_at))})`],
              ['Last heartbeat OK', `${fmtTime(c.last_heartbeat_ok_at)} (${ago(secondsSince(c.last_heartbeat_ok_at))})`],
              ['Consecutive failures', c.consecutive_failures ?? 0], ['Next send attempt', nextIn > 0 ? `in ${nextIn.toFixed(0)}s` : 'now']]} />
            {c.last_error && <div className="mt-2 break-words rounded-md bg-warn/10 p-2 text-xs text-warn">{c.last_error}</div>}
            <p className="mt-2 text-[11px] text-muted">Exponential backoff with jitter: 1 → 2 → 4 → 8 → 16 s (cap). Original event timestamps are preserved.</p>
          </Card>
          <Card title="Delivery verification" tone="local">
            <div className="flex flex-wrap gap-2">
              <button className="btn" onClick={reconcile} disabled={!!busy}>{busy === 'rec' ? <Spinner /> : <Database size={14} />}Reconcile with SaaS</button>
              <button className="btn" onClick={replay} disabled={!!busy}>{busy === 'rep' ? <Spinner /> : <Repeat size={14} />}Duplicate replay</button>
            </div>
            {rec && (rec.error ? <div className="mt-2"><ErrorState error={new Error(rec.error)} /></div> : (
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                {[['Checked', rec.checked], ['Present', rec.present], ['Missing', rec.missing]].map(([k, v]) => (
                  <div key={k} className="rounded-md border border-line p-2"><div className="label">{k}</div>
                    <div className={`text-lg font-semibold ${k === 'Missing' ? (v ? 'text-crit' : 'text-good') : ''}`}>{v}</div></div>))}
                <div className="col-span-3 text-muted">Duplicates stored at SaaS: <b className="text-good">{rec.duplicates_stored}</b> — {rec.note}</div>
              </div>))}
            {s.last_replay && <div className="mt-3 text-xs">Last replay {fmtTime(s.last_replay.at)}: sent {s.last_replay.sent} · accepted {s.last_replay.accepted ?? '—'} · <b className="text-good">duplicates detected {s.last_replay.duplicates ?? '—'}</b>{s.last_replay.error && <span className="text-warn"> · {s.last_replay.error}</span>}</div>}
          </Card>
        </div>
      </div>
    </div>
  )
}

// ================================================================== SCENARIOS
export function Scenarios({ s, collectorId, onChange }) {
  const { push } = useToast()
  const sc = useLocalPoll((signal) => local(collectorId, '/scenarios', { signal }), 3000, [collectorId])
  const [target, setTarget] = useState('')
  const [duration, setDuration] = useState('')
  const [busy, setBusy] = useState(null)
  if (sc.error && !sc.data) return <ErrorState error={sc.error} />
  if (!sc.data) return <LoadingSkeleton rows={4} h={70} />
  const active = Object.fromEntries((s.active_scenarios || []).map((a) => [a.name, a]))
  const trigger = async (name) => {
    setBusy(name)
    const body = {}
    if (target && sc.data.catalog[name].scope === 'host') body.target = target
    if (duration && Number(duration) > 0) body.duration_s = Number(duration)
    try {
      const r = await local(collectorId, `/scenarios/${name}`, { method: 'POST', body })
      push(name === 'duplicate_replay'
        ? { title: 'Duplicate replay', message: `sent ${r.result?.sent ?? 0} · duplicates ${r.result?.duplicates ?? '—'}`, tone: 'info' }
        : { title: `${sc.data.catalog[name].label} ${name === 'normal' ? 'restored' : 'triggered'}`, message: r.target ? `target ${r.target}` : 'whole collector', tone: name === 'normal' ? 'success' : 'warning' })
      onChange(); sc.refresh()
    } catch (e) { push({ title: 'Scenario failed', message: e.message, tone: 'error' }) } finally { setBusy(null) }
  }
  return (
    <div className="space-y-4">
      <Card title="Live demonstration scenarios" tone="local"
        subtitle="Each scenario changes what the telemetry generator / collector pipeline actually does — the SaaS only sees the resulting approved metadata."
        actions={<>
          <Select label="Target host" value={target} onChange={setTarget} options={[{ value: '', label: 'default (app host)' }, ...sc.data.hosts.map((h) => ({ value: h, label: h }))]} />
          <label className="flex items-center gap-1.5 text-xs text-muted">Duration (s)
            <input type="number" min="5" max="600" className="input w-20 py-1" value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="default" /></label>
        </>}>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {Object.entries(sc.data.catalog).map(([name, c]) => (
            <motion.button key={name} whileTap={{ scale: 0.98 }} onClick={() => trigger(name)} disabled={!!busy}
              className={`card-hover rounded-lg border p-3 text-left ${active[name] ? 'border-local bg-local/15' : 'border-line'}`} aria-pressed={!!active[name]}>
              <div className="flex items-center justify-between gap-2 text-sm font-medium">
                <span className="flex items-center gap-1.5">{busy === name ? <Spinner /> : <Play size={13} className="text-local" aria-hidden />}{c.label}</span>
                {active[name] && <span className="flex items-center gap-1 text-xs text-local"><StatusIndicator state="warn" pulse />{active[name].remaining_s}s</span>}
              </div>
              <div className="mt-1 text-xs text-muted">{c.description}</div>
              <div className="mt-1 text-[10px] uppercase tracking-wider text-muted">scope: {c.scope}{c.duration ? ` · ${c.duration}s` : ''}</div>
            </motion.button>))}
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Active scenarios" tone="local">
          {!(s.active_scenarios || []).length ? <EmptyState title="Normal operation" Icon={ShieldCheck}>No scenario is running.</EmptyState> : (
            <ul className="space-y-2">{s.active_scenarios.map((a) => (
              <li key={a.name} className="flex items-center justify-between rounded-lg border border-local/40 bg-local/5 p-2 text-sm">
                <span>{sc.data.catalog[a.name]?.label || a.name}{a.target && <span className="text-xs text-muted"> · {a.target}</span>}</span>
                <span className="text-xs text-local">{a.remaining_s}s left</span></li>))}</ul>)}
        </Card>
        <Card title="Recent triggers" tone="local">
          {!(sc.data.history || []).length ? <EmptyState title="Nothing triggered yet" /> : (
            <ul className="space-y-1 text-xs">{[...sc.data.history].reverse().map((h, i) => (
              <li key={i}><span className="mono text-muted">{new Date(h.at * 1000).toISOString().slice(11, 19)}Z</span> {sc.data.catalog[h.name]?.label || h.name}{h.target && <span className="text-muted"> · {h.target}</span>}</li>))}</ul>)}
        </Card>
      </div>
    </div>
  )
}
