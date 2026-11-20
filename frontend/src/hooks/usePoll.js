import { useCallback, useEffect, useRef, useState } from 'react'

// Polls `fn(signal)` every `interval` ms (0 = once). Cancels in-flight requests on change/unmount,
// pauses while the browser tab is hidden, keeps the last good data on transient errors.
export function usePoll(fn, interval = 2000, deps = []) {
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
    // keep previous data while refetching (smooth time-range transitions); detail views remount via `key`
    setLoading(true)
    refresh()
    if (!interval) return () => ctrlRef.current?.abort()
    const id = setInterval(() => { if (!document.hidden) refresh() }, interval)
    const onVis = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVis)
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', onVis); ctrlRef.current?.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, interval, ...deps])

  return { data, error, loading, refresh, updatedAt }
}
