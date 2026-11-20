import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import {
  Activity, Bell, Brain, Building2, ChevronDown, ChevronsLeft, ChevronsRight, Gauge, Globe2, LogOut, Menu, Network,
  Radar, Search, Server, Settings, ShieldAlert, ShieldCheck, Siren, Terminal, X, Cpu,
} from 'lucide-react'
import { api, LOCAL_COLLECTORS, LOCAL_CONSOLE_URL, session } from '../services/api'
import { usePoll } from '../hooks/usePoll'
import { HealthBadge, ThemeToggle, SeverityBadge, fmtTime } from '../components/ui'
import { Modal, StatusIndicator, TimeRangeContext } from '../components/ds'
import { ToastProvider, useToast } from '../components/Toast'

export const NAV = [
  { group: 'Operations', items: [
    { to: '/', label: 'Command Center', Icon: Gauge, end: true },
    { to: '/infrastructure', label: 'Infrastructure', Icon: Cpu },
    { to: '/network', label: 'Network', Icon: Network },
    { to: '/security', label: 'Security Centre', Icon: ShieldAlert },
  ] },
  { group: 'Intelligence', items: [
    { to: '/aiops', label: 'AIOps Intelligence', Icon: Brain },
    { to: '/incidents', label: 'Incidents', Icon: Siren },
  ] },
  { group: 'Fleet & Trust', items: [
    { to: '/collectors', label: 'Collector Fleet', Icon: Server },
    { to: '/privacy', label: 'Privacy & Egress', Icon: ShieldCheck },
    { to: '/tenants', label: 'Tenants & Isolation', Icon: Building2 },
  ] },
  { group: 'Platform', items: [
    { to: '/performance', label: 'Performance', Icon: Activity },
    { to: '/settings', label: 'Settings & Audit', Icon: Settings },
  ] },
]

