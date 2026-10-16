/* Thin wrapper over the FastAPI backend. Every call throws a plain Error
   carrying the server's own message, which the UI shows as-is. */

export async function api(path, options = {}) {
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`)
  return data
}

export const get = (p) => api(p)
export const post = (p, body) => api(p, { method: 'POST', body })
export const del = (p) => api(p, { method: 'DELETE' })

/* ----------------------------------------------------------- formatting */
export const fmt = (n, d = 2) =>
  n === null || n === undefined || n === '' || Number.isNaN(Number(n))
    ? '—'
    : Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })

export const fmtInt = (n) =>
  n === null || n === undefined ? '—' : Number(n).toLocaleString('en-IN')

export const rs = (n) => (n === null || n === undefined ? '—' : '₹' + fmt(n))
export const sign = (n) => (Number(n) > 0 ? '+' : '')
export const cls = (n) => (Number(n) > 0 ? 'up' : Number(n) < 0 ? 'down' : 'muted')

export const INTERVALS = [
  ['1m', '1m', 5], ['5m', '5m', 10], ['15m', '15m', 30],
  ['30m', '30m', 60], ['1h', '1H', 120], ['1d', '1D', 730],
]

export const lookbackFor = (interval) =>
  (INTERVALS.find((i) => i[0] === interval) || [])[2] || 365
