import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Tier-1 Local Collector Console — a SEPARATE app (port 5174). It proxies only to collector processes on
// 127.0.0.1 and has no route to the SaaS API. Start: npm run dev:local
// serve local.html (not index.html) for every page route of this app
function localHtml() {
  return {
    name: 'nexora-local-html',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.method === 'GET' && req.headers.accept?.includes('text/html')) req.url = '/local.html'
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [localHtml(), react(), tailwindcss()],
  appType: 'spa',
  build: { outDir: 'dist-local', rollupOptions: { input: path.resolve(__dirname, 'local.html') } },
  server: {
    port: 5174,
    strictPort: true,
    open: false,
    proxy: {
      '/collector/col-alpha-01': { target: 'http://127.0.0.1:9101', rewrite: (p) => p.replace(/^\/collector\/col-alpha-01/, '') },
      '/collector/col-beta-01': { target: 'http://127.0.0.1:9102', rewrite: (p) => p.replace(/^\/collector\/col-beta-01/, '') },
    },
  },
  preview: { port: 5174 },
})
