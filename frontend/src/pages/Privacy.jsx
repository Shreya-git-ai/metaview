import { useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { useOutletContext } from 'react-router-dom'
import {
  Ban, CheckCircle2, ExternalLink, EyeOff, FileCheck2, Fingerprint, Lock, ScanLine, ShieldCheck, ShieldX, Trash2,
} from 'lucide-react'
import { api, LOCAL_COLLECTORS, LOCAL_CONSOLE_URL } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import {
  Drawer, EmptyState, ErrorState, KV, LoadingSkeleton, MetricCard, PageHeader, RefreshButton, Reveal, SearchInput,
  Select, Tabs,
} from '../components/ds'
import DataTable from '../components/DataTable'
import { EgressRecordCard } from '../components/domain'
import { Card, JsonView, TierBanner, fmtDateTime, fmtTime } from '../components/ui'

const RULE_NAMES = {
  'R-PASSWORD': 'Password', 'R-APIKEY': 'API key', 'R-BEARER': 'Bearer token', 'R-JWT': 'JWT',
  'R-AWSKEY': 'Cloud access key', 'R-URLCRED': 'URL credential', 'R-KV-SECRET': 'Secret/token', 'R-PKEY': 'Private key',
  'R-EMAIL': 'E-mail (pseudonymised)', 'R-IPV4': 'IP address (pseudonymised)', 'R-USER': 'Account name (pseudonymised)',
}
const SECRET_RULES = new Set(['R-PASSWORD', 'R-APIKEY', 'R-BEARER', 'R-JWT', 'R-AWSKEY', 'R-URLCRED', 'R-KV-SECRET', 'R-PKEY'])
const LOCAL_ONLY = [
  'Raw log lines and application payloads', 'Hostnames and internal IP addresses', 'User names, e-mail addresses',
  'Passwords, API keys, tokens, private keys (removed)', 'Process command lines, environment snapshots',
  'Pseudonymisation key (source salt)', 'Raw authentication records',
]

export default function Privacy() {
  const { tenantId } = useOutletContext()
  const [tab, setTab] = useState('egress')
  const sum = usePoll((signal) => api('/privacy/summary', { signal, expect: 'object' }), 5000)
  const pol = usePoll((signal) => api('/privacy/policy', { signal, expect: 'object' }), 0)
  const s = sum.data
  const collectors = LOCAL_COLLECTORS[tenantId] || []

  return (
    <div>
      <PageHeader title="Privacy & Egress Proof"
        description="Evidence of what left each customer environment. This SaaS view holds approved metadata only — raw telemetry and secrets never reach it. The raw → payload comparison lives in the separate Tier-1 Local Console."
        actions={<>
          {collectors.map((c) => <a key={c} className="btn" href={`${LOCAL_CONSOLE_URL}/${c}`} target="_blank" rel="noreferrer"><ExternalLink size={14} />Local Console {c}</a>)}
          <RefreshButton onClick={sum.refresh} loading={sum.loading} />
        </>} />
      {sum.error && !s && <div className="mb-3"><ErrorState error={sum.error} onRetry={sum.refresh} /></div>}
      <Reveal className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <MetricCard label="Approved events received" value={s?.received_total} Icon={FileCheck2} tone="ok" hint="stored after SaaS re-validation" />
        <MetricCard label="Blocked by policy" value={s?.policy_blocked_total} Icon={ShieldX} tone={s?.policy_blocked_total ? 'warn' : undefined} hint="collector-reported; never transmitted" />
        <MetricCard label="Secrets removed locally" value={s?.processing.secrets_redacted} Icon={Trash2} tone="crit" hint={s ? `in ${s.processing.events_with_secrets_removed} event window(s)` : ''} />
        <MetricCard label="Identifiers pseudonymised" value={s?.processing.identifiers_pseudonymised} Icon={EyeOff} tone="ai" />
        <MetricCard label="Raw fields dropped" value={s?.processing.fields_dropped} Icon={ScanLine} hint={s ? `${s.processing.raw_records.toLocaleString()} raw records processed` : ''} />
        <MetricCard label="Rejected by SaaS" value={s?.ingest.rejected_by_saas} Icon={Ban} tone={s?.ingest.rejected_by_saas ? 'crit' : 'ok'} hint={s ? `${s.ingest.duplicates} duplicates ignored` : ''} />
      </Reveal>

      <LocalVsEgress policy={pol.data} />

      <div className="my-4 grid gap-4 xl:grid-cols-[1fr_1.2fr]">
        <RedactionActivity summary={s} />
        <Card title="Per-collector reported totals" subtitle="Cumulative counters from each collector's last heartbeat">
          {!s ? <LoadingSkeleton rows={2} /> : (
            <DataTable rows={s.collectors} rowKey={(r) => r.collector_id} dense empty="No collectors"
              columns={[{ key: 'collector_id', header: 'Collector', render: (r) => <span className="mono text-xs">{r.collector_id}</span> },
                { key: 'status', header: 'Status' },
                { key: 'payloads_approved_total', header: 'Approved', align: 'right' },
                { key: 'policy_blocked', header: 'Blocked', align: 'right' },
                { key: 'secrets_redacted_total', header: 'Secrets removed', align: 'right' },
                { key: 'reported_at', header: 'Reported', render: (r) => <span className="mono text-xs">{fmtTime(r.reported_at)}</span> }]} />)}
          {s?.notes && <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-muted">{s.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
        </Card>
      </div>

      <Tabs value={tab} onChange={setTab} tabs={[{ value: 'egress', label: 'Egress history' }, { value: 'violations', label: 'Policy violations' },
        { value: 'audit', label: 'Transfer audit' }, { value: 'allow', label: 'Allow-list' }]} />
      <div className="mt-4">
        {tab === 'egress' && <EgressHistory collectors={collectors} />}
        {tab === 'violations' && <Violations summary={s} />}
        {tab === 'audit' && <TransferAudit />}
        {tab === 'allow' && <AllowList policy={pol.data} error={pol.error} />}
      </div>
    </div>
  )
}

function LocalVsEgress({ policy }) {
  const fields = policy?.contract?.payload_fields || []
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr]">
      <div className="card border-local/50 p-4">
        <TierBanner tier={1}>stays inside the customer environment</TierBanner>
        <div className="text-sm font-semibold">Local data</div>
        <ul className="mt-2 space-y-1 text-sm">
          {LOCAL_ONLY.map((x) => <li key={x} className="flex items-center gap-2 text-muted"><Lock size={13} className="shrink-0 text-local" aria-hidden />{x}</li>)}
        </ul>
        <p className="mt-3 text-xs text-muted">Visible only in the Tier-1 Local Console on the collector's own machine.</p>
      </div>
      <div className="hidden items-center md:flex" aria-hidden>
        <svg width="70" height="40"><line x1="0" y1="20" x2="62" y2="20" strokeWidth="2" className="flow" /><path d="M62 14 L70 20 L62 26" fill="none" style={{ stroke: 'var(--color-muted)' }} strokeWidth="2" /></svg>
      </div>
      <div className="card border-accent/50 p-4">
        <TierBanner tier={2}>may reach NEXORA SaaS (TLS + collector token)</TierBanner>
        <div className="text-sm font-semibold">Approved egress</div>
        {!policy ? <LoadingSkeleton rows={2} /> : (
          <>
            <div className="mt-2 flex flex-wrap gap-1">{fields.map((f) => <span key={f} className="mono rounded bg-good/10 px-1.5 py-0.5 text-[11px] text-good">{f}</span>)}</div>
            <p className="mt-3 text-xs text-muted">Free text: {policy.contract.free_text}. Unknown fields: {policy.contract.unknown_fields}.</p>
          </>)}
      </div>
    </div>
  )
}

