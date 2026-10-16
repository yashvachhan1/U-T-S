/* Toasts + confirm dialog, exposed through one context.
   A failing poll fires every few seconds, so identical toasts collapse into
   one row with a counter instead of stacking up the screen. */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import Icon from './Icon.jsx'

const Ctx = createContext(null)
export const useUI = () => useContext(Ctx)

const LIFE = { err: 6500, ok: 3800, '': 3800 }
let seq = 0

export function UIProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [dialog, setDialog] = useState(null)
  const timers = useRef(new Map())
  const resolver = useRef(null)

  const dismiss = useCallback((id) => {
    clearTimeout(timers.current.get(id))
    timers.current.delete(id)
    setToasts((list) => list.map((t) => (t.id === id ? { ...t, out: true } : t)))
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 200)
  }, [])

  const arm = useCallback((id, kind) => {
    clearTimeout(timers.current.get(id))
    timers.current.set(id, setTimeout(() => dismiss(id), LIFE[kind] ?? 3800))
  }, [dismiss])

  const toast = useCallback((message, kind = '') => {
    setToasts((list) => {
      const hit = list.find((t) => t.message === message && t.kind === kind && !t.out)
      if (hit) {
        arm(hit.id, kind)
        return list.map((t) => (t.id === hit.id ? { ...t, n: t.n + 1 } : t))
      }
      const id = ++seq
      arm(id, kind)
      return [...list, { id, message, kind, n: 1 }]
    })
  }, [arm])

  /** Returns a promise resolving to true/false. */
  const confirm = useCallback((opts) => {
    setDialog(opts)
    return new Promise((res) => { resolver.current = res })
  }, [])

  const close = useCallback((value) => {
    setDialog(null)
    if (resolver.current) { resolver.current(value); resolver.current = null }
  }, [])

  const value = useMemo(() => ({ toast, confirm }), [toast, confirm])

  return (
    <Ctx.Provider value={value}>
      {children}
      <Toasts items={toasts} onDismiss={dismiss} />
      {dialog && <ConfirmModal {...dialog} onClose={close} />}
    </Ctx.Provider>
  )
}

function Toasts({ items, onDismiss }) {
  return (
    <div id="toasts">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind} ${t.out ? 'out' : ''}`} onClick={() => onDismiss(t.id)}>
          <Icon name={t.kind === 'err' ? 'alert' : t.kind === 'ok' ? 'check' : 'info'} />
          <span>{t.message}</span>
          {t.n > 1 && <b className="count">×{t.n}</b>}
        </div>
      ))}
    </div>
  )
}

function ConfirmModal({ title, rows = [], note, danger, okText = 'Confirm', onClose }) {
  return (
    <div className="scrim open" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(false) }}>
      <div className="modal">
        <div className="mh">
          <Icon name={danger ? 'alert' : 'info'} style={{ color: `var(--${danger ? 'down' : 'accent'})` }} />
          <div><h4>{title}</h4></div>
        </div>
        <div className="mb">
          {rows.map(([k, v, colour], i) => (
            <div className="kv" key={i}><span>{k}</span><b className={colour || ''}>{v}</b></div>
          ))}
          {note && (
            <div className={'note ' + (danger ? 'bad' : '')} style={{ marginTop: 12 }}>
              <Icon name="alert" /><span>{note}</span>
            </div>
          )}
        </div>
        <div className="mf">
          <button className="btn ghost" onClick={() => onClose(false)}>Cancel</button>
          <button className={'btn ' + (danger ? 'sell' : 'primary')} onClick={() => onClose(true)} autoFocus>
            {okText}
          </button>
        </div>
      </div>
    </div>
  )
}
