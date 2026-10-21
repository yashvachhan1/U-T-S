/* Execution: the loop, the orders, and whether the two books agree.
 *
 * Two things on this page are alarms rather than information, and they are
 * placed above everything else because both are silent failures:
 *
 *   a filled position with no stop   -- the most expensive state possible
 *   our book disagreeing with theirs -- every risk limit is then computed
 *                                       from a book that does not exist
 *
 * When both are clear the page is deliberately boring, which is the point.
 */
import { useCallback, useEffect, useState } from 'react'
import { fmt, fmtInt, get, post } from '../api.js'
import Icon from '../components/Icon.jsx'
import {
  Empty, KeyValues, Panel, SideTag, SkeletonRows, Stat, StatusTag, Table,
} from '../components/ui.jsx'
import { useInterval } from '../hooks/index.js'
import { useUI } from '../components/Feedback.jsx'

const STATUS_TONE = {
  complete: 'good', open: 'ok', partial: 'ok',
  pending: 'muted', rejected: 'crit', cancelled: 'muted',
}
const ROLE_LABEL = {
  entry: 'Entry', stop: 'Stop', target: 'Target',
  exit: 'Exit', square_off: 'Square-off',
}

const ORDER_COLS = [
  {
    label: 'Time',
    get: (r) => <span className="muted mono">{String(r.created_at || '').slice(11, 19)}</span>,
  },
  { label: 'Symbol', get: (r) => <span className="sym-cell">{r.symbol || r.instrument_key}</span> },
  {
    label: 'Role',
    get: (r) => (
      <StatusTag tone={r.role === 'stop' ? 'warn' : 'muted'}
                 icon={r.role === 'stop' ? 'shield' : undefined}>
        {ROLE_LABEL[r.role] || r.role}
      </StatusTag>
    ),
  },
  { label: 'Side', get: (r) => <SideTag side={r.side} /> },
  { label: 'Qty', num: true, get: (r) => fmtInt(r.quantity) },
  {
    label: 'Filled', num: true,
    get: (r) => (
      <span className={r.filled_quantity >= r.quantity ? 'up' : 'muted'}>
        {fmtInt(r.filled_quantity)}
      </span>
    ),
  },
  {
    label: 'Price', num: true,
    get: (r) => fmt(r.avg_fill_price || r.limit_price || r.trigger_price),
  },
  {
    label: 'Status',
    get: (r) => (
      <StatusTag tone={STATUS_TONE[r.status] || 'muted'}
                 icon={r.status === 'rejected' ? 'alert'
                   : r.status === 'complete' ? 'check' : undefined}>
        {r.status}
      </StatusTag>
    ),
  },
  { label: 'Source', get: (r) => <span className="muted">{r.source}</span> },
]

