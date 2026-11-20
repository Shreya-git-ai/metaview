import { useState } from 'react'
import { motion } from 'framer-motion'
import { Building2, Lock, ShieldCheck, ShieldX } from 'lucide-react'
import { api, session } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import { EmptyState, MetricCard, PageHeader, RefreshButton, Reveal, StatusIndicator } from '../components/ds'
import DataTable from '../components/DataTable'
import { AuditTable } from '../components/domain'
import { Card, HealthBadge, Spinner } from '../components/ui'
import { useToast } from '../components/Toast'

export default function Tenants() {
  const user = session.user
  const operator = user?.role === 'platform_operator'
  const t = usePoll((signal) => api('/tenants', { signal, expect: 'array' }), 5000)
  const denied = usePoll((signal) => api('/audit?action=cross_tenant_denied&limit=30', { signal, expect: 'array' }), 5000)
  const rows = t.data || []
  return (
    <div>
      <PageHeader title="Tenants & Isolation"
        description="Tenant scope comes from the signed token (or the collector's credential) and is enforced in every backend query — never by hiding UI elements."
        actions={<RefreshButton onClick={t.refresh} loading={t.loading} />} />
      <Reveal className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard label={operator ? 'Customer organisations' : 'Your organisation'} value={rows.length} Icon={Building2} />
        <MetricCard label="Collectors online" value={rows.reduce((a, x) => a + x.collectors_online, 0)} tone="ok" hint={`of ${rows.reduce((a, x) => a + x.collectors, 0)} registered`} />
        <MetricCard label="Monitored sources" value={rows.reduce((a, x) => a + x.sources, 0)} />
        <MetricCard label="Cross-tenant attempts denied" value={denied.data?.length} tone={denied.data?.length ? 'warn' : 'ok'} Icon={ShieldX} hint="audited, your tenant (last 30)" />
      </Reveal>
      <Card title={operator ? 'Customer organisations (platform operator view)' : 'Tenant overview'}
        subtitle={operator ? 'The operator may switch tenant in the top bar; each request is still scoped to one tenant.' : 'Tenant admins can only ever see their own organisation.'}>
        <DataTable rows={t.data} loading={t.loading} error={t.error} onRetry={t.refresh} rowKey={(r) => r.id} empty="No tenants"
          columns={[
            { key: 'name', header: 'Tenant', render: (r) => <span className="font-medium">{r.name}</span> },
            { key: 'id', header: 'Identifier', render: (r) => <span className="mono text-xs">{r.id}</span> },
            { key: 'region', header: 'Region' },
            { key: 'collectors', header: 'Collectors', render: (r) => `${r.collectors_online}/${r.collectors} online` },
            { key: 'sources', header: 'Sources', align: 'right' },
            { key: 'open_incidents', header: 'Open incidents', align: 'right' },
            { key: 'health', header: 'Health', sortValue: (r) => r.health.state, render: (r) => <HealthBadge state={r.health.state} /> },
            { key: 'iso', header: 'Data isolation', sortable: false, render: () => <StatusIndicator state="ok" label="Enforced server-side" /> },
          ]} />
      </Card>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        {user?.role === 'tenant_admin' ? <IsolationCheck user={user} onDone={denied.refresh} />
          : <Card title="Live isolation check"><EmptyState title="Sign in as a tenant admin" Icon={Lock}>The check fires real cross-tenant requests with a tenant-scoped token. Platform operators are allowed to switch tenants, so the check is shown to tenant admins.</EmptyState></Card>}
        <Card title="Audited cross-tenant denials" subtitle="Recorded by the backend for your tenant">
          <AuditTable rows={denied.data} loading={denied.loading} error={denied.error} maxHeight={320} />
        </Card>
      </div>
    </div>
  )
}

function IsolationCheck({ user, onDone }) {
  const { push } = useToast()
  const [results, setResults] = useState(null)
  const [busy, setBusy] = useState(false)
  const other = user.tenant_id === 'tenant-alpha' ? 'tenant-beta' : 'tenant-alpha'
  async function run() {
    setBusy(true)
    const probes = [
      [`GET /tenants/${other}/metrics`, () => api(`/tenants/${other}/metrics`)],
      [`GET /tenants/${other}/incidents`, () => api(`/tenants/${other}/incidents`)],
      [`GET /tenants/${other}`, () => api(`/tenants/${other}`)],
      ['GET /overview with spoofed X-Tenant-Id', () => api('/overview', { tenantOverride: other })],
      ['GET /privacy/received with spoofed X-Tenant-Id', () => api('/privacy/received', { tenantOverride: other })],
      ['GET /fleet with spoofed X-Tenant-Id', () => api('/fleet', { tenantOverride: other })],
    ]
    const out = []
    for (const [name, fn] of probes) {
      try { const r = await fn(); out.push({ name, status: 200, blocked: false, detail: `returned ${Array.isArray(r) ? `${r.length} rows` : 'data'}` }) }
      catch (e) { out.push({ name, status: e.status, blocked: true, detail: e.message }) }
    }
    setResults(out)
    setBusy(false)
    onDone?.()
    const leaks = out.filter((r) => !r.blocked).length
    push(leaks ? { title: 'Isolation check FAILED', message: `${leaks} request(s) returned data`, tone: 'error' }
      : { title: 'Isolation verified', message: `All ${out.length} cross-tenant requests were denied`, tone: 'success' })
  }
  return (
    <Card title="Live isolation check" subtitle={`Signed in as ${user.username} (${user.tenant_id}). Requests deliberately target ${other}.`}
      actions={<button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? <Spinner /> : <Lock size={14} />}Attempt cross-tenant access</button>}>
      {!results ? <p className="text-sm text-muted">Runs real API calls against the backend with your token and reports what the server returned.</p> : (
        <ul className="space-y-1.5">
          {results.map((r, i) => (
            <motion.li key={r.name} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-xs">
              <span className="mono">{r.name}</span>
              <span className={`flex items-center gap-1.5 ${r.blocked ? 'text-good' : 'text-crit'}`}>
                {r.blocked ? <ShieldCheck size={13} aria-hidden /> : <ShieldX size={13} aria-hidden />}
                HTTP {r.status} · {r.blocked ? `denied — ${r.detail}` : `LEAK — ${r.detail}`}
              </span>
            </motion.li>))}
        </ul>)}
    </Card>
  )
}
