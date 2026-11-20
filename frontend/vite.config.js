import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
import https from 'node:https'
import path from 'node:path'

// NEXORA SaaS dashboard (Tier 2 UI, port 5173).
// The SaaS API is HTTPS-only; the dev proxy verifies its certificate against the local dev CA
// (scripts/setup_dev.py) — no `secure: false`, no plaintext fallback.
// This app deliberately has NO proxy to the collectors' local consoles: raw telemetry cannot reach it.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.NEXORA_SAAS_URL || 'https://localhost:8443'
  const caPath = path.resolve(__dirname, env.NEXORA_CA_CERT || '../certificates/ca.pem')
  const ca = fs.existsSync(caPath) ? fs.readFileSync(caPath) : undefined
  return {
    plugins: [react(), tailwindcss()],
    build: { chunkSizeWarningLimit: 1200 },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target, changeOrigin: true, secure: true, agent: new https.Agent({ ca }) },
      },
    },
  }
})
