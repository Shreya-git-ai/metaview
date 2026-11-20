// NEXORA design system primitives (shared by every page).
import { createContext, useContext, useEffect, useId, useRef, useState } from 'react'
import { AnimatePresence, animate, motion, useReducedMotion } from 'framer-motion'
import { AlertTriangle, ChevronRight, Inbox, RefreshCw, Search, X } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { Spinner } from './ui'

// ------------------------------------------------------------------ motion presets
export const fadeUp = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.22, ease: 'easeOut' } },
  exit: { opacity: 0, y: -4, transition: { duration: 0.12 } },
}
export const stagger = { animate: { transition: { staggerChildren: 0.04 } } }
export const item = { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0, transition: { duration: 0.2 } } }

// ------------------------------------------------------------------ time range (global)
export const RANGES = [
  { label: '5m', minutes: 5 }, { label: '15m', minutes: 15 }, { label: '30m', minutes: 30 },
  { label: '1h', minutes: 60 }, { label: '2h', minutes: 120 },
]
export const TimeRangeContext = createContext({ minutes: 15, setMinutes: () => {} })
export const useTimeRange = () => useContext(TimeRangeContext)

export function TimeRangeSelector() {
  const { minutes, setMinutes } = useTimeRange()
  return <Segmented value={minutes} onChange={setMinutes} options={RANGES.map((r) => ({ value: r.minutes, label: r.label }))} label="Time range" />
}

