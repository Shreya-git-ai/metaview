import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'
import { Terminal } from 'lucide-react'
import '../styles/index.css'
import { initTheme } from '../services/theme'
import LocalConsoleApp from './LocalConsoleApp'
import { COLLECTORS } from './api'
import { ToastProvider } from '../components/Toast'

initTheme()

function Picker() {
  return (
    <div className="flex min-h-full items-center justify-center bg-console p-4">
      <div className="card w-full max-w-md p-6">
        <div className="flex items-center gap-2 font-semibold text-local"><Terminal size={18} />NEXORA Local Collector Console (Tier 1)</div>
        <p className="mt-1 text-xs text-muted">Separate Tier-1 application — runs inside the customer environment and talks only to collector processes on 127.0.0.1. Pick a collector:</p>
        <div className="mt-4 grid gap-2">
          {COLLECTORS.map((c) => <Link key={c.id} to={`/${c.id}`} className="card-hover rounded-lg border border-line p-3 text-sm"><span className="mono">{c.id}</span> <span className="text-muted">· {c.label}</span></Link>)}
        </div>
      </div>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Picker />} />
          <Route path="/:collectorId" element={<LocalConsoleApp />} />
        </Routes>
      </BrowserRouter>
      </ToastProvider>
    </MotionConfig>
  </React.StrictMode>,
)
