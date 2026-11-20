import { useCallback, useEffect, useRef, useState } from 'react'

// Polling hook for the Tier-1 console (cancels in-flight requests, pauses while the tab is hidden,
// keeps the last good data on transient errors). `paused` freezes refreshes (e.g. "pause live tail").
export function useLocalPoll(fn, interval = 2000, deps = [], paused = false) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const ctrlRef = useRef(null)

  const refresh = useCallback(async () => {
    ctrlRef.current?.abort()
    const ctrl = new AbortController()
    ctrlRef.current = ctrl
    try {
      const d = await fnRef.current(ctrl.signal)
      if (ctrl.signal.aborted) return
      setData(d); setError(null); setUpdatedAt(new Date())
    } catch (e) {
      if (e?.name === 'AbortError') return
      setError(e)
    } finally {
      if (!ctrl.signal.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    refresh()
    if (!interval || paused) return () => ctrlRef.current?.abort()
    const id = setInterval(() => { if (!document.hidden) refresh() }, interval)
    return () => { clearInterval(id); ctrlRef.current?.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, interval, paused, ...deps])

  return { data, error, loading, refresh, updatedAt }
}

export const secondsSince = (iso) => (iso ? Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000) : null)
