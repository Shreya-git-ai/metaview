import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './styles/index.css'
import { session } from './services/api'
import { initTheme } from './services/theme'
import AppShell from './layouts/AppShell'
import { LoadingSkeleton } from './components/ds'
import Login from './pages/Login'
const CommandCenter = lazy(() => import('./pages/CommandCenter'))
const Infrastructure = lazy(() => import('./pages/Infrastructure'))
const SourceDetail = lazy(() => import('./pages/SourceDetail'))
const Network = lazy(() => import('./pages/Network'))
const Security = lazy(() => import('./pages/Security'))
const AIOps = lazy(() => import('./pages/AIOps'))
const Incidents = lazy(() => import('./pages/Incidents'))
const Collectors = lazy(() => import('./pages/Collectors'))
const Privacy = lazy(() => import('./pages/Privacy'))
const Tenants = lazy(() => import('./pages/Tenants'))
const Performance = lazy(() => import('./pages/Performance'))
const SettingsPage = lazy(() => import('./pages/Settings'))

initTheme()

function RequireAuth({ children }) {
  return session.token ? children : <Navigate to="/login" replace />
}

// route-level code splitting: each page is its own chunk
const page = (el) => <Suspense fallback={<LoadingSkeleton rows={6} h={36} />}>{el}</Suspense>

// NEXORA SaaS (Tier 2). The Tier-1 Local Collector Console is a separate app (local.html, port 5174).
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={page(<CommandCenter />)} />
          <Route path="infrastructure" element={page(<Infrastructure />)} />
          <Route path="infrastructure/:sourceId" element={page(<SourceDetail />)} />
          <Route path="network" element={page(<Network />)} />
          <Route path="security" element={page(<Security />)} />
          <Route path="aiops" element={page(<AIOps />)} />
          <Route path="incidents" element={page(<Incidents />)} />
          <Route path="collectors" element={page(<Collectors />)} />
          <Route path="privacy" element={page(<Privacy />)} />
          <Route path="tenants" element={page(<Tenants />)} />
          <Route path="performance" element={page(<Performance />)} />
          <Route path="settings" element={page(<SettingsPage />)} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