export function Segmented({ value, onChange, options, label }) {
  const id = useId()
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-panel-2 p-0.5">
      {options.map((o) => (
        <button key={String(o.value)} role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={`relative rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${value === o.value ? 'text-text' : 'text-muted hover:text-text'}`}>
          {value === o.value && <motion.span layoutId={`seg-${id}`} className="absolute inset-0 rounded-md bg-panel shadow-sm ring-1 ring-line" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ numbers
export function AnimatedNumber({ value, decimals = 0, suffix = '' }) {
  const reduce = useReducedMotion()
  const ref = useRef(null)
  const prev = useRef(0)
  useEffect(() => {
    const el = ref.current
    if (!el || value == null || Number.isNaN(Number(value))) return undefined
    const to = Number(value)
    if (reduce) { el.textContent = to.toFixed(decimals) + suffix; prev.current = to; return undefined }
    const ctrl = animate(prev.current, to, {
      duration: 0.6, ease: 'easeOut',
      onUpdate: (v) => { el.textContent = v.toFixed(decimals) + suffix },
    })
    prev.current = to
    return () => ctrl.stop()
  }, [value, decimals, suffix, reduce])
  if (value == null) return <span>—</span>
  return <span ref={ref} className="tabular-nums">{Number(value).toFixed(decimals)}{suffix}</span>
}

const TONE = { crit: 'var(--color-crit)', warn: 'var(--color-warn)', ok: 'var(--color-good)', ai: 'var(--color-ai)', info: 'var(--color-info)', accent: 'var(--color-accent)' }

export function MetricCard({ label, value, decimals = 0, suffix = '', hint, tone, Icon, pulse, onClick, to, children, srStatus }) {
  const color = TONE[tone] || 'var(--color-text)'
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="label">{label}</span>
        {Icon && <Icon size={16} className="text-muted" aria-hidden />}
      </div>
      <div className="mt-2 text-2xl font-semibold" style={{ color }}>
        {typeof value === 'number' ? <AnimatedNumber value={value} decimals={decimals} suffix={suffix} /> : (value ?? '—')}
      </div>
      {srStatus && <span className="sr-only">{srStatus}</span>}
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
      {children}
    </>
  )
  const cls = `card card-hover block p-4 text-left ${pulse ? 'pulse-crit' : ''}`
  if (to) return <Link to={to} className={cls}>{body}</Link>
  if (onClick) return <button onClick={onClick} className={`${cls} w-full`}>{body}</button>
  return <motion.div variants={item} className={cls}>{body}</motion.div>
}

// ------------------------------------------------------------------ status
export function StatusIndicator({ state = 'ok', label, pulse }) {
  const color = { ok: 'var(--color-good)', warn: 'var(--color-warn)', crit: 'var(--color-crit)', idle: 'var(--color-muted)', info: 'var(--color-info)' }[state]
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" role="status">
      <span className="relative inline-flex h-2.5 w-2.5">
        {pulse && <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 motion-reduce:animate-none" style={{ background: color }} />}
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      </span>
      {label}
    </span>
  )
}

// ------------------------------------------------------------------ page header + breadcrumbs
const CRUMB = {
  '': 'Command Center', infrastructure: 'Infrastructure', network: 'Network', security: 'Security Centre',
  aiops: 'AIOps Intelligence', incidents: 'Incidents', collectors: 'Collector Fleet', privacy: 'Privacy & Egress',
  tenants: 'Tenants & Isolation', performance: 'Platform Performance', settings: 'Settings & Audit',
}
export function Breadcrumbs() {
  const { pathname } = useLocation()
  const parts = pathname.split('/').filter(Boolean)
  const crumbs = [{ to: '/', label: 'NEXORA' }, ...parts.map((p, i) => ({ to: '/' + parts.slice(0, i + 1).join('/'), label: CRUMB[p] || decodeURIComponent(p) }))]
  if (!parts.length) crumbs.push({ to: '/', label: 'Command Center' })
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs text-muted">
      {crumbs.map((c, i) => (
        <span key={c.to + i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight size={12} aria-hidden />}
          {i === crumbs.length - 1 ? <span className="text-text" aria-current="page">{c.label}</span> : <Link to={c.to} className="hover:text-text">{c.label}</Link>}
        </span>
      ))}
    </nav>
  )
}

export function PageHeader({ title, description, actions, badge }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <Breadcrumbs />
        <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">{title}{badge}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function RefreshButton({ onClick, loading }) {
  return <button className="btn" onClick={onClick} aria-label="Refresh now">{loading ? <Spinner /> : <RefreshCw size={14} />}<span className="hidden sm:inline">Refresh</span></button>
}

// ------------------------------------------------------------------ states
export function EmptyState({ title = 'Nothing here yet', children, Icon = Inbox }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-4 py-8 text-center">
      <Icon size={22} className="text-muted" aria-hidden />
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-md text-xs text-muted">{children}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry }) {
  if (!error) return null
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-crit/40 bg-crit/10 p-3 text-sm text-crit">
      <span className="flex items-center gap-2"><AlertTriangle size={15} aria-hidden />{error.message || String(error)}</span>
      {onRetry && <button className="btn" onClick={onRetry}>Retry</button>}
    </div>
  )
}

export function LoadingSkeleton({ rows = 3, h = 16 }) {
  return <div className="space-y-2" aria-busy="true" aria-label="Loading">{Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" style={{ height: h }} />)}</div>
}

// ------------------------------------------------------------------ inputs
export function SearchInput({ value, onChange, placeholder = 'Search…', label }) {
  return (
    <label className="relative block min-w-[180px]">
      <span className="sr-only">{label || placeholder}</span>
      <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
      <input className="input pl-8" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </label>
  )
}

export function Select({ value, onChange, options, label }) {
  return (
    <label className="inline-flex items-center gap-2 text-xs text-muted">
      <span className={label ? '' : 'sr-only'}>{label || 'Filter'}</span>
      <select className="rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-xs text-text" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}

// ------------------------------------------------------------------ tabs
export function Tabs({ tabs, value, onChange }) {
  const id = useId()
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}
          className={`relative shrink-0 px-4 py-2 text-sm transition-colors ${value === t.value ? 'text-text' : 'text-muted hover:text-text'}`}>
          {t.label}{t.count != null && <span className="chip ml-1.5">{t.count}</span>}
          {value === t.value && <motion.span layoutId={`tab-${id}`} className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-accent" />}
        </button>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ overlays
function useEscape(open, onClose) {
  useEffect(() => {
    if (!open) return undefined
    const h = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
}

export function Drawer({ open, onClose, title, subtitle, children, width = 560, actions }) {
  useEscape(open, onClose)
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="fixed inset-0 z-40 bg-black/40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.aside role="dialog" aria-modal="true" aria-label={title}
            className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-panel shadow-2xl" style={{ maxWidth: width }}
            initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: 'spring', stiffness: 380, damping: 40 }}>
            <header className="flex items-start justify-between gap-3 border-b border-line p-4">
              <div className="min-w-0"><h2 className="truncate text-base font-semibold">{title}</h2>{subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}</div>
              <div className="flex items-center gap-2">{actions}<button className="btn" onClick={onClose} aria-label="Close"><X size={14} /></button></div>
            </header>
            <div className="flex-1 overflow-y-auto p-4">{children}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

export function Modal({ open, onClose, title, children, footer, width = 480 }) {
  useEscape(open, onClose)
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[12vh]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}>
          <motion.div role="dialog" aria-modal="true" aria-label={title} className="card w-full shadow-2xl" style={{ maxWidth: width }}
            initial={{ opacity: 0, scale: 0.96, y: 8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.16 }} onClick={(e) => e.stopPropagation()}>
            {title && <div className="border-b border-line px-4 py-3 text-sm font-semibold">{title}</div>}
            <div className="p-4">{children}</div>
            {footer && <div className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function ConfirmationDialog({ open, title, message, confirmLabel = 'Confirm', tone = 'primary', onConfirm, onCancel, busy }) {
  return (
    <Modal open={open} onClose={onCancel} title={title}
      footer={<>
        <button className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={`btn ${tone === 'danger' ? 'border-crit bg-crit text-white' : 'btn-primary'}`} onClick={onConfirm} disabled={busy} autoFocus>{busy && <Spinner />}{confirmLabel}</button>
      </>}>
      <div className="text-sm text-muted">{message}</div>
    </Modal>
  )
}

// ------------------------------------------------------------------ section + key/value
export function KV({ items, cols = 2 }) {
  return (
    <dl className={`grid gap-x-4 gap-y-2 text-sm ${cols === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
      {items.filter(Boolean).map(([k, v]) => (
        <div key={k} className="min-w-0"><dt className="label">{k}</dt><dd className="mt-0.5 break-words">{v ?? '—'}</dd></div>
      ))}
    </dl>
  )
}

export function Reveal({ children, className = '' }) {
  return <motion.div variants={stagger} initial="initial" animate="animate" className={className}>{children}</motion.div>
}