function NavItem({ item, collapsed, onNavigate, badge }) {
  const { to, label, Icon, end } = item
  return (
    <NavLink to={to} end={end} onClick={onNavigate} title={collapsed ? label : undefined}
      className={({ isActive }) => `group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? 'text-accent' : 'text-muted hover:bg-panel hover:text-text'}`}>
      {({ isActive }) => (
        <>
          {isActive && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg bg-accent/12 ring-1 ring-accent/25" transition={{ type: 'spring', stiffness: 500, damping: 40 }} />}
          <motion.span className="relative shrink-0" whileHover={{ scale: 1.12 }} transition={{ duration: 0.15 }}><Icon size={17} aria-hidden /></motion.span>
          {!collapsed && <span className="relative truncate">{label}</span>}
          {badge > 0 && <span className={`relative rounded-full bg-crit px-1.5 text-[10px] font-bold text-white ${collapsed ? 'absolute right-1 top-1' : 'ml-auto'}`}>{badge}</span>}
          {collapsed && <span role="tooltip" className="pointer-events-none absolute left-full z-50 ml-2 hidden whitespace-nowrap rounded-md border border-line bg-panel px-2 py-1 text-xs text-text shadow-lg group-hover:block group-focus-visible:block">{label}</span>}
        </>
      )}
    </NavLink>
  )
}

function Sidebar({ collapsed, setCollapsed, mobile, onClose, tenantId, incidents }) {
  const [closed, setClosed] = useState({})
  const localIds = LOCAL_COLLECTORS[tenantId] || []
  return (
    <aside className={`flex h-full flex-col border-r border-line bg-sidebar ${mobile ? 'w-72' : collapsed ? 'w-16' : 'w-64'} transition-[width] duration-200`} aria-label="Primary navigation">
      <div className="flex h-14 items-center gap-2 border-b border-line px-4">
        <Radar className="shrink-0 text-accent" size={22} aria-hidden />
        {(!collapsed || mobile) && <div className="min-w-0"><div className="text-sm font-bold tracking-[0.18em]">NEXORA</div>
          <div className="truncate text-[10px] text-muted">Intelligent Observability · Privacy by Design</div></div>}
        {mobile && <button className="btn ml-auto" onClick={onClose} aria-label="Close navigation"><X size={14} /></button>}
      </div>
      <nav className="flex-1 overflow-y-auto overflow-x-visible p-2">
        {NAV.map((g) => {
          const isClosed = closed[g.group] && !collapsed
          return (
            <div key={g.group} className="mb-2">
              {(!collapsed || mobile) ? (
                <button className="flex w-full items-center justify-between px-2 py-1 label hover:text-text" aria-expanded={!isClosed}
                  onClick={() => setClosed((c) => ({ ...c, [g.group]: !c[g.group] }))}>
                  {g.group}<ChevronDown size={12} className={`transition-transform ${isClosed ? '-rotate-90' : ''}`} aria-hidden />
                </button>
              ) : <div className="mx-3 my-2 border-t border-line" />}
              <AnimatePresence initial={false}>
                {!isClosed && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="overflow-visible">
                    {g.items.map((it) => <NavItem key={it.to} item={it} collapsed={collapsed && !mobile} onNavigate={mobile ? onClose : undefined}
                      badge={it.to === '/incidents' ? incidents : 0} />)}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )
        })}
        <div className="mt-3 border-t border-line pt-2">
          {(!collapsed || mobile) && <div className="label px-2 py-1">Tier 1 · separate app</div>}
          {localIds.map((cid) => (
            <a key={cid} href={`${LOCAL_CONSOLE_URL}/${cid}`} target="_blank" rel="noreferrer" title={`Local Collector Console ${cid} (opens the Tier-1 app)`}
              className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-local hover:bg-local/10">
              <Terminal size={17} className="shrink-0" aria-hidden />{(!collapsed || mobile) && <span className="truncate">Local Console <span className="mono text-xs">{cid}</span></span>}
            </a>
          ))}
        </div>
      </nav>
      {!mobile && (
        <button className="m-2 flex items-center justify-center gap-2 rounded-lg p-2 text-xs text-muted hover:bg-panel hover:text-text" onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? <ChevronsRight size={16} /> : <><ChevronsLeft size={16} /> Collapse</>}
        </button>
      )}
    </aside>
  )
}

function CommandPalette({ open, onClose }) {
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [res, setRes] = useState(null)
  useEffect(() => { if (!open) { setQ(''); setRes(null) } }, [open])
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return undefined }
    const ctrl = new AbortController()
    const t = setTimeout(() => api(`/search?q=${encodeURIComponent(q)}`, { signal: ctrl.signal }).then(setRes).catch(() => {}), 200)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q])
  const pages = NAV.flatMap((g) => g.items).filter((p) => q && p.label.toLowerCase().includes(q.toLowerCase()))
  const go = (to) => { onClose(); nav(to) }
  const Row = ({ onClick, children }) => <button onClick={onClick} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-panel-2 focus:bg-panel-2">{children}</button>
  return (
    <Modal open={open} onClose={onClose} title="Search NEXORA" width={560}>
      <input autoFocus className="input" placeholder="Pages, incidents, sources, collectors…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search query" />
      <div className="mt-3 max-h-[50vh] space-y-3 overflow-y-auto">
        {pages.length > 0 && <div><div className="label mb-1">Pages</div>{pages.map((p) => <Row key={p.to} onClick={() => go(p.to)}><p.Icon size={14} />{p.label}</Row>)}</div>}
        {res?.incidents?.length > 0 && <div><div className="label mb-1">Incidents</div>{res.incidents.map((i) => <Row key={i.id} onClick={() => go(`/incidents?incident=${i.id}`)}><span className="mono text-xs text-muted">{i.id}</span>{i.title}<span className="ml-auto"><SeverityBadge severity={i.severity} /></span></Row>)}</div>}
        {res?.sources?.length > 0 && <div><div className="label mb-1">Sources</div>{res.sources.map((s) => <Row key={s.source_id} onClick={() => go(`/infrastructure/${s.source_id}`)}><span className="mono text-xs">{s.source_id}</span>{s.role}<span className="ml-auto"><HealthBadge state={s.health_state} /></span></Row>)}</div>}
        {res?.collectors?.length > 0 && <div><div className="label mb-1">Collectors</div>{res.collectors.map((c) => <Row key={c.id} onClick={() => go(`/collectors?collector=${c.id}`)}><Server size={14} /><span className="mono text-xs">{c.id}</span><span className="ml-auto text-xs text-muted">{c.status}</span></Row>)}</div>}
        {q.length >= 2 && res && !pages.length && !res.incidents.length && !res.sources.length && !res.collectors.length && <p className="text-sm text-muted">No matches.</p>}
        {q.length < 2 && <p className="text-xs text-muted">Type at least 2 characters. Shortcut: Ctrl/⌘ + K.</p>}
      </div>
    </Modal>
  )
}

