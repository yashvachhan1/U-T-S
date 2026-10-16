/* Debounced instrument lookup with a dropdown of results. */
import { useEffect, useRef, useState } from 'react'
import { get } from '../api.js'
import Icon from './Icon.jsx'

export default function InstrumentSearch({ placeholder = 'RELIANCE, NIFTY, TCS…', onPick }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(null)
  const wrap = useRef(null)

  useEffect(() => {
    const term = q.trim()
    if (!term) { setOpen(false); setRows([]); return }
    const t = setTimeout(async () => {
      try {
        setRows(await get(`/instruments/search?q=${encodeURIComponent(term)}&limit=15`))
        setError(null)
      } catch (e) {
        setRows([])
        setError(e.message)
      }
      setOpen(true)
    }, 200)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    const away = (e) => { if (wrap.current && !wrap.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [])

  const pick = (row) => { setOpen(false); setQ(''); onPick(row) }

  return (
    <div className="sw" ref={wrap}>
      <Icon name="search" className="ico" />
      <input
        type="text" value={q} placeholder={placeholder} autoComplete="off" spellCheck="false"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false)
          if (e.key === 'Enter' && rows.length) pick(rows[0])
        }}
      />
      <div className={'results ' + (open ? 'open' : '')}>
        {error && <div className="none">{error}</div>}
        {!error && !rows.length && <div className="none">Kuch nahi mila</div>}
        {rows.map((r) => {
          const cash = r.segment.endsWith('_EQ') || r.segment.endsWith('_INDEX')
          return (
            <div className="item" key={r.instrument_key} onClick={() => pick(r)}>
              <div style={{ minWidth: 0 }}>
                <div className="sym">{r.symbol}</div>
                <div className="nm">{r.name}</div>
              </div>
              <span className={'seg-tag ' + (cash ? 'eq' : '')}>{r.segment.replace('_', ' ')}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function PickedNote({ instrument }) {
  if (!instrument) {
    return <div className="note" style={{ marginBottom: 12 }}><Icon name="info" /><span>Koi instrument nahi chuna</span></div>
  }
  return (
    <div className="note good" style={{ marginBottom: 12 }}>
      <Icon name="check" />
      <span><b>{instrument.symbol}</b> — {instrument.name} <span className="muted">({instrument.segment})</span></span>
    </div>
  )
}
