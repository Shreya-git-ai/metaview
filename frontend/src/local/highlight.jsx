// Local-only text highlighting. Mirrors the collector's redaction rules (local_collector/redaction.py) so an
// operator can SEE what would be removed. This runs only in the Tier-1 console, never in the SaaS app.

const SECRET_RES = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  /(?<=[a-z][a-z0-9+.-]*:\/\/[^:/\s@]+:)[^@\s]+(?=@)/gi,
  /(?<=\bBearer\s+)[A-Za-z0-9\-_.=+/]{8,}/gi,
  /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g,
  /\bAKIA[0-9A-Z]{12,20}\b/g,
  /\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}/g,
  /(?<=(?:pass(?:word|wd)?|pwd)\s*[=:]\s*)[^\s,;&"']+/gi,
  /(?<=\b(?:api[_-]?key|apikey|secret|token|access[_-]?key)\s*[=:]\s*)[^\s,;&"']+/gi,
]
const IDENT_RES = [
  /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g,
  /\b(?:\d{1,3}\.){3}\d{1,3}\b/g,
  /(?<=\b(?:user=|for (?:invalid user )?))[A-Za-z0-9._-]+/g,
]

function ranges(text, res, kind) {
  const out = []
  for (const re of res) {
    re.lastIndex = 0
    let m
    while ((m = re.exec(text)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue }
      out.push({ start: m.index, end: m.index + m[0].length, kind })
    }
  }
  return out
}

/** Raw text with secrets (red) and identifiers (amber underline) highlighted. */
export function SecretText({ text, extra = [] }) {
  if (!text) return null
  const rs = [...ranges(text, SECRET_RES, 'secret'), ...ranges(text, IDENT_RES, 'ident')]
  for (const c of extra) {
    if (!c || c.length < 6) continue
    let i = text.indexOf(c)
    while (i >= 0) { rs.push({ start: i, end: i + c.length, kind: 'secret' }); i = text.indexOf(c, i + c.length) }
  }
  rs.sort((a, b) => a.start - b.start || (a.kind === 'secret' ? -1 : 1))
  const merged = []
  for (const r of rs) {
    const last = merged[merged.length - 1]
    if (last && r.start < last.end) {
      if (r.kind === 'secret') last.kind = 'secret'
      last.end = Math.max(last.end, r.end)
    } else merged.push({ ...r })
  }
  const parts = []
  let pos = 0
  merged.forEach((r, i) => {
    if (r.start > pos) parts.push(text.slice(pos, r.start))
    parts.push(r.kind === 'secret'
      ? <mark key={i} className="rounded bg-crit/25 px-0.5 font-semibold text-crit" title="Secret — removed before egress">{text.slice(r.start, r.end)}</mark>
      : <mark key={i} className="rounded bg-transparent text-warn underline decoration-dotted underline-offset-2" title="Identifier — pseudonymised before egress">{text.slice(r.start, r.end)}</mark>)
    pos = r.end
  })
  if (pos < text.length) parts.push(text.slice(pos))
  return <>{parts}</>
}

/** Redacted text with [REDACTED:R-x] and [KIND:hash] tokens styled. */
export function RedactedText({ text }) {
  if (!text) return null
  return (
    <>
      {text.split(/(\[(?:REDACTED:[A-Z-]+|[A-Z0-9]+:[0-9a-f]{8})\])/).map((p, i) => {
        if (/^\[REDACTED:/.test(p)) return <span key={i} className="rounded bg-good/15 px-1 text-good">{p}</span>
        if (/^\[[A-Z0-9]+:[0-9a-f]{8}\]$/.test(p)) return <span key={i} className="rounded bg-info/15 px-1 text-info">{p}</span>
        return p
      })}
    </>
  )
}
