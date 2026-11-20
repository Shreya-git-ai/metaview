// Egress proof (Tier 1): raw record with a SYNTHETIC secret → redaction → exact approved payload → SaaS verification.
// Moved here from the SaaS Privacy page: raw data and secret values may only ever be shown in this local console.
import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Ban, CheckCircle2, Fingerprint, ShieldCheck, XCircle } from 'lucide-react'
import { local } from './api'
import { useLocalPoll } from './useLocalPoll'
import { RedactedText, SecretText } from './highlight'
import { CheckChips, StatusText } from './panels'
import { EmptyState, ErrorState, LoadingSkeleton } from '../components/ds'
import { Card, JsonView, Spinner, fmtTime } from '../components/ui'

function Step({ n, title, tone = 'local', children, delay = 0 }) {
  const color = tone === 'saas' ? 'text-accent bg-accent/15' : 'text-local bg-local/15'
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay, duration: 0.25 }} className="min-w-0 rounded-lg border border-line p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider">
        <span className={`flex h-5 w-5 items-center justify-center rounded-full ${color}`}>{n}</span>
        <span className={tone === 'saas' ? 'text-accent' : 'text-local'}>{title}</span>
      </div>
      {children}
    </motion.div>
  )
}

function Verdict({ ok, title, text }) {
  return (
    <div className={`rounded-lg border p-3 ${ok ? 'border-good/40 bg-good/10' : 'border-crit/40 bg-crit/10'}`} role="status">
      <div className={`flex items-center gap-2 text-sm font-semibold ${ok ? 'text-good' : 'text-crit'}`}>{ok ? <CheckCircle2 size={16} aria-hidden /> : <XCircle size={16} aria-hidden />}{title}</div>
      <div className="mt-1 break-words text-xs text-muted">{text}</div>
    </div>
  )
}