function RedactionActivity({ summary }) {
  const rows = useMemo(() => Object.entries(summary?.rules_triggered || {}).sort((a, b) => b[1] - a[1]), [summary])
  const max = rows.length ? rows[0][1] : 1
  return (
    <Card title="Redaction activity" subtitle="Detection rules that fired, by number of event windows — rule ids and counts only">
      {!summary ? <LoadingSkeleton rows={4} /> : !rows.length ? <EmptyState title="No redactions recorded yet" Icon={ShieldCheck}>Trigger "Sensitive log injection" in the Local Console.</EmptyState> : (
        <ul className="space-y-2" aria-label="Redaction rule counts">
          {rows.map(([rule, n], i) => (
            <li key={rule} className="text-xs">
              <div className="mb-0.5 flex items-center justify-between gap-2">
                <span><span className="mono text-muted">{rule}</span> · {RULE_NAMES[rule] || rule}</span>
                <span className="font-semibold tabular-nums">{n.toLocaleString()}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-panel-2">
                <motion.div className="h-full rounded-full" style={{ background: SECRET_RULES.has(rule) ? 'var(--color-crit)' : 'var(--color-ai)' }}
                  initial={{ width: 0 }} animate={{ width: `${(n / max) * 100}%` }} transition={{ duration: 0.5, delay: i * 0.04 }} />
              </div>
            </li>))}
          <li className="flex gap-4 pt-1 text-[11px] text-muted">
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-crit" />secret removed</span>
            <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-ai" />identifier pseudonymised</span>
          </li>
        </ul>)}
    </Card>
  )
}

