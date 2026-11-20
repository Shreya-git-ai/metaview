import { createContext, useCallback, useContext, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'

const ToastCtx = createContext({ push: () => {} })
export const useToast = () => useContext(ToastCtx)

const ICON = { success: CheckCircle2, error: AlertTriangle, info: Info, warning: AlertTriangle }
const COLOR = { success: 'var(--color-good)', error: 'var(--color-crit)', info: 'var(--color-info)', warning: 'var(--color-warn)' }

// NotificationToast provider: push({ title, message, tone }) from anywhere.
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const push = useCallback(({ title, message, tone = 'info', ttl = 4500 }) => {
    const id = Math.random().toString(36).slice(2)
    setToasts((t) => [...t.slice(-3), { id, title, message, tone }])
    setTimeout(() => dismiss(id), ttl)
  }, [dismiss])
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(92vw,360px)] flex-col gap-2" aria-live="polite">
        <AnimatePresence initial={false}>
          {toasts.map((t) => {
            const I = ICON[t.tone] || Info
            return (
              <motion.div key={t.id} layout initial={{ opacity: 0, y: 12, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, x: 40 }} transition={{ duration: 0.18 }}
                className="card pointer-events-auto flex items-start gap-3 p-3 shadow-xl" role="status">
                <I size={16} style={{ color: COLOR[t.tone] }} className="mt-0.5 shrink-0" aria-hidden />
                <div className="min-w-0 flex-1"><div className="text-sm font-medium">{t.title}</div>{t.message && <div className="mt-0.5 text-xs text-muted">{t.message}</div>}</div>
                <button onClick={() => dismiss(t.id)} aria-label="Dismiss notification" className="text-muted hover:text-text"><X size={14} /></button>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}
