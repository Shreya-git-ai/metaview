import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Lock, Radar } from 'lucide-react'
import { api, session } from '../services/api'
import { ErrorBox, Spinner, ThemeToggle } from '../components/ui'

export default function Login() {
  const nav = useNavigate()
  const [users, setUsers] = useState([])
  const [username, setUsername] = useState('alpha.admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { api('/auth/demo-users').then(setUsers).catch(setError) }, [])

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const r = await api('/auth/login', { method: 'POST', body: { username, password } })
      session.clear()
      session.set(r.token, r.user)
      nav('/')
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-md p-7">
        <div className="mb-6 flex items-center gap-3">
          <Radar className="text-accent" size={30} />
          <div><div className="text-xl font-bold tracking-wider">NEXORA</div>
            <div className="text-xs text-muted">Secure AIOps Observability · Central SaaS</div></div>
          <ThemeToggle className="ml-auto" />
        </div>
        <label className="label">Synthetic demo user</label>
        <div className="mt-2 grid gap-2">
          {users.map((u) => (
            <button type="button" key={u.username} onClick={() => setUsername(u.username)}
              className={`rounded-lg border px-3 py-2 text-left text-sm ${username === u.username ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}>
              <div className="font-medium">{u.display} <span className="mono text-xs text-muted">{u.username}</span></div>
              <div className="text-xs text-muted">{u.role === 'tenant_admin' ? `Tenant admin — scoped to ${u.tenant_id}` : 'Platform operator — may switch tenant'}</div>
            </button>
          ))}
        </div>
        <label className="label mt-5 block">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus
          className="mt-2 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
          placeholder="SAAS_DEMO_PASSWORD from config/saas.env" />
        <div className="mt-4"><ErrorBox error={error} /></div>
        <button className="btn btn-primary mt-4 w-full justify-center" disabled={busy || !password}>
          {busy ? <Spinner /> : <Lock size={14} />} Sign in
        </button>
        <p className="mt-4 text-xs text-muted">All users, tenants and telemetry are synthetic. Tenant scope is enforced by the backend from the signed token.</p>
      </form>
    </div>
  )
}