function EgressHistory({ collectors }) {
  const [collector, setCollector] = useState('')
  const [category, setCategory] = useState('')
  const [redacted, setRedacted] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(null)
  const qs = new URLSearchParams({ limit: '60' })
  if (collector) qs.set('collector_id', collector)
  if (category) qs.set('category', category)
  if (redacted) qs.set('redacted_only', 'true')
  if (q.trim()) qs.set('q', q.trim())
  const path = `/privacy/received?${qs}`
  const d = usePoll((signal) => api(path, { signal, expect: 'array' }), 4000, [path])
  return (
    <Card title="Egress history (as received by SaaS)" subtitle="Each record is an approved payload that passed the collector policy engine and SaaS re-validation"
      actions={<>
        <SearchInput value={q} onChange={setQ} placeholder="Event id or source…" label="Search egress records" />
        <Select value={collector} onChange={setCollector} label="Collector" options={[{ value: '', label: 'All' }, ...collectors.map((c) => ({ value: c, label: c }))]} />
        <Select value={category} onChange={setCategory} label="Category" options={[{ value: '', label: 'All' }, { value: 'metrics', label: 'metrics' }, { value: 'security_summary', label: 'security_summary' }, { value: 'local_alert', label: 'local_alert' }]} />
        <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={redacted} onChange={(e) => setRedacted(e.target.checked)} />With redactions only</label>
      </>}>
      {d.error && !d.data ? <ErrorState error={d.error} onRetry={d.refresh} /> : !d.data ? <LoadingSkeleton rows={5} h={46} />
        : !d.data.length ? <EmptyState title="No egress records match" /> : (
          <div className="grid gap-x-3 md:grid-cols-2 xl:grid-cols-3">
            {d.data.map((e) => <EgressRecordCard key={e.event_id} e={e} selected={sel?.event_id === e.event_id} onClick={() => setSel(e)} />)}
          </div>)}
      <Drawer open={!!sel} onClose={() => setSel(null)} width={680} title="Egress record" subtitle={sel?.event_id}>
        {sel && <EgressDetail key={sel.event_id} eventId={sel.event_id} />}
      </Drawer>
    </Card>
  )
}

function Step({ n, title, children, ok }) {
  return (
    <section className="rounded-lg border border-line p-3">
      <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-panel-2 text-[11px]">{n}</span>{title}
        {ok && <CheckCircle2 size={14} className="text-good" aria-label="passed" />}
      </div>
      <div className="text-sm">{children}</div>
    </section>
  )
}

