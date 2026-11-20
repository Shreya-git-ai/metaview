// NEXORA Local Collector Console — the Tier-1 application. Separate from the SaaS app (own entry, own port 5174);
// data comes ONLY from the collector process on 127.0.0.1 via this app's dev proxy, never through the SaaS backend.
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Lock, Terminal } from 'lucide-react'
import { COLLECTORS, local } from './api'
import { useLocalPoll } from './useLocalPoll'
import { BufferDelivery, EgressPolicy, Overview, RawRedaction, Scenarios } from './panels'
import EgressProof from './proof'
import { LoadingSkeleton, Select, StatusIndicator, Tabs } from '../components/ds'
import { ThemeToggle } from '../components/ui'

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'raw', label: 'Raw & redaction' },
  { value: 'egress', label: 'Egress & policy' },
  { value: 'buffer', label: 'Buffer & delivery' },
  { value: 'proof', label: 'Egress proof' },
  { value: 'scenarios', label: 'Scenarios' },
]

function NotRunning({ collectorId, error, onRetry }) {
  return (
    <div className="card mx-auto mt-6 max-w-2xl border-crit/40 p-6" role="alert">
      <StatusIndicator state="crit" label={<span className="text-sm font-semibold">Collector process not running / unreachable</span>} />
      <p className="mt-2 text-sm text-muted">The console could not reach <span className="mono">{collectorId}</span> on 127.0.0.1 ({error?.message}). Start it from the project root:</p>
      <pre className="json mt-3">{`.venv\\Scripts\\python -m local_collector.collector --env config/${collectorId}.env`}</pre>
      <p className="mt-2 text-xs text-muted">If the collector is stopped, the SaaS marks it offline after the heartbeat timeout (30 s) and its sources become stale/poor.</p>
      <button className="btn mt-4" onClick={onRetry}>Retry now</button>
    </div>
  )
}

export default function LocalConsoleApp() {
  const { collectorId } = useParams()
  const nav = useNavigate()
  const [tab, setTab] = useState('overview')
  const st = useLocalPoll((signal) => local(collectorId, '/status', { signal }), 1500, [collectorId])
  const s = st.data
  const reachable = !!s && !(st.error && st.error.name !== 'AbortError')
  const known = COLLECTORS.some((c) => c.id === collectorId)
  const conn = s?.connectivity?.state === 'connected' && !s?.partitioned
  const state = !reachable ? { s: 'crit', label: 'process unreachable' } : s.paused ? { s: 'crit', label: 'halted (simulated)' }
    : conn ? { s: 'ok', label: 'running · SaaS connected' } : { s: 'warn', label: 'running · SaaS unreachable (buffering)' }
  const tabs = TABS.map((t) => (t.value === 'buffer' && s ? { ...t, count: (s.queue?.pending || 0) + (s.queue?.retrying || 0) || undefined }
    : t.value === 'egress' && s?.policy_blocked ? { ...t, count: s.policy_blocked } : t))

  return (
    <div className="min-h-full bg-console">
      <header className="sticky top-0 z-30 border-b border-local/40 bg-console/90 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <Terminal className="text-local" size={20} aria-hidden />
          <div className="min-w-0">
            <h1 className="text-sm font-semibold text-local sm:text-base">NEXORA Local Collector Console</h1>
            <p className="flex items-center gap-1 text-[11px] text-muted"><Lock size={11} aria-hidden />Tier 1 · customer environment · bound to 127.0.0.1 · raw data never leaves this machine · secrets are SYNTHETIC</p>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <StatusIndicator state={state.s} label={state.label} pulse={reachable && !s?.paused} />
            <Select label="Collector" value={collectorId} onChange={(v) => { setTab('overview'); nav(`/${v}`) }}
              options={[...COLLECTORS.map((c) => ({ value: c.id, label: `${c.id} · ${c.label}` })), ...(known ? [] : [{ value: collectorId, label: collectorId }])]} />
            <ThemeToggle />
          </div>
        </div>
        {reachable && <div className="px-4 sm:px-6"><Tabs tabs={tabs} value={tab} onChange={setTab} /></div>}
      </header>

      <main className="p-4 sm:p-6">
        {st.loading && !s ? <LoadingSkeleton rows={4} h={70} /> : !reachable ? <NotRunning collectorId={collectorId} error={st.error} onRetry={st.refresh} /> : (
          <>
            {(s.active_scenarios || []).length > 0 && (
              <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-local/40 bg-local/10 px-3 py-2 text-xs text-local" role="status">
                <StatusIndicator state="warn" pulse />Active scenario{s.active_scenarios.length > 1 ? 's' : ''}:
                {s.active_scenarios.map((a) => <span key={a.name} className="chip text-local">{a.name}{a.target ? ` · ${a.target.split('.')[0]}` : ''} · {a.remaining_s}s</span>)}
              </div>
            )}
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={`${collectorId}-${tab}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16 }}>
                {tab === 'overview' && <Overview s={s} collectorId={collectorId} />}
                {tab === 'raw' && <RawRedaction collectorId={collectorId} />}
                {tab === 'egress' && <EgressPolicy collectorId={collectorId} />}
                {tab === 'buffer' && <BufferDelivery s={s} collectorId={collectorId} />}
                {tab === 'proof' && <EgressProof collectorId={collectorId} />}
                {tab === 'scenarios' && <Scenarios s={s} collectorId={collectorId} onChange={st.refresh} />}
              </motion.div>
            </AnimatePresence>
          </>
        )}
      </main>
    </div>
  )
}