function Menu_({ label, icon, children, align = 'right', badge }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const k = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', h); window.addEventListener('keydown', k)
    return () => { document.removeEventListener('mousedown', h); window.removeEventListener('keydown', k) }
  }, [open])
  return (
    <div className="relative" ref={ref}>
      <button className="relative flex items-center gap-1.5 rounded-lg p-1.5 text-muted hover:bg-panel hover:text-text" aria-haspopup="menu" aria-expanded={open} aria-label={label} onClick={() => setOpen(!open)}>
        {icon}
        {badge > 0 && <span className="absolute -right-1 -top-1 rounded-full bg-crit px-1.5 text-[10px] font-bold text-white">{badge}</span>}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div role="menu" initial={{ opacity: 0, y: -4, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.14 }}
            className={`card absolute z-50 mt-2 w-80 p-2 shadow-2xl ${align === 'right' ? 'right-0' : 'left-0'}`} onClick={() => setOpen(false)}>
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function TopNavigation({ ov, tenant, setTenant, tenants, onMenu, onSearch }) {
  const nav = useNavigate()
  const user = session.user
  const k = ov?.kpis
  const age = ov?.freshness?.age_s
  const fresh = age != null && age < 15
  const events = usePoll((signal) => api('/events/recent?limit=8', { signal, expect: 'array' }), 10000, [tenant])
  return (
    <header className="flex h-14 items-center gap-3 border-b border-line bg-sidebar/80 px-3 backdrop-blur sm:px-5">
      <button className="btn lg:hidden" onClick={onMenu} aria-label="Open navigation"><Menu size={16} /></button>
      {user?.role === 'platform_operator' ? (
        <label className="flex items-center gap-2 text-sm"><span className="sr-only">Tenant</span>
          <select className="max-w-[180px] rounded-lg border border-line bg-panel px-2 py-1.5 text-sm" value={tenant || ''}
            onChange={(e) => { session.setTenant(e.target.value); setTenant(e.target.value) }}>
            {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      ) : <span className="truncate text-sm font-semibold">{ov?.tenant?.name || '…'}</span>}
      {ov?.tenant?.region && <span className="hidden items-center gap-1 text-xs text-muted md:flex" title="Tenant data region"><Globe2 size={13} aria-hidden />{ov.tenant.region}</span>}
      {ov && <span className="hidden sm:inline"><HealthBadge state={ov.health.state} /></span>}
      <button onClick={onSearch} className="ml-auto hidden items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-muted hover:text-text md:flex" aria-label="Search (Ctrl+K)">
        <Search size={13} />Search…<kbd className="chip">Ctrl K</kbd>
      </button>
      <button onClick={onSearch} className="ml-auto rounded-lg p-1.5 text-muted md:hidden" aria-label="Search"><Search size={17} /></button>
      <span className="hidden items-center lg:flex" title="Age of the newest event received from collectors">
        <StatusIndicator state={fresh ? 'ok' : 'warn'} label={`Telemetry ${age != null ? `${age.toFixed(0)}s old` : 'n/a'}`} pulse={fresh} />
      </span>
      <span className="hidden text-xs text-muted xl:inline">{k ? `${k.collectors_online}/${k.collectors_total} collectors` : ''}</span>
      <Menu_ label="Notifications" icon={<Bell size={17} />} badge={k?.active_incidents}>
        <div className="label px-2 py-1">Active incidents</div>
        {!ov?.incidents?.length ? <p className="px-2 py-1 text-sm text-muted">None.</p> : ov.incidents.slice(0, 4).map((i) => (
          <button key={i.id} role="menuitem" onClick={() => nav(`/incidents?incident=${i.id}`)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-panel-2">
            <SeverityBadge severity={i.severity} /><span className="truncate">{i.title}</span>
          </button>))}
        <div className="label mt-2 px-2 py-1">Recent events</div>
        {(events.data || []).slice(0, 5).map((e, idx) => (
          <div key={idx} className="px-2 py-1 text-xs"><span className="mono text-muted">{fmtTime(e.at)}</span> {e.text}</div>))}
      </Menu_>
      <Menu_ label="User menu" icon={<span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/20 text-xs font-bold text-accent">{(user?.display_name || '?').slice(0, 1)}</span>}>
        <div className="px-2 py-1.5"><div className="text-sm font-medium">{user?.display_name}</div><div className="text-xs text-muted">{user?.username} · {user?.role}{user?.tenant_id ? ` · ${user.tenant_id}` : ''}</div></div>
        <div className="my-1 border-t border-line" />
        <div className="flex items-center justify-between px-2 py-1 text-sm" onClick={(e) => e.stopPropagation()}>Theme <ThemeToggle /></div>
        <button role="menuitem" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-panel-2" onClick={() => nav('/settings')}><Settings size={14} />Settings</button>
        <button role="menuitem" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-crit hover:bg-panel-2" onClick={() => { session.clear(); nav('/login') }}><LogOut size={14} />Sign out</button>
      </Menu_>
    </header>
  )
}

function AlertWatcher({ ov }) {
  // toast when a NEW incident appears (alert arrival)
  const { push } = useToast()
  const seen = useRef(null)
  useEffect(() => {
    if (!ov?.incidents) return
    const ids = new Set(ov.incidents.map((i) => i.id))
    if (seen.current) {
      for (const i of ov.incidents) if (!seen.current.has(i.id)) push({ title: `New incident ${i.id}`, message: i.title, tone: i.severity === 'critical' ? 'error' : 'warning' })
    }
    seen.current = ids
  }, [ov, push])
  return null
}

export default function AppShell() {
  const location = useLocation()
  const user = session.user
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('nexora.sidebar') === 'collapsed' } catch { return false } })
  const [mobileOpen, setMobileOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [tenants, setTenants] = useState([])
  const [tenant, setTenant] = useState(session.tenant)
  const [minutes, setMinutes] = useState(15)
  const overview = usePoll((signal) => api('/overview', { signal, expect: 'object' }), 2000, [tenant])
  const timeCtx = useMemo(() => ({ minutes, setMinutes }), [minutes])

  useEffect(() => { try { localStorage.setItem('nexora.sidebar', collapsed ? 'collapsed' : 'open') } catch { /* ignore */ } }, [collapsed])
  useEffect(() => { setMobileOpen(false) }, [location.pathname])
  useEffect(() => {
    const h = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setSearchOpen(true) } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  useEffect(() => {
    if (user?.role === 'platform_operator') {
      api('/tenants').then((t) => { setTenants(t); if (!session.tenant && t.length) { session.setTenant(t[0].id); setTenant(t[0].id) } }).catch(() => {})
    }
  }, [user?.role])

  // Collection-to-dashboard latency: render time − event_time of newly seen data (posted for the Performance page).
  const lastSeen = useRef(null)
  const buf = useRef([])
  useEffect(() => {
    const et = overview.data?.freshness?.latest_event_time
    if (et && et !== lastSeen.current) {
      if (lastSeen.current) buf.current.push(Date.now() - new Date(et).getTime())
      lastSeen.current = et
    }
  }, [overview.data])
  useEffect(() => {
    const id = setInterval(() => {
      if (!buf.current.length) return
      api('/performance/dashboard-latency', { method: 'POST', body: { samples_ms: buf.current.splice(0, 50) } }).catch(() => {})
    }, 10000)
    return () => clearInterval(id)
  }, [])

  const ov = overview.data
  const tenantId = ov?.tenant?.id
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <TimeRangeContext.Provider value={timeCtx}>
          <AlertWatcher ov={ov} />
          <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[70] focus:rounded focus:bg-panel focus:p-2">Skip to content</a>
          <div className="flex h-full">
            <div className="hidden lg:block"><Sidebar collapsed={collapsed} setCollapsed={setCollapsed} tenantId={tenantId} incidents={ov?.kpis?.active_incidents} /></div>
            <AnimatePresence>
              {mobileOpen && (
                <>
                  <motion.div className="fixed inset-0 z-40 bg-black/50 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setMobileOpen(false)} />
                  <motion.div className="fixed inset-y-0 left-0 z-50 lg:hidden" initial={{ x: '-100%' }} animate={{ x: 0 }} exit={{ x: '-100%' }} transition={{ type: 'spring', stiffness: 400, damping: 40 }}>
                    <Sidebar mobile onClose={() => setMobileOpen(false)} tenantId={tenantId} incidents={ov?.kpis?.active_incidents} />
                  </motion.div>
                </>
              )}
            </AnimatePresence>
            <div className="flex min-w-0 flex-1 flex-col">
              <TopNavigation ov={ov} tenant={tenant} setTenant={setTenant} tenants={tenants} onMenu={() => setMobileOpen(true)} onSearch={() => setSearchOpen(true)} />
              <main id="main" className="flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-5">
                {/* entrance-only page transition (an exit animation would render the next route inside the old page) */}
                <motion.div key={location.pathname} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, ease: 'easeOut' }}>
                  <Outlet context={{ overview, tenantId, tenant }} />
                </motion.div>
              </main>
            </div>
          </div>
          <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
        </TimeRangeContext.Provider>
      </ToastProvider>
    </MotionConfig>
  )
}
