import { AlertTriangle, CheckCircle2, CircleDot, Info, Loader2, Moon, ShieldAlert, Sun, XCircle } from 'lucide-react'
import { useTheme } from '../services/theme'

// Status colours resolve through theme tokens, so they are re-stepped automatically in light mode.
const tint = (v, pct) => `color-mix(in srgb, ${v} ${pct}%, transparent)`
export const HEALTH = {
  excellent: { label: 'Excellent', color: 'var(--color-good)', bg: tint('var(--color-good)', 13), Icon: CheckCircle2 },
  good: { label: 'Good', color: 'var(--color-accent)', bg: tint('var(--color-accent)', 13), Icon: CircleDot },
  fair: { label: 'Fair', color: 'var(--color-warn)', bg: tint('var(--color-warn)', 13), Icon: AlertTriangle },
  poor: { label: 'Poor', color: 'var(--color-crit)', bg: tint('var(--color-crit)', 14), Icon: XCircle },
}
export const SEV = {
  info: { label: 'Info', color: 'var(--color-muted)', Icon: Info },
  warning: { label: 'Warning', color: 'var(--color-warn)', Icon: AlertTriangle },
  critical: { label: 'Critical', color: 'var(--color-crit)', Icon: ShieldAlert },
}

export function ThemeToggle({ className = '' }) {
  const [theme, setTheme] = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button className={`btn ${className}`} onClick={() => setTheme(next)} title={`Switch to ${next} mode`} aria-label={`Switch to ${next} mode`}>
      {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}{theme === 'dark' ? 'Light' : 'Dark'}
    </button>
  )
}

export function HealthBadge({ state, stale, size = 'sm' }) {
  const h = HEALTH[state] || HEALTH.fair
  const pad = size === 'lg' ? 'px-3 py-1.5 text-sm' : 'px-2 py-0.5 text-xs'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${pad}`}
      style={{ color: h.color, background: h.bg }}>
      <h.Icon size={size === 'lg' ? 16 : 12} />{h.label}{stale && <span className="opacity-80 font-normal">· stale</span>}
    </span>
  )
}

export function SeverityBadge({ severity, pulse }) {
  const s = SEV[severity] || SEV.info
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold ${pulse && severity === 'critical' ? 'pulse-crit' : ''}`}
      style={{ color: s.color, background: tint(s.color, 12) }}>
      <s.Icon size={12} />{s.label}
    </span>
  )
}

export function StatusDot({ ok, label }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className="h-2 w-2 rounded-full" style={{ background: ok ? 'var(--color-good)' : 'var(--color-crit)' }} />{label}
    </span>
  )
}

export function Card({ title, subtitle, actions, children, className = '', tone }) {
  const border = tone === 'local' ? 'border-local/60' : tone === 'ai' ? 'border-ai/50' : ''
  return (
    <section className={`card p-4 ${border} ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex items-start justify-between gap-3">
          <div>
            {title && <h3 className="text-sm font-semibold text-text">{title}</h3>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function Kpi({ label, value, hint, tone, Icon, pulse }) {
  const color = { crit: 'var(--color-crit)', warn: 'var(--color-warn)', ok: 'var(--color-good)', ai: 'var(--color-ai)' }[tone] || 'var(--color-text)'
  return (
    <div className={`card p-4 ${pulse ? 'pulse-crit' : ''}`}>
      <div className="flex items-center justify-between"><span className="label">{label}</span>{Icon && <Icon size={16} className="text-muted" />}</div>
      <div className="mt-2 text-2xl font-semibold tabular-nums" style={{ color }}>{value ?? '—'}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  )
}

export function Skeleton({ h = 120 }) { return <div className="skeleton w-full" style={{ height: h }} /> }

export function Empty({ children }) {
  return <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">{children}</div>
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="rounded-lg border border-crit/40 bg-crit/10 p-3 text-sm text-crit">⚠ {error.message || String(error)}</div>
}

export function Spinner() { return <Loader2 className="animate-spin" size={14} /> }

export function JsonView({ data, highlight = [] }) {
  let text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const parts = []
  if (highlight.length) {
    const esc = highlight.filter(Boolean).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    const re = new RegExp(`(${esc.join('|')})`, 'g')
    text.split(re).forEach((p, i) => parts.push(
      esc.length && highlight.includes(p) ? <mark key={i} className="rounded bg-crit/30 px-0.5 text-crit">{p}</mark> : p))
  }
  return <pre className="json">{highlight.length ? parts : text}</pre>
}

export function TierBanner({ tier, children }) {
  const local = tier === 1
  return (
    <div className={`mb-3 flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wider ${local ? 'bg-local/15 text-local' : 'bg-accent/15 text-accent'}`}>
      <span className="rounded bg-current/20 px-1.5 py-0.5">{local ? 'Tier 1 · Local' : 'Tier 2 · SaaS'}</span>
      <span className="normal-case tracking-normal font-medium opacity-90">{children}</span>
    </div>
  )
}

export const fmtTime = (iso) => (iso ? new Date(iso).toISOString().slice(11, 19) + 'Z' : '—')
export const fmtDateTime = (iso) => (iso ? new Date(iso).toISOString().replace('T', ' ').slice(0, 19) + 'Z' : '—')
export function fmtVal(v, unit) {
  if (v == null) return '—'
  if (unit === '%') return `${Number(v).toFixed(1)}%`
  if (unit === 'ms') return `${Math.round(v).toLocaleString()} ms`
  if (unit === 'Mbps') return `${Number(v).toFixed(0)} Mbps`
  return typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v)
}
export const ago = (s) => (s == null ? '—' : s < 60 ? `${Math.round(s)}s ago` : `${Math.round(s / 60)}m ago`)
