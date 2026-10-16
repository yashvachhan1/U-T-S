import { useCallback, useEffect, useRef, useState } from 'react'

/** setInterval that always calls the latest callback; `ms = null` pauses it. */
export function useInterval(fn, ms) {
  const saved = useRef(fn)
  useEffect(() => { saved.current = fn }, [fn])
  useEffect(() => {
    if (ms == null) return
    const id = setInterval(() => saved.current(), ms)
    return () => clearInterval(id)
  }, [ms])
}

/** Theme kept on <html data-theme> so CSS and the charts stay in step. */
export function useTheme() {
  const [theme, setTheme] = useState(
    () => document.documentElement.dataset.theme || 'dark')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('theme', theme) } catch { /* private mode */ }
  }, [theme])

  const toggle = useCallback(() => setTheme((t) => (t === 'light' ? 'dark' : 'light')), [])
  return [theme, toggle]
}

/**
 * Returns 'flash-up' / 'flash-down' for a moment whenever `value` changes,
 * then clears so the animation can re-run on the next tick.
 */
export function useFlash(value) {
  const prev = useRef(value)
  const [flash, setFlash] = useState('')

  useEffect(() => {
    if (value == null || prev.current == null || value === prev.current) {
      prev.current = value
      return
    }
    setFlash(value > prev.current ? 'flash-up' : 'flash-down')
    prev.current = value
    const t = setTimeout(() => setFlash(''), 650)
    return () => clearTimeout(t)
  }, [value])

  return flash
}

/**
 * Keeps the last `size` price samples per instrument key, for sparklines.
 * Samples accumulate while the dashboard is open — cheap, and no extra calls.
 */
export function usePriceHistory(rows, size = 40) {
  const [history, setHistory] = useState({})

  useEffect(() => {
    if (!rows || !rows.length) return
    setHistory((prev) => {
      const next = { ...prev }
      let changed = false
      for (const r of rows) {
        if (r.ltp == null) continue
        const list = next[r.instrument_key] || []
        if (list[list.length - 1] === r.ltp) continue
        next[r.instrument_key] = [...list, r.ltp].slice(-size)
        changed = true
      }
      return changed ? next : prev
    })
  }, [rows, size])

  return history
}

/** Live IST clock, formatted HH:MM:SS. */
export function useClock() {
  const [now, setNow] = useState('')
  useEffect(() => {
    const tick = () => setNow(new Date().toLocaleTimeString('en-GB',
      { timeZone: 'Asia/Kolkata', hour12: false }))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return now
}