export default function EgressProof({ collectorId }) {
  const proof = useLocalPoll((signal) => local(collectorId, '/proof', { signal }), 4000, [collectorId])
  const p = proof.data
  const eventId = p?.egress?.event_id
  const [verify, setVerify] = useState(null)
  const [busy, setBusy] = useState(false)

  const runVerify = async (id = eventId) => {
    if (!id) return
    setBusy(true)
    try { setVerify(await local(collectorId, `/verify/${id}`, { method: 'POST' })) } catch (e) { setVerify({ event_id: id, error: e.message }) } finally { setBusy(false) }
  }
  // verify automatically when a new proof event appears (and again once it has been delivered)
  const delivered = p?.egress?.status?.startsWith('delivered')
  useEffect(() => { if (eventId) runVerify(eventId) }, [eventId, delivered]) // eslint-disable-line react-hooks/exhaustive-deps

  if (proof.error && !p) return <ErrorState error={proof.error} onRetry={proof.refresh} />
  if (!p) return <LoadingSkeleton rows={5} h={60} />
  if (!p.available) return (
    <Card title="Egress proof" tone="local"><EmptyState title="No secret-bearing raw record yet" Icon={ShieldCheck}>{p.hint} Go to the Scenarios tab → “Sensitive log injection”.</EmptyState></Card>
  )
  const canaries = p.synthetic_secrets_local_only || []
  const rawText = p.raw.body.line || JSON.stringify(p.raw.body.top_processes)
  const v = verify && verify.event_id === eventId ? verify : null
  return (
    <div className="space-y-4">
      <Card title="Egress proof — one event, end to end" tone="local"
        subtitle="The most recent raw record that contained a SYNTHETIC secret, the redaction applied, the exact payload approved for the same source & window, and what the SaaS confirms it stored."
        actions={eventId && <button className="btn" onClick={() => runVerify()} disabled={busy}>{busy ? <Spinner /> : <Fingerprint size={14} />}Re-verify with SaaS</button>}>
        <div className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-4">
          <Step n={1} title={`Raw record · ${p.raw.host}`}>
            <div className="mb-1 text-[11px] text-muted">{fmtTime(p.raw.ts)} · {p.raw.kind}{p.raw.stream ? ` · ${p.raw.stream}` : ''} · local only</div>
            <pre className="json"><SecretText text={rawText} extra={canaries} /></pre>
          </Step>
          <Step n={2} title="After local redaction" delay={0.05}>
            <pre className="json">{p.redaction?.redacted_text ? <RedactedText text={p.redaction.redacted_text} /> : '(metric record: non-allow-listed fields such as top_processes were dropped by the filter)'}</pre>
            <div className="mt-2 flex flex-wrap gap-1">{(p.redaction?.findings || []).map((f) => (
              <span key={f.rule_id} className={`rounded px-1.5 py-0.5 text-[11px] ${f.kind === 'secret' ? 'bg-crit/15 text-crit' : 'bg-panel-2 text-muted'}`}>{f.rule_id} ×{f.matches}</span>))}</div>
          </Step>
          <Step n={3} title="Approved egress payload" delay={0.1}>
            {p.egress ? (
              <>
                <JsonView data={p.egress.payload} />
                <div className="mt-2 space-y-1 text-xs">
                  <div>event_id <span className="mono">{p.egress.event_id}</span></div>
                  <div>sha256 <span className="mono break-all text-local">{p.egress.sha256}</span></div>
                  <div>status <StatusText s={p.egress.status} /> · {p.egress.payload_bytes} bytes</div>
                  <CheckChips checks={p.egress.policy_checks} />
                </div>
              </>
            ) : <EmptyState title="Payload for this window not produced yet" />}
          </Step>
          <Step n={4} title="SaaS verification (TLS, collector token)" tone="saas" delay={0.15}>
            {!v ? <LoadingSkeleton rows={3} /> : v.error ? <ErrorState error={new Error(`${v.error} — the payload may still be buffered`)} /> : (
              <div className="space-y-2 text-xs">
                <div>SaaS HTTP <b>{v.saas_http_status}</b></div>
                <div>local sha256 <span className="mono break-all text-local">{v.local_sha256}</span></div>
                <div>SaaS sha256 <span className="mono break-all text-accent">{v.saas_sha256 || '— (not stored yet)'}</span></div>
                <div className="text-muted">The SaaS hashed the canonical bytes it received; the collector compares them with its egress audit.</div>
              </div>
            )}
          </Step>
        </div>
        {v && !v.error && (
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <Verdict ok={!!v.match} title={v.match ? 'SHA-256 match' : 'Not matched yet'} text="Egress audit record and SaaS copy are byte-identical (canonical JSON)." />
            <Verdict ok={v.synthetic_secrets_found_in_saas_copy === 0} title={`${v.synthetic_secrets_found_in_saas_copy} synthetic secrets in SaaS copy`}
              text={`Checked by the collector against ${canaries.length ? 'every' : 'all'} synthetic secret it injected locally. Secret values never leave this machine.`} />
            <Verdict ok={!!p.egress && !('raw_log' in (p.egress.payload || {}))} title="Allow-listed fields only"
              text={p.egress ? `Fields: ${Object.keys(p.egress.payload).join(', ')}` : ''} />
          </div>
        )}
      </Card>
      {p.blocked?.length > 0 && (
        <Card title="Blocked in the same window — never transmitted" tone="local" subtitle="A simulated misconfigured plugin tried to attach the raw log line; the policy engine stopped it.">
          {p.blocked.filter(Boolean).map((b) => (
            <div key={b.event_id} className="mb-2 rounded-lg border border-local/40 bg-local/5 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-local"><Ban size={14} aria-hidden />Payload {b.event_id.slice(0, 8)}… blocked</div>
              <CheckChips checks={b.policy_checks} />
              <ul className="mt-1 list-disc pl-5 text-xs text-muted">{b.policy_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>))}
        </Card>
      )}
    </div>
  )
}
