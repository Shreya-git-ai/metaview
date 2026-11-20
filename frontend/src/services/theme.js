import { useEffect, useState } from 'react'

// Light / dark theme. Dark by default; an explicit choice is remembered per browser.
const KEY = 'nexora.theme'
const EVT = 'nexora-theme'

export function storedTheme() {
  try { const t = localStorage.getItem(KEY); return t === 'light' || t === 'dark' ? t : null } catch { return null }
}

export function systemTheme() {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function currentTheme() {
  return document.documentElement.dataset.theme || storedTheme() || 'dark'
}

export function applyTheme(t) {
  document.documentElement.dataset.theme = t
}

export function setTheme(t) {
  try { localStorage.setItem(KEY, t) } catch { /* private mode: still apply for this session */ }
  applyTheme(t)
  window.dispatchEvent(new CustomEvent(EVT, { detail: t }))
}

// NEXORA is a dark-first enterprise UI: dark is the default; light is an explicit, remembered choice.
export function initTheme() {
  applyTheme(storedTheme() || 'dark')
}

export function useTheme() {
  const [theme, setT] = useState(currentTheme())
  useEffect(() => {
    const h = (e) => setT(e.detail)
    window.addEventListener(EVT, h)
    return () => window.removeEventListener(EVT, h)
  }, [])
  return [theme, setTheme]
}

// Chart colours need concrete values (SVG attributes), so they are resolved per theme here.
// Series steps are the validated categorical slots 1–3 for each mode (dataviz validator: all checks pass;
// light slot 3 is below 3:1 on white → relieved by legend, tooltip and the sources table).
export const CHART = {
  dark: { grid: '#24344D', axis: '#8A9BB4', tipBg: '#0F1A2B', tipBorder: '#24344D', tipText: '#E6EDF7',
          legend: '#C4D0E0', ring: '#172235', series: ['#3987e5', '#d95926', '#199e70'] },
  light: { grid: '#E3E8EF', axis: '#5B6B82', tipBg: '#FFFFFF', tipBorder: '#D9E0EA', tipText: '#0F1B2D',
           legend: '#334155', ring: '#FFFFFF', series: ['#2a78d6', '#eb6834', '#1baf7a'] },
}
