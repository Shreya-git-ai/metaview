// Tier-1 Local Console client. Talks ONLY to collector processes on 127.0.0.1 (via this app's own dev proxy).
// This module is not part of the SaaS bundle.
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

export async function local(collectorId, path, { method = 'GET', body, signal } = {}) {
  let res
  try {
    res = await fetch(`/collector/${collectorId}/local${path}`, {
      method, signal, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError(0, 'collector process unreachable')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === 'string' ? data.detail : `collector console error (${res.status})`)
  return data
}

export const COLLECTORS = [
  { id: 'col-alpha-01', tenant: 'tenant-alpha', label: 'Customer Alpha' },
  { id: 'col-beta-01', tenant: 'tenant-beta', label: 'Customer Beta' },
]