function EgressDetail({ eventId }) {
  const d = usePoll((signal) => api(`/privacy/received/${eventId}`, { signal, expect: 'object' }), 0, [eventId])
  if (d.error) return <ErrorState error={d.error} />
  if (!d.data) return <LoadingSkeleton rows={8} />
  const e = d.data
  const p = e.payload || {}
  const proc = p.processing
  const rules = proc?.rules_triggered || []
  return (
    <div className="space-y-3">
      <TierBanner tier={2}>SaaS copy — sanitised metadata only</TierBanner>
      <Step n={1} title="Event identifier"><span className="mono break-all">{e.event_id}</span></Step>
      <Step n={2} title="Source collector"><span className="mono">{e.collector_id}</span> · source <span className="mono">{e.source_id}</span> ({p.source_role})</Step>
      <Step n={3} title="Processing timestamps (UTC)">
        <KV cols={3} items={[['Window start', fmtDateTime(p.window_start)], ['Window end (event time)', fmtDateTime(e.event_time)], ['Received by SaaS', fmtDateTime(e.received_at)]]} />
      </Step>
      <Step n={4} title="Detected sensitive categories">
        {!proc ? <span className="text-muted">No processing evidence in this payload (older schema or non-metrics category).</span>
          : !rules.length ? <span className="text-muted">None detected in this window.</span>
            : <div className="flex flex-wrap gap-1.5">{rules.map((r) => <span key={r} className={`rounded px-1.5 py-0.5 text-xs ${SECRET_RULES.has(r) ? 'bg-crit/12 text-crit' : 'bg-ai/12 text-ai'}`}>{RULE_NAMES[r] || r} <span className="mono opacity-70">{r}</span></span>)}</div>}
      </Step>
      <Step n={5} title="Redaction result" ok={!!proc}>
        {proc ? <KV cols={2} items={[['Raw records in window', proc.raw_records], ['Raw fields dropped', proc.fields_dropped], ['Secrets removed', proc.secrets_redacted], ['Identifiers pseudonymised', proc.identifiers_pseudonymised]]} />
          : <span className="text-muted">Not reported.</span>}
      </Step>
      <Step n={6} title="Policy validation" ok>
        Passed the collector policy engine (strict schema · identity · secret scan · canary check) — only passing payloads are ever transmitted.
        Re-validated by SaaS against the same contract and bound to the authenticated collector's tenant.
      </Step>
      <Step n={7} title="Transmission status" ok>Received over TLS · {e.latency_s}s after window end · {e.payload_bytes} bytes · category {e.category}</Step>
      <Step n={8} title="Sanitised metadata payload"><JsonView data={p} /></Step>
      <Step n={9} title="Audit record">
        <div className="flex items-start gap-2 text-xs"><Fingerprint size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />
          <div className="min-w-0"><div>SHA-256 of received canonical bytes</div><div className="mono break-all text-accent">{e.payload_sha256}</div>
            <p className="mt-1 text-muted">To prove this equals what left the customer environment, open the Tier-1 Local Console for <span className="mono">{e.collector_id}</span> and verify event <span className="mono">{e.event_id.slice(0, 8)}…</span> — the collector compares its egress-audit hash with this one.</p>
            <a className="btn mt-2" href={`${LOCAL_CONSOLE_URL}/${e.collector_id}`} target="_blank" rel="noreferrer"><ExternalLink size={13} />Open Local Console</a>
          </div></div>
      </Step>
    </div>
  )
}

