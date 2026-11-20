import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { EmptyState, ErrorState, LoadingSkeleton } from './ds'

/**
 * Reusable data table.
 * columns: [{ key, header, render?(row), sortValue?(row), className?, align? }]
 * Sortable by clicking a header; rows are keyboard-activatable when onRowClick is set.
 */
export default function DataTable({ columns, rows, rowKey, onRowClick, selectedKey, loading, error, onRetry,
  empty = 'No records', emptyHint, maxHeight, dense, initialSort }) {
  const [sort, setSort] = useState(initialSort || null) // { key, dir }
  const sorted = useMemo(() => {
    if (!rows || !sort) return rows || []
    const col = columns.find((c) => c.key === sort.key)
    const get = col?.sortValue || ((r) => r[sort.key])
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b)
      if (x == null) return 1
      if (y == null) return -1
      return (x > y ? 1 : x < y ? -1 : 0) * (sort.dir === 'asc' ? 1 : -1)
    })
  }, [rows, sort, columns])

  if (error && !rows) return <ErrorState error={error} onRetry={onRetry} />
  if (loading && !rows) return <LoadingSkeleton rows={4} h={28} />
  if (!sorted.length) return <EmptyState title={empty}>{emptyHint}</EmptyState>

  const toggle = (c) => {
    if (c.sortable === false) return
    setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: c.key, dir: 'desc' }))
  }
  return (
    <div className="overflow-x-auto" style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
      {error && <div className="mb-2"><ErrorState error={error} onRetry={onRetry} /></div>}
      <table className={`tbl ${dense ? 'text-xs' : ''}`}>
        <thead className="sticky top-0 z-[1] bg-panel">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.align === 'right' ? 'text-right' : ''}
                aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                {c.sortable === false ? c.header : (
                  <button className="inline-flex items-center gap-1 uppercase hover:text-text" onClick={() => toggle(c)}>
                    {c.header}
                    {sort?.key === c.key && (sort.dir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                  </button>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const k = rowKey(r)
            const clickable = !!onRowClick
            return (
              <tr key={k} onClick={clickable ? () => onRowClick(r) : undefined}
                onKeyDown={clickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(r) } } : undefined}
                tabIndex={clickable ? 0 : undefined}
                className={`${clickable ? 'cursor-pointer' : ''} ${selectedKey === k ? 'bg-accent/10' : ''} transition-colors`}>
                {columns.map((c) => (
                  <td key={c.key} className={`${c.className || ''} ${c.align === 'right' ? 'text-right tabular-nums' : ''}`}>
                    {c.render ? c.render(r) : r[c.key] ?? '—'}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
