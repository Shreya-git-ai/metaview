// Central SaaS API client (Tier 2 only). Tenant scope is decided server-side from the JWT.
// The base URL is configurable (VITE_API_BASE); by default the Vite dev proxy forwards /api over verified TLS.
// NOTE: this SaaS app has NO client for the collectors' local consoles — raw telemetry is never fetched here.
export const API_BASE = (import.meta.env.VITE_API_BASE || '/api/v1').replace(/\/$/, '')
export const LOCAL_CONSOLE_URL = (import.meta.env.VITE_LOCAL_CONSOLE_URL || 'http://localhost:5174').replace(/\/$/, '')

const TOKEN_KEY = 'nexora.token'
const USER_KEY = 'nexora.user'
const TENANT_KEY = 'nexora.tenant' // only used by the platform operator

function safeGet(k) { try { return localStorage.getItem(k) } catch { return null } }
function safeSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v) } catch { /* ignore */ } }

export const session = {
  get token() { return safeGet(TOKEN_KEY) },
  get user() { try { return JSON.parse(safeGet(USER_KEY) || 'null') } catch { return null } },
  get tenant() { return safeGet(TENANT_KEY) },
  set(token, user) { safeSet(TOKEN_KEY, token); safeSet(USER_KEY, JSON.stringify(user)) },
  setTenant(t) { safeSet(TENANT_KEY, t) },
  clear() { safeSet(TOKEN_KEY, null); safeSet(USER_KEY, null); safeSet(TENANT_KEY, null) },
}

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

// Minimal response validation: `expect` is 'array' | 'object' | (data) => boolean
function validate(data, expect, path) {
  if (!expect) return data
  const ok = expect === 'array' ? Array.isArray(data)
    : expect === 'object' ? data !== null && typeof data === 'object' && !Array.isArray(data)
      : expect(data)
  if (!ok) throw new ApiError(0, `Unexpected response shape from ${path}`)
  return data
}

export async function api(path, { method = 'GET', body, tenantOverride, signal, expect } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (session.token) headers.Authorization = `Bearer ${session.token}`
  const user = session.user
  const tenant = tenantOverride ?? (user?.role === 'platform_operator' ? session.tenant : null)
  if (tenant) headers['X-Tenant-Id'] = tenant
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined, signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError(0, 'NEXORA API unreachable — is the SaaS backend running?')
  }
  if (res.status === 401 && path !== '/auth/login') {
    session.clear()
    window.location.href = '/login'
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === 'string' ? data.detail : res.statusText)
  return validate(data, expect, path)
}

export const LOCAL_COLLECTORS = {
  'tenant-alpha': ['col-alpha-01'],
  'tenant-beta': ['col-beta-01'],
}