function Violations({ summary }) {
  const d = usePoll((signal) => api('/privacy/rejections?limit=50', { signal, expect: 'array' }), 5000)
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <Card title="Blocked before egress (Tier 1)" subtitle="Payloads the collector policy engine refused to send">
        {!summary ? <LoadingSkeleton rows={2} /> : (
          <>
            <div className="text-3xl font-semibold tabular-nums text-warn">{summary.policy_blocked_total}</div>
            <p className="mt-1 text-xs text-muted">Collector-reported count. The blocked payloads and their reasons stay local — inspect them in the Local Console egress audit.</p>
            <ul className="mt-3 space-y-1 text-xs">{summary.collectors.map((c) => <li key={c.collector_id} className="flex justify-between"><span className="mono">{c.collector_id}</span><span className="tabular-nums">{c.policy_blocked}</span></li>)}</ul>
          </>)}
      </Card>
      <Card title="Rejected at SaaS ingestion (Tier 2)" subtitle="Schema, identity or authentication failures recorded by the SaaS">
        <DataTable rows={d.data} loading={d.loading} error={d.error} onRetry={d.refresh} rowKey={(r) => r.at + (r.collector_id || '')} dense
          empty="No SaaS-side rejections" emptyHint="Every payload received so far passed SaaS re-validation."
          columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtDateTime(r.at)}</span> },
            { key: 'collector_id', header: 'Collector', render: (r) => <span className="mono">{r.collector_id || '—'}</span> },
            { key: 'outcome', header: 'Outcome', render: (r) => <span className="text-crit">{r.outcome}</span> },
            { key: 'rejected', header: 'Rejected', align: 'right' },
            { key: 'detail', header: 'Reason', sortable: false, render: (r) => <span className="text-muted">{String(r.detail).slice(0, 140)}</span> }]} />
      </Card>
    </div>
  )
}

function TransferAudit() {
  const d = usePoll((signal) => api('/privacy/ingest-audit?limit=80', { signal, expect: 'array' }), 4000)
  return (
    <Card title="Data-transfer audit" subtitle="Every authenticated ingest request: accepted / duplicate / rejected">
      <DataTable rows={d.data} loading={d.loading} error={d.error} onRetry={d.refresh} rowKey={(r) => r.id} dense maxHeight={480}
        columns={[{ key: 'at', header: 'At (UTC)', render: (r) => <span className="mono">{fmtTime(r.at)}</span> },
          { key: 'collector_id', header: 'Collector', render: (r) => <span className="mono">{r.collector_id || '—'}</span> },
          { key: 'endpoint', header: 'Endpoint', render: (r) => <span className="mono text-muted">{r.endpoint}</span> },
          { key: 'outcome', header: 'Outcome', render: (r) => <span className={r.outcome === 'ok' ? 'text-good' : 'text-crit'}>{r.outcome}</span> },
          { key: 'accepted', header: 'Accepted', align: 'right' },
          { key: 'duplicates', header: 'Duplicates', align: 'right', render: (r) => <span className={r.duplicates ? 'text-warn' : ''}>{r.duplicates}</span> },
          { key: 'rejected', header: 'Rejected', align: 'right', render: (r) => <span className={r.rejected ? 'text-crit' : ''}>{r.rejected}</span> },
          { key: 'request_bytes', header: 'Bytes', align: 'right' }]} />
    </Card>
  )
}

function AllowList({ policy, error }) {
  if (error) return <ErrorState error={error} />
  if (!policy) return <LoadingSkeleton rows={4} />
  const c = policy.contract
  const Chips = ({ items }) => <div className="flex flex-wrap gap-1">{items.map((f) => <span key={f} className="mono rounded bg-good/10 px-1.5 py-0.5 text-[11px] text-good">{f}</span>)}</div>
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Approved payload contract" subtitle={`Schema ${c.schema_version} — shared by collector and SaaS`}>
        <div className="label mb-1">Payload fields</div><Chips items={c.payload_fields} />
        <div className="label mb-1 mt-3">Metric fields</div><Chips items={c.metric_fields} />
        {c.processing_fields && <><div className="label mb-1 mt-3">Processing evidence fields (counts + rule ids)</div><Chips items={c.processing_fields} /></>}
        <div className="label mb-1 mt-3">Context codes (enumerated)</div><Chips items={c.context_codes} />
        <p className="mt-3 text-xs text-muted">Unknown fields: {c.unknown_fields}. Free text: {c.free_text}.</p>
      </Card>
      <Card title="SaaS-side checks">
        <ul className="space-y-1.5 text-sm">{policy.saas_checks.map((x) => <li key={x} className="flex gap-2"><ShieldCheck size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />{x}</li>)}</ul>
        <p className="mt-3 rounded-lg bg-panel-2 p-2 text-xs text-muted">{policy.raw_data_endpoints}</p>
      </Card>
    </div>
  )
}