export default function Execution() {
  const { toast, confirm } = useUI()
  const [data, setData] = useState(null)
  const [orders, setOrders] = useState([])
  const [recon, setRecon] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try {
      const [s, o] = await Promise.all([
        get('/execution/status'),
        get('/execution/orders?limit=60'),
      ])
      setData(s); setOrders(o); setError(null)
    } catch (e) { setError(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useInterval(load, 15000)

  const act = async (name, path, body, message) => {
    setBusy(name)
    try {
      const out = await post(path, body)
      toast(message(out), 'ok')
      if (name === 'reconcile') setRecon(out)
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  const toggleLoop = async () => {
    const running = data?.loop?.running
    if (running) {
      const ok = await confirm({
        title: 'Loop band karein?',
        note: 'Orders aur positions waise hi rahenge — loop sirf dekhna band karega. Fill track hona ruk jayega.',
      })
      if (!ok) return
    }
    await act('loop', running ? '/execution/stop' : '/execution/start', null,
      () => (running ? 'Loop band' : 'Loop chalu'))
  }

  const squareOff = async () => {
    const ok = await confirm({
      title: 'Intraday square off karein?',
      note: 'Saari intraday positions abhi band ho jayengi. Square-off time se pehle kuch nahi hoga.',
      danger: true,
    })
    if (!ok) return
    await act('squareoff', '/execution/square-off', null,
      (out) => `${out.squared_off} position band ki gayi`)
  }

  if (!data) {
    return (
      <>
        <Head onReload={load} />
        <Panel>{error ? <Empty icon="alert">{error}</Empty> : <SkeletonRows n={6} />}</Panel>
      </>
    )
  }

  const loop = data.loop || {}
  const ex = data.executor || {}
  const stats = ex.orders || {}
  const unprotected = ex.unprotected_positions || []
  const positions = Object.entries(ex.our_positions || {})

  return (
    <>
      <Head onReload={load} />

      {/* ---- the two alarms, above everything else ---- */}
      {unprotected.length > 0 && (
        <div className="halt-banner">
          <Icon name="alert" />
          <div style={{ flex: 1 }}>
            <div className="halt-title">
              {unprotected.length} position bina stop ke hai
            </div>
            <div className="halt-why">
              Ye system ki sabse mehngi state hai. Trading halt ho chuki hogi,
              aur har poll par stop dobara lagane ki koshish ho rahi hai.
            </div>
            {unprotected.map((p) => (
              <div className="halt-why" key={p.instrument_key}>
                <code>{p.symbol}</code> — {fmtInt(p.quantity)} units
              </div>
            ))}
          </div>
        </div>
      )}

      {recon && !recon.ok && (recon.mismatches || []).length > 0 && (
        <div className="halt-banner">
          <Icon name="link" />
          <div style={{ flex: 1 }}>
            <div className="halt-title">Books match nahi kar rahe</div>
            <div className="halt-why">
              Broker sahi hai, hamara record kahani hai. Jab tak ye match na kare,
              har risk limit galat book par chal rahi hai.
            </div>
            {recon.mismatches.map((m) => (
              <div className="recon-row" key={m.instrument_key}>
                <span className="sym-cell">{m.instrument_key}</span>
                <span className="num muted">hamara {m.ours}</span>
                <span className="num">broker {m.broker}</span>
                <StatusTag tone="crit" icon="alert">{m.kind.replace(/_/g, ' ')}</StatusTag>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat label="Loop" value={loop.running ? 'Chalu' : 'Band'}
              accent={loop.running ? 'g' : ''}
              colour={loop.running ? 'up' : ''}
              sub={loop.running ? `har ${loop.interval_seconds}s` : 'kuch track nahi ho raha'} />
        <Stat label="Cycles" value={fmtInt(loop.cycles)} sub={loop.last_run || '—'} />
        <Stat label="Live orders" value={fmtInt(stats.live)} />
        <Stat label="Bina stop ke" value={fmtInt(stats.unprotected ?? 0)}
              accent={(stats.unprotected ?? 0) > 0 ? 'r' : 'g'}
              colour={(stats.unprotected ?? 0) > 0 ? 'down' : 'up'}
              sub="hamesha zero hona chahiye" />
        <Stat label="Total orders" value={fmtInt(stats.total)} />
        <Stat label="Loop errors" value={fmtInt(loop.errors)}
              accent={loop.errors > 0 ? 'r' : ''}
              sub={loop.last_error ? String(loop.last_error).slice(0, 36) : ''} />
      </div>

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Panel title="Loop"
               right={
                 <div style={{ display: 'flex', gap: 8 }}>
                   <button className="btn ghost sm" onClick={squareOff}
                           disabled={busy === 'squareoff'}>
                     <Icon name="stop" />Square off
                   </button>
                   <button className={'btn sm ' + (loop.running ? 'danger' : 'primary')}
                           onClick={toggleLoop} disabled={busy === 'loop'}>
                     <Icon name={loop.running ? 'pause' : 'play'} />
                     {loop.running ? 'Band karo' : 'Chalu karo'}
                   </button>
                 </div>
               }>
          <p style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.6, margin: '0 0 12px' }}>
            Har cycle ka order fix hai: <b>poll → stop lagao → square-off →
            reconcile → sweep</b>. Pehle reconcile karte to broker ke fresh fill
            hamare purane record se compare hote aur mismatch dikhta jo asal me
            sirf latency hai.
          </p>
          <KeyValues rows={[
            ['State', loop.running ? 'chal raha hai' : 'band hai'],
            ['Interval', `${loop.interval_seconds ?? 15}s (market open)`],
            ['Reconcile', `har ${loop.reconcile_every ?? 4} cycle`],
            ['Last run', loop.last_run || '—'],
            ['Square-off paar ho gaya', ex.past_square_off ? 'haan' : 'nahi'],
          ]} />
        </Panel>

        <Panel title="Reconciliation"
               right={<button className="btn ghost sm"
                              onClick={() => act('reconcile', '/execution/reconcile', null,
                                (out) => (out.ok ? 'Books match kar rahe hain'
                                  : out.error ? 'Broker padha nahi ja saka'
                                    : `${(out.mismatches || []).length} mismatch`))}
                              disabled={busy === 'reconcile'}>
                 {busy === 'reconcile' ? <i className="spin" /> : <Icon name="link" />}
                 Abhi check karo
               </button>}>
          {!recon ? (
            <>
              <Empty icon="link">Abhi tak check nahi kiya.</Empty>
              <p style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.6 }}>
                Share count me rounding error jaisi koi cheez nahi hoti — koi bhi
                fark asli disagreement hai aur trading halt kar deta hai. Par broker
                padh hi na paana mismatch nahi mana jaata.
              </p>
            </>
          ) : (
            <>
              <div style={{ marginBottom: 12 }}>
                <StatusTag tone={recon.ok ? 'good' : 'crit'}
                           icon={recon.ok ? 'check' : 'alert'}>
                  {recon.ok ? 'Books match kar rahe hain'
                    : recon.error ? 'Broker padha nahi ja saka'
                      : `${(recon.mismatches || []).length} mismatch`}
                </StatusTag>
              </div>
              <KeyValues rows={[
                ['Instruments check kiye', recon.instruments_checked ?? 0],
                ['Hamare paas', Object.keys(recon.ours || {}).length],
                ['Broker ke paas', Object.keys(recon.broker || {}).length],
                ['Broker ko na pata orders',
                  (recon.orders?.unknown_to_broker || []).length],
                ['Bina stop wali positions',
                  (recon.unprotected_positions || []).length],
              ]} />
              {recon.note && (
                <p style={{ fontSize: 11.5, color: 'var(--text-2)', marginTop: 10 }}>
                  {recon.note}
                </p>
              )}
            </>
          )}
        </Panel>
      </div>

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Panel title="Hamari positions (apne fills se)">
          {positions.length === 0 ? (
            <Empty icon="wallet">Koi position nahi.</Empty>
          ) : (
            <KeyValues rows={positions.map(([key, qty]) => [key, fmtInt(qty)])} />
          )}
        </Panel>
        <Panel title="Orders kis haal me hain">
          {Object.keys(stats.by_status || {}).length === 0 ? (
            <Empty icon="list">Abhi koi order nahi.</Empty>
          ) : (
            <KeyValues rows={Object.entries(stats.by_status).map(([k, v]) => [k, v])} />
          )}
        </Panel>
      </div>

      <Panel title="Orders" bodyClass="flush"
             bodyStyle={{ maxHeight: 'calc(100vh - 260px)' }}>
        <Table cols={ORDER_COLS} rows={orders}
               empty="Abhi tak koi order nahi gaya" />
      </Panel>
    </>
  )
}

function Head({ onReload }) {
  return (
    <div className="page-head">
      <div>
        <h1>Execution</h1>
        <p>Order, fill, stop, aur broker se milaan</p>
      </div>
      <div className="spacer" />
      <button className="btn ghost sm" onClick={onReload}><Icon name="refresh" />Reload</button>
    </div>
  )
}
