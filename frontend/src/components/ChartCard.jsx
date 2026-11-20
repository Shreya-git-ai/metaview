import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../services/api'
import { CHART, useTheme } from '../services/theme'
import { usePoll } from '../hooks/usePoll'
import { EmptyState, ErrorState, useTimeRange } from './ds'
import { fmtVal } from './ui'

/**
 * ChartCard — live time series of one metric from GET /metrics/history.
 * One line per pseudonymous source (colour follows the source id), optional threshold reference lines
 * (from tenant thresholds) and anomaly markers (points the detector flagged).
 */
export default function ChartCard({ metric, title, minutes: fixedMinutes, sourceId, height = 200, interval = 3000,
  thresholds, showAnomalies = true, onSourceClick }) {
  const [theme] = useTheme()
  const c = CHART[theme] || CHART.dark
  const { minutes: globalMinutes } = useTimeRange()
  const minutes = fixedMinutes || globalMinutes
  const q = `/metrics/history?metric=${metric}&minutes=${minutes}${sourceId ? `&source_id=${sourceId}` : ''}`
  const { data, error, loading } = usePoll((signal) => api(q, { signal, expect: 'object' }), interval, [q])
  const sources = useMemo(() => (data?.sources || []).slice().sort((a, b) => a.source_id.localeCompare(b.source_id)), [data])
  const unit = data?.unit
  const label = (id) => { const s = sources.find((x) => x.source_id === id); return s ? `${id.slice(4, 10)} · ${s.role}` : id }
  const anomalyPts = useMemo(() => {
    if (!showAnomalies || !data?.points) return []
    const out = []
    for (const p of data.points) for (const sid of p._anomalous || []) if (p[sid] != null) out.push({ t: p.t, v: p[sid], sid })
    return out
  }, [data, showAnomalies])
  const latest = data?.points?.length ? data.points[data.points.length - 1] : null

  return (
    <motion.section className="card p-4" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <header className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{title || data?.label}</h3>
          <p className="text-xs text-muted">{unit ? `${unit} · last ${minutes} min · UTC` : 'loading…'}{anomalyPts.length ? ` · ${anomalyPts.length} anomalous point(s)` : ''}</p>
        </div>
        {latest && sources.length === 1 && <span className="text-sm font-semibold tabular-nums">{fmtVal(latest[sources[0].source_id], unit)}</span>}
      </header>
      {error && !data && <ErrorState error={error} />}
      {loading && !data ? <div className="skeleton" style={{ height }} /> : !data?.points?.length ? <EmptyState title="No data in this range">Waiting for collectors to report.</EmptyState> : (
        <div style={{ height }} role="img" aria-label={`${data.label} chart for ${sources.length} source(s), last ${minutes} minutes`}>
          <ResponsiveContainer>
            <ComposedChart data={data.points} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid stroke={c.grid} strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} scale="time" tick={{ fill: c.axis, fontSize: 11 }}
                tickFormatter={(t) => new Date(t).toISOString().slice(11, 16)} stroke={c.grid} minTickGap={40} />
              <YAxis tick={{ fill: c.axis, fontSize: 11 }} stroke={c.grid} width={52}
                domain={unit === '%' && metric !== 'packet_loss_pct' ? [0, 100] : ['auto', 'auto']}
                tickFormatter={(v) => (unit === 'ms' && v >= 1000 ? `${(v / 1000).toFixed(1)}s` : v)} />
              <Tooltip contentStyle={{ background: c.tipBg, border: `1px solid ${c.tipBorder}`, borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: c.axis }} itemStyle={{ color: c.tipText }} cursor={{ stroke: c.axis, strokeDasharray: '3 3' }}
                labelFormatter={(t) => new Date(t).toISOString().slice(11, 19) + 'Z'}
                formatter={(v, name) => (name === 'anomaly' ? [fmtVal(v, unit), 'anomalous point'] : [fmtVal(v, unit), label(name)])} />
              {sources.length > 1 && <Legend wrapperStyle={{ fontSize: 11, cursor: onSourceClick ? 'pointer' : 'default' }} iconType="plainline"
                onClick={(e) => onSourceClick?.(e.dataKey)}
                formatter={(name) => <span style={{ color: c.legend }}>{name === 'anomaly' ? 'anomaly marker' : label(name)}</span>} />}
              {thresholds && thresholds.map((v, i) => v != null && (
                <ReferenceLine key={i} y={v} stroke={i === 0 ? 'var(--color-warn)' : 'var(--color-crit)'} strokeDasharray="4 4"
                  label={{ value: i === 0 ? 'fair' : 'poor', position: 'insideTopRight', fill: c.axis, fontSize: 10 }} />
              ))}
              {sources.map((s, i) => (
                <Line key={s.source_id} dataKey={s.source_id} stroke={c.series[i % c.series.length]} strokeWidth={2} dot={false}
                  isAnimationActive={false} connectNulls activeDot={{ r: 4, stroke: c.ring, strokeWidth: 2 }} />
              ))}
              {anomalyPts.length > 0 && <Scatter data={anomalyPts} dataKey="v" name="anomaly" fill="var(--color-crit)" shape="diamond" isAnimationActive={false} legendType="diamond" />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </motion.section>
  )
}

