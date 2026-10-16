/* Ctrl/Cmd+K palette: jump to a view or pull up any instrument without
   leaving the keyboard. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { get } from '../api.js'
import Icon from './Icon.jsx'

export default function CommandPalette({ open, onClose, views, onView, onInstrument }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState([])
  const [cursor, setCursor] = useState(0)
  const listRef = useRef(null)

  useEffect(() => { if (open) { setQ(''); setHits([]); setCursor(0) } }, [open])

  // Instrument lookup, debounced.
  useEffect(() => {
    const term = q.trim()
    if (!open || term.length < 1) { setHits([]); return }
    const t = setTimeout(async () => {
      try { setHits(await get(`/instruments/search?q=${encodeURIComponent(term)}&limit=8`)) }
      catch { setHits([]) }
    }, 180)
    return () => clearTimeout(t)
  }, [q, open])

  const viewHits = useMemo(() => {
    const term = q.trim().toLowerCase()
    if (!term) return views
    return views.filter((v) => v.label.toLowerCase().includes(term))
  }, [q, views])

  // One flat list so arrow keys walk straight through both groups.
  const items = useMemo(() => [
    ...viewHits.map((v) => ({ kind: 'view', ...v })),
    ...hits.map((r) => ({ kind: 'instrument', row: r })),
  ], [viewHits, hits])

  useEffect(() => { setCursor(0) }, [items.length])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, items.length - 1)) }
      if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)) }
      if (e.key === 'Enter') { e.preventDefault(); choose(items[cursor]) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // Keep the highlighted row in view while arrowing.
  useEffect(() => {
    const node = listRef.current?.querySelector('.cmdk-item.on')
    if (node) node.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  if (!open) return null

  const choose = (item) => {
    if (!item) return
    if (item.kind === 'view') onView(item.id)
    else onInstrument(item.row)
    onClose()
  }

  let seenInstrumentHeader = false

  return (
    <div className="cmdk-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cmdk">
        <div className="cmdk-input">
          <Icon name="search" />
          <input
            autoFocus value={q} placeholder="Stock dhoondein ya page par jayein…"
            onChange={(e) => setQ(e.target.value)} spellCheck="false"
          />
        </div>

        <div className="cmdk-list" ref={listRef}>
          {!items.length && <div className="cmdk-grp" style={{ padding: 20, textAlign: 'center' }}>Kuch nahi mila</div>}

          {items.map((item, i) => {
            let header = null
            if (i === 0 && item.kind === 'view') header = <div className="cmdk-grp" key="gv">Pages</div>
            if (item.kind === 'instrument' && !seenInstrumentHeader) {
              seenInstrumentHeader = true
              header = <div className="cmdk-grp" key="gi">Instruments</div>
            }
            const on = i === cursor
            return (
              <div key={item.kind + i} style={{ display: 'contents' }}>
                {header}
                <div
                  className={'cmdk-item ' + (on ? 'on' : '')}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => choose(item)}
                >
                  <Icon name={item.kind === 'view' ? item.icon : 'chart'} />
                  {item.kind === 'view'
                    ? <div className="t">{item.label}</div>
                    : (
                      <div style={{ minWidth: 0 }}>
                        <div className="t">{item.row.symbol}</div>
                        <div className="s">{item.row.name}</div>
                      </div>
                    )}
                  {item.kind === 'instrument' && (
                    <span className="seg-tag tag">{item.row.segment.replace('_', ' ')}</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="cmdk-foot">
          <span><b>↑↓</b> navigate</span>
          <span><b>↵</b> select</span>
          <span><b>esc</b> close</span>
        </div>
      </div>
    </div>
  )
}
